"""JD ↔ EVIDENCE MATCHING, RELEVANCY RANKING and BULLET BUDGET.

Stages 3 and 4 of the pipeline.

For every requirement in the JobSpec this produces the record the operator asked
for — REQUIREMENT / EVIDENCE FOUND / SOURCE / CONFIDENCE / EXPERIENCES / CAN
CLAIM — and it is the ONLY place allowed to answer "can we claim this?".

  YES      the term resolves to a canonical skill that has real evidence, or the
           term appears verbatim in canonical experience text.
  PARTIAL  no direct evidence, but a curated, auditable adjacency table names
           real evidence of the same purpose. PARTIAL IS NEVER RENDERED — it is
           reported so a human can decide, nothing more.
  NO       no evidence. It is reported as a gap and it is a hard error for it to
           appear anywhere in the generated CV.

Ranking then scores each experience by the JD weight it actually satisfies, so
space follows relevance instead of recency: for an Azure JD the Azure evidence
outranks a more recent AWS-only engagement. Chronology is never reordered — only
depth is redistributed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .inventory import EvidenceInventory, EvidenceRecord
from .jdspec import JobSpec, Requirement, normalize, term_present
from .models import DataBundle

# --------------------------------------------------------------------------- #
# Curated adjacency. A JD tool with NO direct evidence may only be reported as
# PARTIAL when this table names real, evidenced skills that serve the same
# purpose. Absence from this table means the honest answer is NO — which is why
# Veeam, Azure Landing Zones, Entra ID and ExpressRoute resolve to NO even
# though the candidate has Azure and backup-adjacent operational history.
# --------------------------------------------------------------------------- #
ADJACENT_EVIDENCE: dict[str, list[str]] = {
    "bicep": ["Terraform"],
    "arm templates": ["Terraform"],
    "pulumi": ["Terraform", "AWS CDK"],
    "cloudformation": ["Terraform", "AWS CDK"],
    "gke": ["Amazon EKS", "Azure AKS", "Kubernetes"],
    "helm": ["Kubernetes"],
    "argocd": ["Jenkins", "GitLab CI", "Azure DevOps"],
    "github actions": ["GitLab CI", "Jenkins", "Azure DevOps"],
    "prometheus": ["Observability", "Nagios"],
    "grafana": ["Observability", "Nagios"],
    "datadog": ["Observability", "Nagios"],
    "splunk": ["Observability", "Elastic Cloud"],
    "powershell": ["Bash", "Python"],
    "rhel": ["Linux"],
    "ubuntu": ["Linux"],
    "load balancer": ["Kubernetes", "AWS"],
    "kafka": ["Google Pub/Sub"],
    "snowflake": ["Google Dataflow", "Apache Airflow"],
    "databricks": ["Google Dataflow", "Apache Airflow"],
}

# Named frameworks, standards, regulators and certifications. Generic evidence
# never satisfies one of these, however semantically close it looks. Running an
# AI governance pipeline is not experience with NIST AI RMF; working at banks is
# not interaction with the BCRA; enforcing policy is not ISO 42001. Resolution
# skips straight to the gap classifier for these terms.
SPECIFIC_FRAMEWORKS = frozenset({
    "nist ai rmf", "iso 42001", "iso/iec 42001", "eu ai act",
    "owasp llm top 10", "nist csf", "iso 27001", "soc 2", "pci dss",
    "hipaa", "gdpr", "nist", "bcra", "bcu", "sbs", "cmf",
    "cism", "cissp", "crisc", "ccsp", "oscp", "ceh",
    "az-104", "az-305", "az-700", "az-400", "vmce", "cka", "ckad",
    "aws security", "aws certified", "certifications", "certificaciones",
})

CAN_CLAIM_YES = "YES"
CAN_CLAIM_PARTIAL = "PARTIAL"
CAN_CLAIM_NO = "NO"
# Absence of evidence is not evidence of absence. UNKNOWN means the knowledge
# base has no coverage of that dimension at all, so "the candidate has never
# done this" would be as unfounded a claim as the positive one. NO means we do
# have evidence in that dimension and this specific tool is not part of it.
# Both are gaps and neither is ever claimable; they differ only in what we are
# entitled to say about them.
CAN_CLAIM_UNKNOWN = "UNKNOWN"

# Dimensions whose source data is declared incomplete are UNKNOWN by
# construction, whatever else the matrix finds. Certifications are the case the
# operator called out: data/certifications.yaml holds only needs_confirmation
# entries, so a JD asking for AZ-104 cannot be answered either way.
_INCOMPLETE_SOURCE_DIMENSIONS = frozenset({"certification"})


@dataclass
class RequirementMatch:
    requirement: Requirement
    can_claim: str
    confidence: float
    evidence: list[str] = field(default_factory=list)        # canonical skills / phrases
    experience_ids: list[str] = field(default_factory=list)
    source: str = ""                                          # how it was resolved
    note: str = ""
    # Skills that merely co-occur in the matching experiences. They explain the
    # match; they are NOT what the requirement resolves to, so they must never
    # be used as the requirement's display label.
    context_skills: list[str] = field(default_factory=list)

    @property
    def term(self) -> str:
        return self.requirement.term

    @property
    def is_gap(self) -> bool:
        """A gap is anything we cannot claim. UNSUPPORTED and UNKNOWN are both
        gaps for reporting and for the render ban; they differ in the reason."""
        return self.can_claim in (CAN_CLAIM_NO, CAN_CLAIM_UNKNOWN)


@dataclass
class MatchMatrix:
    matches: list[RequirementMatch] = field(default_factory=list)

    def by_term(self, term: str) -> Optional[RequirementMatch]:
        return next((m for m in self.matches if m.term == term), None)

    def claimable(self) -> list[RequirementMatch]:
        return [m for m in self.matches if m.can_claim == CAN_CLAIM_YES]

    def gaps(self, *, musts_only: bool = False) -> list[RequirementMatch]:
        out = [m for m in self.matches if m.is_gap]
        return [m for m in out if m.requirement.is_must] if musts_only else out

    def partials(self) -> list[RequirementMatch]:
        return [m for m in self.matches if m.can_claim == CAN_CLAIM_PARTIAL]

    def unsupported(self) -> list[RequirementMatch]:
        """Looked for, not found, and the dimension IS covered by real data."""
        return [m for m in self.matches if m.can_claim == CAN_CLAIM_NO]

    def unknowns(self) -> list[RequirementMatch]:
        """No basis to answer either way — the sources do not cover this."""
        return [m for m in self.matches if m.can_claim == CAN_CLAIM_UNKNOWN]

    def coverage(self, *, musts_only: bool = True) -> float:
        """Weighted requirement coverage, normalised PER DIMENSION.

        Two properties matter here. First, requirements with NO evidence stay in
        the denominator — a JD we half-match must score as a half-match, not as
        100% because the misses were dropped (the old gate's core defect).

        Second, the average is taken over dimensions rather than over raw terms.
        A JD that enumerates eleven networking nouns (VNet, NSG, subnets,
        DNS, VPN, ExpressRoute, Private Endpoints, firewalls, routing, TCP/IP,
        virtual networks) and names Azure once would otherwise let networking
        outvote the cloud platform eleven to one purely by word count.
        """
        from .jdspec import DIMENSION_WEIGHT
        scope = [m for m in self.matches
                 if (m.requirement.is_must or not musts_only)]
        if not scope:
            return 1.0
        per_dim: dict[str, list[float]] = {}
        for m in scope:
            credit = requirement_credit(m)
            per_dim.setdefault(m.requirement.dimension, []).append(credit)
        num = den = 0.0
        for dim, credits in per_dim.items():
            importance = DIMENSION_WEIGHT.get(dim, DIMENSION_WEIGHT["other"])
            num += importance * (sum(credits) / len(credits))
            den += importance
        return round(num / den, 4) if den else 1.0

    def supporting_ids(self, term: str) -> list[str]:
        m = self.by_term(term)
        return list(m.experience_ids) if m else []

    def claimable_skills(self) -> set[str]:
        out: set[str] = set()
        for m in self.claimable():
            out.update(e for e in m.evidence if not e.startswith("text:"))
        return out


def build_match_matrix(spec: JobSpec, bundle: DataBundle,
                       inventory: EvidenceInventory) -> MatchMatrix:
    alias_index = {k: v for k, v in bundle.skills.alias_index().items()}
    evidenced = set(inventory.skill_evidence)
    matrix = MatchMatrix()

    for req in spec.requirements:
        term_norm = normalize(req.term)

        # 0) A named framework/standard/certification is claimable only from an
        #    explicit record of it. No alias, no text match, no adjacency.
        if term_norm in SPECIFIC_FRAMEWORKS:
            credential = any(
                term_present(req.term, c) for c in inventory.credential_terms)
            if credential:
                matrix.matches.append(RequirementMatch(
                    requirement=req, can_claim=CAN_CLAIM_YES, confidence=1.0,
                    evidence=[f"credential:{req.term}"], experience_ids=[],
                    source="confirmed credential record",
                    note="named framework backed by a confirmed credential"))
            else:
                matrix.matches.append(RequirementMatch(
                    requirement=req, can_claim=CAN_CLAIM_NO, confidence=0.0,
                    evidence=[], experience_ids=[], source="named framework",
                    note="named standard/certification: only an explicit record "
                         "counts; adjacent governance work does not"))
            continue

        # 1) Direct canonical-skill evidence (strongest).
        canonical = alias_index.get(term_norm)
        if canonical and canonical in evidenced:
            ids = [i for i in inventory.ids_with_skill(canonical)
                   if not i.startswith("project:")]
            matrix.matches.append(RequirementMatch(
                requirement=req, can_claim=CAN_CLAIM_YES, confidence=1.0,
                evidence=[canonical], experience_ids=ids,
                source="canonical skill catalog",
                note=f"{canonical} evidenced in {len(ids)} experience(s)"))
            continue

        # 2) Verbatim evidence inside canonical experience text, guarded by
        #    dimension so a skill name cannot answer for an unrelated meaning
        #    of the same word.
        text_ids = inventory.ids_mentioning(req.term, req.dimension)
        if text_ids:
            skills = sorted({s for i in text_ids
                             for s in (inventory.by_id(i).skills if inventory.by_id(i) else [])})
            matrix.matches.append(RequirementMatch(
                requirement=req, can_claim=CAN_CLAIM_YES, confidence=0.8,
                evidence=[f"text:{req.term}"], context_skills=skills[:4],
                experience_ids=text_ids,
                source="canonical experience text",
                note="term appears verbatim in canonical bullets/role data"))
            continue

        # 2b) Evidence that only lives in featured projects or credentials.
        if inventory.project_mentions(req.term, req.dimension):
            matrix.matches.append(RequirementMatch(
                requirement=req, can_claim=CAN_CLAIM_YES, confidence=0.7,
                evidence=[f"text:{req.term}"], experience_ids=[],
                source="featured project",
                note="evidenced by a canonical featured project, not employment"))
            continue

        # 3) Curated adjacency → PARTIAL. Reported, never rendered.
        adjacent = [s for s in ADJACENT_EVIDENCE.get(term_norm, []) if s in evidenced]
        if adjacent:
            ids = sorted({i for s in adjacent for i in inventory.ids_with_skill(s)
                          if not i.startswith("project:")})
            matrix.matches.append(RequirementMatch(
                requirement=req, can_claim=CAN_CLAIM_PARTIAL, confidence=0.35,
                evidence=adjacent, experience_ids=ids,
                source="curated adjacency table",
                note="related evidence of the same purpose; the tool itself is "
                     "NOT claimed"))
            continue

        # 4) No evidence found. Whether that means UNSUPPORTED or UNKNOWN
        #    depends on whether the knowledge base covers this dimension at
        #    all, which is only knowable once every requirement is resolved.
        matrix.matches.append(RequirementMatch(
            requirement=req, can_claim=CAN_CLAIM_NO, confidence=0.0,
            evidence=[], experience_ids=[], source="none",
            note="no evidence in the candidate knowledge base"))

    _reclassify_unknowns(matrix, inventory)
    return matrix


def _reclassify_unknowns(matrix: MatchMatrix, inventory: EvidenceInventory) -> None:
    """Split "no evidence" into UNSUPPORTED and UNKNOWN.

    A dimension counts as COVERED when the knowledge base can speak to it —
    either some requirement in it resolved to real evidence, or the candidate
    has catalogued skills filed under it. In a covered dimension, a missing tool
    is a genuine negative: we know his operating systems are Linux, so Windows
    Server is UNSUPPORTED. In an uncovered dimension nothing was ever recorded,
    so Veeam is UNKNOWN — the dataset models no backup/DR facet whatsoever, and
    asserting he has never used it would be inventing a negative fact.
    """
    covered: set[str] = set()
    for m in matrix.matches:
        if m.can_claim in (CAN_CLAIM_YES, CAN_CLAIM_PARTIAL):
            covered.add(m.requirement.dimension)
    for rec in inventory.records:
        covered.update(d for d, skills in rec.facets.items() if skills)

    for m in matrix.matches:
        if m.can_claim != CAN_CLAIM_NO:
            continue
        dimension = m.requirement.dimension
        if dimension in _INCOMPLETE_SOURCE_DIMENSIONS:
            m.can_claim = CAN_CLAIM_UNKNOWN
            m.source = "source declared incomplete"
            m.note = (f"the {dimension} sources are incomplete, so neither a "
                      "positive nor a negative claim is warranted")
        elif dimension not in covered:
            m.can_claim = CAN_CLAIM_UNKNOWN
            m.source = "dimension not covered by any source"
            m.note = (f"no {dimension} evidence exists in the knowledge base at "
                      "all; absence here is missing information, not a negative")
        else:
            m.note = (f"the {dimension} dimension IS evidenced and this "
                      "specific requirement is not part of it")


# --------------------------------------------------------------------------- #
# Relevancy ranking
# --------------------------------------------------------------------------- #
def _recency_weight(rec: EvidenceRecord) -> float:
    import re
    end = (rec.end or "").lower()
    if end in ("present", "current", ""):
        return 1.0
    m = re.search(r"(19|20)\d{2}", end)
    if not m:
        return 0.5
    year = int(m.group(0))
    return max(0.4, min(1.0, 0.4 + (year - 2013) * (0.6 / 11)))


@dataclass
class RankedExperience:
    record: EvidenceRecord
    score: float
    jd_score: float
    matched_terms: list[str] = field(default_factory=list)
    must_terms: list[str] = field(default_factory=list)
    band: str = "low"                  # high | medium | low | none
    bullet_budget: int = 0
    reasons: list[str] = field(default_factory=list)


def rank_experiences(spec: JobSpec, matrix: MatchMatrix,
                     inventory: EvidenceInventory) -> list[RankedExperience]:
    """Score every experience by the JD weight it actually satisfies.

    Recency is a small tiebreaker (0.30 max), not the driver — that is the whole
    point of the fix: 'most recent = most bullets' is what buried the Azure
    evidence behind AWS-only roles for an Azure JD.
    """
    ranked: list[RankedExperience] = []
    dim_share = spec.dimension_weights()
    for rec in inventory.records:
        contributions: list[float] = []
        matched: list[str] = []
        musts: list[str] = []
        for m in matrix.matches:
            if m.can_claim != CAN_CLAIM_YES or rec.id not in m.experience_ids:
                continue
            # Emphasis: how central is this requirement to THIS vacancy. Term
            # frequency in the JD is the honest proxy — an Azure infrastructure
            # role says "Azure" in a dozen lines and "Bicep" in one.
            emphasis = (1.0 + 0.6 * min(m.requirement.mentions, 6)
                        + 1.5 * dim_share.get(m.requirement.dimension, 0.0))
            contributions.append(m.requirement.weight * m.confidence * emphasis)
            matched.append(m.term)
            if m.requirement.is_must:
                musts.append(m.term)
        # Depth beats breadth: the contributions are summed with a rank decay so
        # an experience is worth what its BEST matches are worth, not what its
        # longest skill list is. Plain summation let a broad engagement that
        # ticks linux/bash/python/ansible outscore the Azure engagement on an
        # Azure vacancy — exactly the failure this rewrite exists to fix.
        jd_score = sum(c / (1.0 + 0.45 * i)
                       for i, c in enumerate(sorted(contributions, reverse=True)))
        recency = _recency_weight(rec)
        depth = min(1.0, (len(rec.bullets) + len(rec.skills)) / 10.0)
        score = jd_score + 0.30 * recency + 0.20 * depth
        reasons = []
        if musts:
            reasons.append("must-haves: " + ", ".join(sorted(musts)[:6]))
        if matched and not musts:
            reasons.append("preferred/secondary: " + ", ".join(sorted(matched)[:6]))
        if not matched:
            reasons.append("no JD requirement matched; kept for chronology only")
        ranked.append(RankedExperience(
            record=rec, score=round(score, 4), jd_score=round(jd_score, 4),
            matched_terms=sorted(set(matched)), must_terms=sorted(set(musts)),
            reasons=reasons))
    return ranked


def assign_bullet_budget(ranked: list[RankedExperience], matrix: MatchMatrix,
                         *, total_bullets: int = 21, high: int = 4,
                         medium: int = 2, low: int = 1,
                         forced: Optional[set[str]] = None) -> list[RankedExperience]:
    """Bullets follow relevance, with two floors that protect honesty:

      * RECENCY FLOOR — the two most recent roles always keep >= 1 bullet, so a
        recruiter still sees the current job.
      * MUST-HAVE FLOOR — any experience that is supporting evidence for a
        must-have requirement keeps >= 1 bullet and is never condensed. This is
        what stops the CV from claiming Azure while hiding two of the three
        engagements where Azure actually happened.

    The global cap is trimmed from the least relevant end first, so low-value
    roles collapse before must-have evidence is touched.
    """
    if not ranked:
        return ranked
    by_score = sorted(ranked, key=lambda r: -r.jd_score)
    top = by_score[0].jd_score or 1.0

    must_support: set[str] = set()
    for m in matrix.matches:
        if m.requirement.is_must and m.can_claim == CAN_CLAIM_YES:
            must_support.update(m.experience_ids)

    for r in ranked:
        ratio = (r.jd_score / top) if top else 0.0
        if ratio >= 0.55:
            r.band, r.bullet_budget = "high", high
        elif ratio >= 0.22:
            r.band, r.bullet_budget = "medium", medium
        elif r.jd_score > 0:
            r.band, r.bullet_budget = "low", low
        else:
            r.band, r.bullet_budget = "none", 0
        r.bullet_budget = min(r.bullet_budget, len(r.record.bullets))

    # Floors.
    chronological = sorted(ranked, key=lambda r: (r.record.end == "present",
                                                  r.record.end), reverse=True)
    recent_ids: set[str] = set()
    for r in chronological[:2]:
        if not r.record.bullets:
            continue
        recent_ids.add(r.record.id)
        if r.bullet_budget < 1:
            r.bullet_budget = 1
            r.band = r.band if r.band != "none" else "low"
            r.reasons.append("recency floor: current/most-recent role kept visible")
    for r in ranked:
        if r.record.id in must_support and r.record.bullets and r.bullet_budget < 1:
            r.bullet_budget = 1
            r.band = r.band if r.band != "none" else "low"
            r.reasons.append("must-have floor: supporting evidence for a JD must-have")

    # Recomposition floor: experiences the factual validator found missing after
    # a previous pass are pinned so the next render cannot drop them again.
    for r in ranked:
        if forced and r.record.id in forced and r.record.bullets and r.bullet_budget < 1:
            r.bullet_budget = 1
            r.band = r.band if r.band != "none" else "low"
            r.reasons.append("recomposed: RELEVANT_EVIDENCE_OMITTED on a prior pass")

    # The floors must survive trimming: without recent_ids here the global
    # cap could zero out the current role the floor had just protected.
    _trim_to_total(ranked, must_support | recent_ids | set(forced or ()),
                   total_bullets)
    return ranked


def _trim_to_total(ranked: list[RankedExperience], protected: set[str],
                   total_bullets: int) -> None:
    """Shed bullets from the least relevant experiences until the budget fits."""
    def used() -> int:
        return sum(r.bullet_budget for r in ranked)

    order = sorted(ranked, key=lambda r: r.jd_score)   # least relevant first
    # Pass 1: unprotected roles down to zero.
    for r in order:
        while used() > total_bullets and r.bullet_budget > 0 and \
                r.record.id not in protected:
            r.bullet_budget -= 1
            r.reasons.append("trimmed for the two-page budget")
    # Pass 2: protected roles down to their floor of one.
    for r in order:
        while used() > total_bullets and r.bullet_budget > 1:
            r.bullet_budget -= 1
            r.reasons.append("trimmed for the two-page budget (kept >=1)")
    for r in ranked:
        if r.bullet_budget == 0:
            r.band = "none"


def select_bullets(rec: EvidenceRecord, spec: JobSpec, matrix: MatchMatrix,
                   limit: int, emphasis: set[str]) -> list[str]:
    """Pick the bullets that answer THIS JD.

    The previous selector scored bullets against emphasis *tags* and then added
    `2 * len(tags & matched_skills)` — but tags are words like `cloud`/`cicd`
    while matched skills are names like `Azure`, so the two sets could never
    intersect and the JD had literally zero influence on bullet choice. Here the
    JD's own requirement terms are matched against the bullet TEXT, weighted by
    requirement weight, with tag overlap as a secondary signal.
    """
    if limit <= 0:
        return []
    # A bullet earns its place by answering the JD, weighted by how central the
    # requirement's DOMAIN is. Ranking on requirement weight alone put the
    # AWS/EKS bullet above the generative-AI governance bullet on an
    # AI-governance vacancy: both are must-haves, only one is the subject.
    claimable_terms = [(m.term, m.requirement.weight, m.requirement.is_must,
                        spec.domain_rank(m.requirement.dimension))
                       for m in matrix.claimable()]
    scored: list[tuple[float, int, str]] = []
    for b in rec.bullets:
        score = 0.0
        for term, weight, is_must, domain_rank in claimable_terms:
            if term_present(term, b["norm"]):
                score += weight * (3.0 if is_must else 1.2) * (0.4 + domain_rank)
        score += 0.4 * len(set(b["tags"]) & emphasis)
        scored.append((score, b["idx"], b["text"]))
    top = sorted(scored, key=lambda t: (-t[0], t[1]))[:limit]
    # Ordered by relevance, ties broken by document order. The most relevant
    # thing this role did for THIS vacancy is the first thing the reader sees.
    return [text for _, _, text in top]


def rendered_coverage(matrix: MatchMatrix, cv_text: str) -> tuple[float, list[str]]:
    """Of the requirements we CAN claim, how many actually reached the CV?

    This is the honest ATS question and it is deliberately separate from
    `MatchMatrix.coverage()`. Coverage answers "how well does this candidate
    match the vacancy?" — a number that legitimately drops when the JD asks for
    Veeam and the candidate has never used it. Rendering answers "did we lose a
    keyword we were entitled to?" — a defect of the generator. Conflating the
    two is how the old gate reported ATS 10/10 on a CV that had silently
    dropped two of three Azure engagements.
    """
    low = normalize(cv_text)
    claimable = matrix.claimable()
    if not claimable:
        return 1.0, []
    missing: list[str] = []
    hit = 0
    for m in claimable:
        if term_present(m.term, low) or any(
                term_present(e, low) for e in m.evidence if not e.startswith("text:")):
            hit += 1
        else:
            missing.append(m.term)
    return round(hit / len(claimable), 4), missing


# --------------------------------------------------------------------------- #
# Auditable match breakdown
# --------------------------------------------------------------------------- #
def requirement_credit(match: RequirementMatch) -> float:
    """Credit a single requirement earns, in [0, 1].

    UNKNOWN scores the same as UNSUPPORTED — zero — and stays in the
    denominator. That is deliberate: a requirement we cannot substantiate must
    not lift the match score, and dropping it from the denominator instead is
    exactly the defect that let the previous gate report 100%. The UNKNOWN /
    UNSUPPORTED distinction is about what we may SAY, not about arithmetic; the
    breakdown reports both so the shortfall is explainable either way.
    """
    if match.can_claim == CAN_CLAIM_YES:
        return 1.0
    if match.can_claim == CAN_CLAIM_PARTIAL:
        return 0.35
    return 0.0


STATUS_OF_VERDICT = {
    CAN_CLAIM_YES: "supported",
    CAN_CLAIM_PARTIAL: "partial",
    CAN_CLAIM_NO: "unsupported",
    CAN_CLAIM_UNKNOWN: "unknown",
}


def match_breakdown(matrix: MatchMatrix, *, musts_only: bool = True) -> dict:
    """Per-requirement contributions that SUM to `MatchMatrix.coverage()`.

    Coverage averages over dimensions rather than over raw terms, so a single
    requirement's share of the final number is

        contribution = importance(dim) * credit / (terms_in_dim * total_importance)

    Summing every contribution reconstructs the score exactly; the test
    `test_match_score_is_reconstructible_from_breakdown` enforces that, so the
    reported percentage can never drift from what the matrix actually justifies.
    """
    from .jdspec import DIMENSION_WEIGHT

    scope = [m for m in matrix.matches
             if (m.requirement.is_must or not musts_only)]
    out_of_scope = [m for m in matrix.matches if m not in scope]

    per_dim: dict[str, list[RequirementMatch]] = {}
    for m in scope:
        per_dim.setdefault(m.requirement.dimension, []).append(m)
    total_importance = sum(
        DIMENSION_WEIGHT.get(d, DIMENSION_WEIGHT["other"]) for d in per_dim) or 1.0

    rows: list[dict] = []
    dimensions: list[dict] = []
    for dim, members in sorted(per_dim.items()):
        importance = DIMENSION_WEIGHT.get(dim, DIMENSION_WEIGHT["other"])
        share = importance / total_importance
        credits = [requirement_credit(m) for m in members]
        dimensions.append({
            "dimension": dim,
            "importance": round(importance, 4),
            "shareOfScore": round(share, 6),
            "requirements": len(members),
            "dimensionCoverage": round(sum(credits) / len(members), 6),
            "contribution": round(share * (sum(credits) / len(members)), 6),
        })
        for m in members:
            credit = requirement_credit(m)
            rows.append({
                "requirement": m.term,
                "dimension": dim,
                "priority": m.requirement.priority,
                "kind": m.requirement.kind,
                "weight": m.requirement.weight,
                "mentions": m.requirement.mentions,
                "status": STATUS_OF_VERDICT[m.can_claim],
                "credit": credit,
                "evidence_ids": list(m.experience_ids),
                "evidence": list(m.evidence),
                "score_contribution": round(share * credit / len(members), 6),
                "reason": m.note or m.source,
                "inScore": True,
            })
    for m in out_of_scope:
        rows.append({
            "requirement": m.term,
            "dimension": m.requirement.dimension,
            "priority": m.requirement.priority,
            "kind": m.requirement.kind,
            "weight": m.requirement.weight,
            "mentions": m.requirement.mentions,
            "status": STATUS_OF_VERDICT[m.can_claim],
            "credit": requirement_credit(m),
            "evidence_ids": list(m.experience_ids),
            "evidence": list(m.evidence),
            "score_contribution": 0.0,
            "reason": m.note or m.source,
            # Nice-to-haves are reported but do not move the must-have score.
            "inScore": False,
        })

    by_status: dict[str, list[str]] = {}
    for m in matrix.matches:
        by_status.setdefault(STATUS_OF_VERDICT[m.can_claim], []).append(m.term)

    recomputed = sum(r["score_contribution"] for r in rows if r["inScore"])
    return {
        "formula": ("score = SUM over dimensions of "
                    "importance(dim)/SUM(importance) * mean(credit in dim); "
                    "credit = 1.0 supported, 0.35 partial, 0.0 unsupported, "
                    "0.0 unknown"),
        "scope": "must-have requirements" if musts_only else "all requirements",
        "requirementsTotal": len(matrix.matches),
        "mustHave": len([m for m in matrix.matches if m.requirement.is_must]),
        "niceToHave": len([m for m in matrix.matches if not m.requirement.is_must]),
        "counts": {status: len(terms) for status, terms in sorted(by_status.items())},
        "byStatus": {status: sorted(terms) for status, terms in sorted(by_status.items())},
        "dimensions": dimensions,
        "requirements": rows,
        "recomputedScore": round(recomputed, 6),
        "reportedScore": matrix.coverage(musts_only=musts_only),
    }


def render_match_breakdown(breakdown: dict) -> str:
    """The MATCH BREAKDOWN block, as plain text for the debug report."""
    counts = breakdown["counts"]
    lines = [
        "MATCH BREAKDOWN",
        "",
        f"Requirements total: {breakdown['requirementsTotal']}",
        f"Must-have:          {breakdown['mustHave']}",
        f"Nice-to-have:       {breakdown['niceToHave']}",
        f"Scope of the score: {breakdown['scope']}",
        "",
    ]
    for status in ("supported", "partial", "unsupported", "unknown"):
        terms = breakdown["byStatus"].get(status, [])
        lines.append(f"{status.upper()} ({len(terms)}):")
        if terms:
            lines.extend(f"  - {term}" for term in terms)
        else:
            lines.append("  - none")
        lines.append("")
    lines += ["Per-dimension contribution:", "",
              f"  {'dimension':22s} {'reqs':>5s} {'importance':>11s} "
              f"{'coverage':>9s} {'contribution':>13s}"]
    for d in sorted(breakdown["dimensions"], key=lambda d: -d["contribution"]):
        lines.append(f"  {d['dimension']:22s} {d['requirements']:>5d} "
                     f"{d['importance']:>11.2f} {d['dimensionCoverage']:>9.3f} "
                     f"{d['contribution']:>13.4f}")
    lines += ["", breakdown["formula"], "",
              f"Weighted match score: {breakdown['reportedScore'] * 100:.1f}%",
              f"Recomputed from contributions: "
              f"{breakdown['recomputedScore'] * 100:.1f}%"]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# SKILL RELEVANCE — what belongs on THIS CV, and how prominently
# --------------------------------------------------------------------------- #
# A CV is not an inventory. Every skill in the catalogue is true, but a CV for
# an AI-governance role that spends a third of its skills block on CloudFront,
# DataPower, WebSphere and BrowserStack is a worse CV than one that omits them.
TIER_PRIMARY = "PRIMARY"
TIER_SECONDARY = "SECONDARY"
TIER_SUPPORTING = "SUPPORTING"
TIER_OMIT = "OMIT"


def skill_relevance(skill: str, *, spec, matrix: MatchMatrix,
                    inventory: EvidenceInventory, catalog) -> tuple[float, dict]:
    """relevance = JD semantic relevance x evidence strength x target importance.

    * semantic relevance — where the skill's dimension sits in the JD's centre
      of gravity.
    * evidence strength — how many real engagements back it.
    * target importance — does it answer a requirement the JD actually made,
      and how central was that requirement. This is the term that keeps Azure
      at the top of an Azure vacancy even when the parser's heaviest dimension
      is networking: the requirement is named, must-have and repeated.
    """
    from .inventory import CATEGORY_DIMENSION
    category = catalog.category_of(skill) or ""
    dimension = CATEGORY_DIMENSION.get(category, "other")
    semantic = spec.domain_rank(dimension)

    ids = [i for i in inventory.ids_with_skill(skill) if not i.startswith("project:")]
    projects = [i for i in inventory.ids_with_skill(skill) if i.startswith("project:")]
    evidence = min(1.0, 0.35 + 0.2 * len(ids) + (0.25 if projects else 0.0))

    importance = 0.25
    matched_terms: list[str] = []
    for m in matrix.claimable():
        if skill not in m.evidence:
            continue
        matched_terms.append(m.term)
        weight = m.requirement.weight * (1 + 0.3 * min(m.requirement.mentions, 6))
        importance = max(importance, min(1.6, weight))
        # A skill that answers a requirement inherits that requirement's
        # centrality. Terraform is not "IaC trivia" on a vacancy whose centre is
        # IaC, and AI Governance is not peripheral on a governance vacancy just
        # because the catalogue files it under a different category name.
        semantic = max(semantic, spec.domain_rank(m.requirement.dimension))

    score = semantic * evidence * importance
    return round(score, 5), {
        "skill": skill, "category": category, "dimension": dimension,
        "semantic": semantic, "evidence": round(evidence, 3),
        "importance": round(importance, 3), "matched": matched_terms,
        "experiences": len(ids),
    }


def rank_skills(spec, matrix: MatchMatrix, inventory: EvidenceInventory,
                catalog) -> dict[str, dict]:
    """Score every evidenced skill and assign it a tier."""
    scored: dict[str, dict] = {}
    for skill in inventory.skill_evidence:
        score, detail = skill_relevance(skill, spec=spec, matrix=matrix,
                                        inventory=inventory, catalog=catalog)
        detail["score"] = score
        scored[skill] = detail
    if not scored:
        return scored

    ordered = sorted(scored.values(), key=lambda d: -d["score"])
    top = ordered[0]["score"] or 1.0
    for detail in ordered:
        ratio = detail["score"] / top
        central = detail["semantic"] >= 0.7      # primary or secondary domain
        if ratio >= 0.55 or (central and detail["matched"]):
            detail["tier"] = TIER_PRIMARY
        elif central or ratio >= 0.22 or detail["matched"]:
            # A skill inside the vacancy's subject matter is worth showing even
            # if the JD never named that exact tool: it is the depth a reader is
            # scanning for.
            detail["tier"] = TIER_SECONDARY
        elif ratio >= 0.06:
            detail["tier"] = TIER_SUPPORTING
        else:
            detail["tier"] = TIER_OMIT
    return scored
