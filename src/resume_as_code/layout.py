"""Shared section spec so renderers and the ATS validator agree on structure."""

from __future__ import annotations

from .models import ResumeModel

# Canonical section order. The validator checks that headings appear in this
# order in the extracted PDF text.
SECTION_ORDER: list[tuple[str, str]] = [
    ("summary", "Professional Summary"),
    ("skills", "Core Skills"),
    ("experience", "Professional Experience"),
    ("projects", "Featured Project"),
    ("certifications", "Certifications"),
    ("training", "Training & Courses"),
    ("languages", "Languages"),
    ("education", "Education"),
]


def present_sections(resume: ResumeModel) -> list[tuple[str, str]]:
    """Return (key, title) for the sections that actually have content."""
    out: list[tuple[str, str]] = []
    for key, title in SECTION_ORDER:
        if key == "summary" and resume.summary:
            out.append((key, title))
        elif key == "skills" and resume.skill_groups:
            out.append((key, title))
        elif key == "experience" and resume.experiences:
            out.append((key, title))
        elif key == "projects" and resume.featured_projects:
            t = "Featured Projects" if len(resume.featured_projects) > 1 else title
            out.append((key, t))
        elif key == "certifications" and resume.certifications:
            out.append((key, title))
        elif key == "training" and resume.training:
            out.append((key, title))
        elif key == "languages" and resume.languages:
            out.append((key, title))
        elif key == "education" and resume.education:
            out.append((key, title))
    return out


def training_line(group: dict) -> str:
    """Render one provider's training group as 'Provider: a (yr), b (yr)'."""
    provider = group.get("provider", "")
    parts = []
    for item in group.get("items", []):
        name = item.get("name", "")
        year = item.get("year")
        parts.append(f"{name} ({year})" if year else name)
    return f"{provider}: " + ", ".join(parts) if provider else ", ".join(parts)


def language_line(item: dict) -> str:
    return f"{item.get('language', '')}: {item.get('level', '')}".strip(": ")


def contact_line(resume: ResumeModel) -> str:
    parts = [resume.location]
    if resume.email:
        parts.append(resume.email)
    parts.extend(resume.links)
    return "  |  ".join(parts)
