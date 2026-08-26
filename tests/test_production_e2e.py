"""PRODUCTION E2E — the 2026-08-26 incident, pinned.

Few tests, each one guarding a behaviour a user actually saw go wrong. They run
the real pipeline against the real Azumo posting and inspect the RENDERED PDF,
not the internal model: the whole point of the incident was that internal
objects looked fine while the delivered document did not.

Reproduced from production artifacts in /home/ndf/.openclaw/media/nexus-resume:
  resume_10010  Azumo JD          → headline "Lead AI Systems"
  resume_10013  "No me des..."    → 0 requirements, "quality gate: JD_PARSED"
  resume_10020  a complaint       → 1 requirement, 100% match, no gaps, PDF sent
"""
from __future__ import annotations

import pathlib

import pytest

from conftest import DATA_DIR, REPO_ROOT

from resume_as_code.factcheck import _cv_body_text, scan_rendered_artifact
from resume_as_code.headline import forbidden_claim_terms, resolve_candidate_headline
from resume_as_code.inventory import build_inventory
from resume_as_code.jdspec import normalize, parse_jd, term_present
from resume_as_code.loader import load_bundle
from resume_as_code.matching import build_match_matrix, match_breakdown
from resume_as_code.pipeline import run_pipeline
from resume_as_code.validate import extract_text

AZUMO = REPO_ROOT / "jobs" / "azumo-devsecops.txt"

# Mandatory asks the candidate cannot evidence. None may become a claim.
AZUMO_GAPS = ("soc 2", "pci dss", "penetration testing", "offensive security",
              "vulnerability management")


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(DATA_DIR)


@pytest.fixture(scope="module")
def azumo_text():
    assert AZUMO.exists(), f"missing regression JD: {AZUMO}"
    return AZUMO.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def azumo(bundle, azumo_text, tmp_path_factory):
    out = tmp_path_factory.mktemp("azumo")
    return run_pipeline(bundle, azumo_text, job_name="Azumo", out_dir=out,
                        stem="Nestor_Fleitas_Azumo",
                        formats=["pdf", "docx", "txt"], data_dir=DATA_DIR, ask=None)


# --------------------------------------------------------------------------- #
# TEST 1 — Azumo E2E: classified right, headline right, artifacts real
# --------------------------------------------------------------------------- #
def test_azumo_end_to_end(azumo):
    assert azumo.admitted
    assert azumo.spec.primary_family == "devsecops"
    assert "DevSecOps Engineer" in azumo.spec.target_role

    headline = azumo.resume.headline
    assert "Lead" not in headline, f"JD seniority leaked into the headline: {headline}"
    assert headline != "Lead AI Systems"
    assert "DevSecOps" in headline

    for kind in ("pdf", "docx"):
        path = pathlib.Path(azumo.artifacts[kind])
        assert path.exists() and path.stat().st_size > 4000, f"{kind} not rendered"
    assert azumo.pages == 2


# --------------------------------------------------------------------------- #
# TEST 2 — the vacancy's title is never the candidate's
# --------------------------------------------------------------------------- #
def test_jd_title_is_never_copied_into_the_headline(bundle):
    jd = ("Principal Kubernetes Security Architect\n"
          "About the role: we are looking for a Principal Kubernetes Security "
          "Architect to lead our platform security.\n"
          "Key responsibilities: design Kubernetes security architecture, run "
          "CI/CD security gates, remediate vulnerabilities, harden clusters.\n"
          "Requirements: 10+ years of experience with Kubernetes, Terraform, AWS, "
          "container security and incident response.\n"
          "Qualifications: secure development practices. Nice to have: SOC 2.\n"
          "Location: Remote. Benefits: full-time.\n")
    spec = parse_jd(jd)
    inventory = build_inventory(bundle)
    matrix = build_match_matrix(spec, bundle, inventory)
    resolved = resolve_candidate_headline(spec=spec, inventory=inventory,
                                          matrix=matrix, bundle=bundle)
    assert resolved.headline != "Principal Kubernetes Security Architect"
    assert "principal" not in resolved.headline.lower()
    assert "architect" not in resolved.headline.lower()
    assert resolved.rejected, "refusing the JD seniority must be recorded"


# --------------------------------------------------------------------------- #
# TEST 3 — AI security is a dimension, not the profession
# --------------------------------------------------------------------------- #
def test_ai_security_is_secondary_not_primary(azumo):
    assert azumo.spec.primary_family == "devsecops"
    assert "ai_security" in azumo.spec.secondary_domains
    assert "AI" not in azumo.resume.headline.split("|")[0], \
        "AI must not be the profession in the headline noun"


# --------------------------------------------------------------------------- #
# TEST 4 — the match score cannot be 100% with mandatory asks unmet
# --------------------------------------------------------------------------- #
def test_match_is_not_inflated(azumo):
    assert azumo.coverage < 1.0, "100% while mandatory requirements are unmet"
    assert 0.15 < azumo.coverage < 0.75, azumo.coverage
    breakdown = match_breakdown(azumo.matrix)
    counts = breakdown["counts"]
    assert counts.get("supported", 0) > 0
    assert (counts.get("unsupported", 0) + counts.get("unknown", 0)) > 0, \
        "a JD this demanding cannot be fully covered"
    recomputed = sum(r["score_contribution"] for r in breakdown["requirements"]
                     if r["inScore"])
    assert abs(recomputed - azumo.coverage) < 1e-4


