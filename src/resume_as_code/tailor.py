"""Tailoring engine: turn canonical data + a profile into a ResumeModel.

This layer is *presentation only*. It selects experiences, orders skills, and
chooses which bullets to show. It can never introduce a company, date, title or
skill that is not already in the canonical data — every skill it emits is drawn
from the catalog, and every bullet is verbatim canonical text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict

from .models import (
    DataBundle,
    Experience,
    RenderExperience,
    RenderProject,
    ResumeModel,
    SkillGroup,
    canonical_fingerprint,
    date_sort_key,
)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    headline: str
    tagline: Optional[str] = None
    summary: str
    skill_priority: list[str] = []
    emphasis_tags: list[str] = []
    max_bullets: int = 3
    full_detail_count: int = 6   # detailed roles after this cap bullets at 2
    condense_before: Optional[str] = None
    include_projects: list[str] = []


def load_profile(path: str | Path) -> Profile:
    with Path(path).open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    prof = Profile(**raw)
    prof.summary = " ".join(prof.summary.split())
    return prof


def _select_bullets(
    exp: Experience, emphasis: set[str], limit: int, boost: set[str]
) -> list[str]:
    """Score bullets by tag overlap with the emphasis set; keep original order."""
    scored = []
    for idx, bullet in enumerate(exp.bullets):
        tagset = set(bullet.tags)
        score = len(tagset & emphasis) + 2 * len(tagset & boost)
        scored.append((score, idx, bullet.text))
    # pick top `limit` by score, then restore document order for readability
    top = sorted(scored, key=lambda t: (-t[0], t[1]))[:limit]
    return [text for _, _, text in sorted(top, key=lambda t: t[1])]


def _skill_groups(
    bundle: DataBundle,
    priority: list[str],
    matched: set[str],
) -> list[SkillGroup]:
    """Build ordered skill groups from evidenced skills only.

    Categories follow `priority`; any evidenced category not listed is appended.
    Within a group, JD-matched skills lead, then skills are ordered by how much
    evidence supports them.
    """
    evidence = bundle.evidence_index()
    catalog = bundle.skills

    # category_id -> list of (skill_name)
    by_cat: dict[str, list[str]] = {}
    for name in catalog.all_names():
        if name in evidence:  # only skills actually used
            cat = catalog.category_of(name)
            by_cat.setdefault(cat, []).append(name)

    ordered_cats = list(priority) + [c for c in by_cat if c not in priority]
    groups: list[SkillGroup] = []
    for cat in ordered_cats:
        names = by_cat.get(cat)
        if not names:
            continue
        names.sort(
            key=lambda n: (
                0 if n in matched else 1,          # matched skills first
                -len(evidence.get(n, [])),          # then by evidence weight
                n,
            )
        )
        groups.append(SkillGroup(name=catalog.category_name(cat), items=names))
    return groups


def build_resume(
    bundle: DataBundle,
    profile: Profile,
    *,
    target: Optional[str] = None,
    matched_skills: Optional[set[str]] = None,
    extra_emphasis: Optional[list[str]] = None,
    summary_override: Optional[str] = None,
) -> ResumeModel:
    matched = matched_skills or set()
    emphasis = set(profile.emphasis_tags) | set(extra_emphasis or [])

    threshold = (
        date_sort_key(profile.condense_before, is_end=True)
        if profile.condense_before
        else None
    )

    experiences: list[RenderExperience] = []
    detailed_seen = 0
    for exp in bundle.experiences_sorted():
        condensed = threshold is not None and exp.end_key() < threshold
        if condensed:
            experiences.append(
                RenderExperience(
                    company=exp.company,
                    title=exp.title,
                    meta_right=exp.date_range(),
                    engagement_label=exp.engagement_label(),
                    location=exp.location,
                    bullets=[],
                    condensed=True,
                )
            )
            continue
        limit = profile.max_bullets if detailed_seen < profile.full_detail_count else 2
        detailed_seen += 1
        experiences.append(
            RenderExperience(
                company=exp.company,
                title=exp.title,
                meta_right=exp.date_range(),
                engagement_label=exp.engagement_label(),
                location=exp.location,
                bullets=_select_bullets(exp, emphasis, limit, matched),
                condensed=False,
            )
        )

    featured: list[RenderProject] = []
    proj_by_id = {p.id: p for p in bundle.projects}
    for pid in profile.include_projects:
        p = proj_by_id.get(pid)
        if not p:
            continue
        bullets = _select_bullets_project(p, emphasis, 4, matched)
        featured.append(
            RenderProject(
                name=p.name, label=p.label, tagline=p.tagline, bullets=bullets
            )
        )

    links = _format_links(bundle)

    return ResumeModel(
        name=bundle.basics.name,
        headline=profile.headline,
        tagline=profile.tagline,
        location=bundle.basics.location,
        email=bundle.basics.email,
        links=links,
        summary=summary_override or profile.summary,
        skill_groups=_skill_groups(bundle, profile.skill_priority, matched),
        experiences=experiences,
        featured_projects=featured,
        certifications=bundle.certifications,
        training=bundle.training,
        languages=bundle.languages,
        education=bundle.education,
        profile_name=profile.name,
        target=target,
        canonical_hash=canonical_fingerprint(bundle),
    )


def _select_bullets_project(project, emphasis, limit, boost) -> list[str]:
    scored = []
    for idx, bullet in enumerate(project.bullets):
        tagset = set(bullet.tags)
        score = len(tagset & emphasis) + 2 * len(tagset & boost)
        scored.append((score, idx, bullet.text))
    top = sorted(scored, key=lambda t: (-t[0], t[1]))[:limit]
    return [text for _, _, text in sorted(top, key=lambda t: t[1])]


def _format_links(bundle: DataBundle) -> list[str]:
    links = bundle.basics.links
    out = []
    for value in (links.website, links.github, links.linkedin):
        if value:
            out.append(value)
    return out
