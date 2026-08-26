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
    # Security-role dimensions. Without these a DevSecOps posting's hardest
    # asks — SOC 2, PCI DSS, penetration testing, customer due diligence —
    # were invisible to the parser, so they could not become gaps and the
    # score could not fall. That is how Azumo reported "no gaps".
    "ai_governance": 0.95,
    "grc": 0.85,
    "risk_management": 0.80,
    "compliance": 0.85,
    "offensive_security": 0.80,
    "vulnerability_management": 0.80,
    "ai_security": 0.60,
    "customer_security": 0.55,
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
    # ── AI governance ──────────────────────────────────────────────────────
    # A governance vacancy is written in the language of control, not of tools.
    # Without these terms the parser saw only "AWS" and "APIs" and produced a
    # DevOps CV for an AI-governance role.
    "ai governance": ("ai_governance", "concept"),
    "gobierno de ia": ("ai_governance", "concept"),
    "gobernanza de ia": ("ai_governance", "concept"),
    "modelo de gobernanza": ("ai_governance", "concept"),
    "marco de gobierno": ("ai_governance", "concept"),
    "responsible ai": ("ai_governance", "concept"),
    "ia responsable": ("ai_governance", "concept"),
    "guardrails": ("ai_governance", "concept"),
    "casos de uso de ia": ("ai_governance", "concept"),
    "sistemas agenticos": ("ai_governance", "concept"),
    "agentic systems": ("ai_governance", "concept"),
    "copilotos": ("ai_governance", "concept"),
    "copilots": ("ai_governance", "concept"),
    "trazabilidad": ("ai_governance", "concept"),
    "traceability": ("ai_governance", "concept"),
    "auditabilidad": ("ai_governance", "concept"),
    "auditability": ("ai_governance", "concept"),
    "evidencia auditable": ("ai_governance", "concept"),
    "aprobacion de casos de uso": ("ai_governance", "concept"),
    "clasificacion de riesgo": ("risk_management", "concept"),
    "genai": ("ai_governance", "concept"),
    "generative ai": ("ai_governance", "concept"),
    "ia generativa": ("ai_governance", "concept"),
    "llms": ("ai_governance", "concept"),
    "llm": ("ai_governance", "concept"),
    # ── specific frameworks. NEVER satisfiable by generic governance work ───
    "nist ai rmf": ("ai_governance", "tool"),
    "iso/iec 42001": ("ai_governance", "tool"),
    "iso 42001": ("ai_governance", "tool"),
    "eu ai act": ("ai_governance", "tool"),
    "owasp llm top 10": ("ai_governance", "tool"),
    "nist csf": ("security_governance", "tool"),
    # ── GRC / risk ─────────────────────────────────────────────────────────
    "grc": ("grc", "concept"),
    "control interno": ("grc", "concept"),
    "internal control": ("grc", "concept"),
    "segregacion de funciones": ("grc", "concept"),
    "segregation of duties": ("grc", "concept"),
    "matriz de riesgo": ("risk_management", "concept"),
    "risk matrix": ("risk_management", "concept"),
    "gestion de riesgos": ("risk_management", "concept"),
    "riesgo tecnologico": ("risk_management", "concept"),
    "auditoria it": ("grc", "concept"),
    "auditoria": ("grc", "concept"),
    "politicas": ("grc", "concept"),
    "estandares": ("grc", "concept"),
    "procedimientos": ("grc", "concept"),
    "cumplimiento regulatorio": ("compliance", "concept"),
    "regulatory compliance": ("compliance", "concept"),
    "entornos regulados": ("domain", "concept"),
    "regulated environments": ("domain", "concept"),
    "fintech": ("domain", "concept"),
    "medios de pago": ("domain", "concept"),
    "servicios financieros": ("domain", "concept"),
    "financial services": ("domain", "concept"),
    "privacidad": ("compliance", "concept"),
    "proteccion de datos": ("compliance", "concept"),
    "data privacy": ("compliance", "concept"),
    # ── regulators / certifications (specific, never inferred) ─────────────
    "bcra": ("compliance", "tool"),
    "bcu": ("compliance", "tool"),
    "sbs": ("compliance", "tool"),
    "cmf": ("compliance", "tool"),
    "cism": ("certification", "tool"),
    "cissp": ("certification", "tool"),
    "crisc": ("certification", "tool"),
    "aws security": ("certification", "tool"),
    # ── compliance / audit frameworks ──────────────────────────────────────
    "soc 2": ("compliance", "tool"),
    "soc2": ("compliance", "tool"),
    "pci dss": ("compliance", "tool"),
    "pci-dss": ("compliance", "tool"),
    "iso 27001": ("compliance", "tool"),
    "hipaa": ("compliance", "tool"),
    "gdpr": ("compliance", "tool"),
    "nist": ("compliance", "tool"),
    "audit preparation": ("compliance", "concept"),
    "evidence collection": ("compliance", "concept"),
    "risk assessment": ("compliance", "concept"),
    "control validation": ("compliance", "concept"),
    # ── offensive security ─────────────────────────────────────────────────
    "penetration testing": ("offensive_security", "concept"),
    "pentesting": ("offensive_security", "concept"),
    "offensive security": ("offensive_security", "concept"),
    "adversarial testing": ("offensive_security", "concept"),
    "red team": ("offensive_security", "concept"),
    "threat modeling": ("offensive_security", "concept"),
    "burp suite": ("offensive_security", "tool"),
    "metasploit": ("offensive_security", "tool"),
    "nmap": ("offensive_security", "tool"),
    # ── vulnerability management ───────────────────────────────────────────
    "vulnerability management": ("vulnerability_management", "concept"),
    "vulnerability scanning": ("vulnerability_management", "concept"),
    "vulnerability remediation": ("vulnerability_management", "concept"),
    "patch management": ("vulnerability_management", "concept"),
    "dependency scanning": ("vulnerability_management", "concept"),
    "snyk": ("vulnerability_management", "tool"),
    "qualys": ("vulnerability_management", "tool"),
    "nessus": ("vulnerability_management", "tool"),
    "trivy": ("vulnerability_management", "tool"),
    # ── AI security ────────────────────────────────────────────────────────
    "prompt injection": ("ai_security", "concept"),
    "adversarial inputs": ("ai_security", "concept"),
    "guardrails": ("ai_security", "concept"),
    "ai security": ("ai_security", "concept"),
    "data leakage": ("ai_security", "concept"),
    "responsible ai": ("ai_security", "concept"),
    # ── customer-facing security ───────────────────────────────────────────
    "due diligence": ("customer_security", "concept"),
    "security questionnaire": ("customer_security", "concept"),
    "security questionnaires": ("customer_security", "concept"),
    "vendor risk": ("customer_security", "concept"),
    "rfp": ("customer_security", "concept"),
    # ── security operations ────────────────────────────────────────────────
    "incident response": ("observability", "concept"),
    "root cause analysis": ("observability", "concept"),
    "siem": ("security_governance", "tool"),
    "wazuh": ("security_governance", "tool"),
    "secrets management": ("security_governance", "concept"),
    "access control": ("identity", "concept"),
    "least privilege": ("identity", "concept"),
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
    "soc2": "soc 2",
    "pci-dss": "pci dss",
    "pentesting": "penetration testing",
    "security questionnaires": "security questionnaire",
    "gobierno de ia": "ai governance",
    "gobernanza de ia": "ai governance",
    "modelo de gobernanza": "ai governance",
    "marco de gobierno": "ai governance",
    "ia responsable": "responsible ai",
    "responsible ai": "ai governance",
    "ia generativa": "generative ai",
    "genai": "generative ai",
    "llms": "llm",
    "sistemas agenticos": "agentic systems",
    "copilotos": "copilots",
    "trazabilidad": "traceability",
    "auditabilidad": "auditability",
    "evidencia auditable": "auditability",
    "riesgo tecnologico": "gestion de riesgos",
    "iso/iec 42001": "iso 42001",
    "internal control": "control interno",
    "segregation of duties": "segregacion de funciones",
    "risk matrix": "matriz de riesgo",
    "regulated environments": "entornos regulados",
    "financial services": "servicios financieros",
    "regulatory compliance": "cumplimiento regulatorio",
    "data privacy": "proteccion de datos",
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


