"""Command-line interface for resume-as-code."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .autoselect import select_profile
from .jobmatch import analyze_job, render_analysis_md
from .loader import DataError, load_bundle
from .render_docx import render_docx
from .render_pdf import render_pdf
from .render_txt import render_txt
from .tailor import build_resume, load_profile
from .validate import format_report, run_ats_validation

PROFILE_SLUGS = {
    "devops": "DevOps",
    "devsecops": "DevSecOps",
    "ai-architect": "AI_Architect",
}


def _slug(profile: str) -> str:
    return PROFILE_SLUGS.get(profile, profile.replace("-", "_").title())


def _write_outputs(resume, out_dir: Path, stem: str, formats: list[str]) -> list[Path]:
    written: list[Path] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    if "txt" in formats:
        p = out_dir / f"{stem}.txt"
        p.write_text(render_txt(resume), encoding="utf-8")
        written.append(p)
    if "docx" in formats:
        written.append(render_docx(resume, out_dir / f"{stem}.docx"))
    if "pdf" in formats:
        written.append(render_pdf(resume, out_dir / f"{stem}.pdf"))
    return written


def cmd_generate(args) -> int:
    try:
        bundle = load_bundle(args.data)
    except DataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # --jd is the machine-driver alias for --job; either resolves the JD path.
    job_path: Path | None = args.jd if args.jd else (Path(args.job) if args.job else None)
    jd_text = job_path.read_text(encoding="utf-8") if job_path else None

    profile_auto = bool(args.auto_profile)
    if profile_auto:
        if jd_text is None:
            print("error: --auto-profile requires --jd/--job", file=sys.stderr)
            return 2
        selection = select_profile(jd_text, args.data, args.profiles)
        profile_name = selection.name
        profile_path = selection.path
    else:
        profile_name = args.profile
        profile_path = Path(args.profiles) / f"{args.profile}.yaml"

    if not profile_path.exists():
        print(f"error: profile not found: {profile_path}", file=sys.stderr)
        return 2
    profile = load_profile(profile_path)

    out_dir = Path(args.out)
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]

    if job_path:
        job_name = args.job_name or job_path.stem
        analysis = analyze_job(bundle, jd_text)
        resume = build_resume(
            bundle, profile,
            target=job_name,
            matched_skills=analysis.matched_skills,
            extra_emphasis=analysis.extra_emphasis,
        )
        stem = f"Nestor_Fleitas_{job_name.title()}"
        written = _write_outputs(resume, out_dir, stem, formats)
        report = render_analysis_md(
            analysis, job_name=job_name, profile_name=profile.name,
            canonical_hash=resume.canonical_hash,
        )
        report_path = out_dir / f"{job_name}-analysis.md"
        report_path.write_text(report, encoding="utf-8")
        written.append(report_path)
    else:
        resume = build_resume(bundle, profile)
        stem = f"Nestor_Fleitas_{(args.job_name.title() if args.job_name else _slug(profile.name))}"
        written = _write_outputs(resume, out_dir, stem, formats)

    artifacts = {
        "pdf": str(out_dir / f"{stem}.pdf"),
        "docx": str(out_dir / f"{stem}.docx"),
        "txt": str(out_dir / f"{stem}.txt"),
    }
    ats_passed = None
    ats_failures: list[str] = []
    if args.validate:
        checks = run_ats_validation(artifacts["pdf"], args.data)
        ats_passed = all(c.ok for c in checks)
        ats_failures = [c.name for c in checks if not c.ok]

    if args.json_out:
        print(json.dumps({
            "profileSelected": profile_name,
            "profileAuto": profile_auto,
            "atsPassed": ats_passed,
            "atsFailures": ats_failures,
            "artifacts": artifacts,
        }))
        return 0

    if not resume.email:
        print("warning: no email resolved (set RESUME_EMAIL or data/contact.local.yaml)",
              file=sys.stderr)

    print(f"Generated ({profile.name}"
          + (f" / {job_path}" if job_path else "") + "):")
    for p in written:
        print(f"  - {p}")
    print(f"canonical fingerprint: {resume.canonical_hash}")
    if args.validate:
        if ats_passed:
            print("ATS validation: PASSED")
        else:
            print("ATS validation: FAILED (" + ", ".join(ats_failures) + ")", file=sys.stderr)
    return 0


def cmd_validate(args) -> int:
    checks = run_ats_validation(args.pdf, args.data)
    report, ok = format_report(args.pdf, checks)
    print(report)
    return 0 if ok else 1


def cmd_check(args) -> int:
    try:
        bundle = load_bundle(args.data)
    except DataError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    from .loader import fingerprint
    print("canonical data: VALID")
    print(f"experiences: {len(bundle.experiences)}")
    print(f"skills in catalog: {len(bundle.skills.all_names())}")
    print(f"projects: {len(bundle.projects)}")
    unused = bundle.unused_skills()
    if unused:
        print(f"warning: {len(unused)} catalog skill(s) with no evidence: "
              + ", ".join(unused))
    print(f"canonical fingerprint: {fingerprint(bundle)}")
    return 0


def cmd_profiles(args) -> int:
    for p in sorted(Path(args.profiles).glob("*.yaml")):
        prof = load_profile(p)
        print(f"{prof.name:14s} {prof.headline}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="resume", description="ATS-first CV generation from canonical data")
    parser.add_argument("--data", default="data", help="canonical data directory")
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="generate a CV")
    profile_grp = g.add_mutually_exclusive_group(required=True)
    profile_grp.add_argument("--profile", help="profile name (see: resume profiles)")
    profile_grp.add_argument("--auto-profile", action="store_true",
                              help="auto-select the profile from the JD")
    g.add_argument("--job", help="path to a Job Description text file")
    g.add_argument("--jd", type=Path,
                   help="path to a Job Description text file (alias for --job)")
    g.add_argument("--job-name", help="override the output filename stem source")
    g.add_argument("--profiles", default="profiles", help="profiles directory")
    g.add_argument("--out", default="generated", help="output directory")
    g.add_argument("--formats", default="pdf,docx,txt", help="comma list: pdf,docx,txt")
    g.add_argument("--validate", action="store_true",
                   help="run ATS validation on the rendered PDF")
    g.add_argument("--json", dest="json_out", action="store_true",
                   help="emit a machine-readable JSON summary to stdout")
    g.set_defaults(func=cmd_generate)

    v = sub.add_parser("validate", help="run ATS validation on a generated PDF")
    v.add_argument("pdf", help="path to the generated PDF")
    v.set_defaults(func=cmd_validate)

    c = sub.add_parser("check", help="validate canonical data & print fingerprint")
    c.set_defaults(func=cmd_check)

    p = sub.add_parser("profiles", help="list available profiles")
    p.add_argument("--profiles", default="profiles", help="profiles directory")
    p.set_defaults(func=cmd_profiles)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
