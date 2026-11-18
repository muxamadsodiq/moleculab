"""Simulation Lab tests.

These pin that the simulations actually compute, that their parameters actually change the result,
and that the one capability MOLECULAB lacks reports itself as missing instead of producing numbers.
"""
import pytest

from app import simulations as sim

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
BARICITINIB = "CCS(=O)(=O)N1CC(CC#N)(n2cc(-c3ncnc4[nH]ccc34)cn2)C1"
FLEXIBLE = "CCCCCCCCOC(=O)CCCCN"


# ------------------------------------------------------------ A: conformers

def test_conformer_ensemble_computes_distinct_energies():
    result = sim.conformer_ensemble(BARICITINIB, n_conformers=6)
    assert result["ok"] and result["result_kind"] == "computed"
    assert result["generated_conformers"] >= 2
    energies = [c["energy_kcal_per_mol"] for c in result["conformers"]]
    assert all(e is not None for e in energies)
    assert len(set(energies)) > 1, "a real ensemble has more than one energy"
    # Sorted ascending, lowest first, with relative energies measured from it.
    assert energies == sorted(energies)
    assert result["conformers"][0]["relative_energy_kcal_per_mol"] == 0.0
    assert all(c["relative_energy_kcal_per_mol"] >= 0 for c in result["conformers"])


def test_conformer_count_parameter_actually_changes_the_computation():
    """A control that does not change the result is decoration, not a parameter."""
    few = sim.conformer_ensemble(FLEXIBLE, n_conformers=2, prune_rms=0.0)
    many = sim.conformer_ensemble(FLEXIBLE, n_conformers=12, prune_rms=0.0)
    assert few["generated_conformers"] < many["generated_conformers"]


def test_conformer_generation_is_deterministic():
    a = sim.conformer_ensemble(ASPIRIN, n_conformers=4)
    b = sim.conformer_ensemble(ASPIRIN, n_conformers=4)
    assert [c["energy_kcal_per_mol"] for c in a["conformers"]] == \
           [c["energy_kcal_per_mol"] for c in b["conformers"]]


def test_conformer_rmsd_is_measured_from_the_lowest_energy_structure():
    result = sim.conformer_ensemble(FLEXIBLE, n_conformers=6, prune_rms=0.0)
    assert result["conformers"][0]["rmsd_to_lowest_angstrom"] == 0.0
    others = [c["rmsd_to_lowest_angstrom"] for c in result["conformers"][1:]]
    assert any(r and r > 0 for r in others)


def test_conformer_energy_is_never_called_binding_or_activity():
    import json
    body = json.dumps(sim.conformer_ensemble(ASPIRIN, n_conformers=2)).lower()
    for forbidden in ("binding_affinity", "docking_score", "predicted_activity", "ic50", "kcal/mol binding"):
        assert forbidden not in body
    assert "not binding affinity" in body
    assert "energy_kcal_per_mol" in body


def test_conformer_ensemble_reports_its_method():
    method = sim.conformer_ensemble(ASPIRIN, n_conformers=2)["method"]
    assert "ETKDG" in method["embedding"]
    assert method["force_field"] in ("MMFF94", "UFF")
    assert method["deterministic"] is True and method["random_seed"]


def test_conformer_ensemble_rejects_invalid_structure():
    result = sim.conformer_ensemble("C1CC")
    assert result["ok"] is False and result["result_kind"] == "unavailable"
    assert "conformers" not in result


def test_conformer_count_is_capped():
    result = sim.conformer_ensemble(ASPIRIN, n_conformers=10_000)
    assert result["requested_conformers"] <= sim.MAX_CONFORMERS


# ------------------------------------------------------------ C: similarity

def test_similarity_search_ranks_by_real_fingerprint_similarity():
    library = [{"name": "self", "smiles": ASPIRIN},
               {"name": "methyl ester", "smiles": "CC(=O)Oc1ccccc1C(=O)OC"},
               {"name": "unrelated", "smiles": "CCO"}]
    result = sim.similarity_search(ASPIRIN, library)
    assert result["ok"] and result["result_kind"] == "computed"
    assert result["results"][0]["name"] == "self" and result["results"][0]["tanimoto"] == 1.0
    scores = [r["tanimoto"] for r in result["results"]]
    assert scores == sorted(scores, reverse=True)
    assert result["results"][-1]["name"] == "unrelated"


def test_similarity_counts_unparseable_library_entries():
    result = sim.similarity_search(ASPIRIN, [{"name": "bad", "smiles": "XX"},
                                             {"name": "ok", "smiles": ASPIRIN}])
    assert result["skipped_unparseable"] == 1 and result["compared"] == 1


def test_similarity_states_it_is_not_activity():
    result = sim.similarity_search(ASPIRIN, [{"name": "x", "smiles": ASPIRIN}])
    assert "not evidence of shared biological activity" in result["interpretation"]
    assert result["method"]["fingerprint"]["bits"] == 2048


def test_similarity_rejects_invalid_query():
    assert sim.similarity_search("C1CC", [{"smiles": ASPIRIN}])["ok"] is False


# ------------------------------------------------------------ B: properties

