"""MOLECULAB 1.0 provider tests: ChEMBL, Open Targets, ClinicalTrials.gov, cache and rate limiting.

Offline by default (mocked transports). MOLECULAB_LIVE=1 adds live checks against the real services.

These tests pin the scientific guarantees of the provider layer, not just its plumbing: activity rows keep
their experimental context, registry records are not read as outcomes, development stage is not read as
regulatory approval, and a failed page is never presented as a complete download.
"""
import json
import os
import time

import httpx
import pytest

from app.clients.base import GraphQLClient, SourceClient
from app.clients.chembl import ChEMBLClient
from app.clients.clinicaltrials import ClinicalTrialsClient
from app.clients.http_cache import RateLimiter, ResponseCache, payload_hash
from app.clients.opentargets import OpenTargetsClient

live = pytest.mark.skipif(os.getenv("MOLECULAB_LIVE") != "1", reason="set MOLECULAB_LIVE=1 for live API tests")


def mocked(cls, handler, **kw):
    """Mocked transport. An injected client also disables response caching, so a cached real
    response can never satisfy a mocked test."""
    return cls(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0, **kw)


# --------------------------------------------------------------------------- ChEMBL

def test_chembl_molecule_separates_provider_properties_from_moleculab_values():
    def handler(req):
        return httpx.Response(200, json={
            "molecule_chembl_id": "CHEMBL25", "pref_name": "ASPIRIN", "molecule_type": "Small molecule",
            "max_phase": "4.0", "first_approval": 1950, "withdrawn_flag": False,
            "molecule_structures": {"canonical_smiles": "CC(=O)Oc1ccccc1C(=O)O",
                                    "standard_inchi_key": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"},
            "molecule_properties": {"full_mwt": "180.16", "alogp": "1.31", "psa": "63.60"}})
    res = mocked(ChEMBLClient, handler).molecule("CHEMBL25")
    assert res.ok
    # ChEMBL's own computed properties stay under chembl_* names so they are never confused with
    # MOLECULAB's locally recomputed RDKit descriptors.
    assert res.data["chembl_mw"] == 180.16 and res.data["chembl_alogp"] == 1.31
    assert "molecular_weight" not in res.data and "logp" not in res.data
    assert res.provider_record_id == "CHEMBL25"
    assert res.provenance()["raw_payload_hash"]


def test_chembl_max_phase_is_not_called_approval():
    """max_phase 4 must not be surfaced under a name implying current regulatory approval."""
    def handler(req):
        return httpx.Response(200, json={"molecule_chembl_id": "C1", "max_phase": "4.0",
                                         "molecule_structures": {}, "molecule_properties": {}})
    data = mocked(ChEMBLClient, handler).molecule("C1").data
    assert data["max_phase"] == "4.0"
    assert not any("approv" in k for k in data if k != "first_approval")


def test_chembl_target_resolution_keeps_target_type():
    """Resolution must expose target_type so a PROTEIN COMPLEX or FAMILY is never silently accepted
    in place of the SINGLE PROTEIN whose measurements were requested."""
    def handler(req):
        return httpx.Response(200, json={"targets": [
            {"target_chembl_id": "CHEMBL2835", "pref_name": "Tyrosine-protein kinase JAK1",
             "target_type": "SINGLE PROTEIN", "organism": "Homo sapiens",
             "target_components": [{"accession": "P23458", "target_component_synonyms": [
                 {"syn_type": "GENE_SYMBOL", "component_synonym": "JAK1"}]}]},
            {"target_chembl_id": "CHEMBL2363062", "pref_name": "Janus Kinase (JAK)",
             "target_type": "PROTEIN FAMILY", "organism": "Homo sapiens", "target_components": []}]})
    rows = mocked(ChEMBLClient, handler).resolve_target("JAK1").data
    assert [r["target_type"] for r in rows] == ["SINGLE PROTEIN", "PROTEIN FAMILY"]
    assert rows[0]["uniprot_accessions"] == ["P23458"] and rows[0]["gene_symbols"] == ["JAK1"]


def test_chembl_activity_preserves_experimental_context():
    def handler(req):
        return httpx.Response(200, json={"page_meta": {"next": None}, "activities": [{
            "activity_id": 1, "assay_chembl_id": "CHEMBL857179", "assay_type": "B",
            "target_chembl_id": "CHEMBL2835", "target_organism": "Homo sapiens",
            "molecule_chembl_id": "CHEMBL538798", "canonical_smiles": "CCO",
            "standard_type": "IC50", "standard_relation": "=", "standard_value": "39810.72",
            "standard_units": "nM", "pchembl_value": "4.40", "document_chembl_id": "CHEMBL1148466"}]})
    rows = mocked(ChEMBLClient, handler).activities("CHEMBL2835", "IC50").data
    row = rows[0]
    for field in ("standard_type", "standard_relation", "standard_value", "standard_units",
                  "assay_chembl_id", "target_chembl_id", "target_organism", "assay_type",
                  "document_chembl_id"):
        assert row[field] is not None, field
    # The client must not convert or collapse the measurement.
    assert row["standard_value"] == "39810.72" and row["standard_units"] == "nM"


def test_chembl_partial_download_is_reported_as_partial():
    """A mid-page failure must never look like a complete dataset."""
    pages = {"count": 0}

    def handler(req):
        pages["count"] += 1
        if pages["count"] == 1:
            return httpx.Response(200, json={
                "page_meta": {"next": "/chembl/api/data/activity.json?offset=1"},
                "activities": [{"activity_id": 1, "standard_type": "IC50"}]})
        return httpx.Response(500)
    res = mocked(ChEMBLClient, handler, max_retries=0).activities("T", "IC50")
    assert res.ok is False
    assert "partial download" in res.error
    assert len(res.data) == 1  # the rows that were retrieved are still returned, clearly labelled


def test_chembl_activity_requires_single_endpoint():
    """One endpoint per call: the signature cannot express a mixture of IC50 and Ki."""
    import inspect
    sig = inspect.signature(ChEMBLClient.activities)
    assert sig.parameters["standard_type"].default is inspect.Parameter.empty


# ---------------------------------------------------------------------- Open Targets

OT_DISEASE = {"data": {"disease": {
    "id": "MONDO_0008383", "name": "rheumatoid arthritis", "description": "d", "dbXRefs": [],
    "synonyms": [{"relation": "hasExactSynonym", "terms": ["RA"]}],
    "therapeuticAreas": [{"id": "EFO_0000540", "name": "immune system disease"}],
    "associatedTargets": {"count": 8420, "rows": [
        {"score": 0.7638, "datatypeScores": [{"id": "genetic_association", "score": 0.973},
                                             {"id": "clinical", "score": 0.991}],
         "target": {"id": "ENSG00000105397", "approvedSymbol": "TYK2", "approvedName": "tyk2",
                    "biotype": "protein_coding",
                    "proteinIds": [{"id": "P29597", "source": "uniprot_swissprot"}],
                    "targetClass": [{"id": 1, "label": "Kinase", "level": 1}]}}]}}}}


def test_opentargets_keeps_datatype_scores_separate():
    """An overall association score must never be the only number kept: the per-datatype breakdown is
    what makes a target's association inspectable, and none of it is a probability of drug success."""
    res = mocked(OpenTargetsClient, lambda r: httpx.Response(200, json=OT_DISEASE)).disease("MONDO_0008383")
    row = res.data["associated_targets"][0]
    assert row["overall_score"] == 0.7638
    assert row["datatype_scores"] == {"genetic_association": 0.973, "clinical": 0.991}
    assert row["uniprot_accessions"] == ["P29597"]
    assert res.data["associated_target_count"] == 8420


def test_opentargets_never_sends_order_by_score():
    """Passing orderByScore to the live API destroys the default descending order (verified
    2026-11-17), so the queries must not contain it."""
    from app.clients import opentargets as ot
    assert "orderByScore" not in ot.DISEASE_Q
    assert "orderByScore" not in ot.TARGET_DISEASES_Q


def test_opentargets_missing_disease_explains_id_resolution():
    res = mocked(OpenTargetsClient,
                 lambda r: httpx.Response(200, json={"data": {"disease": None}})).disease("EFO_0000685")
    assert not res.ok and "search()" in res.error


def test_opentargets_graphql_errors_become_failures():
    body = {"errors": [{"message": "Cannot query field 'nope' on type 'Target'."}]}
    res = mocked(OpenTargetsClient, lambda r: httpx.Response(200, json=body)).disease("X")
    assert not res.ok and "Cannot query field" in res.error


def test_opentargets_drug_candidate_stage_is_not_approval_and_warnings_are_not_safety():
    body = {"data": {"target": {"id": "ENSG1", "approvedSymbol": "JAK1",
        "drugAndClinicalCandidates": {"count": 1, "rows": [{
            "id": "r1", "maxClinicalStage": "APPROVAL",
            "drug": {"id": "CHEMBL1", "name": "RUXOLITINIB", "drugType": "Small molecule",
                     "maximumClinicalStage": "APPROVAL",
                     "drugWarnings": [],
                     "mechanismsOfAction": {"rows": [{"mechanismOfAction": "JAK1 inhibitor",
                                                      "actionType": "INHIBITOR"}]}},
            # A null disease mapping must not drop the row.
            "diseases": [{"diseaseFromSource": "myelofibrosis", "disease": None}]}]}}}}
    res = mocked(OpenTargetsClient, lambda r: httpx.Response(200, json=body)).target_drug_candidates("ENSG1")
    row = res.data["rows"][0]
    assert row["maximum_clinical_stage"] == "APPROVAL"
    # No key may assert approval or safety as a fact.
    assert not any(k in row for k in ("is_approved", "approved", "is_safe", "safety"))
    assert row["drug_warnings"] == []  # absent warnings are not a safety claim
    assert row["indications"][0]["disease_from_source"] == "myelofibrosis"
    assert row["indications"][0]["disease_id"] is None


def test_opentargets_clinical_evidence_keeps_stage_as_development_not_efficacy():
    body = {"data": {"disease": {"id": "MONDO_0008383", "name": "ra", "evidences": {"count": 960, "rows": [
        {"id": "e1", "score": 1, "datasourceId": "clinical_precedence", "datatypeId": "clinical",
         "literature": None, "publicationYear": None, "studyId": None,
         "clinicalStage": "APPROVAL", "confidence": None, "drugFromSource": "tofacitinib",
         "diseaseFromSource": "RA", "target": {"id": "E1", "approvedSymbol": "JAK1"},
         "disease": {"id": "MONDO_0008383", "name": "ra"}}]}}}}
    res = mocked(OpenTargetsClient,
                 lambda r: httpx.Response(200, json=body)).target_disease_evidence("MONDO_0008383", "E1")
    row = res.data["rows"][0]
    assert row["clinical_stage"] == "APPROVAL" and row["drug_from_source"] == "tofacitinib"
    assert row["datasource_id"] == "clinical_precedence"
    assert not any("efficac" in k or "works" in k for k in row)


# ----------------------------------------------------------------- ClinicalTrials.gov

CT_STUDY = {
    "hasResults": True,
    "protocolSection": {
        "identificationModule": {"nctId": "NCT00847613", "briefTitle": "A 2-Year Phase 3 Study"},
        "statusModule": {"overallStatus": "COMPLETED", "whyStopped": None,
                         "startDateStruct": {"date": "2009-02"},
                         "completionDateStruct": {"date": "2012-05"}},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Pfizer"}},
        "conditionsModule": {"conditions": ["Rheumatoid Arthritis"]},
        "designModule": {"studyType": "INTERVENTIONAL", "phases": ["PHASE3"],
                         "enrollmentInfo": {"count": 797, "type": "ACTUAL"}},
        "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "CP-690,550"}]},
        "outcomesModule": {"primaryOutcomes": [{"measure": "ACR20 response", "timeFrame": "6 months"}]},
        "eligibilityModule": {"eligibilityCriteria": "Active RA", "minimumAge": "18 Years"},
        "contactsLocationsModule": {"locations": [{"facility": "F", "country": "Australia"}]},
    },
}


def test_clinicaltrials_registration_is_not_evidence_of_efficacy():
    res = mocked(ClinicalTrialsClient, lambda r: httpx.Response(200, json=CT_STUDY)).study("NCT00847613")
    s = res.data
    assert s["overall_status"] == "COMPLETED"
    # `has_posted_results` says a results section exists; nothing claims success.
    assert s["has_posted_results"] is True
    assert not any(k in s for k in ("successful", "efficacy", "works", "effective"))
    # Declared endpoints are outcome *measures*, not outcomes.
    assert s["primary_outcomes"][0]["measure"] == "ACR20 response"
    assert s["enrollment_type"] == "ACTUAL" and s["enrollment_count"] == 797
    assert s["source_url"] == "https://clinicaltrials.gov/study/NCT00847613"


def test_clinicaltrials_rejects_unknown_phase_and_status():
    c = mocked(ClinicalTrialsClient, lambda r: httpx.Response(200, json={"studies": []}))
    assert "unknown phase" in c.search(condition="ra", phases=["PHASE9"]).error
    assert "unknown status" in c.search(condition="ra", statuses=["DONE"]).error
    assert "required" in c.search().error


def test_clinicaltrials_paginates_and_reports_total():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"totalCount": 2, "studies": [CT_STUDY],
                                             "nextPageToken": "tok"})
        return httpx.Response(200, json={"totalCount": 2, "studies": [CT_STUDY]})
    res = mocked(ClinicalTrialsClient, handler).search(condition="ra")
    assert res.ok and res.data["total_count"] == 2 and res.data["returned"] == 2 and calls["n"] == 2


