"""Formulation research tests.

The central assertion of this file: **missing evidence is never a pass.** Everything else follows
from that — computed descriptors cannot close a physical requirement, an unevaluated material pair
is not compatible, and there is no composite score that could hide an unmeasured constraint.
"""
import pytest
from sqlalchemy import select

from app import formulation as engine
from app.models import (CompatibilityVerdict, ConstraintCategory, ConstraintMeasurement,
                        ConstraintPriority, ConstraintStatus, ExperimentPlan, Formulation,
                        MeasurementKind, ResearchConstraint)


# --------------------------------------------------------------- unit level

class FakeMeasurement:
    def __init__(self, kind, value=None, uncertainty=None, **kw):
        self.id = kw.get("id", 1)
        self.kind = kind
        self.value = value
        self.value_text = kw.get("value_text")
        self.unit = kw.get("unit", "N")
        self.uncertainty = uncertainty
        self.n_replicates = kw.get("n_replicates")
        self.method = kw.get("method")
        self.conditions = kw.get("conditions")
        self.source = kw.get("source")
        self.evidence_record_id = kw.get("evidence_record_id")


class FakeConstraint:
    def __init__(self, measurements=None, **kw):
        self.id = kw.get("id", 1)
        self.name = kw.get("name", "Tablet hardness")
        self.category = kw.get("category", ConstraintCategory.MECHANICAL)
        self.priority = kw.get("priority", ConstraintPriority.MANDATORY)
        self.property_name = kw.get("property_name", "hardness")
        self.unit = kw.get("unit", "N")
        self.min_value = kw.get("min_value", 50.0)
        self.max_value = kw.get("max_value", 120.0)
        self.target_value = kw.get("target_value")
        self.qualitative_target = kw.get("qualitative_target")
        self.measurements = measurements or []


def test_constraint_with_no_measurement_is_not_satisfied():
    """The single most important behaviour in this module."""
    result = engine.evaluate_constraint(FakeConstraint())
    assert result.status == ConstraintStatus.NOT_MEASURED
    assert result.status != ConstraintStatus.SATISFIED
    assert "not a satisfied one" in result.reason


def test_computed_descriptor_cannot_close_a_physical_constraint():
    """An RDKit descriptor describes a molecule, not a manufactured tablet."""
    constraint = FakeConstraint(measurements=[FakeMeasurement(MeasurementKind.COMPUTED, 80.0)])
    result = engine.evaluate_constraint(constraint)
    assert result.status == ConstraintStatus.NOT_MEASURED
    assert "cannot close this requirement" in result.reason
    assert result.supporting_count == 1 and result.confirming_count == 0


def test_model_prediction_cannot_close_a_constraint():
    constraint = FakeConstraint(measurements=[FakeMeasurement(MeasurementKind.PREDICTED, 80.0)])
    assert engine.evaluate_constraint(constraint).status == ConstraintStatus.NOT_MEASURED


def test_researcher_estimate_cannot_close_a_constraint():
    constraint = FakeConstraint(
        measurements=[FakeMeasurement(MeasurementKind.RESEARCHER_ESTIMATE, 80.0)])
    assert engine.evaluate_constraint(constraint).status == ConstraintStatus.NOT_MEASURED


@pytest.mark.parametrize("kind", [MeasurementKind.EXPERIMENTAL, MeasurementKind.LITERATURE,
                                  MeasurementKind.SUPPLIER_SPECIFICATION])
def test_confirming_measurement_in_range_satisfies(kind):
    constraint = FakeConstraint(measurements=[FakeMeasurement(kind, 80.0)])
    result = engine.evaluate_constraint(constraint)
    assert result.status == ConstraintStatus.SATISFIED
    assert result.confirming_count == 1


def test_measurement_outside_range_violates():
    constraint = FakeConstraint(measurements=[FakeMeasurement(MeasurementKind.EXPERIMENTAL, 20.0)])
    assert engine.evaluate_constraint(constraint).status == ConstraintStatus.VIOLATED


