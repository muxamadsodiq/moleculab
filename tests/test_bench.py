"""Research Bench and calculation registry.

These tests pin the two promises the Bench makes to a researcher:

1. A calculation is offered only if it genuinely runs, and its result carries the inputs, method,
   software versions and limitations needed to reproduce and interpret it.
2. A capability MOLECULAB does not have returns a refusal with a reason, never a number.

They also pin the reuse decision: a Bench session IS a ResearchWorkspace, so starting an
investigation must not require a new table.
"""

from __future__ import annotations

import pytest

from app import bench, calculations
from app.models import ResearchWorkspace


# --------------------------------------------------------------------- registry contract

def test_every_registered_calculation_has_an_implementation():
    assert calculations.REGISTRY, "the registry must not be empty"
    for cid, calc in calculations.REGISTRY.items():
        assert callable(calc.run), f"{cid} is registered without a callable"
        assert calc.purpose and calc.method, f"{cid} must declare purpose and method"
        assert calc.limitations, f"{cid} must declare at least one limitation"
        assert calc.outputs, f"{cid} must declare its outputs"


def test_catalogue_separates_implemented_from_unavailable():
    cat = calculations.catalogue()
    offered = {c["id"] for items in cat["categories"].values() for c in items}
    declined = {u["id"] for u in cat["unavailable"]}
    assert offered, "the catalogue must offer something"
    assert declined, "the catalogue must name what it cannot do"
    assert not (offered & declined), "a capability cannot be both offered and unavailable"
    assert cat["implemented_count"] == len(calculations.REGISTRY)


@pytest.mark.parametrize("unavailable_id", ["docking", "qsar", "dissolution_prediction"])
def test_unavailable_capabilities_refuse_and_return_no_numbers(unavailable_id):
    """The central integrity rule: a missing method must not produce a plausible number."""
    with pytest.raises(ValueError) as exc:
        calculations.execute(unavailable_id, {})
    assert "not available" in str(exc.value).lower()
    # And it must not be silently present in the registry either.
    assert unavailable_id not in calculations.REGISTRY


def test_unknown_calculation_raises_keyerror():
    with pytest.raises(KeyError):
        calculations.execute("no_such_calculation", {})


def test_missing_required_input_is_refused_not_guessed():
    with pytest.raises(ValueError) as exc:
        calculations.execute("molecular_descriptors", {})
    assert "smiles" in str(exc.value).lower()


# --------------------------------------------------------------------- real computation

def test_descriptors_match_known_values_for_aspirin():
    r = calculations.execute("molecular_descriptors", {"smiles": "CC(=O)Oc1ccccc1C(=O)O"})
    assert r["succeeded"], r["error"]
    d = r["result"]["descriptors"]
    assert d["molecular_formula"] == "C9H8O4"
    assert d["molecular_weight"] == pytest.approx(180.16, abs=0.05)
    assert r["software_versions"]["rdkit"]
    assert r["reproducibility"]["fingerprint"]


def test_identical_input_reproduces_identical_fingerprint_and_result():
    a = calculations.execute("molecular_descriptors", {"smiles": "c1ccccc1"})
    b = calculations.execute("molecular_descriptors", {"smiles": "c1ccccc1"})
    assert a["reproducibility"]["fingerprint"] == b["reproducibility"]["fingerprint"]
    assert a["result"] == b["result"]


def test_different_input_gives_a_different_fingerprint():
    a = calculations.execute("molecular_descriptors", {"smiles": "c1ccccc1"})
    b = calculations.execute("molecular_descriptors", {"smiles": "CCO"})
    assert a["reproducibility"]["fingerprint"] != b["reproducibility"]["fingerprint"]


def test_invalid_structure_fails_without_inventing_descriptors():
    r = calculations.execute("molecular_descriptors", {"smiles": "this is not a molecule"})
    assert r["succeeded"] is False
    assert r["result"] is None
    assert r["error"]


def test_structure_validation_preserves_the_original_alongside_the_standardized():
    """A salt must standardize, and BOTH forms must be reported."""
    r = calculations.execute("structure_validation",
                             {"smiles": "CC(=O)Oc1ccccc1C(=O)O.[Na+]"})
    res = r["result"]
    assert res["valid"]
    assert res["input_smiles"] == "CC(=O)Oc1ccccc1C(=O)O.[Na+]"
    assert res["changed_by_standardization"] is True
    assert "[Na+]" not in res["standardized_smiles"], "the salt should be stripped"
    assert res["standardization_steps"], "every step must be recorded, not applied silently"


def test_stereochemistry_is_not_normalised_away():
    chiral = "C[C@H](N)C(=O)O"
    r = calculations.execute("structure_validation", {"smiles": chiral})
    assert r["result"]["valid"]
    assert "@" in r["result"]["standardized_smiles"], "stereochemistry must survive"


