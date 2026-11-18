"""Fetch identifiers for the seed corpus from live sources (needs internet).

Writes data/seed/enriched.json so that `scripts.seed` can load the database
offline and reproducibly. Re-run only when you want fresh identifiers.

    python -m scripts.enrich_seed
"""
import json
import sys
from pathlib import Path

from app.clients.crossref import CrossrefClient
from app.clients.gbif import GBIFClient
from app.clients.pubchem import PubChemClient

SEED = Path(__file__).resolve().parent.parent / "data" / "seed"


def main() -> None:
    plants = json.loads((SEED / "plants.json").read_text(encoding="utf-8"))["plants"]
    evidence = json.loads((SEED / "evidence.json").read_text(encoding="utf-8"))["records"]
    cache = {c["pmid"]: c for c in json.loads((SEED / "pubmed_cache.json").read_text(encoding="utf-8"))}

    gbif, pubchem, crossref = GBIFClient(), PubChemClient(), CrossrefClient()
    out = {"plants": {}, "compounds": {}, "publications": {}}

    for p in plants:
        res = gbif.match(p["accepted_name"])
        out["plants"][p["accepted_name"]] = {**(res.data or {}), "ok": res.ok, "error": res.error,
                                             "provenance": res.provenance()}
        print("gbif", p["accepted_name"], res.ok, (res.data or {}).get("scientific_name"), res.error or "")

    for name in sorted({e["compound"] for e in evidence if e["compound"]}):
        res = pubchem.search_name(name)
        entry = {"ok": res.ok, "error": res.error, "provenance": res.provenance()}
        if res.ok:
            entry["candidates"] = len(res.data)
            entry.update(res.data[0])  # PubChem's best match for the name; flagged when ambiguous
            syn = pubchem.synonyms(res.data[0]["pubchem_cid"], limit=8)
            entry["synonyms"] = syn.data if syn.ok else []
        out["compounds"][name] = entry
        print("pubchem", name, res.ok, entry.get("pubchem_cid"), entry.get("inchikey"), res.error or "")

    for pmid in sorted({e["pmid"] for e in evidence}):
        doi = cache[pmid].get("doi")
        entry = {"doi": doi, "crossref_ok": False}
        if doi:
            res = crossref.get_doi(doi)
            entry.update({"crossref_ok": res.ok, "crossref_error": res.error, "crossref": res.data,
                          "provenance": res.provenance()})
        out["publications"][pmid] = entry
        print("crossref", pmid, doi, entry["crossref_ok"])

    (SEED / "enriched.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", SEED / "enriched.json")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy code page
    main()