def test_clinicaltrials_empty_result_is_zero_not_an_absence_claim():
    res = mocked(ClinicalTrialsClient,
                 lambda r: httpx.Response(200, json={"totalCount": 0, "studies": []})).search(condition="zz")
    assert res.ok and res.data["total_count"] == 0 and res.data["studies"] == []


# ------------------------------------------------------------- cache / rate limiting

def test_cache_round_trip_preserves_original_retrieval_time(tmp_path):
    cache = ResponseCache(tmp_path, ttl_seconds=600)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"molecule_chembl_id": "C1", "molecule_structures": {},
                                         "molecule_properties": {}})

    class Cached(ChEMBLClient):
        pass

    c = Cached(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0,
               cache=cache, use_cache=True)
    first = c.molecule("C1")
    second = c.molecule("C1")
    assert calls["n"] == 1  # second call served from disk
    assert second.from_cache and not first.from_cache
    # Provenance must not claim a fresh fetch for a cached value.
    assert second.retrieved_at == first.retrieved_at
    assert second.provenance()["from_cache"] is True


def test_cache_keys_distinguish_different_requests(tmp_path):
    cache = ResponseCache(tmp_path, ttl_seconds=600)
    cache.set("GET", "http://x/a", {"data": 1}, params={"q": "one"})
    assert cache.get("GET", "http://x/a", params={"q": "one"})["data"] == 1
    assert cache.get("GET", "http://x/a", params={"q": "two"}) is None
    assert cache.get("POST", "http://x/a", params={"q": "one"}) is None


