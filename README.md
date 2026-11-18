# MOLECULAB

**An evidence-grounded research workbench for early-stage drug discovery and pharmaceutical formulation.**

Investigate a disease, target, compound or formulation in one workspace. MOLECULAB connects source records to molecular structures, reproducible calculations, candidate comparisons and experiment plans. It keeps the evidence behind each decision available in the same investigation.

Built for the Uzbekistan National AI Hackathon, with a demonstration corpus focused on Central Asian natural products. [Download the hackathon presentation](presentation/MOLECULAB_FINAL.pptx).

## Try the demo

Start the app and open **[Guided Demo](http://127.0.0.1:8765/#/demo)**. Follow rheumatoid arthritis to JAK1, inspect measured activity and a compound in 2D and 3D, then review the evidence and open a worked tablet formulation to see which experiment could resolve a missing measurement. The tour calls the application’s APIs. Live lookups require internet access.

For a local walkthrough, open **[Research Bench](http://127.0.0.1:8765/#/bench)** and choose an example objective. Use the bundled records and calculation tools, then open **[Body Distribution](http://127.0.0.1:8765/#/anatomy)** to run the human example and inspect how simulated concentrations change over time.

## What’s in the workbench

| Area | What you can do |
| --- | --- |
| Research Bench, Workbench and Boards | Start with a plain-language objective and work through Research, Evidence, Assist, Explore, Calculate, Compare, Record and Report. Save investigations, attach source records, track hypotheses and notes, connect findings on a board, and export reports as Markdown. |
| Discovery and evidence | Explore diseases, targets, compounds, plants, publications and evidence. Follow relationships in the knowledge graph, inspect evidence gaps and trace findings to the source record. |
| Drug Intelligence and Elements | Look up and compare active ingredients, marketed products and source-backed label or registry information where available. Browse all 118 elements in the periodic table. |
| AI Research Copilot | Ask literature and structure questions in context. Retrieve records and use extractive answers with the default provider setting. Optionally enable Claude generation with server-side claim verification and abstention when the retrieved evidence is insufficient. |
| Molecular Studio and Playground | Open an indexed compound, paste SMILES or draw a structure with JSME. RDKit validates and standardizes structures while retaining the original input and stereochemistry. View 2D depictions, generate and inspect 3D conformers, and compare descriptors, fingerprints and scaffolds. |
| Virtual Screening and Drug Repurposing | Rank candidates using measured activity for the selected target and compatible assays. Trace the supporting records, check replicates and disagreement, and review evidence for a repurposing hypothesis. |
| Simulation Lab | Explore conformer ensembles, molecular properties and similarity, single-molecule dynamics and an implicit-solvent comparison. Inspect contacts in experimental protein–ligand structures from RCSB PDB. The catalogue has 16 calculations, each with its inputs, method, limitations and a SHA-256 fingerprint of calculation ID, inputs and software versions. |
| Dosing Lab | Model single or repeat administration with configurable schedules and supplied PK parameters. Play concentration curves, inspect Cmax, Tmax, AUC, half-life and accumulation, compare scenarios across species, and explore a 3D surface of modeled exposure across dose levels. |
| Body Distribution | Run whole-body PBPK for human, rat, mouse, rabbit or dog. Scrub through tissue concentrations, select organs for individual curves, and check the solver's reported mass balance. The rotatable BodyParts3D mesh is available for humans. Rat, mouse, rabbit and dog use schematic views with their own reference physiology. |
| Formulation Studio, Materials and Conditions | Build and compare formulations, record ingredients and processing conditions, evaluate measurements against constraints, inspect compatibility and trade-offs, and prioritize experiments that address evidence gaps. Browse and compare the underlying materials and process records. |

## Evidence and model scope

- The eight integrated source providers are **ChEMBL, Open Targets, PubChem, PubMed, Crossref, GBIF, ClinicalTrials.gov and DailyMed**. Provider responses carry their retrieval metadata and release identifiers where available. The structural contact viewer also reads deposited RCSB PDB structures.
- Results use explicit evidence states including `experimental`, `derived`, `computed`, `predicted`, `hypothesis`, `unverified`, `contradiction` and `missing`. Missing values stay empty. Activity comparisons preserve endpoint and assay context, and censored measurements keep their inequality markers. Physical formulation constraints require supporting measurements.
- With generation enabled, the assistant splits the draft into claims and checks each against its cited evidence. The answer omits unsupported claims and can abstain when the records are insufficient.
- The 16 calculations cover descriptors, Lipinski criteria, structure validation, conformer ensembles, pairwise similarity, Murcko scaffolds, measured activity distributions, assay comparability, composition totals, concentration unit conversion, molecular dynamics, implicit solvent, experimental contacts, pharmacokinetics, dilution and PBPK.
- Dynamics uses short single-molecule force-field trajectories. The solvent tool uses a simplified implicit model. Dilution calculates concentration after adding solvent. The Dosing Lab and PBPK use supplied parameters and reference physiology to estimate exposure. Those results support research and require experimental validation before any clinical interpretation.
- The model catalogue marks molecular docking, QSAR activity prediction and dissolution or bioavailability prediction as unavailable. Candidate ranking uses measured evidence. The Uzbekistan medicine registry can be consulted through its official manual lookup.

## Run locally

Use **Python 3.14**. The browser UI uses vanilla JavaScript with no build step. **Node.js 24** is used for the frontend smoke test.

**macOS or Linux**

```bash
git clone https://github.com/muxamadsodiq/moleculab.git
cd moleculab
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
sh scripts/start_backend.sh
```

**Windows PowerShell**

```powershell
git clone https://github.com/muxamadsodiq/moleculab.git
cd moleculab
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Open **[MOLECULAB](http://127.0.0.1:8765/)** or **[API docs](http://127.0.0.1:8765/docs)**. The checked-in `moleculab.db` includes plants, compounds, publications, evidence records, materials and a worked tablet formulation. The curated JAK1 activity dataset ships separately in `research_artifacts/`.

To create a separate SQLite database from seed, set `DATABASE_URL` to `sqlite:///./moleculab-local.db` in your shell, then run `python -m alembic upgrade head` and `python -m scripts.seed` with your virtual environment Python. Use the same `DATABASE_URL` when launching the app.

### Optional AI generation

Copy [`.env.example`](.env.example) to `.env` and select a provider. The default `LLM_PROVIDER=none` keeps the evidence retrieval path available. Set `LLM_PROVIDER=claude_cli` to use a locally authenticated Claude Code CLI, or install the optional `anthropic` package and set `LLM_PROVIDER=anthropic` plus `LLM_API_KEY` for the API. Keep credentials in the server environment or local `.env`.

### Tests

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
node tests/frontend_smoke.mjs http://127.0.0.1:8765
```

Run the frontend smoke test against a server started with `LLM_PROVIDER=none` so the extractive-answer checks are deterministic. Live-provider tests are opt-in through `MOLECULAB_LIVE=1`.

## Architecture and credits

FastAPI serves the UI and API. Python engines implement chemistry, activity screening, simulations and formulation evaluation. RDKit handles molecular validation and calculations. SQLAlchemy and Alembic manage persistence, with SQLite for the bundled local demo.

The local visualizers use vendored [3Dmol.js](https://3dmol.csb.pitt.edu/) and [JSME](https://jsme-editor.github.io/). Human meshes come from [BodyParts3D](https://lifesciencedb.jp/bp3d/), © 2008 Life Science Integrated Database Center, licensed under CC BY-SA 2.1 Japan. See [Third-party licenses](THIRD_PARTY_LICENSES.md) for attribution and terms.
