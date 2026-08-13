"""Hybrid semantic planner: LLM proposes, the deterministic layer is AUTHORITY.

Architecture (per the security invariant):

    JD → deterministic preprocessing (roleintent)
       → [optional] LLM proposal via an injected `ask(prompt)->str`
       → schema validation (closed catalogue)  + evidence validation (canonical)
       → accepted intent/claims  OR  deterministic fallback

    LLM OUTPUT      = UNTRUSTED PROPOSAL
    CANONICAL DATA  = TRUSTED FACTS
    THIS VALIDATOR  = AUTHORITY   → "no unsupported claim crosses the boundary"

The `ask` callable decouples resume-as-code from any provider: production wires
it to OpenClaw (the active AI provider gateway); tests inject a stub; when it is
None or fails, the system falls back to the deterministic intent. CV generation
is NEVER blocked by the absence of an LLM.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from .evidence import EvidenceAuthority
from .models import DataBundle
from .roleintent import RoleIntent, infer_role_intent, validate_intent_schema

PROMPT_VERSION = "role-intent/v1"

AskFn = Callable[[str], str]


@dataclass
class PlanResult:
    intent: RoleIntent
    semantic_matches: list[dict] = field(default_factory=list)   # accepted only
    accepted_claims: list[str] = field(default_factory=list)
    rejected_claims: list[str] = field(default_factory=list)
    fallback_used: bool = True
    provider: str = "deterministic"
    model: Optional[str] = None
    prompt_version: str = PROMPT_VERSION

    def audit(self, *, jd_text: str, bundle: DataBundle) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "canonical_evidence_hash": _bundle_hash(bundle),
            "jd_hash": hashlib.sha256(jd_text.encode()).hexdigest()[:16],
            "role_intent": {
                "primary_role": self.intent.primary_role,
                "role_weights": self.intent.role_weights,
                "seniority": self.intent.seniority,
                "source": self.intent.source,
            },
            "semantic_matches": self.semantic_matches,
            "accepted_claims": self.accepted_claims,
            "rejected_claims": self.rejected_claims,
            "fallback_used": self.fallback_used,
        }


def _bundle_hash(bundle: DataBundle) -> str:
    from .models import canonical_fingerprint
    return canonical_fingerprint(bundle)[:16]


def build_prompt(jd_text: str, bundle: DataBundle, det_intent: RoleIntent) -> str:
    """Prompt the LLM as a SEMANTIC PLANNER, not a source of truth. It re-weights
    within a closed family catalogue and may propose semantic matches, but every
    claim must cite candidate evidence that the validator will check."""
    from .roleintent import ALLOWED_FAMILIES
    evidenced = sorted(EvidenceAuthority(bundle).evidenced_skills())
    return (
        "You are a resume SEMANTIC PLANNER. You never invent facts.\n"
        "Choose positioning for this candidate against the job description.\n\n"
        f"ALLOWED role families (use ONLY these): {sorted(ALLOWED_FAMILIES)}\n"
        f"CANDIDATE evidenced skills (the ONLY facts you may rely on):\n{evidenced}\n\n"
        f"JOB DESCRIPTION:\n{jd_text.strip()[:4000]}\n\n"
        f"Deterministic baseline (you may refine, not widen): "
        f"{det_intent.primary_role} {det_intent.role_weights}\n\n"
        "Return ONLY JSON:\n"
        "{\n"
        '  "primary_role": "<one allowed family>",\n'
        '  "role_weights": {"<allowed family>": <0..1>, ...},\n'
        '  "seniority": "junior|mid|senior|staff|principal|lead",\n'
        '  "reasoning_tags": ["<jd term>", ...],\n'
        '  "semantic_matches": [\n'
        '     {"jd_term":"LLM orchestration","candidate_evidence":["LLM Provider Routing","Multi-Agent Systems"]}\n'
        "  ]\n"
        "}\n"
        "Every candidate_evidence value MUST be one of the evidenced skills above."
    )


def _parse_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def plan(
    jd_text: str,
    bundle: DataBundle,
    *,
    ask: Optional[AskFn] = None,
    years_experience: Optional[float] = None,
    provider: str = "openclaw",
    model: Optional[str] = None,
) -> PlanResult:
    det = infer_role_intent(jd_text, years_experience=years_experience)
    if ask is None:
        return PlanResult(intent=det, fallback_used=True, provider="deterministic")

    try:
        raw = ask(build_prompt(jd_text, bundle, det))
    except Exception:  # noqa: BLE001 — any provider failure → deterministic
        return PlanResult(intent=det, fallback_used=True, provider="deterministic")

    proposal = _parse_json(raw)
    if not proposal:
        return PlanResult(intent=det, fallback_used=True, provider="deterministic")

    intent = validate_intent_schema(proposal, years_experience=years_experience)
    if intent is None:                       # schema violation → reject, fallback
        return PlanResult(intent=det, fallback_used=True, provider="deterministic")
    if not intent.job_title:
        # el LLM re-pondera familias; el título del JD lo ancla el determinista
        intent.job_title = det.job_title

    # Evidence-validate every proposed semantic match; drop unsupported ones.
    authority = EvidenceAuthority(bundle)
    accepted, rejected, matches = [], [], []
    for sm in (proposal.get("semantic_matches") or []):
        term = str(sm.get("jd_term", "")).strip()
        cited = [str(c) for c in (sm.get("candidate_evidence") or [])]
        verdict = authority.validate_semantic_match(term, cited)
        if verdict.supported:
            matches.append({"jd_term": term, "evidence": verdict.evidence})
            accepted.append(term)
        elif term:
            rejected.append(term)

    return PlanResult(
        intent=intent, semantic_matches=matches,
        accepted_claims=accepted, rejected_claims=rejected,
        fallback_used=False, provider=provider, model=model,
    )
