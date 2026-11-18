# Third-party licences

MOLECULAB redistributes the file below inside this repository. BSD-3-Clause requires that the
copyright notice and licence text be reproduced with the redistribution, so the notice is shipped
next to the file and is also served by the application at
`/static/vendor/3Dmol-min.js.LICENSE.txt`.

Nothing else in `app/static/` is third-party: there is no bundler, no `node_modules`, and no CDN
dependency at runtime.

## Vendored into this repository

| File | Library | Version | Licence | Notice shipped |
|---|---|---|---|---|
| `app/static/vendor/3Dmol-min.js` | 3Dmol.js | **2.4.2** | BSD-3-Clause | `app/static/vendor/3Dmol-min.js.LICENSE.txt` |

**Version evidence.** The minified file is 513,564 bytes. The upstream build banner at
`https://unpkg.com/3dmol@2.4.2/build/3Dmol-min.js.LICENSE.txt` reads `3dmol v2.4.2`, and that banner
is reproduced at the top of the shipped notice. (A byte-size comparison against the npm **2.5.5**
build, 537,792 bytes, is a version difference, not a corrupted download.)

**Why it is vendored rather than loaded from a CDN.** Molecular rendering must work offline, which was
a stated requirement. A CDN `<script>` would make 3D structure viewing fail without network access.

3Dmol.js itself incorporates code from GLmol, Three.js and jQuery; the shipped notice reproduces all
of their copyright lines, which is why it lists four separate copyright holders.

## Python dependencies

Installed from PyPI and **not** redistributed here, so no notice reproduction is required. Their
licences are permissive (BSD/MIT/Apache-2.0) except where noted:

- `rdkit` — BSD-3-Clause
- `fastapi`, `starlette`, `pydantic`, `uvicorn`, `httpx` — MIT / BSD-3-Clause
- `SQLAlchemy`, `alembic`, `Mako` — MIT
- `numpy` — BSD-3-Clause (RDKit's own requirement)
- `reportlab` — BSD-3-Clause (**dev only**, for `scripts/make_business_pdf.py`; not imported by the app)

## Data sources

Provider data is fetched live at runtime and is **not** redistributed in this repository. Each record
carries its source, release identifier and retrieval time. The data licences of ChEMBL, PubChem,
Open Targets, PubMed, Crossref, GBIF, ClinicalTrials.gov and DailyMed have **not** been individually
reviewed and are recorded here as unverified. Review them before any redistribution of cached
provider data or any commercial deployment.

## No project licence yet

This repository has no `LICENSE` file, so no licence is granted to anyone else for MOLECULAB's own
code. That is the correct default for unreleased work, but it must be decided before the code is
shared, open-sourced or delivered to a partner.
