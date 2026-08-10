"""End-to-end ATS validation on freshly generated artifacts."""

import pytest

from conftest import DATA_DIR, PROFILES_DIR
from resume_as_code.loader import load_bundle
from resume_as_code.render_docx import render_docx
from resume_as_code.render_pdf import render_pdf
from resume_as_code.tailor import build_resume, load_profile
from resume_as_code.validate import extract_text, run_ats_validation


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(DATA_DIR)


def _generate(bundle, profile_name, tmp_path):
    profile = load_profile(PROFILES_DIR / f"{profile_name}.yaml")
    resume = build_resume(bundle, profile)
    stem = tmp_path / f"cv_{profile_name}"
    render_pdf(resume, stem.with_suffix(".pdf"))
    render_docx(resume, stem.with_suffix(".docx"))
    return stem.with_suffix(".pdf")


@pytest.mark.parametrize("profile_name", ["devops", "devsecops", "ai-architect"])
def test_generated_pdf_passes_all_ats_checks(bundle, profile_name, tmp_path):
    pdf = _generate(bundle, profile_name, tmp_path)
    checks = run_ats_validation(pdf, DATA_DIR)
    failed = [c.name for c in checks if not c.ok]
    assert failed == [], f"failed checks: {failed}"


def test_pdf_text_is_extractable(bundle, tmp_path):
    pdf = _generate(bundle, "devops", tmp_path)
    text = extract_text(pdf)
    assert "Néstor David Fleitas" in text
    assert "Professional Experience" in text
    assert "github.com/ndf14685" in text


def test_validator_detects_invented_year(bundle, tmp_path, monkeypatch):
    # Render a CV whose data contains an invented (non-canonical) year and
    # confirm the validator flags it.
    profile = load_profile(PROFILES_DIR / "devops.yaml")
    resume = build_resume(bundle, profile)
    # Inject an invented year into the summary (presentation surface).
    resume.summary = resume.summary + " Fabricated milestone in 1999."
    pdf = tmp_path / "tampered.pdf"
    render_pdf(resume, pdf)
    checks = run_ats_validation(pdf, DATA_DIR)
    by_name = {c.name: c for c in checks}
    assert by_name["canonical dates (no invented years)"].ok is False
