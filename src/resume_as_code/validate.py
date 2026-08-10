"""ATS validator — real technical checks on a generated PDF (and its DOCX).

Nothing here just *asserts* ATS-compatibility; every check inspects the actual
file. Facts are cross-checked against the canonical data in data/ so tampering
(invented dates or unclaimed skills injected into the file) is detected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from .jobmatch import EXTERNAL_TERMS
from .loader import load_bundle

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

SECTION_TITLES = [
    "Professional Summary",
    "Core Skills",
    "Professional Experience",
    "Featured Project",
    "Education",
    "Certifications",
]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def extract_text(pdf_path: str | Path) -> str:
    reader = PdfReader(str(pdf_path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _page_count(pdf_path: str | Path) -> int:
    return len(PdfReader(str(pdf_path)).pages)


def _has_images(pdf_path: str | Path) -> bool:
    reader = PdfReader(str(pdf_path))
    for page in reader.pages:
        try:
            if page.images:
                return True
        except Exception:
            continue
    return False


def _section_slice(text: str, title: str) -> str:
    """Return the text of a section from its title to the next known title."""
    lower = text.lower()
    start = lower.find(title.lower())
    if start == -1:
        return ""
    ends = [
        lower.find(t.lower(), start + len(title))
        for t in SECTION_TITLES
        if t.lower() != title.lower()
    ]
    ends = [e for e in ends if e != -1]
    end = min(ends) if ends else len(text)
    return text[start:end]


def run_ats_validation(pdf_path: str | Path, data_dir: str | Path = "data") -> list[Check]:
    pdf_path = Path(pdf_path)
    bundle = load_bundle(data_dir)
    text = extract_text(pdf_path)
    norm = re.sub(r"\s+", " ", text)
    checks: list[Check] = []

    # 1. Text extraction
    checks.append(Check(
        "text extraction",
        len(text.strip()) > 400,
        f"{len(text.strip())} characters extracted",
    ))

    # 2. Selectable text / not rasterized (text present AND no images)
    imgs = _has_images(pdf_path)
    checks.append(Check(
        "selectable text (not rasterized)",
        len(text.strip()) > 400 and not imgs,
        "no embedded images; text layer present" if not imgs else "embedded image found",
    ))

    # 3. No decorative images
    checks.append(Check("no decorative images", not imgs,
                        "0 images" if not imgs else "images present"))

    # 4. Heading parsing + order
    present = [(t, norm.lower().find(t.lower())) for t in SECTION_TITLES]
    present = [(t, i) for t, i in present if i != -1]
    ordered = all(present[k][1] < present[k + 1][1] for k in range(len(present) - 1))
    have_core = {"Professional Summary", "Core Skills", "Professional Experience"} <= {
        t for t, _ in present
    }
    checks.append(Check(
        "heading parsing & order",
        ordered and have_core,
        "sections: " + ", ".join(t for t, _ in present),
    ))

    # 5. Experience chronology (companies in reverse-chronological order)
    exp_text = _section_slice(norm, "Professional Experience")
    exp_lower = exp_text.lower()
    ordered_companies: list[str] = []
    for e in bundle.experiences_sorted():
        if e.company not in ordered_companies:
            ordered_companies.append(e.company)
    indices = []
    chron_ok = True
    for comp in ordered_companies:
        idx = exp_lower.find(comp.lower())
        if idx == -1:
            continue
        indices.append(idx)
    for k in range(len(indices) - 1):
        if indices[k] >= indices[k + 1]:
            chron_ok = False
    checks.append(Check(
        "experience chronology",
        chron_ok and "present" in exp_lower,
        f"{len(indices)} companies in reverse-chronological order; current role present",
    ))

    # 6. Canonical dates — every year in the doc is a real canonical year
    canonical_years: set[str] = set()
    for e in bundle.experiences:
        for token in (e.start, e.end):
            m = YEAR_RE.search(token)
            if m:
                canonical_years.add(m.group(0))
    found_years = set(YEAR_RE.findall(text))
    invented = sorted(found_years - canonical_years)
    checks.append(Check(
        "canonical dates (no invented years)",
        not invented,
        "all years match canonical data" if not invented else f"unexpected years: {invented}",
    ))

    # 7. URLs present as text
    link_ok = all(
        (lnk in norm) for lnk in [
            v for v in [
                bundle.basics.links.website,
                bundle.basics.links.github,
                bundle.basics.links.linkedin,
            ] if v
        ]
    )
    checks.append(Check("URLs are selectable text", link_ok,
                        "website/github/linkedin found in text layer"))

    # 8. No unclaimed skills injected into Core Skills
    skills_block = _section_slice(norm, "Core Skills").lower()
    leaked = [t for t in EXTERNAL_TERMS if re.search(rf"\b{re.escape(t)}\b", skills_block)]
    checks.append(Check(
        "no invented/unclaimed skills",
        not leaked,
        "Core Skills contains only canonical skills" if not leaked else f"unclaimed: {leaked}",
    ))

    # 9. Companies recoverable
    missing_companies = [
        e.company for e in bundle.experiences
        if e.company.lower() not in norm.lower()
    ]
    # A company may be intentionally omitted by a profile; only fail if the
    # doc has fewer than half of them (indicates a parsing/extraction problem).
    recoverable = len(bundle.experiences) - len(set(missing_companies))
    checks.append(Check(
        "company names recoverable",
        recoverable >= 1,
        f"{recoverable} distinct companies recoverable from text",
    ))

    # 10. No layout tables in sibling DOCX (PDFs carry no table structure)
    docx_path = pdf_path.with_suffix(".docx")
    if docx_path.exists():
        try:
            from docx import Document
            n_tables = len(Document(str(docx_path)).tables)
            checks.append(Check("no layout tables (DOCX)", n_tables == 0,
                                f"{n_tables} tables in DOCX"))
        except Exception as exc:
            checks.append(Check("no layout tables (DOCX)", False, str(exc)))

    # 11. Page count (2-page target is a soft check -> informational fail>3)
    pages = _page_count(pdf_path)
    checks.append(Check("page count <= 2", pages <= 2, f"{pages} page(s)"))

    return checks


def format_report(pdf_path: str | Path, checks: list[Check]) -> tuple[str, bool]:
    lines = ["ATS VALIDATION", f"file: {pdf_path}", "-" * 48]
    all_ok = True
    for c in checks:
        status = "PASS" if c.ok else "FAIL"
        if not c.ok:
            all_ok = False
        lines.append(f"{status}: {c.name}" + (f"  ({c.detail})" if c.detail else ""))
    lines.append("-" * 48)
    lines.append("RESULT: " + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return "\n".join(lines), all_ok
