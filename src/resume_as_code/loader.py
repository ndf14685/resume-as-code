"""Load and validate the canonical data from data/*.yaml into a DataBundle."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml

from .models import (
    Basics,
    DataBundle,
    Experience,
    Project,
    SkillsCatalog,
    canonical_fingerprint,
)


class DataError(Exception):
    """Raised when the canonical data is invalid or inconsistent."""


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise DataError(f"missing data file: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def resolve_email(data_dir: Path, basics_email: Optional[str]) -> Optional[str]:
    """Resolve the contact email without ever committing it.

    Priority: RESUME_EMAIL env var > data/contact.local.yaml (git-ignored) >
    data/profile.yaml value (normally null).
    """
    env = os.environ.get("RESUME_EMAIL")
    if env:
        return env.strip()
    local = data_dir / "contact.local.yaml"
    if local.exists():
        payload = _read_yaml(local)
        if payload.get("email"):
            return str(payload["email"]).strip()
    return basics_email


def load_bundle(data_dir: str | Path) -> DataBundle:
    """Load, validate and cross-check the full canonical data set."""
    data_dir = Path(data_dir)

    profile = _read_yaml(data_dir / "profile.yaml")
    skills_raw = _read_yaml(data_dir / "skills.yaml")
    experience_raw = _read_yaml(data_dir / "experience.yaml")
    projects_raw = _read_yaml(data_dir / "projects.yaml")
    education_raw = _read_yaml(data_dir / "education.yaml")
    certs_raw = _read_yaml(data_dir / "certifications.yaml")

    try:
        basics = Basics(**profile)
        skills = SkillsCatalog(**skills_raw)
        experiences = [Experience(**e) for e in experience_raw.get("experiences", [])]
        projects = [Project(**p) for p in projects_raw.get("projects", [])]
    except Exception as exc:  # pydantic ValidationError -> friendly message
        raise DataError(f"schema validation failed: {exc}") from exc

    basics.email = resolve_email(data_dir, basics.email)

    bundle = DataBundle(
        basics=basics,
        skills=skills,
        experiences=experiences,
        projects=projects,
        education=education_raw.get("education", []) or [],
        certifications=certs_raw.get("certifications", []) or [],
    )

    integrity_errors = bundle.check_skill_integrity()
    if integrity_errors:
        raise DataError(
            "skill integrity check failed (skills not in data/skills.yaml):\n  - "
            + "\n  - ".join(integrity_errors)
        )

    # Duplicate-id guard.
    ids = [e.id for e in bundle.experiences]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise DataError(f"duplicate experience ids: {sorted(dupes)}")

    return bundle


def fingerprint(bundle: DataBundle) -> str:
    return canonical_fingerprint(bundle)
