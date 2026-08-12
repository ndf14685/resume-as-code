"""Deterministic Evidence Validator + relevance ranking — THE AUTHORITY.

Two jobs:

  1. Rank canonical experiences/projects by relevance to a ROLE_INTENT, so
     composition expands the evidence that matters for THIS role and compresses
     the rest (a Software-Engineer-AI role expands Java/backend + NexusOS; a
     DevOps role expands cloud/CI-CD).

  2. Validate untrusted proposals (from the LLM semantic planner) against
     canonical facts: a semantic-match/reframing claim may only use evidence
     that actually exists in the dataset. Anything else is REJECTED. This is the
     boundary the security invariant names:
         "No unsupported claim can cross the deterministic evidence boundary."
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .models import DataBundle, Experience
from .roleintent import RoleIntent


# --------------------------------------------------------------------------- #
# Relevance ranking
# --------------------------------------------------------------------------- #
@dataclass
class ScoredExperience:
    exp: Experience
    score: float
    reasons: list[str] = field(default_factory=list)


def _recency_weight(exp: Experience) -> float:
    """Newer roles weigh a little more; 'present' is the max."""
    end = (exp.end or "").lower()
    if end in ("present", "current", ""):
        return 1.0
    m = re.search(r"(19|20)\d{2}", end)
    if not m:
        return 0.5
    year = int(m.group(0))
    # linear-ish decay from 1.0 (>=2024) to 0.4 (<=2013)
    return max(0.4, min(1.0, 0.4 + (year - 2013) * (0.6 / 11)))


def _exp_categories(bundle: DataBundle, exp: Experience) -> set[str]:
    cats: set[str] = set()
    for sk in exp.skills:
        c = bundle.skills.category_of(sk)
        if c:
            cats.add(c)
    return cats


def relevance_score(bundle: DataBundle, exp: Experience, intent: RoleIntent) -> ScoredExperience:
    """Score = category alignment + reasoning-tag/skill hits + technical depth +
    recency. Purely from canonical facts; nothing invented."""
    reasons: list[str] = []
    cats = _exp_categories(bundle, exp)
    cat_align = sum(intent.category_weights.get(c, 0.0) for c in cats)
    if cat_align:
        reasons.append(f"category-fit={round(cat_align, 2)}")

    text = " ".join([exp.title.lower(), " ".join(exp.skills).lower(),
                     " ".join(b.text.lower() for b in exp.bullets)])
    tag_hits = sum(1 for t in intent.reasoning_tags if t and re.search(
        rf"(?<![\w]){re.escape(t)}(?![\w])", text))
    if tag_hits:
        reasons.append(f"jd-terms={tag_hits}")

    depth = min(1.0, (len(exp.bullets) + len(exp.skills)) / 8.0)
    recency = _recency_weight(exp)

    score = (2.2 * cat_align) + (0.8 * tag_hits) + (0.9 * depth) + (0.8 * recency)
    return ScoredExperience(exp=exp, score=round(score, 4), reasons=reasons)


def rank_experiences(bundle: DataBundle, intent: RoleIntent) -> list[ScoredExperience]:
    scored = [relevance_score(bundle, e, intent) for e in bundle.experiences_sorted()]
    return scored


# --------------------------------------------------------------------------- #
# The evidence boundary: validate claims / semantic matches / reframings
# --------------------------------------------------------------------------- #
@dataclass
class ClaimVerdict:
    text: str
    supported: bool
    evidence: list[str] = field(default_factory=list)
    reason: str = ""


class EvidenceAuthority:
    """Wraps the canonical dataset and answers, for any proposed concept/keyword,
    'is this backed by real evidence?' — the only source of truth."""

    def __init__(self, bundle: DataBundle):
        self.bundle = bundle
        self._evidence = bundle.evidence_index()          # canonical skill -> [evidence]
        self._alias = bundle.skills.alias_index()          # alias -> canonical
        # Build a lowercase concept → canonical-skill index from real facts only.
        self._concept: dict[str, str] = {}
        for alias, canonical in self._alias.items():
            if canonical in self._evidence:                 # only evidenced skills
                self._concept[alias.lower()] = canonical
        # Featured-project concepts (NexusOS bullets) are real evidence too.
        self._project_terms: set[str] = set()
        for p in bundle.projects:
            for b in p.bullets:
                self._project_terms.add(b.text.lower())

    def evidenced_skills(self) -> set[str]:
        return set(self._evidence)

    def resolve_concept(self, term: str) -> Optional[str]:
        """Return the canonical skill a JD term maps to, if (and only if) it has
        real evidence. None means 'no direct evidence'."""
        t = term.strip().lower()
        return self._concept.get(t)

    def validate_semantic_match(self, jd_term: str, candidate_evidence: list[str]) -> ClaimVerdict:
        """An LLM proposes that `jd_term` is covered by `candidate_evidence`.
        Accept ONLY the cited evidence items that truly exist as canonical
        evidenced skills. If none survive → unsupported (reject)."""
        real = [e for e in candidate_evidence if e in self._evidence]
        if real:
            return ClaimVerdict(jd_term, True, real,
                                "backed by canonical evidenced skills")
        return ClaimVerdict(jd_term, False, [],
                            "no cited evidence exists in canonical data")

    def validate_reframing(self, reframed_text: str, concepts: list[str]) -> ClaimVerdict:
        """A reframed bullet/summary may only use `concepts` that each map to
        real evidence (a skill with evidence, or a canonical project term).
        CLAIM → EVIDENCE[]; any concept without evidence rejects the whole
        reframing (fail-closed)."""
        evidence: list[str] = []
        for c in concepts:
            canonical = self.resolve_concept(c)
            if canonical:
                evidence.append(canonical)
                continue
            # allow concepts literally present in canonical project/experience text
            cl = c.strip().lower()
            if any(cl in t for t in self._project_terms):
                evidence.append(c)
                continue
            return ClaimVerdict(reframed_text, False, evidence,
                                f"concept without evidence: {c!r}")
        return ClaimVerdict(reframed_text, True, evidence, "all concepts evidenced")

    def scan_for_unsupported(self, text: str, deny_terms: list[str]) -> list[str]:
        """Return any deny-listed (unsupported/JD-only) term that leaked into a
        rendered text. Used as a final fail-closed gate on generated content."""
        low = text.lower()
        return [t for t in deny_terms
                if re.search(rf"(?<![\w]){re.escape(t.lower())}(?![\w])", low)]
