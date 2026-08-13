"""Regression: Senior Sales Support Engineer JD (Telegram msg 8242, 2026-08-13).

The production run classified this JD as plain `devops` because (a) the closed
role catalogue could not express sales-support/data-platform semantics and
(b) the semantic planner never ran (ask=None). These tests pin the EXPECTED
SEMANTIC BEHAVIOR — not hardcoded weights: the intent must express the
customer-facing/POC dimension, the truth boundary must hold (no invented
Snowflake/Informatica/etc.), and JD terms without evidence must surface as
transferable-or-gap instead of disappearing.
"""
from pathlib import Path

from resume_as_code.compose import compose_plan
from resume_as_code.jobmatch import analyze_job
from resume_as_code.loader import load_bundle
from resume_as_code.roleintent import (
    ALLOWED_FAMILIES,
    infer_role_intent,
    validate_intent_schema,
)
from resume_as_code.semantic_planner import plan

FIXTURE = Path(__file__).parent / "fixtures" / "jd_sales_support_cloud.txt"
DATA = Path(__file__).parent.parent / "data"

JD = FIXTURE.read_text(encoding="utf-8")

# Truth boundary: none of these may appear as claimed skills — no canonical
# evidence exists (Ansible/Airflow DO have evidence and are legitimately claimable).
FORBIDDEN_CLAIMS = ("Snowflake", "Databricks", "Informatica", "Apache Kafka",
                    "Apache Spark", "AWS Glue", "FinOps")


def test_catalogue_can_express_sales_support_and_data_platforms():
    assert "presales_sales_support" in ALLOWED_FAMILIES
    assert "data_platforms" in ALLOWED_FAMILIES
    assert "finops" in ALLOWED_FAMILIES


def test_deterministic_intent_expresses_the_jd_not_just_devops():
    intent = infer_role_intent(JD, years_experience=13)
    w = intent.role_weights
    # cloud/devops es legítimamente dominante (el título de la JD lo dice)…
    assert w.get("devops", 0) > 0.1
    # …pero la dimensión sales-support/POC/demo DEBE estar expresada
    assert w.get("presales_sales_support", 0) > 0.08, w
    # y data platforms (Snowflake/Databricks/Kafka/Spark/Airflow) también
    assert w.get("data_platforms", 0) > 0.03, w
    # el mix no puede ser ~monofamilia devops
    assert w.get("devops", 0) < 0.6, w
    assert intent.seniority == "senior"


def test_llm_proposal_with_new_families_validates():
    proposal = {
        "primary_role": "presales_sales_support",
        "role_weights": {"presales_sales_support": 0.3, "devops": 0.3,
                         "platform_engineering": 0.2, "data_platforms": 0.1,
                         "sre": 0.1},
        "seniority": "senior",
        "reasoning_tags": ["poc", "workshops", "sales enablement"],
    }
    intent = validate_intent_schema(proposal)
    assert intent is not None and intent.source == "llm+validated"
    assert intent.primary_role == "presales_sales_support"


def test_llm_invented_family_still_rejected():
    assert validate_intent_schema(
        {"role_weights": {"sales_wizard": 1.0}}) is None


def test_planner_keeps_jd_title_when_llm_omits_it():
    bundle = load_bundle(DATA)
    stub = lambda prompt: (  # noqa: E731
        '{"primary_role": "presales_sales_support", '
        '"role_weights": {"presales_sales_support": 0.4, "devops": 0.4, '
        '"data_platforms": 0.2}, "seniority": "senior"}')
    result = plan(JD, bundle, ask=stub)
    assert result.fallback_used is False
    assert result.intent.source == "llm+validated"
    # el título del JD no se pierde cuando el LLM no lo propone
    assert result.intent.job_title  # heredado del análisis determinista


def test_headline_and_summary_not_generic_devops():
    bundle = load_bundle(DATA)
    intent = infer_role_intent(JD, years_experience=13)
    comp = compose_plan(bundle, intent)
    # headline anclado al título real de la JD (Cloud Engineer/DevOps es fiel)
    assert "cloud" in comp.headline.lower()
    # la narrativa debe reflejar la dimensión customer-facing con evidencia
    # real (enablement/training/client engagements existen en experience.yaml)
    text = (comp.summary + " " + comp.tagline).lower()
    assert any(t in text for t in ("enablement", "client engagement",
                                   "stakeholder", "customer")), (
        comp.summary, comp.tagline)