def test_lipinski_is_reported_as_separate_criteria_not_a_score():
    r = calculations.execute("lipinski", {"smiles": "CC(=O)Oc1ccccc1C(=O)O"})
    res = r["result"]
    assert len(res["criteria"]) == 4
    assert all("satisfied" in c and "threshold" in c for c in res["criteria"])
    assert "score" not in res, "no composite score may be produced"
    assert "not a prediction" in res["interpretation"].lower()


def test_incompatible_endpoints_are_refused_for_comparison():
    """IC50 and Ki measure different quantities and must never be pooled."""
    r = calculations.execute("assay_compatibility",
                             {"endpoint_a": "IC50", "units_a": "nM",
                              "endpoint_b": "Ki", "units_b": "nM"})
    assert r["result"]["compatible"] is False
    assert r["result"]["reasons"]


def test_same_endpoint_different_units_is_comparable():
    r = calculations.execute("assay_compatibility",
                             {"endpoint_a": "IC50", "units_a": "nM",
                              "endpoint_b": "IC50", "units_b": "uM"})
    assert r["result"]["compatible"] is True


def test_unit_conversion_is_case_sensitive_and_refuses_mass_per_volume():
    r = calculations.execute("unit_conversion",
                             {"value": 5, "from_unit": "uM", "to_unit": "nM"})
    assert r["result"]["output"]["value"] == pytest.approx(5000.0)
    # mg/mL cannot become molar without a molecular weight, which must not be assumed.
    bad = calculations.execute("unit_conversion",
                               {"value": 5, "from_unit": "mg/mL", "to_unit": "nM"})
    assert bad["succeeded"] is False


def test_composition_totals_never_invent_a_missing_amount():
    r = calculations.execute("composition_totals", {"components": [
        {"name": "API", "amount": 2, "unit": "mg", "role": "active"},
        {"name": "Filler", "amount": 150, "unit": "mg", "role": "filler"},
        {"name": "Lubricant", "unit": "mg", "role": "lubricant"},   # amount unknown
    ]})
    res = r["result"]
    assert res["total"] == pytest.approx(152.0)
    assert res["components_without_recorded_amount"] == ["Lubricant"]
    assert res["mass_balance_checkable"] is False, (
        "a composition with an unknown amount cannot have its mass balance checked")
    missing_row = next(c for c in res["components"] if c["name"] == "Lubricant")
    assert missing_row["amount"] is None
    assert missing_row["percent_of_total"] is None


def test_composition_totals_refuse_a_total_across_mixed_units():
    r = calculations.execute("composition_totals", {"components": [
        {"name": "A", "amount": 2, "unit": "mg"},
        {"name": "B", "amount": 1, "unit": "g"},
    ]})
    assert r["result"]["units_mixed"] is True
    assert r["result"]["total"] is None


# --------------------------------------------------------------------- sessions reuse

def test_a_bench_session_is_a_research_workspace(session):
    """The reuse decision, pinned: no new table is needed to start an investigation."""
    ws = bench.create_session(
        session, objective="I want to investigate a compound and its biological activity.",
        mode="compound")
    session.flush()
    assert isinstance(ws, ResearchWorkspace)
    assert ws.research_question == ("I want to investigate a compound and its biological "
                                   "activity.")
    assert ws.research_domain == "compound", "the Bench mode lives in research_domain"


def test_session_title_is_derived_from_the_objective(session):
    ws = bench.create_session(session, objective="I want to investigate how an existing tablet "
                                                  "formulation could be improved.",
                              mode="formulation")
    assert not ws.title.lower().startswith("i want to")
    assert "tablet" in ws.title.lower()


def test_unknown_mode_is_refused(session):
    with pytest.raises(ValueError) as exc:
        bench.create_session(session, objective="anything", mode="not_a_mode")
    assert "unknown research mode" in str(exc.value).lower()


def test_empty_objective_is_refused(session):
    with pytest.raises(ValueError):
        bench.create_session(session, objective="   ", mode="open")


def test_session_state_reports_progress_from_real_content(session):
    ws = bench.create_session(session, objective="Open question about caper alkaloids.",
                              mode="open")
    session.flush()
    st = bench.session_state(session, ws)
    assert st["session_id"] == ws.id
    assert len(st["stages"]) == 8
    labels = [s["label"] for s in st["stages"]]
    assert labels == ["Research", "Evidence", "Assist", "Explore", "Calculate", "Compare",
                      "Record", "Report"]
    research_stage = next(s for s in st["stages"] if s["id"] == "research")
    assert research_stage["complete"] is True, "an existing session completes the first stage"
    report_stage = next(s for s in st["stages"] if s["id"] == "report")
    assert report_stage["complete"] is False, "a new session has no report"
    assert st["next_step"]["stage"] != "research"


def test_start_options_offer_every_mode_and_stage():
    o = bench.start_options()
    assert len(o["modes"]) == len(bench.MODES)
    assert len(o["stages"]) == 8
    assert o["examples"]
    for m in o["modes"]:
        assert m["prompt"] and m["first_steps"]


