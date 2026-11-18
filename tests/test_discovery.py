"""Discovery (disease/target explorer + graph) tests. Offline by default via httpx.MockTransport.

MOLECULAB_LIVE=1 adds a live check against the real providers.
"""
import json
import os

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import discovery_api
from app.clients.chembl import ChEMBLClient
from app.clients.clinicaltrials import ClinicalTrialsClient
from app.clients.opentargets import OpenTargetsClient

live = pytest.mark.skipif(os.getenv("MOLECULAB_LIVE") != "1", reason="set MOLECULAB_LIVE=1 for live API tests")


def mocked(cls, handler, **kw):
    return cls(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0, max_retries=0, **kw)


# ----------------------------------------------------------------------------- fake provider payloads

DISEASE = {"disease": {
    "id": "MONDO_0000001", "name": "test disease", "description": "d", "dbXRefs": ["X:1"],
    "synonyms": [{"relation": "hasExactSynonym", "terms": ["td"]}],
    "therapeuticAreas": [{"id": "EFO_1", "name": "immune system disease"}],
    "associatedTargets": {"count": 2, "rows": [
        {"score": 0.8, "datatypeScores": [{"id": "genetic_association", "score": 0.7},
                                          {"id": "clinical", "score": 0.1},
                                          {"id": "literature", "score": 0.5}],
         "target": {"id": "ENSG1", "approvedSymbol": "AAA", "approvedName": "a", "biotype": "protein_coding",
                    "proteinIds": [{"id": "P00001", "source": "uniprot_swissprot"}],
                    "targetClass": [{"id": 1, "label": "Kinase", "level": "l1"}]}},
        {"score": 0.4, "datatypeScores": [{"id": "animal_model", "score": 0.4}],
         "target": {"id": "ENSG2", "approvedSymbol": "BBB", "approvedName": "b", "biotype": "protein_coding",
                    "proteinIds": [], "targetClass": []}}]}}}

DRUGS = {"target": {"id": "ENSG1", "approvedSymbol": "AAA", "drugAndClinicalCandidates": {"count": 3, "rows": [
    {"id": "c1", "maxClinicalStage": "PHASE_3", "drug": {
        "id": "CHEMBL1", "name": "drugone", "drugType": "Small molecule", "maximumClinicalStage": "PHASE_3",
        "drugWarnings": [], "mechanismsOfAction": {"rows": [{"mechanismOfAction": "AAA inhibitor",
                                                             "actionType": "INHIBITOR"}]}},
     "diseases": []},
    {"id": "c2", "maxClinicalStage": "PHASE_1", "drug": {
        "id": "CHEMBL2", "name": "drugtwo", "drugType": "Antibody", "maximumClinicalStage": "PHASE_1",
        "drugWarnings": [], "mechanismsOfAction": {"rows": []}}, "diseases": []},
    {"id": "c3", "maxClinicalStage": "PHASE_2", "drug": None, "diseases": []}]}}}

TARGET = {"target": {"id": "ENSG1", "approvedSymbol": "AAA", "approvedName": "a", "biotype": "protein_coding",
                     "functionDescriptions": [], "synonyms": [],
                     "proteinIds": [{"id": "P00001", "source": "uniprot_swissprot"}],
                     "targetClass": [{"id": 1, "label": "Kinase", "level": "l1"}],
                     "genomicLocation": None, "safetyLiabilities": [], "tractability": []}}

TARGET_DISEASES = {"target": {"id": "ENSG1", "approvedSymbol": "AAA", "associatedDiseases": {"count": 1, "rows": [
    {"score": 0.8, "datatypeScores": [{"id": "clinical", "score": 0.1}],
     "disease": {"id": "MONDO_0000001", "name": "test disease", "therapeuticAreas": []}}]}}}

META = {"meta": {"apiVersion": {"x": 4, "y": 0, "z": 0}, "dataVersion": {"year": 26, "month": 9}}}


def ot_handler(fail=()):
    """Open Targets mock dispatching on the GraphQL text. `fail` lists query names that return HTTP 500."""
    def handler(req):
        q = json.loads(req.content)["query"]
        for name, payload in (("query Disease(", DISEASE), ("query TargetDrugs(", DRUGS),
                              ("query TargetDiseases(", TARGET_DISEASES), ("query Target(", TARGET),
                              ("meta {", META)):
            if name in q:
                if name in fail:
                    return httpx.Response(500, json={"errors": [{"message": "boom"}]})
                return httpx.Response(200, json={"data": payload})
        if "search(" in q:
            return httpx.Response(200, json={"data": {"search": {"total": 1, "hits": [
                {"id": "MONDO_0000001", "name": "test disease", "entity": "disease", "description": None}]}}})
        return httpx.Response(400, json={"errors": [{"message": "unexpected query"}]})
    return handler


