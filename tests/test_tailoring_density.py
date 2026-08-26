"""TAILORING DENSITY — does the CV foreground what the vacancy is about?

Selecting the right experiences is not enough. A CV for an AI-governance role
that spends its skills block on CloudFront, DataPower, WebSphere and
BrowserStack is worse than one that omits them, even though every line is true.

These tests check semantic PRECEDENCE, not exact positions: a fragile test that
pins index 0 breaks on every legitimate re-ranking and teaches nothing.
"""
from __future__ import annotations

import pytest

from conftest import DATA_DIR, REPO_ROOT

from resume_as_code.jdspec import normalize, parse_jd, term_present
from resume_as_code.loader import load_bundle
from resume_as_code.pipeline import run_pipeline
from resume_as_code.validate import extract_text

JOBS = {
    "prex": "prex-ai-governance.txt",
    "azure": "logicalis-azure.txt",
    "devsecops": "azumo-devsecops.txt",
}


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(DATA_DIR)


def _run(bundle, name, tmp_path_factory):
    out = tmp_path_factory.mktemp(name)
    jd = (REPO_ROOT / "jobs" / JOBS[name]).read_text(encoding="utf-8")
    return run_pipeline(bundle, jd, job_name=name.title(), out_dir=out,
                        stem=f"Nestor_Fleitas_{name.title()}",
                        formats=["pdf", "docx", "txt"], data_dir=DATA_DIR, ask=None)


@pytest.fixture(scope="module")
def prex(bundle, tmp_path_factory):
    return _run(bundle, "prex", tmp_path_factory)


@pytest.fixture(scope="module")
def azure(bundle, tmp_path_factory):
    return _run(bundle, "azure", tmp_path_factory)


@pytest.fixture(scope="module")
def devsecops(bundle, tmp_path_factory):
    return _run(bundle, "devsecops", tmp_path_factory)


def group_index(result, name: str) -> int:
    for i, g in enumerate(result.resume.skill_groups):
        if g.name == name:
            return i
    return 10_000


def groups(result) -> list[str]:
    return [g.name for g in result.resume.skill_groups]


# --------------------------------------------------------------------------- #
# Centre of gravity
# --------------------------------------------------------------------------- #
def test_prex_centre_is_ai_governance(prex):
    cog = prex.spec.center_of_gravity
    assert cog["primary_domain"] == "ai_governance"
    for domain in ("grc", "compliance", "risk_management"):
        assert domain in cog["secondary_domains"], cog["secondary_domains"]
    # The professional base is still DevSecOps: the centre moved, the identity
    # did not.
    assert prex.spec.primary_family in ("devops", "devsecops")


def test_centre_of_gravity_differs_per_vacancy(prex, azure, devsecops):
    centres = {
        "prex": prex.spec.center_of_gravity["primary_domain"],
        "azure": azure.spec.center_of_gravity["primary_domain"],
        "devsecops": devsecops.spec.center_of_gravity["primary_domain"],
    }
    assert centres["prex"] == "ai_governance"
    assert centres["azure"] in ("networking", "cloud_platform", "iac")
    assert centres["devsecops"] in ("security_governance", "compliance",
                                    "offensive_security")
    assert len(set(centres.values())) >= 2, centres


# --------------------------------------------------------------------------- #
# Relevance density — the point of the whole exercise
# --------------------------------------------------------------------------- #
IRRELEVANT_FOR_GOVERNANCE = ("Integration & Middleware", "Data Engineering",
                             "Quality & Testing", "AWS Services")


def test_governance_vacancy_foregrounds_governance(prex):
    names = groups(prex)
    assert group_index(prex, "AI Systems & Governance") < \
        group_index(prex, "Cloud Platforms"), names
    assert group_index(prex, "DevSecOps & Application Security") < \
        group_index(prex, "Cloud Platforms"), names


def test_governance_vacancy_drops_the_noise(prex):
    """WebSphere, DataPower, Airflow and BrowserStack are true and irrelevant."""
    names = groups(prex)
    for noise in IRRELEVANT_FOR_GOVERNANCE:
        assert noise not in names, f"{noise} survived onto an AI-governance CV"
    # Scoped to the skills block on purpose. Dropping a skill from Core Skills
    # must not rewrite canonical bullet text: Equifax really did run "(Tomcat,
    # WebSphere)", and Airflow really is a Udemy course. Those are facts in
    # their own sections; what the tailoring controls is what gets ADVERTISED.
    rendered = normalize(extract_text(prex.artifacts["pdf"]))
    block = rendered.split("core skills")[-1].split("professional experience")[0]
    for skill in ("websphere", "datapower", "browserstack", "oracle service bus",
                  "cloudfront", "amazon rds"):
        assert not term_present(skill, block), f"{skill} advertised in Core Skills"


def test_governance_skills_outrank_legacy_stack(prex):
    ranking = prex.debug["skillRanking"]
    governance = ["AI Governance", "Auditability", "Policy Enforcement",
                  "Agentic Systems"]
    legacy = ["IBM WebSphere", "IBM DataPower", "Oracle Service Bus",
              "BrowserStack", "Apache Airflow"]
    worst_governance = min(ranking[s]["score"] for s in governance if s in ranking)
    best_legacy = max(ranking[s]["score"] for s in legacy if s in ranking)
    assert worst_governance > best_legacy, (
        f"legacy stack outranked governance evidence: "
        f"{worst_governance} vs {best_legacy}")


