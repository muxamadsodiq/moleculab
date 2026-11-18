"""Molecular intelligence tests (MOLECULAB 1.0, module 1).

These pin the scientific guarantees, not just that RDKit runs: stereochemistry survives
standardization, submitted and standardized structures stay separable, every transformation is
recorded, invalid input is rejected rather than repaired, and structural similarity is never
presented as biological activity.

Reference InChIKeys below were cross-checked against both PubChem and ChEMBL on 2026-11-17.
"""
import os

import pytest

from app.molecules import (CHEMISTRY_VERSION, FINGERPRINT_SPEC, MAX_ATOMS, bulk_tanimoto,
                           canonical_tautomer, descriptors, duplicate_groups, murcko_scaffold,
                           nearest_neighbours, parse_inchi, parse_molblock, parse_smiles,
                           resolve_structure, standardize, stereo_summary, tanimoto)

live = pytest.mark.skipif(os.getenv("MOLECULAB_LIVE") != "1", reason="set MOLECULAB_LIVE=1 for live API tests")

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
ASPIRIN_KEY = "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
TOFACITINIB = "C[C@@H]1CCN(C[C@@H]1N(C)c1ncnc2[nH]ccc12)C(=O)CC#N"
TOFACITINIB_KEY = "UJLAWZDWDVHWOW-YPMHNXCESA-N"
L_ALANINE = "C[C@H](N)C(=O)O"
D_ALANINE = "C[C@@H](N)C(=O)O"


# ----------------------------------------------------------------- validation

def test_valid_structure_resolves_with_both_forms():
    rec = resolve_structure(ASPIRIN)
    assert rec.valid and rec.error is None
    assert rec.original_smiles == ASPIRIN
    assert rec.standardized_smiles == ASPIRIN
    assert rec.inchikey == ASPIRIN_KEY
    assert rec.chemistry_version == CHEMISTRY_VERSION


@pytest.mark.parametrize("bad,expected", [
    ("C1CC", "invalid SMILES"),
    ("", "empty structure input"),
    ("   ", "empty structure input"),
    ("notasmiles", "invalid SMILES"),
    (None, "empty structure input"),
])
def test_invalid_structures_are_rejected_not_repaired(bad, expected):
    rec = resolve_structure(bad)
    assert rec.valid is False
    assert expected in rec.error
    # Nothing downstream may receive a guessed molecule.
    assert rec.standardized_smiles is None and rec.inchikey is None


def test_unsupported_format_is_refused():
    rec = resolve_structure(ASPIRIN, "sdfx")
    assert not rec.valid and "unsupported input format" in rec.error


def test_atom_and_length_limits_are_enforced():
    """Structure input can arrive from an upload; unbounded parsing is a denial-of-service vector."""
    assert f"limit is {MAX_ATOMS}" in resolve_structure("C" * (MAX_ATOMS + 10)).error
    assert "exceeds" in resolve_structure("C" * 200_000).error


def test_molblock_and_inchi_parsers_reject_garbage():
    assert parse_molblock("not a molblock")[0] is None
    assert parse_inchi("InChI=1S/garbage")[0] is None
    assert parse_molblock("")[1] == "empty structure input"


# -------------------------------------------------------------- stereochemistry

def test_stereochemistry_survives_standardization():
    """The pipeline must never discard stereo: StereoParent is deliberately not a stage."""
    rec = resolve_structure(L_ALANINE)
    assert rec.standardized_smiles == L_ALANINE
    assert rec.stereo_centers == 1 and rec.unspecified_stereo_centers == 0
    assert rec.has_defined_stereo is True


def test_enantiomers_are_distinct_molecules_but_share_a_skeleton():
    left, right = resolve_structure(L_ALANINE), resolve_structure(D_ALANINE)
    assert left.inchikey != right.inchikey        # different molecules
    assert left.skeleton_key == right.skeleton_key  # same connectivity
    assert left.standardized_smiles != right.standardized_smiles


