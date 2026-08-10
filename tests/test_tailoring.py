"""Tailoring & job-match tests — selection never becomes invention."""

import pytest

from conftest import DATA_DIR, JOBS_DIR, PROFILES_DIR
from resume_as_code.jobmatch import EXTERNAL_TERMS, analyze_job
from resume_as_code.loader import load_bundle
from resume_as_code.tailor import build_resume, load_profile


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(DATA_DIR)


@pytest.mark.parametrize("profile_name", ["devops", "devsecops", "ai-architect"])
def test_every_profile_builds(bundle, profile_name):
    profile = load_profile(PROFILES_DIR / f"{profile_name}.yaml")
    resume = build_resume(bundle, profile)
    assert resume.summary
    assert resume.experiences
    # Every emitted skill is canonical — nothing invented by presentation.
    catalog = bundle.skills.name_set()
    for group in resume.skill_groups:
        for item in group.items:
            assert item in catalog


def test_ai_profile_features_nexusos(bundle):
    profile = load_profile(PROFILES_DIR / "ai-architect.yaml")
    resume = build_resume(bundle, profile)
    assert any(p.name == "NexusOS" for p in resume.featured_projects)


def test_ecomflow_job_analysis(bundle):
    jd = (JOBS_DIR / "ecomflow.txt").read_text()
    analysis = analyze_job(bundle, jd)

    # Covered: real evidence exists.
    assert "GCP" in analysis.covered
    assert "Observability" in analysis.covered

    # The specific tools we do NOT have are never claimed as skills, whether
    # they land in transferable or gaps.
    not_incorporated = {t["term"] for t in analysis.transferable} | set(analysis.gaps)
    for tool in ["planetscale", "drizzle orm", "cloudflare workers",
                 "opentelemetry", "grafana cloud", "sentry"]:
        assert tool in not_incorporated
        assert tool not in {s.lower() for s in analysis.matched_skills}

    # Transferable: PlanetScale is not claimed; Amazon RDS surfaces instead.
    transfer_terms = {t["term"] for t in analysis.transferable}
    assert "planetscale" in transfer_terms
    assert "Amazon RDS" in analysis.matched_skills
    assert "planetscale" not in analysis.matched_skills


def test_matched_skills_are_all_canonical(bundle):
    jd = (JOBS_DIR / "ecomflow.txt").read_text()
    analysis = analyze_job(bundle, jd)
    catalog = bundle.skills.name_set()
    assert analysis.matched_skills <= catalog
    # No external tool name ever leaks into the claimed skill set.
    for term in EXTERNAL_TERMS:
        assert term not in {s.lower() for s in analysis.matched_skills}


def test_ecomflow_resume_has_no_unclaimed_tools_in_skills(bundle):
    jd = (JOBS_DIR / "ecomflow.txt").read_text()
    analysis = analyze_job(bundle, jd)
    profile = load_profile(PROFILES_DIR / "devops.yaml")
    resume = build_resume(
        bundle, profile, target="ecomflow",
        matched_skills=analysis.matched_skills,
        extra_emphasis=analysis.extra_emphasis,
    )
    all_skills = " ".join(
        item.lower() for g in resume.skill_groups for item in g.items
    )
    for term in ["cloudflare", "planetscale", "drizzle", "grafana",
                 "opentelemetry", "sentry"]:
        assert term not in all_skills
