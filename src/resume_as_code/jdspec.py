"""JD REQUIREMENTS MODEL — the JD parsed into weighted, dimensioned requirements.

This is the first stage of the pipeline and it exists because the previous one
did not have it: positioning used to be derived from a closed dictionary of
generic role keywords (roleintent.ROLE_FAMILIES), so a JD could mention Azure
forty times and Veeam eight and the system would only ever "see" the words
devops / ci-cd / cloud. Every requirement the lexicon did not know about was
invisible — which is why real must-haves silently disappeared from the quality
gate instead of being reported as gaps.

Here the JD is parsed into:

    JobSpec
      title / seniority
      requirements: [Requirement(term, dimension, priority, weight, source)]

Requirements are NOT all equal: a must-have in the cloud-platform dimension
outweighs a "nice to have" certification. The weight is (dimension weight) x
(priority factor) and drives ranking, bullet budget, skill ordering and the
coverage gate downstream.

No LLM, no network. This module only *reads* the JD; it never touches candidate
facts and therefore can never invent one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# --------------------------------------------------------------------------- #
# Dimensions. The weight is how much this class of requirement matters when it
# is a must-have; `priority` then scales it down for preferred/desirable ones.
# --------------------------------------------------------------------------- #
DIMENSION_WEIGHT: dict[str, float] = {
    "cloud_platform": 1.00,
    "iac": 0.90,
    "networking": 0.80,
    "identity": 0.80,
    "security_governance": 0.80,
    "backup_dr": 0.80,
    "containers": 0.70,
    "cicd": 0.70,
    "os": 0.60,
    "observability": 0.55,
    "scripting": 0.50,
    "data": 0.50,
    "ai": 0.40,
    "domain": 0.40,
    "certification": 0.25,
    "other": 0.30,
}

PRIORITY_FACTOR = {"must": 1.0, "preferred": 0.35}

# --------------------------------------------------------------------------- #
# Requirement lexicon.
#
#   term -> (dimension, kind)
#
# `kind` matters for the factual validator: only "tool" terms (concrete
# products / platforms / certifications) are deny-scanned in the rendered CV,
# because a "concept" word like governance or automation can legitimately show
# up in unrelated canonical text without being a claim about the JD's tool.
#
# Multi-word terms are matched before their single-word substrings, so
# "azure landing zones" is its own requirement and never satisfied by "azure".
# The lexicon is open: adding a line teaches the parser a new requirement.
# --------------------------------------------------------------------------- #
TERM_LEXICON: dict[str, tuple[str, str]] = {
    # ── cloud platforms ────────────────────────────────────────────────────
    "microsoft azure": ("cloud_platform", "tool"),
    "azure": ("cloud_platform", "tool"),
    "aws": ("cloud_platform", "tool"),
    "amazon web services": ("cloud_platform", "tool"),
    "gcp": ("cloud_platform", "tool"),
    "google cloud": ("cloud_platform", "tool"),
    "oracle cloud": ("cloud_platform", "tool"),
    "azure landing zones": ("cloud_platform", "tool"),
    "landing zones": ("cloud_platform", "tool"),
    "cloud adoption framework": ("cloud_platform", "tool"),
    "azure arc": ("cloud_platform", "tool"),
    "azure policy": ("security_governance", "tool"),
    "azure monitor": ("observability", "tool"),
    "azure ai studio": ("ai", "tool"),
    "azure openai": ("ai", "tool"),
    "multicloud": ("cloud_platform", "concept"),
    "multi-cloud": ("cloud_platform", "concept"),
    # ── infrastructure as code ─────────────────────────────────────────────
    "terraform": ("iac", "tool"),
    "bicep": ("iac", "tool"),
    "arm templates": ("iac", "tool"),
    "ansible": ("iac", "tool"),
    "pulumi": ("iac", "tool"),
    "cloudformation": ("iac", "tool"),
    "aws cdk": ("iac", "tool"),
    "infrastructure as code": ("iac", "concept"),
    "infraestructura como codigo": ("iac", "concept"),
    "iac": ("iac", "concept"),
    # ── networking ─────────────────────────────────────────────────────────
    "expressroute": ("networking", "tool"),
    "private endpoints": ("networking", "tool"),
    "private endpoint": ("networking", "tool"),
    "network security groups": ("networking", "tool"),
    "vnet": ("networking", "tool"),
    "vnets": ("networking", "tool"),
    "redes virtuales": ("networking", "tool"),
    "virtual networks": ("networking", "tool"),
    "subnets": ("networking", "concept"),
    "tcp/ip": ("networking", "concept"),
    "dns": ("networking", "concept"),
    "vpn": ("networking", "concept"),
    "vpns": ("networking", "concept"),
    "firewalls": ("networking", "concept"),
    "firewall": ("networking", "concept"),
    "enrutamiento": ("networking", "concept"),
    "routing": ("networking", "concept"),
    "load balancer": ("networking", "tool"),
    "networking": ("networking", "concept"),
    # ── identity ───────────────────────────────────────────────────────────
    "microsoft entra id": ("identity", "tool"),
    "entra id": ("identity", "tool"),
    "azure ad": ("identity", "tool"),
    "azure active directory": ("identity", "tool"),
    "active directory": ("identity", "tool"),
    "conditional access": ("identity", "tool"),
    "keycloak": ("identity", "tool"),
    "okta": ("identity", "tool"),
    "rbac": ("identity", "concept"),
    "federacion": ("identity", "concept"),
    "federation": ("identity", "concept"),
    "sso": ("identity", "concept"),
    "iam": ("identity", "concept"),
    "gestion de identidades": ("identity", "concept"),
    "identity and access management": ("identity", "concept"),
    # ── security & governance ──────────────────────────────────────────────
    "governance": ("security_governance", "concept"),
    "gobierno": ("security_governance", "concept"),
    "cumplimiento normativo": ("security_governance", "concept"),
    "compliance": ("security_governance", "concept"),
    "cifrado": ("security_governance", "concept"),
    "encryption": ("security_governance", "concept"),
    "inmutabilidad": ("security_governance", "concept"),
    "immutability": ("security_governance", "concept"),
    "optimizacion de costos": ("security_governance", "concept"),
    "cost optimization": ("security_governance", "concept"),
    "finops": ("security_governance", "concept"),
    "defender for cloud": ("security_governance", "tool"),
    "sentinel": ("security_governance", "tool"),
    "cloud security": ("security_governance", "concept"),
    "secure sdlc": ("security_governance", "concept"),
    "sast": ("security_governance", "tool"),
    "sca": ("security_governance", "tool"),
    "sonarqube": ("security_governance", "tool"),
    "devsecops": ("security_governance", "concept"),
    "hardening": ("security_governance", "concept"),
    "zero trust": ("security_governance", "concept"),
    # ── backup / disaster recovery ─────────────────────────────────────────
    "veeam backup & replication": ("backup_dr", "tool"),
    "veeam backup and replication": ("backup_dr", "tool"),
    "veeam backup for microsoft azure": ("backup_dr", "tool"),
    "veeam": ("backup_dr", "tool"),
    "azure backup": ("backup_dr", "tool"),
    "azure site recovery": ("backup_dr", "tool"),
    "commvault": ("backup_dr", "tool"),
    "netbackup": ("backup_dr", "tool"),
    "disaster recovery": ("backup_dr", "concept"),
    "recuperacion ante desastres": ("backup_dr", "concept"),
    "backup": ("backup_dr", "concept"),
    "respaldo": ("backup_dr", "concept"),
    # ── containers ─────────────────────────────────────────────────────────
    "kubernetes": ("containers", "tool"),
    "aks": ("containers", "tool"),
    "azure kubernetes service": ("containers", "tool"),
    "eks": ("containers", "tool"),
    "gke": ("containers", "tool"),
    "openshift": ("containers", "tool"),
    "docker": ("containers", "tool"),
    "helm": ("containers", "tool"),
    "contenedores": ("containers", "concept"),
    "containers": ("containers", "concept"),
    "orquestacion": ("containers", "concept"),
    "orchestration": ("containers", "concept"),
    # ── ci/cd ──────────────────────────────────────────────────────────────
    "azure devops": ("cicd", "tool"),
    "azure pipelines": ("cicd", "tool"),
    "github actions": ("cicd", "tool"),
    "gitlab ci": ("cicd", "tool"),
    "jenkins": ("cicd", "tool"),
    "argocd": ("cicd", "tool"),
    "bitbucket": ("cicd", "tool"),
    "ci/cd": ("cicd", "concept"),
    "cicd": ("cicd", "concept"),
    "pipelines": ("cicd", "concept"),
    "gitops": ("cicd", "concept"),
    "automatizacion": ("cicd", "concept"),
    "automation": ("cicd", "concept"),
    # ── operating systems ──────────────────────────────────────────────────
    "windows server": ("os", "tool"),
    "windows": ("os", "tool"),
    "linux": ("os", "tool"),
    "rhel": ("os", "tool"),
    "ubuntu": ("os", "tool"),
    # ── observability ──────────────────────────────────────────────────────
    "observability": ("observability", "concept"),
    "observabilidad": ("observability", "concept"),
    "monitoreo": ("observability", "concept"),
    "monitoring": ("observability", "concept"),
    "prometheus": ("observability", "tool"),
    "grafana": ("observability", "tool"),
    "datadog": ("observability", "tool"),
    "nagios": ("observability", "tool"),
    "elastic": ("observability", "tool"),
    "splunk": ("observability", "tool"),
    # ── scripting ──────────────────────────────────────────────────────────
    "powershell": ("scripting", "tool"),
    "bash": ("scripting", "tool"),
    "python": ("scripting", "tool"),
    "scripting": ("scripting", "concept"),
    # ── data ───────────────────────────────────────────────────────────────
    "sql": ("data", "concept"),
    "airflow": ("data", "tool"),
    "kafka": ("data", "tool"),
    "snowflake": ("data", "tool"),
    "databricks": ("data", "tool"),
    # ── certifications ─────────────────────────────────────────────────────
    "az-104": ("certification", "tool"),
    "az-305": ("certification", "tool"),
    "az-700": ("certification", "tool"),
    "az-400": ("certification", "tool"),
    "vmce": ("certification", "tool"),
    "cka": ("certification", "tool"),
    "aws certified": ("certification", "tool"),
    "certificaciones": ("certification", "concept"),
    "certifications": ("certification", "concept"),
    # ── domain ─────────────────────────────────────────────────────────────
    "banking": ("domain", "concept"),
    "banco": ("domain", "concept"),
    "bancario": ("domain", "concept"),
    "fintech": ("domain", "concept"),
    "seguros": ("domain", "concept"),
    "insurance": ("domain", "concept"),
    "telecom": ("domain", "concept"),
    "retail": ("domain", "concept"),
}

# --------------------------------------------------------------------------- #
# Synonym folding. A bilingual JD names the same requirement twice
# ("contenedores" and "containers", "infraestructura como codigo" and
# "Infrastructure as Code"). Without folding, the Spanish surface form has no
# canonical skill alias, so it resolves to NO and drags weighted coverage down
# while reporting a gap the candidate does not actually have.
# --------------------------------------------------------------------------- #
SYNONYMS: dict[str, str] = {
    "microsoft azure": "azure",
    "amazon web services": "aws",
    "google cloud": "gcp",
    "infraestructura como codigo": "infrastructure as code",
    "iac": "infrastructure as code",
    "contenedores": "containers",
    "orquestacion": "orchestration",
    "automatizacion": "automation",
    "monitoreo": "monitoring",
    "observabilidad": "observability",
    "cifrado": "encryption",
    "inmutabilidad": "immutability",
    "cumplimiento normativo": "compliance",
    "gobierno": "governance",
    "optimizacion de costos": "cost optimization",
    "enrutamiento": "routing",
    "federacion": "federation",
    "redes virtuales": "virtual networks",
    "vnets": "vnet",
    "vpns": "vpn",
    "firewall": "firewalls",
    "gestion de identidades": "identity and access management",
    # Same product, two names the JD uses in one breath ("Microsoft Entra ID
    # (Azure AD)"). Keeping them apart reported one gap twice.
    "azure active directory": "entra id",
    "azure ad": "entra id",
    "microsoft entra id": "entra id",
    "landing zones": "azure landing zones",
    "recuperacion ante desastres": "disaster recovery",
    "respaldo": "backup",
    # One requirement, three product names in the same ad. Folding them keeps
    # the gap report from spending three of its slots on the same missing tool.
    "veeam backup and replication": "veeam",
    "veeam backup & replication": "veeam",
    "veeam backup for microsoft azure": "veeam",
    "azure kubernetes service": "aks",
    "azure pipelines": "azure devops",
    "cicd": "ci/cd",
    "certificaciones": "certifications",
    "banco": "banking",
    "bancario": "banking",
    "seguros": "insurance",
    "private endpoint": "private endpoints",
}


# Terms that are strictly narrower than another term in the lexicon: when the
# broader one is detected on the same line, the narrower is what we keep. Sorted
# longest-first at match time, so "azure landing zones" wins over "azure".
_SORTED_TERMS = sorted(TERM_LEXICON, key=lambda t: (-len(t), t))

# --------------------------------------------------------------------------- #
# Section detection (ES + EN). A JD's own structure tells us must vs preferred.
# --------------------------------------------------------------------------- #
_SECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("preferred", re.compile(
        r"^\W*(deseable|deseables|deseado|valorable|se valorar|nice[\s-]to[\s-]have|"
        r"preferred|preferable|desirable|plus|bonus|opcional|optional)\b", re.I)),
    ("requirements", re.compile(
        r"^\W*(requisitos|requerimientos|requirements|calificaciones|"
        r"qualifications|qu[eé] necesitas|what you.{0,12}need|must[\s-]have|"
        r"imprescindible|excluyente|perfil buscado|skills? (?:required|needed))\b", re.I)),
    ("responsibilities", re.compile(
        r"^\W*(responsabilidades|responsibilities|asignaciones|funciones|"
        r"tu d[ií]a a d[ií]a|d[ií]a a d[ií]a|what you.{0,12}do|"
        r"about the role|descripci[oó]n del puesto|misi[oó]n del puesto|tareas)\b", re.I)),
    ("ignore", re.compile(
        r"^\W*(beneficios|benefits|ofrecemos|we offer|qu[eé] ofrecemos|"
        r"sobre nosotros|about us|about the company|nuestra cultura|"
        r"igualdad de oportunidades|diversidad|equal opportunity|how to apply)\b", re.I)),
]

_SENIORITY_TOKENS = (
    ("principal", "principal"), ("staff", "staff"), ("tech lead", "lead"),
    ("lead", "lead"), ("senior", "senior"), ("ssr", "senior"),
    ("sr.", "senior"), ("sr ", "senior"), ("semi senior", "mid"),
    ("junior", "junior"), ("jr.", "junior"), ("jr ", "junior"),
    ("mid", "mid"), ("intermediate", "mid"),
)

# Words that are never part of a role title: geography, contract form, company
# boilerplate. Used to clean the JD title before it informs seniority/intent.
_TITLE_NOISE = re.compile(
    r"\b(argentina|brasil|brazil|chile|mexico|m[eé]xico|colombia|peru|per[uú]|"
    r"uruguay|paraguay|espa[nñ]a|spain|usa|united states|latam|remoto?|remote|"
    r"h[ií]brido|hybrid|onsite|on-site|presencial|full[\s-]?time|part[\s-]?time|"
    r"contract|permanente|permanent|fijo|vacante|vacancy|opportunity|"
    r"oportunidad|posici[oó]n|position|opening|hiring|urgente|urgent|"
    r"ciudad de buenos aires|buenos aires|caba)\b", re.I)


def _strip_accents(text: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def normalize(text: str) -> str:
    """Lowercase, accent-folded, whitespace-collapsed. Both the JD and the CV go
    through this before term matching, so 'Optimización' matches 'optimizacion'."""
    return re.sub(r"\s+", " ", _strip_accents(text).lower())


def term_present(term: str, normalized_text: str) -> bool:
    """Whole-token containment of a (possibly multi-word) term."""
    return re.search(rf"(?<![\w]){re.escape(normalize(term))}(?![\w])",
                     normalized_text) is not None


@dataclass
class Requirement:
    term: str                  # canonical lexicon surface form
    dimension: str
    priority: str              # must | preferred
    kind: str                  # tool | concept
    weight: float
    sources: list[str] = field(default_factory=list)   # JD lines it came from
    # How many JD lines mention it. This is the "what is this vacancy actually
    # about" signal: an Azure role names Azure a dozen times and Bicep once.
    # Without it, ranking treats every must-have as equally central and a role
    # that ticks many peripheral requirements outranks the one that matches the
    # subject of the search.
    mentions: int = 0

    @property
    def is_must(self) -> bool:
        return self.priority == "must"


@dataclass
class JobSpec:
    raw_title: str = ""
    title: str = ""            # noise-stripped role phrase
    seniority: Optional[str] = None
    requirements: list[Requirement] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)

    # -- convenience views ---------------------------------------------------
    def musts(self) -> list[Requirement]:
        return [r for r in self.requirements if r.is_must]

    def by_dimension(self, dimension: str) -> list[Requirement]:
        return [r for r in self.requirements if r.dimension == dimension]

    def dimension_weights(self) -> dict[str, float]:
        """How much of the JD's total weight each dimension represents."""
        out: dict[str, float] = {}
        for r in self.requirements:
            out[r.dimension] = out.get(r.dimension, 0.0) + r.weight
        total = sum(out.values()) or 1.0
        return {d: round(w / total, 4) for d, w in sorted(
            out.items(), key=lambda kv: -kv[1])}

    def total_weight(self) -> float:
        return sum(r.weight for r in self.requirements)

    def tool_terms(self) -> list[str]:
        return [r.term for r in self.requirements if r.kind == "tool"]