# --------------------------------------------------------------------- API surface

def test_bench_start_endpoint(client):
    r = client.get("/api/v1/bench/start")
    assert r.status_code == 200
    d = r.json()
    assert d["modes"] and d["stages"]
    assert d["catalogue_summary"]["calculations_available"] == len(calculations.REGISTRY)


def test_calculation_catalogue_endpoint(client):
    r = client.get("/api/v1/bench/calculations")
    assert r.status_code == 200
    assert r.json()["implemented_count"] == len(calculations.REGISTRY)


def test_run_calculation_endpoint(client):
    r = client.post("/api/v1/bench/calculations/molecular_descriptors/run",
                    json={"smiles": "CCO"})
    assert r.status_code == 200
    assert r.json()["result"]["descriptors"]["molecular_formula"] == "C2H6O"


def test_run_unavailable_calculation_is_refused_by_the_api(client):
    r = client.post("/api/v1/bench/calculations/qsar/run", json={})
    assert r.status_code == 422
    assert "not available" in r.json()["detail"].lower()


def test_create_and_read_back_a_session_through_the_api(client):
    r = client.post("/api/v1/bench/sessions",
                    json={"objective": "Investigate a compound against JAK1.",
                          "mode": "compound"})
    assert r.status_code == 201
    sid = r.json()["session_id"]
    back = client.get(f"/api/v1/bench/sessions/{sid}")
    assert back.status_code == 200
    assert back.json()["objective"] == "Investigate a compound against JAK1."


def test_missing_session_is_404(client):
    assert client.get("/api/v1/bench/sessions/999999").status_code == 404


def test_structure_comparison_needs_two_parseable_structures(client):
    r = client.post("/api/v1/bench/structures/compare", json={"structures": [
        {"label": "ok", "smiles": "c1ccccc1"},
        {"label": "broken", "smiles": "not a molecule"}]})
    assert r.status_code == 422


def test_structure_comparison_reports_similarity_without_claiming_equivalence(client):
    r = client.post("/api/v1/bench/structures/compare", json={"structures": [
        {"label": "aspirin", "smiles": "CC(=O)Oc1ccccc1C(=O)O"},
        {"label": "paracetamol", "smiles": "CC(=O)Nc1ccc(O)cc1"}]})
    assert r.status_code == 200
    d = r.json()
    assert 0.0 <= d["pairwise_similarity"][0]["tanimoto"] <= 1.0
    joined = " ".join(d["limitations"]).lower()
    assert "not pharmacological equivalence" in joined


def test_identical_structures_compare_as_identical(client):
    r = client.post("/api/v1/bench/structures/compare", json={"structures": [
        {"label": "a", "smiles": "c1ccccc1"},
        {"label": "b", "smiles": "C1=CC=CC=C1"}]})
    assert r.status_code == 200
    pair = r.json()["pairwise_similarity"][0]
    assert pair["identical_structure"] is True, "the same molecule written two ways is identical"
    assert pair["tanimoto"] == pytest.approx(1.0)


# --------------------------------------------------------------------- owner identity

def test_bench_session_is_owned_by_the_same_identity_the_workspace_routes_check(client):
    """Regression: the Bench owned sessions as "local" while the workspace routes check
    "local-user", so every attach, note and report on a Bench session returned 404
    "workspace not found" the moment the Evidence stage loaded."""
    from app.workspace_auth import LOCAL_USER

    r = client.post("/api/v1/bench/sessions",
                    json={"objective": "Investigate caper alkaloids.", "mode": "open"})
    assert r.status_code == 201
    sid = r.json()["session_id"]

    # The workspace routes must accept the session the Bench just made.
    listed = client.get(f"/api/workspaces/{sid}/resources")
    assert listed.status_code == 200, (
        f"workspace routes rejected a Bench session: {listed.status_code} {listed.text[:200]}")

    note = client.post(f"/api/workspaces/{sid}/notes",
                       json={"category": "question", "body": "Which alkaloid drives it?"})
    assert note.status_code in (200, 201), note.text[:200]

    report = client.post(f"/api/workspaces/{sid}/reports")
    assert report.status_code in (200, 201), report.text[:200]

    # And the owner really is the shared constant, not a second hardcoded string.
    from app.models import ResearchWorkspace
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        assert db.get(ResearchWorkspace, sid).owner == LOCAL_USER
    finally:
        db.close()


def test_bench_session_list_only_returns_this_researchers_sessions(client):
    r = client.get("/api/v1/bench/sessions")
    assert r.status_code == 200
    from app.workspace_auth import LOCAL_USER
    from app.models import ResearchWorkspace
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        for item in r.json()["items"]:
            ws = db.get(ResearchWorkspace, item["session_id"])
            assert ws.owner == LOCAL_USER
    finally:
        db.close()
