"""DEBUG / OBSERVABILITY ARTIFACT — why every line of this CV is there.

Stage 13. One JSON + one Markdown file per generation, persisted next to the
artifacts, answering: which JD was read, what requirements were extracted, what
evidence was retrieved, how each requirement matched, which experiences were
selected or dropped and why, which keywords are covered, which are not, what
the factual validator found, and which version was actually rendered.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from .factcheck import FactCheckReport, GateResult
from .inventory import EvidenceInventory
from .jdspec import JobSpec
from .matching import (MatchMatrix, RankedExperience, match_breakdown,
                       render_match_breakdown)
from .models import ResumeModel


def build_debug_payload(*, jd_text: str, spec: JobSpec, inventory: EvidenceInventory,
                        matrix: MatchMatrix, ranked: list[RankedExperience],
                        resume: ResumeModel, report: FactCheckReport,
                        gates: list[GateResult], coverage: float,
                        iterations: list[dict[str, Any]],
                        artifacts: dict[str, str]) -> dict[str, Any]:
    return {
        "jd": {
            "hash": hashlib.sha256(jd_text.encode()).hexdigest()[:16],
            "rawTitle": spec.raw_title,
            "cleanTitle": spec.title,
            "seniority": spec.seniority,
            "dimensionWeights": spec.dimension_weights(),
            "sectionsDetected": {k: len(v) for k, v in spec.sections.items()},
        },
        "requirements": [
            {"term": r.term, "dimension": r.dimension, "priority": r.priority,
             "kind": r.kind, "weight": r.weight, "jdLines": r.sources}
            for r in spec.requirements
        ],
        "evidenceInventory": [
            {"id": rec.id, "company": rec.company, "client": rec.client,
             "industry": rec.industry, "role": rec.title,
             "engagement": rec.engagement, "dates": f"{rec.start}..{rec.end}",
             "skills": rec.skills, "facets": rec.facets,
             "bulletCount": len(rec.bullets)}
            for rec in inventory.records
        ],
        "matchMatrix": [
            {"requirement": m.term, "dimension": m.requirement.dimension,
             "priority": m.requirement.priority, "weight": m.requirement.weight,
             "canClaim": m.can_claim, "confidence": m.confidence,
             "evidenceFound": m.evidence, "source": m.source,
             "experiencesSupportingIt": m.experience_ids, "note": m.note}
            for m in matrix.matches
        ],
        "matchBreakdown": match_breakdown(matrix),
        "unknowns": [
            {"requirement": m.term, "dimension": m.requirement.dimension,
             "priority": m.requirement.priority, "reason": m.note}
            for m in matrix.unknowns()
        ],
        "gaps": [
            {"requirement": m.term, "dimension": m.requirement.dimension,
             "priority": m.requirement.priority, "status": m.can_claim,
             "reason": m.note}
            for m in matrix.gaps()
        ],
        "partials": [
            {"requirement": m.term, "adjacentEvidence": m.evidence,
             "note": m.note} for m in matrix.partials()
        ],
        "ranking": [
            {"id": r.record.id, "company": r.record.label, "jdScore": r.jd_score,
             "score": r.score, "band": r.band, "bulletBudget": r.bullet_budget,
             "matchedRequirements": r.matched_terms,
             "mustHavesSupported": r.must_terms,
             "selected": r.bullet_budget > 0,
             "rationale": r.reasons}
            for r in sorted(ranked, key=lambda r: -r.jd_score)
        ],
        "experiencesSelected": [r.record.id for r in ranked if r.bullet_budget > 0],
        "experiencesCondensed": [r.record.id for r in ranked if r.bullet_budget == 0],
        "keywords": {
            "covered": [m.term for m in matrix.claimable()],
            "notCovered": [m.term for m in matrix.gaps()],
            "weightedMustHaveCoverage": coverage,
        },
        "factualValidation": {
            "unsupportedClaims": [
                {"term": c.term, "verdict": c.verdict, "dimension": c.dimension,
                 "where": c.where} for c in report.unsupported_claims],
            "relevantEvidenceOmitted": [
                {"term": o.term, "dimension": o.dimension, "required": o.required,
                 "surfaced": o.surfaced, "missing": o.missing_experiences,
                 "blocking": o.blocking} for o in report.omissions],
            "languageValid": report.language_ok,
            "languageDetail": report.language_detail,
            "targetTitleValid": report.title_ok,
            "targetTitleDetail": report.title_detail,
        },
        "gates": [{"name": g.name, "ok": g.ok, "detail": g.detail} for g in gates],
        "recomposition": iterations,
        "finalVersion": {
            "headline": resume.headline,
            "tagline": resume.tagline,
            "summary": resume.summary,
            "canonicalHash": resume.canonical_hash,
            "skillGroups": [{"group": g.name, "items": g.items}
                            for g in resume.skill_groups],
            "experiences": [
                {"company": e.company, "title": e.title, "dates": e.meta_right,
                 "condensed": e.condensed, "bullets": e.bullets}
                for e in resume.experiences],
            "artifacts": artifacts,
        },
    }


def render_debug_md(payload: dict[str, Any]) -> str:
    jd = payload["jd"]
    lines: list[str] = [
        f"# CV generation debug — {jd['cleanTitle'] or jd['rawTitle'] or 'role'}",
        "",
        f"- **JD hash:** `{jd['hash']}`",
        f"- **JD title (raw):** {jd['rawTitle'] or '—'}",
        f"- **JD title (cleaned):** {jd['cleanTitle'] or '—'}",
        f"- **Seniority detected:** {jd['seniority'] or '—'}",
        f"- **Target title composed:** {payload['finalVersion']['headline']}"
        f" | {payload['finalVersion']['tagline'] or ''}",
        f"- **Weighted must-have coverage:** "
        f"{round(payload['keywords']['weightedMustHaveCoverage'] * 100)}%",
        f"- **Canonical fingerprint:** `{payload['finalVersion']['canonicalHash'][:16]}…`",
        "",
        "## 1. Quality gates",
        "",
        "| Gate | Result | Detail |",
        "| --- | --- | --- |",
    ]
    for g in payload["gates"]:
        lines.append(f"| `{g['name']}` | {'PASS' if g['ok'] else '**FAIL**'} | {g['detail']} |")

    lines += ["", "## 2. Requirements extracted from the JD", "",
              "| Requirement | Dimension | Priority | Weight |",
              "| --- | --- | --- | --- |"]
    for r in payload["requirements"]:
        lines.append(f"| {r['term']} | {r['dimension']} | {r['priority']} | {r['weight']} |")

    lines += ["", "## 3. Match matrix (requirement → evidence)", "",
              "| Requirement | Can claim | Confidence | Evidence | Experiences | Source |",
              "| --- | --- | --- | --- | --- | --- |"]
    for m in payload["matchMatrix"]:
        lines.append(
            f"| {m['requirement']} | **{m['canClaim']}** | {m['confidence']} | "
            f"{', '.join(m['evidenceFound']) or '—'} | "
            f"{', '.join(m['experiencesSupportingIt']) or '—'} | {m['source']} |")

    lines += ["", "## 4. Match breakdown (the score, reconstructible)", "",
              "```", render_match_breakdown(payload["matchBreakdown"]), "```", "",
              "| Requirement | Dim | Prio | Status | Credit | Contribution | Evidence |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in sorted(payload["matchBreakdown"]["requirements"],
                    key=lambda r: (-r["score_contribution"], r["requirement"])):
        if not r["inScore"]:
            continue
        lines.append(
            f"| {r['requirement']} | {r['dimension']} | {r['priority']} | "
            f"**{r['status']}** | {r['credit']} | {r['score_contribution']:.6f} | "
            f"{', '.join(r['evidence_ids']) or '—'} |")

    lines += ["", "## 5. Gaps — required, not claimable", "",
              "`unsupported` = the dimension is evidenced and this is not part "
              "of it. `unknown` = no source covers this dimension, so a negative "
              "claim would be as unfounded as a positive one.", ""]
    if payload["gaps"]:
        for g in payload["gaps"]:
            lines.append(f"- **{g['requirement']}** ({g['dimension']}, "
                         f"{g['priority']}, `{g['status']}`) — {g['reason']}")
    else:
        lines.append("_None._")

    lines += ["", "## 6. Partial / adjacent evidence — reported, never rendered", ""]
    if payload["partials"]:
        for p in payload["partials"]:
            lines.append(f"- **{p['requirement']}** → adjacent: {', '.join(p['adjacentEvidence'])}")
    else:
        lines.append("_None._")

    lines += ["", "## 7. Relevancy ranking and bullet budget", "",
              "| Experience | JD score | Band | Bullets | Must-haves supported | Rationale |",
              "| --- | --- | --- | --- | --- | --- |"]
    for r in payload["ranking"]:
        lines.append(
            f"| {r['company']} | {r['jdScore']} | {r['band']} | {r['bulletBudget']} | "
            f"{', '.join(r['mustHavesSupported']) or '—'} | {'; '.join(r['rationale'])} |")

    fv = payload["factualValidation"]
    claims = ", ".join(c["term"] for c in fv["unsupportedClaims"]) or "none"
    omitted = "; ".join(
        "{term} {surfaced}/{required}".format(**o)
        for o in fv["relevantEvidenceOmitted"]) or "none"
    lines += ["", "## 8. Factual validation", "",
              f"- Unsupported claims rendered: {claims}",
              f"- Relevant evidence omitted: {omitted}",
              f"- Language: {fv['languageDetail']}",
              f"- Target title: {fv['targetTitleDetail']}"]

    if payload["recomposition"]:
        lines += ["", "## 9. Recomposition passes", ""]
        for i, it in enumerate(payload["recomposition"], 1):
            lines.append(f"{i}. {it.get('reason', '')} → {it.get('action', '')}")

    lines += ["", "---",
              "_Selection, not invention: every rendered line traces back to "
              "canonical data protected by the fingerprint above._", ""]
    return "\n".join(lines)


def write_debug(out_dir, job_name: str, payload: dict[str, Any]) -> list:
    from pathlib import Path
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{job_name}-debug.json"
    md_path = out_dir / f"{job_name}-debug.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    md_path.write_text(render_debug_md(payload), encoding="utf-8")
    return [json_path, md_path]
