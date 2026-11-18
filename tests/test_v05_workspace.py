"""v0.5 research workspace tests (real seed data; SYNTHETIC only where stated)."""
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session
from app.main import app
from app.models import EvidenceRecord
from tests.conftest import compound_id, plant_id

B = "/api/v1/workspaces"
CAPPARIS_Q = "Which constituents of Capparis spinosa are documented in the indexed literature, and where is the evidence incomplete?"


def new_ws(client, title="Chemical composition and research gaps in Capparis spinosa", **kw):
    r = client.post(B, json={"title": title, "research_question": CAPPARIS_Q, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------- end-to-end demonstration scenario -----------------

def test_capparis_end_to_end_scenario(client):
    # 1-2 create the investigation with its research question
    ws = new_ws(client, research_domain="phytochemistry", scope="Capparis spinosa, indexed PubMed abstracts")
    wid = ws["id"]
    assert ws["status"] == "planning" and ws["research_question"] == CAPPARIS_Q
    # 3-4 add the plant, collect its indexed compounds and publications
    pid = plant_id(client, "Capparis spinosa")
    added = client.post(f"{B}/{wid}/collect-plant", json={"plant_id": pid, "include": ["compounds", "publications"]}).json()["added"]
    assert added == {"plant": 1, "compound": 5, "publication": 7, "evidence": 0}
    # 5 attach selected evidence records (the real stachydrine isolation record and the rutin quantity record)
    stach = next(e for e in client.get("/api/evidence", params={"plant_id": pid}).json()
                 if e["compound"] and e["compound"]["name"] == "stachydrine" and e["publication"]["pmid"] == "21372539")
    for eid in (stach["id"],):
        assert client.post(f"{B}/{wid}/resources", json={"resource_type": "evidence", "resource_ref": str(eid)}).status_code == 201
    # 6 examine a research gap
    tox = next(g for g in client.get(f"/api/research/plants/{pid}/gaps").json()["items"]
               if g["category"] == "missing_toxicity_evidence")
    r = client.post(f"{B}/{wid}/resources", json={"resource_type": "gap", "resource_ref": tox["id"],
                                                  "researcher_note": "Plan a toxicology literature search."})
    assert r.status_code == 201 and r.json()["snapshot"]["explanation"].startswith("No toxicity evidence is currently indexed")
    # 7 contradictions: none indexed for Capparis -> correct handling of absence
    assert client.get(f"/api/research/plants/{pid}/contradictions").json() == []
    # 8 generate and save an eligible hypothesis
    runs = client.post(f"{B}/{wid}/hypotheses/generate", json={}).json()
    [cand] = runs[0]["hypotheses"]
    h = client.post(f"{B}/{wid}/hypotheses", json={"origin": "generated", "statement": cand["hypothesis"],
                                                   "source": runs[0]["source"]}).json()
    assert h["causal_attribution"] == "untested" and h["validation_state"] == "unvalidated"
    assert h["unresolved_questions"] and "do not establish causation" in h["unresolved_questions"][0]
    # 9 task, 10 note
    t = client.post(f"{B}/{wid}/tasks", json={"title": "Search toxicology literature for Capparis spinosa", "priority": "high",
                                             "related_resources": [{"resource_type": "gap", "resource_ref": tox["id"]}]}).json()
    n = client.post(f"{B}/{wid}/notes", json={"category": "question", "body": "Is the fraction's activity due to stachydrine?",
                                             "resource_type": "evidence", "resource_ref": str(stach["id"])}).json()
    assert n["content_origin"] == "researcher_authored" and t["status"] == "open"
    # 11 reproducible report
    rep = client.post(f"{B}/{wid}/reports").json()
    md = rep["markdown"]
    assert rep["summary"]["verified_observations"] == 1 and rep["summary"]["contradictions"] == 0
    assert "No conflicting findings were detected" in md
    assert "currently indexed dataset" in md and "not the entirety of the published literature" in md
    assert "UNVALIDATED" in md and "Hypothesis (unvalidated): stachydrine might contribute" in md
    assert "researcher-authored; not verified evidence" in md
    assert f"evidence #{stach['id']}" in md and "PMID 21372539" in md and "10.1248/yakushi.131.423" in md
    assert "Search toxicology literature for Capparis spinosa" in md
    again = client.post(f"{B}/{wid}/reports").json()
    assert again["dataset_fingerprint"]["content_sha256"] == rep["dataset_fingerprint"]["content_sha256"]
    ex = client.get(f"{B}/{wid}/reports/{rep['id']}/export")
    assert ex.status_code == 200 and ex.headers["content-type"].startswith("text/markdown")
    assert 'filename="moleculab-chemical-composition-and-research-gaps-in-capparis-spinosa-report-' in ex.headers["content-disposition"]
    actions = [a["action"] for a in client.get(f"{B}/{wid}/timeline").json()]
    assert actions == ["investigation_created", "plant_collected", "evidence_collected", "gap_investigated",
                       "hypothesis_generated", "task_created", "note_created", "report_generated", "report_generated"]


# ---------------- workspaces -----------------

def test_update_archive_reopen_delete(client, session):
    wid = new_ws(client)["id"]
    r = client.patch(f"{B}/{wid}", json={"title": "Renamed investigation", "status": "active"}).json()
    assert r["title"] == "Renamed investigation" and r["status"] == "active"
    assert client.patch(f"{B}/{wid}", json={"bogus": 1}).status_code == 422
    assert client.delete(f"{B}/{wid}").status_code == 409  # must archive first
    a = client.post(f"{B}/{wid}/archive").json()
    assert a["status"] == "archived" and a["archived_at"]
    assert client.post(f"{B}/{wid}/notes", json={"category": "observation", "body": "x"}).status_code == 409
    assert client.patch(f"{B}/{wid}", json={"title": "nope nope"}).status_code == 409
    re = client.post(f"{B}/{wid}/reopen").json()
    assert re["status"] == "active" and re["archived_at"] is None
    assert client.post(f"{B}/{wid}/reopen").status_code == 409
    client.post(f"{B}/{wid}/collect-plant", json={"plant_id": 1, "include": ["evidence"]})
    client.post(f"{B}/{wid}/archive")
    before = session.scalar(select(EvidenceRecord.id).order_by(EvidenceRecord.id.desc()))
    assert client.delete(f"{B}/{wid}").status_code == 204
    assert client.get(f"{B}/{wid}").status_code == 404
    assert session.scalar(select(EvidenceRecord.id).order_by(EvidenceRecord.id.desc())) == before
    assert len(client.get("/api/evidence").json()) == 37  # shared scientific records untouched


def test_workspace_validation(client):
    assert client.post(B, json={"title": "ab", "research_question": CAPPARIS_Q}).status_code == 422
    assert client.post(B, json={"title": "Valid title", "research_question": "short"}).status_code == 422
    assert client.post(B, json={"title": "Valid title", "research_question": CAPPARIS_Q, "status": "archived"}).status_code == 422
    assert client.get(f"{B}/99999").status_code == 404


def test_empty_workspace_and_report(client):
    wid = new_ws(client, title="Empty investigation")["id"]
    assert client.get(f"{B}/{wid}/resources").json() == []
    assert client.post(f"{B}/{wid}/hypotheses/generate", json={}).status_code == 422  # nothing collected
    md = client.post(f"{B}/{wid}/reports").json()["markdown"]
    assert "No collected evidence record passed verification" in md and "No hypotheses recorded" in md


# ---------------- resources -----------------

def test_resource_collection_duplicates_and_invalid_ids(client):
    wid = new_ws(client)["id"]
    first = client.post(f"{B}/{wid}/resources", json={"resource_type": "compound", "resource_ref": str(compound_id(client, "rutin"))})
    dup = client.post(f"{B}/{wid}/resources", json={"resource_type": "compound", "resource_ref": str(compound_id(client, "rutin"))})
    assert first.status_code == 201 and dup.status_code == 200 and dup.json()["created"] is False
    assert len(client.get(f"{B}/{wid}/resources").json()) == 1
    for rtype, ref, code in (("evidence", "99999", 404), ("plant", "abc", 422), ("gap", "made-up-gap", 404),
                             ("contradiction", "conflict:p9:c9", 404), ("bogus", "1", 422)):
        assert client.post(f"{B}/{wid}/resources", json={"resource_type": rtype, "resource_ref": ref}).status_code == code


def test_resource_filter_search_note_remove(client):
    wid = new_ws(client)["id"]
    client.post(f"{B}/{wid}/collect-plant", json={"plant_id": 1, "include": ["compounds"]})
    comps = client.get(f"{B}/{wid}/resources", params={"resource_type": "compound"}).json()
    assert len(comps) == 5 and all(c["resource_type"] == "compound" for c in comps)
    rutin = client.get(f"{B}/{wid}/resources", params={"q": "rutin"}).json()
    assert [r["label"] for r in rutin] == ["rutin"]
    upd = client.patch(f"{B}/{wid}/resources/{rutin[0]['id']}", json={"researcher_note": "check mg/100 g units"}).json()
    assert upd["researcher_note"] == "check mg/100 g units"
    assert client.get(f"{B}/{wid}/resources", params={"q": "mg/100"}).json()[0]["id"] == rutin[0]["id"]
    assert client.delete(f"{B}/{wid}/resources/{rutin[0]['id']}").status_code == 204
    assert client.delete(f"{B}/{wid}/resources/{rutin[0]['id']}").status_code == 404


def test_contradiction_collected_and_preserved_in_report(client):
    wid = new_ws(client, title="Pulegone variation")["id"]
    zid = plant_id(client, "Ziziphora clinopodioides")
    client.post(f"{B}/{wid}/collect-plant", json={"plant_id": zid, "include": ["compounds", "evidence"]})
    [c] = client.get("/api/research/contradictions").json()
    r = client.post(f"{B}/{wid}/resources", json={"resource_type": "contradiction", "resource_ref": c["id"]})
    assert r.status_code == 201 and r.json()["snapshot"]["assessment"] == "insufficient_context"
    md = client.post(f"{B}/{wid}/reports").json()["markdown"]
    for v in ("62.17", "29.24", "7.9"):
        assert v in md
    assert "pulegone in Ziziphora clinopodioides (collected)" in md
    assert "does not decide which report is correct" in md


def test_contradictions_in_scope_reported_even_if_not_collected(client):
    wid = new_ws(client, title="Ziziphora only")["id"]
    client.post(f"{B}/{wid}/collect-plant", json={"plant_id": plant_id(client, "Ziziphora clinopodioides"), "include": []})
    rep = client.post(f"{B}/{wid}/reports").json()
    assert rep["summary"]["contradictions"] == 1  # never silently omitted


def test_unverified_collected_record_is_not_an_observation(client):
    wid = new_ws(client, title="Glycyrrhizin check")["id"]
    client.post(f"{B}/{wid}/resources", json={"resource_type": "evidence", "resource_ref": "20"})
    rep = client.post(f"{B}/{wid}/reports").json()
    assert rep["summary"]["verified_observations"] == 0
    assert "failed** verification" in rep["markdown"] and "evidence #20" in rep["markdown"]


# ---------------- notes -----------------

def test_notes_crud_and_links(client):
    wid = new_ws(client)["id"]
    n = client.post(f"{B}/{wid}/notes", json={"category": "methodology", "body": "GC-MS library matching only?",
                                             "resource_type": "publication", "resource_ref": "1"}).json()
    assert n["verification_status"].startswith("not_verified")
    assert client.post(f"{B}/{wid}/notes", json={"category": "observation", "body": "x", "resource_type": "evidence",
                                                 "resource_ref": "99999"}).status_code == 404
    assert client.post(f"{B}/{wid}/notes", json={"category": "observation", "body": "x", "resource_type": "evidence"}).status_code == 422
    assert client.post(f"{B}/{wid}/notes", json={"category": "rumour", "body": "x"}).status_code == 422
    u = client.patch(f"{B}/{wid}/notes/{n['id']}", json={"body": "Confirmed GC-MS + retention indices", "category": "decision"}).json()
    assert u["category"] == "decision" and u["updated_at"] >= n["updated_at"]
    assert [x["id"] for x in client.get(f"{B}/{wid}/notes", params={"category": "decision"}).json()] == [n["id"]]
    assert client.delete(f"{B}/{wid}/notes/{n['id']}").status_code == 204
    assert client.get(f"{B}/{wid}/notes").json() == []


def test_researcher_note_never_becomes_an_observation(client):
    wid = new_ws(client)["id"]
    client.post(f"{B}/{wid}/notes", json={"category": "interpretation", "body": "Stachydrine is clearly the anti-arthritic agent."})
    rep = client.post(f"{B}/{wid}/reports").json()
    obs = " ".join(o["statement"] for o in rep["content"]["verified_observations"])
    assert "anti-arthritic agent" not in obs
    section = rep["markdown"].split("## 10. Researcher notes")[1]
    assert "Stachydrine is clearly the anti-arthritic agent" in section


# ---------------- hypotheses -----------------

def test_hypothesis_tracking_and_rules(client):
    wid = new_ws(client)["id"]
    h = client.post(f"{B}/{wid}/hypotheses", json={"origin": "researcher", "statement": "Rutin content may differ between buds and berries.",
                                                   "supporting_evidence_ids": [1]}).json()
    assert h["statement"].startswith("Hypothesis (researcher, unvalidated):") and h["status"] == "proposed"
    hid = h["id"]
    assert client.patch(f"{B}/{wid}/hypotheses/{hid}", json={"status": "supported_by_preliminary_evidence"}).status_code == 200
    assert client.patch(f"{B}/{wid}/hypotheses/{hid}", json={"status": "contradicted"}).status_code == 422  # no contradicting evidence
    ok = client.patch(f"{B}/{wid}/hypotheses/{hid}", json={"status": "contradicted", "contradicting_evidence_ids": [4],
                                                         "alternative_explanations": ["different cultivars"]}).json()
    assert ok["contradicting_evidence_ids"] == [4] and ok["validation_state"] == "unvalidated"
    assert client.patch(f"{B}/{wid}/hypotheses/{hid}", json={"supporting_evidence_ids": [99999]}).status_code == 404
    assert client.patch(f"{B}/{wid}/hypotheses/{hid}", json={"status": "established_fact"}).status_code == 422
    nolink = client.post(f"{B}/{wid}/hypotheses", json={"origin": "researcher", "statement": "Rutin may matter here.",
                                                        "supporting_evidence_ids": []}).json()
    assert client.patch(f"{B}/{wid}/hypotheses/{nolink['id']}", json={"status": "supported_by_preliminary_evidence"}).status_code == 422


def test_causal_attribution_cannot_be_upgraded_without_compound_experiment(client):
    wid = new_ws(client)["id"]
    client.post(f"{B}/{wid}/collect-plant", json={"plant_id": 1, "include": []})
    runs = client.post(f"{B}/{wid}/hypotheses/generate", json={}).json()
    h = client.post(f"{B}/{wid}/hypotheses", json={"origin": "generated", "statement": runs[0]["hypotheses"][0]["hypothesis"],
                                                   "source": runs[0]["source"]}).json()
    r = client.patch(f"{B}/{wid}/hypotheses/{h['id']}", json={"causal_attribution": "supported_by_compound_level_experiment"})
    assert r.status_code == 422 and "does not establish causation" in r.json()["detail"]
    r = client.post(f"{B}/{wid}/hypotheses", json={"origin": "researcher", "statement": "Stachydrine may cause the activity.",
                                                   "supporting_evidence_ids": [9],
                                                   "causal_attribution": "supported_by_compound_level_experiment"})
    assert r.status_code == 422
    # generated wording cannot be edited
    assert client.patch(f"{B}/{wid}/hypotheses/{h['id']}", json={"statement": "Stachydrine proven to cure arthritis"}).status_code == 422


def test_generated_hypothesis_must_match_generator(client):
    wid = new_ws(client)["id"]
    r = client.post(f"{B}/{wid}/hypotheses", json={"origin": "generated", "source": {"plant_id": 1},
                                                   "statement": "Hypothesis (unvalidated): stachydrine cures arthritis."})
    assert r.status_code == 422 and "not produced by the generator" in r.json()["detail"]
    assert client.post(f"{B}/{wid}/hypotheses", json={"origin": "generated", "statement": "x" * 20}).status_code == 422


def test_researcher_hypothesis_global_claims_rejected(client):
    wid = new_ws(client)["id"]
    r = client.post(f"{B}/{wid}/hypotheses", json={"origin": "researcher",
                                                   "statement": "Rutin may matter; it has never been studied in caper."})
    assert r.status_code == 422


# ---------------- tasks & timeline -----------------

def test_task_management(client):
    wid = new_ws(client)["id"]
    t = client.post(f"{B}/{wid}/tasks", json={"title": "Verify plant synonym Kovul", "due_date": "2026-12-01",
                                             "related_resources": [{"resource_type": "plant", "resource_ref": "1"}]}).json()
    assert t["due_date"] == "2026-12-01" and t["priority"] == "medium"
    assert client.post(f"{B}/{wid}/tasks", json={"title": "x" * 5, "due_date": "not-a-date"}).status_code == 422
    assert client.post(f"{B}/{wid}/tasks", json={"title": "bad link", "related_resources": [
        {"resource_type": "evidence", "resource_ref": "99999"}]}).status_code == 404
    done = client.patch(f"{B}/{wid}/tasks/{t['id']}", json={"status": "done"}).json()
    assert done["completed_at"]
    assert client.get(f"{B}/{wid}/tasks", params={"outstanding": True}).json() == []
    assert next(w for w in client.get(B).json() if w["id"] == wid)["outstanding_tasks"] == 0
    reopened = client.patch(f"{B}/{wid}/tasks/{t['id']}", json={"status": "open"}).json()
    assert reopened["completed_at"] is None
    actions = [a["action"] for a in client.get(f"{B}/{wid}/timeline").json()]
    assert actions[-3:] == ["task_created", "task_completed", "task_reopened"]


def test_children_are_scoped_to_their_workspace(client):
    a = new_ws(client, title="Investigation A")["id"]
    b = new_ws(client, title="Investigation B")["id"]
    n = client.post(f"{B}/{a}/notes", json={"category": "observation", "body": "belongs to A"}).json()
    assert client.patch(f"{B}/{b}/notes/{n['id']}", json={"body": "hijack"}).status_code == 404
    assert client.delete(f"{B}/{b}/notes/{n['id']}").status_code == 404


# ---------------- reports: markdown safety -----------------

def test_report_markdown_is_escaped(client):
    wid = new_ws(client, title="<script>alert(1)</script> | # x")["id"]
    client.post(f"{B}/{wid}/notes", json={"category": "observation", "body": "<img src=x onerror=alert(1)> [link](javascript:x)"})
    md = client.post(f"{B}/{wid}/reports").json()["markdown"]
    assert "<script>" not in md and "<img" not in md
    # the brackets are escaped, so no Markdown renderer can turn this into a javascript: link
    assert "[link](" not in md and "\\[link\\]" in md
    assert "&lt;script&gt;" in md and "\\|" in md


def test_report_not_found_and_scoped(client):
    a = new_ws(client, title="Report owner A")["id"]
    b = new_ws(client, title="Report owner B")["id"]
    rid = client.post(f"{B}/{a}/reports").json()["id"]
    assert client.get(f"{B}/{b}/reports/{rid}").status_code == 404
    assert client.get(f"{B}/{a}/reports/9999").status_code == 404
    assert client.get(f"{B}/{a}/reports/{rid}/export", params={"format": "pdf"}).status_code == 422


# ---------------- access control -----------------

def test_token_mode_isolates_owners(client, monkeypatch):
    monkeypatch.setenv("RESEARCHER_TOKENS", "Dr A=token-aaaaaaaaaaaaaaaa;Dr B=token-bbbbbbbbbbbbbbbb")
    A = {"Authorization": "Bearer token-aaaaaaaaaaaaaaaa"}
    Bh = {"Authorization": "Bearer token-bbbbbbbbbbbbbbbb"}
    assert client.get(B).status_code == 401
    assert client.get(B, headers={"Authorization": "Bearer wrong-token-xxxxxxxx"}).status_code == 401
    wid = client.post(B, json={"title": "Private A", "research_question": CAPPARIS_Q}, headers=A).json()["id"]
    assert client.get(f"{B}/{wid}", headers=A).json()["owner"] == "Dr A"
    assert client.get(f"{B}/{wid}", headers=Bh).status_code == 404
    assert client.patch(f"{B}/{wid}", json={"title": "taken over"}, headers=Bh).status_code == 404
    assert client.post(f"{B}/{wid}/notes", json={"category": "observation", "body": "x"}, headers=Bh).status_code == 404
    assert client.get(B, headers=Bh).json() == []
    assert client.get(f"{B}/access", headers=A).json()["private"] is True


def test_local_mode_refuses_non_loopback_clients(session_factory):
    def _s():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_session] = _s
    try:
        with TestClient(app, client=("10.1.2.3", 5000)) as remote:
            r = remote.get(B)
            assert r.status_code == 403 and "local single-user mode" in r.json()["detail"]
        with TestClient(app) as local:
            body = local.get(f"{B}/access").json()
            assert body["mode"] == "local_single_user" and body["private"] is False
    finally:
        app.dependency_overrides.clear()


# ---------------- migrations -----------------

def test_migration_0004_upgrade_downgrade(tmp_path):
    from alembic import command
    from alembic.config import Config
    from app.config import ROOT
    from app.db import make_engine
    from app.migrate import upgrade
    db = tmp_path / "m.db"
    engine = make_engine(f"sqlite:///{db}")
    assert upgrade(engine) == "0007"  # head moved 0004 -> 0005 with the 1.0 discovery tables
    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert {"research_workspace", "workspace_resource", "research_note", "workspace_hypothesis", "research_task",
            "workspace_activity", "research_report", "evidence_record"} <= tables
    con.execute("insert into research_workspace (owner,title,research_question,status,created_at,updated_at) values ('u','t','q','planning',0,0)")
    con.commit()
    con.close()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0003")
    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert "research_workspace" not in tables and "evidence_record" in tables and "review_record" in tables
    assert con.execute("select version_num from alembic_version").fetchone() == ("0003",)
    con.close()
    assert upgrade(engine) == "0007"