def test_uncertainty_spanning_the_boundary_is_uncertain_not_satisfied():
    """52 ± 5 N against a 50 N minimum cannot honestly be called a pass."""
    constraint = FakeConstraint(
        measurements=[FakeMeasurement(MeasurementKind.EXPERIMENTAL, 52.0, uncertainty=5.0)])
    result = engine.evaluate_constraint(constraint)
    assert result.status == ConstraintStatus.UNCERTAIN
    assert "crosses an acceptance boundary" in result.reason


def test_tight_uncertainty_inside_the_range_still_satisfies():
    constraint = FakeConstraint(
        measurements=[FakeMeasurement(MeasurementKind.EXPERIMENTAL, 80.0, uncertainty=2.0)])
    assert engine.evaluate_constraint(constraint).status == ConstraintStatus.SATISFIED


def test_disagreeing_measurements_are_conflicting_not_averaged():
    constraint = FakeConstraint(measurements=[
        FakeMeasurement(MeasurementKind.EXPERIMENTAL, 80.0, id=1),
        FakeMeasurement(MeasurementKind.EXPERIMENTAL, 20.0, id=2)])
    result = engine.evaluate_constraint(constraint)
    assert result.status == ConstraintStatus.CONFLICTING
    assert "does not choose between them" in result.reason


def test_measurements_without_an_acceptance_range_are_uncertain():
    constraint = FakeConstraint(min_value=None, max_value=None,
                                measurements=[FakeMeasurement(MeasurementKind.EXPERIMENTAL, 80.0)])
    result = engine.evaluate_constraint(constraint)
    assert result.status == ConstraintStatus.UNCERTAIN
    assert "no acceptance range" in result.reason


def test_not_applicable_constraint_is_excluded_from_judgement():
    constraint = FakeConstraint(priority=ConstraintPriority.NOT_APPLICABLE)
    assert engine.evaluate_constraint(constraint).status == ConstraintStatus.NOT_APPLICABLE


# --------------------------------------------------------- formulation level

class FakeFormulation:
    def __init__(self, constraints):
        self.id = 1
        self.name = "Candidate A"
        self.dosage_form = "tablet"
        self.constraints = constraints


def test_formulation_is_not_ready_while_a_mandatory_constraint_is_unmeasured():
    f = FakeFormulation([
        FakeConstraint(id=1, name="Hardness",
                       measurements=[FakeMeasurement(MeasurementKind.EXPERIMENTAL, 80.0)]),
        FakeConstraint(id=2, name="Disintegration time",
                       category=ConstraintCategory.DISINTEGRATION, measurements=[]),
    ])
    result = engine.evaluate_formulation(f)
    assert result["unresolved_mandatory"] == ["Disintegration time"]
    assert "unmeasured is not the same as met" in result["readiness_statement"]


def test_formulation_reports_no_composite_score():
    result = engine.evaluate_formulation(FakeFormulation([FakeConstraint()]))
    assert "does not compute an overall formulation score" in result["no_composite_score"]
    for forbidden in ("score", "overall_score", "readiness_percent", "confidence"):
        assert forbidden not in result


def test_violated_mandatory_constraint_is_reported_plainly():
    f = FakeFormulation([FakeConstraint(
        measurements=[FakeMeasurement(MeasurementKind.EXPERIMENTAL, 10.0)])])
    result = engine.evaluate_formulation(f)
    assert result["violated_mandatory"] == ["Tablet hardness"]
    assert "does not currently meet its own specification" in result["readiness_statement"]


# ------------------------------------------------------------- trade-offs

def test_trade_offs_only_surface_for_categories_actually_constrained():
    f = FakeFormulation([
        FakeConstraint(id=1, category=ConstraintCategory.MECHANICAL),
        FakeConstraint(id=2, category=ConstraintCategory.DISINTEGRATION),
    ])
    result = engine.trade_offs(f)
    pairs = [tuple(t["between"]) for t in result["trade_offs"]]
    assert ("mechanical", "disintegration") in pairs
    # A principle whose categories are not both in use is not shown.
    assert ("dissolution", "bioavailability") not in pairs