# --------------------------------------------------------------------------- #
# JD CLASSIFICATION — which discipline is this vacancy, and which are secondary.
#
# The title decides the family. Body keywords only decide the SECONDARY domains.
# That ordering is the fix for the Azumo regression: a DevSecOps vacancy at an
# AI company mentions AI in half its bullets, so body-keyword scoring ranked
# `ai_systems` first and the CV headline became "Lead AI Systems". AI security
# is a dimension of that role, not the profession.
# --------------------------------------------------------------------------- #
FAMILY_TITLE_PATTERNS: list[tuple[str, str]] = [
    ("devsecops", r"devsecops|sec\s*dev\s*ops|security engineer|security solutions|"
                  r"application security|appsec|product security|security lead"),
    ("cloud_security", r"cloud security|security architect|infrastructure security"),
    ("sre", r"\bsre\b|site reliability|reliability engineer"),
    ("platform_engineering", r"platform engineer|infrastructure engineer|"
                             r"developer platform"),
    ("devops", r"devops|cloud engineer|cloud infrastructure"),
    ("data_platforms", r"data engineer|data platform"),
    ("ai_systems", r"\bai engineer|ml engineer|machine learning engineer|"
                   r"llm engineer|agentic engineer"),
    ("backend_engineering", r"backend engineer|back-end engineer|software engineer"),
]

