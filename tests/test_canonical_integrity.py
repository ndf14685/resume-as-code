"""Anti-hallucination: facts are protected; overlaps are represented faithfully."""

import copy

from conftest import DATA_DIR
from resume_as_code.loader import load_bundle
from resume_as_code.models import canonical_fingerprint


def test_fingerprint_is_deterministic():
    assert canonical_fingerprint(load_bundle(DATA_DIR)) == canonical_fingerprint(
        load_bundle(DATA_DIR)
    )


def test_wording_change_does_not_change_fingerprint():
    bundle = load_bundle(DATA_DIR)
    baseline = canonical_fingerprint(bundle)
    # Rewriting a bullet is presentation, not fact.
    bundle.experiences[0].bullets[0].text = "totally different wording here"
    assert canonical_fingerprint(bundle) == baseline


def test_date_change_changes_fingerprint():
    bundle = load_bundle(DATA_DIR)
    baseline = canonical_fingerprint(bundle)
    bundle.experiences[0].end = "2099-01"  # invented date
    assert canonical_fingerprint(bundle) != baseline


def test_engagement_change_changes_fingerprint():
    bundle = load_bundle(DATA_DIR)
    baseline = canonical_fingerprint(bundle)
    # e.g. turning a contractor into full-time must change the fingerprint.
    allianz = next(e for e in bundle.experiences if e.id == "pichincha")
    allianz.engagement = "full_time"
    assert canonical_fingerprint(bundle) != baseline


def test_overlap_between_allianz_and_pichincha_is_preserved():
    bundle = load_bundle(DATA_DIR)
    allianz = next(e for e in bundle.experiences if e.id == "allianz")
    pichincha = next(e for e in bundle.experiences if e.id == "pichincha")
    assert allianz.engagement == "full_time"
    assert pichincha.engagement == "contract"
    # They genuinely overlap in time and both are kept (overlap not hidden).
    assert allianz.start_key() <= pichincha.end_key()
    assert pichincha.start_key() <= allianz.end_key()


def test_telecom_two_distinct_stages():
    bundle = load_bundle(DATA_DIR)
    telecom = [e for e in bundle.experiences if e.company == "Telecom"]
    assert len(telecom) == 2
    engagements = {e.engagement for e in telecom}
    assert engagements == {"full_time", "part_time"}