def test_trade_offs_are_labelled_general_principles_not_predictions():
    f = FakeFormulation([FakeConstraint(id=1, category=ConstraintCategory.MECHANICAL),
                         FakeConstraint(id=2, category=ConstraintCategory.DISINTEGRATION)])
    result = engine.trade_offs(f)
    entry = result["trade_offs"][0]
    assert entry["evidence_status"] == "general pharmaceutical principle"
    assert entry["applies_to_this_material"] == "not established — investigate"
    assert entry["source"]
    assert "NOT predictions about this active ingredient" in result["interpretation"]


def test_every_trade_off_principle_carries_a_source():
    assert all(p.get("source") for p in engine.TRADE_OFF_PRINCIPLES)


def test_disintegration_and_dissolution_are_distinguished():
    principle = next(p for p in engine.TRADE_OFF_PRINCIPLES
                     if set(p["between"]) == {"disintegration", "dissolution"})
    assert "different tests" in principle["source"] or "separate compendial" in principle["source"]
    assert "neither substitutes" in principle["direction"]


# --------------------------------------------------- experiment prioritisation

class FakeExperiment:
    def __init__(self, eid, title, constraint_ids, cost=None, hours=None):
        self.id = eid
        self.title = title
        self.status = "proposed"
        self.addresses_constraint_ids = constraint_ids
        self.estimated_cost = cost
        self.cost_currency = "USD" if cost else None
        self.estimated_duration_hours = hours
        self.estimate_basis = "researcher estimate" if cost else None


def test_experiment_addressing_unmeasured_mandatory_constraint_ranks_highest():
    unmeasured = engine.evaluate_constraint(FakeConstraint(id=1, name="Disintegration"))
    satisfied = engine.evaluate_constraint(FakeConstraint(
        id=2, name="Hardness", measurements=[FakeMeasurement(MeasurementKind.EXPERIMENTAL, 80.0)]))
    evaluations = {1: unmeasured, 2: satisfied}
    result = engine.prioritise_experiments(
        [FakeExperiment(10, "Re-measure hardness", [2]),
         FakeExperiment(11, "Disintegration test", [1])], evaluations)
    assert result["experiments"][0]["title"] == "Disintegration test"
    assert result["experiments"][0]["information_value"] > result["experiments"][1]["information_value"]


def test_experiment_on_already_satisfied_constraints_is_flagged_redundant():
    satisfied = engine.evaluate_constraint(FakeConstraint(
        id=2, measurements=[FakeMeasurement(MeasurementKind.EXPERIMENTAL, 80.0)]))
    result = engine.prioritise_experiments([FakeExperiment(10, "Repeat", [2])], {2: satisfied})
    entry = result["experiments"][0]
    assert entry["likely_redundant"] is True
    assert "would not resolve an open question" in entry["redundancy_note"]


def test_prioritisation_shows_its_arithmetic():
    unmeasured = engine.evaluate_constraint(FakeConstraint(id=1))
    result = engine.prioritise_experiments([FakeExperiment(10, "T", [1])], {1: unmeasured})
    entry = result["experiments"][0]
    contribution = entry["contributions"][0]
    assert contribution["priority_weight"] == 3.0          # mandatory
    assert contribution["uncertainty_remaining"] == 1.0    # not measured
    assert entry["information_value"] == 3.0
    assert "Σ (priority_weight × uncertainty_remaining)" in result["method"]["formula"]


def test_prioritisation_declares_its_assumptions():
    result = engine.prioritise_experiments([], {})
    joined = " ".join(result["assumptions"]).lower()
    assert "convention" in joined
    assert "researcher estimates" in joined
    assert "moleculab supplies no price data" in joined


def test_cost_effectiveness_omitted_when_no_cost_supplied():
    unmeasured = engine.evaluate_constraint(FakeConstraint(id=1))
    result = engine.prioritise_experiments([FakeExperiment(10, "T", [1])], {1: unmeasured})
    entry = result["experiments"][0]
    assert entry["cost_effectiveness"] is None
    assert "No cost estimate supplied" in entry["cost_effectiveness_note"]


