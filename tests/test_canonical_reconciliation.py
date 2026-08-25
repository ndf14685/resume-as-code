"""Pins the CV -> canonical reconciliation of 2026-08-25.

These are facts and modelling rules, not behaviour: each test fails if a value
the candidate confirmed drifts, if the four kinds of credential start converting
into one another, or if "we have no information" is silently downgraded into
"the candidate does not have it".
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from conftest import DATA_DIR, REPO_ROOT

from resume_as_code.inventory import build_inventory
from resume_as_code.jdspec import normalize, parse_jd, term_present
from resume_as_code.loader import DataError, load_bundle, _check_credential_types
from resume_as_code.matching import (CAN_CLAIM_NO, CAN_CLAIM_UNKNOWN,
                                     CAN_CLAIM_YES, build_match_matrix,
                                     match_breakdown)

RECONCILIATION_DOC = (REPO_ROOT / "docs" / "data-reconciliation"
                      / "CV_CANONICAL_RECONCILIATION.md")


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(DATA_DIR)


def _exp(bundle, exp_id):
    return next(e for e in bundle.experiences if e.id == exp_id)


# --------------------------------------------------------------------------- #
# 1. Telecom exact dates
# --------------------------------------------------------------------------- #
def test_telecom_dates_are_exact(bundle):
    lead = _exp(bundle, "telecom_lead")
    support = _exp(bundle, "telecom_support")
    assert lead.start == "2018-03"
    assert support.end == "2022-12"
    # The CV shows Telecom as 03/2018 - 12/2022; these are the span endpoints.
    assert (lead.start, support.end) == ("2018-03", "2022-12")


def test_no_experience_carries_an_approximation_marker(bundle):
    approximate = [e.id for e in bundle.experiences if e.approximate_start]
    assert not approximate, f"approximate dates on: {approximate}"


def test_no_rendered_date_is_marked_approximate(bundle):
    for exp in bundle.experiences:
        assert "~" not in exp.date_range(), f"{exp.id}: {exp.date_range()}"


def test_telecom_is_two_engagements_not_one(bundle):
    """The CV renders Telecom as one 03/2018-12/2022 block. The candidate
    confirmed on 2026-08-25 that it is really two engagements, so the split is
    canonical and must never be collapsed back into a single record."""
    lead = _exp(bundle, "telecom_lead")
    support = _exp(bundle, "telecom_support")
    assert "SHAPE CONFIRMED" in (lead.note or ""),         "the confirmed two-block shape is not recorded"
    assert "CONFLICT" not in (lead.note or ""),         "the Telecom conflict was resolved; the note still calls it open"
    assert lead.engagement == "full_time"
    assert support.engagement == "part_time"
    assert (lead.start, lead.end) == ("2018-03", "2020-02")
    assert (support.start, support.end) == ("2020-02", "2022-12")
    telecom = [e for e in bundle.experiences if e.company == "Telecom"]
    assert len(telecom) == 2, f"Telecom collapsed into {len(telecom)} record(s)"


# --------------------------------------------------------------------------- #
# 2. Banco Pichincha
# --------------------------------------------------------------------------- #
def test_pichincha_stack_and_engagement(bundle):
    pichincha = _exp(bundle, "pichincha")
    assert pichincha.engagement == "freelance"
    assert pichincha.industry == "Banking"
    for skill in ("Azure", "Terraform", "Red Hat OpenShift"):
        assert skill in pichincha.skills, f"{skill} missing from Pichincha"
    text = normalize(" ".join(b.text for b in pichincha.bullets))
    for phrase in ("azure", "terraform", "openshift 4", "cncf", "ci/cd"):
        assert term_present(phrase, text), f"Pichincha bullets never mention {phrase!r}"


def test_pichincha_claims_no_undocumented_azure_specifics(bundle):
    """Azure + Terraform + OpenShift is what was confirmed. Nothing more."""
    text = normalize(" ".join(b.text for b in _exp(bundle, "pichincha").bullets))
    for invented in ("landing zone", "entra id", "expressroute", "vnet",
                     "private endpoint", "conditional access", "veeam"):
        assert not term_present(invented, text), f"Pichincha invents {invented!r}"


# --------------------------------------------------------------------------- #
# 3-5. The three Azure engagements stay semantically separate
# --------------------------------------------------------------------------- #
def test_fluxit_azure_devops_and_mobile_scope(bundle):
    fluxit = _exp(bundle, "fluxit")
    assert fluxit.client == "La Nación"
    for skill in ("Azure", "Azure DevOps", "Docker", "Jenkins", "SonarQube",
                  "BrowserStack", "AWS", "GCP"):
        assert skill in fluxit.skills, f"{skill} missing from Flux IT"
    text = normalize(" ".join(b.text for b in fluxit.bullets))
    assert term_present("azure devops", text)
    assert term_present("android", text) and term_present("ios", text)


def test_fluxit_does_not_imply_azure_networking_or_landing_zones(bundle):
    text = normalize(" ".join(b.text for b in _exp(bundle, "fluxit").bullets))
    for invented in ("landing zone", "vnet", "network security group",
                     "expressroute", "private endpoint", "entra id"):
        assert not term_present(invented, text), f"Flux IT implies {invented!r}"


def test_mercantil_andina_evidence(bundle):
    bullets = [b.text for b in _exp(bundle, "ingenia").bullets
               if "Mercantil Andina" in b.text]
    assert bullets, "Mercantil Andina has no dedicated bullet"
    joined = normalize(" ".join(bullets))
    for phrase in ("azure", "aks", "terraform", "azure devops", "bitbucket"):
        assert term_present(phrase, joined), f"Mercantil Andina missing {phrase!r}"


def test_geopagos_does_not_inherit_azure_from_mercantil(bundle):
    """One employer, two clients: the separation lives at bullet level."""
    ingenia = _exp(bundle, "ingenia")
    geopagos = normalize(" ".join(b.text for b in ingenia.bullets
                                  if "Geopagos" in b.text))
    assert geopagos, "Geopagos has no dedicated bullet"
    for azure_term in ("azure", "aks", "azure devops", "bitbucket"):
        assert not term_present(azure_term, geopagos), (
            f"Geopagos inherited {azure_term!r} from the Mercantil Andina work")
    for own in ("aws", "kubernetes", "terraform", "airflow", "gitlab"):
        assert term_present(own, geopagos), f"Geopagos lost its own {own!r}"

    mercantil = normalize(" ".join(b.text for b in ingenia.bullets
                                   if "Mercantil Andina" in b.text))
    for geopagos_only in ("gitlab", "airflow"):
        assert not term_present(geopagos_only, mercantil), (
            f"Mercantil Andina claims {geopagos_only!r}, which is Geopagos evidence")


# --------------------------------------------------------------------------- #
# 6. Training / certifications load without converting between types
# --------------------------------------------------------------------------- #
EXPECTED_TRAINING = {
    ("Cisco Networking Academy", "CyberOps"): (2024, "training"),
    ("Hackademy", "Red Team"): (2025, "training"),
    ("Hackademy", "OSINT"): (2024, "training"),
    ("Hackademy", "DevSecOps & Cloud Security"): (2022, "training"),
    ("Hackademy", "Fundamentals of Hacking and Defense"): (2022, "training"),
    ("MundoSE", "Data Science"): (2023, "training"),
    ("MundoSE", "DevOps"): (2022, "training"),
    ("Udemy", "Docker"): (None, "course"),
    ("Udemy", "Kubernetes"): (None, "course"),
    ("Udemy", "Airflow"): (None, "course"),
    ("Educación IT", "Linux"): (None, "course"),
    ("Educación IT", "Java"): (None, "course"),
}


def test_every_training_item_is_recorded_with_its_type(bundle):
    found = {}
    for group in bundle.training:
        for item in group["items"]:
            found[(group["provider"], item["name"])] = (item.get("year"),
                                                        item.get("type"))
    assert found == EXPECTED_TRAINING


def test_training_never_holds_a_credential_type(bundle):
    for group in bundle.training:
        for item in group["items"]:
            assert item["type"] in ("training", "course"), (
                f"{group['provider']}/{item['name']} is typed {item['type']!r}")


def test_unconfirmed_credentials_are_recorded_but_not_claimable(bundle):
    names = {c["name"] for c in bundle.certifications_all}
    assert {"AWS Certified Cloud Practitioner", "Cisco Networking Academy"} <= names
    for cert in bundle.certifications_all:
        assert cert["status"] in ("confirmed", "needs_confirmation", "expired")
        assert cert["type"] in ("certification", "badge")
    # Only confirmed credentials reach the renderable/claimable list.
    assert all(c.get("status") == "confirmed" for c in bundle.certifications)
    assert not bundle.certifications, (
        "a certification became claimable without a confirmed issuing record")


def test_expired_certification_is_kept_but_never_claimed(bundle):
    """The candidate holds an expired AWS Cloud Practitioner. Losing the record
    would be forgetting a fact; rendering it would be claiming a live
    credential. Neither is acceptable, so it is kept and not claimable."""
    aws = next(c for c in bundle.certifications_all
               if c["name"] == "AWS Certified Cloud Practitioner")
    assert aws["status"] == "expired"
    assert aws["issuer"] == "Amazon Web Services"
    assert "issued" not in aws and "expires" not in aws,         "no date was supplied; none may be invented"
    assert aws not in bundle.certifications


def test_loader_rejects_dates_on_an_expired_credential():
    with pytest.raises(DataError, match="expired entries carry no"):
        _check_credential_types([{"name": "X", "issuer": "Y",
                                  "type": "certification", "status": "expired",
                                  "issued": "2021-01"}], [])


def test_cisco_badge_stays_unconfirmed(bundle):
    """Deliberately left incomplete by the candidate on 2026-08-25."""
    cisco = next(c for c in bundle.certifications_all
                 if c["name"] == "Cisco Networking Academy")
    assert cisco["type"] == "badge"
    assert cisco["status"] == "needs_confirmation"
    assert cisco not in bundle.certifications


def test_loader_rejects_a_type_conversion():
    with pytest.raises(DataError, match="belongs in data/certifications.yaml"):
        _check_credential_types([], [{"provider": "Udemy",
                                      "items": [{"name": "Docker",
                                                 "type": "certification"}]}])
    with pytest.raises(DataError, match="type must be one of"):
        _check_credential_types([{"name": "X", "type": "diploma",
                                  "status": "confirmed", "issuer": "Y"}], [])
    with pytest.raises(DataError, match="status must be one of"):
        _check_credential_types([{"name": "X", "type": "certification",
                                  "status": "probably"}], [])


def test_unconfirmed_credential_never_reaches_a_rendered_cv():
    from resume_as_code.pipeline import run_pipeline
    import tempfile
    bundle = load_bundle(DATA_DIR)
    jd = (REPO_ROOT / "jobs" / "logicalis-azure.txt").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_pipeline(bundle, jd, job_name="Cred", out_dir=Path(tmp),
                              stem="cred", formats=["txt"], data_dir=DATA_DIR,
                              ask=None, write_debug_artifacts=False)
    from resume_as_code.factcheck import _cv_body_text
    body = normalize(_cv_body_text(result.resume))
    assert not term_present("cloud practitioner", body)
    assert not term_present("needs_confirmation", body)


# --------------------------------------------------------------------------- #
# 3 (spec) — education stays separate and unknown
# --------------------------------------------------------------------------- #
def test_education_is_unknown_not_invented(bundle):
    assert bundle.education == []
    raw = yaml.safe_load((DATA_DIR / "education.yaml").read_text(encoding="utf-8"))
    assert raw.get("education_status") == "unknown_incomplete", (
        "education must be explicitly marked unknown, not silently empty")


def test_training_is_not_promoted_into_education(bundle):
    """A DevOps course is not a degree."""
    assert bundle.training, "training should exist"
    assert bundle.education == [], "education was inferred from training"


# --------------------------------------------------------------------------- #
# 7 + 10. UNKNOWN is not UNSUPPORTED
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def azure_matrix(bundle):
    jd = (REPO_ROOT / "jobs" / "logicalis-azure.txt").read_text(encoding="utf-8")
    return build_match_matrix(parse_jd(jd), bundle, build_inventory(bundle))


def test_certifications_are_unknown_while_the_source_is_incomplete(azure_matrix):
    """AZ-104/AZ-305/VMCE are gaps, but we may not assert he lacks them: the
    certification source holds only needs_confirmation entries."""
    for cert in ("az-104", "az-305", "vmce"):
        match = azure_matrix.by_term(cert)
        assert match is not None, f"{cert} was not extracted from the JD"
        assert match.can_claim == CAN_CLAIM_UNKNOWN, (
            f"{cert} reported {match.can_claim}; an incomplete source cannot "
            "support a negative claim")
        assert match.is_gap, f"{cert} must still be reported as a gap"


def test_backup_dimension_is_unknown_because_nothing_covers_it(azure_matrix):
    for term in ("veeam", "backup", "disaster recovery"):
        match = azure_matrix.by_term(term)
        assert match is not None and match.can_claim == CAN_CLAIM_UNKNOWN, (
            f"{term}: no backup/DR evidence exists in any source, so the honest "
            "verdict is UNKNOWN")


def test_windows_server_is_unsupported_not_unknown(azure_matrix):
    """The OS dimension IS evidenced (Linux), so its absence is informative."""
    match = azure_matrix.by_term("windows server")
    assert match is not None and match.can_claim == CAN_CLAIM_NO
    assert azure_matrix.by_term("linux").can_claim == CAN_CLAIM_YES


def test_azure_specific_products_are_unsupported(azure_matrix):
    """Cloud platform and identity ARE evidenced; these products are not."""
    for term in ("azure landing zones", "entra id", "conditional access",
                 "expressroute", "private endpoints"):
        match = azure_matrix.by_term(term)
        assert match is not None, f"{term} not extracted"
        assert match.can_claim in (CAN_CLAIM_NO, CAN_CLAIM_UNKNOWN)
        assert match.is_gap


def test_unknown_and_unsupported_are_disjoint(azure_matrix):
    unknown = {m.term for m in azure_matrix.unknowns()}
    unsupported = {m.term for m in azure_matrix.unsupported()}
    assert not (unknown & unsupported)
    assert unknown and unsupported, "the two buckets must both be exercised"
    assert unknown | unsupported == {m.term for m in azure_matrix.gaps()}


def test_routing_no_longer_matches_llm_provider_routing(azure_matrix):
    """Regression for the collision the score audit exposed: the JD's network
    routing requirement was satisfied by the NexusOS `LLM Provider Routing`
    skill."""
    match = azure_matrix.by_term("routing")
    assert match is not None
    assert match.can_claim != CAN_CLAIM_YES, (
        f"network routing matched {match.evidence} via {match.source}")


# --------------------------------------------------------------------------- #
# 9. The score is reconstructible from the breakdown
# --------------------------------------------------------------------------- #
def test_match_score_is_reconstructible_from_breakdown(azure_matrix):
    breakdown = match_breakdown(azure_matrix)
    recomputed = sum(r["score_contribution"] for r in breakdown["requirements"]
                     if r["inScore"])
    reported = azure_matrix.coverage(musts_only=True)
    assert abs(recomputed - reported) < 1e-4, (
        f"breakdown sums to {recomputed}, matrix reports {reported}")
    assert abs(breakdown["recomputedScore"] - breakdown["reportedScore"]) < 1e-4


def test_breakdown_accounts_for_every_requirement(azure_matrix):
    breakdown = match_breakdown(azure_matrix)
    assert len(breakdown["requirements"]) == len(azure_matrix.matches)
    assert sum(breakdown["counts"].values()) == len(azure_matrix.matches)
    for row in breakdown["requirements"]:
        assert row["status"] in ("supported", "partial", "unsupported", "unknown")
        assert row["reason"], f"{row['requirement']} has no reason recorded"


def test_dimension_contributions_sum_to_the_score(azure_matrix):
    breakdown = match_breakdown(azure_matrix)
    total = sum(d["contribution"] for d in breakdown["dimensions"])
    assert abs(total - breakdown["reportedScore"]) < 1e-4


def test_unknown_requirements_stay_in_the_denominator(azure_matrix):
    """Dropping them would re-create the defect that reported 100%."""
    breakdown = match_breakdown(azure_matrix)
    unknown_rows = [r for r in breakdown["requirements"]
                    if r["status"] == "unknown" and r["inScore"]]
    assert unknown_rows, "no in-scope UNKNOWN requirement to check"
    assert all(r["credit"] == 0.0 for r in unknown_rows)
    dims = {r["dimension"] for r in unknown_rows}
    counted = {d["dimension"] for d in breakdown["dimensions"]}
    assert dims <= counted, "an UNKNOWN dimension vanished from the denominator"


# --------------------------------------------------------------------------- #
# 11. The reconciliation report exists and is auditable
# --------------------------------------------------------------------------- #
def test_reconciliation_report_exists_and_is_structured():
    assert RECONCILIATION_DOC.exists(), f"missing: {RECONCILIATION_DOC}"
    text = RECONCILIATION_DOC.read_text(encoding="utf-8")
    for action in ("CONFIRMED", "CORRECTED", "ADDED", "NEEDS_CONFIRMATION",
                   "UNCHANGED", "CONFLICT"):
        assert action in text, f"reconciliation report never uses {action}"
    for column in ("Canonical before", "Canonical after", "Action",
                   "Evidence", "Confidence"):
        assert column in text, f"reconciliation report lacks a {column} column"


def test_reconciliation_report_covers_every_changed_record():
    text = RECONCILIATION_DOC.read_text(encoding="utf-8")
    for subject in ("telecom_lead", "telecom_support", "pichincha", "fluxit",
                    "ingenia", "AWS Certified Cloud Practitioner",
                    "Cisco Networking Academy", "Education"):
        assert subject in text, f"reconciliation report omits {subject}"


def test_resolved_and_open_items_are_both_recorded():
    text = RECONCILIATION_DOC.read_text(encoding="utf-8")
    assert "Resolved by the candidate" in text
    assert "Open items" in text
    # Resolved on 2026-08-25: the two-block Telecom shape, and the expired AWS
    # certification. Deliberately left open: the Cisco badge and Education.
    assert re.search(r"Telecom shape.*RESOLVED", text, re.S | re.I)
    assert re.search(r"AWS Certified Cloud Practitioner.*expired", text, re.S | re.I)