# Secondary domains, detected in the body. These describe WHAT the role covers.
DOMAIN_PATTERNS: list[tuple[str, str]] = [
    ("cloud_security", r"cloud security|secure .{0,20}cloud|harden .{0,20}cloud"),
    ("application_security", r"application security|appsec|secure development|"
                             r"sast|sca|secure coding|api security"),
    ("infrastructure_security", r"infrastructure security|system hardening|"
                                r"harden(ing)? (?:cloud )?infrastructure"),
    ("vulnerability_management", r"vulnerabilit|remediat|patch management"),
    ("offensive_security", r"penetration testing|pentest|offensive security|"
                           r"adversarial testing|red team"),
    ("incident_response", r"incident response|incident|root cause analysis|"
                          r"threat detection"),
    ("ai_security", r"ai security|prompt injection|adversarial input|"
                    r"ai[- ]driven system|ai system|guardrail"),
    ("compliance", r"soc 2|soc2|pci dss|pci-dss|compliance|audit|regulator|"
                   r"risk assessment"),
    ("customer_security", r"due diligence|security questionnaire|\brfp\b|"
                          r"vendor risk|customer .{0,15}security"),
    ("identity", r"identity|access control|\biam\b|\brbac\b|sso"),
    ("observability", r"monitoring|logging|alerting|observability"),
    ("containers", r"kubernetes|\baks\b|\beks\b|docker|container"),
    ("iac", r"terraform|infrastructure as code|\biac\b|bicep|ansible"),
    ("cicd", r"ci/cd|cicd|pipeline|github actions|azure devops|jenkins|gitlab"),
    ("cloud_platform", r"\baws\b|\bazure\b|\bgcp\b|google cloud"),
]


