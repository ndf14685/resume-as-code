"""Schema & data-integrity tests for the canonical source of truth."""

import copy

import pytest

from conftest import DATA_DIR
from resume_as_code.loader import DataError, load_bundle
from resume_as_code.models import Experience, SkillsCatalog


def test_bundle_loads_and_validates():
    bundle = load_bundle(DATA_DIR)
    assert len(bundle.experiences) >= 13
    assert bundle.basics.name


def test_all_referenced_skills_exist_in_catalog():
    bundle = load_bundle(DATA_DIR)
    assert bundle.check_skill_integrity() == []


def test_no_unused_catalog_skills():
    # Every catalog skill must have real evidence somewhere.
    bundle = load_bundle(DATA_DIR)
    assert bundle.unused_skills() == []


def test_unknown_skill_is_rejected():
    catalog = SkillsCatalog(categories=[])
    exp = Experience(
        id="x", company="C", title="T", start="2020", end="2021",
        skills=["Totally Made Up Skill"],
    )
    from resume_as_code.models import Basics, DataBundle
    bundle = DataBundle(
        basics=Basics(name="n", location="l", headline="h"),
        skills=catalog, experiences=[exp],
    )
    assert bundle.check_skill_integrity() != []


def test_experiences_sorted_reverse_chronological():
    bundle = load_bundle(DATA_DIR)
    ordered = bundle.experiences_sorted()
    keys = [e.end_key() for e in ordered]
    assert keys == sorted(keys, reverse=True)
    # Most recent role is the current one.
    assert ordered[0].end == "present"


def test_duplicate_ids_detected(tmp_path):
    # Corrupt a copy of experience.yaml with a duplicate id.
    import shutil, yaml
    for name in ["profile.yaml", "skills.yaml", "projects.yaml",
                 "education.yaml", "certifications.yaml"]:
        shutil.copy(DATA_DIR / name, tmp_path / name)
    data = yaml.safe_load((DATA_DIR / "experience.yaml").read_text())
    data["experiences"].append(copy.deepcopy(data["experiences"][0]))
    (tmp_path / "experience.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(DataError):
        load_bundle(tmp_path)
