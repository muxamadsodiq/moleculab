"""Simulation Lab: computations that actually run, with their method and limits attached.

Every function here returns a `method` block (algorithm, parameters, software versions) and a
`result_kind` describing what the numbers are:

    computed      - calculated here, now, by a named algorithm (conformer energies, descriptors)
    experimental  - a measurement from a source database, carried through unchanged
    derived       - arithmetic on experimental values (e.g. pIC50 from an exact molar IC50)
    unavailable   - the capability does not exist; no numbers are produced

Nothing in this module produces a docking score, a binding affinity or a predicted activity. The
protein-ligand entry point exists only to report, explicitly, that the capability is absent.

A force-field conformer energy is an *intramolecular* strain estimate under a classical force field
in vacuum. It is not binding affinity, not free energy of binding, and not biological activity.
"""
from __future__ import annotations

import json
import logging
import math
from functools import lru_cache
from pathlib import Path

import rdkit
from rdkit import Chem, DataStructs
from rdkit.Chem import AllChem, rdMolAlign

from app.config import ROOT
from app.molecules import (CHEMISTRY_VERSION, FINGERPRINT_SPEC, descriptors, fingerprint,
                           murcko_scaffold, parse_smiles)

log = logging.getLogger("moleculab")

MAX_CONFORMERS = 50
MAX_SIMILARITY_LIBRARY = 10_000
CONFORMER_SEED = 0xC0FFEE

FORCE_FIELD_CAVEAT = (
    "Force-field conformer energy is an intramolecular strain estimate for an isolated molecule in "
    "vacuum. It is NOT binding affinity, NOT free energy of binding, and NOT biological activity. "
    "Relative energies between conformers of the SAME molecule are meaningful; energies of different "
    "molecules are not comparable."
)


def software_versions() -> dict:
    return {"rdkit": rdkit.__version__, "chemistry_version": CHEMISTRY_VERSION}


# --------------------------------------------------------------- A: conformers

def conformer_ensemble(smiles: str, n_conformers: int = 10, optimise: bool = True,
                       prune_rms: float = 0.5, seed: int = CONFORMER_SEED) -> dict:
    """Generate a conformer ensemble with RDKit ETKDGv3 and (optionally) MMFF94/UFF optimisation.

    Real computation. Returns one entry per conformer with its force-field energy and its heavy-atom
    RMSD to the lowest-energy conformer, sorted by energy.

    `prune_rms` removes near-duplicate embeddings, so the count returned can be lower than requested;
    that is reported rather than padded.
    """
    n_conformers = max(1, min(int(n_conformers), MAX_CONFORMERS))
    mol, error = parse_smiles(smiles)
    if mol is None:
        return {"ok": False, "error": error, "result_kind": "unavailable"}

    try:
        molh = Chem.AddHs(mol)
        params = AllChem.ETKDGv3()
        params.randomSeed = seed
        params.pruneRmsThresh = prune_rms
        params.useSmallRingTorsions = True
        ids = list(AllChem.EmbedMultipleConfs(molh, numConfs=n_conformers, params=params))
        if not ids:
            params.useRandomCoords = True
            ids = list(AllChem.EmbedMultipleConfs(molh, numConfs=n_conformers, params=params))
        if not ids:
            return {"ok": False, "result_kind": "unavailable",
                    "error": "RDKit could not embed any conformer for this structure"}

        force_field, energies = None, {}
        if optimise:
            if AllChem.MMFFHasAllMoleculeParams(molh):
                force_field = "MMFF94"
                results = AllChem.MMFFOptimizeMoleculeConfs(molh, maxIters=500)
            else:
                force_field = "UFF"
                results = AllChem.UFFOptimizeMoleculeConfs(molh, maxIters=500)
            for conf_id, (converged, energy) in zip(ids, results):
                energies[conf_id] = {"energy": round(float(energy), 4), "converged": converged == 0}

        ordered = sorted(ids, key=lambda cid: energies.get(cid, {}).get("energy", math.inf))
        reference = ordered[0]
        lowest = energies.get(reference, {}).get("energy")

        conformers = []
        for rank, conf_id in enumerate(ordered, start=1):
            entry = energies.get(conf_id, {})
            energy = entry.get("energy")
            try:
                rmsd = (0.0 if conf_id == reference
                        else round(rdMolAlign.GetBestRMS(
                            Chem.Mol(molh), Chem.Mol(molh), prbId=conf_id, refId=reference), 4))
            except Exception:
                rmsd = None
            conformers.append({
                "rank": rank,
                "conformer_id": int(conf_id),
                "energy_kcal_per_mol": energy,
                "relative_energy_kcal_per_mol": (None if energy is None or lowest is None
                                                 else round(energy - lowest, 4)),
                "optimisation_converged": entry.get("converged"),
                "rmsd_to_lowest_angstrom": rmsd,
                "molblock": Chem.MolToMolBlock(molh, confId=int(conf_id)),
            })

        return {
            "ok": True,
            "result_kind": "computed",
            "requested_conformers": n_conformers,
            "generated_conformers": len(conformers),
            "pruned": n_conformers - len(conformers),
            "atom_count": molh.GetNumAtoms(),
            "conformers": conformers,
            "energy_units": "kcal/mol" if force_field else None,
            "method": {
                "embedding": "RDKit ETKDGv3 (distance geometry)",
                "force_field": force_field,
                "max_iterations": 500 if optimise else 0,
                "prune_rms_threshold_angstrom": prune_rms,
                "random_seed": seed,
                "deterministic": True,
                "rmsd": "heavy-atom best RMS to the lowest-energy conformer",
                "software": software_versions(),
            },
            "interpretation": FORCE_FIELD_CAVEAT,
            "limitations": [
                "Conformers are generated in vacuum with no solvent and no protein environment.",
                "A classical force field approximates energies; it is not a quantum calculation.",
                "The lowest-energy conformer found is not necessarily the global minimum, and not "
                "necessarily the conformation a molecule adopts when bound to a protein.",
                "Energies of different molecules cannot be compared with each other.",
            ],
        }
    except Exception as exc:
        log.warning("conformer ensemble failed: %s", exc)
        return {"ok": False, "result_kind": "unavailable",
                "error": f"conformer generation failed: {type(exc).__name__}"}