def clean_title(raw: str) -> str:
    """The role phrase only. Drops geography/contract/company noise segments —
    the regression driver is the Logicalis subject line
    'Argentina - Sr Cloud Azure Infraestructura Engineer', whose leading
    'Argentina' used to survive into the CV headline *and* the first word of the
    professional summary."""
    s = raw.strip().strip("#*_ ").strip()
    s = re.split(r"[.:;]", s, maxsplit=1)[0].strip().strip("*_ ").strip()
    segments = [seg.strip() for seg in re.split(r"\s*[-–—|]\s*|\s{2,}", s) if seg.strip()]
    kept = [seg for seg in segments if not _TITLE_NOISE.fullmatch(seg.strip())
            and not (_TITLE_NOISE.search(seg) and len(seg.split()) <= 3)]
    s = " - ".join(kept) if kept else (segments[0] if segments else "")
    s = _TITLE_NOISE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" -–—|,")
    return " ".join(s.split()[:8])


def _extract_title(jd_text: str) -> tuple[str, str]:
    for line in jd_text.splitlines():
        s = line.strip().strip("#*_ ").strip()
        if not s:
            continue
        m = re.match(r"(?:role|title|position|puesto|rol|cargo)\s*[:\-]\s*(.+)", s, re.I)
        raw = m.group(1) if m else s
        return raw.strip(), clean_title(raw)
    return "", ""


