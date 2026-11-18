# Research artifacts

## jak1_curated.json

The curated JAK1 IC50 dataset produced by the MOLECULAB 1.0 activity-compatibility pipeline.
**7,777 records.** One object per compound:

    molecule_chembl_id, smiles, p_activity_median, replicate_count,
    fold_spread, scaffold (Bemis-Murcko), assay_ids, document_ids

### Provenance

- Source: ChEMBL **release 37**, target **CHEMBL2835** (JAK1, SINGLE PROTEIN, Homo sapiens, UniProt P23458)
- Retrieved and curated: **2026-11-17**
- Pipeline: `ChEMBLClient.activities(relation=None)` -> `app.activity.curate(...)` ->
  `aggregate_by_compound(...)`, with filters: endpoint IC50, organism Homo sapiens,
  assay_type B (binding), exact relations only, SINGLE PROTEIN target required.
- From 15,034 raw rows: 9,136 measurements survived curation (7,854 compounds); 2,587 censored
  measurements were excluded from numeric use; aggregation produced 7,777 compounds and rejected
  77 for >100-fold replicate disagreement.

### Why it is here

It saves the next session a full re-download and lets QSAR work start immediately. **It is derived
data, not a source of truth.** It is fully reproducible from ChEMBL_37; if ChEMBL has moved on, or if
you change any curation rule, regenerate it rather than trusting this file.

### What is NOT here

`jak1_ic50_raw.json` (the 15,034 raw rows, ~26 MB) was **deliberately excluded** for size. It is
re-downloadable in one call:

    from app.clients.chembl import ChEMBLClient
    rows = ChEMBLClient().activities("CHEMBL2835", "IC50", relation=None).data

### Open question before you model this

`p_activity_median` is MOLECULAB's **own** computation from `standard_value` + `standard_units`.
ChEMBL also publishes `pchembl_value`. Which is authoritative is **undecided**, and the disagreement
between them has **not** been measured. See Part E of `HANDOFF_TO_PHONE.md`.