def test_expired_cache_entry_is_a_miss(tmp_path):
    cache = ResponseCache(tmp_path, ttl_seconds=0.05)
    cache.set("GET", "http://x", {"data": 1})
    time.sleep(0.1)
    assert cache.get("GET", "http://x") is None


def test_corrupt_cache_file_is_a_miss_not_an_error(tmp_path):
    cache = ResponseCache(tmp_path, ttl_seconds=600)
    cache.set("GET", "http://x", {"data": 1})
    for path in tmp_path.rglob("*.json"):
        path.write_text("{not json", encoding="utf-8")
    assert cache.get("GET", "http://x") is None


def test_injected_client_disables_caching():
    """Tests inject transports; a cached real response must never satisfy a mocked test."""
    c = ChEMBLClient(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    assert c._cache is None
    assert ChEMBLClient.cacheable is True  # but caching is still on for real use


def test_legacy_clients_do_not_cache_by_default():
    assert SourceClient.cacheable is False


def test_rate_limiter_spaces_requests():
    limiter = RateLimiter(0.05)
    start = time.monotonic()
    for _ in range(3):
        limiter.wait()
    assert time.monotonic() - start >= 0.08


def test_payload_hash_is_stable_and_order_independent():
    assert payload_hash({"a": 1, "b": 2}) == payload_hash({"b": 2, "a": 1})
    assert payload_hash({"a": 1}) != payload_hash({"a": 2})


def test_graphql_client_rejects_non_dict_body():
    class G(GraphQLClient):
        name, base_url = "g", "http://example.invalid/graphql"
    res = mocked(G, lambda r: httpx.Response(200, text="plain"))._graphql("{x}")
    assert not res.ok and "unexpected" in res.error


# ------------------------------------------------------------------------- live

@live
def test_live_chembl_release_and_activity_volume():
    c = ChEMBLClient(use_cache=False)
    rel = c.release()
    assert rel.ok and rel.source_release and rel.source_release.startswith("ChEMBL")
    count = c.activity_count("CHEMBL2835", "IC50")
    assert count.ok and count.data > 1000  # JAK1 IC50 volume supports a trainable dataset


@live
def test_live_chembl_resolves_il6r_not_a_sodium_channel():
    """Guards the audit finding: plausible-looking ChEMBL ids must never be guessed."""
    rows = ChEMBLClient(use_cache=False).resolve_target("IL6R").data
    single = [r for r in rows if r["target_type"] == "SINGLE PROTEIN"]
    assert single and "Interleukin-6" in single[0]["pref_name"]
    assert single[0]["chembl_id"] != "CHEMBL4296"


@live
def test_live_opentargets_disease_targets_are_score_descending():
    data = OpenTargetsClient(use_cache=False).disease("MONDO_0008383", targets=5).data
    scores = [t["overall_score"] for t in data["associated_targets"]]
    assert scores == sorted(scores, reverse=True) and scores[0] > 0.5
    assert all(t["datatype_scores"] for t in data["associated_targets"])


@live
def test_live_opentargets_target_profile():
    data = OpenTargetsClient(use_cache=False).target("ENSG00000162434").data
    assert data["gene_symbol"] == "JAK1" and "P23458" in data["uniprot_accessions"]


@live
def test_live_clinicaltrials_search_and_study():
    c = ClinicalTrialsClient(use_cache=False)
    res = c.search(condition="rheumatoid arthritis", intervention="tofacitinib", max_records=10)
    assert res.ok and res.data["total_count"] > 0
    assert all(s["nct_id"].startswith("NCT") for s in res.data["studies"])
    one = c.study("NCT00847613")
    assert one.ok and one.data["lead_sponsor"] and one.data["phases"]