def _seniority(title: str, body: str) -> Optional[str]:
    for scope in (title.lower(), body.lower()):
        for token, level in _SENIORITY_TOKENS:
            if token in scope:
                return level
    return None


def _terms_in(line: str) -> list[tuple[str, str, str]]:
    """(term, dimension, kind) for every lexicon term present in `line`.

    Longest-first with a consumed-span mask, so 'azure landing zones' claims its
    span and the bare 'azure' inside it is not double-counted from that phrase.
    A separate standalone 'Azure' elsewhere on the line still matches.
    """
    norm = normalize(line)
    masked = norm
    found: list[tuple[str, str, str]] = []
    for term in _SORTED_TERMS:
        pattern = rf"(?<![\w]){re.escape(normalize(term))}(?![\w])"
        if re.search(pattern, masked):
            canonical = SYNONYMS.get(term, term)
            dimension, kind = TERM_LEXICON.get(canonical, TERM_LEXICON[term])
            found.append((canonical, dimension, kind))
            masked = re.sub(pattern, lambda m: "\x00" * len(m.group(0)), masked)
    return found


def _classify_lines(jd_text: str) -> list[tuple[str, str]]:
    """[(section, line)] — section in {responsibilities, requirements,
    preferred, ignore, body}."""
    out: list[tuple[str, str]] = []
    current = "body"
    for raw_line in jd_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        header = None
        probe = _strip_accents(line.strip("#*_ ").strip())
        if len(probe.split()) <= 8:
            for name, pattern in _SECTION_PATTERNS:
                if pattern.search(probe):
                    header = name
                    break
        if header:
            current = header
            continue
        out.append((current, line))
    return out


