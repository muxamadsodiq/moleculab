"""Repurposing analyses saved into research workspaces: snapshots, integrity, comparison, reports, isolation.
Offline: providers are the fixed fakes from tests/test_repurposing.py (ids prefixed FAKE/TEST)."""
import pytest
from sqlalchemy import func, select

from app import repurposing_api
from app.models import EvidenceRecord, Plant, WorkspaceResource
from tests.test_repurposing import FakeChEMBL, FakeCT, FakeOT, cand, ok, DIS, OTHER

B = "/api/v1/workspaces"
Q = "Which compounds recorded against TESTK1 have documented development for test arthritis?"
BODY = {"target": "TESTK1", "disease": "test arthritis", "include_registered_studies": True}


class FakeCTRecords(FakeCT):
    """Registry fake that returns study records: one listing both condition and intervention, one discovery-only
    text match, and one record without an NCT id (to test that it is counted, not invented)."""

    def search(self, condition=None, intervention=None, page_size=25, max_records=None):
        self.calls.append((condition, intervention))
        studies = [
            {"nct_id": "NCT_TEST_0001", "title": f"TEST study of {intervention}", "overall_status": "COMPLETED",
             "phases": ["PHASE3"], "conditions": ["Test Arthritis"], "has_posted_results": True,
             "interventions": [{"name": intervention.title(), "other_names": []}],
             "source_url": "https://clinicaltrials.gov/study/NCT_TEST_0001"},
            {"nct_id": "NCT_TEST_0002", "title": "TEST registry text match", "overall_status": "RECRUITING",
             "phases": ["PHASE2"], "conditions": ["Test Dermatitis"], "has_posted_results": False,
             "interventions": [{"name": "Placebo", "other_names": []}],
             "source_url": "https://clinicaltrials.gov/study/NCT_TEST_0002"},
            {"nct_id": None, "title": "TEST record without id", "conditions": [], "interventions": []},
        ]
        return ok("clinicaltrials", {"total_count": 57, "studies": studies, "returned": 3})


@pytest.fixture()
def providers(monkeypatch):
    state = {"ot": FakeOT(), "chembl": FakeChEMBL(), "ct": FakeCTRecords()}
    monkeypatch.setattr(repurposing_api, "_clients", lambda: dict(state))
    return state


