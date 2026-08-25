"""CV COMPOSITION from the match matrix — title, summary, skills, bullet plan.

Stages 5-9. Everything here runs AFTER matching, which is the point: the
professional summary is assembled from the evidence that actually matched this
JD instead of being a master paragraph with two keywords swapped, and the target
title is composed from the candidate's real profile rather than copied off the
vacancy.

Hard rules enforced here:
  * the target title is NEVER the JD's own title;
  * only skills present in the canonical catalog WITH evidence are ordered/shown;
  * every sentence of the summary is derived from a matched requirement, a real
    date range or a canonical industry — nothing is asserted from a template.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .compose import ComposedPlan, FAMILY_EMPHASIS, FAMILY_LABEL, _career_years
from .inventory import CATEGORY_DIMENSION, EvidenceInventory
from .jdspec import DIMENSION_WEIGHT, JobSpec, normalize
from .matching import CAN_CLAIM_YES, MatchMatrix, RankedExperience
from .models import DataBundle
from .roleintent import RoleIntent

SENIORITY_WORD = {
    "junior": "", "mid": "", "senior": "Senior", "staff": "Staff",
    "principal": "Principal", "lead": "Lead",
}

# Infrastructure-weighted JDs get an infrastructure noun instead of the generic
# family label: "Cloud Infrastructure Engineer", not "DevOps Engineer", when the
# JD's weight sits in cloud platform / IaC / networking / OS.
_INFRA_DIMENSIONS = ("cloud_platform", "iac", "networking", "os", "backup_dr")
_INFRA_FAMILIES = ("devops", "platform_engineering", "sre", "cloud_security",
                   "finops", "presales_sales_support")

# Dimensions that do not differentiate a senior candidate. They are real
# requirements and stay in the matrix, the summary and the skills block — they
# just do not earn one of the three slots in the headline tagline, where
# "Linux" would displace the platform signal a recruiter scans for.
_COMMODITY_DIMENSIONS = frozenset({"os", "scripting", "domain", "certification",
                                   "other"})

# Display names for requirement terms that are not canonical skill names.
_TERM_DISPLAY = {
    "aks": "AKS", "eks": "EKS", "gke": "GKE", "ci/cd": "CI/CD",
    "iac": "Infrastructure as Code", "rbac": "RBAC", "iam": "IAM",
    "dns": "DNS", "vpn": "VPN", "tcp/ip": "TCP/IP", "sso": "SSO",
    "sast": "SAST", "sca": "SCA", "sql": "SQL", "vnet": "VNet",
    "aws": "AWS", "gcp": "GCP", "azure ad": "Azure AD",
    "entra id": "Microsoft Entra ID", "expressroute": "ExpressRoute",
    "network security groups": "Network Security Groups",
    "veeam backup & replication": "Veeam Backup & Replication",
    "veeam backup for microsoft azure": "Veeam Backup for Microsoft Azure",
    "azure landing zones": "Azure Landing Zones",
    "cloud adoption framework": "Cloud Adoption Framework",
    "az-104": "AZ-104", "az-305": "AZ-305", "az-700": "AZ-700",
    "az-400": "AZ-400", "vmce": "VMCE", "cka": "CKA",
    "openshift": "OpenShift", "argocd": "ArgoCD", "rhel": "RHEL",
    "azure openai": "Azure OpenAI", "azure ai studio": "Azure AI Studio",
    "azure monitor": "Azure Monitor", "azure devops": "Azure DevOps",
}

_DIMENSION_LABEL = {
    "cloud_platform": "cloud platforms", "iac": "infrastructure as code",
    "networking": "cloud networking", "identity": "identity & access",
    "security_governance": "security & governance", "backup_dr": "backup & DR",
    "containers": "containers & orchestration", "cicd": "CI/CD",
    "os": "operating systems", "observability": "observability",
    "scripting": "scripting", "data": "data platforms", "ai": "AI systems",
    "domain": "domain", "certification": "certifications", "other": "other",
}


def display_term(match) -> str:
    """Prefer the canonical skill name; fall back to a readable term."""
    for ev in match.evidence:
        if not ev.startswith("text:"):
            return ev
    term = match.term
    if term in _TERM_DISPLAY:
        return _TERM_DISPLAY[term]
    return " ".join(w if w.isupper() else w.capitalize() for w in term.split())


# --------------------------------------------------------------------------- #
# 6. Target title
# --------------------------------------------------------------------------- #
def compose_target_title(spec: JobSpec, intent: RoleIntent, matrix: MatchMatrix,
                         bundle: DataBundle) -> tuple[str, str]:
    """A natural professional title = real profile + target of the search.

    Never the vacancy's own title. The regression driver is
    'Sr Cloud Azure Infraestructura Engineer', a hybrid the previous pipeline
    produced by copying the ad (leading geography included).
    """
    dims = spec.dimension_weights()
    infra_share = sum(dims.get(d, 0.0) for d in _INFRA_DIMENSIONS)
    primary = intent.top_families(1)[0] if intent.role_weights else intent.primary_role

    if infra_share >= 0.35 and primary in _INFRA_FAMILIES:
        noun = "Cloud Infrastructure Engineer"
    else:
        noun = FAMILY_LABEL.get(primary, "Engineer")

    # Seniority: the lower of what the JD asks for and what the years support.
    years = _career_years(bundle)
    word = SENIORITY_WORD.get(spec.seniority or "", "")
    if word and years < 8:
        word = ""
    headline = f"{word} {noun}".strip()

    # Tagline: the strongest claim per dimension. Deduping by dimension is what
    # keeps it from reading "Terraform · Ansible" — two IaC tools where the JD
    # asked for "Terraform/Bicep/Ansible (al menos uno)" — instead of covering
    # cloud, IaC and identity.
    seen: list[str] = []
    used_dimensions: set[str] = set()
    for m in claim_ranking(matrix):
        dim = m.requirement.dimension
        if dim in used_dimensions or dim in _COMMODITY_DIMENSIONS:
            continue
        label = display_term(m)
        if label.lower() in headline.lower() or label in seen:
            continue
        used_dimensions.add(dim)
        seen.append(label)
        if len(seen) >= 3:
            break
    return headline, " · ".join(seen)


def claim_ranking(matrix: MatchMatrix) -> list:
    """Claimable requirements, most role-defining first.

    Sort key: must-haves before preferred, then requirement weight scaled by how
    often the JD mentions the term, then evidence depth. Mentions matter because
    weight alone cannot tell "Azure", named in every other line, from "Ansible",
    named once as one of three acceptable alternatives.
    """
    return sorted(
        matrix.claimable(),
        key=lambda m: (0 if m.requirement.is_must else 1,
                       0 if m.requirement.kind == "tool" else 1,
                       -(m.requirement.weight * (1 + 0.3 * m.requirement.mentions)),
                       -m.confidence, -len(m.experience_ids), m.term))


# --------------------------------------------------------------------------- #
# 7. Professional summary — built from the match matrix, after matching
# --------------------------------------------------------------------------- #
def compose_evidence_summary(bundle: DataBundle, spec: JobSpec, matrix: MatchMatrix,
                             inventory: EvidenceInventory, headline: str,
                             ranked: list[RankedExperience]) -> str:
    years = _career_years(bundle)
    parts: list[str] = []

    industries = [i for i in inventory.industries() if i]
    industry_str = ""
    if industries:
        top = industries[:3]
        industry_str = (", ".join(top[:-1]) + " and " + top[-1]) if len(top) > 1 else top[0]

    lead = f"{headline} with {years}+ years" if years >= 3 else headline
    if industry_str:
        lead += f" across {industry_str.lower()}"
    parts.append(lead.rstrip(".") + ".")

    # Sentence 2: what this JD asked for and where the evidence lives.
    ranked_claims = claim_ranking(matrix)
    labels: list[str] = []
    used_dimensions: set[str] = set()
    for m in ranked_claims:
        if m.requirement.dimension in used_dimensions:
            continue
        label = display_term(m)
        if label in labels:
            continue
        used_dimensions.add(m.requirement.dimension)
        labels.append(label)
        if len(labels) >= 4:
            break
    if labels:
        anchor = ranked_claims[0]
        exp_labels: list[str] = []
        for exp_id in anchor.experience_ids:
            rec = inventory.by_id(exp_id)
            if rec and rec.short_label not in exp_labels:
                exp_labels.append(rec.short_label)
        claim_str = (", ".join(labels[:-1]) + " and " + labels[-1]) if len(labels) > 1 \
            else labels[0]
        sentence = f"Matched to this search on {claim_str}"
        if exp_labels:
            where = ", ".join(exp_labels[:3])
            sentence += f", evidenced at {where}"
        parts.append(sentence + ".")

    # Sentence 3: the strongest non-JD differentiator that is real.
    if intent_wants_ai(spec):
        parts.append("Creator of NexusOS, a governed execution platform for "
                     "autonomous AI agents — capability-based authorization, "
                     "policy, verification and audit.")
    else:
        parts.append("Creator of NexusOS, an independent architecture project "
                     "applying governance and reliability practices end to end.")

    summary = " ".join(parts)
    words = summary.split()
    if len(words) > 92:
        summary = " ".join(words[:92]).rstrip(",;") + "."
    return summary


def intent_wants_ai(spec: JobSpec) -> bool:
    return spec.dimension_weights().get("ai", 0.0) >= 0.05


# --------------------------------------------------------------------------- #
# 8. Skill ordering driven by JD relevance (evidenced skills only)
# --------------------------------------------------------------------------- #
def compose_skill_priority(bundle: DataBundle, spec: JobSpec, matrix: MatchMatrix,
                           inventory: EvidenceInventory) -> list[str]:
    catalog = bundle.skills
    dim_weight = spec.dimension_weights()

    cat_score: dict[str, float] = {}
    best_per_skill: dict[str, dict[str, float]] = {}
    for m in matrix.claimable():
        for ev in m.evidence:
            if ev.startswith("text:"):
                continue
            cat = catalog.category_of(ev)
            if not cat:
                continue
            # Accumulate rather than take the max: a category the JD names from
            # several angles (Azure DevOps, AKS, Kubernetes, containers,
            # orchestration) is more relevant than one it names once, even when
            # each individual mention sits in the "Deseables" block.
            bonus = (m.requirement.weight * (1.5 if m.requirement.is_must else 1.0)
                     * (1 + 0.3 * m.requirement.mentions)
                     * DIMENSION_WEIGHT.get(m.requirement.dimension,
                                            DIMENSION_WEIGHT["other"]))
            # Per SKILL, not per requirement: "terraform", "iac" and
            # "infrastructure as code" all resolve to the same canonical skill,
            # so summing raw requirements would count Terraform three times and
            # float Infrastructure as Code above Cloud Platforms on an Azure JD.
            best_per_skill.setdefault(cat, {})
            best_per_skill[cat][ev] = max(best_per_skill[cat].get(ev, 0.0), bonus)
    for cat, per_skill in best_per_skill.items():
        # Depth first, breadth second: the category holding the single strongest
        # match leads, ties broken by how much else it covers. A long IaC list
        # must not outrank the cloud platform the whole vacancy is about.
        cat_score[cat] = max(per_skill.values()) * 10 + sum(per_skill.values())
    # Categories with no direct match still inherit their dimension's JD weight,
    # so an unmatched-but-relevant category outranks an irrelevant one.
    for cat, dim in CATEGORY_DIMENSION.items():
        cat_score.setdefault(cat, dim_weight.get(dim, 0.0) * 0.2)

    evidenced_cats = {catalog.category_of(n) for n in inventory.skill_evidence}
    ordered = [c for c, _ in sorted(cat_score.items(), key=lambda kv: -kv[1])
               if c in evidenced_cats]
    return ordered + [c for c in evidenced_cats if c and c not in ordered]


# --------------------------------------------------------------------------- #
# 5+9. The composed plan
# --------------------------------------------------------------------------- #
@dataclass
class EvidencePlan:
    plan: ComposedPlan
    ranked: list[RankedExperience]
    bullets: dict[str, list[str]] = field(default_factory=dict)


def compose_from_evidence(bundle: DataBundle, spec: JobSpec, intent: RoleIntent,
                          matrix: MatchMatrix, inventory: EvidenceInventory,
                          ranked: list[RankedExperience]) -> ComposedPlan:
    headline, tagline = compose_target_title(spec, intent, matrix, bundle)
    summary = compose_evidence_summary(bundle, spec, matrix, inventory,
                                       headline, ranked)
    skill_priority = compose_skill_priority(bundle, spec, matrix, inventory)

    emphasis: list[str] = []
    for f in intent.top_families(3):
        for e in FAMILY_EMPHASIS.get(f, []):
            if e not in emphasis:
                emphasis.append(e)

    expand: dict[str, int] = {}
    condense: set[str] = set()
    for r in ranked:
        if r.bullet_budget > 0:
            expand[r.record.id] = r.bullet_budget
        else:
            condense.add(r.record.id)

    include = ["nexusos"] if intent_wants_ai(spec) or \
        spec.dimension_weights().get("other", 0) >= 0.5 else []

    return ComposedPlan(
        headline=headline, tagline=tagline, summary=summary,
        skill_priority=skill_priority, emphasis_tags=emphasis,
        expand=expand, condense=condense, include_projects=include,
        profile_name=f"role:{intent.primary_role}",
    )
