"""Command-line interface for resume-as-code."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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

    profile_path = Path(args.profiles) / f"{args.profile}.yaml"
    if not profile_path.exists():
        print(f"error: profile not found: {profile_path}", file=sys.stderr)
        return 2
    profile = load_profile(profile_path)

    out_dir = Path(args.out)
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]

    if args.job:
        job_path = Path(args.job)
        jd_text = job_path.read_text(encoding="utf-8")
        job_name = job_path.stem
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
        stem = f"Nestor_Fleitas_{_slug(profile.name)}"
        written = _write_outputs(resume, out_dir, stem, formats)

    if not resume.email:
        print("warning: no email resolved (set RESUME_EMAIL or data/contact.local.yaml)",
              file=sys.stderr)

    print(f"Generated ({profile.name}"
          + (f" / {args.job}" if args.job else "") + "):")
    for p in written:
        print(f"  - {p}")
    print(f"canonical fingerprint: {resume.canonical_hash}")
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
    g.add_argument("--profile", required=True, help="profile name (see: resume profiles)")
    g.add_argument("--job", help="path to a Job Description text file")
    g.add_argument("--profiles", default="profiles", help="profiles directory")
    g.add_argument("--out", default="generated", help="output directory")
    g.add_argument("--formats", default="pdf,docx,txt", help="comma list: pdf,docx,txt")
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
