"""ChEMBL web services client (molecules, targets, assays, activities, mechanisms).

Two scientific rules are enforced here rather than left to callers:

1. **Identifiers are never guessed.** Every ChEMBL id must come from a search or a stored record. During
   the 1.0 audit, `CHEMBL4296` looked like a plausible IL6R id but is a sodium channel; the real IL6R is
   `CHEMBL2364155`. `resolve_target` exists so nothing in MOLECULAB has to guess.
2. **Activity records keep their experimental context.** `standard_type`, `standard_relation`,
   `standard_value`, `standard_units`, assay id, target id, organism and assay type are preserved
   individually. This client never converts between endpoints and never merges them: IC50, Ki, Kd and EC50
   come back labelled, and deciding what is comparable is the screening layer's job (see
   `app/activity.py`), which has the compatibility rules and refuses incompatible merges.

ChEMBL asks that clients identify themselves and avoid hammering the service; hence rate limiting and
caching. Paging uses ChEMBL's own `page_meta` rather than guessing offsets.
"""
from __future__ import annotations

from urllib.parse import quote

from app.clients.base import SourceClient, SourceResult

#: Fields kept verbatim from a ChEMBL activity row. Nothing here is averaged or unit-converted.
ACTIVITY_FIELDS = (
    "activity_id", "assay_chembl_id", "assay_type", "assay_description", "target_chembl_id",
    "target_organism", "target_pref_name", "molecule_chembl_id", "canonical_smiles",
    "standard_type", "standard_relation", "standard_value", "standard_units", "standard_flag",
    "pchembl_value", "activity_comment", "data_validity_comment", "potential_duplicate",
    "document_chembl_id", "src_id", "bao_endpoint", "bao_label",
)


def _activity(row: dict) -> dict:
    out = {k: row.get(k) for k in ACTIVITY_FIELDS}
    out["document_doi"] = row.get("document_journal") and row.get("doi") or row.get("doi")
    return out


def _molecule(row: dict) -> dict:
    structures = row.get("molecule_structures") or {}
    props = row.get("molecule_properties") or {}
    return {
        "chembl_id": row.get("molecule_chembl_id"),
        "pref_name": row.get("pref_name"),
        "molecule_type": row.get("molecule_type"),
        "smiles": structures.get("canonical_smiles"),
        "inchi": structures.get("standard_inchi"),
        "inchikey": structures.get("standard_inchi_key"),
        "molecular_formula": props.get("full_molformula"),
        # ChEMBL's own computed properties. Kept distinct from MOLECULAB's RDKit values, which are
        # recomputed locally and stored separately, so the two are never conflated.
        "chembl_mw": float(props["full_mwt"]) if props.get("full_mwt") else None,
        "chembl_alogp": float(props["alogp"]) if props.get("alogp") else None,
        "chembl_psa": float(props["psa"]) if props.get("psa") else None,
        # `max_phase` is ChEMBL's highest observed development phase. It is NOT a statement of current
        # regulatory approval in any particular jurisdiction; the repurposing layer labels it accordingly.
        "max_phase": row.get("max_phase"),
        "first_approval": row.get("first_approval"),
        "oral": row.get("oral"),
        "parenteral": row.get("parenteral"),
        "topical": row.get("topical"),
        "withdrawn_flag": row.get("withdrawn_flag"),
        "black_box_warning": row.get("black_box_warning"),
        "atc_classifications": row.get("atc_classifications") or [],
        # Salt/solvate forms have their own ChEMBL ids; the hierarchy links them to the parent compound.
        # None when ChEMBL gives no hierarchy, so a missing link is never invented.
        "parent_chembl_id": (row.get("molecule_hierarchy") or {}).get("parent_chembl_id"),
    }


def _target(row: dict) -> dict:
    components = row.get("target_components") or []
    accessions = [c.get("accession") for c in components if c.get("accession")]
    return {
        "chembl_id": row.get("target_chembl_id"),
        "pref_name": row.get("pref_name"),
        "target_type": row.get("target_type"),
        "organism": row.get("organism"),
        "tax_id": row.get("tax_id"),
        "uniprot_accessions": accessions,
        "gene_symbols": sorted({
            syn.get("component_synonym")
            for c in components for syn in (c.get("target_component_synonyms") or [])
            if syn.get("syn_type") == "GENE_SYMBOL" and syn.get("component_synonym")
        }),
    }


