"""ROLE_INTENT: what role is this JD hiring for, and how much of each family.

Deterministic engine + closed schema. It runs on the JD's *title and body* (so a
title-only JD like "Software Engineer - AI" still yields a real intent) and
produces a normalized weight vector over a fixed catalogue of role families,
each mapped to canonical skill categories. It is used two ways:

  1. as the offline fallback when no LLM is available, and
  2. as the CLOSED SCHEMA that validates an LLM's ROLE_INTENT proposal — any
     family/seniority the LLM invents outside this catalogue is rejected.

No LLM here, no network, no invented facts. It only decides *positioning*.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# --------------------------------------------------------------------------- #
# Closed catalogue. Each family declares: title patterns (strong signal),
# keyword weights (body signal), and the canonical skill-category weights it
# implies (used by compose.py to order Core Skills and pick evidence).
# Category ids MUST exist in data/skills.yaml.
# --------------------------------------------------------------------------- #
ROLE_FAMILIES: dict[str, dict] = {
    "software_engineering": {
        "titles": [r"software engineer", r"software developer", r"\bsde\b",
                   r"programmer", r"software development engineer"],
        "keywords": {"python": 2, "java": 2, "api": 2, "apis": 2, "backend": 2,
                     "distributed systems": 3, "microservices": 2, "algorithms": 1,
                     "data structures": 1, "software engineering": 3, "testing": 1,
                     "object-oriented": 1, "system design": 2, "code": 1},
        "categories": {"languages": 1.0, "platform": 0.6, "integration": 0.5,
                       "data": 0.3, "quality": 0.3},
    },
    "ai_systems": {
        "titles": [r"ai engineer", r"ml engineer", r"machine learning engineer",
                   r"ai/ml", r"\bai\b.*engineer", r"engineer.*\bai\b",
                   r"agentic", r"llm engineer"],
        "keywords": {"ai": 3, "artificial intelligence": 3, "llm": 3, "llms": 3,
                     "agent": 3, "agents": 3, "agentic": 3, "machine learning": 2,
                     "mcp": 3, "orchestration": 2, "rag": 2, "prompt": 2,
                     "model": 1, "multi-agent": 3, "tool execution": 2,
                     "llm orchestration": 3, "generative ai": 2, "genai": 2},
        "categories": {"ai": 1.0, "languages": 0.4, "platform": 0.3},
    },
    "ai_platform": {
        "titles": [r"ai platform engineer", r"ml platform", r"ai infrastructure",
                   r"ml infrastructure", r"mlops"],
        "keywords": {"ai platform": 3, "agent runtime": 3, "model serving": 2,
                     "provider routing": 3, "inference": 2, "mlops": 2,
                     "ai infrastructure": 3, "model routing": 2},
        "categories": {"ai": 0.8, "platform": 0.7, "containers": 0.5,
                       "cloud": 0.4, "reliability": 0.3},
    },
    "ai_architect": {
        "titles": [r"ai architect", r"ai systems architect",
                   r"ai solutions architect", r"principal.*\bai\b"],
        "keywords": {"ai architecture": 3, "ai governance": 3, "governance": 2,
                     "multi-agent": 2, "capability": 1, "policy": 1,
                     "autonomous": 2, "ai systems": 3},
        "categories": {"ai": 1.0, "platform": 0.6, "devsecops": 0.4,
                       "reliability": 0.3},
    },
    "platform_engineering": {
        "titles": [r"platform engineer", r"infrastructure engineer",
                   r"developer platform", r"internal platform"],
        "keywords": {"platform": 3, "kubernetes": 2, "terraform": 2,
                     "runtime": 1, "developer platform": 3, "internal tooling": 2,
                     "self-service": 1, "golden path": 2},
        "categories": {"platform": 1.0, "containers": 0.8, "iac": 0.7,
                       "cicd": 0.6, "cloud": 0.6},
    },
    "devops": {
        "titles": [r"devops engineer", r"devops", r"cloud engineer"],
        "keywords": {"devops": 3, "ci/cd": 3, "cicd": 3, "terraform": 2,
                     "kubernetes": 2, "automation": 2, "cloud": 2, "pipeline": 2,
                     "gitops": 2, "docker": 1, "aws": 1, "gcp": 1, "azure": 1},
        "categories": {"cicd": 1.0, "cloud": 0.9, "containers": 0.8, "iac": 0.8,
                       "reliability": 0.5, "platform": 0.4},
    },
    "devsecops": {
        "titles": [r"devsecops", r"secure devops"],
        "keywords": {"devsecops": 3, "secure sdlc": 3, "sast": 2, "sca": 2,
                     "security": 2, "shift left": 2, "pipeline security": 2},
        "categories": {"devsecops": 1.0, "cicd": 0.7, "cloud": 0.5,
                       "containers": 0.4},
    },
    "sre": {
        "titles": [r"\bsre\b", r"site reliability", r"reliability engineer"],
        "keywords": {"reliability": 3, "sre": 3, "incident": 2, "observability": 2,
                     "slo": 2, "sla": 1, "on-call": 2, "production support": 2,
                     "monitoring": 2},
        "categories": {"reliability": 1.0, "platform": 0.5, "containers": 0.5,
                       "cloud": 0.5},
    },
    "application_security": {
        "titles": [r"application security", r"\bappsec\b", r"security engineer",
                   r"product security"],
        "keywords": {"appsec": 3, "application security": 3, "sast": 2, "dast": 2,
                     "owasp": 2, "secure coding": 2, "vulnerability": 2,
                     "threat modeling": 2, "sca": 1},
        "categories": {"devsecops": 1.0, "languages": 0.4, "quality": 0.3},
    },
    "cloud_security": {
        "titles": [r"cloud security", r"security architect"],
        "keywords": {"cloud security": 3, "iam": 2, "cspm": 2, "kubernetes security": 2,
                     "secrets management": 2, "zero trust": 2},
        "categories": {"devsecops": 0.8, "cloud": 0.8, "containers": 0.5},
    },
    "backend_engineering": {
        "titles": [r"backend engineer", r"back-end engineer", r"backend developer",
                   r"server-side"],
        "keywords": {"backend": 3, "api": 2, "apis": 2, "microservices": 2,
                     "java": 2, "node": 1, "database": 2, "sql": 1, "rest": 1,
                     "grpc": 1, "distributed systems": 2},
        "categories": {"languages": 1.0, "integration": 0.6, "data": 0.5,
                       "platform": 0.4, "reliability": 0.3},
    },
}

ALLOWED_FAMILIES = frozenset(ROLE_FAMILIES)
SENIORITY_LEVELS = ("junior", "mid", "senior", "staff", "principal", "lead")

_SENIORITY_TITLE = {
    "principal": "principal", "staff": "staff", "lead": "lead",
    "senior": "senior", "sr.": "senior", "sr ": "senior",
    "junior": "junior", "jr.": "junior", "jr ": "junior",
    "mid": "mid", "intermediate": "mid",
}


@dataclass
class RoleIntent:
    job_title: str
    primary_role: str
    role_weights: dict[str, float] = field(default_factory=dict)
    category_weights: dict[str, float] = field(default_factory=dict)
    seniority: Optional[str] = None
    reasoning_tags: list[str] = field(default_factory=list)
    source: str = "deterministic"     # deterministic | llm | llm+validated

    def top_families(self, n: int = 2) -> list[str]:
        return [f for f, _ in sorted(self.role_weights.items(),
                                     key=lambda kv: -kv[1])][:n]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


# Recruiter / company / location / employment noise: a JD subject line like
# "AI Security Architect Opportunity - US Global Semiconductor Leader Enterprise -
# 100% Remote" must NOT become the professional title. These tokens (and anything
# after them) are stripped so only the ROLE survives.
_NOISE_TOKEN = re.compile(
    r"\b(opportunity|opportunities|position|vacancy|opening|role\s+at|hiring|urgent|"
    r"remote|onsite|on-site|hybrid|relocation|contract|c2c|w2|full[\s-]?time|"
    r"part[\s-]?time|permanent|enterprise|global|worldwide|leader|semiconductor|"
    r"fortune|inc\.?|ltd\.?|llc|gmbh|corp\.?|company|client|usd|salary|100%|"
    r"visa|h1b|eod|asap)\b", re.I)


def _title_head(s: str) -> str:
    """The ROLE phrase only. Strips markdown, cuts at sentence/label boundaries,
    splits recruiter-subject segments on ' - '/'|'/dashes and keeps only the
    leading role segments (stopping at the first company/location/context noise).
    'Software Engineer - AI' survives; 'AI Security Architect Opportunity - US
    Global ... - 100% Remote' reduces to 'AI Security Architect'."""
    s = s.strip().strip("#*_ ").strip()
    s = re.split(r"[.:;,]", s, maxsplit=1)[0].strip().strip("*_ ").strip()
    segs = re.split(r"\s+[-–—|]\s+", s)
    kept: list[str] = []
    for seg in segs:
        m = _NOISE_TOKEN.search(seg)
        if m:
            head = seg[:m.start()].strip()
            if head and not kept:
                kept.append(head)
            break
        kept.append(seg)
    s = " - ".join(kept).strip() if kept else (segs[0] if segs else "")
    words = s.split()
    if len(words) > 7:
        s = " ".join(words[:7])
    return s.rstrip(" -–—|").strip()


