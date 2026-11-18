"""NCBI E-utilities client for PubMed search, metadata and abstracts."""
import xml.etree.ElementTree as ET

from app.clients.base import SourceClient, SourceResult
from app.config import settings


def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_pubmed_xml(xml_text: str) -> list[dict]:
    records = []
    root = ET.fromstring(xml_text)
    for art in root.iter("PubmedArticle"):
        cit = art.find("MedlineCitation")
        pmid = _text(cit.find("PMID"))
        article = cit.find("Article")
        authors = []
        for a in article.findall("AuthorList/Author"):
            last, init = _text(a.find("LastName")), _text(a.find("Initials"))
            name = f"{last} {init}".strip() or _text(a.find("CollectiveName"))
            if name:
                authors.append(name)
        year = _text(article.find("Journal/JournalIssue/PubDate/Year"))
        if not year:
            year = _text(article.find("Journal/JournalIssue/PubDate/MedlineDate"))[:4]
        doi = ""
        for aid in art.findall("PubmedData/ArticleIdList/ArticleId"):
            if aid.get("IdType") == "doi":
                doi = _text(aid)
        abstract = " ".join(
            (f"{p.get('Label')}: " if p.get("Label") else "") + _text(p)
            for p in article.findall("Abstract/AbstractText")
        )
        records.append({
            "pmid": pmid,
            "title": _text(article.find("ArticleTitle")),
            "authors": authors,
            "year": int(year) if year.isdigit() else None,
            "journal": _text(article.find("Journal/Title")),
            "doi": doi or None,
            "language": _text(article.find("Language")) or None,
            "abstract": abstract,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "publication_types": [_text(t) for t in article.findall("PublicationTypeList/PublicationType")],
        })
    return records


class PubMedClient(SourceClient):
    name = "pubmed"
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def _params(self, **kw) -> dict:
        p = {"tool": "moleculab", **kw}
        if settings.contact_email:
            p["email"] = settings.contact_email
        if settings.ncbi_api_key:
            p["api_key"] = settings.ncbi_api_key
        return p

    def search(self, term: str, retmax: int = 10) -> SourceResult:
        res = self._get("/esearch.fcgi", self._params(db="pubmed", term=term, retmode="json",
                                                       retmax=retmax))
        if res.ok:
            try:
                r = res.data["esearchresult"]
                res.data = {"count": int(r["count"]), "pmids": r["idlist"]}
            except (KeyError, TypeError, ValueError):
                res.ok, res.data, res.error = False, None, "unexpected esearch response"
        return res

    def fetch(self, pmids: list[str]) -> SourceResult:
        if not pmids:
            return SourceResult(self.name, True, data=[])
        res = self._get("/efetch.fcgi", self._params(db="pubmed", id=",".join(pmids), retmode="xml"))
        if res.ok:
            try:
                res.data = parse_pubmed_xml(res.data)
            except ET.ParseError as exc:
                res.ok, res.data, res.error = False, None, f"invalid PubMed XML: {exc}"
        return res
