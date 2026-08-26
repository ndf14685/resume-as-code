"""CANDIDATE HEADLINE — the candidate's own professional title, never the JD's.

    target_role       what the vacancy is called          (belongs to the JD)
    candidate_headline what the candidate IS              (belongs to the dataset)

These are two different entities and this module exists because the pipeline
used to conflate them. The evidence:

  * "DevSecOps Engineer / Security Solutions Lead" (Azumo) produced the headline
    "Lead AI Systems" — `Lead` lifted from the vacancy's seniority token, and
    `AI Systems` a role-family LABEL, not a job title the candidate holds.
  * A later run on the same family produced "Lead DevSecOps Engineer" — the
    discipline was right, but `Lead` still came from the ad.

The JD may steer SPECIALIZATION. It may never grant SENIORITY or a TITLE.
Seniority comes from the candidate's own years and canonical titles, or it does
not appear at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Nouns the candidate may be called. Closed set, every entry backed by a
# canonical job title in data/experience.yaml. A JD family that maps to nothing
# here falls back to the baseline — an AI-security vacancy does not turn a
# DevSecOps engineer into an "AI Systems" anything.
CANDIDATE_NOUNS: dict[str, str] = {
    "devsecops": "DevSecOps Engineer",
    "application_security": "DevSecOps Engineer",
    "cloud_security": "Cloud & DevSecOps Engineer",
    "devops": "DevOps / DevSecOps Engineer",
    "platform_engineering": "Platform Engineer",
    "sre": "Site Reliability Engineer",
    "cloud_infrastructure": "Cloud Infrastructure Engineer",
    "data_platforms": "Data Engineer / DevOps",
}
BASELINE_NOUN = "DevOps / DevSecOps Engineer"

# Seniority tokens a JD is NOT allowed to confer. Every one of these appeared in,
# or could be derived from, a vacancy title; none is claimable without the
# candidate's own record showing it in the same discipline.
GRANTABLE_BY_YEARS = {"senior"}
TITLE_ONLY_SENIORITY = ("lead", "principal", "staff", "head", "manager",
                        "director", "chief", "vp", "architect")
SENIOR_YEARS_THRESHOLD = 8
DECLARED_YEARS_THRESHOLD = 5

# Specialization phrases, and the canonical skills that entitle us to say them.
# A phrase is only appended when its evidence exists — the specialization is
# still a claim.
SPECIALIZATIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("cloud_security", "Cloud Security", ("Cloud Security",)),
    ("application_security", "Application Security", ("SAST", "SCA", "Secure SDLC")),
    ("ai_security", "AI Systems Security",
     ("Prompt Injection Mitigation", "AI Governance", "Policy Enforcement")),
    ("containers", "Kubernetes", ("Kubernetes", "Azure AKS", "Amazon EKS")),
    ("iac", "Terraform", ("Terraform",)),
    ("cloud_platform", "Azure", ("Azure",)),
    ("cicd", "CI/CD", ("Jenkins", "GitLab CI", "Azure DevOps")),
    ("observability", "Observability", ("Observability",)),
    ("incident_response", "Incident Response", ("Incident Response",)),
]


@dataclass
class CandidateHeadline:
    headline: str
    reason: str
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    seniority: Optional[str] = None
    seniority_source: str = "none"
    specialization: str = ""
    noun: str = BASELINE_NOUN
    rejected: list[str] = field(default_factory=list)

    def audit(self) -> dict:
        return {
            "candidateHeadline": self.headline,
            "noun": self.noun,
            "specialization": self.specialization,
            "seniority": self.seniority,
            "seniuoritySource": self.seniority_source,
            "headlineSource": self.reason,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "rejectedFromJD": self.rejected,
        }


def _discipline_years(inventory) -> tuple[int, list[str]]:
    """Years in the DevOps/DevSecOps/SRE/Cloud discipline, and the roles proving it.

    Deliberately not total career years: the candidate's first decade was
    Java/SOA. "13+ years" is true of a technology career and false of DevSecOps,
    and a headline that implies the latter would be a claim we cannot support.
    """
    pattern = re.compile(r"devops|devsecops|\bsre\b|site reliability|cloud|platform",
                         re.I)
    months = 0
    roles: list[str] = []
    for rec in inventory.records:
        if not pattern.search(rec.title):
            continue
        roles.append(f"{rec.title} @ {rec.company} ({rec.start}..{rec.end})")
        months += _span_months(rec.start, rec.end)
    return months // 12, roles


def _span_months(start: str, end: str) -> int:
    def parse(v: str, default_month: int) -> tuple[int, int]:
        if (v or "").lower() in ("present", "current", ""):
            return (2026, 8)
        parts = v.split("-")
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else default_month)
    sy, sm = parse(start, 1)
    ey, em = parse(end, 12)
    return max(0, (ey - sy) * 12 + (em - sm))


def _canonical_title_seniority(inventory) -> dict[str, str]:
    """Seniority tokens that appear in the candidate's OWN job titles, mapped to
    the title that proves them. `Technical Lead / Configuration Manager / SOA`
    proves `lead` — in SOA integration, which is why the caller still has to
    check the discipline matches before using it."""
    found: dict[str, str] = {}
    for rec in inventory.records:
        low = rec.title.lower()
        for token in TITLE_ONLY_SENIORITY:
            if re.search(rf"(?<![\w]){token}(?![\w])", low) and token not in found:
                found[token] = f"{rec.title} @ {rec.company}"
    return found


def _noun_for(primary_family: str, secondary: list[str]) -> str:
    if primary_family in CANDIDATE_NOUNS:
        return CANDIDATE_NOUNS[primary_family]
    for dom in secondary:
        if dom in CANDIDATE_NOUNS:
            return CANDIDATE_NOUNS[dom]
    return BASELINE_NOUN


def _declared_seniority(bundle) -> Optional[str]:
    """Seniority the candidate already claims publicly, from data/profile.yaml.

    This is canonical evidence like any other field: it is the headline the
    candidate uses on their own profile, not something the vacancy supplied.
    """
    declared = (getattr(getattr(bundle, "basics", None), "headline", "") or "").lower()
    for token in ("principal", "staff", "lead", "senior"):
        if re.search(rf"(?<![\w]){token}(?![\w])", declared):
            return token
    return None


def resolve_candidate_headline(*, spec, inventory, matrix, bundle=None,
                               max_specializations: int = 2) -> CandidateHeadline:
    """The single place a CV headline is decided.

    Inputs are the candidate's canonical profile and evidence plus the JD's
    CLASSIFICATION — never the JD's title string.
    """
    rejected: list[str] = []
    primary = getattr(spec, "primary_family", "") or ""
    secondary = list(getattr(spec, "secondary_domains", []) or [])

    # 1) Noun — what the candidate is called, from the closed evidenced set.
    noun = _noun_for(primary, secondary)

    # 2) Seniority — from the candidate, never from the ad.
    years, roles = _discipline_years(inventory)
    title_seniority = _canonical_title_seniority(inventory)
    seniority: Optional[str] = None
    seniority_source = "none"
    evidence: list[str] = []

    jd_asked = (getattr(spec, "seniority", None) or "").lower()
    if jd_asked and jd_asked not in GRANTABLE_BY_YEARS:
        # The vacancy asked for lead/principal/head/... We record the refusal
        # rather than silently dropping it: this is the P0 rule firing.
        proof = title_seniority.get(jd_asked)
        rejected.append(
            f"JD seniority '{jd_asked}' not claimed"
            + (f" (candidate title '{proof}' proves it only in another discipline)"
               if proof else " (no canonical title proves it)"))

    declared = _declared_seniority(bundle) if bundle is not None else None
    if years >= SENIOR_YEARS_THRESHOLD:
        seniority = "Senior"
        seniority_source = f"candidate evidence: {years}+ years in the discipline"
        evidence.extend(roles[:4])
    elif declared in GRANTABLE_BY_YEARS and years >= DECLARED_YEARS_THRESHOLD:
        # Self-declared on the candidate's own canonical profile AND backed by a
        # substantial run in the discipline. Only `senior` is reachable this
        # way: `lead`/`principal` stay title-only, whatever a profile says.
        seniority = "Senior"
        seniority_source = (f"declared in data/profile.yaml, corroborated by "
                            f"{years} years in the discipline")
        evidence.append("data/profile.yaml headline declares Senior")
        evidence.extend(roles[:3])

    # 3) Specialization — steered by the JD, still gated on real evidence.
    claimable = matrix.claimable_skills() if matrix is not None else set()
    wanted = [primary, *secondary]
    # Rank the candidate specializations by how central each is to THIS
    # vacancy, not by the order they happen to be declared in.
    ranked_specs = sorted(
        SPECIALIZATIONS,
        key=lambda s: (-spec.domain_rank(s[0]),
                       wanted.index(s[0]) if s[0] in wanted else 99))
    phrases: list[str] = []
    for key, phrase, needs in ranked_specs:
        if (key not in wanted and spec.domain_rank(key) < 0.7) or phrase in phrases:
            continue
        proof = [s for s in needs if s in claimable]
        if not proof:
            continue
        phrases.append(phrase)
        evidence.append(f"{phrase} ← {', '.join(proof)}")
        if len(phrases) >= max_specializations:
            break
    specialization = " & ".join(phrases)

    headline = " ".join(p for p in (seniority, noun) if p).strip()
    if specialization:
        headline = f"{headline} | {specialization}"

    reason = (f"noun from candidate discipline (JD family '{primary or 'unknown'}')"
              f"; seniority {seniority_source}"
              + (f"; specialization steered by JD, evidenced" if specialization else ""))
    confidence = 0.9 if (seniority and specialization) else 0.75 if seniority else 0.6

    return CandidateHeadline(
        headline=headline, reason=reason, evidence=evidence, confidence=confidence,
        seniority=seniority, seniority_source=seniority_source,
        specialization=specialization, noun=noun, rejected=rejected)


# --------------------------------------------------------------------------- #
# Forbidden claims — enforced on the FINAL artifact, not the internal model
# --------------------------------------------------------------------------- #
# A seniority or credential that reaches the rendered document without canonical
# backing is a lie regardless of which layer introduced it. The renderer is as
# capable of introducing one as the composer.
FORBIDDEN_SENIORITY = ("lead", "principal", "staff", "head of", "manager",
                       "director", "chief", "cto", "ciso", "vp of")
FORBIDDEN_CREDENTIALS = ("cissp", "cism", "ccsp", "oscp", "ceh", "gsec", "gcih",
                         "az-104", "az-305", "az-700", "vmce", "cka", "ckad")
FORBIDDEN_EXPERIENCE_CLAIMS = ("soc 2", "soc2", "pci dss", "pci-dss",
                               "penetration testing", "pentesting",
                               "offensive security", "red team engagement",
                               "veeam", "entra id", "landing zones",
                               "expressroute")


def forbidden_claim_terms(inventory, matrix=None) -> list[str]:
    """Credential and experience terms that must not appear in the rendered CV.

    Anything the candidate can actually evidence is removed, so this never
    blocks a truthful claim. Seniority is deliberately NOT handled here: see
    `forbidden_headline_seniority`.
    """
    claimable = {s.lower() for s in (matrix.claimable_skills() if matrix else set())}
    corpus = " ".join(r.corpus for r in inventory.records).lower()
    terms: list[str] = []
    for term in (*FORBIDDEN_CREDENTIALS, *FORBIDDEN_EXPERIENCE_CLAIMS):
        if term in claimable or term in corpus:
            continue
        terms.append(term)
    return terms


def forbidden_headline_seniority(granted: Optional[str] = None) -> list[str]:
    """Seniority tokens that may not appear in the candidate's headline.

    Corpus filtering is wrong for seniority and dangerously so: the candidate's
    canonical history contains "Technical Lead / Configuration Manager / SOA",
    so a corpus check finds the word `lead` and licenses "Lead DevSecOps
    Engineer" — a title in a discipline where nothing proves it. Seniority is
    positional: what matters is what is being claimed, not whether the word
    exists somewhere in a twelve-year history.

    Only the seniority the resolver actually granted is permitted.
    """
    allowed = (granted or "").lower()
    return [term for term in FORBIDDEN_SENIORITY if term != allowed]
