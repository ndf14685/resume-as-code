"""Command-line interface for resume-as-code."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .autoselect import select_profile
from .compose import compose_plan
from .jobmatch import analyze_job, render_analysis_md
from .pipeline import run_pipeline
from .score import evaluate as evaluate_quality
from .semantic_planner import plan as semantic_plan
from .tailor import build_from_plan
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


def _build_ask(ask_cmd: str):
    """ask(prompt)->str vía subproceso: prompt por stdin, respuesta por stdout.
    Desacopla el proveedor (OpenClaw, ai.ask del bridge, un stub de test).
    Cualquier fallo/timeout lo maneja el planner con fallback determinista."""
    import subprocess

    def ask(prompt: str) -> str:
        proc = subprocess.run(
            ask_cmd, shell=True, input=prompt, capture_output=True,
            text=True, timeout=90)
        if proc.returncode != 0:
            raise RuntimeError(f"ask-cmd exit {proc.returncode}: {proc.stderr[:200]}")
        return proc.stdout

    return ask


_REFUSAL_TEXT = {
    "control": ("Recibí una instrucción, no una búsqueda. No generé ningún CV.\n"
                "Mandame la descripción del puesto y lo armo."),
    "too_short": ("Eso es muy corto para ser una búsqueda ({chars} caracteres). "
                  "No generé nada.\nPegá el aviso completo y lo proceso."),
    "conversation": ("No parece una descripción de puesto, así que no generé un "
                     "CV.\nMotivo: {reason}"),
}


def _refusal_reply(pipe) -> str:
    """What Telegram shows when the input was not a vacancy."""
    kind = pipe.debug["admission"]["kind"]
    signals = pipe.debug["admission"]["signals"]
    template = _REFUSAL_TEXT.get(kind, _REFUSAL_TEXT["conversation"])
    return template.format(chars=signals.get("chars", 0), reason=pipe.refusal)


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
    # Role-driven pipeline: JD → ROLE_INTENT (hybrid: LLM proposes / deterministic
    # authority) → role-composed positioning. Explicit --profile keeps the legacy
    # template path for backward compatibility.
    role_driven = profile_auto and jd_text is not None

    out_dir = Path(args.out)
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    if (args.validate or args.json_out) and "pdf" not in formats:
        # ATS validation always needs a rendered PDF; force it even if
        # --formats excluded it, without changing what --formats reports.
        formats.append("pdf")

    intent = None
    plan_result = None
    analysis = None
    pipe = None
    if role_driven:
        # ask=None → deterministic authority (offline-safe default). --ask-cmd
        # inyecta el planner semántico (producción: OpenClaw/ai.ask); claims sin
        # evidencia se rechazan aguas arriba y cualquier fallo cae al determinista.
        ask_fn = _build_ask(args.ask_cmd) if getattr(args, "ask_cmd", None) else None
        job_name = args.job_name or (job_path.stem if job_path else "Role")
        stem = f"Nestor_Fleitas_{job_name.title()}"
        # Full evidence pipeline: JD → requirements → evidence → matching →
        # ranking → composition → factual validation → gates → artifacts.
        pipe = run_pipeline(
            bundle, jd_text, job_name=job_name, out_dir=out_dir, stem=stem,
            formats=formats, data_dir=args.data, ask=ask_fn)
        if not pipe.admitted:
            # Not a job description. Nothing is rendered, nothing is delivered,
            # and the caller is told what it actually received — the previous
            # behaviour was to generate a CV from a control message and then
            # reject it with "quality gate: JD_PARSED".
            gate = pipe.pipeline_gates[0]
            payload = {
                "admitted": False,
                "inputKind": pipe.debug["admission"]["kind"],
                "atsPassed": False,
                "atsFailures": [gate.name],
                "artifacts": {},
                "gates": [g.as_dict() for g in pipe.pipeline_gates],
                "reason": pipe.refusal,
                "diagnostic": gate.diagnostic,
                "recoverable": gate.recoverable,
                "replyText": _refusal_reply(pipe),
            }
            if args.json_out:
                print(json.dumps(payload))
                return 0
            print(f"NOT A JOB DESCRIPTION: {pipe.refusal}", file=sys.stderr)
            return 3
        plan_result = pipe.plan_result
        intent = pipe.plan_result.intent
        resume = pipe.resume
        written = list(pipe.written)
        profile_name = pipe.spec.primary_family or intent.primary_role
        analysis = analyze_job(bundle, jd_text)
        report = render_analysis_md(
            analysis, job_name=job_name, profile_name=profile_name,
            canonical_hash=resume.canonical_hash,
            matrix=pipe.matrix,
        )
        report_path = out_dir / f"{job_name}-analysis.md"
        report_path.write_text(report, encoding="utf-8")
        written.append(report_path)
    else:
        if profile_auto:
            selection = select_profile(jd_text or "", args.data, args.profiles)
            profile_name, profile_path = selection.name, selection.path
        else:
            profile_name = args.profile
            profile_path = Path(args.profiles) / f"{args.profile}.yaml"
        if not profile_path.exists():
            print(f"error: profile not found: {profile_path}", file=sys.stderr)
            return 2
        profile = load_profile(profile_path)
        if job_path:
            job_name = args.job_name or job_path.stem
            analysis = analyze_job(bundle, jd_text)
            resume = build_resume(
                bundle, profile, target=job_name,
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
    quality = None
    if args.validate:
        if role_driven and pipe is not None:
            # Scored quality report (ATS_SCORE/RECRUITER_SCORE) computed against
            # the REAL requirements matrix, plus the fail-closed pipeline gates.
            # atsPassed reflects both, so the NexusOS adapter blocks delivery on
            # an unsupported claim or omitted must-have evidence — not only on a
            # parse error.
            quality = evaluate_quality(
                artifacts["pdf"], jd_text=jd_text, bundle=bundle,
                intent=intent, data_dir=args.data, matrix=pipe.matrix)
            ats_passed = quality.passed and pipe.passed
            ats_failures = list(dict.fromkeys(quality.failures + pipe.failures))
        else:
            checks = run_ats_validation(artifacts["pdf"], args.data)
            ats_passed = all(c.ok for c in checks)
            ats_failures = [c.name for c in checks if not c.ok]

    if args.json_out:
        payload = {
            "profileSelected": profile_name,
            "profileAuto": profile_auto,
            "atsPassed": ats_passed,
            "atsFailures": ats_failures,
            "artifacts": artifacts,
        }
        if quality is not None:
            payload.update({
                "atsScore": quality.ats_score,
                "recruiterScore": quality.recruiter_score,
                "roleAlignment": quality.role_alignment,
                "keywordCoverage": quality.must_have_coverage,
                "unsupportedClaims": quality.unsupported_claims,
                "qualityReport": quality.render(),
            })
        if analysis is not None:
            # El gap analysis ya se calcula para el reporte .md; exponerlo en el
            # JSON evita que un consumidor tenga que parsear markdown o importar
            # internals de este paquete para saber que le falta al candidato.
            payload["jobMatch"] = {
                "covered": list(analysis.covered),
                "transferable": list(analysis.transferable),
                "gaps": list(analysis.gaps),
                "matchedSkills": list(analysis.matched_skills),
            }
        if intent is not None:
            payload["roleIntent"] = {
                "primaryRole": intent.primary_role,
                "roleWeights": intent.role_weights,
                "seniority": intent.seniority,
                "source": intent.source,
            }
        if pipe is not None:
            # Everything a caller needs to explain the CV to a human without
            # parsing markdown: gates, gaps, omissions and the debug artifact.
            payload.update({
                "targetTitle": pipe.resume.headline,
                "targetTagline": pipe.resume.tagline,
                "mustHaveCoverage": pipe.coverage,
                "matchScore": round(pipe.coverage * 100),
                "renderedKeywordCoverage": pipe.rendered,
                "claimableNotRendered": pipe.rendered_missing,
                "gates": [{"name": g.name, "ok": g.ok, "detail": g.detail}
                          for g in pipe.gates],
                "gateFailures": pipe.failures,
                "gapsNotIncluded": pipe.gap_labels(),
                "gapsAll": [m.term for m in pipe.matrix.gaps()],
                "partialEvidence": [m.term for m in pipe.matrix.partials()],
                "unsupportedClaims": [c.term for c in pipe.report.unsupported_claims],
                "relevantEvidenceOmitted": [
                    {"term": o.term, "surfaced": o.surfaced, "required": o.required,
                     "missing": o.missing_experiences}
                    for o in pipe.report.omissions],
                "admitted": True,
                "targetRole": pipe.spec.target_role,
                "primaryFamily": pipe.spec.primary_family,
                "secondaryDomains": pipe.spec.secondary_domains,
                "seniorityRequestedByJD": pipe.spec.seniority_requested,
                "candidateHeadline": pipe.resume.headline,
                "headlineAudit": pipe.headline_audit,
                "artifactFindings": [f.__dict__ for f in pipe.artifact_findings],
                "pipelineGates": [g.as_dict() for g in pipe.pipeline_gates],
                "supportedCount": len(pipe.matrix.claimable()),
                "partialCount": len(pipe.matrix.partials()),
                "unsupportedCount": len(pipe.matrix.unsupported()),
                "unknownCount": len(pipe.matrix.unknowns()),
                "recompositionPasses": pipe.iterations,
                "pages": pipe.pages,
                "debugReport": str(out_dir / f"{job_name}-debug.json"),
                "requirementsExtracted": len(pipe.spec.requirements),
                "mustHaveRequirements": len(pipe.spec.musts()),
            })
        if plan_result is not None:
            payload["plannerAudit"] = plan_result.audit(
                jd_text=jd_text or "", bundle=bundle)
        print(json.dumps(payload))
        return 0

    if not resume.email:
        print("warning: no email resolved (set RESUME_EMAIL or data/contact.local.yaml)",
              file=sys.stderr)

    print(f"Generated ({profile_name}"
          + (f" / {job_path}" if job_path else "") + "):")
    if pipe is not None:
        print(f"  TARGET_TITLE: {pipe.resume.headline}"
              + (f" | {pipe.resume.tagline}" if pipe.resume.tagline else ""))
        print("QUALITY_GATES")
        for g in pipe.gates:
            print(f"  {'PASS' if g.ok else 'FAIL'}  {g.name:34s} {g.detail}")
        gaps = pipe.gap_labels(limit=10)
        print("  GAPS_NOT_CLAIMED: " + (", ".join(gaps) if gaps else "—"))
    if quality is not None:
        print(quality.render())
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
    g.add_argument("--ask-cmd", default=None,
                   help="shell command for the semantic planner: prompt on "
                        "stdin, LLM answer on stdout (production: OpenClaw)")
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
