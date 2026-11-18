"""Source-client tests. Offline by default (mocked transports); set MOLECULAB_LIVE=1 to
also hit the real PubChem, PubMed, Crossref and GBIF APIs."""
import os

import httpx
import pytest

from app.clients.crossref import CrossrefClient
from app.clients.gbif import GBIFClient
from app.clients.pubchem import PubChemClient
from app.clients.pubmed import PubMedClient, parse_pubmed_xml

live = pytest.mark.skipif(os.getenv("MOLECULAB_LIVE") != "1", reason="set MOLECULAB_LIVE=1 for live API tests")


def mocked(cls, handler, **kw):
    return cls(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0, **kw)


def test_pubchem_parses_new_and_old_smiles_fields():
    def handler(req):
        return httpx.Response(200, json={"PropertyTable": {"Properties": [
            {"CID": 1, "MolecularFormula": "C1", "MolecularWeight": "12.0", "InChIKey": "K", "ConnectivitySMILES": "C"},
            {"CID": 2, "CanonicalSMILES": "CC"}]}})
    res = mocked(PubChemClient, handler).search_name("x")
    assert res.ok and res.data[0]["smiles"] == "C" and res.data[0]["molecular_weight"] == 12.0
    assert res.data[1]["smiles"] == "CC" and res.request_url.startswith("https://pubchem")


def test_pubchem_not_found():
    res = mocked(PubChemClient, lambda r: httpx.Response(404, json={})).search_name("nonexistent")
    assert not res.ok and res.error == "not found"


def test_retries_then_fails_on_server_errors():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503)
    res = mocked(PubChemClient, handler, max_retries=2).search_name("x")
    assert not res.ok and res.error == "HTTP 503" and len(calls) == 3


def test_timeout_is_reported_not_raised():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)
    res = mocked(CrossrefClient, handler, max_retries=1).get_doi("10.1/x")
    assert not res.ok and "ReadTimeout" in res.error


def test_recovers_after_transient_error():
    seq = [httpx.Response(500), httpx.Response(200, json={"message": {"DOI": "10.1/X", "title": ["T"],
                                                                      "issued": {"date-parts": [[2020]]}}})]
    res = mocked(CrossrefClient, lambda r: seq.pop(0), max_retries=2).get_doi("10.1/x")
    assert res.ok and res.data == {"doi": "10.1/x", "title": "T", "authors": [], "year": 2020, "journal": "",
                                   "language": None, "url": None, "type": None}


def test_malformed_json_is_reported():
    res = mocked(PubChemClient, lambda r: httpx.Response(200, content=b"{oops",
                                                         headers={"content-type": "application/json"})).search_name("x")
    assert not res.ok and "invalid response" in res.error


def test_unexpected_shape_is_reported():
    res = mocked(PubChemClient, lambda r: httpx.Response(200, json={"Fault": {}})).search_name("x")
    assert not res.ok and "unexpected" in res.error


def test_pubmed_xml_parsing():
    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>
      <Journal><Title>J Test</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
      <ArticleTitle>A <i>Ziziphora</i> study</ArticleTitle>
      <Abstract><AbstractText Label="RESULTS">Pulegone was major.</AbstractText></Abstract>
      <AuthorList><Author><LastName>Doe</LastName><Initials>J</Initials></Author></AuthorList>
      <Language>eng</Language><PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
      </Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/abc</ArticleId></ArticleIdList>
      </PubmedData></PubmedArticle></PubmedArticleSet>"""
    [rec] = parse_pubmed_xml(xml)
    assert rec["pmid"] == "123" and rec["title"] == "A Ziziphora study" and rec["year"] == 2024
    assert rec["abstract"] == "RESULTS: Pulegone was major." and rec["doi"] == "10.1/abc" and rec["authors"] == ["Doe J"]


def test_api_reports_source_failure(client, monkeypatch):
    from app.clients.base import SourceResult
    monkeypatch.setattr(PubChemClient, "search_name",
                        lambda self, name: SourceResult("pubchem", False, error="ConnectError: offline"))
    r = client.get("/api/sources/pubchem", params={"name": "quercetin"})
    assert r.status_code == 502 and r.json()["detail"]["error"] == "ConnectError: offline"
    # the rest of the application keeps working while the source is down
    assert client.get("/api/search", params={"q": "quercetin"}).status_code == 200


@live
def test_live_pubchem():
    res = PubChemClient().search_name("quercetin")
    assert res.ok and res.data[0]["pubchem_cid"] == 5280343 and res.data[0]["inchikey"] == "REFJWTPEDVJJIY-UHFFFAOYSA-N"


@live
def test_live_pubmed():
    client = PubMedClient()
    recs = client.fetch(["37505209"])
    assert recs.ok and "Ziziphora clinopodioides" in recs.data[0]["title"]
    found = client.search('"Ziziphora clinopodioides"[tiab] AND pulegone[tiab]', 3)
    assert found.ok and found.data["count"] > 0


@live
def test_live_crossref():
    res = CrossrefClient().get_doi("10.3390/plants15182781")
    assert res.ok and "Ferula" in res.data["title"]


@live
def test_live_gbif():
    res = GBIFClient().match("Ziziphora clinopodioides")
    assert res.ok and res.data["family"] == "Lamiaceae"


def test_rate_limit_is_retried_then_reported():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "1"})
    res = mocked(PubMedClient, handler, max_retries=2).search("x")
    assert not res.ok and res.error == "rate limited (HTTP 429)" and res.status_code == 429 and len(calls) == 3


def test_success_records_status_and_timing():
    res = mocked(GBIFClient, lambda r: httpx.Response(200, json={"matchType": "EXACT", "usageKey": 1,
                                                                 "family": "F"})).match("x y")
    assert res.ok and res.status_code == 200 and res.elapsed_ms is not None
    assert res.provenance()["http_status"] == 200


def test_gbif_no_match_is_reported():
    res = mocked(GBIFClient, lambda r: httpx.Response(200, json={"matchType": "NONE"})).match("Nonexistus plantus")
    assert not res.ok and res.error == "no GBIF match"


def test_gbif_higher_rank_fallback_is_not_a_species_match():
    # found by the live check: a nonsense name was "matched" to the kingdom Plantae
    res = mocked(GBIFClient, lambda r: httpx.Response(200, json={"matchType": "HIGHERRANK", "usageKey": 6,
                                                                 "scientificName": "Plantae", "rank": "KINGDOM"})
                 ).match("Zzqx nonexistentia")
    assert not res.ok and "no species-level GBIF match" in res.error
