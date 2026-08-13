"""Scored quality gates: ATS_SCORE, RECRUITER_SCORE, keyword coverage and a
fail-closed CV_QUALITY_REPORT. Scores are computed from the REAL artifacts and
the JD — never hardcoded — reusing the parse-safety checks in validate.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .evidence import EvidenceAuthority
from .models import DataBundle
from .roleintent import ROLE_FAMILIES, RoleIntent
from .validate import extract_text, run_ats_validation


@dataclass
class KeywordReport:
    matched_exact: list[str] = field(default_factory=list)
    matched_semantic: list[str] = field(default_factory=list)
    missing_supported: list[str] = field(default_factory=list)     # evidence exists, CV omits
    missing_unsupported: list[str] = field(default_factory=list)   # no evidence (correct)

    def coverage(self) -> float:
        total = (len(self.matched_exact) + len(self.matched_semantic)
                 + len(self.missing_supported))
        if total == 0:
            return 1.0
        return round((len(self.matched_exact) + len(self.matched_semantic)) / total, 4)


@dataclass
class QualityReport:
    ats_score: float
    recruiter_score: float
    role_alignment: float
    must_have_coverage: float
    supported_keyword_coverage: float
    unsupported_claims: int
    chronology_ok: bool
    sections_ok: bool
    pdf_parse_ok: bool
    docx_parse_ok: bool
    identity_ok: bool
    keyword_report: KeywordReport
    passed: bool
    failures: list[str] = field(default_factory=list)

    def render(self) -> str:
        kr = self.keyword_report
        lines = [
            "CV_QUALITY_REPORT",
            f"  ATS_SCORE:                {self.ats_score:.1f}/10",
            f"  RECRUITER_SCORE:          {self.recruiter_score:.1f}/10",
            f"  ROLE_ALIGNMENT:           {round(self.role_alignment*100)}%",
            f"  MUST_HAVE_COVERAGE:       {round(self.must_have_coverage*100)}%",
            f"  SUPPORTED_KEYWORDS:       {round(self.supported_keyword_coverage*100)}%",
            f"  UNSUPPORTED_CLAIMS:       {self.unsupported_claims}",
            f"  CHRONOLOGY:               {'PASS' if self.chronology_ok else 'FAIL'}",
            f"  SECTIONS:                 {'PASS' if self.sections_ok else 'FAIL'}",
            f"  PDF_PARSE:                {'PASS' if self.pdf_parse_ok else 'FAIL'}",
            f"  DOCX_PARSE:               {'PASS' if self.docx_parse_ok else 'FAIL'}",
            f"  IDENTITY_CONSISTENCY:     {'PASS' if self.identity_ok else 'FAIL'}",
            f"  keywords matched(exact):  {', '.join(kr.matched_exact) or '—'}",
            f"  keywords matched(semant): {', '.join(kr.matched_semantic) or '—'}",
            f"  missing_supported(FIX):   {', '.join(kr.missing_supported) or '—'}",
            f"  missing_unsupported(OK):  {', '.join(kr.missing_unsupported) or '—'}",
            f"  RESULT:                   {'GO' if self.passed else 'NO_GO'}",
        ]
        if self.failures:
            lines.append("  failures: " + "; ".join(self.failures))
        return "\n".join(lines)


def _jd_keywords(jd_text: str, intent: RoleIntent) -> list[str]:
    """Union of the JD's role-family keywords actually present + intent tags."""
    low = re.sub(r"\s+", " ", jd_text.lower())
    kws: list[str] = []
    for fam in intent.role_weights:
        for kw in ROLE_FAMILIES[fam]["keywords"]:
            if re.search(rf"(?<![\w]){re.escape(kw)}(?![\w])", low) and kw not in kws:
                kws.append(kw)
    for t in intent.reasoning_tags:
        if t not in kws:
            kws.append(t)
    return kws


def keyword_report(jd_text: str, cv_text: str, bundle: DataBundle,
                   intent: RoleIntent) -> KeywordReport:
    authority = EvidenceAuthority(bundle)
    cv_low = cv_text.lower()
    report = KeywordReport()
    for kw in _jd_keywords(jd_text, intent):
        present = re.search(rf"(?<![\w]){re.escape(kw)}(?![\w])", cv_low) is not None
        canonical = authority.resolve_concept(kw)
        if present:
            report.matched_exact.append(kw)
        elif canonical:
            # evidence exists; is the canonical skill (semantic equivalent) present?
            if canonical.lower() in cv_low:
                report.matched_semantic.append(kw)
            else:
                report.missing_supported.append(kw)      # auto-fixable
        else:
            report.missing_unsupported.append(kw)         # correctly omitted
    return report


