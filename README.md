# MOLECULAB

**An evidence-grounded research environment for early-stage drug discovery and pharmaceutical
formulation.**

A researcher investigating a single compound today moves between a structure database, a
literature index, an activity database, a clinical-trials registry and a spreadsheet. None of them
talk to each other, so most of the day goes on copying between tabs and trying not to lose track
of where each fact came from.

MOLECULAB puts that work on one bench, keeps every value attached to its source — and refuses to
answer when the evidence cannot support an answer.

---

## What makes it different

Most research tools show you a number and let you assume it was measured. MOLECULAB never confuses
a computation with a measurement, and it says "I don't know" rather than guessing.

**Every value carries its epistemic status.** `experimental` · `derived` · `computed` ·
`predicted` · `hypothesis` · `unverified` · `contradiction` · `missing` — each with a colour, a
glyph *and* a word, so the distinction survives greyscale and a screenshot. A computed descriptor
cannot satisfy a physical formulation constraint. Missing data is shown as missing, never as zero
and never silently estimated.

**The AI can only lose a claim, never promote one.** Its draft is split into atomic claims and
each is checked against the record it cites. Claims the record does not support are *deleted*, not
softened. Ask the assistant something the indexed evidence cannot answer and it abstains, naming
what the records do and do not contain.

**It refuses, with the reason on screen.** Three capabilities are deliberately unavailable:

| Capability | Why it returns nothing |
|---|---|
| **Molecular docking** | No docking engine, no prepared receptors, no binding sites. A docking score is the easiest plausible-looking number to fabricate and one of the hardest to challenge. |
| **QSAR / activity prediction** | Gated on evidence. In the curated JAK1 dataset (ChEMBL_37, 7,777 compounds) **6,774 have a single unreplicated measurement**, and of the 1,003 measured more than once, **263 (26%) disagree with themselves by tenfold or more**. No honest model ships on that noise floor. |
| **Dissolution / bioavailability prediction** | No validated model, and no measured dissolution profiles to validate one against. |

Where docking would normally go, MOLECULAB shows something stronger: **real experimental
protein–ligand structures** from the RCSB PDB, with the contacts actually observed in the crystal.
For `1M17` (EGFR + erlotinib) it measures the Met769 hinge hydrogen bond at **2.70 Å** — a result
you can check against the literature, which a docking score never is.

---

## What it does

### Research Bench — the front door
One workspace, seven stages in plain words:
`Research → Evidence → Assist → Explore → Calculate → Compare → Record → Report`.
You describe a goal in your own words; a stage is marked complete only when something real is in
it. A session *is* a research workspace, so notes, hypotheses, attached records and reports all
hang off it.

### Molecular Playground — build in 2D, explore in 3D
Place atoms, drag atom-to-atom to bond, change bond orders, undo and redo. The 3D conformer
rebuilds itself as you draw; rotate, zoom and click any atom for its element and coordinates.
**The editor is never the chemistry authority** — every structure is re-parsed by RDKit on the
backend. Give a carbon five bonds and it is rejected with the reason, and nothing is computed.

### Computational sandbox — 16 real calculations
Each declares its purpose, inputs, method, assumptions and limitations, and every run returns a
**SHA-256 reproducibility fingerprint** over the calculation id, exact inputs and software
versions.

| Category | Calculations |
|---|---|
| Molecular | descriptors · Lipinski · structure validation · conformer ensemble · similarity · Murcko scaffold · **molecular dynamics** · **implicit solvent** |
| Activity | measured activity distribution · assay comparability |
| Formulation | composition totals · unit conversion |
| Structural | **protein–ligand contacts** (experimental structures) |
| Dosing | **pharmacokinetics** · **whole-body PBPK** · dilution |

Molecular dynamics is velocity-Verlet integration on the MMFF94 potential with a Berendsen
thermostat — real time evolution, with the trajectory playable in the 3D viewer. The PK engine was
validated against the closed-form solutions (half-life exact, AUC within 0.04%), and the PBPK
model conserves mass to **0.00000%** across all five species, a figure it publishes on every run
so you can check the integration rather than trust it.

### Evidence with provenance
Eight live providers — ChEMBL, Open Targets, PubChem, PubMed, Crossref, GBIF, ClinicalTrials.gov,
DailyMed. Every record keeps its source, retrieval time and release identifier. An attached record
is a pointer, never a copy: the scientific corpus is never modified.

---

## Running it