# -------------------------------------------------------------- C: similarity

def similarity_search(query_smiles: str, library: list[dict], top_n: int = 25) -> dict:
    """Rank a library by Morgan/Tanimoto similarity to a query. Real fingerprint computation.

    `library` entries need `smiles` plus whatever identifying fields the caller wants echoed back.
    Unparseable library entries are counted and reported, never silently treated as dissimilar.
    """
    query, error = parse_smiles(query_smiles)
    if query is None:
        return {"ok": False, "error": error, "result_kind": "unavailable"}

    query_fp = fingerprint(query)
    rows, skipped = [], 0
    for entry in library[:MAX_SIMILARITY_LIBRARY]:
        mol, _ = parse_smiles(entry.get("smiles") or "")
        if mol is None:
            skipped += 1
            continue
        rows.append({
            **{k: v for k, v in entry.items() if k != "smiles"},
            "smiles": entry.get("smiles"),
            "tanimoto": round(DataStructs.TanimotoSimilarity(query_fp, fingerprint(mol)), 6),
            "shares_scaffold": murcko_scaffold(mol) == murcko_scaffold(query),
        })
    rows.sort(key=lambda r: r["tanimoto"], reverse=True)

    return {
        "ok": True,
        "result_kind": "computed",
        "query_smiles": Chem.MolToSmiles(query),
        "library_size": len(library),
        "compared": len(rows),
        "skipped_unparseable": skipped,
        "results": rows[:top_n],
        "method": {
            "fingerprint": dict(FINGERPRINT_SPEC),
            "metric": "Tanimoto coefficient",
            "scaffold": "Bemis-Murcko, compared as canonical SMILES",
            "software": software_versions(),
        },
        "interpretation": (
            "Tanimoto similarity over Morgan fingerprints measures overlap of substructural features. "
            "It is a measure of CHEMICAL STRUCTURE ONLY. A high value is not evidence of shared "
            "biological activity, shared mechanism, or shared safety profile; structurally similar "
            "molecules routinely differ in potency by orders of magnitude."),
        "limitations": [
            "Fingerprint similarity depends on the fingerprint chosen; a different radius or bit size "
            "gives different numbers for the same pair.",
            "Similarity is symmetric and context-free: it knows nothing about the target.",
            "The library searched is only what MOLECULAB holds, not all known chemistry.",
        ],
    }


# ------------------------------------------------------- B: property explorer

