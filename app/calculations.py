"""Calculation registry: the single declared inventory of what MOLECULAB can actually compute.

The Research Bench offers the researcher a list of calculations. That list comes from here, and
nothing else. The rule this module exists to enforce is:

    **A calculation appears in the product only if it is registered here, and it can only be
    registered here if a real function computes it.**

Every entry declares its scientific purpose, required inputs, outputs, the actual method, the
software version that produced it, its assumptions and its limitations. A run records all of that
alongside the result, so a number can always be traced back to how it was produced.

`UNAVAILABLE` is the other half of the contract. Docking and QSAR are described here as explicitly
unavailable, with the reason, so the Bench can show a researcher *why* a capability they expect is
missing instead of silently omitting it. A capability listed in UNAVAILABLE must never acquire a
`run` function that returns numbers.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app import (activity, dynamics, formulation, molecules, pbpk, pharmacokinetics,
                 simulations, structures)
from app.config import APP_VERSION

CALC_REGISTRY_VERSION = "1.0.0"


@dataclass(frozen=True)
class Input:
    name: str
    kind: str                      # smiles | number | integer | text | reference | boolean
    description: str
    required: bool = True
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class Calculation:
    id: str
    name: str
    category: str                  # molecular | activity | formulation
    purpose: str
    method: str
    inputs: tuple[Input, ...]
    outputs: tuple[str, ...]
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    run: Callable[..., dict] = field(repr=False, default=None)  # type: ignore[assignment]
    deterministic: bool = True

    def describe(self) -> dict:
        return {
            "id": self.id, "name": self.name, "category": self.category,
            "purpose": self.purpose, "method": self.method,
            "inputs": [
                {"name": i.name, "kind": i.kind, "description": i.description,
                 "required": i.required, "default": i.default,
                 "minimum": i.minimum, "maximum": i.maximum} for i in self.inputs
            ],
            "outputs": list(self.outputs),
            "assumptions": list(self.assumptions),
            "limitations": list(self.limitations),
            "deterministic": self.deterministic,
            "implemented": True,
        }


# --------------------------------------------------------------------------- molecular

def _require_mol(smiles: str):
    mol, err = molecules.parse_smiles(smiles)
    if mol is None:
        raise ValueError(f"Structure could not be parsed: {err}")
    return mol


def _descriptors(smiles: str) -> dict:
    """Descriptors are computed on the STANDARDIZED structure, and the audit trail says so."""
    mol = _require_mol(smiles)
    std, steps = molecules.standardize(mol)
    desc = molecules.descriptors(std)
    return {
        "input_smiles": smiles,
        "standardized_smiles": molecules.Chem.MolToSmiles(std),
        "standardization_steps": steps,
        "descriptors": desc,
        "stereochemistry": molecules.stereo_summary(std),
        "note": "Every value here is computed from the structure by RDKit. None is a measurement.",
    }


def _lipinski(smiles: str) -> dict:
    """Lipinski's rule of five. Reported as four individual criteria, never as a pass/fail score."""
    mol = _require_mol(smiles)
    std, _ = molecules.standardize(mol)
    d = molecules.descriptors(std)
    criteria = [
        {"criterion": "Molecular weight <= 500 Da", "value": d.get("molecular_weight"),
         "threshold": 500, "satisfied": (d.get("molecular_weight") or 0) <= 500},
        {"criterion": "cLogP <= 5", "value": d.get("clogp"),
         "threshold": 5, "satisfied": (d.get("clogp") or 0) <= 5},
        {"criterion": "H-bond donors <= 5", "value": d.get("h_bond_donors"),
         "threshold": 5, "satisfied": (d.get("h_bond_donors") or 0) <= 5},
        {"criterion": "H-bond acceptors <= 10", "value": d.get("h_bond_acceptors"),
         "threshold": 10, "satisfied": (d.get("h_bond_acceptors") or 0) <= 10},
    ]
    return {
        "criteria": criteria,
        "violations": [c["criterion"] for c in criteria if not c["satisfied"]],
        "interpretation": (
            "Lipinski's rule of five is a heuristic describing trends among orally administered "
            "drugs. Satisfying it does not make a compound orally bioavailable, and violating it "
            "does not make a compound unusable: several approved oral drugs violate it. This is "
            "not a prediction of absorption."
        ),
    }


def _conformers(smiles: str, n_conformers: int = 10, optimise: bool = True) -> dict:
    return simulations.conformer_ensemble(smiles, n_conformers=int(n_conformers),
                                          optimise=bool(optimise))


def _similarity_pair(smiles_a: str, smiles_b: str) -> dict:
    a, b = _require_mol(smiles_a), _require_mol(smiles_b)
    sa, _ = molecules.standardize(a)
    sb, _ = molecules.standardize(b)
    return {
        "tanimoto": molecules.tanimoto(sa, sb),
        "fingerprint": molecules.FINGERPRINT_SPEC,
        "scaffold_a": molecules.murcko_scaffold(sa),
        "scaffold_b": molecules.murcko_scaffold(sb),
        "shares_scaffold": molecules.murcko_scaffold(sa) == molecules.murcko_scaffold(sb),
        "interpretation": (
            "Tanimoto similarity on Morgan fingerprints measures shared substructure, not shared "
            "biological activity. Two compounds can be highly similar and differ in potency by "
            "orders of magnitude (an activity cliff), and dissimilar compounds can share a target."
        ),
    }


