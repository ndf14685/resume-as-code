"""THE EVIDENCE PIPELINE — the ordered stages, with recomposition.

    JD → requirements model → evidence retrieval → evidence matching →
    relevancy ranking → CV composition → factual validation →
    ATS/quality validation → PDF

The old flow was JD → keywords → template: a closed keyword dictionary decided
positioning, bullet selection could not see the JD at all, and the quality gate
scored only the keywords it already knew about. Each stage below exists to close
one of those holes, and the loop at the end is what makes the factual validator
mean something: an omission or a third page does not just get reported, it
forces a recomposition and blocks the render if it cannot be resolved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .composition import compose_from_evidence, display_term
from .debugreport import build_debug_payload, write_debug
from .factcheck import (GateResult, Gate, evaluate_gates,
                        evaluate_pipeline_gates, run_factcheck,
                        scan_rendered_artifact)
from .inventory import build_inventory
from .jdspec import parse_jd
from .matching import (assign_bullet_budget, build_match_matrix,
                       rank_experiences, rendered_coverage, select_bullets)
from .models import DataBundle, ResumeModel
from .render_docx import render_docx
from .render_pdf import render_pdf
from .render_txt import render_txt
from .semantic_planner import plan as semantic_plan
from .tailor import build_from_plan
from .validate import extract_text, run_ats_validation

MAX_PASSES = 4
# Threshold for the ATS gate: of the requirements we are ENTITLED to
# claim, how many must actually reach the page. Not a judgement on how
# well the candidate matches the vacancy — that is `matchScore`.
MIN_RENDERED_COVERAGE = 0.90


@dataclass
class PipelineResult:
    resume: Any
    spec: Any
    inventory: Any
    matrix: Any
    ranked: list
    plan_result: Any
    gates: list[GateResult]
    report: Any
    coverage: float
    rendered: float = 1.0
    rendered_missing: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    written: list[Path] = field(default_factory=list)
    pages: Optional[int] = None
    iterations: list[dict[str, Any]] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)
    pipeline_gates: list[Gate] = field(default_factory=list)
    artifact_findings: list = field(default_factory=list)
    admitted: bool = True
    refusal: str = ""
    headline_audit: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.admitted and all(g.ok for g in self.pipeline_gates)

    @property
    def failures(self) -> list[str]:
        return [g.name for g in self.pipeline_gates if not g.ok]

    def gap_labels(self, *, musts_only: bool = True, limit: int = 8) -> list[str]:
        """Gaps worth telling a human about, most actionable first.

        Named products lead: "Veeam" and "Microsoft Entra ID" tell the operator
        something they can act on (learn it, or skip the vacancy); "backup" and
        "compliance" are categories those products already imply.
        """
        gaps = self.matrix.gaps(musts_only=musts_only)
        gaps.sort(key=lambda m: (
            0 if m.requirement.kind == "tool" else 1,
            -(m.requirement.weight * (1 + 0.3 * m.requirement.mentions)),
            m.term))
        return [display_term(m) for m in gaps[:limit]]


def _write(resume: ResumeModel, out_dir: Path, stem: str,
           formats: list[str]) -> tuple[dict[str, str], list[Path]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    artifacts: dict[str, str] = {}
    if "txt" in formats:
        p = out_dir / f"{stem}.txt"
        p.write_text(render_txt(resume), encoding="utf-8")
        written.append(p)
        artifacts["txt"] = str(p)
    if "docx" in formats:
        p = render_docx(resume, out_dir / f"{stem}.docx")
        written.append(p)
        artifacts["docx"] = str(p)
    if "pdf" in formats:
        p = render_pdf(resume, out_dir / f"{stem}.pdf")
        written.append(p)
        artifacts["pdf"] = str(p)
    return artifacts, written


def run_pipeline(bundle: DataBundle, jd_text: str, *, job_name: str,
                 out_dir: Path, stem: str, formats: list[str],
                 data_dir: str | Path = "data",
                 ask: Optional[Callable[[str], str]] = None,
                 write_debug_artifacts: bool = True) -> PipelineResult:
    out_dir = Path(out_dir)

    # 1. JD → requirements model
    spec = parse_jd(jd_text)

    # 1b. ADMISSION. A control message, a greeting or a complaint is not a
    #     vacancy. Before this gate existed, every Telegram message in the CV
    #     topic became a "job description": a 44-char "no me des respuestas"
    #     parsed to zero requirements and surfaced as a quality-gate rejection,
    #     and a 70-char complaint scored 100% with no gaps and shipped a PDF.
    if spec.admission is not None and not spec.admission.accepted:
        gate = Gate("G1_JD_VALID", False, reason=spec.admission.reason,
                    diagnostic=f"kind={spec.admission.kind} "
                               f"signals={spec.admission.signals}",
                    recoverable=True)
        return PipelineResult(
            resume=None, spec=spec, inventory=None, matrix=None, ranked=[],
            plan_result=None, gates=[], report=None, coverage=0.0,
            pipeline_gates=[gate], admitted=False,
            refusal=spec.admission.reason,
            debug={"admission": {"accepted": False, "kind": spec.admission.kind,
                                 "reason": spec.admission.reason,
                                 "signals": spec.admission.signals}})

    # 2. Evidence inventory from ALL canonical sources (never a previous CV)
    inventory = build_inventory(bundle)

    # Positioning proposal (LLM optional, deterministic authority, unchanged
    # contract). Only decides role family weights — never facts.
    plan_result = semantic_plan(jd_text, bundle, ask=ask)
    intent = plan_result.intent
    if not intent.job_title:
        intent.job_title = spec.title

    # 3. Requirement ↔ evidence matching
    matrix = build_match_matrix(spec, bundle, inventory)
    coverage = matrix.coverage(musts_only=True)

    # 4. Relevancy ranking + bullet budget
    ranked = rank_experiences(spec, matrix, inventory)
    # Start generous and let the render loop below shrink it: the real
    # constraint is the two-page limit measured on the rendered PDF, not a
    # guessed line count. A tight guess silently cost must-have evidence
    # (Flux IT's Azure bullet) that the page had room for.
    total_bullets = 24
    forced: set[str] = set()
    assign_bullet_budget(ranked, matrix, total_bullets=total_bullets, forced=forced)

    iterations: list[dict[str, Any]] = []
    resume = None
    artifacts: dict[str, str] = {}
    written: list[Path] = []
    report = None
    pages: Optional[int] = None

    exp_by_id = {e.id: e for e in bundle.experiences}
    for attempt in range(1, MAX_PASSES + 1):
        # 5. Composition
        comp = compose_from_evidence(bundle, spec, intent, matrix, inventory, ranked)

        def selector(exp, limit, emphasis, _matrix=matrix, _inv=inventory, _spec=spec):
            rec = _inv.by_id(exp.id)
            if rec is None:
                return []
            return select_bullets(rec, _spec, _matrix, limit, emphasis)

        resume = build_from_plan(
            bundle, comp, target=job_name,
            matched_skills=matrix.claimable_skills(),
            bullet_selector=selector)

        artifacts, written = _write(resume, out_dir, stem, formats)

        pdf_path = artifacts.get("pdf")
        pages = None
        if pdf_path and Path(pdf_path).exists():
            from pypdf import PdfReader
            pages = len(PdfReader(pdf_path).pages)

        # 6. Factual validation — independent second pass over the artifact
        report = run_factcheck(resume, spec, matrix, inventory)

        missing = report.missing_experience_ids()
        if missing and attempt < MAX_PASSES:
            forced |= missing
            iterations.append({
                "pass": attempt,
                "reason": "RELEVANT_EVIDENCE_OMITTED: " + "; ".join(
                    f"{o.term} {o.surfaced}/{o.required}"
                    for o in report.blocking_omissions),
                "action": "pinned " + ", ".join(sorted(missing)) + " and recomposed",
            })
            assign_bullet_budget(ranked, matrix, total_bullets=total_bullets,
                                 forced=forced)
            continue

        if pages is not None and pages > 2 and attempt < MAX_PASSES:
            total_bullets = max(12, total_bullets - 3)
            iterations.append({
                "pass": attempt,
                "reason": f"TWO_PAGE_LIMIT: rendered {pages} pages",
                "action": f"reduced the global bullet budget to {total_bullets} "
                          "(least relevant experiences shed first)",
            })
            assign_bullet_budget(ranked, matrix, total_bullets=total_bullets,
                                 forced=forced)
            continue
        break

    # 7. ATS / structural validation on the rendered artifact
    pdf_ok, pdf_detail = True, "not rendered"
    if artifacts.get("pdf") and Path(artifacts["pdf"]).exists():
        checks = {c.name: c for c in run_ats_validation(artifacts["pdf"], data_dir)}
        blocking = ["text extraction", "selectable text (not rasterized)",
                    "heading parsing & order", "experience chronology",
                    "canonical dates (no invented years)",
                    "no invented/unclaimed skills"]
        failed = [n for n in blocking if n in checks and not checks[n].ok]
        pdf_ok = not failed
        pdf_detail = "structural checks passed" if pdf_ok else \
            "failed: " + ", ".join(failed)

    cv_text = extract_text(artifacts["pdf"]) if artifacts.get("pdf")         and Path(artifacts["pdf"]).exists() else render_txt(resume)
    rendered, rendered_missing = rendered_coverage(matrix, cv_text)

    # 6b. Scan the artifact the user will actually receive.
    from .headline import forbidden_claim_terms
    artifact_findings = []
    if artifacts.get("pdf") and Path(artifacts["pdf"]).exists():
        artifact_findings = scan_rendered_artifact(
            artifacts["pdf"],
            forbidden_terms=forbidden_claim_terms(inventory, matrix),
            target_role=getattr(spec, "target_role", ""),
            headline=f"{resume.headline} {resume.tagline or ''}",
            granted_seniority=(getattr(comp, "headline_audit", {}) or {}).get("seniority"))

    gates = evaluate_gates(
        spec=spec, inventory=inventory, matrix=matrix, ranked=ranked,
        report=report, coverage=coverage, min_coverage=MIN_RENDERED_COVERAGE,
        pages=pages, pdf_ok=pdf_ok, pdf_detail=pdf_detail,
        rendered=rendered, rendered_missing=rendered_missing)
    pipeline_gates = evaluate_pipeline_gates(
        spec=spec, inventory=inventory, matrix=matrix, ranked=ranked,
        report=report, artifact_findings=artifact_findings, pages=pages,
        pdf_ok=pdf_ok, pdf_detail=pdf_detail, rendered=rendered,
        rendered_missing=rendered_missing)

    payload_extra = {
        "classification": {
            "targetRole": spec.target_role,
            "primaryFamily": spec.primary_family,
            "secondaryDomains": spec.secondary_domains,
            "seniorityRequestedByJD": spec.seniority_requested,
            "admission": {"accepted": True, "kind": spec.admission.kind,
                          "signals": spec.admission.signals}
                         if spec.admission else {},
        },
        "headline": getattr(comp, "headline_audit", {}),
        "artifactFindings": [f.__dict__ for f in artifact_findings],
        "pipelineGates": [g.as_dict() for g in pipeline_gates],
    }
    payload = build_debug_payload(
        jd_text=jd_text, spec=spec, inventory=inventory, matrix=matrix,
        ranked=ranked, resume=resume, report=report, gates=gates,
        coverage=coverage, iterations=iterations, artifacts=artifacts)
    payload.update(payload_extra)
    if write_debug_artifacts:
        written.extend(write_debug(out_dir, job_name, payload))

    return PipelineResult(
        resume=resume, spec=spec, inventory=inventory, matrix=matrix,
        ranked=ranked, plan_result=plan_result, gates=gates, report=report,
        coverage=coverage, rendered=rendered, rendered_missing=rendered_missing,
        artifacts=artifacts, written=written, pages=pages,
        iterations=iterations, debug=payload,
        pipeline_gates=pipeline_gates, artifact_findings=artifact_findings,
        headline_audit=getattr(comp, "headline_audit", {}))
