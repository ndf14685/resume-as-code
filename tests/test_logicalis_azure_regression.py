"""REGRESSION: the Logicalis "Sr Cloud Azure Infraestructura Engineer" vacancy.

The CV the factory produced for this JD on 2026-08-25 is the reference failure.
It was a valid, ATS-clean, two-page PDF that scored 10/10 — and it was wrong in
every way that matters:

  * it reported MUST_HAVE_COVERAGE 100% while Veeam, Azure Landing Zones and
    Microsoft Entra ID had no evidence at all (the gate excluded unsupported
    requirements from its own denominator);
  * it reported "No hard gaps detected", because gap detection ran off a
    ~30-entry lexicon curated for previous vacancies;
  * it copied the vacancy's title into the candidate's headline, geography and
    all: "Argentina — Sr Cloud Azure Infraestructura Engineer", which then
    became the first word of the professional summary;
  * it gave the Azure evidence at Flux IT / La Nación a single bullet and the
    Azure/AKS evidence at INGENIA one line, while spending more space on
    AWS-only engagements;
  * and it could not surface Azure at Banco Pichincha at all, because the
    canonical record for that engagement had no stack recorded.

Every assertion below pins one of those defects. The suite is offline: no LLM,
no network — the deterministic path must produce this result on its own.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import DATA_DIR, REPO_ROOT

from resume_as_code.factcheck import run_factcheck
from resume_as_code.inventory import build_inventory
from resume_as_code.jdspec import parse_jd
from resume_as_code.loader import load_bundle
from resume_as_code.matching import (CAN_CLAIM_NO, CAN_CLAIM_UNKNOWN,
                                     CAN_CLAIM_YES, build_match_matrix)
from resume_as_code.pipeline import run_pipeline

JD_PATH = REPO_ROOT / "jobs" / "logicalis-azure.txt"

# The three engagements where Azure actually happened. The old pipeline
# surfaced one of them.
AZURE_EVIDENCE = {"pichincha", "ingenia", "fluxit"}

# Named in the JD, absent from the knowledge base. None may appear in the CV.
FABRICATION_TRAPS = ("veeam", "azure landing zones", "entra id", "expressroute",
                     "windows server", "conditional access", "bicep",
                     "private endpoints", "network security groups")


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(DATA_DIR)


@pytest.fixture(scope="module")
def jd_text():
    assert JD_PATH.exists(), f"regression JD missing: {JD_PATH}"
    return JD_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def spec(jd_text):
    return parse_jd(jd_text)


@pytest.fixture(scope="module")
def matrix(spec, bundle):
    return build_match_matrix(spec, bundle, build_inventory(bundle))


@pytest.fixture(scope="module")
def result(bundle, jd_text, tmp_path_factory):
    out = tmp_path_factory.mktemp("logicalis")
    return run_pipeline(bundle, jd_text, job_name="Logicalis", out_dir=out,
                        stem="Nestor_Fleitas_Logicalis",
                        formats=["txt", "docx", "pdf"], data_dir=DATA_DIR,
                        ask=None)


# --------------------------------------------------------------------------- #
# 1. Requirement coverage — the JD is parsed, not keyword-matched
# --------------------------------------------------------------------------- #
def test_jd_yields_a_real_requirements_model(spec):
    assert len(spec.requirements) >= 30
    assert len(spec.musts()) >= 15
    dims = spec.dimension_weights()
    for expected in ("cloud_platform", "iac", "networking", "identity",
                     "backup_dr", "containers", "cicd"):
        assert expected in dims, f"dimension not detected: {expected}"


def test_desirables_are_not_must_haves(spec):
    """The JD's 'Deseables' block must not be weighted like its requirements."""
    aks = next((r for r in spec.requirements if r.term == "aks"), None)
    assert aks is not None and aks.priority == "preferred"
    azure = next(r for r in spec.requirements if r.term == "azure")
    assert azure.priority == "must"
    assert azure.weight > aks.weight


# --------------------------------------------------------------------------- #
# 2. Evidence retrieval — all three Azure engagements are found
# --------------------------------------------------------------------------- #
def test_azure_evidence_is_retrieved_from_all_three_engagements(matrix):
    azure = matrix.by_term("azure")
    assert azure is not None
    assert azure.can_claim == CAN_CLAIM_YES
    assert AZURE_EVIDENCE <= set(azure.experience_ids), (
        f"Azure evidence not fully retrieved: {azure.experience_ids}")