def _scaffold(smiles: str) -> dict:
    mol = _require_mol(smiles)
    std, _ = molecules.standardize(mol)
    scaf = molecules.murcko_scaffold(std)
    return {
        "murcko_scaffold": scaf,
        "has_scaffold": scaf is not None and scaf != "",
        "interpretation": (
            "The Bemis-Murcko scaffold is the ring systems plus the linkers joining them, with "
            "side chains removed. It is a structural classification, not an activity claim."
        ),
    }


def _validate_structure(smiles: str) -> dict:
    mol, err = molecules.parse_smiles(smiles)
    if mol is None:
        return {"valid": False, "error": err,
                "note": "MOLECULAB refuses an unparseable structure rather than guessing at it."}
    std, steps = molecules.standardize(mol)
    # A Kekule form for the structure EDITOR. JSME lays an aromatic-lowercase carbonyl carbon
    # out incorrectly, so the editor is given explicit alternating bonds instead. This is a
    # drawing aid only: it is the same molecule, and nothing downstream uses it.
    editor_smiles = None
    try:
        kek = molecules.Chem.Mol(std)
        molecules.Chem.Kekulize(kek, clearAromaticFlags=True)
        editor_smiles = molecules.Chem.MolToSmiles(kek, kekuleSmiles=True)
    except Exception:  # noqa: BLE001 - some structures cannot be kekulized; the raw form stands
        editor_smiles = None

    return {
        "valid": True,
        "input_smiles": smiles,
        "editor_smiles": editor_smiles,
        "canonical_smiles": molecules.Chem.MolToSmiles(mol),
        "standardized_smiles": molecules.Chem.MolToSmiles(std),
        "changed_by_standardization": molecules.Chem.MolToSmiles(mol) != molecules.Chem.MolToSmiles(std),
        "standardization_steps": steps,
        "inchikey": molecules.Chem.MolToInchiKey(std),
        "atom_count": std.GetNumAtoms(),
        "note": ("The original structure is preserved above alongside the standardized one. "
                 "Standardization is recorded step by step, never applied silently."),
    }


# --------------------------------------------------------------------------- activity

def _activity_distribution(min_p_activity: float | None = None,
                           min_replicates: int = 1) -> dict:
    return simulations.activity_explore(min_p_activity=min_p_activity,
                                        min_replicates=int(min_replicates))


def _probe(standard_type: str, standard_units: str) -> "activity.Measurement":
    """A minimal Measurement standing in for 'a measurement of this type in these units'.

    The compatibility rules depend only on endpoint family and unit dimension, so a placeholder
    value of 1.0 with an exact relation is enough to ask whether the two could ever be compared.
    """
    return activity.Measurement(
        molecule_chembl_id=None, smiles=None, standard_type=standard_type,
        standard_relation="=", standard_value=1.0, standard_units=standard_units)


def _assay_compatibility(endpoint_a: str, units_a: str, endpoint_b: str, units_b: str) -> dict:
    """Decides whether two measurements may be compared at all. Refusal is the useful answer."""
    report = activity.compatibility_report(_probe(endpoint_a, units_a),
                                           _probe(endpoint_b, units_b))
    return {**report, "compared": {"a": {"endpoint": endpoint_a, "units": units_a},
                                   "b": {"endpoint": endpoint_b, "units": units_b}}}


# --------------------------------------------------------------------------- formulation

def _composition_totals(components: list[dict]) -> dict:
    """Composition arithmetic with explicit unit handling. Never invents a missing amount."""
    if not components:
        raise ValueError("No components supplied.")
    units = {str(c.get("unit") or "").strip().lower() for c in components if c.get("amount") is not None}
    missing = [c.get("name") or "(unnamed)" for c in components if c.get("amount") is None]
    known = [c for c in components if c.get("amount") is not None]
    mixed = len(units) > 1
    total = sum(float(c["amount"]) for c in known) if known and not mixed else None
    rows = []
    for c in components:
        amt = c.get("amount")
        rows.append({
            "name": c.get("name") or "(unnamed)",
            "role": c.get("role"),
            "amount": amt,
            "unit": c.get("unit"),
            "percent_of_total": (round(float(amt) / total * 100, 3)
                                 if amt is not None and total else None),
            "amount_recorded": amt is not None,
        })
    return {
        "components": rows,
        "total": total,
        "total_unit": next(iter(units)) if len(units) == 1 else None,
        "units_mixed": mixed,
        "components_without_recorded_amount": missing,
        "mass_balance_checkable": bool(total) and not missing,
        "limitations": [
            "Percentages are computed only against components whose amount is recorded.",
            ("Amounts are missing for: " + ", ".join(missing) + ". MOLECULAB does not estimate "
             "them, so the total and every percentage below are incomplete.") if missing else
            "Every component has a recorded amount.",
            ("Components use more than one unit, so no total was computed. Convert to a common "
             "unit first.") if mixed else "All amounts share one unit.",
        ],
    }