@dataclass
class JobSpec:
    raw_title: str = ""
    title: str = ""            # noise-stripped role phrase
    seniority: Optional[str] = None
    requirements: list[Requirement] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)
    # Classification. `target_role` is what the VACANCY is called and is never
    # used as the candidate headline — see headline.resolve_candidate_headline.
    target_role: str = ""
    primary_family: str = ""
    secondary_domains: list[str] = field(default_factory=list)
    seniority_requested: list[str] = field(default_factory=list)
    admission: Optional["Admission"] = None
    # WHERE the vacancy's weight actually sits. Distinct from `primary_family`,
    # which is the professional base of the role. A DevSecOps engineer applying
    # to an AI-governance post keeps the base and shifts the centre: the CV must
    # foreground governance evidence without claiming a different profession.
    center_of_gravity: dict = field(default_factory=dict)

    def primary_domain(self) -> str:
        return self.center_of_gravity.get("primary_domain", "")

    def domain_rank(self, domain: str) -> float:
        """1.0 primary, 0.7 secondary, 0.4 supporting, 0.1 otherwise."""
        cog = self.center_of_gravity
        if domain == cog.get("primary_domain"):
            return 1.0
        if domain in cog.get("secondary_domains", []):
            return 0.7
        if domain in cog.get("supporting_domains", []):
            return 0.4
        return 0.1

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


# Portal boilerplate that occupies the first line and is not the role.
_TITLE_BOILERPLATE = re.compile(
    r"^(descripcion (?:completa )?(?:del )?(?:empleo|puesto|trabajo)|"
    r"job description|about the job|about the role|descripcion general|"
    r"detalles del empleo|job details|acerca del empleo|oferta de empleo)\b", re.I)

# "Azumo is looking for a highly technical and hands-on DevSecOps Engineer /
#  Security Solutions Lead to own and strengthen..." — the role lives mid
# sentence. Capture the noun phrase ending in a role noun.
_ROLE_NOUN = (r"engineer|developer|architect|consultant|specialist|analyst|"
              r"administrator|manager|lead|scientist|designer|technician")
# Spanish postings often state the role as a mission line ("Tu misión:
# Liderar el marco de gobierno de Inteligencia Artificial...") with no title
# anywhere. Recognising the mission verb lets the report name the role instead
# of quoting the company's marketing paragraph.
_MISSION = re.compile(
    r"(?:tu misi[oó]n|your mission|el rol|the role|objetivo del puesto)\s*[:\-]?\s*"
    r"([A-ZÁÉÍÓÚÑ][^.\n]{10,110})", re.I)

_LOOKING_FOR = re.compile(
    r"(?:looking for|hiring|seeking|in search of|buscamos|estamos buscando|"
    r"nos encontramos buscando)\s+(?:an?\s+|una?\s+)?"
    r"(?:[\w\-]+(?:\s+[\w\-]+){0,4}?\s+)??"
    r"([A-Z][\w&/\-\.]*(?:\s+[\w&/\-\.]+){0,6}?"
    rf"\s*(?:{_ROLE_NOUN})\b(?:\s*/\s*[\w&/\-\. ]{{0,40}}?(?:{_ROLE_NOUN})\b)?)",
    re.I)


def _extract_title(jd_text: str) -> tuple[str, str]:
    """Best-effort vacancy title.

    Skips portal boilerplate ("Descripción completa del empleo") — taking it
    literally is how the Azumo posting reported its target_role as that phrase
    instead of "DevSecOps Engineer / Security Solutions Lead".
    """
    lines = [l.strip().strip("#*_ ").strip() for l in jd_text.splitlines()]
    lines = [l for l in lines if l]

    for line in lines[:6]:
        m = re.match(r"(?:role|title|position|puesto|rol|cargo)\s*[:\-]\s*(.+)",
                     line, re.I)
        if m:
            return m.group(1).strip(), clean_title(m.group(1))

    # A "we are looking for <ROLE>" sentence beats a bare first line, because a
    # posting that opens with boilerplate still names the role in prose.
    m = _LOOKING_FOR.search(" ".join(lines[:12]))
    if m:
        # "highly technical and hands-on DevSecOps Engineer" -> the role starts
        # at the first capitalised token; everything before it is adjectives.
        words = m.group(1).split()
        while len(words) > 2 and words[0][:1].islower():
            words.pop(0)
        raw = " ".join(words).strip()
        return raw, clean_title(raw)

    m = _MISSION.search("\n".join(lines[:40]))
    if m:
        raw = " ".join(m.group(1).split())
        return raw, clean_title(raw)

    for line in lines[:6]:
        if _TITLE_BOILERPLATE.match(line):
            continue
        return line, clean_title(line)
    return (lines[0], clean_title(lines[0])) if lines else ("", "")


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
    classify_jd(jd_text, spec)
    spec.center_of_gravity = compute_center_of_gravity(spec)
    spec.admission = classify_admission(jd_text, len(spec.requirements))
    return spec