def test_pichincha_carries_its_real_stack(bundle):
    """The root cause of the reference failure: this record was empty."""
    pichincha = next(e for e in bundle.experiences if e.id == "pichincha")
    assert "Azure" in pichincha.skills
    assert "Terraform" in pichincha.skills
    assert len(pichincha.bullets) >= 4


def test_azure_devops_evidence_spans_ingenia_and_fluxit(matrix):
    ado = matrix.by_term("azure devops")
    assert ado is not None and ado.can_claim == CAN_CLAIM_YES
    assert {"ingenia", "fluxit"} <= set(ado.experience_ids)


def test_terraform_evidence_is_multi_engagement(matrix):
    terraform = matrix.by_term("terraform")
    assert terraform is not None and terraform.can_claim == CAN_CLAIM_YES
    assert len(terraform.experience_ids) >= 4


# --------------------------------------------------------------------------- #
# 3. Gaps — honestly reported, never claimed
# --------------------------------------------------------------------------- #
# Every one of these is a gap; the verdict says WHY. UNSUPPORTED means the
# dimension is evidenced and this is not part of it (identity exists, Entra ID
# is not in it). UNKNOWN means no source covers the dimension at all, so a
# negative claim would be as unfounded as a positive one (nothing in the dataset
# models backup/DR or cloud networking).
GAP_VERDICTS = {
    "azure landing zones": CAN_CLAIM_NO,
    "entra id": CAN_CLAIM_NO,
    "conditional access": CAN_CLAIM_NO,
    "windows server": CAN_CLAIM_NO,
    "veeam": CAN_CLAIM_UNKNOWN,
    "expressroute": CAN_CLAIM_UNKNOWN,
}


@pytest.mark.parametrize("term,expected", sorted(GAP_VERDICTS.items()))
def test_requirement_without_evidence_is_a_gap(matrix, term, expected):
    match = matrix.by_term(term)
    assert match is not None, f"{term!r} was not even extracted from the JD"
    assert match.is_gap, f"{term!r} is claimable via {match.evidence}"
    assert match.can_claim == expected, (
        f"{term!r} resolved to {match.can_claim}, expected {expected}")


def test_gaps_are_reported_not_hidden(result):
    gaps = {m.term for m in result.matrix.gaps()}
    assert {"veeam", "azure landing zones", "entra id"} <= gaps
    labels = result.gap_labels(limit=20)
    assert labels, "no gaps surfaced for a JD the candidate only partly matches"


def test_match_score_reflects_the_real_gaps(result):
    """The old gate said 100%. A JD demanding Veeam, Landing Zones, Entra ID and
    an Azure networking stack the candidate has never touched cannot be a full
    match — and must not be reported as one."""
    assert 0.10 <= result.coverage <= 0.75, result.coverage


# --------------------------------------------------------------------------- #
# 4. No unsupported claims anywhere in the document
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("trap", FABRICATION_TRAPS)
def test_no_fabricated_technology_reaches_the_cv(result, trap):
    from resume_as_code.factcheck import _cv_body_text
    from resume_as_code.jdspec import normalize, term_present
    body = normalize(_cv_body_text(result.resume))
    assert not term_present(trap, body), f"unsupported claim rendered: {trap!r}"


def test_factual_validator_finds_nothing_to_report(result):
    assert result.report.unsupported_claims == []
    assert result.report.blocking_omissions == []


# --------------------------------------------------------------------------- #
# 5. Relevancy ranking — Azure outranks AWS-only for an Azure vacancy
# --------------------------------------------------------------------------- #
def test_azure_engagements_outrank_aws_only_ones(result):
    scores = {r.record.id: r.jd_score for r in result.ranked}
    for azure_id in ("pichincha", "ingenia"):
        assert scores[azure_id] > scores["arkho"], (
            f"{azure_id} ranked below the AWS-only ARKHO engagement")
    assert scores["pichincha"] > scores["itau"], (
        "the more recent AWS-only role outranked the Azure engagement — "
        "recency is a tiebreaker, not the ranking")


def test_every_azure_engagement_gets_real_estate(result):
    budgets = {r.record.id: r.bullet_budget for r in result.ranked}
    for exp_id in AZURE_EVIDENCE:
        assert budgets[exp_id] >= 2, (
            f"{exp_id} was reduced to {budgets[exp_id]} bullet(s) on an Azure JD")