def _mechanism(row: dict) -> dict:
    return {
        "mechanism_of_action": row.get("mechanism_of_action"),
        "molecule_chembl_id": row.get("molecule_chembl_id"),
        "target_chembl_id": row.get("target_chembl_id"),
        "action_type": row.get("action_type"),
        "mechanism_comment": row.get("mechanism_comment"),
        "max_phase": row.get("max_phase"),
        "direct_interaction": row.get("direct_interaction"),
        "molecular_mechanism": row.get("molecular_mechanism"),
    }


class ChEMBLClient(SourceClient):
    name = "chembl"
    base_url = "https://www.ebi.ac.uk/chembl/api/data"
    cacheable = True
    min_interval = 0.25  # polite spacing; ChEMBL has no published hard per-second cap
    transformation_version = "1.0"

    # ---------------------------------------------------------------- release

    def release(self) -> SourceResult:
        """ChEMBL release name, stored as `source_release` provenance on imported records."""
        res = self._get("/status.json")
        if res.ok and isinstance(res.data, dict):
            res.source_release = res.data.get("chembl_db_version") or res.data.get("chembl_release")
        return res

    # --------------------------------------------------------------- molecules

    def molecule(self, chembl_id: str) -> SourceResult:
        res = self._get(f"/molecule/{quote(str(chembl_id), safe='')}.json")
        if res.ok:
            if isinstance(res.data, dict) and res.data.get("molecule_chembl_id"):
                res.provider_record_id = res.data["molecule_chembl_id"]
                res.data = _molecule(res.data)
            else:
                res.ok, res.data, res.error = False, None, "unexpected ChEMBL molecule response shape"
        return res

    def search_molecule(self, query: str, limit: int = 20) -> SourceResult:
        """Name/synonym search. Returns candidates; the caller chooses, nothing is auto-resolved."""
        res = self._get("/molecule/search.json", {"q": query, "limit": limit})
        if res.ok:
            try:
                res.data = [_molecule(m) for m in res.data["molecules"]]
            except (KeyError, TypeError):
                res.ok, res.data, res.error = False, None, "unexpected ChEMBL molecule search response shape"
        return res

    def molecules_by_inchikey(self, inchikey: str) -> SourceResult:
        res = self._get("/molecule.json", {"molecule_structures__standard_inchi_key": inchikey, "limit": 5})
        if res.ok:
            try:
                res.data = [_molecule(m) for m in res.data["molecules"]]
            except (KeyError, TypeError):
                res.ok, res.data, res.error = False, None, "unexpected ChEMBL molecule response shape"
        return res

    # ----------------------------------------------------------------- targets

    def target(self, chembl_id: str) -> SourceResult:
        res = self._get(f"/target/{quote(str(chembl_id), safe='')}.json")
        if res.ok:
            if isinstance(res.data, dict) and res.data.get("target_chembl_id"):
                res.provider_record_id = res.data["target_chembl_id"]
                res.data = _target(res.data)
            else:
                res.ok, res.data, res.error = False, None, "unexpected ChEMBL target response shape"
        return res

    def resolve_target(self, gene_or_name: str, organism: str | None = "Homo sapiens",
                       limit: int = 10) -> SourceResult:
        """Search targets by synonym. Use this instead of hardcoding any ChEMBL target id.

        Results are returned in ChEMBL's order with `target_type` intact so the caller can require
        SINGLE PROTEIN rather than silently accepting a PROTEIN COMPLEX or FAMILY, which would mix
        measurements made against different biological entities.
        """
        params = {"target_synonym__icontains": gene_or_name, "limit": limit}
        if organism:
            params["organism"] = organism
        res = self._get("/target.json", params)
        if res.ok:
            try:
                res.data = [_target(t) for t in res.data["targets"]]
            except (KeyError, TypeError):
                res.ok, res.data, res.error = False, None, "unexpected ChEMBL target search response shape"
        return res

    # -------------------------------------------------------------- activities

    def activity_count(self, target_chembl_id: str, standard_type: str,
                       relation: str = "=") -> SourceResult:
        """Total matching measurements, from ChEMBL's page_meta. Used to decide whether there is
        enough data to train a model at all, before any download begins."""
        params = {"target_chembl_id": target_chembl_id, "standard_type": standard_type, "limit": 1}
        if relation:
            params["standard_relation"] = relation
        res = self._get("/activity.json", params)
        if res.ok:
            try:
                res.data = res.data["page_meta"]["total_count"]
            except (KeyError, TypeError):
                res.ok, res.data, res.error = False, None, "unexpected ChEMBL activity response shape"
        return res

    def activities(self, target_chembl_id: str, standard_type: str, relation: str | None = "=",
                   limit: int = 1000, max_records: int | None = None,
                   organism: str | None = None) -> SourceResult:
        """Experimental activity measurements for ONE target and ONE endpoint.

        `standard_type` is required and single on purpose: a model or ranking must never be built from a
        mixture of endpoints. Paging follows ChEMBL's `page_meta.next`. On a mid-page failure, whatever was
        collected is returned with `ok=False` and the error, so a partial download is never silently
        presented as complete.
        """
        params = {"target_chembl_id": target_chembl_id, "standard_type": standard_type,
                  "limit": min(int(limit), 1000), "offset": 0}
        if relation:
            params["standard_relation"] = relation
        if organism:
            params["target_organism"] = organism

        rows: list[dict] = []
        first = self._get("/activity.json", params)
        res = first
        while True:
            if not res.ok:
                if rows:  # partial result: report both the data and the failure
                    first.data, first.ok, first.error = rows, False, f"partial download: {res.error}"
                    return first
                return res
            try:
                page = res.data["activities"]
                meta = res.data.get("page_meta") or {}
            except (KeyError, TypeError):
                res.ok, res.data, res.error = False, None, "unexpected ChEMBL activity response shape"
                return res
            rows.extend(_activity(r) for r in page)
            nxt = meta.get("next")
            if not nxt or (max_records is not None and len(rows) >= max_records):
                break
            res = self._get(f"https://www.ebi.ac.uk{nxt}")

        if max_records is not None:
            rows = rows[:max_records]
        first.data, first.ok, first.error = rows, True, None
        first.provider_record_id = f"{target_chembl_id}:{standard_type}"
        return first

    # -------------------------------------------------------------- mechanisms

    def activity_count_for_molecule(self, target_chembl_id: str, molecule_chembl_id: str) -> SourceResult:
        """Number of ChEMBL activity records for one molecule against one target, any endpoint.

        This is a count of deposited records, not a potency: endpoints, relations and assay types are
        not compared here (that is `app/activity.py`'s job).
        """
        res = self._get("/activity.json", {"target_chembl_id": target_chembl_id,
                                           "molecule_chembl_id": molecule_chembl_id, "limit": 1})
        if res.ok:
            try:
                res.data = int(res.data["page_meta"]["total_count"])
            except (KeyError, TypeError, ValueError):
                res.ok, res.data, res.error = False, None, "unexpected ChEMBL activity response shape"
        return res

    def mechanisms_for_target(self, target_chembl_id: str, limit: int = 100) -> SourceResult:
        res = self._get("/mechanism.json", {"target_chembl_id": target_chembl_id, "limit": limit})
        if res.ok:
            try:
                res.data = [_mechanism(m) for m in res.data["mechanisms"]]
            except (KeyError, TypeError):
                res.ok, res.data, res.error = False, None, "unexpected ChEMBL mechanism response shape"
        return res

    def mechanisms_for_molecule(self, molecule_chembl_id: str, limit: int = 50) -> SourceResult:
        res = self._get("/mechanism.json", {"molecule_chembl_id": molecule_chembl_id, "limit": limit})
        if res.ok:
            try:
                res.data = [_mechanism(m) for m in res.data["mechanisms"]]
            except (KeyError, TypeError):
                res.ok, res.data, res.error = False, None, "unexpected ChEMBL mechanism response shape"
        return res

    def assay(self, assay_chembl_id: str) -> SourceResult:
        res = self._get(f"/assay/{quote(str(assay_chembl_id), safe='')}.json")
        if res.ok and isinstance(res.data, dict):
            res.provider_record_id = res.data.get("assay_chembl_id")
            res.data = {k: res.data.get(k) for k in (
                "assay_chembl_id", "assay_type", "assay_type_description", "description",
                "assay_organism", "assay_tax_id", "target_chembl_id", "bao_format",
                "confidence_score", "confidence_description", "document_chembl_id", "relationship_type")}
        return res
