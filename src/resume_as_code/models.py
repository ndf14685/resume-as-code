"""Pydantic schema for the canonical data and the derived resume model.

The models here validate the *facts* (data/*.yaml) and enforce the core
anti-hallucination invariant: every skill referenced by an experience or
project must exist in the canonical skills catalog. A canonical fingerprint
(sha256 over the fact-bearing fields) lets tests and the validator detect any
tampering with companies, titles, dates, engagement types or skills.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

Engagement = Literal[
    "full_time", "contract", "freelance", "part_time", "consulting"
]

ENGAGEMENT_LABELS: dict[str, str] = {
    "full_time": "Full-time",
    "contract": "Contract",
    "freelance": "Freelance",
    "part_time": "Part-time",
    "consulting": "Consulting engagement",
}

MONTHS = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


# --------------------------------------------------------------------------- #
# Date helpers
# --------------------------------------------------------------------------- #
def date_sort_key(value: str, *, is_end: bool) -> tuple[int, int]:
    """Return a sortable (year, month) key for a date string.

    Accepts "present", "YYYY" (month unknown) or "YYYY-MM". Year-only dates
    sort to the start of the year for a start date and the end of the year for
    an end date, so overlapping ranges order sensibly.
    """
    if value == "present":
        return (9999, 99)
    parts = value.split("-")
    year = int(parts[0])
    if len(parts) == 1:
        return (year, 12 if is_end else 1)
    return (year, int(parts[1]))


def fmt_date(value: str, *, approximate: bool = False) -> str:
    """Human-readable date: 'Present', 'Feb 2024', or '2018' (year only)."""
    if value == "present":
        return "Present"
    parts = value.split("-")
    if len(parts) == 1:
        text = parts[0]
    else:
        text = f"{MONTHS[int(parts[1])]} {parts[0]}"
    return f"~{text}" if approximate else text


# --------------------------------------------------------------------------- #
# Skills catalog
# --------------------------------------------------------------------------- #
class SkillDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    aliases: list[str] = []


class SkillCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    skills: list[SkillDef]


class SkillsCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: list[SkillCategory]

    def all_names(self) -> list[str]:
        return [s.name for c in self.categories for s in c.skills]

    def name_set(self) -> set[str]:
        return set(self.all_names())

    def category_of(self, name: str) -> Optional[str]:
        for c in self.categories:
            for s in c.skills:
                if s.name == name:
                    return c.id
        return None

    def category_name(self, category_id: str) -> str:
        for c in self.categories:
            if c.id == category_id:
                return c.name
        return category_id

    def alias_index(self) -> dict[str, str]:
        """Map every lowercase alias (and the name itself) to the canonical name."""
        index: dict[str, str] = {}
        for c in self.categories:
            for s in c.skills:
                index[s.name.lower()] = s.name
                for alias in s.aliases:
                    index[alias.lower()] = s.name
        return index


# --------------------------------------------------------------------------- #
# Experience / projects
# --------------------------------------------------------------------------- #
class Bullet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    tags: list[str] = []

    @field_validator("text")
    @classmethod
    def _clean(cls, v: str) -> str:
        return " ".join(v.split())


class Experience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    company: str
    title: str
    engagement: Optional[Engagement] = None
    location: Optional[str] = None
    start: str
    end: str
    approximate_start: bool = False
    note: Optional[str] = None
    skills: list[str] = []
    bullets: list[Bullet] = []

    @field_validator("note")
    @classmethod
    def _clean_note(cls, v: Optional[str]) -> Optional[str]:
        return " ".join(v.split()) if v else v

    def start_key(self) -> tuple[int, int]:
        return date_sort_key(self.start, is_end=False)

    def end_key(self) -> tuple[int, int]:
        return date_sort_key(self.end, is_end=True)

    def date_range(self) -> str:
        return (
            f"{fmt_date(self.start, approximate=self.approximate_start)} – "
            f"{fmt_date(self.end)}"
        )

    def engagement_label(self) -> Optional[str]:
        return ENGAGEMENT_LABELS.get(self.engagement) if self.engagement else None


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    label: str
    tagline: Optional[str] = None
    skills: list[str] = []
    bullets: list[Bullet] = []


# --------------------------------------------------------------------------- #
# Basics / bundle
# --------------------------------------------------------------------------- #
class Links(BaseModel):
    model_config = ConfigDict(extra="forbid")
    website: Optional[str] = None
    github: Optional[str] = None
    linkedin: Optional[str] = None


class Basics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    location: str
    headline: str
    tagline: Optional[str] = None
    links: Links = Links()
    email: Optional[str] = None


class DataBundle(BaseModel):
    """The complete, validated single source of truth."""

    model_config = ConfigDict(extra="forbid")
    basics: Basics
    skills: SkillsCatalog
    experiences: list[Experience]
    projects: list[Project] = []
    education: list[dict] = []
    certifications: list[dict] = []

    def check_skill_integrity(self) -> list[str]:
        """Return a list of skill references that are not in the catalog."""
        known = self.skills.name_set()
        errors: list[str] = []
        for exp in self.experiences:
            for skill in exp.skills:
                if skill not in known:
                    errors.append(f"experience '{exp.id}' -> unknown skill '{skill}'")
        for proj in self.projects:
            for skill in proj.skills:
                if skill not in known:
                    errors.append(f"project '{proj.id}' -> unknown skill '{skill}'")
        return errors

    def evidence_index(self) -> dict[str, list[str]]:
        """Map each canonical skill -> list of experience/project labels using it."""
        evidence: dict[str, list[str]] = {}
        for exp in self.experiences:
            for skill in exp.skills:
                evidence.setdefault(skill, []).append(exp.company)
        for proj in self.projects:
            for skill in proj.skills:
                evidence.setdefault(skill, []).append(proj.name)
        return evidence

    def unused_skills(self) -> list[str]:
        """Catalog skills with no evidence in any experience or project."""
        used = set(self.evidence_index())
        return [n for n in self.skills.all_names() if n not in used]

    def experiences_sorted(self) -> list[Experience]:
        """Reverse-chronological by end date, then start date."""
        return sorted(
            self.experiences,
            key=lambda e: (e.end_key(), e.start_key()),
            reverse=True,
        )


# --------------------------------------------------------------------------- #
# Canonical fingerprint (tamper detection)
# --------------------------------------------------------------------------- #
def canonical_fingerprint(bundle: DataBundle) -> str:
    """Deterministic sha256 over the fact-bearing fields only.

    Presentation choices (bullet wording, ordering) do NOT affect this hash.
    Companies, titles, dates, engagement types and skill sets DO.
    """
    facts = {
        "name": bundle.basics.name,
        "location": bundle.basics.location,
        "experiences": sorted(
            (
                {
                    "id": e.id,
                    "company": e.company,
                    "title": e.title,
                    "engagement": e.engagement,
                    "start": e.start,
                    "end": e.end,
                    "approximate_start": e.approximate_start,
                    "skills": sorted(e.skills),
                }
                for e in bundle.experiences
            ),
            key=lambda d: d["id"],
        ),
        "projects": sorted(
            (
                {"id": p.id, "name": p.name, "skills": sorted(p.skills)}
                for p in bundle.projects
            ),
            key=lambda d: d["id"],
        ),
    }
    payload = json.dumps(facts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Resume model (output of tailoring, input to renderers)
# --------------------------------------------------------------------------- #
class RenderExperience(BaseModel):
    company: str
    title: str
    meta_right: str  # date range
    engagement_label: Optional[str] = None
    location: Optional[str] = None
    note: Optional[str] = None
    bullets: list[str] = []
    condensed: bool = False


class RenderProject(BaseModel):
    name: str
    label: str
    tagline: Optional[str] = None
    bullets: list[str] = []


class SkillGroup(BaseModel):
    name: str
    items: list[str]


class ResumeModel(BaseModel):
    """Fully resolved, presentation-ready CV. Contains no logic, only text."""

    name: str
    headline: str
    tagline: Optional[str] = None
    location: str
    email: Optional[str] = None
    links: list[str] = []
    summary: str = ""
    skill_groups: list[SkillGroup] = []
    experiences: list[RenderExperience] = []
    featured_projects: list[RenderProject] = []
    education: list[dict] = []
    certifications: list[dict] = []
    # provenance
    profile_name: str = ""
    target: Optional[str] = None
    canonical_hash: str = ""
