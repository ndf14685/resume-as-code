# CV → canonical dataset reconciliation

**Date:** 2026-08-25
**Canonical source:** `data/*.yaml` in this repository
**Reconciled against:** the candidate's real CV, as supplied by the candidate
**Trigger:** the Logicalis "Sr Cloud Azure Infraestructura Engineer" regression,
where the generated CV could not surface Azure evidence that exists in the
candidate's history because the canonical record did not hold it.

Nothing here was changed silently. Every row states what the CV says, what the
dataset held, what it holds now, and on what evidence.

## Classification vocabulary

| Action | Meaning |
| --- | --- |
| `CONFIRMED` | CV and canonical already agreed; no value changed |
| `CORRECTED` | canonical held a wrong value; replaced on CV evidence |
| `ADDED` | canonical held nothing; recorded from CV evidence |
| `NEEDS_CONFIRMATION` | recorded but NOT claimable; evidence is weak (e.g. a badge image with no issuing record) |
| `UNCHANGED` | difference is cosmetic (language, label) — no change made |
| `CONFLICT` | CV and canonical disagree in a way that cannot be resolved without the candidate; **canonical left untouched** |

`CONFIDENCE` is `high` when the candidate stated the fact directly, `medium`
when it is a faithful translation/derivation of something they stated, and
`low` when the only trace is a visual artefact.

---

## 1. Experience

| Field / experience | CV value | Canonical before | Canonical after | Action | Evidence | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| `telecom_lead` — start | `03/2018` | `2018-03` | `2018-03` (unchanged) | `CONFIRMED` | candidate's CV | high |
| `telecom_lead` — approximation marker | none | **none** (`approximate_start` was never set on any record) | none | `CONFIRMED` | inspected `data/experience.yaml`; no record carries the field | high |
| README claim "Telecom start is approximate (`~2018`)" | n/a | stale documentation asserting an approximation that the data never had | struck through and corrected in `README.md` | `CORRECTED` | the data itself | high |
| `telecom_lead` — role label | `Desarrollo SOA / Área Técnica - Configuration Manager - Referente Técnico` | `Technical Lead / Configuration Manager / SOA` | unchanged; the Spanish CV label is recorded in the entry's `note` | `UNCHANGED` | faithful translation; "Referente Técnico" is already stated in a canonical bullet ("acted as technical reference for the team") | medium |
| `telecom_support` — end | `12/2022` | `2022-12` | `2022-12` (unchanged) | `CONFIRMED` | candidate's CV | high |
| **Telecom — shape of the engagement** | ONE continuous block, `03/2018 – 12/2022` | TWO records: `telecom_lead` full-time `2018-03 → 2020-02`, plus `telecom_support` part-time `2020-02 → 2022-12`, whose note states the part-time work overlapped Equifax, La Nación and INGENIA | unchanged — **two records**, now marked `SHAPE CONFIRMED` in both notes | `CONFIRMED` | raised as a `CONFLICT` and **resolved by the candidate on 2026-08-25: two blocks, not one**. The CV renders the pair as a single span; the split is the canonical truth | high |
| `pichincha` — engagement | Freelance | `contract` | `freelance` | `CORRECTED` | candidate's CV | high |
| `pichincha` — stack | Azure + Terraform; OpenShift 4; CNCF evaluation in the DevOps Chapter; CI/CD in banking | `[Secure SDLC, Release Automation]` — README: "stack/scope kept general (specifics not provided)" | `[Azure, Terraform, Red Hat OpenShift, Secure SDLC, Release Automation]` + 5 bullets | `ADDED` | candidate's CV | high |
| `fluxit` — mobile scope | CI/CD de aplicaciones Android/iOS | bullet said only "cross-browser testing" | bullet now names Android and iOS explicitly | `ADDED` | candidate's CV; BrowserStack was already canonical | high |
| `fluxit` — Azure / Azure DevOps | present | present, but compressed into one multi-cloud clause | split so the Azure delivery platform is its own bullet | `CONFIRMED` | already canonical; only the granularity changed | high |
| `ingenia` — Mercantil Andina Azure/AKS/Terraform/Azure DevOps/Bitbucket | present | present, compressed into a single line | split into an architecture bullet and a pipelines bullet, both prefixed "Mercantil Andina:" | `CONFIRMED` | already canonical | high |
| `ingenia` — Geopagos | AWS, Kubernetes, Terraform, Airflow, GitLab | present | unchanged, kept in its own "Geopagos:" bullet | `UNCHANGED` | already canonical | high |
| `client` on 5 records | implied by the CV | absent | `Mercantil Andina, Geopagos` (ingenia), `La Nación` (fluxit), `Clarín` (agea), `Banco HSBC` (hsbc), `Banco Credicoop` (credicoop) | `ADDED` | already present inside company names and bullets; promoted to a field so retrieval can use it | high |
| `industry` on all 13 records | implied by the CV | absent | Banking / Insurance / Media / Telecommunications / Financial Services / Technology Consulting / Insurance & Payments | `ADDED` | classification of employers already named in canonical data | medium |

