"""Research workbench: aggregation over existing workspace records."""
import pytest

from app.main import app
from app.workbench_api import CONTEXT_CAP, router

# The integration owner mounts the router in app.main; mount it here only if that has not happened yet.
if not any(getattr(r, "path", "").startswith("/api/v1/workbench") for r in app.routes):
    app.include_router(router, prefix="/api/v1/workbench")

WB = "/api/v1/workbench/investigations"
QUESTION = "Which Capparis spinosa constituents merit a formulation study?"


@pytest.fixture()
def inv(client):
    r = client.post("/api/workspaces", json={"title": "Workbench test", "research_question": QUESTION})
    assert r.status_code == 201
    return r.json()["id"]


def first_compound(client):
    return next(c["id"] for c in client.get("/api/compounds").json())


def first_evidence(client):
    return client.get("/api/evidence").json()[0]["id"]


def test_overview_shape(client, inv):
    d = client.get(f"{WB}/{inv}/overview").json()
    for k in ("workspace", "counts", "resources", "resources_by_type", "notes", "hypotheses", "tasks", "reports",
              "activity", "formulations", "experiments", "boards", "open_questions", "attachable_types"):
        assert k in d
    assert d["workspace"]["research_question"] == QUESTION
    # nothing attached yet: every section is honestly empty
    assert d["counts"]["resources"] == 0 and d["counts"]["notes"] == 0 and d["counts"]["hypotheses"] == 0
    assert d["resources"] == [] and d["formulations"] == [] and d["open_questions"] == []
    assert any(a["action"] == "investigation_created" for a in d["activity"])
    listing = client.get(WB).json()
    assert any(w["id"] == inv and "counts" in w for w in listing)


def test_attach_persists_and_appears(client, inv):
    cid = first_compound(client)
    r = client.post(f"{WB}/{inv}/attach", json={"ref_type": "compound", "ref_id": cid, "note": "lead"})
    assert r.status_code == 201
    d = client.get(f"{WB}/{inv}/overview").json()
    assert d["counts"]["resources_by_type"] == {"compound": 1}
    assert d["resources_by_type"]["compound"][0]["resource_ref"] == str(cid)
    assert d["resources_by_type"]["compound"][0]["researcher_note"] == "lead"
    # visible through the existing workspace API too (same table)
    assert len(client.get(f"/api/workspaces/{inv}/resources").json()) == 1


def test_unknown_ref_type_rejected_with_accepted_list(client, inv):
    r = client.post(f"{WB}/{inv}/attach", json={"ref_type": "spaceship", "ref_id": 1})
    assert r.status_code == 422
    accepted = r.json()["detail"]["accepted_ref_types"]
    assert "compound" in accepted and "evidence" in accepted
    assert "repurposing_analysis" not in accepted


def test_missing_investigation_404(client):
    assert client.get(f"{WB}/99999/overview").status_code == 404
    assert client.get(f"{WB}/99999/context").status_code == 404
    assert client.post(f"{WB}/99999/attach", json={"ref_type": "compound", "ref_id": 1}).status_code == 404
    assert client.delete(f"{WB}/99999/attach/1").status_code == 404


def test_attach_missing_record_is_404(client, inv):
    assert client.post(f"{WB}/{inv}/attach", json={"ref_type": "compound", "ref_id": 999999}).status_code == 404


def test_context_block_capped_and_has_question(client, inv):
    for c in client.get("/api/compounds").json()[:15]:
        client.post(f"{WB}/{inv}/attach", json={"ref_type": "compound", "ref_id": c["id"]})
    for i in range(12):
        client.post(f"/api/workspaces/{inv}/notes", json={"category": "question", "body": f"Open question {i} " + "x" * 400})
        client.post(f"/api/workspaces/{inv}/hypotheses", json={
            "origin": "researcher", "statement": f"Compound {i} may relate to the observed activity " + "y" * 500})
    r = client.get(f"{WB}/{inv}/context")
    assert r.status_code == 200
    assert len(r.text) <= CONTEXT_CAP
    body = r.json()
    assert QUESTION in body["research_question"]
    assert body["compounds"]
    assert all(h["status"] for h in body["recent_hypotheses"])


def test_detach_keeps_scientific_records(client, inv):
    cid, eid = first_compound(client), first_evidence(client)
    n_compounds = len(client.get("/api/compounds").json())
    n_evidence = len(client.get("/api/evidence").json())
    rc = client.post(f"{WB}/{inv}/attach", json={"ref_type": "compound", "ref_id": cid}).json()
    re_ = client.post(f"{WB}/{inv}/attach", json={"ref_type": "evidence", "ref_id": eid}).json()
    assert client.delete(f"{WB}/{inv}/attach/{rc['id']}").status_code == 204
    assert client.delete(f"{WB}/{inv}/attach/{re_['id']}").status_code == 204
    d = client.get(f"{WB}/{inv}/overview").json()
    assert d["resources"] == []
    assert len(client.get("/api/compounds").json()) == n_compounds
    assert len(client.get("/api/evidence").json()) == n_evidence
    assert client.get(f"/api/compounds/{cid}").status_code == 200
    assert client.get(f"/api/evidence/{eid}").status_code == 200


def test_linked_formulation_and_report_flow(client, inv):
    f = client.post("/api/v1/formulations", json={"name": "Tablet A", "dosage_form": "tablet", "workspace_id": inv})
    assert f.status_code in (200, 201)
    client.post(f"/api/workspaces/{inv}/notes", json={"category": "observation", "body": "A researcher observation"})
    d = client.get(f"{WB}/{inv}/overview").json()
    assert [x["name"] for x in d["formulations"]] == ["Tablet A"]
    assert d["notes"][0]["content_origin"] == "researcher_authored"
    assert d["counts"]["formulations"] == 1
    assert client.post(f"/api/workspaces/{inv}/reports").status_code == 201
    assert client.get(f"{WB}/{inv}/overview").json()["counts"]["reports"] == 1


def test_archived_investigation_refuses_attach(client, inv):
    client.post(f"/api/workspaces/{inv}/archive")
    r = client.post(f"{WB}/{inv}/attach", json={"ref_type": "compound", "ref_id": first_compound(client)})
    assert r.status_code == 409
