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
    training_path = data_dir / "training.yaml"
    languages_path = data_dir / "languages.yaml"
    training_raw = _read_yaml(training_path) if training_path.exists() else {}
    languages_raw = _read_yaml(languages_path) if languages_path.exists() else {}

    try:
        basics = Basics(**profile)
        skills = SkillsCatalog(**skills_raw)
        experiences = [Experience(**e) for e in experience_raw.get("experiences", [])]
        projects = [Project(**p) for p in projects_raw.get("projects", [])]
    except Exception as exc:  # pydantic ValidationError -> friendly message
        raise DataError(f"schema validation failed: {exc}") from exc

    basics.email = resolve_email(data_dir, basics.email)

    certifications_all = certs_raw.get("certifications", []) or []
    training_groups = training_raw.get("training", []) or []
    _check_credential_types(certifications_all, training_groups)

    bundle = DataBundle(
        basics=basics,
        skills=skills,
        experiences=experiences,
        projects=projects,
        education=education_raw.get("education", []) or [],
        # Only confirmed credentials are renderable/claimable.
        certifications=[c for c in certifications_all
                        if c.get("status") in CLAIMABLE_CREDENTIAL_STATUSES],
        certifications_all=certifications_all,
        training=training_groups,
        languages=languages_raw.get("languages", []) or [],
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


CREDENTIAL_TYPES = frozenset({"certification", "badge"})
TRAINING_TYPES = frozenset({"training", "course"})
# Only `confirmed` is renderable and claimable. `expired` is a credential the
# candidate genuinely held whose validity has lapsed: keeping the record is
# honest, presenting it as current would not be. `needs_confirmation` is a
# credential visible somewhere with no issuing record behind it.
CREDENTIAL_STATUSES = frozenset({"confirmed", "needs_confirmation", "expired"})
CLAIMABLE_CREDENTIAL_STATUSES = frozenset({"confirmed"})


def _check_credential_types(certifications: list[dict],
                            training_groups: list[dict]) -> None:
    """Keep the credential vocabularies closed and non-overlapping.

    Certification, badge, training and course are different kinds of evidence
    and must never silently convert into one another — a Udemy course cannot
    drift into a certification because someone edited a `type`, and a badge
    cannot be promoted by adding a year. The two vocabularies are disjoint, so
    a mistyped entry fails the load rather than reaching a CV.
    """
    errors: list[str] = []
    for cert in certifications:
        name = cert.get("name", "<unnamed>")
        # An expired credential must not smuggle a date back in through the
        # renderer: the candidate said it lapsed and gave no dates, so there
        # are none to render.
        if cert.get("status") == "expired" and (cert.get("issued")
                                                or cert.get("expires")):
            errors.append(f"certification '{name}': expired entries carry no "
                          "issue/expiry date unless one was actually supplied")
        ctype = cert.get("type")
        status = cert.get("status")
        if ctype not in CREDENTIAL_TYPES:
            errors.append(f"certification '{name}': type must be one of "
                          f"{sorted(CREDENTIAL_TYPES)}, got {ctype!r}")
        if status not in CREDENTIAL_STATUSES:
            errors.append(f"certification '{name}': status must be one of "
                          f"{sorted(CREDENTIAL_STATUSES)}, got {status!r}")
        if status in ("confirmed", "expired") and not cert.get("issuer"):
            errors.append(f"certification '{name}': confirmed entries need an "
                          "issuer (evidence of a real issuing record)")
    for group in training_groups:
        provider = group.get("provider", "<unknown provider>")
        for item in group.get("items", []) or []:
            name = item.get("name", "<unnamed>")
            ttype = item.get("type")
            if ttype in CREDENTIAL_TYPES:
                errors.append(
                    f"training '{provider} / {name}': {ttype!r} is a credential "
                    "type; it belongs in data/certifications.yaml, not here")
            elif ttype not in TRAINING_TYPES:
                errors.append(f"training '{provider} / {name}': type must be one "
                              f"of {sorted(TRAINING_TYPES)}, got {ttype!r}")
    if errors:
        raise DataError("credential type/status check failed:\n  - "
                        + "\n  - ".join(errors))


def fingerprint(bundle: DataBundle) -> str:
    return canonical_fingerprint(bundle)

# probe