def test_cost_effectiveness_computed_only_from_supplied_estimate():
    unmeasured = engine.evaluate_constraint(FakeConstraint(id=1))
    result = engine.prioritise_experiments(
        [FakeExperiment(10, "T", [1], cost=600.0, hours=8)], {1: unmeasured})
    entry = result["experiments"][0]
    assert entry["cost_effectiveness"] == pytest.approx(3.0 / 600.0)
    assert entry["estimate_basis"] == "researcher estimate"


# ----------------------------------------------------------------- API level

def _make_formulation(client, name="Candidate A"):
    return client.post("/api/v1/formulations", json={"name": name, "dosage_form": "tablet"}).json()["id"]


def test_vocabulary_explains_the_distinctions(client):
    body = client.get("/api/v1/formulations/vocabulary").json()
    notes = body["notes"]
    assert "USP <701>" in notes["disintegration_vs_dissolution"]
    assert "'evaporation'" in notes["disintegration_vs_dissolution"]
    assert "never as satisfied" in notes["missing_data"]
    assert "tablet" in body["dosage_forms"] and "injectable" in body["dosage_forms"]


def test_constraint_lifecycle_through_the_api(client):
    fid = _make_formulation(client)
    cid = client.post(f"/api/v1/formulations/{fid}/constraints", json={
        "category": "mechanical", "name": "Tablet hardness", "priority": "mandatory",
        "property_name": "hardness", "unit": "N", "min_value": 50, "max_value": 120,
        "rationale": "Must survive coating and packaging."}).json()["id"]

    evaluation = client.get(f"/api/v1/formulations/{fid}/evaluation").json()
    assert evaluation["constraints"][0]["status"] == "not_measured"
    assert evaluation["unresolved_mandatory"] == ["Tablet hardness"]

    posted = client.post(f"/api/v1/formulations/constraints/{cid}/measurements", json={
        "kind": "experimental", "value": 78.0, "unit": "N", "n_replicates": 10,
        "method": "Texture analyser, diametral compression",
        "source": "Internal run 2026-11-18"}).json()
    assert posted["constraint_status_now"]["status"] == "satisfied"

    evaluation = client.get(f"/api/v1/formulations/{fid}/evaluation").json()
    assert evaluation["status_counts"]["satisfied"] == 1
    assert evaluation["unresolved_mandatory"] == []


def test_measurement_requires_a_value(client):
    fid = _make_formulation(client)
    cid = client.post(f"/api/v1/formulations/{fid}/constraints",
                      json={"category": "mechanical", "name": "H"}).json()["id"]
    response = client.post(f"/api/v1/formulations/constraints/{cid}/measurements",
                           json={"kind": "experimental"})
    assert response.status_code == 422


def test_constraint_rejects_inverted_range(client):
    fid = _make_formulation(client)
    response = client.post(f"/api/v1/formulations/{fid}/constraints", json={
        "category": "mechanical", "name": "H", "min_value": 100, "max_value": 10})
    assert response.status_code == 422


def test_unevaluated_material_pair_is_not_compatible(client):
    a = client.post("/api/v1/formulations/materials",
                    json={"name": "Test active X", "role": "active_ingredient"}).json()["id"]
    b = client.post("/api/v1/formulations/materials",
                    json={"name": "Lactose monohydrate", "role": "filler_diluent"}).json()["id"]
    fid = _make_formulation(client, "Compat candidate")
    for mid, role in ((a, "active_ingredient"), (b, "filler_diluent")):
        client.post(f"/api/v1/formulations/{fid}/components",
                    json={"material_id": mid, "role": role})
    matrix = client.get(f"/api/v1/formulations/compatibility/matrix?formulation_id={fid}").json()
    assert matrix["pair_count"] == 1
    assert matrix["pairs"][0]["verdict"] == "not_evaluated"
    assert matrix["evaluated_pairs"] == 0
    assert "NOT mean the materials are compatible" in matrix["interpretation"]


def test_demonstrated_compatibility_requires_a_source(client):
    a = client.post("/api/v1/formulations/materials", json={"name": "Mat A"}).json()["id"]
    b = client.post("/api/v1/formulations/materials", json={"name": "Mat B"}).json()["id"]
    refused = client.post("/api/v1/formulations/compatibility", json={
        "material_a_id": a, "material_b_id": b, "verdict": "demonstrated_compatible"})
    assert refused.status_code == 422
    assert "requires a source" in refused.json()["detail"]

    allowed = client.post("/api/v1/formulations/compatibility", json={
        "material_a_id": a, "material_b_id": b, "verdict": "hypothesis"})
    assert allowed.status_code == 201


