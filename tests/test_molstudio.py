"""Molecular Studio tests (MOLECULAB 1.0).

The adversarial point of this file: a 3D viewer is the easiest place in the product to imply something
untrue. These tests fail if a computed conformer is ever labelled experimental, if docking language
appears, or if a failed computation is replaced by a fabricated geometry.
"""
import pytest

from app.molstudio import (CONFORMER_DISCLAIMER, MAX_3D_ATOMS, atom_bond_inventory, compare,
                           conformer_3d, depict_2d, studio_payload)

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
ARTEMISININ = "C[C@@H]1CC[C@H]2[C@H](C(=O)O[C@H]3[C@@]24[C@H]1CC[C@](O3)(OO4)C)C"
L_ALANINE = "C[C@H](N)C(=O)O"


# ------------------------------------------------------------------ 2D depiction

def test_2d_depiction_returns_svg_labelled_as_computed():
    result = depict_2d(ASPIRIN)
    assert result["ok"] is True
    assert result["svg"].lstrip().startswith("<?xml") or "<svg" in result["svg"]
    assert result["structure_origin"] == "computed_depiction"
    assert "no physical meaning" in result["disclaimer"]


def test_2d_depiction_rejects_invalid_structure():
    result = depict_2d("C1CC")
    assert result["ok"] is False
    assert "invalid SMILES" in result["error"]
    assert result["structure_origin"] is None
    assert "svg" not in result


def test_2d_depiction_is_legible_on_a_dark_background():
    """Regression: the first implementation drew black atoms on a dark panel."""
    import re
    svg = depict_2d(ASPIRIN)["svg"]
    strokes = set(re.findall(r"stroke:#([0-9A-Fa-f]{6})", svg))
    assert strokes, "no stroke colours found in the SVG"
    # At least one stroke must be light enough to read against the dark shell.
    assert any(int(c[0:2], 16) > 170 for c in strokes)


# ------------------------------------------------------------------ 3D conformer

def test_3d_conformer_is_labelled_computed_never_experimental():
    result = conformer_3d(ASPIRIN)
    assert result["ok"] is True
    assert result["structure_origin"] == "computed_conformer"
    assert result["structure_origin"] != "experimental"
    assert "NOT an experimentally determined structure" in result["disclaimer"]
    assert "No docking was performed" in result["disclaimer"]


def test_3d_conformer_returns_a_real_molblock():
    result = conformer_3d(ASPIRIN)
    molblock = result["molblock"]
    assert "V2000" in molblock or "V3000" in molblock
    # A molblock with coordinates has more lines than the atom count.
    assert len(molblock.splitlines()) > result["atom_count"]
    assert result["atom_count"] > result["heavy_atom_count"]  # hydrogens were added


def test_3d_conformer_is_deterministic():
    """A fixed seed means a demo shows the same geometry every time."""
    assert conformer_3d(ASPIRIN)["molblock"] == conformer_3d(ASPIRIN)["molblock"]
    assert conformer_3d(ASPIRIN)["method"]["deterministic"] is True


def test_3d_conformer_records_its_method():
    method = conformer_3d(ASPIRIN)["method"]
    assert "ETKDG" in method["embedding"]
    assert method["force_field"] in ("MMFF94", "UFF", None)
    assert method["random_seed"] is not None


def test_3d_conformer_preserves_stereochemistry():
    left = conformer_3d(L_ALANINE)
    right = conformer_3d("C[C@@H](N)C(=O)O")
    assert left["ok"] and right["ok"]
    # Enantiomers must not produce an identical geometry.
    assert left["molblock"] != right["molblock"]


def test_3d_conformer_handles_many_stereocentres():
    result = conformer_3d(ARTEMISININ)
    assert result["ok"] is True and result["atom_count"] == 42


def test_3d_conformer_failure_is_reported_not_faked():
    result = conformer_3d("C1CC")
    assert result["ok"] is False
    assert result["structure_origin"] is None
    assert "molblock" not in result


def test_3d_conformer_enforces_a_size_limit():
    result = conformer_3d("C" * (MAX_3D_ATOMS + 5))
    assert result["ok"] is False
    assert str(MAX_3D_ATOMS) in result["error"]


# ------------------------------------------------------------------- payload

def test_studio_payload_separates_submitted_and_standardized_structures():
    payload = studio_payload("CC(=O)Oc1ccccc1C(=O)[O-].[Na+]", name="aspirin sodium")
    assert payload["ok"] is True
    assert "[Na+]" in payload["original_smiles"]
    assert payload["standardized_smiles"] == ASPIRIN
    assert payload["standardization_changed"] is True
    assert any(t["step"] == "fragment_parent" for t in payload["transformations"])


def test_studio_payload_declares_the_stored_structure_is_not_experimental():
    payload = studio_payload(ASPIRIN, name="aspirin")
    assert payload["source_structure_origin"] == "imported"
    assert "No experimentally determined 3D structure" in payload["source_structure_note"]


