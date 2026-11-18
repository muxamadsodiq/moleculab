"""Drug intelligence tests (offline by default; MOLECULAB_LIVE=1 adds live DailyMed checks).

They pin the regulatory boundary: ingredient, product and regulatory record stay separate, a ChEMBL
max_phase of 4 never becomes a registration, and Uzbekistan access is reported honestly.
"""
import os

import httpx
import pytest

from app import drugs, drugs_api
from app.clients.chembl import ChEMBLClient
from app.clients.dailymed import DailyMedClient
from app.clients.pubchem import PubChemClient

live = pytest.mark.skipif(os.getenv("MOLECULAB_LIVE") != "1", reason="set MOLECULAB_LIVE=1 for live API tests")

ASPIRIN_KEY = "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"


def mocked(cls, handler, **kw):
    return cls(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0, max_retries=0, **kw)


def chembl_handler(max_phase="4.0", fail=False):
    def handler(req):
        if fail:
            return httpx.Response(503)
        return httpx.Response(200, json={"molecules": [{
            "molecule_chembl_id": "CHEMBL25", "pref_name": "ASPIRIN", "max_phase": max_phase,
            "atc_classifications": ["A01AD05", "B01AC06"],
            "molecule_structures": {"canonical_smiles": "CC(=O)Oc1ccccc1C(=O)O", "standard_inchi_key": ASPIRIN_KEY},
            "molecule_properties": {"full_molformula": "C9H8O4"}}]})
    return handler


def pubchem_handler(fail=False):
    def handler(req):
        if fail:
            return httpx.Response(503)
        return httpx.Response(200, json={"PropertyTable": {"Properties": [{
            "CID": 2244, "MolecularFormula": "C9H8O4", "MolecularWeight": "180.16", "InChIKey": ASPIRIN_KEY}]}})
    return handler


def dailymed_handler(fail=False, empty=False):
    def handler(req):
        if fail:
            return httpx.Response(503)
        path = req.url.path
        if path.endswith("/spls.json"):
            rows = [] if empty else [{"setid": "abc-1", "spl_version": 2, "published_date": "x",
                                      "title": "ASPIRIN TABLET [ACME LABS]"}]
            return httpx.Response(200, json={"data": rows, "metadata": {"total_elements": len(rows),
                                                                         "db_published_date": "Nov 1, 2026"}})
        return httpx.Response(200, json={"data": {"setid": "abc-1", "title": "ASPIRIN TABLET [ACME LABS]",
                                                  "products": [{"product_name": "Aspirin", "product_code": "1-2",
                                                                "active_ingredients": [{"name": "ASPIRIN", "strength": "81 mg"}],
                                                                "packaging": []}]},
                                         "metadata": {}})
    return handler


def providers(max_phase="4.0", chembl_fail=False, pubchem_fail=False, dm_fail=False, dm_empty=False):
    return {"chembl": mocked(ChEMBLClient, chembl_handler(max_phase, chembl_fail)),
            "pubchem": mocked(PubChemClient, pubchem_handler(pubchem_fail)),
            "dailymed": mocked(DailyMedClient, dailymed_handler(dm_fail, dm_empty))}


# ----------------------------------------------------------------- client

def test_dailymed_client_normalises_and_never_raises():
    c = mocked(DailyMedClient, dailymed_handler())
    res = c.search_spls("aspirin")
    assert res.ok and res.data["spls"][0]["labeler"] == "ACME LABS"
    assert res.source_release == "Nov 1, 2026"
    assert c.spl("abc-1").data["products"][0]["active_ingredients"][0]["strength"] == "81 mg"
    bad = mocked(DailyMedClient, dailymed_handler(fail=True)).search_spls("x")
    assert bad.ok is False and bad.error


# ----------------------------------------------------------------- separation

def test_ingredient_and_product_are_separate_records():
    p = drugs.build_drug_profile("aspirin", providers())
    ing, prods = p["active_ingredient"], p["pharmaceutical_products"]
    assert ing["chembl_id"] == "CHEMBL25" and ing["pubchem_cid"] == 2244
    assert prods and prods[0]["source_record_id"] == "abc-1"
    assert "brand_name" not in ing and "chembl_id" not in prods[0]
    assert "jurisdiction" not in ing and "jurisdiction" not in prods[0]
    assert set(p["regulatory_records"]) == {"US", "EU", "UZ"}


# ----------------------------------------------------------------- the core rule

def test_chembl_phase_4_is_not_registration_anywhere():
    p = drugs.build_drug_profile("aspirin", providers(max_phase="4.0"))
    assert p["active_ingredient"]["chembl_max_phase"] == 4.0
    statuses = p["regulatory_status_by_jurisdiction"]
    assert statuses and all(s in drugs.STATUSES for s in statuses.values())
    assert "registered" not in statuses.values()
    assert statuses["UZ"] == "not_indexed"
    assert "approval" in p["active_ingredient"]["chembl_max_phase_note"]