PROPERTY_FIELDS = ("molecular_weight", "clogp", "tpsa", "h_bond_donors", "h_bond_acceptors",
                   "rotatable_bonds", "aromatic_ring_count", "fraction_csp3", "heavy_atom_count",
                   "qed", "exact_mass", "ring_count", "molar_refractivity", "formal_charge")

PROPERTY_LABELS = {
    "molecular_weight": "Molecular weight (Da)", "clogp": "cLogP (Wildman-Crippen)",
    "tpsa": "TPSA (A^2)", "h_bond_donors": "H-bond donors", "h_bond_acceptors": "H-bond acceptors",
    "rotatable_bonds": "Rotatable bonds", "aromatic_ring_count": "Aromatic rings",
    "fraction_csp3": "Fraction Csp3", "heavy_atom_count": "Heavy atoms", "qed": "QED",
    "exact_mass": "Exact mass (Da)", "ring_count": "Rings",
    "molar_refractivity": "Molar refractivity", "formal_charge": "Formal charge",
}


def property_matrix(library: list[dict]) -> dict:
    """Compute descriptors across a library, for interactive property exploration.

    All values are computed locally by RDKit. None are measured, and none of them establish
    drug-likeness, safety or efficacy.
    """
    rows, skipped = [], 0
    for entry in library:
        mol, _ = parse_smiles(entry.get("smiles") or "")
        if mol is None:
            skipped += 1
            continue
        desc = descriptors(mol)
        rows.append({**{k: v for k, v in entry.items() if k != "smiles"},
                     "smiles": entry.get("smiles"),
                     **{f: desc.get(f) for f in PROPERTY_FIELDS}})
    return {
        "ok": True,
        "result_kind": "computed",
        "count": len(rows),
        "skipped_unparseable": skipped,
        "fields": [{"key": f, "label": PROPERTY_LABELS[f]} for f in PROPERTY_FIELDS],
        "rows": rows,
        "method": {"source": "RDKit descriptors computed locally", "software": software_versions()},
        "interpretation": (
            "These are calculated physicochemical descriptors, not measured values. Descriptor "
            "ranges summarise what has historically been common among oral drugs; they do not "
            "establish that a molecule is drug-like, safe, bioavailable or effective."),
    }


# -------------------------------------------------- D: activity data explorer

ACTIVITY_DATASET = Path(ROOT) / "research_artifacts" / "jak1_curated.json"