# --------------------------------------------------------------------------- #
# Evidence ordering follows the vacancy
# --------------------------------------------------------------------------- #
def test_allianz_leads_with_ai_governance_on_a_governance_vacancy(prex):
    allianz = next(e for e in prex.resume.experiences
                   if e.company == "Allianz Argentina")
    assert allianz.bullets, "Allianz rendered with no bullets"
    first = normalize(allianz.bullets[0])
    assert term_present("ai governance", first) or term_present("generative-ai", first), \
        f"Allianz opened with: {allianz.bullets[0]}"


def test_nexusos_is_promoted_on_a_governance_vacancy(prex):
    assert prex.resume.featured_projects, "NexusOS absent from an AI-governance CV"
    project = prex.resume.featured_projects[0]
    assert 3 <= len(project.bullets) <= 4, len(project.bullets)
    rendered = normalize(extract_text(prex.artifacts["pdf"]))
    assert term_present("nexusos", rendered)
    for claim in ("capability-based authorization", "prompt-injection",
                  "human-in-the-loop"):
        assert term_present(claim, rendered), f"NexusOS evidence missing: {claim}"


def test_banking_evidence_surfaces_without_claiming_grc(prex):
    summary = normalize(prex.resume.summary)
    assert term_present("regulated banking and financial-services environments",
                        summary), prex.resume.summary
    # BANKING_DOMAIN_EVIDENCE is not FORMAL_GRC_EVIDENCE.
    rendered = normalize(extract_text(prex.artifacts["pdf"]))
    for inflated in ("grc", "regulatory specialist", "compliance lead",
                     "regulatory audit", "bcra", "bcu"):
        assert not term_present(inflated, rendered), f"inflated claim: {inflated}"


# --------------------------------------------------------------------------- #
# Named frameworks never arrive by semantic similarity
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("framework", [
    "nist ai rmf", "iso 42001", "eu ai act", "owasp llm top 10", "nist csf",
    "iso 27001", "cism", "cissp", "crisc",
])
def test_named_frameworks_stay_gaps(prex, framework):
    match = prex.matrix.by_term(framework)
    assert match is not None, f"{framework} not extracted from the JD"
    assert match.is_gap, (
        f"{framework} resolved to {match.can_claim} via {match.evidence} — "
        "generic governance work is not experience with a named standard")
    rendered = normalize(extract_text(prex.artifacts["pdf"]))
    assert not term_present(framework, rendered)


def test_match_reflects_the_missing_frameworks(prex):
    assert prex.coverage < 0.75, prex.coverage
    counts = prex.debug["matchBreakdown"]["counts"]
    assert counts.get("unsupported", 0) + counts.get("unknown", 0) >= 5, counts
    recomputed = sum(r["score_contribution"]
                     for r in prex.debug["matchBreakdown"]["requirements"]
                     if r["inScore"])
    assert abs(recomputed - prex.coverage) < 1e-4


# --------------------------------------------------------------------------- #
# The same tailor, four vacancies — nothing else regresses
# --------------------------------------------------------------------------- #
def test_azure_vacancy_still_foregrounds_azure(azure):
    names = groups(azure)
    cloud = next(g for g in azure.resume.skill_groups if g.name == "Cloud Platforms")
    assert cloud.items[0] == "Azure", cloud.items
    for infra in ("Cloud Platforms", "Infrastructure as Code"):
        assert group_index(azure, infra) < group_index(azure, "Programming & Scripting"), names
    assert "Azure" in azure.resume.tagline or "Azure" in azure.resume.headline


def test_devsecops_vacancy_still_foregrounds_security(devsecops):
    names = groups(devsecops)
    assert group_index(devsecops, "DevSecOps & Application Security") < \
        group_index(devsecops, "Cloud Platforms"), names
    assert "DevSecOps" in devsecops.resume.headline


@pytest.mark.parametrize("fixture_name", ["prex", "azure", "devsecops"])
def test_identity_never_copies_the_vacancy(request, fixture_name):
    result = request.getfixturevalue(fixture_name)
    headline = result.resume.headline
    assert "DevSecOps" in headline or "DevOps" in headline, headline
    for banned in ("Lead", "Head", "Manager", "Director", "Principal"):
        assert banned not in headline, headline
    assert normalize(headline) != normalize(result.spec.target_role)


@pytest.mark.parametrize("fixture_name", ["prex", "azure", "devsecops"])
def test_final_artifacts_pass_factcheck(request, fixture_name):
    result = request.getfixturevalue(fixture_name)
    assert result.artifact_findings == [], result.artifact_findings
    failed = [g.as_dict() for g in result.pipeline_gates if not g.ok]
    assert not failed, failed
    assert result.pages is not None and result.pages <= 2


@pytest.mark.parametrize("fixture_name", ["prex", "azure", "devsecops"])
def test_docx_matches_the_pdf_semantically(request, fixture_name):
    """The DOCX is a separate renderer; it must carry the same claims."""
    from docx import Document
    result = request.getfixturevalue(fixture_name)
    pdf = normalize(extract_text(result.artifacts["pdf"]))
    docx = normalize("\n".join(p.text for p in
                               Document(result.artifacts["docx"]).paragraphs))
    assert term_present(result.resume.headline.split()[0], docx)
    for group in result.resume.skill_groups[:2]:
        for skill in group.items[:3]:
            assert term_present(skill, docx), f"{skill} missing from the DOCX"
            assert term_present(skill, pdf), f"{skill} missing from the PDF"
