# resume-as-code

**ATS-first CV/resume generation from a single, versioned source of truth.**

One canonical record of a professional career lives in `data/`. From it, the
tool renders multiple tailored, ATS-safe CVs — DevOps, DevSecOps, AI Systems
Architect, or a variant tuned to a specific job description — as **DOCX, PDF and
TXT**. No Canva, no manual layout, no LLM API key required for the base system.

The core design constraint: **facts are protected from the presentation
layer.** Tailoring can select, reorder and emphasize — it can never invent a
company, a date, an engagement type, a metric, or a skill.

---

## Why

Maintaining a dozen slightly-different CVs by hand is error-prone and drifts
from the truth. This project treats a career like source code:

- **Single source of truth** — companies, dates, engagement types, titles and
  the *real* technologies used are stored once, structured and validated.
- **Reproducible builds** — the same data always produces the same CV.
- **Tailoring without lying** — a profile or a job description changes what is
  *shown and emphasized*, never what is *claimed*.
- **ATS as a first-class requirement** — every generated PDF is checked with
  real text-extraction tests, not just declared "ATS compatible".

## Architecture

```
data/                     ← SINGLE SOURCE OF TRUTH (facts; public-safe)
  profile.yaml            ← name, location, links (NO email — see Privacy)
  skills.yaml             ← canonical skills catalog + aliases (authoritative)
  experience.yaml         ← roles: company, title, dates, engagement, skills, bullets
  projects.yaml           ← featured/independent projects (e.g. NexusOS)
  education.yaml          ← (empty until provided; never invented)
  certifications.yaml     ← (empty until provided; never invented)

profiles/                 ← PRESENTATION ONLY (select / order / emphasize)
  devops.yaml  devsecops.yaml  ai-architect.yaml

jobs/                     ← job descriptions to tailor against (e.g. ecomflow.txt)

src/resume_as_code/
  models.py     ← pydantic schema + canonical fingerprint (tamper detection)
  loader.py     ← load + validate data; resolve email safely
  tailor.py     ← build a ResumeModel from data + a profile (selection only)
  jobmatch.py   ← deterministic JD analysis (covered / transferable / gap)
  render_pdf.py ← ATS-safe PDF (reportlab, selectable text, no layout tables)
  render_docx.py← ATS-safe DOCX (python-docx, single column, no tables)
  render_txt.py ← plain text (what a naive parser sees)
  validate.py   ← real ATS checks on the generated PDF
  cli.py        ← the `resume` command

generated/                ← outputs (git-ignored; embeds the resolved email)
tests/                    ← schema, integrity, tailoring & ATS tests
```

**Data flow:** `data/*.yaml → load+validate → tailor(profile [+ job]) →
ResumeModel → render(pdf|docx|txt) → validate(pdf)`.

## Quickstart

```bash
# 1. Environment (Python 3.10+)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Validate the canonical data and see the fingerprint
./resume check

# 3. Generate a CV (writes PDF + DOCX + TXT to generated/)
./resume generate --profile devops

# 4. Run ATS validation on the PDF
./resume validate generated/Nestor_Fleitas_DevOps.pdf
```

The `./resume` wrapper uses the project virtualenv automatically. You can also
`pip install -e .` and call `resume` directly.

## Editing the canonical data

All facts live in `data/`. To add or change a role, edit `data/experience.yaml`:

```yaml
- id: acme
  company: ACME Corp
  title: Staff Platform Engineer
  engagement: full_time        # full_time|contract|freelance|part_time|consulting|null
  start: "2025-03"             # "YYYY-MM", or "YYYY" if the month is unknown
  end: present                 # or "YYYY-MM" / "YYYY"
  skills: [AWS, Terraform]     # MUST all exist in data/skills.yaml
  bullets:
    - text: What you actually did, using the tech above.
      tags: [cloud, iac]       # tags drive profile/job bullet selection
```

Rules enforced by the loader (`./resume check` fails otherwise):

- Every skill referenced by an experience/project **must exist** in
  `data/skills.yaml`. New skill → add it to the catalog first.
- Every catalog skill **must have evidence** (be used somewhere). Unused skills
  are reported so the catalog can't accumulate empty claims.
- Experience ids are unique.

Dates are never "fixed" to look linear. Overlapping engagements are represented
honestly via distinct entries and engagement labels (e.g. a `contract`
engagement running alongside a `full_time` role).

## Adding a new profile

Copy an existing file in `profiles/` and adjust. A profile is **presentation
only**:

```yaml
name: sre
headline: Senior Site Reliability Engineer
tagline: Reliability · Observability · Incident Response
summary: >-
  A human-authored summary. Only claim capabilities backed by the data.
skill_priority: [reliability, cloud, containers, cicd, iac]  # category order
emphasis_tags: [sre, reliability, incident, observability]   # bullet selection
max_bullets: 3
condense_before: "2019-01"   # older roles collapse to a one-line entry
include_projects: []          # e.g. [nexusos]
```

Then: `./resume generate --profile sre`.

## Job-specific generation

Drop a job description into `jobs/` and tailor a base profile against it:

```bash
./resume generate --profile devops --job jobs/ecomflow.txt
```

This produces the CV **and** a transparency report,
`generated/<job>-analysis.md`, with:

- **Requirements covered** — canonical skills found in the JD, with evidence.
- **Partially covered / transferable** — JD tools we do *not* claim, mapped to
  the related canonical skill that constitutes honest transferable evidence
  (e.g. *PlanetScale → Amazon RDS*).
- **Gaps** — required but unevidenced; explicitly omitted.
- **Terms incorporated / deliberately NOT incorporated** — the anti
  keyword-stuffing ledger.

The analysis is **deterministic** (keyword/evidence matching against the
catalog). It never adds a skill that isn't already in the canonical data.

### Optional AI tailoring (decoupled, no API key)

The base system needs no LLM. If you want an AI to refine wording, the
interface is deliberately decoupled: export the data/analysis, refine text
*externally* (Claude, Codex, etc.), and paste the reviewed result back into a
profile's `summary` or a bullet's `text`. The canonical facts and the
fingerprint remain the guardrail — the schema and validator reject anything
that introduces an unknown skill or an invented date.

## ATS validation

`./resume validate <pdf>` runs technical checks on the actual file:

```
PASS: text extraction
PASS: selectable text (not rasterized)
PASS: no decorative images
PASS: heading parsing & order
PASS: experience chronology
PASS: canonical dates (no invented years)
PASS: URLs are selectable text
PASS: no invented/unclaimed skills
PASS: company names recoverable
PASS: no layout tables (DOCX)
PASS: page count <= 2
```

The validator cross-checks the extracted text against `data/`, so a tampered
file (an invented year, or an unclaimed tool injected into Core Skills) fails.

## ATS design decisions

The renderers deliberately produce a simple, single-column, professional
document: standard fonts (Helvetica in PDF, Calibri in DOCX), clear headings,
real text, real bullets, parseable dates and company names, and working URLs as
text. **No** photo, logos, icons, skill bars, charts, columns, sidebars, text
boxes, or tables used for positioning. Right-aligned dates in the PDF are drawn
by a tiny custom flowable, so the content stream stays plain left-to-right text.

## Privacy & public-repo safety

This repo is intended to be safe to publish. It contains **no** date of birth,
national ID, home address, private phone, secrets, tokens, or client-
confidential data — only professional identity already used publicly.

**Email is never committed.** It is resolved at generation time from, in order:

1. the `RESUME_EMAIL` environment variable, or
2. `data/contact.local.yaml` (git-ignored), or
3. omitted (the CV is generated without an email line, with a warning).

`generated/` is git-ignored because the built CVs embed the resolved email.

## Anti-hallucination policy

The system may **select, reorder, emphasize and reword within the facts**. It
may **never**:

- invent experience, metrics, technologies, or credentials;
- change dates to look more linear, or extend a tenure;
- turn a contractor engagement into full-time;
- claim a skill without evidence in the canonical data.

Enforcement: a pydantic schema, a skill-integrity check (references must exist
in the catalog), an evidence check (catalog skills must be used), a canonical
fingerprint over the fact-bearing fields, deterministic job matching, and ATS
validation that re-checks the rendered file against the data.

## Testing

```bash
pytest        # schema, canonical integrity, tailoring, end-to-end ATS checks
```

## Known gaps / pending factual validation

Nothing below is invented; these are simply unconfirmed and are handled
conservatively (omitted or left unlabeled) rather than guessed:

- **Engagement type** is unlabeled (`null`) for AGEA/Clarín, INGENIA, Flux
  IT/La Nación, Equifax, HSBC, Credicoop and Telefónica — confirm and set.
- **Telecom (lead role)** start is approximate (`~2018`, month unknown).
- **Banco Pichincha** stack/scope is kept general (specifics not provided).
- **Education** and **Certifications** are empty (none provided) and are
  omitted from CVs until real entries are added.

## License

MIT — see [LICENSE](LICENSE).