### Note on Mercantil Andina vs Geopagos

Both were clients of the same employer (INGENIA), so they share one experience
record. The separation lives at bullet level and is enforced by test: no bullet
naming Geopagos may mention Azure, and no bullet naming Mercantil Andina may
claim the AWS/GitLab stack. Geopagos does **not** inherit Azure.

---

## 2. Training and certifications

The four kinds of evidence are modelled separately and never convert into one
another. `data/training.yaml` holds `training` and `course`; `certifications.yaml`
holds `certification` and `badge`. The loader rejects a file that mixes them.

| Item | Provider | Year | CV value | Canonical before | Canonical after | Action | Confidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CyberOps | Cisco Networking Academy | 2024 | listed under "SeaCCNA / Cisco" | present, untyped | `type: training` | `CONFIRMED` | high |
| Red Team | Hackademy | 2025 | present | present, untyped | `type: training` | `CONFIRMED` | high |
| OSINT | Hackademy | 2024 | present | present, untyped | `type: training` | `CONFIRMED` | high |
| DevSecOps & Cloud Security | Hackademy | 2022 | present | present, untyped | `type: training` | `CONFIRMED` | high |
| Fundamentals of Hacking and Defense | Hackademy | 2022 | present | present, untyped | `type: training` | `CONFIRMED` | high |
| Data Science | MundoSE | 2023 | present | present, untyped | `type: training` | `CONFIRMED` | high |
| DevOps | MundoSE | 2022 | present | present, untyped | `type: training` | `CONFIRMED` | high |
| Docker | Udemy | — | present | present, untyped | `type: course` | `CONFIRMED` | high |
| Kubernetes | Udemy | — | present | present, untyped | `type: course` | `CONFIRMED` | high |
| Airflow | Udemy | — | present | present, untyped | `type: course` | `CONFIRMED` | high |
| Linux | Educación IT | — | present | present, untyped | `type: course` | `CONFIRMED` | high |
| Java | Educación IT | — | present | present, untyped | `type: course` | `CONFIRMED` | high |
| Provider label "SeaCCNA / Cisco" | — | — | CV writes "SeaCCNA / Cisco" | `Cisco Networking Academy` | unchanged; CV label recorded as a comment | `UNCHANGED` | high |
| **AWS Certified Cloud Practitioner** | Amazon Web Services | not recorded | badge rendered in the visual CV, confirmed expired by the candidate | `certifications: []` | `type: certification`, `status: expired`, no dates | `CONFIRMED` (as expired) | high |
| **Cisco Networking Academy** (badge) | Cisco | unknown | badge rendered in the visual CV | `certifications: []` | `type: badge`, `status: needs_confirmation` | `NEEDS_CONFIRMATION` | low |

Only `status: confirmed` credentials are rendered into a CV or visible to JD
matching. Neither entry above is claimable.