def test_registered_requires_an_authoritative_source():
    with pytest.raises(ValueError):
        drugs.RegulatoryRecord("UZ", "x", "registered", None, "now")
    with pytest.raises(ValueError):
        drugs.RegulatoryRecord("UZ", "x", "approved", None, "now")
    ok = drugs.RegulatoryRecord("XX", "x", "registered", "url", "now", evidence={"authoritative_source": True})
    assert ok.status == "registered"


def test_no_products_does_not_become_not_registered():
    p = drugs.build_drug_profile("aspirin", providers(dm_empty=True))
    assert p["pharmaceutical_products"] == []
    assert p["regulatory_status_by_jurisdiction"]["US"] == "not_indexed"


# ----------------------------------------------------------------- Uzbekistan

def test_uzbekistan_registry_reports_actual_access():
    uz = drugs.uzbekistan_registry_status()
    assert uz["available"] is False and uz["reason"]
    assert uz["checked"] and uz["required"]
    assert uz["status"] == "not_indexed"
    assert "registration_number" not in uz and "records" not in uz


# ----------------------------------------------------------------- missing and failures

def test_missing_fields_are_reported_not_omitted():
    prov = providers(pubchem_fail=True)
    p = drugs.build_drug_profile("aspirin", prov)
    missing = p["active_ingredient"]["missing_fields"]
    assert "pubchem_cid" in missing and "molecular_weight" in missing
    assert "pubchem_cid" in p["active_ingredient"]
    assert p["pharmaceutical_products"][0]["missing_fields"]
    assert "dosage_form" in p["pharmaceutical_products"][0]["missing_fields"]


def test_provider_failure_surfaces_as_unavailable():
    p = drugs.build_drug_profile("aspirin", providers(chembl_fail=True, dm_fail=True))
    assert {"chembl", "dailymed"} <= set(p["unavailable_providers"])
    down = [x for x in p["provenance"] if not x["available"]]
    assert down and all(x["error"] for x in down)
    assert p["active_ingredient"]["pubchem_cid"] == 2244  # still resolved from the working provider
    assert "registered" not in p["regulatory_status_by_jurisdiction"].values()


def test_everything_down_returns_honest_empty_profile():
    p = drugs.build_drug_profile("aspirin", providers(True and "4.0", True, True, True))
    assert p["active_ingredient"] is None
    assert set(p["unavailable_providers"]) == {"chembl", "pubchem", "dailymed"}


def test_compare_marks_missing_values():
    c = drugs.compare_profiles(["aspirin", "other"], providers(pubchem_fail=True))
    row = next(r for r in c["rows"] if r["field"] == "PubChem CID")
    assert [v["status"] for v in row["values"]] == ["missing", "missing"]
    uz = next(r for r in c["rows"] if r["field"] == "Regulatory status: UZ")
    assert uz["values"][0]["value"] == "not_indexed"


# ----------------------------------------------------------------- API

@pytest.fixture()
def drug_client(client, monkeypatch):
    if not any(getattr(r, "path", "") == "/api/v1/drugs/registry/uzbekistan" for r in client.app.routes):
        client.app.include_router(drugs_api.router, prefix="/api/v1/drugs")
    monkeypatch.setattr(drugs, "get_providers", lambda: providers())
    return client


def test_api_endpoints(drug_client):
    r = drug_client.get("/api/v1/drugs/registry/uzbekistan").json()
    assert r["available"] is False and r["reason"]
    prof = drug_client.get("/api/v1/drugs/profile/aspirin").json()
    assert prof["provenance"] and "registered" not in prof["regulatory_status_by_jurisdiction"].values()
    assert drug_client.get("/api/v1/drugs/ingredient/aspirin").json()["ingredient"]["chembl_id"] == "CHEMBL25"
    s = drug_client.get("/api/v1/drugs/search", params={"q": "aspirin"}).json()
    assert s["ingredient_candidates"] and s["product_candidates"]
    cmp_ = drug_client.get("/api/v1/drugs/compare", params={"names": "aspirin,other"}).json()
    assert len(cmp_["profiles"]) == 2
    assert drug_client.get("/api/v1/drugs/compare", params={"names": "only"}).status_code == 422


# ----------------------------------------------------------------- live

@live
def test_live_dailymed_shape():
    res = DailyMedClient().search_spls("aspirin", pagesize=2)
    assert res.ok and res.data["spls"] and res.data["spls"][0]["setid"]
    det = DailyMedClient().spl(res.data["spls"][0]["setid"])
    assert det.ok and "products" in det.data
