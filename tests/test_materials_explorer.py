"""Material and conditions explorer. Self-contained: mounts the router on its own app so the tests
do not depend on the integration step, and seeds rows directly through the ORM."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_session
from app.materials_api import router
from app.models import (CompatibilityAssessment, Formulation, FormulationComponent, Material,
                        ProcessingCondition)

BASE = "/api/v1/materials-explorer"


@pytest.fixture()
def mx(session_factory):
    app = FastAPI()
    app.include_router(router, prefix=BASE)

    def _session():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def ids(session_factory):
    """Three materials, one formulation, one assessment (A-B only), two conditions."""
    with session_factory() as s:
        a = Material(name="TestActive", role="active_ingredient", cas_number="123-45-6",
                     properties=[{"name": "melting point", "value": 180, "unit": "C",
                                  "kind": "experimental", "source": "Supplier CoA"}])
        b = Material(name="TestFiller", role="filler_diluent",
                     properties=[{"name": "Melting_Point", "value": 201, "unit": "C"}])
        c = Material(name="TestLubricant", role="lubricant")
        s.add_all([a, b, c])
        s.flush()
        f = Formulation(name="TestForm")
        f2 = Formulation(name="TestForm2")
        s.add_all([f, f2])
        s.flush()
        s.add(FormulationComponent(formulation_id=f.id, material_id=a.id, role="active_ingredient",
                                   amount=10.0, amount_unit="mg"))
        s.add(CompatibilityAssessment(material_a_id=a.id, material_b_id=b.id,
                                      verdict="demonstrated_compatible",
                                      conditions_summary="40C/75%RH 4 wk", source="DSC study"))
        s.add(ProcessingCondition(formulation_id=f.id, parameter="temperature", value=60.0,
                                  unit="C", process_step="drying", is_target=True))
        s.add(ProcessingCondition(formulation_id=f.id, parameter="temperature", value=58.5,
                                  unit="C", process_step="drying", is_target=False))
        s.add(ProcessingCondition(formulation_id=f2.id, parameter="compression force", value=12.0,
                                  unit="kN", process_step="compression", is_target=True))
        s.commit()
        return {"a": a.id, "b": b.id, "c": c.id, "f": f.id, "f2": f2.id}


def test_list_and_filters(mx, ids):
    r = mx.get(f"{BASE}/materials").json()
    assert r["total"] == 3 and "interpretation" in r
    assert {i["name"] for i in r["items"]} == {"TestActive", "TestFiller", "TestLubricant"}
    assert mx.get(f"{BASE}/materials", params={"q": "123-45"}).json()["total"] == 1
    assert mx.get(f"{BASE}/materials", params={"q": "testfill"}).json()["items"][0]["name"] == "TestFiller"
    assert mx.get(f"{BASE}/materials", params={"role": "lubricant"}).json()["total"] == 1
    page = mx.get(f"{BASE}/materials", params={"limit": 1, "offset": 1}).json()
    assert page["count"] == 1 and page["total"] == 3
    active = next(i for i in r["items"] if i["name"] == "TestActive")
    assert active["formulation_count"] == 1 and active["compatibility_assessment_count"] == 1
    assert active["has_structure"] is False
    assert "solubility" in active["missing_properties"]
    assert mx.get(f"{BASE}/materials", params={"role": "nonsense"}).status_code == 422
    assert mx.get(f"{BASE}/materials", params={"limit": 0}).status_code == 422


def test_detail_shape_and_missing(mx, ids):
    d = mx.get(f"{BASE}/materials/{ids['a']}").json()
    for k in ("structure", "properties", "formulations", "compatibility_assessments",
              "missing_information", "interpretation", "limitations"):
        assert k in d
    assert d["structure"] is None
    assert d["properties"][0]["source"] == "Supplier CoA"
    assert d["formulations"][0]["amount"] == 10.0
    assert d["compatibility_assessments"][0]["other_material"]["name"] == "TestFiller"
    fields = {m["field"] for m in d["missing_information"]}
    assert "solubility" in fields and "molecular structure" in fields
    assert "melting point" not in fields and "CAS number" not in fields
    lub = mx.get(f"{BASE}/materials/{ids['c']}").json()
    assert "CAS number" in {m["field"] for m in lub["missing_information"]}


def test_unassessed_partner_is_not_evaluated(mx, ids):
    d = mx.get(f"{BASE}/materials/{ids['a']}").json()
    partners = {u["name"]: u["verdict"] for u in d["unassessed_partners"]}
    assert partners == {"TestLubricant": "not_evaluated"}


def test_compare_validates_count(mx, ids):
    assert mx.get(f"{BASE}/materials/compare", params={"ids": str(ids["a"])}).status_code == 422
    five = ",".join(str(i) for i in range(1, 6))
    assert mx.get(f"{BASE}/materials/compare", params={"ids": five}).status_code == 422
    assert mx.get(f"{BASE}/materials/compare", params={"ids": f"{ids['a']},{ids['a']}"}).status_code == 422
    assert mx.get(f"{BASE}/materials/compare", params={"ids": "x,y"}).status_code == 422
    assert mx.get(f"{BASE}/materials/compare", params={"ids": f"{ids['a']},9999"}).status_code == 404


def test_compare_never_fills_gaps_or_infers_compatibility(mx, ids):
    c = mx.get(f"{BASE}/materials/compare",
               params={"ids": f"{ids['a']},{ids['b']},{ids['c']}"}).json()
    pairs = {(p["material_a"]["name"], p["material_b"]["name"]): p for p in c["compatibility_pairs"]}
    assert pairs[("TestActive", "TestFiller")]["verdict"] == "assessed"
    assert pairs[("TestActive", "TestFiller")]["assessments"][0]["verdict"] == "demonstrated_compatible"
    for key in (("TestActive", "TestLubricant"), ("TestFiller", "TestLubricant")):
        assert pairs[key]["verdict"] == "not_evaluated"
        assert "compatible" not in pairs[key]["verdict"]
    mp = next(p for p in c["properties"] if p["key"] == "melting point")
    assert mp["shared"] is False
    lub_cell = next(v for v in mp["values"] if v["material_id"] == ids["c"])
    assert lub_cell["recorded"] is False and lub_cell["entries"] == [] and lub_cell["label"] == "not recorded"
    assert "value" not in lub_cell
    sol = next(p for p in c["properties"] if p["key"] == "solubility")
    assert sol["recorded_for"] == 0


def test_unknown_material_404(mx):
    assert mx.get(f"{BASE}/materials/424242").status_code == 404


def test_conditions_target_vs_measured(mx, ids):
    r = mx.get(f"{BASE}/conditions", params={"parameter": "Temperature"}).json()
    assert r["total"] == 2 and "interpretation" in r
    by_kind = {c["value_kind"]: c for c in r["items"]}
    assert by_kind["target"]["is_target"] is True and by_kind["target"]["value"] == 60.0
    assert "NOT a measurement" in by_kind["target"]["value_kind_note"]
    assert by_kind["measured"]["is_target"] is False and by_kind["measured"]["value"] == 58.5
    assert by_kind["target"]["formulation_link"] == f"#/formulations?id={ids['f']}"
    only_t = mx.get(f"{BASE}/conditions", params={"is_target": "true"}).json()
    assert only_t["total"] == 2 and all(c["value_kind"] == "target" for c in only_t["items"])
    only_m = mx.get(f"{BASE}/conditions", params={"is_target": "false"}).json()
    assert only_m["total"] == 1 and only_m["items"][0]["value_kind"] == "measured"
    step = mx.get(f"{BASE}/conditions", params={"process_step": "compression"}).json()
    assert step["total"] == 1
    assert mx.get(f"{BASE}/conditions", params={"limit": 0}).status_code == 422


def test_condition_parameters(mx, ids):
    p = mx.get(f"{BASE}/conditions/parameters").json()
    temp = next(x for x in p["parameters"] if x["parameter"] == "temperature")
    assert temp["count"] == 2 and temp["units"] == ["C"]
    assert temp["target_count"] == 1 and temp["measured_count"] == 1
    assert {s["process_step"] for s in p["process_steps"]} == {"drying", "compression"}


def test_conditions_compare_shows_gaps(mx, ids):
    c = mx.get(f"{BASE}/conditions/compare",
               params={"formulation_ids": f"{ids['f']},{ids['f2']}"}).json()
    row = next(r for r in c["rows"] if r["parameter"] == "temperature")
    assert row["gap"] is True and row["documented_in"] == 1
    empty = next(v for v in row["values"] if v["formulation_id"] == ids["f2"])
    assert empty["recorded"] is False and empty["label"] == "not documented" and empty["entries"] == []
    assert c["gap_count"] == 2
    assert mx.get(f"{BASE}/conditions/compare", params={"formulation_ids": "1"}).status_code == 422
    assert mx.get(f"{BASE}/conditions/compare",
                  params={"formulation_ids": f"{ids['f']},9999"}).status_code == 404