def new_ws(client, title="Repurposing evidence for test arthritis", headers=None):
    r = client.post(B, json={"title": title, "research_question": Q}, headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def save(client, wid, body=None, headers=None):
    return client.post(f"{B}/{wid}/repurposing-analyses", json=body or BODY, headers=headers or {})


# ------------------------------------------------------------------------------ save / list / retrieve

def test_save_list_retrieve(client, providers):
    wid = new_ws(client)
    r = save(client, wid)
    assert r.status_code == 201
    saved = r.json()
    assert saved["created"] is True and saved["integrity"]["status"] == "verified"
    s = saved["summary"]
    assert (s["target"], s["target_id"], s["disease"], s["disease_id"]) == ("TESTK1", "ENSG_TEST", "test arthritis", "MONDO_TEST1")
    assert s["candidate_count"] == 3 and s["software_versions"]["classification_rules"] == "1.0"
    assert s["source_releases"] == {"opentargets": "api 0 / data test", "chembl": "CHEMBL_TEST"}
    assert s["retrieved_from"] and s["retrieved_to"]
    lst = client.get(f"{B}/{wid}/repurposing-analyses").json()
    assert [x["id"] for x in lst] == [saved["id"]] and "analysis" not in lst[0]
    full = client.get(f"{B}/{wid}/repurposing-analyses/{saved['id']}").json()
    a = full["analysis"]
    assert a["parameters"]["target_query"] == "TESTK1" and a["content_fingerprint"] == s["content_fingerprint"]
    for src in a["sources"]:  # provider provenance preserved
        assert {"request_url", "retrieved_at", "http_status", "raw_payload_hash", "purpose", "ok"} <= set(src)
    # the generic resource listing summarises instead of returning the whole snapshot
    res = client.get(f"{B}/{wid}/resources", params={"resource_type": "repurposing_analysis"}).json()
    assert res[0]["historical_snapshot"] is True and "analysis" not in res[0]["snapshot"]


def test_duplicate_save_returns_existing(client, providers):
    wid = new_ws(client)
    first = save(client, wid).json()
    second = save(client, wid)
    assert second.status_code == 200 and second.json()["created"] is False
    assert second.json()["id"] == first["id"] == second.json()["duplicate_of_existing"]
    assert len(client.get(f"{B}/{wid}/repurposing-analyses").json()) == 1


def test_changed_provider_data_creates_new_snapshot_and_old_is_unchanged(client, providers):
    wid = new_ws(client)
    old = save(client, wid).json()
    old_analysis = client.get(f"{B}/{wid}/repurposing-analyses/{old['id']}").json()["analysis"]
    rows = FakeOT().rows
    rows[2]["maximum_clinical_stage"] = "PHASE_3"  # provider data changes "tomorrow"
    providers["ot"] = FakeOT(rows=rows)
    new = save(client, wid).json()
    assert new["created"] is True and new["id"] != old["id"]
    assert new["summary"]["content_fingerprint"] != old["summary"]["content_fingerprint"]
    again = client.get(f"{B}/{wid}/repurposing-analyses/{old['id']}").json()
    assert again["analysis"] == old_analysis and again["integrity"]["status"] == "verified"
    cmp = client.get(f"{B}/{wid}/repurposing-analyses/compare", params={"base": old["id"], "other": new["id"]}).json()
    assert cmp["identical_content"] is False and cmp["same_target_and_disease"] is True
    assert cmp["candidates_changed"]["FAKE_P2"]["opentargets_stages"] == {"base": ["PHASE_2"], "other": ["PHASE_3"]}
    assert "not evidence of a change in any compound's effectiveness" in cmp["note"]
    same = client.get(f"{B}/{wid}/repurposing-analyses/compare", params={"base": old["id"], "other": old["id"]}).json()
    assert same["identical_content"] is True and same["candidates_changed"] == {}


def test_tampered_snapshot_is_detected(client, session_factory, providers):
    wid = new_ws(client)
    rid = save(client, wid).json()["id"]
    with session_factory() as s:
        r = s.get(WorkspaceResource, rid)
        snap = dict(r.snapshot)
        snap["analysis"] = {**snap["analysis"], "candidate_count": 99}
        r.snapshot = snap
        s.commit()
    got = client.get(f"{B}/{wid}/repurposing-analyses/{rid}").json()
    assert got["integrity"]["status"] == "MISMATCH" and got["summary"]["integrity"] == "MISMATCH"
    report = client.get(f"{B}/{wid}/repurposing-analyses/{rid}/report").text
    assert "WARNING: snapshot integrity MISMATCH" in report


def test_fingerprint_reproducible_from_stored_snapshot(client, providers):
    from app.repurposing import fingerprint
    wid = new_ws(client)
    rid = save(client, wid).json()["id"]
    a = client.get(f"{B}/{wid}/repurposing-analyses/{rid}").json()["analysis"]
    assert fingerprint(a) == a["content_fingerprint"]


# ------------------------------------------------------------------------------ evidence preservation

def test_missing_and_conflicting_evidence_preserved(client, providers):
    providers["chembl"] = FakeChEMBL(failing_molecules={"FAKE_P2"}, failing_activity={"FAKE_P2"})
    wid = new_ws(client)
    rid = save(client, wid).json()["id"]
    a = client.get(f"{B}/{wid}/repurposing-analyses/{rid}").json()["analysis"]
    c = {x["candidate_id"]: x for x in a["candidates"]}
    assert {x["type"] for x in c["FAKE_P3"]["contradictions"]} == {"development_stage_disagreement", "action_type_disagreement"}
    assert c["FAKE_P2"]["dimensions"]["experimental_activity"]["status"] == "unavailable"
    assert any("no Open Targets indication record mapped" in m for m in c["FAKE_P2"]["missing_information"])
    assert any(not s["ok"] for s in a["sources"])
    report = client.get(f"{B}/{wid}/repurposing-analyses/{rid}/report").text
    assert "unresolved provider disagreement (development\\_stage\\_disagreement)" in report  # Markdown-escaped


def test_trial_records_and_identifiers_preserved(client, providers):
    wid = new_ws(client)
    rid = save(client, wid).json()["id"]
    a = client.get(f"{B}/{wid}/repurposing-analyses/{rid}").json()["analysis"]
    rs = next(c for c in a["candidates"] if c["candidate_id"] == "FAKE_P1")["dimensions"]["registered_studies"]
    assert rs["discovery_search_total"] == 57 and rs["records_retrieved"] == 3
    by = {r["nct_id"]: r for r in rs["records"]}
    assert by["NCT_TEST_0001"]["match"] == "condition_and_intervention_listed"
    assert by["NCT_TEST_0001"]["source_url"] == "https://clinicaltrials.gov/study/NCT_TEST_0001"
    assert by["NCT_TEST_0001"]["overall_status"] == "COMPLETED" and by["NCT_TEST_0001"]["conditions"] == ["Test Arthritis"]
    assert by["NCT_TEST_0002"]["match"] == "discovery_text_match_only"
    assert rs["condition_and_intervention_listed_in_retrieved"] == 1 and rs["records_without_nct_id"] == 1
    assert None in by and by[None]["match"] == "discovery_text_match_only"  # kept, not given an invented id
    assert "Registration is not an outcome" in rs["meaning"]


def test_malformed_provider_rows_are_reported_not_repaired(client, providers):
    rows = [cand("FAKE_P1", "ALPHANIB", "APPROVAL", indications=[DIS]), "not-an-object",
            {"drug_name": "NOID", "maximum_clinical_stage": "PHASE_1", "indications": None, "mechanisms_of_action": None}]
    providers["ot"] = FakeOT(rows=rows)
    wid = new_ws(client)
    r = save(client, wid)
    assert r.status_code == 201
    a = client.get(f"{B}/{wid}/repurposing-analyses/{r.json()['id']}").json()["analysis"]
    assert any("malformed" in l for l in a["limitations"])
    assert any(c["candidate_id"].startswith("unidentified:") for c in a["candidates"])


# ------------------------------------------------------------------------------ reports

def test_report_separates_content_classes(client, providers):
    wid = new_ws(client)
    saved = save(client, wid).json()
    fp = saved["summary"]["content_fingerprint"]
    n = client.post(f"{B}/{wid}/notes", json={"category": "question", "body": "Check registry records by hand.",
                                             "resource_type": "repurposing_analysis", "resource_ref": fp})
    assert n.status_code == 201
    rep = client.get(f"{B}/{wid}/repurposing-analyses/{saved['id']}/report")
    assert rep.status_code == 200 and rep.headers["content-type"].startswith("text/markdown")
    t = rep.text
    for heading in ("## Scope", "## 1-2. Provider records and computed summaries",
                    "## 3. Researcher notes (researcher-authored; not verified evidence)",
                    "## 4. Computational predictions", "## 5. Unresolved questions"):
        assert heading in t
    assert "historical snapshot" in t and "Snapshot integrity: **verified**" in t
    assert "Check registry records by hand." in t and "No value in this analysis is a prediction" in t
    assert "not a treatment recommendation" in t and "candidates are not ranked" in t
    assert "remains open" in t
    assert client.get(f"{B}/{wid}/repurposing-analyses/{saved['id']}/report").text == t  # deterministic


def test_workspace_report_lists_saved_analyses_only_when_present(client, providers):
    wid = new_ws(client)
    empty = client.post(f"{B}/{wid}/reports").json()
    assert "repurposing_analyses" not in empty["content"]  # unchanged content for workspaces without analyses
    save(client, wid)
    rep = client.post(f"{B}/{wid}/reports").json()
    assert len(rep["content"]["repurposing_analyses"]) == 1
    assert "Saved repurposing evidence inventories (historical snapshots)" in rep["markdown"]


# ------------------------------------------------------------------------------ failures, isolation

def test_invalid_ids_and_wrong_types(client, providers):
    wid = new_ws(client)
    assert save(client, 99999).status_code == 404
    assert client.get(f"{B}/{wid}/repurposing-analyses/99999").status_code == 404
    res = client.post(f"{B}/{wid}/resources", json={"resource_type": "plant", "resource_ref": "1"}).json()
    assert client.get(f"{B}/{wid}/repurposing-analyses/{res['id']}").status_code == 404  # not an analysis
    other = new_ws(client, title="Other investigation")
    rid = save(client, other).json()["id"]
    assert client.get(f"{B}/{wid}/repurposing-analyses/{rid}").status_code == 404  # belongs to another workspace
    assert client.get(f"{B}/{wid}/repurposing-analyses/compare", params={"base": rid, "other": rid}).status_code == 404
    assert client.get(f"{B}/{wid}/repurposing-analyses/compare", params={"base": 0, "other": 1}).status_code == 422
    assert save(client, wid, {"target": "", "disease": "x"}).status_code == 422
    assert client.post(f"{B}/{wid}/notes", json={"category": "question", "body": "x", "resource_type": "repurposing_analysis",
                                                 "resource_ref": "0" * 64}).status_code == 404


def test_client_supplied_snapshot_is_rejected(client, providers):
    wid = new_ws(client)
    r = client.post(f"{B}/{wid}/resources", json={"resource_type": "repurposing_analysis", "resource_ref": "a" * 64})
    assert r.status_code == 422 and "runs the analysis server-side" in r.json()["detail"]


def test_provider_failure_saves_nothing(client, providers):
    providers["ot"] = FakeOT(fail_candidates=True)
    wid = new_ws(client)
    r = save(client, wid)
    assert r.status_code == 502
    assert client.get(f"{B}/{wid}/repurposing-analyses").json() == []


def test_archived_workspace_is_read_only(client, providers):
    wid = new_ws(client)
    rid = save(client, wid).json()["id"]
    client.post(f"{B}/{wid}/archive")
    assert save(client, wid).status_code == 409
    assert client.get(f"{B}/{wid}/repurposing-analyses/{rid}").status_code == 200  # still readable


def test_token_mode_isolation_and_unauthorized(client, providers, monkeypatch):
    monkeypatch.setenv("RESEARCHER_TOKENS", "Dr A=token-aaaaaaaaaaaaaaaa;Dr B=token-bbbbbbbbbbbbbbbb")
    A = {"Authorization": "Bearer token-aaaaaaaaaaaaaaaa"}
    Bh = {"Authorization": "Bearer token-bbbbbbbbbbbbbbbb"}
    wid = new_ws(client, headers=A)
    rid = save(client, wid, headers=A).json()["id"]
    assert save(client, wid).status_code == 401
    assert client.get(f"{B}/{wid}/repurposing-analyses").status_code == 401
    for path in (f"{B}/{wid}/repurposing-analyses", f"{B}/{wid}/repurposing-analyses/{rid}",
                 f"{B}/{wid}/repurposing-analyses/{rid}/report"):
        assert client.get(path, headers=Bh).status_code == 404
    assert client.get(f"{B}/{wid}/repurposing-analyses/compare", params={"base": rid, "other": rid}, headers=Bh).status_code == 404
    assert save(client, wid, headers=Bh).status_code == 404


def test_no_mutation_of_scientific_records(client, session_factory, providers):
    with session_factory() as s:
        before = [(e.id, e.original_excerpt, e.evidence_type) for e in s.scalars(select(EvidenceRecord).order_by(EvidenceRecord.id))]
        plants = s.scalar(select(func.count()).select_from(Plant))
    wid = new_ws(client)
    save(client, wid)
    with session_factory() as s:
        after = [(e.id, e.original_excerpt, e.evidence_type) for e in s.scalars(select(EvidenceRecord).order_by(EvidenceRecord.id))]
        assert after == before and s.scalar(select(func.count()).select_from(Plant)) == plants
        from app.models import BiologicalTarget, Disease
        assert s.scalar(select(func.count()).select_from(Disease)) == 0  # saving does not persist context
        assert s.scalar(select(func.count()).select_from(BiologicalTarget)) == 0
