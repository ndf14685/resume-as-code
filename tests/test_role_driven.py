"""Role-driven tailoring + hybrid semantic planner (LLM proposes, deterministic
authority validates). Same canonical dataset, notably different CV per role."""
import pathlib

import pytest

from resume_as_code.compose import compose_plan
from resume_as_code.evidence import EvidenceAuthority
from resume_as_code.loader import load_bundle
from resume_as_code.roleintent import infer_role_intent, validate_intent_schema
from resume_as_code.semantic_planner import plan
from resume_as_code.tailor import build_from_plan

DATA = str(pathlib.Path(__file__).resolve().parent.parent / "data")

JDS = {
    "software-engineer-ai": "Role: **Software Engineer - AI**",
    "ai-architect": "AI Systems Architect — governance for autonomous multi-agent systems.",
    "platform": "Platform Engineer — Kubernetes, Terraform, developer platforms, runtime.",
    "devops": "Senior DevOps Engineer. Kubernetes, Terraform, CI/CD, GitOps, observability.",
    "devsecops": "DevSecOps Engineer — secure SDLC, SAST, SCA, pipeline security.",
    "appsec": "Application Security Engineer — SAST, DAST, OWASP, secure coding, threat modeling.",
    "cloud-security": "Cloud Security Engineer — IAM, CSPM, Kubernetes security, secrets management.",
    "backend": "Backend Engineer: Java, APIs, microservices, distributed systems, PostgreSQL.",
}


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(DATA)


# ── ROLE_INTENT ──────────────────────────────────────────────────────────────
def test_role_intent_from_title_only_is_swe_and_ai():
    ri = infer_role_intent("Role: **Software Engineer - AI**", years_experience=11)
    assert "software_engineering" in ri.role_weights
    assert "ai_systems" in ri.role_weights
    assert ri.primary_role in {"software_engineering", "ai_systems"}
    # NOT the old hardcoded ai-architect default
    assert ri.primary_role != "ai_architect"


def test_role_intent_discriminates_across_families():
    assert infer_role_intent(JDS["devops"]).primary_role == "devops"
    assert infer_role_intent(JDS["appsec"]).primary_role == "application_security"
    assert infer_role_intent(JDS["backend"]).primary_role == "backend_engineering"
    assert infer_role_intent(JDS["ai-architect"]).primary_role == "ai_architect"


def test_intent_schema_rejects_invented_family():
    assert validate_intent_schema({"role_weights": {"quantum_wizard": 1.0}}) is None
    ok = validate_intent_schema({"role_weights": {"software_engineering": 0.6,
                                                  "ai_systems": 0.4}})
    assert ok is not None and ok.source == "llm+validated"


# ── Hybrid planner: LLM proposes, deterministic validates ────────────────────
def test_planner_deterministic_fallback_without_llm(bundle):
    r = plan(JDS["software-engineer-ai"], bundle, ask=None)
    assert r.fallback_used is True and r.provider == "deterministic"
    assert r.intent.primary_role in {"software_engineering", "ai_systems"}


def test_planner_rejects_invented_family_and_falls_back(bundle):
    ask = lambda _p: '{"primary_role":"wizard","role_weights":{"wizard":1.0}}'
    r = plan(JDS["software-engineer-ai"], bundle, ask=ask)
    assert r.fallback_used is True                      # invented family rejected
    assert r.intent.source == "deterministic"


def test_planner_rejects_unsupported_semantic_match(bundle):
    ask = lambda _p: (
        '{"primary_role":"ai_systems","role_weights":{"ai_systems":0.6,'
        '"software_engineering":0.4},"seniority":"senior",'
        '"semantic_matches":['
        '  {"jd_term":"LLM orchestration","candidate_evidence":["LLM Provider Routing"]},'
        '  {"jd_term":"fine-tuning","candidate_evidence":["PyTorch","CUDA"]}]}'
    )
    r = plan(JDS["software-engineer-ai"], bundle, ask=ask)
    assert r.fallback_used is False and r.intent.source == "llm+validated"
    accepted = {m["jd_term"] for m in r.semantic_matches}
    assert "LLM orchestration" in accepted            # cited evidence exists
    assert "fine-tuning" in r.rejected_claims          # PyTorch/CUDA not evidenced


def test_evidence_authority_rejects_reframing_without_evidence(bundle):
    auth = EvidenceAuthority(bundle)
    good = auth.validate_reframing("…", ["LLM Provider Routing", "Multi-Agent Systems"])
    bad = auth.validate_reframing("…", ["PyTorch", "fine-tuning"])
    assert good.supported is True
    assert bad.supported is False


# ── Role-driven CVs are notably different ────────────────────────────────────
def _resume_for(bundle, key):
    ri = infer_role_intent(JDS[key], years_experience=11)
    comp = compose_plan(bundle, ri)
    return build_from_plan(bundle, comp)


def test_each_role_produces_a_distinct_cv(bundle):
    resumes = {k: _resume_for(bundle, k) for k in JDS}
    headlines = {k: r.headline for k, r in resumes.items()}
    # headlines are not all identical (true tailoring, not "one base + title")
    assert len(set(headlines.values())) >= 5
    # software-engineer-ai leads with programming skills, devops with CI/CD
    swe_top = resumes["software-engineer-ai"].skill_groups[0].name
    devops_top = resumes["devops"].skill_groups[0].name
    assert swe_top != devops_top
    assert "Programming" in swe_top or "AI" in swe_top


def test_swe_ai_headline_is_software_engineer_not_architect(bundle):
    r = _resume_for(bundle, "software-engineer-ai")
    assert "software engineer" in r.headline.lower()
    assert "architect" not in r.headline.lower()


def test_swe_ai_expands_software_engineering_history(bundle):
    r = _resume_for(bundle, "software-engineer-ai")
    # the older Java/SOA roles must be visible (not all collapsed)
    java_roles = [e for e in r.experiences
                  if any(t in e.title.lower() for t in ("java", "soa"))]
    assert any(not e.condensed and e.bullets for e in java_roles)


# ── Truth invariants ─────────────────────────────────────────────────────────
def test_no_unsupported_skill_leaks(bundle):
    deny = ["pytorch", "tensorflow", "cuda", "fine-tuning", "vector database"]
    for key in JDS:
        r = _resume_for(bundle, key)
        blob = " ".join(g.name + " " + " ".join(g.items)
                        for g in r.skill_groups).lower()
        for term in deny:
            assert term not in blob, f"{term} leaked for {key}"


def test_chronology_always_has_company_title_dates(bundle):
    for key in JDS:
        r = _resume_for(bundle, key)
        for e in r.experiences:
            assert e.company and e.title and e.meta_right


def test_recruiter_subject_not_used_as_specialization(bundle):
    """Phase 17: a recruiter subject line must not become the professional title
    or a fake specialization ('specializing in US Global')."""
    jd = ("AI Security Architect Opportunity - US Global Semiconductor Leader "
          "Enterprise - 100% Remote\nHi Nestor, we have a role...")
    ri = infer_role_intent(jd, years_experience=11)
    assert ri.job_title == "AI Security Architect"
    comp = compose_plan(bundle, ri)
    for noise in ("us global", "opportunity", "semiconductor", "100%",
                  "enterprise", "specializing in us"):
        assert noise not in comp.headline.lower(), noise
        assert noise not in comp.summary.lower(), noise