def test_material_cannot_be_assessed_against_itself(client):
    a = client.post("/api/v1/formulations/materials", json={"name": "Solo"}).json()["id"]
    response = client.post("/api/v1/formulations/compatibility",
                           json={"material_a_id": a, "material_b_id": a})
    assert response.status_code == 422


def test_duplicate_material_name_is_refused(client):
    client.post("/api/v1/formulations/materials", json={"name": "Unique material"})
    again = client.post("/api/v1/formulations/materials", json={"name": "Unique material"})
    assert again.status_code == 409


def test_experiment_priority_endpoint_ranks_by_open_uncertainty(client):
    fid = _make_formulation(client, "Priority candidate")
    measured = client.post(f"/api/v1/formulations/{fid}/constraints", json={
        "category": "mechanical", "name": "Hardness", "min_value": 50, "max_value": 120,
        "unit": "N"}).json()["id"]
    unmeasured = client.post(f"/api/v1/formulations/{fid}/constraints", json={
        "category": "dissolution", "name": "Dissolution at 30 min", "min_value": 80,
        "max_value": 100, "unit": "%"}).json()["id"]
    client.post(f"/api/v1/formulations/constraints/{measured}/measurements",
                json={"kind": "experimental", "value": 80, "unit": "N"})

    client.post("/api/v1/formulations/experiments", json={
        "title": "Repeat hardness", "formulation_id": fid,
        "addresses_constraint_ids": [measured]})
    client.post("/api/v1/formulations/experiments", json={
        "title": "Dissolution profile", "formulation_id": fid,
        "addresses_constraint_ids": [unmeasured], "estimated_cost": 400,
        "cost_currency": "USD", "estimate_basis": "lab quote"})

    body = client.get(f"/api/v1/formulations/{fid}/experiments/priority").json()
    assert body["experiments"][0]["title"] == "Dissolution profile"
    assert body["experiments"][1]["likely_redundant"] is True
    assert body["experiments"][0]["cost_effectiveness"] is not None


def test_compare_does_not_declare_a_winner(client):
    a = _make_formulation(client, "Cand A")
    b = _make_formulation(client, "Cand B")
    body = client.get(f"/api/v1/formulations/compare?ids={a},{b}").json()
    assert len(body["formulations"]) == 2
    assert "does not rank them overall" in body["interpretation"]
    for forbidden in ("winner", "best", "recommended", "overall_score"):
        assert forbidden not in body


def test_compare_requires_at_least_two(client):
    a = _make_formulation(client)
    assert client.get(f"/api/v1/formulations/compare?ids={a}").status_code == 422


def test_formulation_detail_includes_evaluation_and_trade_offs(client):
    fid = _make_formulation(client, "Detail candidate")
    client.post(f"/api/v1/formulations/{fid}/constraints",
                json={"category": "mechanical", "name": "Hardness"})
    client.post(f"/api/v1/formulations/{fid}/constraints",
                json={"category": "disintegration", "name": "Disintegration"})
    body = client.get(f"/api/v1/formulations/{fid}").json()
    assert body["evaluation"]["constraint_count"] == 2
    assert body["trade_offs"]["trade_offs"], "mechanical/disintegration principle should surface"


def test_processing_condition_records_target_versus_measured(client):
    fid = _make_formulation(client, "Conditions candidate")
    client.post(f"/api/v1/formulations/{fid}/conditions", json={
        "process_step": "drying", "parameter": "temperature", "value": 60, "unit": "C",
        "is_target": True, "rationale": "Below the active's reported degradation onset."})
    body = client.get(f"/api/v1/formulations/{fid}").json()
    condition = body["conditions"][0]
    assert condition["is_target"] is True and condition["unit"] == "C"


def test_unknown_formulation_is_404(client):
    assert client.get("/api/v1/formulations/999999").status_code == 404
