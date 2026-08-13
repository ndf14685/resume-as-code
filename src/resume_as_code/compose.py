"""Role-driven composition: a ROLE_INTENT → headline, tagline, evidence-grounded
summary, skill ordering and a relevance-driven expand/compress plan.

This replaces the 'pick one of three fixed templates' model with positioning
composed from the JD. It invents nothing: the headline is anchored on the JD
title, the summary is assembled from real years/domains/evidenced skills, and
the expand/compress plan only reorders how much of each REAL role is shown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .models import DataBundle
from .roleintent import RoleIntent
from .evidence import rank_experiences

FAMILY_LABEL = {
    "software_engineering": "Software Engineer",
    "ai_systems": "AI Systems",
    "ai_platform": "AI Platform Engineer",
    "ai_architect": "AI Systems Architect",
    "platform_engineering": "Platform Engineer",
    "devops": "DevOps Engineer",
    "devsecops": "DevSecOps Engineer",
    "sre": "Site Reliability Engineer",
    "application_security": "Application Security Engineer",
    "cloud_security": "Cloud Security Engineer",
    "backend_engineering": "Backend Engineer",
    "presales_sales_support": "Sales Support / Cloud Engineer",
    "data_platforms": "Data Platform Engineer",
    "finops": "Cloud Engineer — FinOps",
}
FAMILY_TAGLINE = {
    "software_engineering": ["Distributed Systems", "APIs", "Backend Engineering"],
    "ai_systems": ["Agentic Platforms", "LLM Orchestration", "Multi-Agent Systems"],
    "ai_platform": ["Agent Runtimes", "Provider Routing", "AI Infrastructure"],
    "ai_architect": ["AI Governance", "Agentic Platforms", "Multi-Agent Systems"],
    "platform_engineering": ["Kubernetes", "Runtime Architecture", "Developer Platforms"],
    "devops": ["CI/CD", "Kubernetes", "Cloud Automation"],
    "devsecops": ["Secure SDLC", "SAST/SCA", "Pipeline Security"],
    "sre": ["Reliability", "Observability", "Incident Response"],
    "application_security": ["SAST/DAST", "Secure Coding", "Threat Modeling"],
    "cloud_security": ["Cloud Security", "IAM", "Kubernetes Security"],
    "backend_engineering": ["APIs", "Distributed Systems", "Java/Node"],
    "presales_sales_support": ["Cloud Infrastructure", "POC & Demo Environments",
                               "Technical Enablement"],
    "data_platforms": ["Data Pipelines", "Cloud Data Platforms", "ETL"],
    "finops": ["Cloud Cost Optimization", "Multi-Cloud", "Automation"],
}
FAMILY_EMPHASIS = {
    "software_engineering": ["backend", "architecture", "distributed", "data", "quality"],
    "ai_systems": ["ai", "governance", "architecture", "backend"],
    "ai_platform": ["ai", "platform", "cloud", "observability"],
    "ai_architect": ["ai", "governance", "security", "architecture", "platform"],
    "platform_engineering": ["platform", "cloud", "iac", "cicd", "observability"],
    "devops": ["cloud", "cicd", "iac", "automation", "containers"],
    "devsecops": ["devsecops", "security", "cicd", "cloud"],
    "sre": ["reliability", "observability", "incident", "cloud"],
    "application_security": ["security", "devsecops", "backend"],
    "cloud_security": ["security", "cloud", "devsecops"],
    "backend_engineering": ["backend", "data", "architecture", "integration"],
    "presales_sales_support": ["cloud", "architecture", "leadership", "platform"],
    "data_platforms": ["data", "cloud", "platform"],
    "finops": ["cloud", "reliability", "automation"],
}
_ENG_NOUN = re.compile(r"engineer|developer|architect|programmer|\bsre\b|specialist", re.I)


@dataclass
class ComposedPlan:
    headline: str
    tagline: str
    summary: str
    skill_priority: list[str]              # category ids, ordered
    emphasis_tags: list[str]
    expand: dict[str, int] = field(default_factory=dict)   # exp id -> bullet limit
    condense: set[str] = field(default_factory=set)        # exp ids shown as one line
    include_projects: list[str] = field(default_factory=list)
    profile_name: str = "role-driven"


def _clean_title(raw: str) -> str:
    t = re.sub(r"\s*[-–—]\s*", " — ", raw.strip().strip("*_# ").strip())
    t = re.sub(r"\s+", " ", t)
    return t


def _career_years(bundle: DataBundle) -> int:
    years = []
    for e in bundle.experiences:
        for tok in (e.start, e.end):
            m = re.search(r"(19|20)\d{2}", tok or "")
            if m:
                years.append(int(m.group(0)))
    if not years:
        return 0
    import datetime as _dt  # local; only for 'present'
    end = max(years + [2013])
    if any((e.end or "").lower() in ("present", "current") for e in bundle.experiences):
        end = max(end, 2026)
    return max(0, end - min(years))


def compose_headline(intent: RoleIntent) -> tuple[str, str]:
    """Headline anchored on the JD title (truthful to the role applied for);
    tagline built from the family mix. Never fabricates a seniority."""
    fams = intent.top_families(2)
    primary = fams[0] if fams else intent.primary_role
    title = _clean_title(intent.job_title) if intent.job_title else ""
    if title and _ENG_NOUN.search(title) and 2 <= len(title.split()) <= 7:
        headline = title
    else:
        # Family fallback: lead with the primary label only (avoid redundant
        # "X Engineer | Y Engineer"); specializations live in the tagline.
        headline = FAMILY_LABEL.get(primary, primary)
    # tagline: top emphasis phrases across the family mix, de-duplicated
    phrases: list[str] = []
    for f in fams:
        for p in FAMILY_TAGLINE.get(f, []):
            if p not in phrases and p.lower() not in headline.lower():
                phrases.append(p)
    return headline, " · ".join(phrases[:3])


def _top_evidenced_skills(bundle: DataBundle, categories: list[str], limit: int) -> list[str]:
    evidence = bundle.evidence_index()
    catalog = bundle.skills
    out: list[str] = []
    for cat in categories:
        names = [n for n in catalog.all_names()
                 if n in evidence and catalog.category_of(n) == cat]
        names.sort(key=lambda n: (-len(evidence.get(n, [])), n))
        for n in names:
            if n not in out:
                out.append(n)
            if len(out) >= limit:
                return out
    return out


_CF_PATTERNS = (
    ("client engagements", r"client engagements?"),
    ("technical enablement and training delivery", r"enablement and training"),
    ("stakeholder collaboration", r"stakeholders?\b"),
)


def _customer_facing_evidence(bundle: DataBundle) -> str:
    """Frases customer-facing RESPALDADAS por bullets canónicos, verbatim-based.
    Sin evidencia → cadena vacía (la dimensión no se narra). Nunca inventa
    presales/sales cycles: solo refleja lo que los bullets ya dicen."""
    corpus = " ".join(
        b.text for exp in bundle.experiences for b in exp.bullets).lower()
    found = [phrase for phrase, pat in _CF_PATTERNS if re.search(pat, corpus)]
    return ", ".join(found[:2])


def compose_summary(bundle: DataBundle, intent: RoleIntent, skill_priority: list[str],
                    headline: str) -> str:
    """Evidence-grounded professional summary (<=90 words). Identity is taken
    from the composed headline (so it stays consistent with the role applied
    for); seniority from real years; competencies from evidenced skills."""
    years = _career_years(bundle)
    fams = intent.top_families(2)
    # identity/focus derived from the headline: "Software Engineer — AI"
    parts_h = re.split(r"\s*[—|]\s*", headline, maxsplit=1)
    role_noun = parts_h[0].strip() or (FAMILY_LABEL.get(fams[0], "Engineer") if fams else "Engineer")
    focus = parts_h[1].strip() if len(parts_h) > 1 else ""
    # only use focus as a domain phrase (not another 'X Engineer' label)
    if focus and "engineer" not in focus.lower() and len(focus.split()) <= 4:
        identity = f"{role_noun} specializing in {focus}"
    else:
        identity = role_noun
    comps = _top_evidenced_skills(bundle, skill_priority[:4], 5)
    comp_str = ", ".join(comps[:5])
    yrs = f"{years}+ years" if years >= 3 else "hands-on"
    ai_relevant = intent.category_weights.get("ai", 0) >= 0.4 or "ai_systems" in fams \
        or "ai_platform" in fams or "ai_architect" in fams
    parts = [f"{identity} with {yrs} across cloud, distributed systems and "
             f"secure software delivery." if years >= 3
             else f"{identity} across distributed systems and secure delivery."]
    if comp_str:
        parts.append(f"Core strengths: {comp_str}.")
    # Dimensión customer-facing (sales support / presales): SOLO si la familia
    # pesa en el intent Y existe evidencia real en los bullets canónicos
    # (enablement/training/client engagements/arquitectura frente a clientes).
    if intent.role_weights.get("presales_sales_support", 0) >= 0.08:
        cf = _customer_facing_evidence(bundle)
        if cf:
            parts.append(f"Customer-facing track record: {cf}.")
    if ai_relevant:
        parts.append("Creator of NexusOS, a governed execution platform for "
                     "autonomous AI agents — capability-based authorization, "
                     "policy, verification and audit around LLM and multi-agent "
                     "runtimes.")
    else:
        parts.append("Creator of NexusOS, an independent architecture project "
                     "applying governance and reliability practices end to end.")
    summary = " ".join(parts)
    # trim to ~90 words
    words = summary.split()
    if len(words) > 92:
        summary = " ".join(words[:92]).rstrip(",;") + "."
    return summary


def compose_plan(
    bundle: DataBundle,
    intent: RoleIntent,
    *,
    page_budget_expanded: int = 6,
    max_bullets: int = 3,
) -> ComposedPlan:
    # skill priority: categories by intent weight, then remaining evidenced cats
    ranked_cats = [c for c, _ in sorted(intent.category_weights.items(),
                                        key=lambda kv: -kv[1])]
    evidence = bundle.evidence_index()
    evidenced_cats = {bundle.skills.category_of(n) for n in evidence}
    skill_priority = ranked_cats + [c for c in evidenced_cats
                                    if c and c not in ranked_cats]

    headline, tagline = compose_headline(intent)
    summary = compose_summary(bundle, intent, skill_priority, headline)

    emphasis: list[str] = []
    for f in intent.top_families(3):
        for e in FAMILY_EMPHASIS.get(f, []):
            if e not in emphasis:
                emphasis.append(e)

    # relevance-driven expansion: highest-relevance roles get full detail, the
    # rest fewer bullets, the least relevant collapse to one line — but every
    # role keeps company/title/dates (chronology never disappears).
    scored = rank_experiences(bundle, intent)  # already reverse-chronological input
    order = sorted(scored, key=lambda s: -s.score)
    expand: dict[str, int] = {}
    condense: set[str] = set()
    for rank, s in enumerate(order):
        eid = s.exp.id
        if rank < page_budget_expanded:
            expand[eid] = max_bullets if rank < page_budget_expanded - 2 else 2
        elif rank < page_budget_expanded + 3:
            expand[eid] = 1
        else:
            condense.add(eid)

    # Recency floor: the 2 most-recent roles always stay visible with >=1 bullet
    # (a recruiter expects the current role) regardless of relevance ranking.
    for exp in bundle.experiences_sorted()[:2]:
        condense.discard(exp.id)
        expand.setdefault(exp.id, 0)
        if expand[exp.id] < 1:
            expand[exp.id] = 1

    include = ["nexusos"] if intent.category_weights.get("ai", 0) >= 0.3 \
        or intent.category_weights.get("platform", 0) >= 0.6 else []

    return ComposedPlan(
        headline=headline, tagline=tagline, summary=summary,
        skill_priority=skill_priority, emphasis_tags=emphasis,
        expand=expand, condense=condense, include_projects=include,
        profile_name=f"role:{intent.primary_role}",
    )
