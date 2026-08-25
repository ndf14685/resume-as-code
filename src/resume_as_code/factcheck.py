"""FACTUAL VALIDATOR — an independent second pass over the finished CV.

Stages 11 and 12. This runs on the RENDERED artifact, not on the plan that
produced it, and it answers two questions the previous gate never asked:

  1. CLAIM → SOURCE EVIDENCE. Does every JD-relevant term that appears in the
     CV have evidence behind it? A requirement whose match verdict is NO or
     PARTIAL must not appear anywhere in the document. (The old validator only
     scanned the Core Skills block against a hardcoded lexicon of ~30 external
     tools, so a fabricated Veeam or Entra ID line in the experience section
     would have passed untouched.)

  2. Was relevant evidence LOST? If the JD's must-have is Azure and the
     knowledge base holds three Azure engagements but the CV surfaces one, that
     is a defect of the same severity as inventing a skill — it is reported as
     RELEVANT_EVIDENCE_OMITTED and it blocks the render until recomposed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .inventory import EvidenceInventory
from .jdspec import JobSpec, normalize, term_present
from .matching import (CAN_CLAIM_NO, CAN_CLAIM_PARTIAL, CAN_CLAIM_YES,
                       MatchMatrix, RankedExperience)
from .models import ResumeModel

# Dimensions whose must-haves are load-bearing for the role: losing evidence
# here changes whether the candidate reads as a fit. `os`/`scripting`/`domain`
# omissions are reported but do not block.
BLOCKING_DIMENSIONS = frozenset({
    "cloud_platform", "iac", "containers", "cicd", "identity", "networking",
    "backup_dr", "security_governance",
})

# Unambiguous Spanish function/JD words. The canonical dataset is written in
# English, so more than a couple of these in the body means JD text leaked into
# the document (a real failure mode when a summary is written from the ad).
_ES_LEAK = ("para", "con", "los", "las", "una", "segun", "mediante", "desde",
            "sobre", "entre", "cuando", "tambien", "puesto", "vacante",
            "requisitos", "deseables", "conocimientos", "experiencia",
            "administracion", "infraestructura", "seguridad", "empresas")


@dataclass
class ClaimFinding:
    term: str
    verdict: str            # NO | PARTIAL
    dimension: str
    where: str


@dataclass
class OmissionFinding:
    term: str
    dimension: str
    required: int
    surfaced: int
    missing_experiences: list[str] = field(default_factory=list)
    blocking: bool = True


@dataclass
class GateResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class FactCheckReport:
    unsupported_claims: list[ClaimFinding] = field(default_factory=list)
    omissions: list[OmissionFinding] = field(default_factory=list)
    language_ok: bool = True
    language_detail: str = ""
    title_ok: bool = True
    title_detail: str = ""

    @property
    def blocking_omissions(self) -> list[OmissionFinding]:
        return [o for o in self.omissions if o.blocking]

    def missing_experience_ids(self) -> set[str]:
        return {e for o in self.blocking_omissions for e in o.missing_experiences}


def _cv_body_text(resume: ResumeModel) -> str:
    """Everything the reader sees, minus the Languages section (which legitimately
    names Spanish) and minus link/handle noise."""
    chunks: list[str] = [resume.headline, resume.tagline or "", resume.summary]
    for group in resume.skill_groups:
        chunks.append(group.name)
        chunks.extend(group.items)
    for exp in resume.experiences:
        chunks.extend([exp.company, exp.title, exp.engagement_label or "",
                       exp.location or ""])
        chunks.extend(exp.bullets)
    for proj in resume.featured_projects:
        chunks.extend([proj.name, proj.label, proj.tagline or ""])
        chunks.extend(proj.bullets)
    return " \n".join(c for c in chunks if c)


def check_unsupported_claims(resume: ResumeModel, matrix: MatchMatrix) -> list[ClaimFinding]:
    """Any requirement we cannot claim must be absent from the document.

    Only `tool` requirements are scanned: a concept word like 'governance' or
    'automation' can appear in unrelated canonical text without asserting the
    JD's tool, whereas a product name cannot.
    """
    body = normalize(_cv_body_text(resume))
    findings: list[ClaimFinding] = []
    for m in matrix.matches:
        if m.can_claim == CAN_CLAIM_YES or m.requirement.kind != "tool":
            continue
        if term_present(m.term, body):
            findings.append(ClaimFinding(
                term=m.term, verdict=m.can_claim,
                dimension=m.requirement.dimension,
                where="rendered CV body"))
    return findings


def check_evidence_omissions(resume: ResumeModel, matrix: MatchMatrix,
                             inventory: EvidenceInventory) -> list[OmissionFinding]:
    """Must-have evidence present in the knowledge base but absent from the CV."""
    surfaced_companies = {
        e.company.lower() for e in resume.experiences if e.bullets and not e.condensed
    }
    findings: list[OmissionFinding] = []
    for m in matrix.matches:
        if not m.requirement.is_must or m.can_claim != CAN_CLAIM_YES:
            continue
        supporting = [i for i in m.experience_ids if inventory.by_id(i)]
        if len(supporting) < 2:
            continue                       # nothing to lose
        required = min(len(supporting), 3)
        shown, missing = [], []
        for exp_id in supporting:
            rec = inventory.by_id(exp_id)
            if rec and rec.company.lower() in surfaced_companies:
                shown.append(exp_id)
            elif rec:
                missing.append(exp_id)
        if len(shown) >= required:
            continue
        findings.append(OmissionFinding(
            term=m.term, dimension=m.requirement.dimension,
            required=required, surfaced=len(shown),
            missing_experiences=missing,
            blocking=m.requirement.dimension in BLOCKING_DIMENSIONS))
    return findings


def check_language(resume: ResumeModel) -> tuple[bool, str]:
    body = normalize(_cv_body_text(resume))
    hits = sorted({w for w in _ES_LEAK if term_present(w, body)})
    if len(hits) >= 3:
        return False, f"mixed-language leak: {', '.join(hits[:8])}"
    return True, "single-language document"


def check_title(resume: ResumeModel, spec: JobSpec) -> tuple[bool, str]:
    """The candidate's professional title must not be the vacancy's title."""
    head = normalize(resume.headline)
    for candidate in (spec.title, spec.raw_title):
        if candidate and normalize(candidate) == head:
            return False, f"target title copied verbatim from the JD: {candidate!r}"
    if head and normalize(spec.raw_title).startswith(head) and len(head.split()) >= 4:
        return False, "target title is a prefix of the vacancy title"
    return True, "composed from the candidate profile"


def run_factcheck(resume: ResumeModel, spec: JobSpec, matrix: MatchMatrix,
                  inventory: EvidenceInventory) -> FactCheckReport:
    report = FactCheckReport()
    report.unsupported_claims = check_unsupported_claims(resume, matrix)
    report.omissions = check_evidence_omissions(resume, matrix, inventory)
    report.language_ok, report.language_detail = check_language(resume)
    report.title_ok, report.title_detail = check_title(resume, spec)
    return report


# --------------------------------------------------------------------------- #
# 12. Quality gates — fail-closed, in pipeline order
# --------------------------------------------------------------------------- #
GATE_ORDER = (
    "JD_PARSED",
    "CANDIDATE_EVIDENCE_LOADED",
    "REQUIREMENTS_MATCHED",
    "RELEVANT_EXPERIENCE_RANKED",
    "NO_UNSUPPORTED_CLAIMS",
    "NO_RELEVANT_EVIDENCE_OMITTED",
    "ATS_KEYWORD_COVERAGE_ACCEPTABLE",
    "LANGUAGE_VALID",
    "TWO_PAGE_LIMIT",
    "PDF_RENDER_VALID",
)


def evaluate_gates(*, spec: JobSpec, inventory: EvidenceInventory,
                   matrix: MatchMatrix, ranked: list[RankedExperience],
                   report: FactCheckReport, coverage: float,
                   min_coverage: float, pages: Optional[int],
                   pdf_ok: bool, pdf_detail: str = "",
                   rendered: float = 1.0,
                   rendered_missing: Optional[list[str]] = None) -> list[GateResult]:
    gates: list[GateResult] = []

    gates.append(GateResult(
        "JD_PARSED", bool(spec.requirements),
        f"{len(spec.requirements)} requirements "
        f"({len(spec.musts())} must-have) across "
        f"{len(spec.dimension_weights())} dimensions"))

    gates.append(GateResult(
        "CANDIDATE_EVIDENCE_LOADED", bool(inventory.records),
        f"{len(inventory.records)} experiences, "
        f"{len(inventory.skill_evidence)} evidenced skills"))

    gates.append(GateResult(
        "REQUIREMENTS_MATCHED", len(matrix.matches) == len(spec.requirements),
        f"{len(matrix.claimable())} claimable, {len(matrix.partials())} partial, "
        f"{len(matrix.gaps())} gap(s)"))

    ranked_ok = bool(ranked) and any(r.bullet_budget > 0 for r in ranked)
    gates.append(GateResult(
        "RELEVANT_EXPERIENCE_RANKED", ranked_ok,
        "top: " + ", ".join(f"{r.record.company}({r.band})"
                            for r in sorted(ranked, key=lambda r: -r.jd_score)[:4])))

    gates.append(GateResult(
        "NO_UNSUPPORTED_CLAIMS", not report.unsupported_claims,
        "no unclaimable term rendered" if not report.unsupported_claims
        else "rendered without evidence: " + ", ".join(
            f"{c.term}({c.verdict})" for c in report.unsupported_claims)))

    blocking = report.blocking_omissions
    gates.append(GateResult(
        "NO_RELEVANT_EVIDENCE_OMITTED", not blocking,
        "all must-have evidence surfaced" if not blocking
        else "; ".join(f"{o.term}: {o.surfaced}/{o.required} experiences "
                       f"(missing {', '.join(o.missing_experiences)})"
                       for o in blocking)))

    missing = rendered_missing or []
    gates.append(GateResult(
        "ATS_KEYWORD_COVERAGE_ACCEPTABLE", rendered >= min_coverage,
        f"{round(rendered*100)}% of claimable requirements rendered "
        f"(threshold {round(min_coverage*100)}%); JD match "
        f"{round(coverage*100)}%"
        + (f"; not rendered: {', '.join(missing[:8])}" if missing else "")))

    gates.append(GateResult("LANGUAGE_VALID", report.language_ok,
                            report.language_detail))

    gates.append(GateResult(
        "TWO_PAGE_LIMIT", pages is not None and pages <= 2,
        f"{pages} page(s)" if pages is not None else "not rendered"))

    gates.append(GateResult("PDF_RENDER_VALID", pdf_ok,
                            pdf_detail or ("parse OK" if pdf_ok else "parse failed")))

    order = {name: i for i, name in enumerate(GATE_ORDER)}
    return sorted(gates, key=lambda g: order.get(g.name, 99))