def test_property_matrix_computes_descriptors():
    result = sim.property_matrix([{"name": "aspirin", "smiles": ASPIRIN}])
    assert result["result_kind"] == "computed" and result["count"] == 1
    row = result["rows"][0]
    assert row["molecular_weight"] == pytest.approx(180.16, abs=0.01)
    assert "clogp" in row and "tpsa" in row
    assert "drug-like" not in result["interpretation"].lower().replace("drug-like,", "")
    assert "not measured values" in result["interpretation"]


def test_property_matrix_skips_unparseable():
    result = sim.property_matrix([{"smiles": "XX"}, {"smiles": ASPIRIN}])
    assert result["count"] == 1 and result["skipped_unparseable"] == 1


# ------------------------------------------------------------ D: activity

def test_activity_dataset_info_describes_its_provenance():
    info = sim.activity_dataset_info()
    assert info["result_kind"] == "experimental"
    assert info["source"] == "ChEMBL release 37"
    assert info["target"]["chembl_target_id"] == "CHEMBL2835"
    assert "IC50 is not a Ki" in info["compatibility_rule"]
    assert any("11,700-fold" in limitation for limitation in info["limitations"])


@pytest.mark.skipif(not sim.activity_dataset_info()["available"],
                    reason="curated activity dataset not installed")
def test_activity_explore_filters_real_measurements():
    everything = sim.activity_explore(limit=5)
    assert everything["ok"] and everything["result_kind"] == "experimental"
    assert everything["matched"] > 1000
    potent = sim.activity_explore(min_p_activity=9.0, limit=5)
    assert potent["matched"] < everything["matched"]
    assert all(r["p_activity_median"] >= 9.0 for r in potent["rows"])
    assert potent["statistics"]["min"] >= 9.0


@pytest.mark.skipif(not sim.activity_dataset_info()["available"],
                    reason="curated activity dataset not installed")
def test_activity_histogram_sums_to_the_match_count():
    result = sim.activity_explore(min_p_activity=10.0)
    assert sum(b["count"] for b in result["histogram"]) == result["matched"]


@pytest.mark.skipif(not sim.activity_dataset_info()["available"],
                    reason="curated activity dataset not installed")
def test_activity_replicate_filter_is_real():
    single = sim.activity_explore(min_replicates=1)
    replicated = sim.activity_explore(min_replicates=3)
    assert replicated["matched"] < single["matched"]


# -------------------------------------------------- E: protein-ligand absent

def test_protein_ligand_reports_unavailable_and_produces_no_numbers():
    status = sim.protein_ligand_status()
    assert status["available"] is False and status["result_kind"] == "unavailable"

    # No result-bearing key, and no numeric value of any kind: the payload is prose and flags only.
    # (The words "pose" and "docking" do appear — in the explanation of what is NOT done.)
    for forbidden in ("binding_energy", "docking_score", "affinity", "score", "pose", "energy",
                      "results", "poses"):
        assert forbidden not in status, f"unavailable payload must not carry a {forbidden!r} key"

    def numbers(value):
        if isinstance(value, bool):
            return []
        if isinstance(value, (int, float)):
            return [value]
        if isinstance(value, dict):
            return [n for v in value.values() for n in numbers(v)]
        if isinstance(value, list):
            return [n for v in value for n in numbers(v)]
        return []

    assert numbers(status) == [], "an unavailable capability must produce no numbers at all"
    assert status["what_would_be_required"]
    assert "no docking engine" in status["reason"].lower()


# ------------------------------------------------------------- API surface

def test_catalogue_declares_status_per_simulation(client):
    body = client.get("/api/v1/simulations/").json()
    by_id = {s["id"]: s for s in body["simulations"]}
    assert by_id["conformers"]["status"] == "available"
    assert by_id["protein_ligand"]["status"] == "unavailable"
    assert by_id["activity"]["result_kind"] == "experimental"
    assert by_id["similarity"]["result_kind"] == "computed"


def test_conformer_endpoint(client):
    body = client.post("/api/v1/simulations/conformers",
                       json={"smiles": ASPIRIN, "n_conformers": 3}).json()
    assert body["ok"] and len(body["conformers"]) >= 1
    assert body["conformers"][0]["molblock"]


def test_conformer_endpoint_rejects_invalid_structure(client):
    assert client.post("/api/v1/simulations/conformers", json={"smiles": "C1CC"}).status_code == 422


def test_conformer_endpoint_enforces_bounds(client):
    assert client.post("/api/v1/simulations/conformers",
                       json={"smiles": ASPIRIN, "n_conformers": 999}).status_code == 422


def test_similarity_endpoint_searches_the_local_library(client):
    body = client.post("/api/v1/simulations/similarity",
                       json={"smiles": ASPIRIN, "top_n": 5}).json()
    assert body["ok"] and body["library_size"] > 0
    assert all(0 <= r["tanimoto"] <= 1 for r in body["results"])


def test_properties_endpoint(client):
    body = client.get("/api/v1/simulations/properties").json()
    assert body["count"] > 0 and body["fields"]
    assert any(f["key"] == "clogp" for f in body["fields"])


def test_protein_ligand_endpoint_is_200_not_404(client):
    """The absence is the information, so it is served, not hidden behind an error."""
    response = client.get("/api/v1/simulations/protein-ligand")
    assert response.status_code == 200
    assert response.json()["available"] is False