def _unit_conversion(value: float, from_unit: str, to_unit: str) -> dict:
    """Concentration conversion to nanomolar, using the engine's own verified table.

    `activity.to_nanomolar` returns (value, error) and its unit table is CASE-SENSITIVE: 'mM' and
    'uM' differ by a factor of 1000 from 'MM'/'UM', so the keys must not be lowercased.
    """
    nm, err = activity.to_nanomolar(value, from_unit)
    if nm is None:
        raise ValueError(
            f"Cannot convert {value!r} from {from_unit!r}: {err}. MOLECULAB converts only exact, "
            f"same-quantity concentration units: {sorted(activity.TO_NANOMOLAR)}. Mass-per-volume "
            f"needs a molecular weight, which is not assumed.")
    factor = activity.TO_NANOMOLAR.get(to_unit)
    if factor is None:
        raise ValueError(f"Cannot convert to {to_unit!r}: not a supported concentration unit. "
                         f"Supported: {sorted(activity.TO_NANOMOLAR)}")
    return {"input": {"value": value, "unit": from_unit},
            "nanomolar": nm,
            "output": {"value": nm / factor, "unit": to_unit},
            "p_activity_if_concentration": activity.p_activity_from_nm(nm),
            "note": ("p-activity is -log10(molar concentration). It is meaningful only when the "
                     "value really is a concentration, such as an IC50 or Ki.")}


# --------------------------------------------------------------------------- dynamics

def _molecular_dynamics(smiles: str, temperature_k: float = 300.0, steps: int = 800,
                        timestep_fs: float = 1.0, solvent: str = "implicit_water") -> dict:
    return dynamics.run_dynamics(smiles, temperature_k=float(temperature_k), steps=int(steps),
                                 timestep_fs=float(timestep_fs), solvent=str(solvent))


def _solvent_effect(smiles: str) -> dict:
    return dynamics.solvent_effect(smiles)


def _protein_ligand(pdb_id: str, ligand_code: str | None = None) -> dict:
    return structures.contacts(pdb_id, ligand_code)


# --------------------------------------------------------------------------- pharmacokinetics

def _pk(dose_mg: float, interval_h: float = 0, duration_h: float = 48,
        clearance_l_per_h: float = 6.0, volume_l: float = 36.0, ka_per_h: float = 1.5,
        bioavailability: float = 1.0, species: str = "human",
        scale_from_human: bool = False, molecular_weight: float | None = None) -> dict:
    return pharmacokinetics.simulate(
        dose_mg=float(dose_mg), interval_h=float(interval_h), duration_h=float(duration_h),
        clearance_l_per_h=float(clearance_l_per_h), volume_l=float(volume_l),
        ka_per_h=float(ka_per_h), bioavailability=float(bioavailability),
        species=str(species), scale_from_human=bool(scale_from_human),
        molecular_weight=molecular_weight)


def _dilution(amount_mg: float, volume_ml: float, molecular_weight: float | None = None,
              added_ml_per_event: float = 0.0, events: int = 0) -> dict:
    return pharmacokinetics.dilution(
        amount_mg=float(amount_mg), volume_ml=float(volume_ml),
        molecular_weight=molecular_weight,
        added_ml_per_event=float(added_ml_per_event), events=int(events))


def _pbpk(dose_mg: float, species: str = "human", duration_h: float = 24,
          interval_h: float = 0, hepatic_cl_l_per_h: float = 6.0,
          renal_cl_l_per_h: float = 0.0, ka_per_h: float = 1.5,
          bioavailability: float = 1.0, kp: dict | None = None) -> dict:
    return pbpk.simulate(dose_mg=float(dose_mg), species=str(species),
                         duration_h=float(duration_h), interval_h=float(interval_h),
                         hepatic_cl_l_per_h=float(hepatic_cl_l_per_h),
                         renal_cl_l_per_h=float(renal_cl_l_per_h),
                         ka_per_h=float(ka_per_h), bioavailability=float(bioavailability),
                         kp=kp)


# --------------------------------------------------------------------------- the registry

REGISTRY: dict[str, Calculation] = {}


def _register(c: Calculation) -> None:
    if c.run is None:                                     # pragma: no cover - guard
        raise RuntimeError(f"{c.id} has no implementation and must not be registered")
    REGISTRY[c.id] = c