def chembl_handler(req):
    path = req.url.path
    if path.endswith("/target.json"):
        return httpx.Response(200, json={"targets": [
            {"target_chembl_id": "CHEMBL_FAM", "pref_name": "family", "target_type": "PROTEIN FAMILY",
             "target_components": []},
            {"target_chembl_id": "CHEMBL_AAA", "pref_name": "AAA kinase", "target_type": "SINGLE PROTEIN",
             "organism": "Homo sapiens", "target_components": [
                 {"accession": "P00001", "target_component_synonyms": [
                     {"syn_type": "GENE_SYMBOL", "component_synonym": "AAA"}]}]}]})
    if path.endswith("/activity.json"):
        n = {"IC50": 1234, "Ki": 56}[req.url.params["standard_type"]]
        return httpx.Response(200, json={"activities": [], "page_meta": {"total_count": n, "next": None}})
    return httpx.Response(404)


def ct_handler(req):
    return httpx.Response(200, json={"totalCount": 2, "studies": [
        {"protocolSection": {"identificationModule": {"nctId": "NCT00000001", "briefTitle": "t"},
                             "statusModule": {"overallStatus": "COMPLETED"},
                             "designModule": {"phases": ["PHASE2"]}}, "hasResults": False}]})


def make_client(monkeypatch, ot_fail=(), chembl=chembl_handler, ct=ct_handler):
    clients = {"ot": mocked(OpenTargetsClient, ot_handler(ot_fail)),
               "chembl": mocked(ChEMBLClient, chembl), "ct": mocked(ClinicalTrialsClient, ct)}
    monkeypatch.setattr(discovery_api, "_clients", lambda: clients)
    app = FastAPI()
    app.include_router(discovery_api.router, prefix="/api/v1/discovery")
    return TestClient(app)


BASE = "/api/v1/discovery"


# ------------------------------------------------------------------------------------------- tests

def test_disease_detail_shape_and_provenance(monkeypatch):
    r = make_client(monkeypatch).get(BASE + "/diseases/MONDO_0000001")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["disease"]["id"] == "MONDO_0000001"
    assert body["disease"]["synonyms"][0]["terms"] == ["td"]
    assert body["disease"]["therapeutic_areas"][0]["name"] == "immune system disease"
    assert len(body["associated_targets"]) == 2
    assert "not probabilities" in body["score_note"]
    prov = body["provenance"]
    assert prov["provider"] == "opentargets" and prov["retrieved_at"]
    assert prov["source_release"] and "26-9" in prov["source_release"]


def test_datatype_scores_kept_separate_not_merged(monkeypatch):
    rows = make_client(monkeypatch).get(BASE + "/diseases/MONDO_0000001").json()["associated_targets"]
    first = rows[0]
    assert first["overall_score"] == 0.8
    assert first["datatype_scores"] == {"genetic_association": 0.7, "clinical": 0.1, "literature": 0.5}
    # a datatype with no recorded score is absent, not zero-filled
    assert "animal_model" not in first["datatype_scores"]
    assert rows[1]["datatype_scores"] == {"animal_model": 0.4}


def test_failed_provider_reports_unavailable_not_fake_data(monkeypatch):
    c = make_client(monkeypatch, ot_fail=("query Disease(",))
    body = c.get(BASE + "/diseases/MONDO_0000001").json()
    assert body["available"] is False
    assert body["error"]
    assert "associated_targets" not in body and "disease" not in body


def test_search_failure_is_flagged(monkeypatch):
    def broken(req):
        return httpx.Response(503, text="down")
    clients = {"ot": mocked(OpenTargetsClient, broken), "chembl": None, "ct": None}
    monkeypatch.setattr(discovery_api, "_clients", lambda: clients)
    app = FastAPI()
    app.include_router(discovery_api.router, prefix=BASE)
    body = TestClient(app).get(BASE + "/diseases/search", params={"q": "asthma"}).json()
    assert body["available"] is False and body["error"] and body["results"] == []


def test_target_detail_partial_failure_returns_surviving_parts(monkeypatch):
    c = make_client(monkeypatch, ot_fail=("query TargetDrugs(",))
    body = c.get(BASE + "/targets/ENSG1").json()
    assert body["available"] is True
    assert body["target"]["available"] is True
    assert body["associated_diseases"]["available"] is True
    assert body["drug_candidates"]["available"] is False and body["drug_candidates"]["error"]
    assert "data" not in body["drug_candidates"]


def test_target_chembl_single_protein_counts(monkeypatch):
    body = make_client(monkeypatch).get(BASE + "/targets/ENSG1").json()
    cb = body["chembl"]
    assert cb["resolved"] is True and cb["target"]["chembl_id"] == "CHEMBL_AAA"
    assert cb["target"]["target_type"] == "SINGLE PROTEIN"
    assert cb["activity_counts"]["IC50"]["record_count"] == 1234
    assert cb["activity_counts"]["Ki"]["record_count"] == 56
    assert "not potencies" in cb["activity_count_note"]
    assert "not a statement of regulatory approval" in body["drug_candidates"]["stage_note"]


