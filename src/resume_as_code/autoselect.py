"""Deterministic profile auto-selection from a job description.

No LLM, no network. Scores each presentation profile by the overlap between
its skill_priority and the matched_skills the analyzer extracted from the JD.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from resume_as_code.jobmatch import analyze_job
from resume_as_code.loader import load_bundle
from resume_as_code.tailor import load_profile


@dataclass
class ProfileSelection:
    name: str
    path: Path
    score: int
    scores: dict[str, int]
    is_default: bool


def score_profiles(matched_categories: set[str], profiles_dir: str | Path) -> dict[str, int]:
    """Score each profile by overlap between its skill_priority (category ids)
    and matched_categories (category ids), stem -> overlap count."""
    scores: dict[str, int] = {}
    for path in sorted(Path(profiles_dir).glob("*.yaml")):
        profile = load_profile(path)
        scores[path.stem] = len(set(profile.skill_priority) & set(matched_categories))
    return scores


def pick_profile(scores: dict[str, int], default: str = "ai-architect") -> tuple[str, bool]:
    if not scores:
        return default, True
    # alphabetical tie-break: iterate names in sorted order, keep first max
    best = max(sorted(scores), key=lambda name: scores[name])
    if scores[best] == 0:
        return default, True
    return best, False


def select_profile(
    jd_text: str,
    data_dir: str | Path,
    profiles_dir: str | Path,
    default: str = "ai-architect",
) -> ProfileSelection:
    bundle = load_bundle(data_dir)
    analysis = analyze_job(bundle, jd_text)
    matched_categories = {
        cat
        for skill in analysis.matched_skills
        if (cat := bundle.skills.category_of(skill)) is not None
    }
    scores = score_profiles(matched_categories, profiles_dir)
    name, is_default = pick_profile(scores, default=default)
    return ProfileSelection(
        name=name,
        path=Path(profiles_dir) / f"{name}.yaml",
        score=scores.get(name, 0),
        scores=scores,
        is_default=is_default,
    )