_register(Calculation(
    id="molecular_descriptors", name="Molecular descriptors", category="molecular",
    purpose="Compute the standard physicochemical descriptors of a structure.",
    method="RDKit descriptor calculation on the standardized structure",
    inputs=(Input("smiles", "smiles", "The structure, as SMILES."),),
    outputs=("molecular_weight", "molecular_formula", "exact_mass", "clogp", "tpsa",
             "h_bond_donors", "h_bond_acceptors", "rotatable_bonds", "ring_count",
             "aromatic_ring_count", "heavy_atom_count", "fraction_csp3", "formal_charge",
             "molar_refractivity", "qed"),
    assumptions=("The structure as supplied is the intended neutral parent.",
                 "Descriptors describe the 2D structure, not any particular solid form."),
    limitations=("Every value is computed, not measured.",
                 "cLogP and TPSA are empirical estimates from atom contributions; they are not "
                 "experimental partition coefficients or measured surface areas.",
                 "Nothing here describes a salt, polymorph, hydrate or formulated product."),
    run=_descriptors))

_register(Calculation(
    id="lipinski", name="Lipinski rule-of-five criteria", category="molecular",
    purpose="Report the four rule-of-five criteria individually for an oral-drug heuristic.",
    method="RDKit descriptors compared against Lipinski's published thresholds",
    inputs=(Input("smiles", "smiles", "The structure, as SMILES."),),
    outputs=("criteria", "violations"),
    assumptions=("The compound is being considered for oral administration.",),
    limitations=("A heuristic about historical trends, not a prediction of bioavailability.",
                 "Reported as four separate criteria; MOLECULAB does not reduce them to a score.",
                 "Several approved oral drugs violate one or more criteria."),
    run=_lipinski))

_register(Calculation(
    id="structure_validation", name="Structure validation and standardization", category="molecular",
    purpose="Check that a structure parses, and show exactly how standardization changes it.",
    method="RDKit sanitization, then Cleanup -> FragmentParent -> Uncharger, each step recorded",
    inputs=(Input("smiles", "smiles", "The structure to validate."),),
    outputs=("valid", "canonical_smiles", "standardized_smiles", "editor_smiles",
             "standardization_steps", "inchikey", "changed_by_standardization"),
    assumptions=("Standardization should remove salts and neutralize charges, but must not alter "
                 "stereochemistry or choose a tautomer.",),
    limitations=("Stereochemistry is deliberately NOT normalized, and tautomers are deliberately "
                 "NOT canonicalized, because both change chemical meaning.",
                 "A valid structure is not a real or available compound."),
    run=_validate_structure))

_register(Calculation(
    id="conformer_ensemble", name="3D conformer ensemble", category="molecular",
    purpose="Generate and optimise 3D conformations to inspect accessible shapes.",
    method="RDKit ETKDGv3 embedding with a fixed seed, then MMFF94 optimisation (UFF fallback)",
    inputs=(Input("smiles", "smiles", "The structure, as SMILES."),
            Input("n_conformers", "integer", "How many conformers to generate.",
                  required=False, default=10, minimum=1, maximum=50),
            Input("optimise", "boolean", "Run force-field optimisation.",
                  required=False, default=True)),
    outputs=("conformers", "energy_kcal_per_mol", "relative_energy_kcal_per_mol",
             "rmsd_to_lowest_angstrom", "molblock"),
    assumptions=("The molecule is treated in isolation, in vacuum, with no solvent and no "
                 "binding partner.",
                 "A fixed random seed is used, so the same input reproduces the same ensemble."),
    limitations=("Force-field energies are NOT binding affinities and are not comparable between "
                 "different molecules.",
                 "An in-vacuum conformer is not the bound conformation or the crystal conformation.",
                 "Conformer generation is a search: absence of a shape is not proof it cannot form."),
    run=_conformers))

_register(Calculation(
    id="similarity_pair", name="Pairwise structural similarity", category="molecular",
    purpose="Measure shared substructure between two structures, and compare their scaffolds.",
    method="Tanimoto coefficient on Morgan fingerprints (radius 2, 2048 bits) + Murcko scaffolds",
    inputs=(Input("smiles_a", "smiles", "First structure."),
            Input("smiles_b", "smiles", "Second structure.")),
    outputs=("tanimoto", "scaffold_a", "scaffold_b", "shares_scaffold", "fingerprint"),
    assumptions=("Morgan radius-2 fingerprints are an adequate description of local environment.",),
    limitations=("Structural similarity is not activity similarity; activity cliffs are common.",
                 "The value depends on the fingerprint; a different fingerprint gives a different "
                 "number for the same pair."),
    run=_similarity_pair))

_register(Calculation(
    id="scaffold", name="Bemis-Murcko scaffold", category="molecular",
    purpose="Extract the ring-and-linker core of a structure for structural classification.",
    method="RDKit MurckoScaffold on the standardized structure",
    inputs=(Input("smiles", "smiles", "The structure, as SMILES."),),
    outputs=("murcko_scaffold", "has_scaffold"),
    assumptions=("Side chains are not part of the structural core.",),
    limitations=("An acyclic molecule has no scaffold; that is a real answer, not an error.",
                 "Shared scaffold does not imply shared activity or shared target."),
    run=_scaffold))