# --------------------------------------------------------------------------- #
# TEST 5 — gaps survive as gaps
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("term", AZUMO_GAPS)
def test_mandatory_gaps_are_preserved(azumo, term):
    match = azumo.matrix.by_term(term)
    assert match is not None, f"{term!r} was not extracted from the JD"
    assert match.is_gap, f"{term!r} resolved to {match.can_claim} via {match.evidence}"


def test_gaps_are_reported_to_the_user(azumo):
    labels = " ".join(azumo.gap_labels(limit=12)).lower()
    assert "soc 2" in labels or "compliance" in labels
    assert azumo.gap_labels(), "a partially matched JD must report gaps"


# --------------------------------------------------------------------------- #
# TEST 7 — control messages never produce a CV
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    "No me des respuestas a menos que te las pida",
    "Es un puesto de devsecops y me respondiste con uno de Lead Ai system??",
    "no me generes cv ahora, solo ayudame a responder este mensaje del recruiter",
    "gracias",
    "Hi Nestor! Hope you are doing great! I am Lari, a recruiter at Revelo.",
])
def test_non_vacancies_are_refused_without_generating(bundle, text, tmp_path):
    result = run_pipeline(bundle, text, job_name="X", out_dir=tmp_path, stem="x",
                          formats=["pdf", "docx"], data_dir=DATA_DIR, ask=None,
                          write_debug_artifacts=False)
    assert not result.admitted, f"treated as a job description: {text!r}"
    assert not result.passed
    assert result.written == [], "a refused input rendered artifacts"
    assert list(tmp_path.iterdir()) == [], "a refused input wrote files"
    assert result.refusal
    gate = result.pipeline_gates[0]
    assert gate.name == "G1_JD_VALID" and gate.recoverable


# --------------------------------------------------------------------------- #
# TEST 8 — the final gate blocks a claim injected after composition
# --------------------------------------------------------------------------- #
def test_final_artifact_factcheck_blocks_injected_claims(azumo, bundle, tmp_path):
    """The renderer is a layer of its own; validating the model is not enough."""
    from resume_as_code.render_pdf import render_pdf

    tampered = azumo.resume.model_copy(deep=True)
    tampered.headline = "Lead DevSecOps Engineer"
    tampered.summary = ("CISSP-certified security lead with SOC 2 expert "
                        "experience and hands-on penetration testing.")
    pdf = render_pdf(tampered, tmp_path / "tampered.pdf")

    findings = scan_rendered_artifact(
        pdf, forbidden_terms=forbidden_claim_terms(azumo.inventory, azumo.matrix),
        target_role=azumo.spec.target_role, headline=tampered.headline,
        granted_seniority=azumo.headline_audit.get("seniority"))
    found = {f.term for f in findings}
    assert "lead" in found, "an invented seniority reached the PDF unblocked"
    assert "cissp" in found, "an invented certification reached the PDF unblocked"
    assert "soc 2" in found and "penetration testing" in found

    kinds = {f.kind for f in findings}
    assert {"seniority", "credential", "experience"} <= kinds


def test_clean_artifact_has_no_findings(azumo):
    assert azumo.artifact_findings == [], azumo.artifact_findings
    assert all(g.ok for g in azumo.pipeline_gates), \
        [g.as_dict() for g in azumo.pipeline_gates if not g.ok]


# --------------------------------------------------------------------------- #
# TEST 9 — inspect the PDF the user receives, not the JSON
# --------------------------------------------------------------------------- #
def test_rendered_pdf_content(azumo):
    text = normalize(extract_text(azumo.artifacts["pdf"]))

    assert term_present("devsecops engineer", text), "headline missing from the PDF"
    assert not term_present("matched to this search", text)
    assert not term_present("the job description", text)
    assert not term_present("lead ai systems", text)

    for banned in ("cissp", "cism", "soc 2", "pci dss", "penetration testing",
                   "offensive security", "veeam", "entra id"):
        assert not term_present(banned, text), f"unsupported claim in the PDF: {banned}"

    # Real experience is present and correctly attributed.
    for company in ("allianz", "banco itau", "ingenia", "equifax"):
        assert term_present(company, text), f"{company} missing from the PDF"


def test_years_claim_is_discipline_honest(azumo):
    summary = normalize(azumo.resume.summary)
    if term_present("13+ years", summary) or "years" in summary:
        assert not any(
            term_present(f"years {d}", summary) or term_present(f"{d} for 13", summary)
            for d in ("devsecops", "of devsecops", "in devsecops")), summary
        assert term_present("years in technology", summary), \
            f"an unqualified years claim: {azumo.resume.summary}"


# --------------------------------------------------------------------------- #
# Observability — a generation must be explainable after the fact
# --------------------------------------------------------------------------- #
def test_generation_is_traceable(azumo):
    debug = azumo.debug
    classification = debug["classification"]
    assert classification["primaryFamily"] == "devsecops"
    assert classification["targetRole"]
    assert "lead" in classification["seniorityRequestedByJD"], \
        "the JD's seniority ask must be recorded even though it is refused"

    audit = debug["headline"]
    full = f"{azumo.resume.headline} | {azumo.resume.tagline}".strip(" |")
    assert audit["candidateHeadline"] == full
    assert audit["headlineSource"] and audit["evidence"]
    assert audit["rejectedFromJD"], "refusing 'Lead' must be auditable"

    assert [g["gate"] for g in debug["pipelineGates"]][:2] == \
        ["G1_JD_VALID", "G2_EVIDENCE_VALID"]