def _extract_title(jd_text: str) -> str:
    """Best-effort job title. Honors 'Role:'/'Title:'/'Position:' prefixes and
    common markdown emphasis; else the first non-empty line, reduced to the
    role phrase."""
    for line in jd_text.splitlines():
        s = line.strip().strip("#*_ ").strip()
        if not s:
            continue
        m = re.match(r"(?:role|title|position|puesto|rol)\s*[:\-]\s*(.+)", s, re.I)
        return _title_head(m.group(1) if m else s)
    return ""


def _seniority(title: str, body: str, years: Optional[float]) -> Optional[str]:
    t = title.lower()
    for token, level in _SENIORITY_TITLE.items():
        if token in t:
            return level
    b = body.lower()
    for token, level in _SENIORITY_TITLE.items():
        if token in b:
            return level
    if years is not None:
        if years >= 8:
            return "senior"
        if years >= 4:
            return "mid"
        return "junior"
    return None


def infer_role_intent(jd_text: str, *, years_experience: Optional[float] = None) -> RoleIntent:
    """Deterministic ROLE_INTENT from title (strong) + body keywords."""
    title = _extract_title(jd_text)
    text = _normalize(jd_text)
    title_norm = _normalize(title)

    raw: dict[str, float] = {}
    tags: list[str] = []
    for fam, spec in ROLE_FAMILIES.items():
        score = 0.0
        for pat in spec["titles"]:
            if re.search(pat, title_norm):
                score += 6.0          # title is the strongest signal
            elif re.search(pat, text):
                score += 1.5
        for kw, w in spec["keywords"].items():
            if re.search(rf"(?<![\w]){re.escape(kw)}(?![\w])", text):
                score += float(w)
                if w >= 3 and kw not in tags:
                    tags.append(kw)
        if score > 0:
            raw[fam] = score

    if not raw:
        # No signal at all → neutral software-engineering baseline (never a
        # hardcoded AI-architect default). Keeps the system honest for empty JDs.
        raw = {"software_engineering": 1.0}

    total = sum(raw.values())
    weights = {f: round(s / total, 4) for f, s in raw.items()}
    primary = max(weights, key=lambda f: weights[f])

    # Blend skill-category weights across families, weighted by role weight.
    cat: dict[str, float] = {}
    for fam, w in weights.items():
        for c, cw in ROLE_FAMILIES[fam]["categories"].items():
            cat[c] = cat.get(c, 0.0) + w * cw
    # normalize categories to [0,1] by max
    if cat:
        mx = max(cat.values())
        cat = {c: round(v / mx, 4) for c, v in cat.items()}

    return RoleIntent(
        job_title=title,
        primary_role=primary,
        role_weights=weights,
        category_weights=cat,
        seniority=_seniority(title, jd_text, years_experience),
        reasoning_tags=tags[:8],
        source="deterministic",
    )


