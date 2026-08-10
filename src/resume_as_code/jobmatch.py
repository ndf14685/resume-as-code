"""Deterministic Job-Description analysis.

Compares a JD against the canonical skills catalog and classifies every notable
term into one of three honest buckets:

  * covered      -- a canonical skill (real evidence exists)
  * transferable -- an external tool we do NOT claim, but for which we have
                    genuine transferable evidence in a related canonical skill
  * gap          -- required, but no evidence: explicitly NOT claimed

No LLM, no network. It never invents a skill; it can only surface canonical
skills the candidate already has. This is the anti-keyword-stuffing guard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import DataBundle

# --------------------------------------------------------------------------- #
# External-tool lexicon: tools/terms that are NOT in the canonical catalog.
# `transferable_to` lists canonical skills that constitute honest, related
# evidence. Empty transferable_to => hard gap.
# --------------------------------------------------------------------------- #
EXTERNAL_TERMS: dict[str, dict] = {
    "cloudflare workers": {
        "transferable_to": ["AWS", "Node.js", "Distributed Systems", "AWS CDK"],
        "note": "serverless / edge backend (AWS Lambda-style, Node.js microservices on AWS)",
    },
    "cloudflare": {
        "transferable_to": ["Amazon CloudFront", "AWS"],
        "note": "CDN / edge networking (operated Amazon CloudFront)",
    },
    "planetscale": {
        "transferable_to": ["Amazon RDS"],
        "note": "managed relational databases (operated Amazon RDS)",
    },
    "drizzle orm": {
        "transferable_to": ["Node.js", "Java", "Amazon RDS"],
        "note": "backend data access & schema management",
    },
    "drizzle": {
        "transferable_to": ["Node.js", "Amazon RDS"],
        "note": "backend data access & schema management",
    },
    "grafana cloud": {
        "transferable_to": ["Observability"],
        "note": "metrics & observability tooling (operated monitoring stacks)",
    },
    "grafana": {
        "transferable_to": ["Observability"],
        "note": "metrics dashboards / observability",
    },
    "opentelemetry": {
        "transferable_to": ["Observability"],
        "note": "instrumentation, distributed tracing & metrics",
    },
    "sentry": {
        "transferable_to": ["Observability", "Incident Response"],
        "note": "error monitoring & incident response",
    },
    "datadog": {
        "transferable_to": ["Observability"],
        "note": "observability / monitoring",
    },
    "prometheus": {
        "transferable_to": ["Observability"],
        "note": "metrics / monitoring",
    },
    # Reliability / distributed-systems concepts (transferable capabilities).
    "webhooks": {
        "transferable_to": ["Node.js", "Distributed Systems", "AWS"],
        "note": "event-driven integrations",
    },
    "idempotency": {
        "transferable_to": ["Distributed Systems", "SRE Practices"],
        "note": "reliability engineering / distributed-systems design",
    },
    "rate limiting": {
        "transferable_to": ["Distributed Systems", "SRE Practices"],
        "note": "reliability engineering / distributed-systems design",
    },
    "retry handling": {
        "transferable_to": ["Distributed Systems", "SRE Practices", "Incident Response"],
        "note": "reliability engineering",
    },
    "queue": {
        "transferable_to": ["Google Pub/Sub", "Distributed Systems"],
        "note": "message queues / event streaming (Google Pub/Sub)",
    },
    "schema evolution": {
        "transferable_to": ["Amazon RDS", "Distributed Systems"],
        "note": "database schema management & migrations",
    },
    "database performance": {
        "transferable_to": ["Amazon RDS", "SRE Practices"],
        "note": "operating & tuning production databases",
    },
    "error recovery": {
        "transferable_to": ["SRE Practices", "Incident Response"],
        "note": "reliability engineering",
    },
    "queue resilience": {
        "transferable_to": ["Distributed Systems", "SRE Practices"],
        "note": "reliability engineering",
    },
}

# JD concept -> emphasis tags to bias bullet selection toward.
CONCEPT_TAGS: dict[str, list[str]] = {
    "reliability": ["reliability", "sre", "incident"],
    "reliable": ["reliability", "sre"],
    "observability": ["observability"],
    "monitoring": ["observability"],
    "tracing": ["observability"],
    "alerting": ["observability"],
    "incident": ["incident", "reliability"],
    "backend": ["backend"],
    "database": ["backend", "data"],
    "queue": ["data", "backend"],
    "security": ["security", "devsecops"],
    "distributed": ["backend", "architecture"],
    "system design": ["architecture", "backend"],
    "infrastructure": ["cloud", "iac"],
    "ci/cd": ["cicd"],
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def _contains(term: str, text: str) -> bool:
    if not term.isalnum() and (" " in term or "/" in term or "-" in term):
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


@dataclass
class JobAnalysis:
    covered: dict[str, list[str]] = field(default_factory=dict)      # skill -> evidence
    transferable: list[dict] = field(default_factory=list)           # {term, via, note}
    gaps: list[str] = field(default_factory=list)                    # external terms, no evidence
    matched_skills: set[str] = field(default_factory=set)            # canonical names to surface
    extra_emphasis: list[str] = field(default_factory=list)          # bullet emphasis tags
    detected_terms: list[str] = field(default_factory=list)


def analyze_job(bundle: DataBundle, jd_text: str) -> JobAnalysis:
    text = _normalize(jd_text)
    alias_index = bundle.skills.alias_index()
    evidence = bundle.evidence_index()
    result = JobAnalysis()

    # 1) Canonical skills directly present in the JD. Only surface skills that
    #    actually have evidence -- a catalog skill with no evidence is never
    #    claimed as covered.
    for alias, canonical in alias_index.items():
        if _contains(alias, text) and canonical in evidence:
            result.covered.setdefault(canonical, evidence[canonical])
            result.matched_skills.add(canonical)
            if canonical not in result.detected_terms:
                result.detected_terms.append(canonical)

    # 2) External tools/terms.
    for term, meta in EXTERNAL_TERMS.items():
        if not _contains(term, text):
            continue
        result.detected_terms.append(term)
        # transferable canonical skills that actually have evidence
        via = [s for s in meta["transferable_to"] if s in evidence]
        if via:
            result.transferable.append(
                {"term": term, "via": via, "note": meta["note"]}
            )
            result.matched_skills.update(via)
        else:
            result.gaps.append(term)

    # 3) Emphasis tags from JD concepts.
    emphasis: list[str] = []
    for concept, tags in CONCEPT_TAGS.items():
        if _contains(concept, text):
            for t in tags:
                if t not in emphasis:
                    emphasis.append(t)
    result.extra_emphasis = emphasis

    # de-dup gaps preserving order
    seen: set[str] = set()
    result.gaps = [g for g in result.gaps if not (g in seen or seen.add(g))]
    return result


def render_analysis_md(
    analysis: JobAnalysis,
    *,
    job_name: str,
    profile_name: str,
    canonical_hash: str,
) -> str:
    lines: list[str] = []
    lines.append(f"# Job Match Analysis — {job_name}")
    lines.append("")
    lines.append(f"- **Base profile:** `{profile_name}`")
    lines.append(f"- **Canonical fingerprint:** `{canonical_hash[:16]}…`")
    lines.append(
        "- **Method:** deterministic keyword/evidence matching against the "
        "canonical skills catalog. No experience, skill or metric is invented."
    )
    lines.append("")

    lines.append("## 1. Requirements covered (real evidence)")
    lines.append("")
    if analysis.covered:
        lines.append("| Skill | Evidence |")
        lines.append("| --- | --- |")
        for skill in sorted(analysis.covered):
            ev = analysis.covered[skill]
            uniq = ", ".join(dict.fromkeys(ev)) if ev else "—"
            lines.append(f"| {skill} | {uniq} |")
    else:
        lines.append("_None detected._")
    lines.append("")

    lines.append("## 2. Partially covered / transferable")
    lines.append("")
    lines.append(
        "These JD tools are **not claimed** as direct experience. The CV "
        "instead surfaces the related canonical skills below as *transferable* "
        "evidence."
    )
    lines.append("")
    if analysis.transferable:
        lines.append("| JD term (not claimed) | Transferable evidence | Rationale |")
        lines.append("| --- | --- | --- |")
        for item in analysis.transferable:
            via = ", ".join(item["via"])
            lines.append(f"| {item['term']} | {via} | {item['note']} |")
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## 3. Gaps (required, no evidence — NOT claimed)")
    lines.append("")
    if analysis.gaps:
        for g in analysis.gaps:
            lines.append(f"- **{g}** — no evidence in canonical data; deliberately omitted.")
    else:
        lines.append("_No hard gaps detected._")
    lines.append("")

    lines.append("## 4. Terms incorporated into the CV")
    lines.append("")
    lines.append(
        "Only canonical skills with real evidence are surfaced/emphasized:"
    )
    lines.append("")
    lines.append(", ".join(sorted(analysis.matched_skills)) or "_None._")
    lines.append("")

    lines.append("## 5. Terms deliberately NOT incorporated (anti keyword-stuffing)")
    lines.append("")
    not_incorporated = [t["term"] for t in analysis.transferable] + analysis.gaps
    if not_incorporated:
        lines.append(
            "These JD tools were detected but are **not** asserted as skills, "
            "because there is no direct evidence:"
        )
        lines.append("")
        for t in not_incorporated:
            lines.append(f"- {t}")
    else:
        lines.append("_None._")
    lines.append("")
    lines.append("---")
    lines.append(
        "_Generated by resume-as-code. Facts are protected by the canonical "
        "fingerprint; this report reflects selection, not invention._"
    )
    lines.append("")
    return "\n".join(lines)