@lru_cache(maxsize=1)
def _load_activity_dataset() -> tuple:
    """The curated JAK1 IC50 dataset shipped in research_artifacts/ (derived from ChEMBL_37)."""
    try:
        rows = json.loads(ACTIVITY_DATASET.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("activity dataset unavailable: %s", exc)
        return ()
    return tuple(rows)


def activity_dataset_info() -> dict:
    rows = _load_activity_dataset()
    return {
        "available": bool(rows),
        "path": "research_artifacts/jak1_curated.json",
        "compound_count": len(rows),
        "target": {"gene_symbol": "JAK1", "chembl_target_id": "CHEMBL2835",
                   "organism": "Homo sapiens", "target_type": "SINGLE PROTEIN",
                   "uniprot": "P23458"},
        "endpoint": "IC50",
        "units": "nM, converted to pIC50 (-log10 molar)",
        "source": "ChEMBL release 37",
        "retrieved": "2026-11-17",
        "result_kind": "experimental",
        "curation": (
            "From 15,034 raw IC50 rows: filtered to Homo sapiens, assay_type B (binding), exact "
            "relations only, SINGLE PROTEIN target. 9,136 measurements over 7,854 compounds "
            "survived; 2,587 censored measurements (>, <, <=) were excluded from numeric use; "
            "aggregation by median pIC50 produced 7,777 compounds and rejected 77 whose replicates "
            "disagreed by more than 100-fold."),
        "compatibility_rule": (
            "Every row here is the same endpoint (IC50), same organism (human), same assay type "
            "(binding) and same target. Measurements of different endpoints are never pooled: an "
            "IC50 is not a Ki and is not an EC50."),
        "limitations": [
            "6,774 of 7,777 compounds have a single, unreplicated measurement.",
            "Reference drugs disagree with themselves across publications: ruxolitinib's reported "
            "IC50 values span 11,700-fold over 18 measurements, tofacitinib's 250-fold over 26. "
            "That inter-laboratory scatter is the practical noise floor of this dataset.",
            "Censored measurements are excluded, which biases the retained set toward measurable "
            "potencies.",
            "These are deposited literature values, not measurements made by MOLECULAB.",
        ],
    }


def activity_explore(min_p_activity: float | None = None, max_p_activity: float | None = None,
                     min_replicates: int = 1, scaffold: str | None = None,
                     search: str | None = None, limit: int = 200, histogram_bins: int = 28) -> dict:
    """Filter and summarise the curated activity dataset. Experimental values, carried unchanged."""
    rows = _load_activity_dataset()
    if not rows:
        return {"ok": False, "result_kind": "unavailable",
                "error": "The curated activity dataset is not present in this installation "
                         "(research_artifacts/jak1_curated.json)."}

    selected = []
    for row in rows:
        value = row.get("p_activity_median")
        if value is None:
            continue
        if min_p_activity is not None and value < min_p_activity:
            continue
        if max_p_activity is not None and value > max_p_activity:
            continue
        if row.get("replicate_count", 1) < min_replicates:
            continue
        if scaffold and row.get("scaffold") != scaffold:
            continue
        if search and search.lower() not in str(row.get("molecule_chembl_id", "")).lower():
            continue
        selected.append(row)

    values = [r["p_activity_median"] for r in selected]
    histogram = []
    if values:
        low, high = min(values), max(values)
        span = (high - low) or 1.0
        width = span / histogram_bins
        counts = [0] * histogram_bins
        for value in values:
            index = min(int((value - low) / width), histogram_bins - 1)
            counts[index] += 1
        histogram = [{"from": round(low + i * width, 3), "to": round(low + (i + 1) * width, 3),
                      "count": counts[i]} for i in range(histogram_bins)]

    scaffolds: dict[str, int] = {}
    for row in selected:
        key = row.get("scaffold") or "(acyclic)"
        scaffolds[key] = scaffolds.get(key, 0) + 1

    ordered = sorted(selected, key=lambda r: r["p_activity_median"], reverse=True)
    return {
        "ok": True,
        "result_kind": "experimental",
        "dataset": activity_dataset_info(),
        "matched": len(selected),
        "returned": min(limit, len(ordered)),
        "statistics": ({
            "min": round(min(values), 3), "max": round(max(values), 3),
            "median": round(sorted(values)[len(values) // 2], 3),
            "mean": round(sum(values) / len(values), 3),
            "unreplicated": sum(1 for r in selected if r.get("replicate_count", 1) == 1),
            "unique_scaffolds": len(scaffolds),
        } if values else None),
        "histogram": histogram,
        "histogram_axis": {"x": "pIC50 (-log10 molar)", "y": "compounds"},
        "top_scaffolds": sorted(({"scaffold": k, "count": v} for k, v in scaffolds.items()),
                                key=lambda s: -s["count"])[:10],
        "rows": ordered[:limit],
        "derived_note": ("pIC50 is derived arithmetic on an exact molar IC50 (-log10 of the molar "
                         "value). The underlying IC50 is the experimental measurement."),
    }


# ------------------------------------------- E: protein-ligand (not available)

def protein_ligand_status() -> dict:
    """MOLECULAB has no docking capability. This reports that, and produces no numbers."""
    return {
        "ok": False,
        "available": False,
        "result_kind": "unavailable",
        "capability": "protein-ligand interaction analysis / molecular docking",
        "reason": (
            "MOLECULAB has no docking engine, no prepared protein structures, no binding-site "
            "definitions and no ligand preparation pipeline. None of the components required for a "
            "defensible docking result is present."),
        "what_would_be_required": [
            "An experimentally determined or validated protein structure (e.g. from RCSB PDB) with "
            "a defined binding site.",
            "Protein preparation: protonation states, missing residues and side chains, waters.",
            "Ligand preparation: tautomer and protonation enumeration, ring conformations.",
            "A docking engine (e.g. AutoDock Vina, Smina) with a validated scoring function.",
            "Re-docking validation against known co-crystallised ligands to establish that the "
            "pipeline reproduces observed poses.",
        ],
        "why_it_is_not_faked": (
            "A docking score is the easiest plausible-looking number to fabricate and one of the "
            "hardest for a reader to challenge. Even a real docking score is not experimental "
            "binding affinity and does not establish binding or efficacy. Rather than display an "
            "illustrative pose, MOLECULAB shows nothing here."),
    }