def requirement_label(req: Requirement) -> str:
    """Human-facing name for reports/Telegram (title-cased, acronyms intact)."""
    if req.term.isupper() or len(req.term) <= 4:
        return req.term.upper() if req.term.replace("-", "").isalpha() and len(
            req.term) <= 4 else req.term
    return " ".join(w if w.isupper() else w.capitalize() for w in req.term.split())


def iter_terms(reqs: Iterable[Requirement]) -> list[str]:
    return [r.term for r in reqs]


# --------------------------------------------------------------------------- #
# ADMISSION — is this text a job description at all?
# --------------------------------------------------------------------------- #
# The most damaging defect in production was that ANY Telegram message in the CV
# topic was treated as a JD. "No me des respuestas a menos que te las pida"
# (44 chars) became a job description, parsed to zero requirements, and surfaced
# as "CV rechazado por quality gate: JD_PARSED". A 70-char complaint
# ("me respondiste con uno de Lead Ai system??") parsed to ONE requirement,
# scored 100% with no gaps, and delivered a PDF.
#
# A job description has shape: it is long, it enumerates requirements, and it
# uses recruiting vocabulary. A control message does none of those.

CONTROL_PATTERNS = (
    r"^\s*no me (?:des|generes|mandes|contestes|respondas)",
    r"^\s*no (?:generes|hagas|mandes)\b",
    r"^\s*(?:dale|ok|okay|listo|gracias|perfecto|bien|si|no|yes)\s*[.!]?\s*$",
    r"solo ayudame a responder",
    r"no me generes cv",
    r"me respondiste",
    r"te ped[ií]\b",
    r"^\s*(?:pero|entonces|por que|porque)\b.{0,120}\?\s*$",
)

# Vocabulary that only appears in an actual posting.
JD_MARKERS = (
    "responsibilities", "responsabilidades", "requirements", "requisitos",
    "qualifications", "calificaciones", "what you", "we are looking",
    "estamos buscando", "buscamos", "nice to have", "deseable", "deseables",
    "years of experience", "anos de experiencia", "about the role",
    "descripcion del puesto", "descripcion completa del empleo",
    "job description", "key responsibilities", "beneficios", "benefits",
    "we offer", "ofrecemos", "location:", "full-time", "part-time",
    "seniority", "role overview", "who you are", "your profile",
)

MIN_JD_CHARS = 350
MIN_JD_REQUIREMENTS = 6
MIN_JD_MARKERS = 2


@dataclass
class Admission:
    accepted: bool
    reason: str
    signals: dict = field(default_factory=dict)
    kind: str = "unknown"     # job_description | control | conversation | too_short


def classify_admission(jd_text: str, requirement_count: int) -> Admission:
    """Decide whether `jd_text` is a job description worth generating a CV from."""
    raw = (jd_text or "").strip()
    norm = normalize(raw)
    signals = {
        "chars": len(raw),
        "lines": len([l for l in raw.splitlines() if l.strip()]),
        "requirements": requirement_count,
        "markers": sorted({m for m in JD_MARKERS if m in norm}),
    }

    for pattern in CONTROL_PATTERNS:
        if re.search(pattern, norm):
            return Admission(False, "control message, not a job description",
                             signals, "control")

    if len(raw) < MIN_JD_CHARS:
        return Admission(
            False,
            f"too short to be a job description: {len(raw)} chars < {MIN_JD_CHARS}",
            signals, "too_short")

    if len(signals["markers"]) < MIN_JD_MARKERS:
        return Admission(
            False,
            "no job-posting vocabulary found (needs at least "
            f"{MIN_JD_MARKERS} of responsibilities/requirements/qualifications/...)",
            signals, "conversation")

    if requirement_count < MIN_JD_REQUIREMENTS:
        return Admission(
            False,
            f"only {requirement_count} requirement(s) extracted "
            f"(< {MIN_JD_REQUIREMENTS}); a real posting enumerates more",
            signals, "conversation")

    return Admission(True, "job description", signals, "job_description")