_register(Calculation(
    id="activity_distribution", name="Measured activity distribution", category="activity",
    purpose="Explore the distribution of real measured potencies in the curated dataset.",
    method="Curated ChEMBL IC50 measurements, aggregated per compound by median",
    inputs=(Input("min_p_activity", "number", "Keep compounds at or above this pActivity.",
                  required=False, minimum=0, maximum=12),
            Input("min_replicates", "integer", "Keep compounds with at least this many "
                  "independent measurements.", required=False, default=1, minimum=1)),
    outputs=("rows", "statistics", "histogram", "dataset"),
    assumptions=("Measurements curated as comparable may be aggregated by median.",
                 "Only exact relations ('=') are used numerically."),
    limitations=("Every value is an experimental measurement from the literature, with that "
                 "literature's variability: replicate spreads of 100-fold or more occur.",
                 "Censored values ('>', '<') are preserved but excluded from numeric use.",
                 "The dataset covers one target and one endpoint; it is not a general activity map."),
    run=_activity_distribution))

_register(Calculation(
    id="assay_compatibility", name="Assay comparability check", category="activity",
    purpose="Decide whether two measurements may legitimately be compared at all.",
    method="Endpoint-family and unit-dimension rules from app.activity",
    inputs=(Input("endpoint_a", "text", "First endpoint, e.g. IC50."),
            Input("units_a", "text", "First units, e.g. nM."),
            Input("endpoint_b", "text", "Second endpoint, e.g. Ki."),
            Input("units_b", "text", "Second units, e.g. uM.")),
    outputs=("compatible", "reasons", "compared"),
    assumptions=("Endpoints from different families answer different questions and must not be "
                 "pooled, even when their units match.",),
    limitations=("A 'comparable' verdict concerns dimensional and endpoint compatibility only. It "
                 "does not establish that two assays used the same protocol, construct or "
                 "laboratory conditions."),
    run=_assay_compatibility))

_register(Calculation(
    id="composition_totals", name="Formulation composition totals", category="formulation",
    purpose="Compute component totals and percentages for a formulation composition.",
    method="Explicit arithmetic over recorded amounts, with unit consistency checked first",
    inputs=(Input("components", "reference", "The component list, each with name, amount, unit "
                  "and role."),),
    outputs=("components", "total", "percent_of_total", "components_without_recorded_amount",
             "mass_balance_checkable", "units_mixed"),
    assumptions=("Amounts are per single dosage unit unless the records say otherwise.",),
    limitations=("A missing amount is reported as missing and is never estimated, so any total "
                 "that excludes one is explicitly incomplete.",
                 "No total is computed across mixed units.",
                 "Composition arithmetic says nothing about whether a formulation will work."),
    run=_composition_totals))

_register(Calculation(
    id="unit_conversion", name="Concentration unit conversion", category="formulation",
    purpose="Convert a concentration between units and derive its p-activity.",
    method="Verified conversion table from app.activity, via nanomolar",
    inputs=(Input("value", "number", "The numeric value."),
            Input("from_unit", "text", "Unit to convert from, e.g. uM."),
            Input("to_unit", "text", "Unit to convert to, e.g. nM.")),
    outputs=("nanomolar", "output", "p_activity_if_concentration"),
    assumptions=("The value is a molar concentration.",),
    limitations=("Mass-per-volume units cannot be converted to molar without a molecular weight; "
                 "MOLECULAB refuses rather than assuming one.",),
    run=_unit_conversion))


_register(Calculation(
    id="molecular_dynamics", name="Molecular dynamics", category="molecular",
    purpose="Watch how a molecule moves and flexes over time at a given temperature.",
    method=("Velocity-Verlet integration of Newton's equations on the MMFF94 potential, using "
            "analytic gradients from RDKit, with a Berendsen thermostat"),
    inputs=(Input("smiles", "smiles", "The structure, as SMILES."),
            Input("temperature_k", "number", "Temperature in kelvin.",
                  required=False, default=300, minimum=1, maximum=1000),
            Input("steps", "integer", "Number of integration steps.",
                  required=False, default=800, minimum=10, maximum=4000),
            Input("timestep_fs", "number", "Timestep in femtoseconds.",
                  required=False, default=1.0, minimum=0.1, maximum=2.0),
            Input("solvent", "text",
                  "vacuum, implicit_water or implicit_organic.",
                  required=False, default="implicit_water")),
    outputs=("trace", "trajectory_frames", "summary", "temperature_k",
             "potential_energy_kcal_per_mol", "kinetic_energy_kcal_per_mol",
             "rmsd_from_start_angstrom"),
    assumptions=("Classical mechanics on a fitted force field is an adequate description of "
                 "molecular motion for this purpose.",
                 "The molecule is isolated: there is no explicit solvent and no binding partner.",
                 "A fixed random seed makes the run reproducible."),
    limitations=("No explicit solvent. Any solvent here is an implicit dielectric, which is NOT "
                 "a solvation model: there are no water molecules, no hydrogen bonds to solvent "
                 "and no hydrophobic effect.",
                 "Runs are picoseconds long. Long enough to watch a molecule relax and flex; far "
                 "too short to sample a conformational landscape or support any thermodynamic "
                 "claim.",
                 "MMFF94 cannot break or form bonds, so no chemistry occurs and there is no "
                 "electronic structure.",
                 "A Berendsen thermostat controls mean temperature but does not produce a correct "
                 "canonical ensemble, so fluctuation-derived quantities are invalid.",
                 "No protein, receptor or binding site is present. This is not a binding "
                 "simulation and produces no affinity.",
                 "Energies compare frames of this molecule in this run only; never between "
                 "different molecules."),
    deterministic=True,
    run=_molecular_dynamics))

