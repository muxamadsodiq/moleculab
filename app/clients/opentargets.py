"""Open Targets Platform GraphQL client (diseases, targets, target-disease evidence, drug candidates).

Schema notes verified by live introspection on 2026-11-17 against API 26.9 / data 26.09. Several widely
documented fields no longer exist, so this client is written against the live schema, not the docs:

* `disease(efoId:)` accepts the platform's current ontology ids. Rheumatoid arthritis is `MONDO_0008383`;
  the older `EFO_0000685` returns null. Disease ids are therefore always resolved through `search`.
* `Target.knownDrugs` does not exist. The field is `Target.drugAndClinicalCandidates`.
* `Drug.isApproved` and `Drug.maximumClinicalTrialPhase` do not exist; there is `maximumClinicalStage`.
  There is also no `hasBeenWithdrawn`/`blackBoxWarning`; curated `drugWarnings` rows carry that instead.
* `Disease.synonyms` is a list of `{relation, terms}`, not strings.
* `drugAndClinicalCandidates.rows` are `ClinicalTargetFromTarget`: `{id, maxClinicalStage, drug,
  clinicalReports, diseases}`, where `diseases` items are `{diseaseFromSource, disease}` and `disease`
  can be null.
* **`orderByScore` must be omitted.** Passing any value (`"descending"`, `"desc"`) silently destroys the
  ordering: `associatedTargets` then returns low-scoring targets first (verified 2026-11-17 — with the
  argument, RA's top rows were CRYL1 0.0053 / CHEK2 0.0097; without it, TYK2 0.7638 / IL6R 0.7517).
  The default order is already score-descending, so this client never sends the argument.
* `Evidence` has `clinicalStage` and `confidence`, not `clinicalPhase`/`clinicalStatus`.

**Association scores are kept with their meaning.** Open Targets returns an overall `score` plus
per-datatype scores (genetic_association, clinical, literature, animal_model, rna_expression, ...). These
are evidence-aggregation scores for prioritisation. They are not probabilities of drug success, and this
client never collapses them into one number: the per-datatype breakdown travels with every row so the UI
and reports can show *why* a target is associated.
"""
from __future__ import annotations

from app.clients.base import GraphQLClient, SourceResult

SEARCH_Q = """
query Search($q: String!, $entities: [String!], $size: Int!) {
  search(queryString: $q, entityNames: $entities, page: {index: 0, size: $size}) {
    total
    hits { id name entity description }
  }
}
"""

DISEASE_Q = """
query Disease($id: String!, $size: Int!) {
  disease(efoId: $id) {
    id name description
    dbXRefs
    synonyms { relation terms }
    therapeuticAreas { id name }
    associatedTargets(page: {index: 0, size: $size}) {
      count
      rows {
        score
        datatypeScores { id score }
        target { id approvedSymbol approvedName biotype
                 proteinIds { id source }
                 targetClass { id label level } }
      }
    }
  }
}
"""

TARGET_Q = """
query Target($id: String!) {
  target(ensemblId: $id) {
    id approvedSymbol approvedName biotype
    functionDescriptions
    synonyms { label source }
    proteinIds { id source }
    targetClass { id label level }
    genomicLocation { chromosome start end strand }
    safetyLiabilities { event eventId biosamples { tissueLabel } datasource literature }
    tractability { label modality value }
  }
}
"""

TARGET_DISEASES_Q = """
query TargetDiseases($id: String!, $size: Int!) {
  target(ensemblId: $id) {
    id approvedSymbol
    associatedDiseases(page: {index: 0, size: $size}) {
      count
      rows { score datatypeScores { id score } disease { id name therapeuticAreas { id name } } }
    }
  }
}
"""

TARGET_DRUGS_Q = """
query TargetDrugs($id: String!) {
  target(ensemblId: $id) {
    id approvedSymbol
    drugAndClinicalCandidates {
      count
      rows {
        id
        maxClinicalStage
        drug {
          id name drugType maximumClinicalStage
          drugWarnings { warningType description toxicityClass country year }
          mechanismsOfAction { rows { mechanismOfAction actionType } }
        }
        diseases { diseaseFromSource disease { id name } }
      }
    }
  }
}
"""

EVIDENCE_Q = """
query Evidence($efoId: String!, $ensemblIds: [String!]!, $datasources: [String!], $size: Int!) {
  disease(efoId: $efoId) {
    id name
    evidences(ensemblIds: $ensemblIds, datasourceIds: $datasources, size: $size) {
      count
      rows {
        id score datasourceId datatypeId
        targetFromSourceId
        literature
        publicationYear
        studyId
        clinicalStage confidence drugFromSource
        diseaseFromSource
        target { id approvedSymbol }
        disease { id name }
      }
    }
  }
}
"""

META_Q = "{ meta { apiVersion { x y z } dataVersion { year month iteration } } }"


def _datatypes(row: dict) -> dict:
    """Per-datatype association scores, keyed by datasource/datatype id, each kept separate."""
    return {d["id"]: d["score"] for d in (row.get("datatypeScores") or []) if d.get("id") is not None}