def test_truth_boundary_gap_analysis_surfaces_unevidenced_jd_terms():
    bundle = load_bundle(DATA)
    analysis = analyze_job(bundle, JD)
    surfaced = {t["term"].lower() for t in analysis.transferable} | \
               {g.lower() for g in analysis.gaps}
    for term in ("snowflake", "databricks", "informatica"):
        assert term in surfaced, (term, surfaced)
    # y ninguno de ellos aparece como skill cubierta/claimed
    covered = {s.lower() for s in analysis.matched_skills}
    for term in ("snowflake", "databricks", "informatica", "finops"):
        assert term not in covered


def test_generated_resume_never_claims_forbidden_skills(tmp_path):
    from resume_as_code.jobmatch import analyze_job as _aj
    from resume_as_code.tailor import build_from_plan

    bundle = load_bundle(DATA)
    intent = infer_role_intent(JD, years_experience=13)
    comp = compose_plan(bundle, intent)
    analysis = _aj(bundle, JD)
    resume = build_from_plan(bundle, comp, target="SalesSupport",
                             matched_skills=analysis.matched_skills,
                             extra_emphasis=analysis.extra_emphasis)
    from resume_as_code.render_txt import render_txt
    text = render_txt(resume)
    for claim in FORBIDDEN_CLAIMS:
        assert claim not in text, claim
    # Ansible y Airflow tienen evidencia canónica: reclamarlas es legítimo
    assert "Ansible" in text and "Airflow" in text


# ── regresión msg 8275: JD corta/en español no debe cratear ROLE_ALIGNMENT ──
JD_SPANISH_AGENTS = """DevOps / SRE — Agentes IA
Buscamos DevOps / SRE con experiencia en CI/CD y manejo de workloads sobre Kubernetes, con conocimiento de agent skills y comportamiento general de sistemas de IA.
Requisitos
Sólido entendimiento de procesos CI/CD
Gestión de workloads en Kubernetes
Conocimiento de agent skills y comportamientos de IA
Inglés avanzado fluido
Modalidad: contractor, USD · full remoto · Argentina"""


def test_role_alignment_not_cratered_by_language_mismatch():
    """Tags del LLM en español ("comportamiento de sistemas de IA") no pueden
    aparecer verbatim en un CV en inglés. La alineación debe medir también la
    fidelidad por FAMILIAS del intent (idioma-neutral), no solo frases exactas.
    Un CV compuesto DESDE el intent devops/sre/ai no puede alinear 0.4."""
    from resume_as_code.render_txt import render_txt
    from resume_as_code.roleintent import RoleIntent
    from resume_as_code.score import _role_alignment
    from resume_as_code.tailor import build_from_plan

    bundle = load_bundle(DATA)
    intent = RoleIntent(
        job_title="DevOps / SRE — Agentes IA", primary_role="devops",
        role_weights={"devops": 0.45, "sre": 0.3, "ai_systems": 0.15,
                      "platform_engineering": 0.1},
        category_weights={"cicd": 1.0, "cloud": 0.9, "containers": 0.8,
                          "reliability": 0.6, "ai": 0.4},
        seniority="senior",
        reasoning_tags=["CI/CD", "Kubernetes workloads", "agent skills",
                        "comportamiento de sistemas de IA"],
        source="llm+validated")
    comp = compose_plan(bundle, intent)
    resume = build_from_plan(bundle, comp, target="AgentesIA",
                             matched_skills=[], extra_emphasis=[])
    alignment = _role_alignment(render_txt(resume), intent)
    assert alignment >= 0.7, alignment


def test_role_alignment_still_low_for_true_mismatch():
    """La corrección NO relaja el gate: un CV que no expresa las familias del
    intent sigue alineando bajo."""
    from resume_as_code.roleintent import RoleIntent
    from resume_as_code.score import _role_alignment

    intent = RoleIntent(
        job_title="iOS Developer", primary_role="software_engineering",
        role_weights={"software_engineering": 1.0},
        category_weights={"languages": 1.0},
        reasoning_tags=["swift", "swiftui", "ios sdk", "app store"],
        source="deterministic")
    cv = "Marketing specialist. Social media campaigns. Brand strategy."
    assert _role_alignment(cv, intent) < 0.3
