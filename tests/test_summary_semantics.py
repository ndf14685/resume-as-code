"""SUMMARY SEMANTICS — true sentences that would still mislead.

Two failure modes, neither of which a factcheck on individual claims catches:

  1. "Banco Itaú, Banco Pichincha, Equifax and 2 more" — every name true, and
     it reads as machine output. A CV names things or names a category.

  2. "Hands-on across AI Governance, Agentic Systems and LLMs, delivered at
     Allianz Argentina" — three true capabilities and one true employer,
     asserting something false: Agentic Systems is evidenced by NexusOS, not by
     Allianz. Combining true evidence from two sources produced a claim
     stronger than either source supports.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from conftest import DATA_DIR, REPO_ROOT

from resume_as_code.jdspec import normalize, term_present
from resume_as_code.loader import load_bundle
from resume_as_code.pipeline import run_pipeline
from resume_as_code.validate import extract_text

JOBS = {"prex": "prex-ai-governance.txt",
        "azure": "logicalis-azure.txt",
        "devsecops": "azumo-devsecops.txt"}

# "and 2 more", "+3 others", "y 2 más" — any count standing in for names.
AGGREGATION_RE = re.compile(
    r"\b(?:and|y|\+)\s*\d+\s*(?:more|others?|m[aá]s|otros?)\b", re.I)


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(DATA_DIR)


@pytest.fixture(scope="module", params=sorted(JOBS))
def result(request, bundle, tmp_path_factory):
    name = request.param
    out = tmp_path_factory.mktemp(f"sem-{name}")
    jd = (REPO_ROOT / "jobs" / JOBS[name]).read_text(encoding="utf-8")
    res = run_pipeline(bundle, jd, job_name=name.title(), out_dir=out,
                       stem=f"Nestor_Fleitas_{name.title()}",
                       formats=["pdf", "docx", "txt"], data_dir=DATA_DIR, ask=None)
    res.case = name
    return res


# --------------------------------------------------------------------------- #
# 1. No "and X more" — summary, bullets, caption
# --------------------------------------------------------------------------- #
def test_no_aggregation_count_anywhere_in_the_cv(result):
    assert not AGGREGATION_RE.search(result.resume.summary), result.resume.summary
    for exp in result.resume.experiences:
        for bullet in exp.bullets:
            assert not AGGREGATION_RE.search(bullet), bullet
    for project in result.resume.featured_projects:
        for bullet in project.bullets:
            assert not AGGREGATION_RE.search(bullet), bullet
    rendered = extract_text(result.artifacts["pdf"])
    assert not AGGREGATION_RE.search(rendered), \
        AGGREGATION_RE.search(rendered).group(0)


def test_gap_labels_carry_no_aggregation(result):
    """What this repo hands the delivery layer must already be clean.

    The caption is assembled downstream, but it is assembled FROM these labels,
    so the ban starts here.
    """
    for label in result.gap_labels(limit=12):
        assert not AGGREGATION_RE.search(label), label
        assert label.strip() == label and label, repr(label)


def test_no_aggregation_count_in_the_delivery_caption():
    """The Telegram caption is prose the user reads next to the document.

    The adapter lives in the idp-platform repo, so this asserts the real
    integration where both are checked out and skips where only this repo is —
    a cross-repo import must not decide whether this suite is green.
    """
    import sys
    adapter_root = "/home/ndf/workspace/idp-platform"
    if not pathlib.Path(adapter_root).is_dir():
        pytest.skip("idp-platform not checked out alongside this repo")
    sys.path.insert(0, adapter_root)
    try:
        from scripts.nexusos.adapters.resume_generate_adapter import _delivery_caption
    except ImportError as exc:
        pytest.skip(f"delivery adapter unavailable: {exc}")
    caption = _delivery_caption({
        "candidateHeadline": "Senior DevOps / DevSecOps Engineer",
        "targetRole": "AI Governance Lead",
        "matchScore": 38,
        "gapsNotIncluded": ["ISO/IEC 42001", "EU AI Act", "NIST AI RMF",
                            "OWASP LLM Top 10", "BCRA", "BCU"],
    })
    assert not AGGREGATION_RE.search(caption), caption


def test_regulated_domain_names_two_or_states_the_category(result):
    summary = result.resume.summary
    if "regulated" not in summary.lower():
        pytest.skip("this vacancy does not ask for regulated-sector evidence")
    assert "financial-services environments" in summary, summary
    named = re.findall(r"including ([^.]+)\.", summary)
    if named:
        assert named[0].count(",") <= 1, f"too many employers named: {named[0]}"


# --------------------------------------------------------------------------- #
# 2. No cross-source evidence fusion
# --------------------------------------------------------------------------- #
def _attributed_claims(summary: str) -> list[tuple[list[str], list[str]]]:
    """[(capabilities, sources)] for each clause that attributes to a source."""
    out = []
    for sentence in re.split(r"(?<=\.)\s+", summary):
        m = re.search(r"Hands-on across (.+?), delivered at (.+?)\.$", sentence)
        if m:
            caps = re.split(r",\s*|\s+and\s+", m.group(1))
            srcs = re.split(r",\s*|\s+and\s+", m.group(2))
            out.append(([c.strip() for c in caps if c.strip()],
                        [s.strip() for s in srcs if s.strip()]))
    return out


def test_every_attributed_capability_is_evidenced_at_that_source(result):
    """A clause that lists capabilities and then names an employer must have
    evidence for ALL of them at that employer."""
    for capabilities, sources in _attributed_claims(result.resume.summary):
        for source in sources:
            record = next((r for r in result.inventory.records
                           if r.short_label == source or r.company == source), None)
            assert record is not None, f"unknown source attributed: {source}"
            for capability in capabilities:
                supported = (
                    capability in record.skills
                    or record.mentions(capability)
                    or any(capability in m.evidence and record.id in m.experience_ids
                           for m in result.matrix.claimable())
                )
                assert supported, (
                    f"{capability!r} attributed to {source!r}, which does not "
                    f"evidence it — its skills are {record.skills}")


def test_project_only_capabilities_are_attributed_to_the_project(result):
    """Evidence that exists only in NexusOS is named as independent work."""
    summary = result.resume.summary
    project_names = {p.name for p in result.resume.featured_projects}
    if "Complemented by independent work" not in summary:
        return
    clause = re.search(r"Complemented by independent work on (.+?) through (.+?)\.",
                       summary)
    assert clause, summary
    assert clause.group(2).strip() in project_names or \
        clause.group(2).strip() == "NexusOS", clause.group(2)
    # And those capabilities must NOT also be attributed to an employer.
    independent = {c.strip() for c in
                   re.split(r",\s*|\s+and\s+", clause.group(1)) if c.strip()}
    for capabilities, _ in _attributed_claims(summary):
        assert not (independent & set(capabilities)), \
            f"capability claimed twice, from two sources: {independent & set(capabilities)}"


def test_summary_never_narrates_its_own_generation(result):
    low = normalize(result.resume.summary)
    for tell in ("matched to this search", "this position", "the job description",
                 "target role", "aligned to this"):
        assert not term_present(tell, low), result.resume.summary


# --------------------------------------------------------------------------- #
# 3-6. Nothing else moved
# --------------------------------------------------------------------------- #
def test_headline_stays_evidence_bound(result):
    headline = result.resume.headline
    assert "DevSecOps" in headline or "DevOps" in headline, headline
    for banned in ("Lead", "Head", "Manager", "Director", "Principal",
                   "AI Governance Lead", "Security Lead"):
        assert banned not in headline, headline
    assert normalize(headline) != normalize(result.spec.target_role)


def test_prex_still_prioritises_ai_governance(result):
    if result.case != "prex":
        pytest.skip("Prex-specific")
    groups = [g.name for g in result.resume.skill_groups]
    assert groups.index("AI Systems & Governance") < groups.index("Cloud Platforms")
    rendered = normalize(extract_text(result.artifacts["pdf"]))
    assert term_present("ai governance", rendered)
    assert term_present("nexusos", rendered)


def test_pdf_and_docx_stay_equivalent(result):
    from docx import Document
    pdf = normalize(extract_text(result.artifacts["pdf"]))
    docx = normalize("\n".join(p.text for p in
                               Document(result.artifacts["docx"]).paragraphs))
    # The summary is the sentence this whole file is about: both renderers must
    # carry the same one.
    for fragment in result.resume.summary.split(". ")[:3]:
        probe = normalize(fragment)[:60]
        assert probe in pdf, f"missing from PDF: {fragment[:60]}"
        assert probe in docx, f"missing from DOCX: {fragment[:60]}"
    assert not AGGREGATION_RE.search(docx)


def test_final_gates_and_factcheck_still_green(result):
    assert result.artifact_findings == [], result.artifact_findings
    failed = [g.as_dict() for g in result.pipeline_gates if not g.ok]
    assert not failed, failed