def test_target_chembl_no_single_protein_gives_no_counts(monkeypatch):
    def only_family(req):
        return httpx.Response(200, json={"targets": [
            {"target_chembl_id": "CHEMBL_FAM", "pref_name": "family", "target_type": "PROTEIN FAMILY",
             "target_components": []}]})
    body = make_client(monkeypatch, chembl=only_family).get(BASE + "/targets/ENSG1").json()
    assert body["chembl"]["resolved"] is False
    assert "activity_counts" not in body["chembl"]


def test_chembl_failure_marked_unavailable(monkeypatch):
    body = make_client(monkeypatch, chembl=lambda req: httpx.Response(500)).get(BASE + "/targets/ENSG1").json()
    assert body["chembl"]["available"] is False and body["chembl"]["error"]
    assert body["target"]["available"] is True


def test_graph_edges_only_from_provider_assertions(monkeypatch):
    g = make_client(monkeypatch).get(BASE + "/graph", params={"disease": "MONDO_0000001"}).json()
    assert g["available"] is True
    nodes = {n["id"]: n for n in g["nodes"]}
    # disease + 2 targets + 2 identified drugs (the drug-less record is skipped, not invented)
    assert set(nodes) == {"disease:MONDO_0000001", "target:ENSG1", "target:ENSG2",
                          "drug:CHEMBL1", "drug:CHEMBL2"}
    assert g["drug_section"]["records_without_drug_id_skipped"] == 1
    for n in nodes.values():
        assert n["id"] and n["type"] and n["label"] and n["source"] and n["identifiers"]
    pairs = {(e["source"], e["target"]) for e in g["edges"]}
    assert pairs == {("disease:MONDO_0000001", "target:ENSG1"), ("disease:MONDO_0000001", "target:ENSG2"),
                     ("target:ENSG1", "drug:CHEMBL1"), ("target:ENSG1", "drug:CHEMBL2")}
    # nothing connects the second target to a drug, a drug to the disease, or two drugs to each other
    assert ("target:ENSG2", "drug:CHEMBL1") not in pairs
    assert ("disease:MONDO_0000001", "drug:CHEMBL1") not in pairs
    for e in g["edges"]:
        assert e["relationship_type"] and e["provider"] == "opentargets" and e["evidence_basis"]
        assert e["source"] in nodes and e["target"] in nodes
    assoc = next(e for e in g["edges"] if e["target"] == "target:ENSG1" and e["source"].startswith("disease"))
    assert assoc["score"] == 0.8 and assoc["datatype_scores"]["genetic_association"] == 0.7
    drug = next(e for e in g["edges"] if e["target"] == "drug:CHEMBL1")
    assert drug["max_clinical_stage"] == "PHASE_3"
    assert drug["mechanisms_of_action"][0]["mechanism_of_action"] == "AAA inhibitor"


def test_graph_drug_failure_keeps_disease_and_targets(monkeypatch):
    c = make_client(monkeypatch, ot_fail=("query TargetDrugs(",))
    g = c.get(BASE + "/graph", params={"disease": "MONDO_0000001"}).json()
    assert g["available"] is True
    assert g["drug_section"]["available"] is False and g["drug_section"]["error"]
    assert not any(n["type"] == "drug" for n in g["nodes"])
    assert len(g["edges"]) == 2


def test_graph_disease_failure_is_unavailable(monkeypatch):
    g = make_client(monkeypatch, ot_fail=("query Disease(",)).get(BASE + "/graph",
                                                                  params={"disease": "MONDO_0000001"}).json()
    assert g["available"] is False and g["error"] and g["nodes"] == [] and g["edges"] == []


def test_trials_shape(monkeypatch):
    body = make_client(monkeypatch).get(BASE + "/trials", params={"condition": "asthma"}).json()
    assert body["available"] is True
    assert body["total_count"] == 2 and body["returned"] == 1
    s = body["studies"][0]
    assert s["nct_id"] == "NCT00000001" and s["overall_status"] == "COMPLETED"
    assert s["has_posted_results"] is False
    assert body["provenance"]["provider"] == "clinicaltrials" and body["provenance"]["retrieved_at"]
    assert "not outcomes" in body["note"]


def test_trials_requires_a_query(monkeypatch):
    assert make_client(monkeypatch).get(BASE + "/trials").status_code == 422


def test_trials_failure_unavailable(monkeypatch):
    body = make_client(monkeypatch, ct=lambda req: httpx.Response(500)).get(
        BASE + "/trials", params={"condition": "x"}).json()
    assert body["available"] is False and body["error"] and "studies" not in body


@live
def test_live_disease_search_and_graph():
    discovery_api._CLIENTS = None
    app = FastAPI()
    app.include_router(discovery_api.router, prefix=BASE)
    c = TestClient(app)
    hits = c.get(BASE + "/diseases/search", params={"q": "rheumatoid arthritis"}).json()
    assert hits["available"] and hits["results"]
    g = c.get(BASE + "/graph", params={"disease": hits["results"][0]["id"]}).json()
    assert g["available"] and g["edges"]