def evaluate(pdf_path: str | Path, *, jd_text: str, bundle: DataBundle,
             intent: RoleIntent, data_dir: str | Path = "data",
             min_ats: float = 8.5, min_recruiter: float = 8.0) -> QualityReport:
    pdf_path = Path(pdf_path)
    checks = {c.name: c for c in run_ats_validation(pdf_path, data_dir)}
    cv_text = extract_text(pdf_path)
    kr = keyword_report(jd_text, cv_text, bundle, intent)

    parse_ok = checks.get("selectable text (not rasterized)")
    pdf_parse_ok = bool(parse_ok and parse_ok.ok)
    sections_ok = bool(checks.get("heading parsing & order") and checks["heading parsing & order"].ok)
    chronology_ok = bool(checks.get("experience chronology") and checks["experience chronology"].ok)
    no_invented = bool(checks.get("no invented/unclaimed skills") and checks["no invented/unclaimed skills"].ok)
    dates_ok = bool(checks.get("canonical dates (no invented years)") and checks["canonical dates (no invented years)"].ok)
    docx_check = checks.get("no layout tables (DOCX)")
    docx_parse_ok = bool(docx_check.ok) if docx_check else True

    # identity: the composed headline noun must be recoverable from the text
    identity_ok = intent.job_title.split()[0].lower() in cv_text.lower() if intent.job_title else True

    must_have = kr.coverage()
    supported = must_have
    role_alignment = _role_alignment(cv_text, intent)
    unsupported = 0 if (no_invented and dates_ok) else 1

    # ── ATS_SCORE (0-10): parse-safety + structure + keyword coverage + density
    parse_pts = sum(1 for n in [
        "text extraction", "selectable text (not rasterized)", "no decorative images",
        "URLs are selectable text", "no layout tables (DOCX)", "page count <= 2",
    ] if checks.get(n) and checks[n].ok)
    parse_score = parse_pts / 6.0                      # 0..1
    structure_score = 1.0 if (sections_ok and chronology_ok) else 0.5
    ats_score = round(10 * (0.45 * parse_score + 0.20 * structure_score
                            + 0.35 * must_have), 2)

    # ── RECRUITER_SCORE (0-10): role alignment + coverage + credibility + density
    credibility = 1.0 if unsupported == 0 else 0.0
    density = _density_score(cv_text)
    recruiter_score = round(10 * (0.40 * role_alignment + 0.25 * must_have
                                  + 0.20 * credibility + 0.15 * density), 2)

    failures: list[str] = []
    if ats_score < min_ats:
        failures.append(f"ATS_SCORE {ats_score} < {min_ats}")
    if recruiter_score < min_recruiter:
        failures.append(f"RECRUITER_SCORE {recruiter_score} < {min_recruiter}")
    if unsupported > 0:
        failures.append("unsupported_claims > 0")
    if not chronology_ok:
        failures.append("chronology_error")
    if not sections_ok:
        failures.append("missing_required_section")
    if not pdf_parse_ok:
        failures.append("pdf_parse_error")
    if not docx_parse_ok:
        failures.append("docx_parse_error")

    return QualityReport(
        ats_score=ats_score, recruiter_score=recruiter_score,
        role_alignment=role_alignment, must_have_coverage=must_have,
        supported_keyword_coverage=supported, unsupported_claims=unsupported,
        chronology_ok=chronology_ok, sections_ok=sections_ok,
        pdf_parse_ok=pdf_parse_ok, docx_parse_ok=docx_parse_ok,
        identity_ok=identity_ok, keyword_report=kr,
        passed=not failures, failures=failures,
    )


def _role_alignment(cv_text: str, intent: RoleIntent) -> float:
    """Fidelidad del CV al ROLE_INTENT: máx(tags, familias).

    Los reasoning_tags son frases del JD (pueden venir en otro idioma o como
    frases compuestas que jamás aparecen verbatim en un CV en inglés — msg
    8275: "comportamiento de sistemas de IA" ⇒ alignment 0.4 artificial). La
    señal por FAMILIAS es idioma-neutral: qué fracción ponderada de las
    familias del intent tiene sus keywords canónicas (inglés) expresadas en el
    CV. Un CV realmente desalineado sigue puntuando bajo en ambas señales.
    """
    low = cv_text.lower()

    def _hit(term: str) -> bool:
        return re.search(rf"(?<![\w]){re.escape(term.lower())}(?![\w])",
                         low) is not None

    tags = intent.reasoning_tags or list(
        ROLE_FAMILIES[intent.primary_role]["keywords"])[:6]
    tag_score = (sum(1 for t in tags if _hit(t)) / len(tags)) if tags else 1.0

    weights = intent.role_weights or {intent.primary_role: 1.0}
    total = sum(weights.values()) or 1.0
    fam_score = 0.0
    for fam, w in weights.items():
        spec = ROLE_FAMILIES.get(fam)
        if spec is None:
            continue
        if any(_hit(kw) for kw in spec["keywords"]):
            fam_score += w / total
    return round(max(tag_score, fam_score), 4)


def _density_score(cv_text: str) -> float:
    """Reward substantive content, penalize slogan lines and over-long bullets."""
    lines = [l.strip() for l in cv_text.splitlines() if l.strip()]
    if not lines:
        return 0.0
    slogans = sum(1 for l in lines if l.endswith(".") and len(l.split()) <= 4)
    long_bullets = sum(1 for l in lines if len(l.split()) > 34)
    penalty = (slogans + long_bullets) / max(1, len(lines))
    return round(max(0.0, 1.0 - 2 * penalty), 4)
