"""NIH DailyMed SPL client (public API v2, https://dailymed.nlm.nih.gov/dailymed/services/v2).

DailyMed publishes the structured product labels (SPL) of products marketed in the UNITED STATES.
A DailyMed record therefore says "this label exists in the US label repository". It is not a statement
about registration in any other country, and this client never implies one.

Endpoint shapes verified live: `/spls.json?drug_name=&pagesize=` returns {data: [{setid, title,
spl_version, published_date}], metadata: {...}}; `/spls/{setid}/packaging.json` returns {data: {products: [...]}}
(`/spls/{setid}.json` answers HTTP 415 and is not used).
"""
from __future__ import annotations

import re
from urllib.parse import quote

from app.clients.base import SourceClient, SourceResult

_BRACKET = re.compile(r"^(?P<product>.*?)\s*\[(?P<labeler>[^\]]+)\]\s*$")


def _split_title(title: str | None) -> dict:
    """Title looks like 'NAME (GENERIC) DOSAGE FORM [LABELER]'. Only the bracketed labeler is split off;
    nothing else is parsed out of free text, so no field is invented."""
    if not title:
        return {"title": None, "labeler": None}
    m = _BRACKET.match(title)
    return {"title": title, "labeler": m.group("labeler") if m else None}


def _spl_row(row: dict) -> dict:
    out = _split_title(row.get("title"))
    out.update({"setid": row.get("setid"), "spl_version": row.get("spl_version"),
                "published_date": row.get("published_date")})
    return out


class DailyMedClient(SourceClient):
    name = "dailymed"
    base_url = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
    cacheable = True
    min_interval = 0.3
    transformation_version = "1.0"

    def search_spls(self, drug_name: str, pagesize: int = 25) -> SourceResult:
        res = self._get("/spls.json", {"drug_name": drug_name, "pagesize": pagesize})
        if res.ok:
            try:
                meta = res.data.get("metadata") or {}
                res.source_release = meta.get("db_published_date")
                res.data = {"total": meta.get("total_elements"),
                            "spls": [_spl_row(r) for r in res.data["data"]]}
            except (KeyError, TypeError, AttributeError):
                res.ok, res.data, res.error = False, None, "unexpected DailyMed search response shape"
        return res

    def spl(self, setid: str) -> SourceResult:
        res = self._get(f"/spls/{quote(str(setid), safe='')}/packaging.json")
        if res.ok:
            try:
                body = res.data["data"]
                res.source_release = (res.data.get("metadata") or {}).get("db_published_date")
                res.provider_record_id = body.get("setid") or setid
                products = []
                for p in body.get("products") or []:
                    products.append({
                        "product_name": p.get("product_name"),
                        "generic_name": p.get("product_name_generic"),
                        "product_code": p.get("product_code"),
                        "active_ingredients": [{"name": a.get("name"), "strength": a.get("strength")}
                                               for a in p.get("active_ingredients") or []],
                        "ndc_packages": [pk.get("ndc") for pk in p.get("packaging") or [] if pk.get("ndc")],
                    })
                res.data = {**_split_title(body.get("title")), "setid": body.get("setid"),
                            "spl_version": body.get("spl_version"),
                            "published_date": body.get("published_date"), "products": products}
            except (KeyError, TypeError, AttributeError):
                res.ok, res.data, res.error = False, None, "unexpected DailyMed SPL response shape"
        return res