def test_azure_bullets_are_actually_rendered(result):
    """Ranking is meaningless if the selected bullets do not carry the term."""
    from resume_as_code.jdspec import normalize, term_present
    by_company = {e.company: e for e in result.resume.experiences}
    for company in ("Banco Pichincha", "INGENIA", "Flux IT / La Nación"):
        exp = by_company[company]
        assert exp.bullets, f"{company} rendered with no bullets"
        assert any(term_present("azure", normalize(b)) for b in exp.bullets), (
            f"{company} is Azure evidence but no rendered bullet says Azure")


# --------------------------------------------------------------------------- #
# 6. Target title — composed, never copied
# --------------------------------------------------------------------------- #
def test_target_title_is_not_the_vacancy_title(result):
    from resume_as_code.jdspec import normalize
    head = normalize(result.resume.headline)
    assert head != normalize(result.spec.raw_title)
    assert "infraestructura" not in head, (
        "the vacancy's Spanish title leaked into the professional title")
    assert not head.startswith("argentina"), (
        "geography from the JD subject line leaked into the title")
    assert result.report.title_ok


def test_summary_is_built_from_evidence_not_from_the_ad(result):
    summary = result.resume.summary
    assert not summary.lower().startswith("argentina")
    assert "Azure" in summary
    # It must name where the evidence lives, not just the keyword.
    assert any(name in summary for name in
               ("Banco Pichincha", "INGENIA", "Flux IT")), summary


# --------------------------------------------------------------------------- #
# 7. Skills ordering and the two-page contract
# --------------------------------------------------------------------------- #
def test_relevant_skill_groups_lead(result):
    """Semantic precedence, not a pinned index: any re-ranking that keeps the
    infrastructure categories ahead of the peripheral ones is correct."""
    groups = [g.name for g in result.resume.skill_groups]
    cloud = next(g for g in result.resume.skill_groups
                 if g.name == "Cloud Platforms")
    assert cloud.items[0] == "Azure", cloud.items

    def rank(name):
        return groups.index(name) if name in groups else 10_000

    for infra in ("Cloud Platforms", "Infrastructure as Code"):
        assert rank(infra) < rank("Programming & Scripting"), groups
        assert rank(infra) < rank("Integration & Middleware"), groups
    assert min(rank("Cloud Platforms"), rank("Infrastructure as Code")) == 0, groups


def test_two_pages_and_all_gates_green(result):
    assert result.pages is not None and result.pages <= 2
    failures = [g.name for g in result.gates if not g.ok]
    assert not failures, f"quality gates failed: {failures}"


def test_debug_artifact_explains_every_decision(result):
    debug = result.debug
    for key in ("requirements", "evidenceInventory", "matchMatrix", "gaps",
                "ranking", "experiencesSelected", "experiencesCondensed",
                "keywords", "factualValidation", "gates", "finalVersion"):
        assert key in debug, f"debug report missing section: {key}"
    ranked_ids = {r["id"] for r in debug["ranking"]}
    assert AZURE_EVIDENCE <= ranked_ids
    for row in debug["ranking"]:
        assert row["rationale"], f"no rationale recorded for {row['id']}"


# --------------------------------------------------------------------------- #
# 8. The driver contract the NexusOS adapter depends on
# --------------------------------------------------------------------------- #
def test_cli_json_exposes_gaps_and_gates(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "resume_as_code.cli", "--data", str(DATA_DIR),
         "generate", "--auto-profile", "--jd", str(JD_PATH),
         "--job-name", "Logicalis", "--out", str(tmp_path),
         "--profiles", str(REPO_ROOT / "profiles"), "--validate", "--json"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        env={"PYTHONPATH": str(REPO_ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0, proc.stderr[-2000:]
    payload = json.loads(proc.stdout)
    assert payload["atsPassed"] is True, payload.get("atsFailures")
    assert payload["gapsNotIncluded"], "adapter would have nothing to report"
    assert payload["unsupportedClaims"] == []
    assert payload["relevantEvidenceOmitted"] == []
    assert 0 < payload["matchScore"] < 100
    assert Path(payload["debugReport"]).exists()
    names = [g["name"] for g in payload["gates"]]
    for gate in ("JD_PARSED", "CANDIDATE_EVIDENCE_LOADED", "REQUIREMENTS_MATCHED",
                 "RELEVANT_EXPERIENCE_RANKED", "NO_UNSUPPORTED_CLAIMS",
                 "NO_RELEVANT_EVIDENCE_OMITTED", "ATS_KEYWORD_COVERAGE_ACCEPTABLE",
                 "LANGUAGE_VALID", "TWO_PAGE_LIMIT", "PDF_RENDER_VALID"):
        assert gate in names, f"missing quality gate: {gate}"