def parse_jd(jd_text: str) -> JobSpec:
    """JD → structured, weighted requirements matrix."""
    raw_title, title = _extract_title(jd_text)
    spec = JobSpec(raw_title=raw_title, title=title,
                   seniority=_seniority(title, jd_text))

    classified = _classify_lines(jd_text)
    for section, line in classified:
        spec.sections.setdefault(section, []).append(line)

    # term -> Requirement, strongest priority wins (a term that shows up both in
    # "Requisitos" and in "Deseables" is a must-have).
    acc: dict[str, Requirement] = {}
    for section, line in classified:
        if section == "ignore":
            continue
        priority = "preferred" if section == "preferred" else "must"
        # Body text outside any recognised section is weaker evidence of a
        # requirement than an explicit requirements bullet.
        line_factor = 0.7 if section == "body" else 1.0
        for term, dimension, kind in _terms_in(line):
            weight = (DIMENSION_WEIGHT.get(dimension, DIMENSION_WEIGHT["other"])
                      * PRIORITY_FACTOR[priority] * line_factor)
            existing = acc.get(term)
            if existing is None:
                acc[term] = Requirement(term=term, dimension=dimension,
                                        priority=priority, kind=kind,
                                        weight=round(weight, 4),
                                        sources=[line[:220]], mentions=1)
                continue
            existing.mentions += 1
            if len(existing.sources) < 4 and line[:220] not in existing.sources:
                existing.sources.append(line[:220])
            if priority == "must" and existing.priority == "preferred":
                existing.priority = "must"
                existing.weight = round(weight, 4)
            elif weight > existing.weight:
                existing.weight = round(weight, 4)

    spec.requirements = sorted(acc.values(), key=lambda r: (-r.weight, r.term))
    return spec


def requirement_label(req: Requirement) -> str:
    """Human-facing name for reports/Telegram (title-cased, acronyms intact)."""
    if req.term.isupper() or len(req.term) <= 4:
        return req.term.upper() if req.term.replace("-", "").isalpha() and len(
            req.term) <= 4 else req.term
    return " ".join(w if w.isupper() else w.capitalize() for w in req.term.split())


def iter_terms(reqs: Iterable[Requirement]) -> list[str]:
    return [r.term for r in reqs]