def _uniprot(protein_ids: list | None) -> list[str]:
    return [p["id"] for p in (protein_ids or [])
            if p.get("source") in ("uniprot_swissprot", "uniprot_trembl", "uniprot")]


class OpenTargetsClient(GraphQLClient):
    name = "opentargets"
    base_url = "https://api.platform.opentargets.org/api/v4/graphql"
    cacheable = True
    min_interval = 0.2
    transformation_version = "1.0"

    def release(self) -> SourceResult:
        res = self._graphql(META_Q)
        if res.ok and isinstance(res.data, dict):
            meta = res.data.get("meta") or {}
            api, data = meta.get("apiVersion") or {}, meta.get("dataVersion") or {}
            api_s = ".".join(str(api.get(k)) for k in "xyz" if api.get(k) is not None)
            data_s = "-".join(str(data.get(k)) for k in ("year", "month", "iteration")
                              if data.get(k) is not None)
            res.source_release = f"api {api_s} / data {data_s}"
            res.data = {"api_version": api_s, "data_version": data_s}
        return res

    # ------------------------------------------------------------------ search

    def search(self, query: str, entities: list[str] | None = None, size: int = 10) -> SourceResult:
        """Resolve a free-text name to platform ids. The only correct way to obtain a disease or
        target id: nothing in MOLECULAB hardcodes one."""
        res = self._graphql(SEARCH_Q, {"q": query, "entities": entities or ["disease", "target", "drug"],
                                       "size": size})
        if res.ok:
            try:
                hits = res.data["search"]["hits"]
                res.data = [{"id": h["id"], "name": h["name"], "entity": h["entity"],
                             "description": h.get("description")} for h in hits]
            except (KeyError, TypeError):
                res.ok, res.data, res.error = False, None, "unexpected Open Targets search response shape"
        return res

    # ----------------------------------------------------------------- disease

    def disease(self, efo_id: str, targets: int = 25) -> SourceResult:
        res = self._graphql(DISEASE_Q, {"id": efo_id, "size": targets})
        if res.ok:
            d = (res.data or {}).get("disease")
            if not d:
                res.ok, res.data = False, None
                res.error = (f"Open Targets has no disease {efo_id}. Resolve the id with search() first; "
                             "ontology ids change between releases.")
                return res
            assoc = d.get("associatedTargets") or {}
            res.provider_record_id = d["id"]
            res.data = {
                "id": d["id"], "name": d["name"], "description": d.get("description"),
                "db_xrefs": d.get("dbXRefs") or [],
                "synonyms": [{"relation": s.get("relation"), "terms": s.get("terms") or []}
                             for s in (d.get("synonyms") or [])],
                "therapeutic_areas": [{"id": t["id"], "name": t["name"]}
                                      for t in (d.get("therapeuticAreas") or [])],
                "associated_target_count": assoc.get("count"),
                "associated_targets": [{
                    "ensembl_id": r["target"]["id"],
                    "gene_symbol": r["target"].get("approvedSymbol"),
                    "target_name": r["target"].get("approvedName"),
                    "biotype": r["target"].get("biotype"),
                    "uniprot_accessions": _uniprot(r["target"].get("proteinIds")),
                    "target_classes": [c.get("label") for c in (r["target"].get("targetClass") or [])],
                    "overall_score": r.get("score"),
                    "datatype_scores": _datatypes(r),
                } for r in (assoc.get("rows") or [])],
            }
        return res

    # ------------------------------------------------------------------ target

    def target(self, ensembl_id: str) -> SourceResult:
        res = self._graphql(TARGET_Q, {"id": ensembl_id})
        if res.ok:
            t = (res.data or {}).get("target")
            if not t:
                res.ok, res.data, res.error = False, None, f"Open Targets has no target {ensembl_id}"
                return res
            res.provider_record_id = t["id"]
            loc = t.get("genomicLocation") or {}
            res.data = {
                "ensembl_id": t["id"], "gene_symbol": t.get("approvedSymbol"),
                "target_name": t.get("approvedName"), "biotype": t.get("biotype"),
                "function_descriptions": t.get("functionDescriptions") or [],
                "synonyms": [s.get("label") for s in (t.get("synonyms") or []) if s.get("label")],
                "uniprot_accessions": _uniprot(t.get("proteinIds")),
                "target_classes": [c.get("label") for c in (t.get("targetClass") or [])],
                "genomic_location": {k: loc.get(k) for k in ("chromosome", "start", "end", "strand")}
                if loc else None,
                # Known safety liabilities are real curated records; absence of them is NOT safety.
                "safety_liabilities": [{"event": s.get("event"), "datasource": s.get("datasource"),
                                        "literature": s.get("literature") or []}
                                       for s in (t.get("safetyLiabilities") or [])],
                "tractability": [{"modality": x.get("modality"), "label": x.get("label"),
                                  "value": x.get("value")} for x in (t.get("tractability") or [])],
            }
        return res

    def target_diseases(self, ensembl_id: str, size: int = 25) -> SourceResult:
        res = self._graphql(TARGET_DISEASES_Q, {"id": ensembl_id, "size": size})
        if res.ok:
            t = (res.data or {}).get("target")
            if not t:
                res.ok, res.data, res.error = False, None, f"Open Targets has no target {ensembl_id}"
                return res
            assoc = t.get("associatedDiseases") or {}
            res.data = {"ensembl_id": t["id"], "gene_symbol": t.get("approvedSymbol"),
                        "count": assoc.get("count"),
                        "rows": [{"disease_id": r["disease"]["id"], "disease_name": r["disease"]["name"],
                                  "overall_score": r.get("score"), "datatype_scores": _datatypes(r)}
                                 for r in (assoc.get("rows") or [])]}
        return res

    def target_drug_candidates(self, ensembl_id: str) -> SourceResult:
        """Drugs and clinical candidates recorded against this target.

        `phase`/`maximumClinicalStage` describe observed clinical development, not current regulatory
        approval in a given jurisdiction. The repurposing layer labels these separately.
        """
        res = self._graphql(TARGET_DRUGS_Q, {"id": ensembl_id})
        if res.ok:
            t = (res.data or {}).get("target")
            if not t:
                res.ok, res.data, res.error = False, None, f"Open Targets has no target {ensembl_id}"
                return res
            block = t.get("drugAndClinicalCandidates") or {}
            res.data = {
                "ensembl_id": t["id"], "gene_symbol": t.get("approvedSymbol"),
                "count": block.get("count"),
                "rows": [self._candidate_row(r) for r in (block.get("rows") or [])],
            }
        return res

    @staticmethod
    def _candidate_row(row: dict) -> dict:
        """One drug/clinical-candidate row.

        `maximum_clinical_stage` is the highest development stage Open Targets records anywhere, and
        `indications` lists the diseases it is recorded against with that stage. Neither is a statement of
        current regulatory approval in a given country, so neither is renamed to suggest one. Curated
        `drug_warnings` are real findings; an empty list means no warning is *recorded*, not that the drug
        is safe.
        """
        drug = row.get("drug") or {}
        moa = (drug.get("mechanismsOfAction") or {}).get("rows") or []
        indications = []
        for item in row.get("diseases") or []:
            disease = item.get("disease") or {}
            indications.append({
                "disease_id": disease.get("id"),
                "disease_name": disease.get("name"),
                # Kept even when the ontology mapping is missing, so the record is not silently dropped.
                "disease_from_source": item.get("diseaseFromSource"),
            })
        return {
            "candidate_id": row.get("id"),
            "row_max_clinical_stage": row.get("maxClinicalStage"),
            "drug_chembl_id": drug.get("id"),
            "drug_name": drug.get("name"),
            "drug_type": drug.get("drugType"),
            "maximum_clinical_stage": drug.get("maximumClinicalStage"),
            "mechanisms_of_action": [{"mechanism_of_action": m.get("mechanismOfAction"),
                                      "action_type": m.get("actionType")} for m in moa],
            "drug_warnings": [{"warning_type": w.get("warningType"), "description": w.get("description"),
                               "toxicity_class": w.get("toxicityClass"), "country": w.get("country"),
                               "year": w.get("year")} for w in (drug.get("drugWarnings") or [])],
            "indications": indications,
        }

    # ---------------------------------------------------------------- evidence

    def target_disease_evidence(self, efo_id: str, ensembl_id: str,
                                datasources: list[str] | None = None, size: int = 25) -> SourceResult:
        """Individual evidence rows behind one target-disease association, each keeping its
        datasource and datatype so the reason for an association stays inspectable."""
        res = self._graphql(EVIDENCE_Q, {"efoId": efo_id, "ensemblIds": [ensembl_id],
                                         "datasources": datasources, "size": size})
        if res.ok:
            d = (res.data or {}).get("disease")
            if not d:
                res.ok, res.data, res.error = False, None, f"Open Targets has no disease {efo_id}"
                return res
            ev = d.get("evidences") or {}
            res.data = {
                "disease_id": d["id"], "disease_name": d["name"], "count": ev.get("count"),
                "rows": [{
                    "evidence_id": r.get("id"), "score": r.get("score"),
                    "datasource_id": r.get("datasourceId"), "datatype_id": r.get("datatypeId"),
                    "target_ensembl_id": (r.get("target") or {}).get("id"),
                    "gene_symbol": (r.get("target") or {}).get("approvedSymbol"),
                    "disease_from_source": r.get("diseaseFromSource"),
                    "pmids": r.get("literature") or [],
                    "publication_year": r.get("publicationYear"),
                    "study_id": r.get("studyId"),
                    # Clinical-precedence evidence records a development stage reached for this
                    # target-disease pair. It is evidence of development, not of efficacy.
                    "clinical_stage": r.get("clinicalStage"),
                    "confidence": r.get("confidence"),
                    "drug_from_source": r.get("drugFromSource"),
                } for r in (ev.get("rows") or [])],
            }
        return res
