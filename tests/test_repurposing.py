"""Repurposing evidence-inventory tests. Offline: providers are replaced with fakes returning fixed,
clearly artificial records (ids prefixed TEST/FAKE). MOLECULAB_LIVE=1 adds one live check."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from app import repurposing_api
from app.clients.base import SourceResult
from app.db import get_session
from app.main import app
from app.repurposing import (CandidateClass, RepurposingConfig, RepurposingError, analyse, classify, fingerprint,
                             persist_context, summary_markdown)

live = pytest.mark.skipif(os.getenv("MOLECULAB_LIVE") != "1", reason="set MOLECULAB_LIVE=1 for live API tests")
FIXED = "2026-11-17T00:00:00+00:00"


def ok(source, data, **kw):
    return SourceResult(source, True, data=data, request_url=f"https://fake/{source}", retrieved_at=kw.pop("at", FIXED),
                        status_code=200, raw_payload_hash=json.dumps(data, sort_keys=True, default=str)[:64], **kw)


def fail(source, error="HTTP 500"):
    return SourceResult(source, False, error=error, request_url=f"https://fake/{source}", retrieved_at=FIXED, status_code=500)


def cand(drug_id, name, stage, actions=("INHIBITOR",), indications=(), warnings=(), row_stage=None):
    return {"candidate_id": f"row-{drug_id}", "row_max_clinical_stage": row_stage or stage, "drug_chembl_id": drug_id,
            "drug_name": name, "drug_type": "Small molecule", "maximum_clinical_stage": stage,
            "mechanisms_of_action": [{"mechanism_of_action": "TEST kinase inhibitor", "action_type": a} for a in actions],
            "drug_warnings": list(warnings),
            "indications": [{"disease_id": d, "disease_name": n, "disease_from_source": src} for d, n, src in indications]}


DIS = ("MONDO_TEST1", "test arthritis", "test arthritis")
OTHER = ("MONDO_TEST2", "test dermatitis", "test dermatitis")


class FakeOT:
    def __init__(self, rows=None, assoc_rows=None, fail_candidates=False, search_hits=None, at=FIXED):
        self.rows = rows if rows is not None else [
            cand("FAKE_P1", "ALPHANIB", "APPROVAL", indications=[DIS, OTHER]),
            cand("FAKE_S1", "ALPHANIB CITRATE", "APPROVAL", indications=[DIS]),
            cand("FAKE_P2", "BETANIB", "PHASE_2", indications=[OTHER, (None, None, "test arthritis, refractory")]),
            cand("FAKE_P3", "GAMMANIB", "APPROVAL", actions=("AGONIST",), indications=[OTHER]),
        ]
        self.assoc_rows = assoc_rows if assoc_rows is not None else [
            {"disease_id": "MONDO_TEST1", "disease_name": "test arthritis", "overall_score": 0.5,
             "datatype_scores": {"genetic_association": 0.4}}]
        self.fail_candidates, self.search_hits, self.at = fail_candidates, search_hits, at

    def release(self):
        r = ok("opentargets", {"api_version": "0", "data_version": "test"}, at=self.at)
        r.source_release = "api 0 / data test"
        return r

    def search(self, q, entities, size):
        if self.search_hits is not None:
            return self.search_hits
        hits = {"target": [{"id": "ENSG_TEST", "name": "TESTK1", "entity": "target"},
                           {"id": "ENSG_TEST2", "name": "TESTK2", "entity": "target"}],
                "disease": [{"id": "MONDO_TEST1", "name": "test arthritis", "entity": "disease"},
                            {"id": "MONDO_TEST3", "name": "test arthritis, juvenile", "entity": "disease"}]}[entities[0]]
        return ok("opentargets", hits, at=self.at)

    def target_diseases(self, ensembl_id, size):
        return ok("opentargets", {"count": len(self.assoc_rows), "rows": self.assoc_rows}, at=self.at)

    def target_drug_candidates(self, ensembl_id):
        if self.fail_candidates:
            return fail("opentargets")
        return ok("opentargets", {"count": len(self.rows), "rows": self.rows}, at=self.at)


class FakeChEMBL:
    def __init__(self, molecules=None, failing_molecules=(), activity=None, failing_activity=(), mechanisms=None,
                 targets=None):
        self.molecules = molecules if molecules is not None else {
            "FAKE_P1": {"chembl_id": "FAKE_P1", "pref_name": "ALPHANIB", "max_phase": "4.0", "parent_chembl_id": "FAKE_P1"},
            "FAKE_S1": {"chembl_id": "FAKE_S1", "pref_name": "ALPHANIB CITRATE", "max_phase": "4.0", "parent_chembl_id": "FAKE_P1"},
            "FAKE_P2": {"chembl_id": "FAKE_P2", "pref_name": "BETANIB", "max_phase": "2.0", "parent_chembl_id": "FAKE_P2"},
            "FAKE_P3": {"chembl_id": "FAKE_P3", "pref_name": "GAMMANIB", "max_phase": "2.0", "parent_chembl_id": "FAKE_P3"},
        }
        self.failing_molecules, self.failing_activity = set(failing_molecules), set(failing_activity)
        self.activity = activity if activity is not None else {"FAKE_P1": 12, "FAKE_S1": 0, "FAKE_P2": 3, "FAKE_P3": 0}
        self.mechanisms = mechanisms if mechanisms is not None else [
            {"molecule_chembl_id": "FAKE_P1", "action_type": "INHIBITOR", "mechanism_of_action": "TEST inhibitor",
             "direct_interaction": True},
            {"molecule_chembl_id": "FAKE_P3", "action_type": "INHIBITOR", "mechanism_of_action": "TEST inhibitor",
             "direct_interaction": True}]
        self.targets = targets if targets is not None else [
            {"chembl_id": "CHEMBL_TEST", "target_type": "SINGLE PROTEIN", "organism": "Homo sapiens", "gene_symbols": ["TESTK1"]},
            {"chembl_id": "CHEMBL_CPLX", "target_type": "PROTEIN COMPLEX", "organism": "Homo sapiens", "gene_symbols": ["TESTK1"]}]

    def release(self):
        r = ok("chembl", {"chembl_db_version": "CHEMBL_TEST"})
        r.source_release = "CHEMBL_TEST"
        return r

    def resolve_target(self, symbol):
        return ok("chembl", self.targets)

    def mechanisms_for_target(self, tid):
        return ok("chembl", self.mechanisms)

    def molecule(self, mid):
        if mid in self.failing_molecules or mid not in self.molecules:
            return fail("chembl")
        return ok("chembl", self.molecules[mid])

    def activity_count_for_molecule(self, tid, mid):
        if mid in self.failing_activity:
            return fail("chembl", "ReadTimeout")
        return ok("chembl", self.activity.get(mid, 0))


class FakeCT:
    def __init__(self, fail_names=()):
        self.fail_names, self.calls = set(fail_names), []

    def search(self, condition=None, intervention=None, page_size=25, max_records=None):
        self.calls.append((condition, intervention))
        if intervention in self.fail_names:
            return fail("clinicaltrials")
        return ok("clinicaltrials", {"total_count": len(intervention), "studies": [], "returned": 0})


def run(**kw):
    clients = {"ot": kw.pop("ot", FakeOT()), "chembl": kw.pop("chembl", FakeChEMBL()), "ct": kw.pop("ct", FakeCT())}
    cfg = RepurposingConfig("TESTK1", "test arthritis", include_registered_studies=True, **kw)
    return analyse(cfg, **clients)


def by_id(a):
    return {c["candidate_id"]: c for c in a["candidates"]}


# ------------------------------------------------------------------ identity, deduplication, provenance

def test_salt_forms_grouped_under_parent_with_source_ids_preserved():
    a = run()
    c = by_id(a)
    assert set(c) == {"FAKE_P1", "FAKE_P2", "FAKE_P3"}
    assert [f["chembl_id"] for f in c["FAKE_P1"]["forms"]] == ["FAKE_P1", "FAKE_S1"]
    assert {n for f in c["FAKE_P1"]["forms"] for n in f["names"]} == {"ALPHANIB", "ALPHANIB CITRATE"}


def test_exact_duplicate_rows_are_merged_not_double_counted():
    rows = [cand("FAKE_P2", "BETANIB", "PHASE_2", indications=[OTHER]),
            cand("FAKE_P2", "BETANIB", "PHASE_2", indications=[OTHER])]
    a = run(ot=FakeOT(rows=rows))
    assert a["candidate_count"] == 1
    assert len(by_id(a)["FAKE_P2"]["dimensions"]["other_indications"]["mapped"]) == 1


def test_every_cited_source_exists_and_carries_provenance():
    a = run()
    ids = {s["id"] for s in a["sources"]}
    text = json.dumps(a)
    import re
    cited = set(re.findall(r'"(src-\d+)"', json.dumps({k: v for k, v in a.items() if k != "sources"})))
    assert cited and cited <= ids
    for s in a["sources"]:
        assert s["request_url"] and s["retrieved_at"] and "raw_payload_hash" in s
    assert a["source_releases"] == {"opentargets": "api 0 / data test", "chembl": "CHEMBL_TEST"}
    assert "FAKE_P1" in text


# ------------------------------------------------------------------ target and disease handling

def test_identifiers_are_never_guessed():
    ot = FakeOT(search_hits=ok("opentargets", [{"id": "A", "name": "TESTK1-like", "entity": "target"},
                                               {"id": "B", "name": "TESTK1 variant", "entity": "target"}]))
    with pytest.raises(RepurposingError) as e:
        run(ot=ot)
    assert e.value.status == 422 and len(e.value.detail["candidates"]) == 2


def test_chembl_target_requires_one_human_single_protein():
    a = run()
    assert a["target"]["chembl_target_id"] == "CHEMBL_TEST"  # the PROTEIN COMPLEX was not chosen
    a = run(chembl=FakeChEMBL(targets=[{"chembl_id": "X", "target_type": "PROTEIN FAMILY", "organism": "Homo sapiens",
                                        "gene_symbols": ["TESTK1"]}]))
    assert a["target"]["chembl_target_id"] is None
    assert any("none was chosen" in l for l in a["limitations"])
    assert all(c["dimensions"]["experimental_activity"]["status"] == "unavailable" for c in a["candidates"])


def test_disease_evidence_is_not_transferred_between_diseases():
    c = by_id(run())
    assert c["FAKE_P1"]["dimensions"]["disease_association"]["indication_records_for_this_disease"]
    # BETANIB and GAMMANIB have development recorded only for another disease
    for cid in ("FAKE_P2", "FAKE_P3"):
        d = c[cid]["dimensions"]
        assert d["disease_association"]["indication_records_for_this_disease"] == []
        assert {"disease_id": "MONDO_TEST2", "disease_name": "test dermatitis"} in d["other_indications"]["mapped"]
        assert any("no Open Targets indication record mapped" in m for m in c[cid]["missing_information"])


def test_unmapped_indication_text_is_kept_not_matched():
    d = by_id(run())["FAKE_P2"]["dimensions"]
    assert d["disease_association"]["indication_records_for_this_disease"] == []
    assert d["disease_association"]["unmapped_indication_text_mentioning_disease"] == ["test arthritis, refractory"]
    assert d["other_indications"]["unmapped_count"] == 1


def test_association_absent_from_returned_rows_is_not_absence_of_association():
    a = run(ot=FakeOT(assoc_rows=[]))
    assert a["target_disease_association"]["status"] == "not_in_returned_rows"
    assert a["target_disease_association"]["provider_query"] == "Open Targets Target.associatedDiseases"
    assert any("not evidence of no association" in l for l in a["limitations"])
    assert "Not a probability" in a["target_disease_association"]["score_meaning"] or \
        "not a probability" in a["target_disease_association"]["score_meaning"]


# ------------------------------------------------------------------ classification

@pytest.mark.parametrize("stages,phase,activity,reg,expected", [
    (["APPROVAL"], None, None, [], CandidateClass.CLINICAL_STAGE_CANDIDATE),
    (["PHASE_4"], None, None, [], CandidateClass.CLINICAL_STAGE_CANDIDATE),
    ([], "4.0", None, [], CandidateClass.CLINICAL_STAGE_CANDIDATE),
    (["PHASE_2"], "2.0", None, [], CandidateClass.INVESTIGATIONAL_DRUG),
    (["EARLY_PHASE_1"], None, None, [], CandidateClass.INVESTIGATIONAL_DRUG),
    (["PRECLINICAL"], None, None, [], CandidateClass.PRECLINICAL_COMPOUND),
    ([], None, True, [], CandidateClass.EXPERIMENTAL_MOLECULE),
    ([], None, None, [], CandidateClass.UNKNOWN),
    (["APPROVAL"], "4.0", None, [{"chembl_id": "X", "jurisdiction": "TESTLAND", "authority": "Test Agency",
                                  "reference": "TEST-REG-001"}], CandidateClass.APPROVED_DRUG),
])
def test_classification(stages, phase, activity, reg, expected):
    c = classify(stages, phase, activity, reg)
    assert c["category"] == expected
    if not reg:
        assert c["regulatory_approval"].startswith("not established")


def test_provider_stage_labels_never_become_approval():
    a = run()
    for c in a["candidates"]:
        assert c["classification"]["category"] != CandidateClass.APPROVED_DRUG
        assert "not established" in c["classification"]["regulatory_approval"]
    assert by_id(a)["FAKE_P1"]["classification"]["category"] == CandidateClass.CLINICAL_STAGE_CANDIDATE
    assert by_id(a)["FAKE_P2"]["classification"]["category"] == CandidateClass.INVESTIGATIONAL_DRUG


def test_regulatory_record_must_be_jurisdiction_specific():
    with pytest.raises(RepurposingError):
        run(regulatory_records=[{"chembl_id": "FAKE_P1", "jurisdiction": "", "authority": "x", "reference": "y"}])
    a = run(regulatory_records=[{"chembl_id": "FAKE_P1", "jurisdiction": "TESTLAND", "authority": "Test Agency",
                                 "reference": "TEST-REG-001"}])
    c = by_id(a)["FAKE_P1"]["classification"]
    assert c["category"] == CandidateClass.APPROVED_DRUG and "TESTLAND" in c["basis"][0]
    assert by_id(a)["FAKE_P2"]["classification"]["category"] != CandidateClass.APPROVED_DRUG


# ------------------------------------------------------------------ contradictions and missing evidence

def test_stage_and_action_contradictions_are_visible():
    c = by_id(run())["FAKE_P3"]  # OT: APPROVAL + AGONIST; ChEMBL: max_phase 2 + INHIBITOR
    types = {x["type"] for x in c["contradictions"]}
    assert types == {"development_stage_disagreement", "action_type_disagreement"}
    stage = next(x for x in c["contradictions"] if x["type"] == "development_stage_disagreement")
    assert stage["opentargets"] == ["APPROVAL"] and stage["chembl_max_phase"] == 2.0 and stage["sources"]


def test_chembl_failure_is_unavailable_not_absent():
    a = run(chembl=FakeChEMBL(failing_molecules={"FAKE_P2"}, failing_activity={"FAKE_P2"}))
    c = by_id(a)["FAKE_P2"]
    assert c["forms"][0]["chembl_record"] == "unavailable"
    assert c["dimensions"]["clinical_development_stage"]["chembl_max_phase"] is None
    assert c["dimensions"]["experimental_activity"]["status"] == "unavailable"
    assert c["dimensions"]["experimental_activity"]["record_counts"] == {}
    assert any("ChEMBL molecule records were unavailable" in l for l in a["limitations"])
    assert any(not s["ok"] for s in a["sources"])


def test_missing_chembl_mechanism_is_reported():
    c = by_id(run())["FAKE_P2"]
    assert c["dimensions"]["target_relationship"]["chembl"]["status"] == "not_recorded"
    assert "no ChEMBL mechanism record links this compound to the target" in c["missing_information"]


def test_registered_study_failure_and_meaning():
    a = run(ct=FakeCT(fail_names={"BETANIB"}))
    c = by_id(a)
    assert c["FAKE_P2"]["dimensions"]["registered_studies"]["status"] == "unavailable"
    rs = c["FAKE_P1"]["dimensions"]["registered_studies"]
    assert rs["status"] == "recorded" and "not an outcome" in rs["meaning"]


def test_recorded_warnings_empty_means_none_recorded():
    d = by_id(run())["FAKE_P1"]["dimensions"]["recorded_warnings"]
    assert d["warnings"] == [] and "not that the compound is safe" in d["meaning"]


# ------------------------------------------------------------------ comparison dimensions

def test_dimensions_are_separate_and_unranked():
    a = run()
    names = [c["name"] for c in a["candidates"]]
    assert names == sorted(names, key=str.lower)
    assert "not ranked" in a["candidate_ordering"]
    for c in a["candidates"]:
        assert set(c["dimensions"]) == {"target_relationship", "experimental_activity", "disease_association",
                                        "clinical_development_stage", "other_indications", "registered_studies",
                                        "recorded_warnings", "evidence_completeness"}
        assert not any("score" in k or "rank" in k for k in c)
    assert by_id(a)["FAKE_P1"]["dimensions"]["experimental_activity"]["record_counts"] == {"FAKE_P1": 12, "FAKE_S1": 0}


def test_without_chembl_the_gaps_are_declared():
    a = analyse(RepurposingConfig("TESTK1", "test arthritis", include_chembl=False), ot=FakeOT())
    assert any("ChEMBL was not queried" in l for l in a["limitations"])
    for c in a["candidates"]:
        assert c["dimensions"]["target_relationship"]["chembl"] == {"status": "unavailable"}
        assert c["dimensions"]["experimental_activity"]["status"] == "not_requested"


# ------------------------------------------------------------------ failures

def test_candidate_list_failure_raises_502():
    with pytest.raises(RepurposingError) as e:
        run(ot=FakeOT(fail_candidates=True))
    assert e.value.status == 502


def test_search_failure_raises_502():
    with pytest.raises(RepurposingError) as e:
        run(ot=FakeOT(search_hits=fail("opentargets")))
    assert e.value.status == 502


def test_config_validation():
    with pytest.raises(RepurposingError):
        analyse(RepurposingConfig("", "x"), ot=FakeOT())
    with pytest.raises(RepurposingError):
        analyse(RepurposingConfig("TESTK1", "test arthritis", max_candidates=0), ot=FakeOT())


# ------------------------------------------------------------------ determinism and summary

def test_fingerprint_ignores_retrieval_time_but_not_data():
    a = run(ot=FakeOT(at="2026-01-01T00:00:00+00:00"))
    b = run(ot=FakeOT(at="2026-12-31T23:59:59+00:00"))
    assert a["content_fingerprint"] == b["content_fingerprint"] == fingerprint(a)
    rows = FakeOT().rows
    rows[2]["maximum_clinical_stage"] = "PHASE_3"
    c = run(ot=FakeOT(rows=rows))
    assert c["content_fingerprint"] != a["content_fingerprint"]


def test_summary_is_deterministic_and_carries_disclaimer():
    a, b = run(), run()
    for x in (a, b):
        x["generated_at"] = "fixed"
    sa, sb = summary_markdown(a), summary_markdown(b)
    assert sa == sb
    assert "not a treatment recommendation" in sa and "not a statement of regulatory approval" in sa
    assert "none recorded ≠ safe" in sa and "candidates are not ranked" in sa
    assert "development\\_stage\\_disagreement" in sa  # Markdown-escaped by app.workspace.md


# ------------------------------------------------------------------ persistence and API

def test_persist_context_is_idempotent_and_keeps_provenance(session):
    from sqlalchemy import func, select
    from app.models import BiologicalTarget, Disease, EvidenceRecord, TargetDiseaseEvidence
    a = run()
    first = persist_context(session, a)
    assert first["created"] == {"disease": True, "target": True, "association": True}
    second = persist_context(session, a)
    assert second["created"] == {"disease": False, "target": False, "association": False}
    t = session.scalar(select(BiologicalTarget))
    assert t.ensembl_id == "ENSG_TEST" and t.provider == "opentargets" and t.retrieved_at == FIXED
    link = session.scalar(select(TargetDiseaseEvidence))
    assert link.overall_score == 0.5 and "Not a probability" in link.score_meaning
    assert session.scalar(select(func.count()).select_from(Disease)) == 1
    assert session.scalar(select(func.count()).select_from(EvidenceRecord)) == 37  # curated data untouched


@pytest.fixture()
def api(client, monkeypatch):
    monkeypatch.setattr(repurposing_api, "_clients", lambda: {"ot": FakeOT(), "chembl": FakeChEMBL(), "ct": FakeCT()})
    return client


def test_api_analysis_and_markdown(api):
    body = {"target": "TESTK1", "disease": "test arthritis", "include_registered_studies": True}
    r = api.post("/api/v1/repurposing/analyses", json=body)
    assert r.status_code == 200 and r.json()["candidate_count"] == 3
    assert api.post("/api/repurposing/analyses", json=body).json()["content_fingerprint"] == r.json()["content_fingerprint"]
    m = api.post("/api/v1/repurposing/analyses", json=body, params={"format": "markdown"})
    assert m.status_code == 200 and m.headers["content-type"].startswith("text/markdown")
    rules = api.get("/api/v1/repurposing/classification-rules").json()
    assert "never establish approval" in rules["approval_rule"]


def test_api_errors(api, monkeypatch):
    assert api.post("/api/v1/repurposing/analyses", json={"target": "", "disease": "x"}).status_code == 422
    assert api.post("/api/v1/repurposing/analyses", json={"target": "a", "disease": "b",
                                                          "max_candidates": 9999}).status_code == 422
    monkeypatch.setattr(repurposing_api, "_clients", lambda: {"ot": FakeOT(fail_candidates=True), "chembl": FakeChEMBL()})
    r = api.post("/api/v1/repurposing/analyses", json={"target": "TESTK1", "disease": "test arthritis"})
    assert r.status_code == 502 and "unavailable" in r.json()["detail"]["error"]


@live
def test_live_jak1_rheumatoid_arthritis_inventory():
    a = analyse(RepurposingConfig("JAK1", "rheumatoid arthritis", include_chembl=False))
    assert a["target"]["ensembl_id"] == "ENSG00000162434" and a["disease"]["ontology_id"] == "MONDO_0008383"
    assert a["candidate_count"] >= 1
    assert all(c["classification"]["category"] != CandidateClass.APPROVED_DRUG for c in a["candidates"])
    assert all(s["ok"] for s in a["sources"])


# ------------------------------------------------------------------ ChEMBL client additions (mock transport)

def _mocked_chembl(handler):
    import httpx
    from app.clients.chembl import ChEMBLClient
    return ChEMBLClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0)


def test_chembl_parent_id_from_hierarchy_and_none_when_absent():
    import httpx

    def handler(req):
        if "FAKE_SALT" in str(req.url):
            return httpx.Response(200, json={"molecule_chembl_id": "FAKE_SALT", "molecule_structures": {},
                                             "molecule_properties": {},
                                             "molecule_hierarchy": {"molecule_chembl_id": "FAKE_SALT",
                                                                    "parent_chembl_id": "FAKE_PARENT"}})
        return httpx.Response(200, json={"molecule_chembl_id": "FAKE_X", "molecule_structures": {},
                                         "molecule_properties": {}})
    c = _mocked_chembl(handler)
    assert c.molecule("FAKE_SALT").data["parent_chembl_id"] == "FAKE_PARENT"
    assert c.molecule("FAKE_X").data["parent_chembl_id"] is None  # never invented


def test_chembl_activity_count_for_molecule():
    import httpx
    seen = {}

    def handler(req):
        seen.update(dict(req.url.params))
        return httpx.Response(200, json={"page_meta": {"total_count": 42}, "activities": []})
    res = _mocked_chembl(handler).activity_count_for_molecule("CHEMBL_T", "CHEMBL_M")
    assert res.ok and res.data == 42
    assert seen["target_chembl_id"] == "CHEMBL_T" and seen["molecule_chembl_id"] == "CHEMBL_M"
    bad = _mocked_chembl(lambda req: httpx.Response(200, json={"unexpected": 1})).activity_count_for_molecule("A", "B")
    assert not bad.ok and "unexpected" in bad.error