_register(Calculation(
    id="solvent_effect", name="Implicit solvent comparison", category="molecular",
    purpose="Show how an implicit solvent environment changes a molecule's optimised shape.",
    method=("MMFF94 optimisation repeated under vacuum, water-like and organic-like dielectric "
            "models, plus solvent-accessible surface area via RDKit FreeSASA"),
    inputs=(Input("smiles", "smiles", "The structure, as SMILES."),),
    outputs=("models", "optimised_energy_kcal_per_mol", "rmsd_vs_vacuum_angstrom",
             "solvent_accessible_surface_area_A2"),
    assumptions=("Screening electrostatics with a distance-dependent dielectric approximates a "
                 "polar environment.",),
    limitations=("A dielectric constant is NOT a solvent. No water molecules are present, and "
                 "neither hydrogen bonding to solvent nor the hydrophobic effect is modelled.",
                 "Energies from different dielectric models are minima of different potentials "
                 "and must not be compared or subtracted.",
                 "No solvation free energy, solubility or logP is produced.",
                 "Solvent-accessible surface area is a geometric property of the structure, not "
                 "a measurement."),
    run=_solvent_effect))

_register(Calculation(
    id="protein_ligand_contacts", name="Protein-ligand contacts (experimental structure)",
    category="structural",
    purpose=("Measure how a ligand actually sits in its target, using a deposited experimental "
             "structure rather than a predicted pose."),
    method=("Retrieve the experimental structure from the RCSB PDB and measure heavy-atom "
            "distances between the bound ligand and surrounding protein residues"),
    inputs=(Input("pdb_id", "text", "Four-character PDB id, for example 1M17."),
            Input("ligand_code", "text",
                  "Three-character ligand code. Omit to take the largest bound ligand.",
                  required=False)),
    outputs=("contact_residues", "hydrogen_bond_candidates", "ligand", "pocket_pdb",
             "resolution_angstrom", "experimental_method"),
    assumptions=("The deposited structure represents a real binding mode under the conditions "
                 "it was determined in.",
                 "A heavy-atom distance of 4 angstrom or less is a contact."),
    limitations=("This is an OBSERVED structure. MOLECULAB performs no docking and predicts no "
                 "pose.",
                 "No binding affinity is produced. A short contact is not a binding free energy.",
                 "Hydrogen bonds are candidates based on heavy-atom distance and element; most "
                 "crystal structures do not resolve hydrogens.",
                 "One crystal conformation under crystallisation conditions is not necessarily "
                 "the physiological binding mode.",
                 "Requires network access to the RCSB PDB."),
    run=_protein_ligand))


_register(Calculation(
    id="pharmacokinetics", name="Dosing simulation (pharmacokinetics)", category="dosing",
    purpose=("Simulate what a dose does over time in a chosen species: concentration, "
             "accumulation on repeat dosing, and exposure metrics."),
    method=("Compartmental pharmacokinetics with first-order absorption, integrated with a "
            "fixed-step fourth-order Runge-Kutta solver; optional allometric scaling between "
            "species"),
    inputs=(Input("dose_mg", "number", "Dose in milligrams."),
            Input("interval_h", "number", "Hours between doses. 0 for a single dose.",
                  required=False, default=0, minimum=0, maximum=720),
            Input("duration_h", "number", "How long to simulate, in hours.",
                  required=False, default=48, minimum=1, maximum=4320),
            Input("clearance_l_per_h", "number", "Clearance, L/h. A measured or literature value.",
                  required=False, default=6.0, minimum=0.0001),
            Input("volume_l", "number", "Volume of distribution, L. Measured or literature.",
                  required=False, default=36.0, minimum=0.0001),
            Input("ka_per_h", "number", "Absorption rate constant, per hour.",
                  required=False, default=1.5, minimum=0.0001),
            Input("bioavailability", "number", "Fraction absorbed, 0 to 1.",
                  required=False, default=1.0, minimum=0.001, maximum=1),
            Input("species", "text", "human, rat, mouse, rabbit or dog.",
                  required=False, default="human"),
            Input("scale_from_human", "boolean",
                  "Treat clearance and volume as human values and scale them allometrically.",
                  required=False, default=False)),
    outputs=("series", "concentration_mg_per_l", "cmax_mg_per_l", "tmax_h", "auc_mg_h_per_l",
             "terminal_half_life_h", "accumulation_ratio", "troughs"),
    assumptions=("Linear, first-order absorption and elimination throughout.",
                 "The supplied clearance, volume, absorption rate and bioavailability are "
                 "correct for the subject being simulated.",
                 "Where allometric scaling is used, the same elimination pathway scales with "
                 "body size."),
    limitations=("PK PARAMETERS ARE INPUTS, NOT PREDICTIONS. MOLECULAB cannot derive clearance, "
                 "volume, absorption or bioavailability from a structure, and does not try.",
                 "A concentration is not an effect. This predicts neither efficacy nor safety.",
                 "No organ, tissue, disease state or individual is modelled; there is no "
                 "physiological 'before and after' for an animal or a person.",
                 "Population means hide very large between-subject variability.",
                 "Saturable (non-linear) kinetics are not modelled.",
                 "Allometric scaling fails for species-specific metabolism."),
    run=_pk))

