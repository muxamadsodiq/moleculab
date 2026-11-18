"""GBIF backbone taxonomy client for plant-name resolution."""
from app.clients.base import SourceClient, SourceResult


class GBIFClient(SourceClient):
    name = "gbif"
    base_url = "https://api.gbif.org/v1"

    def match(self, name: str) -> SourceResult:
        res = self._get("/species/match", {"name": name, "kingdom": "Plantae"})
        if res.ok:
            d = res.data
            if d.get("matchType") == "NONE":
                res.ok, res.data, res.error = False, None, "no GBIF match"
            elif d.get("matchType") == "HIGHERRANK":
                # GBIF fell back to a genus/family/kingdom: that is not an identification of the species asked for
                res.ok, res.data, res.error = False, None, \
                    f"no species-level GBIF match (only matched {d.get('rank', 'a higher rank')} '{d.get('scientificName')}')"
            else:
                res.data = {
                    "gbif_key": d.get("usageKey"),
                    "accepted_gbif_key": d.get("acceptedUsageKey") or d.get("usageKey"),
                    "scientific_name": d.get("scientificName"),
                    "canonical_name": d.get("canonicalName"),
                    "status": d.get("status"),
                    "match_type": d.get("matchType"),
                    "confidence": d.get("confidence"),
                    "family": d.get("family"),
                }
        return res