def validate_intent_schema(proposal: dict, *, years_experience: Optional[float] = None) -> Optional[RoleIntent]:
    """Validate an LLM ROLE_INTENT proposal against the CLOSED schema. Any
    unknown family or malformed value → None (caller falls back to deterministic).
    The LLM never widens the catalogue; it only re-weights within it."""
    try:
        weights_in = proposal.get("role_weights") or {}
        if not isinstance(weights_in, dict) or not weights_in:
            return None
        clean: dict[str, float] = {}
        for fam, w in weights_in.items():
            if fam not in ALLOWED_FAMILIES:
                return None                      # reject invented family
            w = float(w)
            if not (0.0 <= w <= 1.0):
                return None
            clean[fam] = w
        total = sum(clean.values()) or 1.0
        clean = {f: round(w / total, 4) for f, w in clean.items()}
        primary = proposal.get("primary_role") or max(clean, key=lambda f: clean[f])
        if primary not in ALLOWED_FAMILIES:
            return None
        seniority = proposal.get("seniority")
        if seniority is not None and seniority not in SENIORITY_LEVELS:
            seniority = None
        cat: dict[str, float] = {}
        for fam, w in clean.items():
            for c, cw in ROLE_FAMILIES[fam]["categories"].items():
                cat[c] = cat.get(c, 0.0) + w * cw
        if cat:
            mx = max(cat.values())
            cat = {c: round(v / mx, 4) for c, v in cat.items()}
        tags = [t for t in (proposal.get("reasoning_tags") or []) if isinstance(t, str)][:8]
        return RoleIntent(
            job_title=str(proposal.get("job_title") or ""),
            primary_role=primary,
            role_weights=clean,
            category_weights=cat,
            seniority=seniority or (
                "senior" if (years_experience or 0) >= 8 else None),
            reasoning_tags=tags,
            source="llm+validated",
        )
    except (TypeError, ValueError):
        return None
