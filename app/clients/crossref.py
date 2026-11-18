"""Crossref client: resolves DOIs to bibliographic metadata."""
from urllib.parse import quote

from app.clients.base import SourceClient, SourceResult


def normalise_work(m: dict) -> dict:
    year = None
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (m.get(key) or {}).get("date-parts") or [[None]]
        if parts[0] and parts[0][0]:
            year = parts[0][0]
            break
    authors = [f"{a.get('family', '')} {a.get('given', '')}".strip() for a in m.get("author", [])]
    return {
        "doi": (m.get("DOI") or "").lower() or None,
        "title": (m.get("title") or [""])[0],
        "authors": [a for a in authors if a],
        "year": year,
        "journal": (m.get("container-title") or [""])[0],
        "language": m.get("language"),
        "url": m.get("URL"),
        "type": m.get("type"),
    }


class CrossrefClient(SourceClient):
    name = "crossref"
    base_url = "https://api.crossref.org"

    def get_doi(self, doi: str) -> SourceResult:
        res = self._get(f"/works/{quote(doi.strip(), safe='/')}")
        if res.ok:
            try:
                res.data = normalise_work(res.data["message"])
            except (KeyError, TypeError):
                res.ok, res.data, res.error = False, None, "unexpected Crossref response"
        return res