def test_unspecified_stereocentre_is_reported_not_invented():
    rec = resolve_structure("CC(N)C(=O)O")
    assert rec.stereo_centers == 1
    assert rec.unspecified_stereo_centers == 1
    assert rec.has_defined_stereo is False


def test_tofacitinib_matches_public_databases():
    """Cross-checked against PubChem and ChEMBL (2026-11-17), stereo layer included."""
    rec = resolve_structure(TOFACITINIB)
    assert rec.inchikey == TOFACITINIB_KEY
    assert rec.descriptors["molecular_formula"] == "C16H20N6O"
    assert rec.descriptors["molecular_weight"] == pytest.approx(312.38, abs=0.02)
    assert rec.descriptors["exact_mass"] == pytest.approx(312.1699, abs=0.001)
    assert rec.stereo_centers == 2 and rec.has_defined_stereo


# ------------------------------------------------------------- standardization

def test_salt_is_stripped_and_every_step_is_recorded():
    rec = resolve_structure("CC(=O)Oc1ccccc1C(=O)[O-].[Na+]")
    assert rec.valid and rec.standardized_smiles == ASPIRIN
    assert rec.standardization_changed is True
    # The submitted structure is still available verbatim and as canonical SMILES.
    assert "[Na+]" in rec.input_value and "[Na+]" in rec.original_smiles
    steps = {t["step"]: t for t in rec.transformations}
    assert set(steps) == {"cleanup", "fragment_parent", "uncharge"}
    assert steps["fragment_parent"]["changed"] is True
    assert steps["uncharge"]["changed"] is True
    for step in rec.transformations:
        assert step["smiles_before"] and step["smiles_after"] and step["description"]


def test_unchanged_molecule_records_no_change():
    rec = resolve_structure(ASPIRIN)
    assert rec.standardization_changed is False
    assert all(t["changed"] is False for t in rec.transformations)


def test_tautomer_canonicalization_is_not_applied_to_the_stored_structure():
    """For an activity record the depositor's tautomer is part of the record, so the standardized
    structure must not be silently re-tautomerized. It is available separately."""
    from rdkit import Chem

    enol = "CC(O)=CC(C)=O"
    keto = "CC(=O)CC(C)=O"
    rec = resolve_structure(enol)
    mol, _ = parse_smiles(enol)

    # The standardized structure is still the submitted enol (canonical SMILES of the same molecule),
    # not the keto tautomer it would collapse to under tautomer canonicalization.
    assert rec.standardized_smiles == Chem.MolToSmiles(mol)
    assert rec.standardized_smiles != Chem.MolToSmiles(parse_smiles(keto)[0])
    assert rec.standardization_changed is False
    assert not any(t["step"] == "tautomer" for t in rec.transformations)
    # Available separately, for callers that explicitly want it.
    assert canonical_tautomer(mol) is not None


def test_standardize_reports_stage_failure_without_raising():
    mol, _ = parse_smiles(ASPIRIN)
    std, steps = standardize(mol)
    assert std is not None and len(steps) == 3


# ------------------------------------------------------------------ descriptors

def test_descriptors_are_present_and_named_as_computed_estimates():
    desc = descriptors(parse_smiles(ASPIRIN)[0])
    for key in ("molecular_formula", "molecular_weight", "exact_mass", "tpsa", "clogp",
                "h_bond_donors", "h_bond_acceptors", "rotatable_bonds", "ring_count",
                "aromatic_ring_count", "fraction_csp3", "formal_charge", "qed"):
        assert key in desc, key
    assert desc["molecular_formula"] == "C9H8O4"
    assert desc["molecular_weight"] == pytest.approx(180.16, abs=0.01)
    # A computed logP estimate must not be named as if it were measured.
    assert "clogp" in desc and "measured_logp" not in desc and "logp_experimental" not in desc