Requires **Python 3.14** and **Node 24** (Node is only used by the frontend test harness).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Linux/macOS: .venv/bin/python
./scripts/start_backend.sh
```

Open **http://127.0.0.1:8765/**.

The repository ships with `moleculab.db`, a working database so the application is useful
immediately: 5 plants, 23 compounds, 20 publications, 37 evidence records, 4 materials and a
worked tablet formulation. To rebuild it from the seed data instead:

```bash
.venv/Scripts/python -m scripts.seed
.venv/Scripts/python -m alembic upgrade head
```

### Tests

```bash
.venv/Scripts/python -m pytest -q                        # 542 passed, 14 skipped
LLM_PROVIDER=none ./scripts/start_backend.sh             # then, in another shell:
node tests/frontend_smoke.mjs http://127.0.0.1:8765      # 40/40
```

Run the frontend smoke suite against a server with `LLM_PROVIDER=none`: two assistant checks
assert the exact wording of the deterministic extractive answer, and a live model rewrites it.

The 14 skips are opt-in live-provider tests (`MOLECULAB_LIVE=1`).

### AI assistance (optional)

Generation is optional and the application is fully usable without it — when no model is
configured the assistant reports itself unavailable rather than producing a simulated answer.

Two providers are supported: a local **Claude Code CLI** bridge (`LLM_PROVIDER=claude_cli`, no API
key, all tools disabled, empty sandbox working directory), or the Anthropic API
(`LLM_PROVIDER=anthropic` with `LLM_API_KEY`). Credentials stay server-side and are never sent to
the browser.

---

## Architecture

```
Browser — vanilla JS, hash router, NO build step, works offline
  22 view modules · design tokens · epistemic-status vocabulary
        ▼
FastAPI — thin routers: validate, call an engine, serialise
  optional routers mount defensively; a broken module reports itself
  instead of taking down the application
        ▼
Engines — pure Python, no web dependency, independently testable
  molecules · activity · screening · simulations · dynamics · pbpk
  pharmacokinetics · formulation · structures · claims · verification
        ▼
SQLAlchemy 2.x + Alembic — 33 tables, SQLite (PostgreSQL-ready)
        ▼
Provider clients — all subclass SourceClient, cached, rate-limited, never raise
```

The engine/transport split is the load-bearing decision: engines take a session and plain
arguments and return plain data, so the science is testable without a web server.

**Structural guarantees enforced by schema and test:** no column containing "predict" exists in
any activity-measurement table, so a prediction cannot be written where a measurement is read;
IC50 is never pooled with Ki; censored values (`>`, `<`) are preserved and excluded from numeric
use; an unevaluated material pair reads `not_evaluated`, never "compatible"; and no composite
score exists anywhere in screening, repurposing or formulation.

**50** Python modules · **22** frontend modules · **24** test files · **7** migrations ·
RDKit 2026.9.2 · FastAPI 0.163.1 · SQLAlchemy 2.1.8

---

## Scope and limitations

MOLECULAB supports research. It is **not** a substitute for laboratory validation, expert review
or regulatory assessment.

- Computational results are not evidence of clinical effectiveness or safety.
- A predicted or computed property is not proof of real-world product performance.
- Pharmacokinetic parameters and tissue partition coefficients are **inputs you supply**, not
  values derived from a structure. The curves are only as good as the parameters entered.
- A concentration is not an effect: nothing here predicts efficacy, toxicity or harm.
- The curated corpus is small and intended as a working demonstration.
- No Uzbek medicine-registry data is included — that registry has no public programmatic access,
  and the application says so rather than fabricating a registration record.

## Third-party components

Vendored for offline use, each with its licence shipped alongside. See
[`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md) — kept because BSD-3-Clause and CC BY-SA both
require the notice to travel with the code.

| Component | Use | Licence |
|---|---|---|
| [RDKit](https://www.rdkit.org/) | all chemistry | BSD-3-Clause |
| [3Dmol.js](https://3dmol.csb.pitt.edu/) 2.4.2 | 3D molecular viewer | BSD-3-Clause |
| [JSME](https://jsme-editor.github.io/) 2024.4.29 | 2D structure editor | BSD-3-Clause |
| [BodyParts3D](https://lifesciencedb.jp/bp3d/) | human anatomy meshes | CC BY-SA 2.1 Japan |

> BodyParts3D, Copyright 2008 Life Science Integrated Database Center licensed by CC
> Display-Inheritance 2.1 Japan

Provider data is fetched live and not redistributed here; each record carries its source and
retrieval time.

---

Built for the Uzbekistan National AI Hackathon, with an initial focus on pharmaceutical
formulation research and Central Asian natural-product chemistry.