_register(Calculation(
    id="dilution", name="Dilution and concentration", category="dosing",
    purpose="Work out the concentration when an amount is dissolved in, or diluted into, water.",
    method="Exact arithmetic: concentration = amount / total volume",
    inputs=(Input("amount_mg", "number", "Amount of compound, in milligrams."),
            Input("volume_ml", "number", "Starting volume of water, in millilitres."),
            Input("molecular_weight", "number",
                  "Molecular weight, to also report molar concentration.", required=False),
            Input("added_ml_per_event", "number", "Millilitres of water added each step.",
                  required=False, default=0),
            Input("events", "integer", "How many times water is added.",
                  required=False, default=0, minimum=0, maximum=200)),
    outputs=("steps", "concentration_mg_per_ml", "concentration_mg_per_l", "concentration_um"),
    assumptions=("The entire amount dissolves completely.",),
    limitations=("This does NOT establish that the compound is soluble at this concentration. "
                 "MOLECULAB has no measured solubility for it.",
                 "No dissolution RATE is modelled: dosage form, particle size, agitation, pH "
                 "and temperature are not used.",
                 "Nothing here describes stability, precipitation, or behaviour in a "
                 "biological fluid."),
    run=_dilution))


_register(Calculation(
    id="pbpk", name="Whole-body distribution (PBPK)", category="dosing",
    purpose=("Show where a dose goes organ by organ: liver, kidney, brain, muscle, fat and the "
             "rest, over time, in a chosen species."),
    method=("Perfusion-limited physiologically-based pharmacokinetics on published reference "
            "physiology, integrated with RK4 and checked by mass balance"),
    inputs=(Input("dose_mg", "number", "Dose in milligrams."),
            Input("species", "text", "human, rat, mouse, rabbit or dog.",
                  required=False, default="human"),
            Input("duration_h", "number", "Hours to simulate.",
                  required=False, default=24, minimum=0.1, maximum=720),
            Input("interval_h", "number", "Hours between doses; 0 for a single dose.",
                  required=False, default=0, minimum=0, maximum=720),
            Input("hepatic_cl_l_per_h", "number", "Hepatic clearance, L/h (an input).",
                  required=False, default=6.0, minimum=0),
            Input("renal_cl_l_per_h", "number", "Renal clearance, L/h (an input).",
                  required=False, default=0.0, minimum=0),
            Input("ka_per_h", "number", "Absorption rate constant, per hour.",
                  required=False, default=1.5, minimum=0.0001),
            Input("bioavailability", "number", "Fraction absorbed, 0 to 1.",
                  required=False, default=1.0, minimum=0.001, maximum=1)),
    outputs=("series", "tissues", "peak_concentration_mg_per_l", "percent_of_dose_at_end",
             "mass_balance", "plasma_mg_per_l"),
    assumptions=("Each tissue is well-stirred and perfusion-limited.",
                 "Reference physiology describes an average animal of that species.",
                 "Supplied clearances and partition coefficients are correct."),
    limitations=("PARTITION COEFFICIENTS ARE INPUTS. MOLECULAB does not estimate Kp from a "
                 "structure. The default of 1 everywhere means distribution by blood flow "
                 "alone, which no real drug follows.",
                 "THIS PREDICTS NO TOXICITY, HARM OR DEATH. A concentration in an organ is not "
                 "damage to that organ. There is no LD50 and no dose-response for harm.",
                 "Perfusion-limited assumes instant equilibration; permeability-limited drugs "
                 "and transporter substrates do not behave this way.",
                 "Protein binding, metabolites, enterohepatic recycling and active transport "
                 "are not modelled.",
                 "Reference physiology is an average animal; disease and individual variation "
                 "change organ volumes and blood flows substantially."),
    run=_pbpk))


# --------------------------------------------------------- declared-unavailable capabilities