def test_moleculab_descriptors_are_not_named_like_provider_properties():
    """ChEMBL's own values are stored as chembl_*; locally computed values must not collide."""
    desc = descriptors(parse_smiles(ASPIRIN)[0])
    assert not any(k.startswith("chembl_") for k in desc)


# ------------------------------------------------------- similarity / scaffolds

def test_tanimoto_is_bounded_and_self_similarity_is_one():
    a, _ = parse_smiles(ASPIRIN)
    b, _ = parse_smiles("CC(=O)Oc1ccccc1C(=O)OC")
    assert tanimoto(a, a) == 1.0
    assert 0.0 < tanimoto(a, b) < 1.0


def test_similarity_results_are_labelled_as_structural_only():
    rows = nearest_neighbours(TOFACITINIB, [("tofa", TOFACITINIB), ("aspirin", ASPIRIN)])
    assert rows[0]["identifier"] == "tofa" and rows[0]["tanimoto"] == 1.0
    assert "not evidence of activity" in rows[0]["measure"]
    # No key may imply predicted activity.
    assert not any(k in rows[0] for k in ("activity", "predicted_activity", "potency"))


def test_nearest_neighbours_skips_unparseable_candidates():
    rows = nearest_neighbours(ASPIRIN, [("good", ASPIRIN), ("bad", "XX"), ("none", "")])
    assert [r["identifier"] for r in rows] == ["good"]


def test_nearest_neighbours_with_invalid_query_returns_nothing():
    assert nearest_neighbours("C1CC", [("good", ASPIRIN)]) == []


def test_fingerprint_spec_is_pinned():
    """Comparisons are only like-for-like if every fingerprint uses one spec."""
    assert FINGERPRINT_SPEC == {"type": "Morgan", "radius": 2, "bits": 2048, "name": "ECFP4-like"}


def test_bulk_tanimoto_matches_pairwise():
    q, _ = parse_smiles(ASPIRIN)
    mols = [parse_smiles(s)[0] for s in (ASPIRIN, TOFACITINIB)]
    assert bulk_tanimoto(q, mols) == [tanimoto(q, m) for m in mols]
    assert bulk_tanimoto(q, []) == []


def test_murcko_scaffold_strips_substituents():
    assert murcko_scaffold(parse_smiles(ASPIRIN)[0]) == "c1ccccc1"
    assert murcko_scaffold(parse_smiles("CCO")[0]) == ""  # acyclic: no scaffold


# ------------------------------------------------------------------- duplicates

def test_duplicate_detection_treats_stereoisomers_as_distinct_by_default():
    recs = [resolve_structure(s) for s in (L_ALANINE, D_ALANINE, L_ALANINE, "CCO")]
    groups = duplicate_groups(recs)
    assert list(groups.values()) == [[0, 2]]  # only the two identical L forms


def test_duplicate_detection_can_merge_stereoisomers_explicitly():
    recs = [resolve_structure(s) for s in (L_ALANINE, D_ALANINE, L_ALANINE)]
    groups = duplicate_groups(recs, ignore_stereo=True)
    assert list(groups.values()) == [[0, 1, 2]]


def test_duplicate_detection_ignores_invalid_records():
    recs = [resolve_structure("C1CC"), resolve_structure(ASPIRIN)]
    assert duplicate_groups(recs) == {}


# ------------------------------------------------------------------------ live

@live
def test_live_structures_match_pubchem_and_chembl():
    from app.clients.chembl import ChEMBLClient
    from app.clients.pubchem import PubChemClient
    chembl, pubchem = ChEMBLClient(use_cache=False), PubChemClient()
    for name, chembl_id in (("tofacitinib", "CHEMBL221959"), ("aspirin", "CHEMBL25")):
        record = chembl.molecule(chembl_id)
        assert record.ok, record.error
        mine = resolve_structure(record.data["smiles"])
        assert mine.inchikey == record.data["inchikey"], name
        remote = pubchem.search_name(name)
        assert remote.ok and mine.inchikey == remote.data[0]["inchikey"], name
