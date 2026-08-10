"""ATS-safe PDF renderer (reportlab / Platypus).

Design goals: single column, standard base-14 font (Helvetica) so text is
always extractable and never rasterized, clean typographic hierarchy, real
selectable text, working URLs. No images, no icons, no multi-column layout,
and no tables used for positioning — right-aligned dates are drawn by a tiny
custom flowable, so the PDF content stream is plain left-to-right text.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from .layout import contact_line, present_sections
from .models import ResumeModel

INK = HexColor("#1A1A1A")
ACCENT = HexColor("#1F2D3D")
MUTED = HexColor("#555555")
RULE = HexColor("#9AA5B1")

MARGIN = 0.6 * inch

_STYLES = {
    "name": ParagraphStyle(
        "name", fontName="Helvetica-Bold", fontSize=21, leading=24,
        textColor=ACCENT, spaceAfter=1,
    ),
    "headline": ParagraphStyle(
        "headline", fontName="Helvetica-Bold", fontSize=11.5, leading=14,
        textColor=INK, spaceAfter=1,
    ),
    "tagline": ParagraphStyle(
        "tagline", fontName="Helvetica", fontSize=10, leading=13,
        textColor=MUTED,
    ),
    "contact": ParagraphStyle(
        "contact", fontName="Helvetica", fontSize=9.3, leading=12,
        textColor=MUTED, spaceAfter=2,
    ),
    "section": ParagraphStyle(
        "section", fontName="Helvetica-Bold", fontSize=11, leading=13,
        textColor=ACCENT, spaceBefore=9, spaceAfter=2,
    ),
    "body": ParagraphStyle(
        "body", fontName="Helvetica", fontSize=9.8, leading=12.4,
        textColor=INK, alignment=TA_LEFT,
    ),
    "title": ParagraphStyle(
        "title", fontName="Helvetica-Bold", fontSize=10.3, leading=12.6,
        textColor=INK,
    ),
    "sub": ParagraphStyle(
        "sub", fontName="Helvetica-Oblique", fontSize=9.5, leading=12,
        textColor=MUTED, spaceAfter=1,
    ),
    "bullet": ParagraphStyle(
        "bullet", fontName="Helvetica", fontSize=9.8, leading=12.2,
        textColor=INK, leftIndent=13, bulletIndent=2, spaceAfter=1.5,
    ),
    "skill": ParagraphStyle(
        "skill", fontName="Helvetica", fontSize=9.8, leading=12.6,
        textColor=INK, spaceAfter=1,
    ),
}


class TitleDateLine(Flowable):
    """One line: left title (bold) + right-aligned date. Not a table."""

    def __init__(self, left: str, right: str, width: float,
                 *, left_size=10.3, bold=True, right_size=9.3):
        super().__init__()
        self.left = left
        self.right = right
        self.width = width
        self.left_size = left_size
        self.bold = bold
        self.right_size = right_size
        self.height = left_size + 3

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return availWidth, self.height

    def draw(self):
        c: Canvas = self.canv
        y = self.height - self.left_size
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold" if self.bold else "Helvetica", self.left_size)
        c.drawString(0, y, self.left)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", self.right_size)
        c.drawRightString(self.width, y, self.right)


def _content_width() -> float:
    return A4[0] - 2 * MARGIN


def render_pdf(resume: ResumeModel, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"{resume.name} — {resume.headline}",
        author=resume.name,
    )
    width = _content_width()
    story: list = []

    story.append(Paragraph(resume.name, _STYLES["name"]))
    story.append(Paragraph(resume.headline, _STYLES["headline"]))
    if resume.tagline:
        story.append(Paragraph(resume.tagline, _STYLES["tagline"]))
    story.append(Paragraph(_escape(contact_line(resume)), _STYLES["contact"]))

    for key, title in present_sections(resume):
        story.append(Paragraph(title, _STYLES["section"]))
        story.append(HRFlowable(width="100%", thickness=0.6, color=RULE,
                                spaceBefore=1, spaceAfter=4))
        if key == "summary":
            story.append(Paragraph(_escape(resume.summary), _STYLES["body"]))
        elif key == "skills":
            for g in resume.skill_groups:
                text = f"<b>{_escape(g.name)}:</b> {_escape(', '.join(g.items))}"
                story.append(Paragraph(text, _STYLES["skill"]))
        elif key == "experience":
            for exp in resume.experiences:
                if exp.condensed:
                    left = f"{exp.title}, {exp.company}"
                    story.append(TitleDateLine(left, exp.meta_right, width,
                                               left_size=9.8))
                    story.append(Spacer(1, 3))
                    continue
                story.append(TitleDateLine(exp.title, exp.meta_right, width))
                sub = " · ".join(
                    x for x in [exp.company, exp.engagement_label, exp.location] if x
                )
                story.append(Paragraph(_escape(sub), _STYLES["sub"]))
                for b in exp.bullets:
                    story.append(Paragraph(_escape(b), _STYLES["bullet"],
                                           bulletText="•"))
                story.append(Spacer(1, 3))
        elif key == "projects":
            for proj in resume.featured_projects:
                story.append(TitleDateLine(proj.name, proj.label, width))
                if proj.tagline:
                    story.append(Paragraph(_escape(proj.tagline), _STYLES["sub"]))
                for b in proj.bullets:
                    story.append(Paragraph(_escape(b), _STYLES["bullet"],
                                           bulletText="•"))
                story.append(Spacer(1, 3))
        elif key == "education":
            for e in resume.education:
                story.append(Paragraph(
                    _escape(" · ".join(str(v) for v in e.values() if v)),
                    _STYLES["body"]))
        elif key == "certifications":
            for cert in resume.certifications:
                story.append(Paragraph(
                    _escape(" · ".join(str(v) for v in cert.values() if v)),
                    _STYLES["body"]))

    doc.build(story)
    return out_path


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
