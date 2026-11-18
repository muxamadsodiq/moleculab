"""Global drug intelligence with an explicit regulatory boundary.

Three concepts are kept in three SEPARATE record types and are never merged:

* ActiveIngredient       - a substance (INN/generic name, structure, identifiers). From ChEMBL / PubChem.
* PharmaceuticalProduct  - a finished product (brand, labeler, strength, form). From DailyMed (US labels).
* RegulatoryRecord       - a jurisdiction-specific registration with an authority, status and source.

CRITICAL RULE: presence in ChEMBL, PubChem or DailyMed is NOT regulatory approval. ChEMBL `max_phase` is the
highest development phase observed anywhere; it says nothing about registration in a particular country.
`regulatory_status_by_jurisdiction` therefore only ever holds one of:

    registered                      - only with an authoritative source for THAT jurisdiction
    not_found_in_indexed_sources    - an authoritative source for that jurisdiction was queried and had no match
    not_indexed                     - no authoritative source for that jurisdiction is connected

No authoritative registry is currently connected, so `registered` is never produced by build_drug_profile.
The Uzbekistan boundary is reported honestly by uzbekistan_registry_status(); nothing is fabricated.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from app.clients.chembl import ChEMBLClient
from app.clients.dailymed import DailyMedClient
from app.clients.pubchem import PubChemClient

REGISTERED = "registered"
NOT_FOUND = "not_found_in_indexed_sources"
NOT_INDEXED = "not_indexed"
STATUSES = (REGISTERED, NOT_FOUND, NOT_INDEXED)

JURISDICTIONS = {
    "US": "United States (FDA)",
    "EU": "European Union (EMA / national authorities)",
    "UZ": "Uzbekistan (Ministry of Health / State Centre for Expertise and Standardization of Medicines)",
}

MAX_PRODUCT_DETAILS = 5

PHASE_NOTE = ("ChEMBL max_phase is the highest clinical development phase recorded for the molecule worldwide. "
              "It is not a registration or approval in any specific jurisdiction.")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ record types

@dataclass
class ActiveIngredient:
    name: str
    inn_or_generic_name: str | None = None
    chembl_id: str | None = None
    pubchem_cid: int | None = None
    inchikey: str | None = None
    smiles: str | None = None
    molecular_formula: str | None = None
    molecular_weight: float | None = None
    atc_codes: list | None = None
    chembl_max_phase: float | None = None
    chembl_max_phase_note: str = PHASE_NOTE
    match_confidence: str | None = None

    def missing_fields(self) -> list:
        core = ("inn_or_generic_name", "chembl_id", "pubchem_cid", "inchikey", "smiles", "molecular_formula",
                "molecular_weight", "atc_codes", "chembl_max_phase")
        return [k for k in core if getattr(self, k) in (None, [], "")]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["missing_fields"] = self.missing_fields()
        return d


@dataclass
class PharmaceuticalProduct:
    label_title: str | None = None
    brand_name: str | None = None
    manufacturer_or_labeler: str | None = None
    dosage_form: str | None = None
    strength: str | None = None
    route: str | None = None
    ingredient_names: list = field(default_factory=list)
    source: str = "dailymed"
    source_record_id: str | None = None
    market: str = "US (DailyMed label repository)"

    def missing_fields(self) -> list:
        core = ("brand_name", "manufacturer_or_labeler", "dosage_form", "strength", "route")
        return [k for k in core if getattr(self, k) in (None, [], "")]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["missing_fields"] = self.missing_fields()
        return d


@dataclass
class RegulatoryRecord:
    jurisdiction: str
    authority: str
    status: str
    source: str | None
    retrieved_at: str
    note: str | None = None
    evidence: dict | None = None

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError("invalid regulatory status: " + str(self.status))
        if self.status == REGISTERED and not (self.evidence and self.evidence.get("authoritative_source")):
            # Hard guard for the core rule: no 'registered' without an authoritative source.
            raise ValueError("'registered' requires an authoritative source for the jurisdiction")

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ providers

def get_providers() -> dict:
    return {"chembl": ChEMBLClient(), "pubchem": PubChemClient(), "dailymed": DailyMedClient()}


def _prov(res, role: str) -> dict:
    p = res.provenance()
    return {"provider": res.source, "role": role, "available": bool(res.ok), "error": res.error,
            "source_url": res.request_url, "retrieved_at": res.retrieved_at,
            "source_release": res.source_release, "http_status": res.status_code,
            "from_cache": bool(res.from_cache), "raw_payload_hash": p.get("raw_payload_hash")}


def _first14(key):
    return key.split("-")[0] if key else None


def _pick_chembl(name: str, cands: list) -> tuple:
    if not cands:
        return None, None
    low = name.strip().lower()
    for c in cands:
        if (c.get("pref_name") or "").lower() == low:
            return c, "exact_preferred_name"
    if len(cands) == 1:
        return cands[0], "single_candidate"
    return cands[0], "unconfirmed_first_candidate"


def resolve_ingredient(name: str, providers: dict | None = None) -> dict:
    """Resolve a substance name to ONE ActiveIngredient using ChEMBL and PubChem.

    Returns {ingredient, candidates, providers (provenance and availability), contradictions, limitations}.
    A provider that fails is reported as available=False; the ingredient is then built from whatever else
    answered, with the gaps listed in `missing_fields`."""
    providers = providers or get_providers()
    name = (name or "").strip()
    out = {"query": name, "ingredient": None, "candidates": [], "providers": [], "contradictions": [],
           "limitations": [PHASE_NOTE], "retrieved_at": now_iso()}
    if not name:
        out["limitations"].append("empty query")
        return out

    ing = ActiveIngredient(name=name)
    chembl_res = providers["chembl"].search_molecule(name, limit=10)
    out["providers"].append(_prov(chembl_res, "structure, development phase, ATC"))
    if chembl_res.ok:
        out["candidates"] = [{"chembl_id": c.get("chembl_id"), "pref_name": c.get("pref_name"),
                              "max_phase": c.get("max_phase")} for c in chembl_res.data]
        chembl, conf = _pick_chembl(name, chembl_res.data)
        if chembl:
            ing.match_confidence = conf
            ing.chembl_id = chembl.get("chembl_id")
            ing.inn_or_generic_name = chembl.get("pref_name")
            ing.inchikey = chembl.get("inchikey")
            ing.smiles = chembl.get("smiles")
            ing.molecular_formula = chembl.get("molecular_formula")
            ing.atc_codes = [a.get("level5") if isinstance(a, dict) else a
                             for a in (chembl.get("atc_classifications") or [])]
            try:
                mp = chembl.get("max_phase")
                ing.chembl_max_phase = float(mp) if mp is not None else None
            except (TypeError, ValueError):
                ing.chembl_max_phase = None
            if conf == "unconfirmed_first_candidate":
                out["limitations"].append("Several ChEMBL candidates; the first was used and is unconfirmed. "
                                          "Check `candidates` before relying on this identity.")

    pc_res = providers["pubchem"].search_name(name)
    out["providers"].append(_prov(pc_res, "identifiers, formula, weight"))
    if pc_res.ok and pc_res.data:
        pc = pc_res.data[0]
        ing.pubchem_cid = pc.get("pubchem_cid")
        ing.molecular_weight = pc.get("molecular_weight")
        ing.molecular_formula = ing.molecular_formula or pc.get("molecular_formula")
        ing.smiles = ing.smiles or pc.get("smiles")
        if ing.inchikey and pc.get("inchikey") and _first14(ing.inchikey) != _first14(pc["inchikey"]):
            out["contradictions"].append({"field": "inchikey", "chembl": ing.inchikey,
                                          "pubchem": pc["inchikey"],
                                          "note": "Connectivity layers differ; the two providers may describe "
                                                  "different entities. MOLECULAB does not choose."})
        ing.inchikey = ing.inchikey or pc.get("inchikey")
        if ing.match_confidence is None:
            ing.match_confidence = "pubchem_first_candidate"

    if ing.chembl_id or ing.pubchem_cid:
        out["ingredient"] = ing.to_dict()
    else:
        out["limitations"].append("No ingredient could be resolved from the available providers.")
    return out


def _products_from_dailymed(name: str, dm) -> tuple:
    """Returns (products, provenance entries, summary)."""
    provs = []
    res = dm.search_spls(name)
    provs.append(_prov(res, "US product labels"))
    if not res.ok:
        return [], provs, {"available": False, "total_labels": None}
    spls = res.data["spls"]
    summary = {"available": True, "total_labels": res.data.get("total"), "returned": len(spls)}
    products = []
    for row in spls[:MAX_PRODUCT_DETAILS]:
        det = dm.spl(row["setid"])
        provs.append(_prov(det, "US label detail"))
        if det.ok and det.data.get("products"):
            for p in det.data["products"]:
                strengths = sorted({a["strength"] for a in p["active_ingredients"] if a.get("strength")})
                products.append(PharmaceuticalProduct(
                    label_title=det.data.get("title"), brand_name=p.get("product_name"),
                    manufacturer_or_labeler=det.data.get("labeler"),
                    strength="; ".join(strengths) or None,
                    ingredient_names=[a["name"] for a in p["active_ingredients"] if a.get("name")],
                    source_record_id=row["setid"]).to_dict())
        else:
            products.append(PharmaceuticalProduct(
                label_title=row.get("title"), manufacturer_or_labeler=row.get("labeler"),
                source_record_id=row["setid"]).to_dict())
    return products, provs, summary


def uzbekistan_registry_status() -> dict:
    """The ACTUAL access situation for Uzbekistan's official medicines register.

    Result of the investigation (2026-11-18): no documented public API or structured download was found.
    No Uzbek registration record is ever returned by MOLECULAB; this function says why."""
    return {
        "jurisdiction": "UZ",
        "authority": JURISDICTIONS["UZ"],
        "available": False,
        "status": NOT_INDEXED,
        "reason": ("No documented public API or structured (CSV/JSON/XML) download of the Uzbek national "
                   "medicines register was found. The authority's website is a JavaScript single-page app "
                   "whose probed paths (/api, /docs, /swagger, /robots.txt) all return the same HTML shell, "
                   "and the national open-data portal showed no confirmed medicines-register dataset."),
        "checked": [
            {"url": "https://uzpharm-control.uz/", "outcome": "reachable, HTML single-page app; no API "
             "documentation or download link found"},
            {"url": "https://uzpharm-control.uz/api", "outcome": "returns the generic HTML shell, not an API"},
            {"url": "https://uzpharm-control.uz/swagger", "outcome": "returns the generic HTML shell"},
            {"url": "https://data.egov.uz/", "outcome": "national open-data portal reachable; search is "
             "client-rendered and no medicines-register dataset could be confirmed"},
            {"url": "https://data.gov.uz/", "outcome": "unreachable from the test environment"},
            {"url": "https://minzdrav.uz/, https://pharmagency.uz/, https://dori.uz/", "outcome": "unreachable "
             "from the test environment"},
            {"url": "web search: Uzbekistan state register of medicines open data API",
             "outcome": "only law-firm summaries; no documented endpoint"},
        ],
        "required": [
            "A written statement or documentation from the State Centre / Ministry of Health describing a "
            "public API or bulk download of the register, or a formal data-sharing agreement.",
            "Terms of use permitting automated retrieval and redistribution of register entries.",
            "A stable record identifier (registration number) and fields for trade name, INN, manufacturer, "
            "dosage form, registration date and validity, so records can be matched without guessing.",
        ],
        "limitations": ["Absence of a public API does not mean a medicine is unregistered in Uzbekistan. "
                        "Only the official register or the authority can confirm registration.",
                        "A manual lookup on the official site is the only supported route today."],
        "retrieved_at": now_iso(),
        "investigated_on": "2026-11-18",
    }


def regulatory_records(name: str, us_labels: dict) -> dict:
    """One RegulatoryRecord per jurisdiction. No jurisdiction can be `registered` while no authoritative
    registry for it is connected, so this reports not_indexed, with label-repository facts in `evidence`."""
    t = now_iso()
    uz = uzbekistan_registry_status()
    recs = {
        "US": RegulatoryRecord(
            "US", JURISDICTIONS["US"], NOT_INDEXED, "https://dailymed.nlm.nih.gov/dailymed/", t,
            note=("DailyMed is a label repository. A label listing does not by itself establish an approval "
                  "pathway, and no FDA approval database is connected, so no approval is claimed."),
            evidence={"dailymed_labels_found": us_labels.get("total_labels"),
                      "dailymed_available": us_labels.get("available"), "authoritative_source": False}),
        "EU": RegulatoryRecord(
            "EU", JURISDICTIONS["EU"], NOT_INDEXED, None, t,
            note="No EU authorisation source is connected.", evidence={"authoritative_source": False}),
        "UZ": RegulatoryRecord(
            "UZ", JURISDICTIONS["UZ"], NOT_INDEXED, "https://uzpharm-control.uz/", t,
            note=uz["reason"], evidence={"registry_access": "unavailable", "authoritative_source": False}),
    }
    return {k: v.to_dict() for k, v in recs.items()}


def build_drug_profile(name: str, providers: dict | None = None) -> dict:
    """Assemble ingredient + products + regulatory records, kept as three separate sections."""
    providers = providers or get_providers()
    resolved = resolve_ingredient(name, providers)
    products, dm_prov, us = _products_from_dailymed(name, providers["dailymed"])
    records = regulatory_records(name, us)
    unavailable = [p["provider"] for p in resolved["providers"] + dm_prov if not p["available"]]
    return {
        "query": name,
        "active_ingredient": resolved["ingredient"],
        "ingredient_candidates": resolved["candidates"],
        "contradictions": resolved["contradictions"],
        "pharmaceutical_products": products,
        "regulatory_records": records,
        "regulatory_status_by_jurisdiction": {k: v["status"] for k, v in records.items()},
        "uzbekistan_registry": uzbekistan_registry_status(),
        "provenance": resolved["providers"] + dm_prov,
        "unavailable_providers": sorted(set(unavailable)),
        "limitations": resolved["limitations"] + [
            "Presence in ChEMBL, PubChem or DailyMed is not regulatory approval.",
            "Products shown are US DailyMed labels only (first %d detailed); they do not describe "
            "what is marketed in any other country." % MAX_PRODUCT_DETAILS,
        ],
        "retrieved_at": now_iso(),
    }


def search_drugs(q: str, providers: dict | None = None) -> dict:
    providers = providers or get_providers()
    q = (q or "").strip()
    out = {"query": q, "ingredient_candidates": [], "product_candidates": [], "providers": [],
           "unavailable_providers": [], "retrieved_at": now_iso()}
    if not q:
        return out
    c = providers["chembl"].search_molecule(q, limit=10)
    out["providers"].append(_prov(c, "ingredient candidates"))
    if c.ok:
        out["ingredient_candidates"] = [{"chembl_id": m.get("chembl_id"), "name": m.get("pref_name"),
                                         "inchikey": m.get("inchikey"), "max_phase": m.get("max_phase")}
                                        for m in c.data]
    p = providers["pubchem"].search_name(q)
    out["providers"].append(_prov(p, "ingredient candidates"))
    if p.ok:
        have = {i.get("inchikey") for i in out["ingredient_candidates"]}
        for m in p.data[:5]:
            if m.get("inchikey") not in have:
                out["ingredient_candidates"].append({"pubchem_cid": m.get("pubchem_cid"),
                                                     "name": m.get("iupac_name"), "inchikey": m.get("inchikey")})
    d = providers["dailymed"].search_spls(q, pagesize=10)
    out["providers"].append(_prov(d, "US product label candidates"))
    if d.ok:
        out["product_candidates"] = d.data["spls"]
        out["product_total"] = d.data.get("total")
    out["unavailable_providers"] = sorted({x["provider"] for x in out["providers"] if not x["available"]})
    return out


COMPARE_FIELDS = (
    ("Name (INN/generic)", "inn_or_generic_name"),
    ("ChEMBL id", "chembl_id"),
    ("PubChem CID", "pubchem_cid"),
    ("InChIKey", "inchikey"),
    ("Molecular formula", "molecular_formula"),
    ("Molecular weight", "molecular_weight"),
    ("ATC codes", "atc_codes"),
    ("ChEMBL max phase (not approval)", "chembl_max_phase"),
)


def _cell(value) -> dict:
    if value in (None, "", []):
        return {"status": "missing", "value": None}
    return {"status": "present", "value": value}


def compare_profiles(names: list, providers: dict | None = None) -> dict:
    providers = providers or get_providers()
    profiles = [build_drug_profile(n, providers) for n in names]
    rows = []
    for label, key in COMPARE_FIELDS:
        rows.append({"field": label, "values": [_cell((p.get("active_ingredient") or {}).get(key))
                                                for p in profiles]})
    rows.append({"field": "US DailyMed products found",
                 "values": [_cell(len(p["pharmaceutical_products"]) or None) for p in profiles]})
    for j in JURISDICTIONS:
        rows.append({"field": "Regulatory status: " + j,
                     "values": [_cell(p["regulatory_status_by_jurisdiction"][j]) for p in profiles]})
    return {"names": names, "rows": rows, "profiles": profiles,
            "unavailable_providers": sorted({u for p in profiles for u in p["unavailable_providers"]}),
            "limitations": ["Missing values are shown as missing, not hidden.",
                            "Presence in a database is not regulatory approval."],
            "retrieved_at": now_iso()}