def test_studio_payload_never_claims_docking_or_binding():
    import json
    body = json.dumps(studio_payload(ARTEMISININ, name="artemisinin")).lower()
    for forbidden in ("docking_score", "binding_energy", "kcal/mol", "binding_affinity",
                      "binding_pose", "predicted_activity"):
        assert forbidden not in body
    # Docking must be mentioned only to deny it.
    assert "no docking was performed" in body


def test_studio_payload_reports_stereo_and_descriptors():
    payload = studio_payload(ARTEMISININ, name="artemisinin")
    assert payload["stereo"]["stereo_centers"] == 7
    assert payload["stereo"]["has_defined_stereo"] is True
    assert payload["descriptors"]["molecular_formula"] == "C15H22O5"
    assert payload["inchikey"] == "BLUAFEHZUWYNDE-NNWCWBAJSA-N"


def test_studio_payload_invalid_structure_is_explicit():
    payload = studio_payload("not-a-molecule", name="junk")
    assert payload["ok"] is False and payload["error"]
    assert "descriptors" not in payload


def test_studio_payload_can_skip_3d():
    payload = studio_payload(ASPIRIN, want_3d=False)
    assert payload["conformer_3d"]["ok"] is False
    assert payload["depiction_2d"]["ok"] is True


# ----------------------------------------------------------------- inventory

def test_atom_bond_inventory_counts_elements():
    inventory = atom_bond_inventory(ASPIRIN)
    assert inventory["ok"] and inventory["elements"] == {"C": 9, "O": 4}
    assert inventory["rings"] == 1


def test_atom_bond_inventory_rejects_invalid():
    assert atom_bond_inventory("C1CC")["ok"] is False


# ------------------------------------------------------------------ compare

def test_compare_reports_similarity_with_its_caveat():
    result = compare(ASPIRIN, "CC(=O)Oc1ccccc1C(=O)OC")
    assert result["ok"] and 0 < result["tanimoto"] < 1
    assert "not evidence of shared biological activity" in result["similarity_note"]
    assert result["descriptors"]["molecular_weight"]["delta"] != 0


def test_compare_rejects_invalid_structure():
    assert compare(ASPIRIN, "C1CC")["ok"] is False


# ---------------------------------------------------------------- API routes

def test_molecule_library_lists_records_and_flags_missing_structures(client):
    body = client.get("/api/v1/molecules/").json()
    assert body["count"] > 0
    assert any(i["has_structure"] for i in body["items"])
    # A compound without a structure is listed honestly, not hidden.
    assert any(i["has_structure"] is False for i in body["items"])
    assert "no experimentally determined 3d structures" in body["note"].lower()


def test_compound_studio_endpoint_returns_full_payload(client):
    compounds = client.get("/api/compounds").json()
    target = next(c for c in compounds if c["name"] == "artemisinin")
    body = client.get(f"/api/v1/molecules/compound/{target['id']}").json()
    assert body["ok"] is True
    assert body["name"] == "artemisinin"
    assert body["depiction_2d"]["ok"] and body["conformer_3d"]["ok"]
    assert body["conformer_3d"]["structure_origin"] == "computed_conformer"
    assert body["evidence_count"] >= 1


def test_compound_without_structure_returns_explicit_unavailable(client):
    # /api/compounds does not expose SMILES, so the library endpoint is the authority on
    # which records actually carry a structure.
    library = client.get("/api/v1/molecules/").json()["items"]
    missing = next((i for i in library
                    if i["source"] == "compound" and i["has_structure"] is False), None)
    if missing is None:
        pytest.skip("every compound in the corpus has a structure")
    body = client.get(f"/api/v1/molecules/compound/{missing['id']}").json()
    assert body["ok"] is False and body["structure_available"] is False
    assert "No structure is stored" in body["error"]


def test_unknown_compound_is_404(client):
    assert client.get("/api/v1/molecules/compound/999999").status_code == 404


def test_smiles_endpoint_accepts_ad_hoc_structure(client):
    body = client.post("/api/v1/molecules/smiles",
                       json={"smiles": ASPIRIN, "name": "aspirin"}).json()
    assert body["ok"] is True and body["inchikey"] == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"


def test_smiles_endpoint_reports_invalid_structure_without_500(client):
    response = client.post("/api/v1/molecules/smiles", json={"smiles": "C1CC"})
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_smiles_endpoint_rejects_oversized_input(client):
    response = client.post("/api/v1/molecules/smiles", json={"smiles": "C" * 5000})
    assert response.status_code == 422  # pydantic max_length, before RDKit is touched


def test_compare_endpoint(client):
    body = client.post("/api/v1/molecules/compare",
                       json={"smiles_a": ASPIRIN, "smiles_b": ARTEMISININ}).json()
    assert 0 <= body["tanimoto"] <= 1
    assert "not evidence" in body["similarity_note"]


def test_compare_endpoint_rejects_invalid(client):
    response = client.post("/api/v1/molecules/compare",
                           json={"smiles_a": ASPIRIN, "smiles_b": "C1CC"})
    assert response.status_code == 422
