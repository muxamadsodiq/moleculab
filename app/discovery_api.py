"""Disease Explorer, Target Explorer and relationship-graph endpoints. Mounted at /api/v1/discovery.

Everything here is a thin, honest composition of the existing provider clients (Open Targets, ChEMBL,
ClinicalTrials.gov). Rules enforced in this module:

* No placeholder data. A provider call that fails becomes `{"available": false, "error": ...}` for that
  part; the parts that succeeded are still returned (partial results with a visible gap).
* Open Targets association scores are evidence-aggregation scores for prioritisation, NOT probabilities of
  drug success. The overall score and every per-datatype score are passed through separately, never merged.
* A development stage is not regulatory approval; a count of ChEMBL records is not a potency; a registry
  record is not an outcome; the absence of a record is not the absence of evidence.
* The graph only contains an edge when a provider record asserts that exact relationship. Two nodes that
  merely appear in the same response are never connected.
* Ontology ids (MONDO/EFO) are never hardcoded; they come from search.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query

from app.clients.base import SourceResult
from app.clients.chembl import ChEMBLClient
from app.clients.clinicaltrials import ClinicalTrialsClient
from app.clients.opentargets import OpenTargetsClient

router = APIRouter(tags=["discovery"])

#: Per sub-call budget. A provider that does not answer in this time is reported unavailable.
CALL_TIMEOUT = 20.0

SCORE_NOTE = ("Association scores are evidence-aggregation scores used to prioritise targets for review. "
              "They are not probabilities of drug success, efficacy or safety.")
STAGE_NOTE = ("A maximum clinical stage records how far development has been observed. It is not a statement "
              "of regulatory approval in any jurisdiction.")
COUNT_NOTE = ("Counts of deposited ChEMBL activity records for the endpoint. They are not potencies and "
              "say nothing about compound quality. No record is not evidence of no activity.")
TRIAL_NOTE = ("Registry records describe registered studies, not outcomes. Status is administrative, and the "
              "absence of a study is not evidence that no research exists.")

_POOL = ThreadPoolExecutor(max_workers=12, thread_name_prefix="discovery")
_CLIENTS: dict | None = None


def _clients() -> dict:
    """Provider clients, created once. Replaced in tests with mocked-transport clients."""
    global _CLIENTS
    if _CLIENTS is None:
        _CLIENTS = {"ot": OpenTargetsClient(), "chembl": ChEMBLClient(), "ct": ClinicalTrialsClient()}
    return _CLIENTS


# ------------------------------------------------------------------------------------------- helpers

def _failed(provider: str, message: str) -> SourceResult:
    return SourceResult(provider, False, error=message)


def _call(fn: Callable[[], SourceResult], provider: str) -> Any:
    """Submit a provider call to the pool; resolved later by `_gather`."""
    def safe() -> SourceResult:
        try:
            return fn()
        except Exception as exc:  # clients should never raise, but one bad part must not sink the rest
            return _failed(provider, f"{type(exc).__name__}: {exc}")
    return (_POOL.submit(safe), provider)


def _gather(*pending) -> list[SourceResult]:
    futures = [p[0] for p in pending]
    wait(futures, timeout=CALL_TIMEOUT)
    out = []
    for fut, provider in pending:
        if fut.done():
            out.append(fut.result())
        else:
            fut.cancel()
            out.append(_failed(provider, f"timed out after {CALL_TIMEOUT:.0f}s"))
    return out


def _prov(res: SourceResult | None, provider: str | None = None, release: str | None = None) -> dict:
    p = res.provenance() if res is not None else {}
    return {"provider": provider or (res.source if res is not None else None),
            "source_release": release or (res.source_release if res is not None else None),
            "retrieved_at": p.get("retrieved_at"), "request_url": p.get("request_url"),
            "raw_payload_hash": p.get("raw_payload_hash"), "from_cache": p.get("from_cache", False)}


def _part(res: SourceResult, release: str | None = None, **extra) -> dict:
    """One section of a response: its data and provenance, or an honest unavailable marker."""
    if not res.ok:
        return {"available": False, "error": res.error or "provider call failed",
                "provenance": _prov(res, release=release)}
    out = {"available": True, "data": res.data, "provenance": _prov(res, release=release)}
    out.update(extra)
    return out


def _release_of(res: SourceResult) -> str | None:
    return res.source_release if res.ok else None


def _envelope(primary: str, primary_res: SourceResult | None, release: str | None, sources: list[dict]) -> dict:
    prov = _prov(primary_res, provider=primary, release=release)
    prov["sources"] = sources
    return prov


def _search(q: str, entity: str) -> dict:
    ot = _clients()["ot"]
    res, rel = _gather(_call(lambda: ot.search(q, [entity], size=10), "opentargets"),
                       _call(ot.release, "opentargets"))
    release = _release_of(rel)
    base = {"query": q, "entity": entity, "provenance": _prov(res, "opentargets", release)}
    if not res.ok:
        return {**base, "available": False, "error": res.error, "results": []}
    return {**base, "available": True, "results": res.data,
            "note": "Ids are resolved from this search; they are never hardcoded. Zero hits means this "
                    "index has no match for the text, not that the entity does not exist."}


def _stage_rank(stage) -> int:
    order = ["UNKNOWN", "PRECLINICAL", "PHASE_1", "EARLY_PHASE_1", "PHASE_1_2", "PHASE_2", "PHASE_2_3",
             "PHASE_3", "PHASE_4", "APPROVAL", "WITHDRAWN"]
    return order.index(stage) if stage in order else -1


# ------------------------------------------------------------------------------------------ diseases

@router.get("/diseases/search")
def disease_search(q: str = Query(..., min_length=2, max_length=200)):
    return _search(q, "disease")


@router.get("/diseases/{ontology_id}")
def disease_detail(ontology_id: str, targets: int = Query(25, ge=1, le=100)):
    ot = _clients()["ot"]
    res, rel = _gather(_call(lambda: ot.disease(ontology_id, targets=targets), "opentargets"),
                       _call(ot.release, "opentargets"))
    release = _release_of(rel)
    prov = _envelope("opentargets", res, release, [_prov(res, "opentargets", release)])
    if not res.ok:
        return {"available": False, "error": res.error, "ontology_id": ontology_id, "provenance": prov}
    d = res.data
    return {"available": True, "disease": {k: d[k] for k in (
                "id", "name", "description", "db_xrefs", "synonyms", "therapeutic_areas")},
            "associated_target_count": d.get("associated_target_count"),
            "associated_targets": d["associated_targets"],  # per-datatype scores kept separate per row
            "score_note": SCORE_NOTE,
            "limitations": ["Only the top rows by overall score are returned; the full association set is larger.",
                            "A missing datatype score means no evidence of that datatype was recorded, "
                            "which is not evidence of absence."],
            "provenance": prov}


# ------------------------------------------------------------------------------------------- targets

@router.get("/targets/search")
def target_search(q: str = Query(..., min_length=2, max_length=200)):
    return _search(q, "target")


def _pick_chembl_target(candidates: list[dict], symbol: str | None, uniprots: list[str]) -> tuple[dict | None, str]:
    """Choose a ChEMBL SINGLE PROTEIN target for a gene. Never falls back to a complex or family, which
    would mix measurements made on different biological entities."""
    singles = [c for c in candidates if (c.get("target_type") or "").upper() == "SINGLE PROTEIN"]
    if not singles:
        types = sorted({c.get("target_type") or "unknown" for c in candidates})
        return None, ("ChEMBL returned no SINGLE PROTEIN target for this gene"
                      + (f" (found target types: {', '.join(types)})" if types else " (no candidates)")
                      + "; activity counts are not shown rather than mixing entities.")
    wanted = {u.upper() for u in uniprots}
    by_uniprot = [c for c in singles if wanted & {a.upper() for a in c.get("uniprot_accessions") or []}]
    if by_uniprot:
        return by_uniprot[0], "matched by UniProt accession"
    sym = (symbol or "").upper()
    by_symbol = [c for c in singles if sym and sym in {g.upper() for g in c.get("gene_symbols") or []}]
    if by_symbol:
        return by_symbol[0], "matched by gene symbol"
    return None, ("ChEMBL SINGLE PROTEIN candidates did not match this gene's symbol or UniProt accession; "
                  "none was chosen to avoid attaching another protein's data.")


def _chembl_section(symbol: str | None, uniprots: list[str]) -> dict:
    cb = _clients()["chembl"]
    if not symbol:
        return {"available": False, "error": "no gene symbol available to resolve a ChEMBL target"}
    res, = _gather(_call(lambda: cb.resolve_target(symbol), "chembl"))
    if not res.ok:
        return {"available": False, "error": res.error, "provenance": _prov(res, "chembl")}
    chosen, how = _pick_chembl_target(res.data or [], symbol, uniprots)
    out = {"available": True, "resolved": chosen is not None, "resolution": how,
           "candidates": [{"chembl_id": c["chembl_id"], "target_type": c["target_type"],
                           "pref_name": c["pref_name"]} for c in (res.data or [])],
           "provenance": _prov(res, "chembl")}
    if not chosen:
        return out
    ic50, ki = _gather(_call(lambda: cb.activity_count(chosen["chembl_id"], "IC50", relation=""), "chembl"),
                       _call(lambda: cb.activity_count(chosen["chembl_id"], "Ki", relation=""), "chembl"))
    counts = {}
    for label, r in (("IC50", ic50), ("Ki", ki)):
        counts[label] = ({"available": True, "record_count": r.data, "provenance": _prov(r, "chembl")}
                         if r.ok else {"available": False, "error": r.error, "provenance": _prov(r, "chembl")})
    out.update({"target": chosen, "activity_counts": counts, "activity_count_note": COUNT_NOTE})
    return out


@router.get("/targets/{ensembl_id}")
def target_detail(ensembl_id: str, diseases: int = Query(25, ge=1, le=100)):
    ot = _clients()["ot"]
    t, dis, drugs, rel = _gather(
        _call(lambda: ot.target(ensembl_id), "opentargets"),
        _call(lambda: ot.target_diseases(ensembl_id, size=diseases), "opentargets"),
        _call(lambda: ot.target_drug_candidates(ensembl_id), "opentargets"),
        _call(ot.release, "opentargets"))
    release = _release_of(rel)
    symbol = (t.data or {}).get("gene_symbol") if t.ok else None
    symbol = symbol or ((dis.data or {}).get("gene_symbol") if dis.ok else None) \
        or ((drugs.data or {}).get("gene_symbol") if drugs.ok else None)
    uniprots = (t.data or {}).get("uniprot_accessions", []) if t.ok else []
    chembl = _chembl_section(symbol, uniprots)

    parts = {"target": _part(t, release), "associated_diseases": _part(dis, release),
             "drug_candidates": _part(drugs, release)}
    parts["associated_diseases"]["score_note"] = SCORE_NOTE
    parts["drug_candidates"]["stage_note"] = STAGE_NOTE
    parts["chembl"] = chembl
    sources = [_prov(r, "opentargets", release) for r in (t, dis, drugs)]
    if chembl.get("provenance"):
        sources.append(chembl["provenance"])
    ok_any = any(p.get("available") for p in parts.values())
    resp = {"available": ok_any, "ensembl_id": ensembl_id, **parts,
            "provenance": _envelope("opentargets", t, release, sources)}
    if not ok_any:
        resp["error"] = t.error
    return resp


# -------------------------------------------------------------------------------------- evidence/trials

@router.get("/evidence")
def evidence(disease: str = Query(..., min_length=3), target: str = Query(..., min_length=3),
             size: int = Query(25, ge=1, le=100)):
    ot = _clients()["ot"]
    res, rel = _gather(_call(lambda: ot.target_disease_evidence(disease, target, size=size), "opentargets"),
                       _call(ot.release, "opentargets"))
    release = _release_of(rel)
    prov = _envelope("opentargets", res, release, [_prov(res, "opentargets", release)])
    if not res.ok:
        return {"available": False, "error": res.error, "provenance": prov}
    return {"available": True, "evidence": res.data, "provenance": prov,
            "note": "Evidence rows keep their datasource and datatype. Clinical-stage rows record development "
                    "reached, not efficacy or approval."}


@router.get("/trials")
def trials(condition: str | None = Query(None, max_length=200),
           intervention: str | None = Query(None, max_length=200)):
    if not (condition or intervention):
        raise HTTPException(422, "provide condition and/or intervention")
    ct = _clients()["ct"]
    res, = _gather(_call(lambda: ct.search(condition=condition, intervention=intervention, max_records=25),
                         "clinicaltrials"))
    prov = _envelope("clinicaltrials", res, None, [_prov(res, "clinicaltrials")])
    if not res.ok:
        return {"available": False, "error": res.error, "provenance": prov}
    d = res.data or {}
    return {"available": True, "total_count": d.get("total_count"), "returned": d.get("returned"),
            "studies": d.get("studies", []), "note": TRIAL_NOTE, "provenance": prov}


# ------------------------------------------------------------------------------------------- graph

def _node(nid: str, ntype: str, label: str, ids: dict, source: str, **extra) -> dict:
    return {"id": nid, "type": ntype, "label": label,
            "identifiers": {k: v for k, v in ids.items() if v}, "source": source, **extra}


@router.get("/graph")
def graph(disease: str = Query(..., min_length=3), targets: int = Query(10, ge=1, le=30),
          drugs: int = Query(15, ge=1, le=50)):
    """Disease -> top targets -> drug candidates of the top target.

    Edges come only from provider assertions: Open Targets target-disease association rows, and Open
    Targets drug/clinical-candidate records for the top target. No other pair of nodes is ever linked.
    """
    ot = _clients()["ot"]
    res, rel = _gather(_call(lambda: ot.disease(disease, targets=targets), "opentargets"),
                       _call(ot.release, "opentargets"))
    release = _release_of(rel)
    if not res.ok:
        return {"available": False, "error": res.error, "nodes": [], "edges": [],
                "provenance": _envelope("opentargets", res, release, [_prov(res, "opentargets", release)])}

    d = res.data
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    did = "disease:" + d["id"]
    nodes[did] = _node(did, "disease", d["name"], {"ontology_id": d["id"]}, "opentargets",
                       therapeutic_areas=[t["name"] for t in d.get("therapeutic_areas", [])])
    rows = d.get("associated_targets", [])
    for r in rows:
        tid = "target:" + r["ensembl_id"]
        nodes[tid] = _node(tid, "target", r.get("gene_symbol") or r["ensembl_id"],
                           {"ensembl_id": r["ensembl_id"], "gene_symbol": r.get("gene_symbol"),
                            "uniprot": ", ".join(r.get("uniprot_accessions") or [])},
                           "opentargets", target_name=r.get("target_name"),
                           target_classes=r.get("target_classes") or [])
        edges.append({"id": did + "->" + tid, "source": did, "target": tid,
                      "relationship_type": "associated_with", "provider": "opentargets",
                      "evidence_basis": "Open Targets target-disease association (aggregated evidence "
                                        "score; prioritisation only, not a probability of success)",
                      "score": r.get("overall_score"), "datatype_scores": r.get("datatype_scores") or {}})

    sources = [_prov(res, "opentargets", release)]
    drug_part: dict = {"available": False, "error": "no associated targets returned, so there is no top "
                                                     "target to look up drugs for"}
    top = rows[0] if rows else None
    if top:
        dres, = _gather(_call(lambda: ot.target_drug_candidates(top["ensembl_id"]), "opentargets"))
        sources.append(_prov(dres, "opentargets", release))
        if not dres.ok:
            drug_part = {"available": False, "error": dres.error, "top_target": top["ensembl_id"]}
        else:
            tid = "target:" + top["ensembl_id"]
            skipped = 0
            seen: dict[str, dict] = {}
            for c in dres.data.get("rows", []):
                cid = c.get("drug_chembl_id")
                if not cid:
                    skipped += 1  # cannot make an identified node from a record with no drug id
                    continue
                if cid in seen:
                    for m in c.get("mechanisms_of_action") or []:
                        if m not in seen[cid]["mechanisms"]:
                            seen[cid]["mechanisms"].append(m)
                    if _stage_rank(c.get("maximum_clinical_stage")) > _stage_rank(seen[cid]["stage"]):
                        seen[cid]["stage"] = c.get("maximum_clinical_stage")
                    continue
                seen[cid] = {"name": c.get("drug_name"), "type": c.get("drug_type"),
                             "stage": c.get("maximum_clinical_stage"),
                             "mechanisms": list(c.get("mechanisms_of_action") or [])}
            ranked = sorted(seen.items(), key=lambda kv: -_stage_rank(kv[1]["stage"]))[:drugs]
            for cid, v in ranked:
                nid = "drug:" + cid
                nodes[nid] = _node(nid, "drug", v["name"] or cid, {"chembl_id": cid}, "opentargets",
                                   drug_type=v["type"], maximum_clinical_stage=v["stage"])
                edges.append({"id": tid + "->" + nid, "source": tid, "target": nid,
                              "relationship_type": "drug_or_clinical_candidate_for_target",
                              "provider": "opentargets",
                              "evidence_basis": "Open Targets drug and clinical candidate record for this "
                                                "target (stage is development stage, not approval)",
                              "mechanisms_of_action": v["mechanisms"], "max_clinical_stage": v["stage"],
                              "score": None})
            drug_part = {"available": True, "top_target": top["ensembl_id"],
                         "candidates_recorded": dres.data.get("count"), "candidates_shown": len(ranked),
                         "records_without_drug_id_skipped": skipped,
                         "note": "Drug edges are shown for the top-scoring target only. " + STAGE_NOTE}

    return {"available": True, "disease_id": d["id"], "nodes": list(nodes.values()), "edges": edges,
            "drug_section": drug_part,
            "notes": [SCORE_NOTE, STAGE_NOTE,
                      "Edges exist only where a provider record asserts the relationship. Absence of an "
                      "edge is not evidence that no relationship exists."],
            "provenance": _envelope("opentargets", res, release, sources)}