* **AWS Certified Cloud Practitioner** — the candidate confirmed on 2026-08-25
  that it has **expired**, and supplied no dates. It is recorded as
  `status: expired` with no `issued`/`expires` field, and the loader rejects the
  file if a date is ever added without one actually being supplied. Keeping the
  record is honest; rendering it would claim a live credential.
* **Cisco Networking Academy badge** — deliberately left at
  `needs_confirmation` by the candidate on 2026-08-25. The documented study
  record behind it (CyberOps 2024) stays a `training` entry and is not promoted.

---

## 3. Education

| Field | CV value | Canonical before | Canonical after | Action | Evidence | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| Formal education | no institution, degree or dates supplied | `education: []` | `education: []` + explicit `education_status: unknown_incomplete` | `UNCHANGED` | none available | — |

Education is **UNKNOWN / INCOMPLETE**, which is not the same as "none". Training
and courses are never used to infer it: an academy program is not a degree. The
Education section stays out of generated CVs until a real record exists.

---

## 4. Technologies that remain gaps

None of the following were added to any skill, experience or certification,
because no evidence for them exists in any source. They continue to surface as
gaps whenever a JD requires them:

Microsoft Entra ID · Veeam · Azure Landing Zones · Cloud Adoption Framework ·
Private Endpoints · Conditional Access · ExpressRoute · Network Security Groups ·
Virtual Networks / VNet · Windows Server · AZ-104 · AZ-305 · AZ-700 · VMCE

They are not all the same kind of gap:

* **UNSUPPORTED** — the dimension is evidenced and this is not part of it.
  Windows Server is unsupported because the candidate's operating-system
  evidence exists and is Linux. Azure Landing Zones and Entra ID are
  unsupported because cloud-platform and identity evidence exists without them.
* **UNKNOWN** — no source covers the dimension at all, so a negative claim would
  be as unfounded as a positive one. Veeam, backup and disaster recovery are
  unknown because the dataset models no backup/DR facet whatsoever. AZ-104,
  AZ-305, AZ-700 and VMCE are unknown because the certification source is
  declared incomplete.

Both are gaps, neither is claimable, and both score zero. The distinction
governs what may be *said*, not the arithmetic.

---

## 5. Defect found while auditing the score

The match breakdown surfaced a false positive that predates this reconciliation:
the JD's **network routing** requirement was being satisfied by the candidate's
`LLM Provider Routing` skill from the NexusOS project — two unrelated meanings
of one word. Verbatim text matching is now dimension-guarded: a skill name can
only answer for a requirement in its own dimension. Bullet prose is still
searched freely; the guard exists for skill-name collisions.

Effect on the Logicalis vacancy: `routing` moved from supported to a networking
gap, and the reported match went from 31% to 30%. Nothing else changed.

---

## 6. Resolved by the candidate — 2026-08-25

1. **Telecom shape** — `RESOLVED`. Two blocks, not one: the full-time lead role
   `2018-03 → 2020-02` and the separate part-time out-of-hours engagement
   `2020-02 → 2022-12`. The canonical split stands and is now marked
   `SHAPE CONFIRMED`; a test fails if the two records are ever collapsed.
2. **AWS Certified Cloud Practitioner** — `RESOLVED`. Held but **expired**, and
   no dates were supplied. Recorded as `status: expired`, never rendered, never
   claimed.

## 7. Open items — deliberately left incomplete

The candidate asked on 2026-08-25 not to complete these. Neither blocks
anything, and nothing downstream depends on them.

3. **Cisco Networking Academy badge** (`NEEDS_CONFIRMATION`) — what the badge
   certifies, and whether it should stay a badge or fold into the CyberOps
   training record. Not claimable meanwhile.
4. **Education** (`UNKNOWN`) — no institution, program or dates recorded. Stays
   UNKNOWN/INCOMPLETE, which is not a claim that there is none, and training is
   never used to infer it.
