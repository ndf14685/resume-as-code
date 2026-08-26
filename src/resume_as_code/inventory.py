"""EVIDENCE INVENTORY — the candidate's real history, indexed granularly.

Stage 2 of the pipeline. Before a single word of the CV is written, every
canonical experience is turned into a searchable record with its evidence split
by the SAME dimensions the JD is parsed into (cloud platform, IaC, networking,
identity, containers, CI/CD, OS, scripting, observability, security/governance,
data, AI, domain). That symmetry is what makes JD↔evidence matching possible at
all: previously the JD side and the candidate side spoke different vocabularies
(JD terms vs. bullet emphasis tags), so JD matching could not influence which
bullets were selected.

Sources consulted: ALL canonical files in data/ — experiences (skills, bullets,
client, industry), featured projects, certifications and training. A previously
generated CV is an OUTPUT and is never read as a source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .jdspec import normalize, term_present
from .models import DataBundle, Experience

# skills.yaml category id -> JD dimension. Both sides of the match now share a
# vocabulary; anything unmapped lands in "other" and still carries evidence.
CATEGORY_DIMENSION: dict[str, str] = {
    "cloud": "cloud_platform",
    "aws_services": "cloud_platform",
    "iac": "iac",
    "cicd": "cicd",
    "containers": "containers",
    "devsecops": "security_governance",
    "reliability": "observability",
    "languages": "scripting",
    "data": "data",
    "ai": "ai_governance",
    "platform": "other",
    "integration": "other",
    "quality": "other",
}


@dataclass
class EvidenceRecord:
    """One professional experience, indexed for retrieval."""
    id: str
    company: str
    title: str
    client: Optional[str] = None
    industry: Optional[str] = None
    engagement: Optional[str] = None
    start: str = ""
    end: str = ""
    skills: list[str] = field(default_factory=list)
    facets: dict[str, list[str]] = field(default_factory=dict)  # dimension -> skills
    bullets: list[dict] = field(default_factory=list)           # {idx,text,tags,norm}
    corpus: str = ""                                            # normalized haystack
    exp: Optional[Experience] = None
    _dimension_corpus: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def label(self) -> str:
        """Company, plus the end client when it adds information. 'AGEA / Clarín'
        already names Clarín, so appending it again produces 'AGEA / Clarín /
        Clarín' in reports and summaries."""
        if not self.client:
            return self.company
        low = self.company.lower()
        extra = [c.strip() for c in self.client.split(",")
                 if c.strip() and c.strip().lower() not in low]
        return f"{self.company} / {', '.join(extra)}" if extra else self.company

    @property
    def short_label(self) -> str:
        """Prose form: company plus at most one end client. Listing every client
        of a multi-account engagement turns a summary sentence into a roster."""
        full = self.label
        if " / " not in full:
            return full
        company, _, clients = full.partition(" / ")
        return f"{company} / {clients.split(',')[0].strip()}"

    def mentions(self, term: str, dimension: Optional[str] = None) -> bool:
        """Does this experience mention `term`?

        With a `dimension`, skill NAMES belonging to other dimensions are
        excluded from the haystack. Bullet prose is always searched — the guard
        exists for skill-name collisions, not for what the candidate wrote.

        Driver: the JD's network "routing" requirement matched the candidate's
        `LLM Provider Routing` skill and reported network routing as covered.
        Two unrelated meanings of one word; the dimensions say so.
        """
        if dimension is None:
            return term_present(term, self.corpus)
        return term_present(term, self.corpus_for(dimension))

    def corpus_for(self, dimension: str) -> str:
        """Haystack with only same-dimension skill names, plus all prose."""
        if dimension not in self._dimension_corpus:
            same = " ".join(self.facets.get(dimension, []))
            self._dimension_corpus[dimension] = normalize(" ".join(filter(None, [
                self.company, self.title, self.client or "", self.industry or "",
                same, " ".join(b["text"] for b in self.bullets),
            ])))
        return self._dimension_corpus[dimension]

    def bullets_mentioning(self, term: str) -> list[int]:
        return [b["idx"] for b in self.bullets if term_present(term, b["norm"])]


@dataclass
class EvidenceInventory:
    records: list[EvidenceRecord] = field(default_factory=list)
    skill_evidence: dict[str, list[str]] = field(default_factory=dict)  # skill->exp ids
    # (normalized bullet text, dimensions the project has evidence in)
    project_terms: list[tuple[str, frozenset]] = field(default_factory=list)
    credential_terms: set[str] = field(default_factory=set)

    def by_id(self, exp_id: str) -> Optional[EvidenceRecord]:
        return next((r for r in self.records if r.id == exp_id), None)

    def ids_with_skill(self, skill: str) -> list[str]:
        return list(self.skill_evidence.get(skill, []))

    def ids_mentioning(self, term: str, dimension: Optional[str] = None) -> list[str]:
        return [r.id for r in self.records if r.mentions(term, dimension)]

    def project_mentions(self, term: str, dimension: Optional[str] = None) -> bool:
        """Featured-project evidence, guarded the same way: a project only
        answers for a dimension it actually has catalogued skills in."""
        for text, dimensions in self.project_terms:
            if dimension is not None and dimension not in dimensions:
                continue
            if term_present(term, text):
                return True
        return False

    def dimension_skills(self, dimension: str) -> set[str]:
        out: set[str] = set()
        for rec in self.records:
            out.update(rec.facets.get(dimension, []))
        return out

    def industries(self) -> list[str]:
        seen: list[str] = []
        for rec in self.records:
            if rec.industry and rec.industry not in seen:
                seen.append(rec.industry)
        return seen


def build_inventory(bundle: DataBundle) -> EvidenceInventory:
    inv = EvidenceInventory()
    catalog = bundle.skills

    for exp in bundle.experiences_sorted():
        facets: dict[str, list[str]] = {}
        for skill in exp.skills:
            dim = CATEGORY_DIMENSION.get(catalog.category_of(skill) or "", "other")
            facets.setdefault(dim, []).append(skill)
            inv.skill_evidence.setdefault(skill, []).append(exp.id)

        bullets = [
            {"idx": i, "text": b.text, "tags": list(b.tags), "norm": normalize(b.text)}
            for i, b in enumerate(exp.bullets)
        ]
        corpus = normalize(" ".join(filter(None, [
            exp.company, exp.title, getattr(exp, "client", None) or "",
            getattr(exp, "industry", None) or "",
            " ".join(exp.skills), " ".join(b["text"] for b in bullets),
        ])))
        inv.records.append(EvidenceRecord(
            id=exp.id, company=exp.company, title=exp.title,
            client=getattr(exp, "client", None),
            industry=getattr(exp, "industry", None),
            engagement=exp.engagement, start=exp.start, end=exp.end,
            skills=list(exp.skills), facets=facets, bullets=bullets,
            corpus=corpus, exp=exp,
        ))

    for proj in bundle.projects:
        dimensions = set()
        for skill in proj.skills:
            inv.skill_evidence.setdefault(skill, []).append(f"project:{proj.id}")
            dimensions.add(CATEGORY_DIMENSION.get(catalog.category_of(skill) or "",
                                                  "other"))
        frozen = frozenset(dimensions)
        for b in proj.bullets:
            inv.project_terms.append((normalize(b.text), frozen))

    for group in bundle.training:
        for item in group.get("items", []) or []:
            if item.get("name"):
                inv.credential_terms.add(normalize(str(item["name"])))
    for cert in bundle.certifications:
        for value in cert.values():
            inv.credential_terms.add(normalize(str(value)))

    return inv