def classify_jd(jd_text: str, spec: "JobSpec") -> None:
    """Fill target_role / primary_family / secondary_domains on `spec`."""
    title_norm = normalize(spec.title or spec.raw_title)
    body_norm = normalize(jd_text)

    spec.target_role = (spec.title or spec.raw_title).strip()

    # 1) Family from the TITLE. First match wins; the list is ordered so a
    #    security title outranks a generic engineering one.
    family = ""
    for name, pattern in FAMILY_TITLE_PATTERNS:
        if re.search(pattern, title_norm):
            family = name
            break
    # 2) Only if the title says nothing, fall back to the body.
    if not family:
        for name, pattern in FAMILY_TITLE_PATTERNS:
            if re.search(pattern, body_norm):
                family = name
                break
    spec.primary_family = family or "devops"

    # 3) Secondary domains from the body, ordered by first appearance.
    found: list[tuple[int, str]] = []
    for name, pattern in DOMAIN_PATTERNS:
        m = re.search(pattern, body_norm)
        if m and name != spec.primary_family:
            found.append((m.start(), name))
    spec.secondary_domains = [n for _, n in sorted(found)]

    # 4) Every seniority token the vacancy asks for. Recorded, never granted.
    asked: list[str] = []
    scope = f"{title_norm} {normalize(spec.raw_title)}"
    for token, level in _SENIORITY_TOKENS:
        if token in scope and level not in asked:
            asked.append(level)
    spec.seniority_requested = asked


# --------------------------------------------------------------------------- #
# CENTRE OF GRAVITY
# --------------------------------------------------------------------------- #
# Dimensions that describe a PROFESSION rather than a subject area. They can be
# supporting evidence for any vacancy, so they never become the centre unless
# nothing else does.
_BASE_DIMENSIONS = frozenset({
    "cicd", "containers", "iac", "cloud_platform", "os", "scripting",
    "observability", "data", "other",
})


def compute_center_of_gravity(spec: "JobSpec") -> dict:
    """Rank the JD's dimensions by the weight they actually carry.

    Mass is (requirement weight x how often the JD says it), so a vacancy that
    mentions governance in every bullet and AWS once lands on governance. The
    professional-base dimensions are held back from the primary slot: a CV must
    not decide it is an "AWS vacancy" because a governance role happens to run
    on AWS.
    """
    mass: dict[str, float] = {}
    for req in spec.requirements:
        weight = req.weight * (1 + 0.4 * min(req.mentions, 6))
        mass[req.dimension] = mass.get(req.dimension, 0.0) + weight
    if not mass:
        return {"primary_domain": "", "secondary_domains": [],
                "supporting_domains": [], "mass": {}}

    total = sum(mass.values()) or 1.0
    shares = {d: round(m / total, 4) for d, m in mass.items()}
    ordered = sorted(shares.items(), key=lambda kv: -kv[1])

    subject = [d for d, _ in ordered if d not in _BASE_DIMENSIONS]
    primary = subject[0] if subject else ordered[0][0]

    secondary, supporting = [], []
    for dim, share in ordered:
        if dim == primary:
            continue
        if dim in _BASE_DIMENSIONS:
            # Carries real weight (an Azure role really is about the cloud
            # platform) → secondary. Merely present → supporting.
            (secondary if share >= 0.10 else supporting).append(dim)
        elif share >= 0.04:
            secondary.append(dim)
        else:
            supporting.append(dim)

    return {"primary_domain": primary,
            "secondary_domains": secondary,
            "supporting_domains": supporting,
            "mass": shares}