UNAVAILABLE: tuple[dict, ...] = (
    {
        "id": "docking", "name": "Molecular docking / binding affinity",
        "category": "molecular",
        "reason": ("No docking engine is integrated, no receptor structures are prepared, no "
                   "binding sites are defined and no ligand preparation pipeline exists."),
        "what_would_be_required": ("A docking engine (for example AutoDock Vina or smina), "
                                   "prepared receptor structures from the PDB, defined binding "
                                   "sites, and a validated redocking benchmark."),
        "never_do": ("MOLECULAB must not report any binding-affinity number, pose or score while "
                     "this is unavailable. /api/v1/simulations/protein-ligand returns zero numeric "
                     "values and a test asserts that."),
        "what_exists_instead": ("Use `protein_ligand_contacts`, which measures how a ligand "
                                "ACTUALLY sits in its target in a deposited experimental "
                                "structure. Those contacts are observations, which is stronger "
                                "evidence than a predicted pose, not weaker."),
    },
    {
        "id": "qsar", "name": "QSAR / activity prediction",
        "category": "activity",
        # Figures below are reproducible from research_artifacts/jak1_curated.json. An earlier
        # draft quoted 11,700-fold and 250-fold replicate spreads for two named drugs; those came
        # from the raw pre-curation rows, which are not shipped, and curation had in fact removed
        # both compounds for exceeding the 100-fold spread limit. They are not cited any more.
        "reason": ("Gated on evidence, not on effort. In the curated JAK1 IC50 dataset "
                   "(ChEMBL_37, 7,777 compounds) 6,774 compounds carry a single unreplicated "
                   "measurement, and of the 1,003 measured more than once, 263 (26%) disagree "
                   "with themselves by tenfold or more. Curation discards any compound whose "
                   "replicates span more than 100-fold. That inter-laboratory scatter is the "
                   "practical noise floor."),
        "what_would_be_required": ("A model that beats a mean-predictor baseline on a scaffold "
                                   "split by a margin larger than that noise floor, with a stated "
                                   "applicability domain and calibrated uncertainty."),
        "never_do": ("No predicted activity may be written into an activity measurement table, "
                     "and no predicted number may be presented as a measurement."),
    },
    {
        "id": "dissolution_prediction", "name": "Dissolution / bioavailability prediction",
        "category": "formulation",
        "reason": ("No validated dissolution model is implemented, and the database holds no "
                   "measured dissolution profiles to validate one against."),
        "what_would_be_required": ("Measured dissolution data for the formulations of interest, "
                                   "and a model validated against held-out measurements."),
        "never_do": ("A computed descriptor must never close a physical formulation constraint. "
                     "Dissolution, hardness, friability and stability require measurement."),
    },
)


# --------------------------------------------------------------------------- execution

def catalogue() -> dict:
    """Everything the Bench may offer, plus everything it must honestly decline."""
    by_cat: dict[str, list[dict]] = {}
    for c in REGISTRY.values():
        by_cat.setdefault(c.category, []).append(c.describe())
    return {
        "registry_version": CALC_REGISTRY_VERSION,
        "software_versions": _versions(),
        "categories": by_cat,
        "implemented_count": len(REGISTRY),
        "unavailable": list(UNAVAILABLE),
        "contract": ("A calculation is offered only if it is registered with a real "
                     "implementation. Capabilities under 'unavailable' return no numbers."),
    }


def _versions() -> dict:
    v = simulations.software_versions()
    return {"moleculab": APP_VERSION, "calculation_registry": CALC_REGISTRY_VERSION, **v}


def execute(calc_id: str, params: dict) -> dict:
    """Run a registered calculation, returning the result with its full provenance.

    Raises KeyError for an unknown id and ValueError for bad input. A capability that is
    declared unavailable raises ValueError naming the reason, so it cannot be invoked by id.
    """
    for u in UNAVAILABLE:
        if u["id"] == calc_id:
            raise ValueError(f"{u['name']} is not available. {u['reason']}")
    if calc_id not in REGISTRY:
        raise KeyError(calc_id)
    calc = REGISTRY[calc_id]

    supplied = dict(params or {})
    call: dict[str, Any] = {}
    for spec in calc.inputs:
        if spec.name in supplied and supplied[spec.name] not in (None, ""):
            call[spec.name] = supplied[spec.name]
        elif spec.required:
            raise ValueError(f"Missing required input: {spec.name} ({spec.description})")
        elif spec.default is not None:
            call[spec.name] = spec.default

    started = time.time()
    error: str | None = None
    result: dict | None = None
    try:
        result = calc.run(**call)
    except (ValueError, KeyError) as exc:
        error = str(exc)
    except Exception as exc:                                   # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    duration_ms = int((time.time() - started) * 1000)

    versions = _versions()
    payload = {
        "calculation_id": calc.id,
        "calculation_name": calc.name,
        "category": calc.category,
        "method": calc.method,
        "inputs": call,
        "result": result,
        "error": error,
        "succeeded": error is None,
        "duration_ms": duration_ms,
        "software_versions": versions,
        "assumptions": list(calc.assumptions),
        "limitations": list(calc.limitations),
        "deterministic": calc.deterministic,
    }
    payload["reproducibility"] = {
        "fingerprint": _fingerprint(calc.id, call, versions),
        "note": ("This fingerprint covers the calculation id, the exact inputs and the software "
                 "versions. A deterministic calculation re-run with the same fingerprint inputs "
                 "produces the same result." if calc.deterministic else
                 "This calculation is not deterministic; the fingerprint identifies the inputs only."),
    }
    return payload


def _fingerprint(calc_id: str, inputs: dict, versions: dict) -> str:
    blob = json.dumps({"calculation": calc_id, "inputs": inputs, "versions": versions},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
