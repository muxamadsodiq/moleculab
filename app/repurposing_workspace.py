"""Repurposing analyses inside research workspaces: frozen snapshots, retrieval, comparison and reports.

A saved analysis is a `WorkspaceResource` of type `repurposing_analysis` (no new table):

* `resource_ref` is the analysis content fingerprint, so the existing UNIQUE(workspace, type, ref)
  constraint makes saving the same analysis twice a no-op (duplicate detection).
* `snapshot` holds the complete analysis exactly as computed, plus `analysis_sha256`, a hash over the
  stored JSON. Retrieval recomputes it, so any later change to the stored snapshot is detected and reported
  instead of being presented as the original. No API endpoint edits a snapshot; a re-run that gives
  different provider data has a different fingerprint and becomes a new snapshot.
* Snapshots are created only by the server running the analysis (never from client-supplied JSON), the
  same rule v0.5 applies to generated hypotheses.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import repurposing
from app.models import ResearchNote, ResearchWorkspace, ResourceType, WorkspaceResource, utcnow
from app.workspace import WorkspaceError, iso, log, md

SNAPSHOT_VERSION = "1"


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def analysis_sha256(analysis: dict) -> str:
    return hashlib.sha256(_canonical(analysis).encode("utf-8")).hexdigest()


def _retrieval_window(analysis: dict) -> tuple[str | None, str | None]:
    times = sorted(s.get("retrieved_at") for s in analysis.get("sources", []) if s.get("retrieved_at"))
    return (times[0], times[-1]) if times else (None, None)


def integrity(r: WorkspaceResource) -> dict:
    snap = r.snapshot or {}
    analysis = snap.get("analysis")
    if not isinstance(analysis, dict):
        return {"status": "invalid", "detail": "snapshot has no analysis object"}
    stored_hash = snap.get("analysis_sha256")
    actual = analysis_sha256(analysis)
    fp_ok = repurposing.fingerprint(analysis) == analysis.get("content_fingerprint") == r.resource_ref
    if actual == stored_hash and fp_ok:
        return {"status": "verified", "analysis_sha256": actual}
    return {"status": "MISMATCH", "stored_sha256": stored_hash, "recomputed_sha256": actual,
            "fingerprint_consistent": fp_ok,
            "detail": "The stored snapshot no longer matches the hash recorded when it was saved. It must not be "
                      "treated as the original analysis."}


def snapshot_summary(r: WorkspaceResource) -> dict:
    a = (r.snapshot or {}).get("analysis") or {}
    first, last = _retrieval_window(a)
    return {"content_fingerprint": a.get("content_fingerprint"),
            "target": (a.get("target") or {}).get("gene_symbol"),
            "target_id": (a.get("target") or {}).get("ensembl_id"),
            "disease": (a.get("disease") or {}).get("name"),
            "disease_id": (a.get("disease") or {}).get("ontology_id"),
            "candidate_count": a.get("candidate_count"), "source_releases": a.get("source_releases"),
            "software_versions": a.get("software_versions"), "retrieved_from": first, "retrieved_to": last,
            "saved_at": (r.snapshot or {}).get("saved_at"), "saved_by": (r.snapshot or {}).get("saved_by"),
            "integrity": integrity(r)["status"]}


def save_analysis(s: Session, ws: ResearchWorkspace, analysis: dict, actor: str) -> tuple[WorkspaceResource, bool]:
    fp = analysis.get("content_fingerprint")
    if not fp or repurposing.fingerprint(analysis) != fp:
        raise WorkspaceError("analysis fingerprint is missing or inconsistent; refusing to save", 422)
    existing = s.scalar(select(WorkspaceResource).where(
        WorkspaceResource.workspace_id == ws.id, WorkspaceResource.resource_type == ResourceType.REPURPOSING_ANALYSIS,
        WorkspaceResource.resource_ref == fp))
    if existing:
        return existing, False
    frozen = json.loads(_canonical(analysis))  # detach from any live object
    snap = {"kind": "repurposing_analysis_snapshot", "snapshot_version": SNAPSHOT_VERSION,
            "saved_at": utcnow().isoformat(timespec="seconds"), "saved_by": actor,
            "analysis_sha256": analysis_sha256(frozen), "analysis": frozen}
    t, d = frozen["target"], frozen["disease"]
    label = (f"Repurposing inventory: {t['gene_symbol']} x {d['name']} "
             f"({frozen['candidate_count']} candidates, {fp[:12]})")[:300]
    r = WorkspaceResource(workspace_id=ws.id, resource_type=ResourceType.REPURPOSING_ANALYSIS, resource_ref=fp,
                          label=label, snapshot=snap)
    s.add(r)
    log(s, ws, "repurposing_analysis_saved", f"{label}; releases {frozen.get('source_releases')}", actor)
    return r, True


def get_saved(s: Session, ws: ResearchWorkspace, resource_id: int) -> WorkspaceResource:
    r = s.get(WorkspaceResource, resource_id)
    if r is None or r.workspace_id != ws.id or r.resource_type != ResourceType.REPURPOSING_ANALYSIS:
        raise WorkspaceError("repurposing analysis not found in this workspace", 404)
    return r


def list_saved(s: Session, ws: ResearchWorkspace) -> list[WorkspaceResource]:
    return list(s.scalars(select(WorkspaceResource).where(
        WorkspaceResource.workspace_id == ws.id,
        WorkspaceResource.resource_type == ResourceType.REPURPOSING_ANALYSIS).order_by(WorkspaceResource.id)))


def saved_dict(r: WorkspaceResource, full: bool = True) -> dict:
    d = {"id": r.id, "workspace_id": r.workspace_id, "label": r.label, "researcher_note": r.researcher_note,
         "summary": snapshot_summary(r), "integrity": integrity(r), "created_at": iso(r.created_at)}
    if full:
        d["snapshot_version"] = (r.snapshot or {}).get("snapshot_version")
        d["analysis"] = (r.snapshot or {}).get("analysis")
    return d


# ---------------------------------------------------------------------------------------- comparison

def _candidate_view(c: dict) -> dict:
    d = c.get("dimensions", {})
    rs = d.get("registered_studies", {})
    return {
        "name": c.get("name"),
        "forms": sorted(f.get("chembl_id") for f in c.get("forms", [])),
        "classification": (c.get("classification") or {}).get("category"),
        "opentargets_stages": (d.get("clinical_development_stage") or {}).get("opentargets_maximum_clinical_stage"),
        "chembl_max_phase": (d.get("clinical_development_stage") or {}).get("chembl_max_phase"),
        "indication_records_for_disease": len((d.get("disease_association") or {}).get("indication_records_for_this_disease") or []),
        "other_indications_mapped": len((d.get("other_indications") or {}).get("mapped") or []),
        "activity_record_counts": (d.get("experimental_activity") or {}).get("record_counts"),
        "registered_studies_listed": sorted(x.get("nct_id") or "" for x in rs.get("records", [])
                                            if x.get("match") == "condition_and_intervention_listed"),
        "recorded_warnings": len((d.get("recorded_warnings") or {}).get("warnings") or []),
        "contradictions": sorted(x.get("type") for x in c.get("contradictions", [])),
        "missing_information": sorted(c.get("missing_information", [])),
    }


def compare(base: WorkspaceResource, other: WorkspaceResource) -> dict:
    a, b = (base.snapshot or {}).get("analysis") or {}, (other.snapshot or {}).get("analysis") or {}
    ca = {c["candidate_id"]: c for c in a.get("candidates", [])}
    cb = {c["candidate_id"]: c for c in b.get("candidates", [])}
    changed = {}
    for cid in sorted(set(ca) & set(cb)):
        va, vb = _candidate_view(ca[cid]), _candidate_view(cb[cid])
        diff = {k: {"base": va[k], "other": vb[k]} for k in va if va[k] != vb[k]}
        if diff:
            changed[cid] = diff
    params_a, params_b = a.get("parameters", {}), b.get("parameters", {})
    assoc_a, assoc_b = a.get("target_disease_association", {}), b.get("target_disease_association", {})
    same_query = (a.get("target", {}).get("ensembl_id"), a.get("disease", {}).get("ontology_id")) == \
                 (b.get("target", {}).get("ensembl_id"), b.get("disease", {}).get("ontology_id"))
    return {
        "base": {"id": base.id, **snapshot_summary(base)}, "other": {"id": other.id, **snapshot_summary(other)},
        "identical_content": a.get("content_fingerprint") == b.get("content_fingerprint"),
        "same_target_and_disease": same_query,
        "parameter_changes": {k: {"base": params_a.get(k), "other": params_b.get(k)}
                              for k in sorted(set(params_a) | set(params_b)) if params_a.get(k) != params_b.get(k)},
        "source_release_changes": {k: {"base": (a.get("source_releases") or {}).get(k),
                                       "other": (b.get("source_releases") or {}).get(k)}
                                   for k in sorted(set(a.get("source_releases") or {}) | set(b.get("source_releases") or {}))
                                   if (a.get("source_releases") or {}).get(k) != (b.get("source_releases") or {}).get(k)},
        "association_change": None if (assoc_a.get("status"), assoc_a.get("overall_score")) ==
                                      (assoc_b.get("status"), assoc_b.get("overall_score")) else
                              {"base": {k: assoc_a.get(k) for k in ("status", "overall_score", "provider_query")},
                               "other": {k: assoc_b.get(k) for k in ("status", "overall_score", "provider_query")}},
        "candidates_only_in_base": sorted(set(ca) - set(cb)),
        "candidates_only_in_other": sorted(set(cb) - set(ca)),
        "candidates_changed": changed,
        "note": ("Differences are differences between provider records retrieved at two times (or with different "
                 "parameters). They are not evidence of a change in any compound's effectiveness."),
    }


# ------------------------------------------------------------------------------------------- report

def report_markdown(s: Session, ws: ResearchWorkspace, r: WorkspaceResource) -> str:
    a = (r.snapshot or {}).get("analysis") or {}
    integ = integrity(r)
    first, last = _retrieval_window(a)
    notes = list(s.scalars(select(ResearchNote).where(
        ResearchNote.workspace_id == ws.id, ResearchNote.resource_type == ResourceType.REPURPOSING_ANALYSIS,
        ResearchNote.resource_ref == r.resource_ref).order_by(ResearchNote.id)))
    body = repurposing.summary_markdown(a).split("\n", 2)[2]  # drop the summary's own title
    unresolved = []
    assoc = a.get("target_disease_association", {})
    if assoc.get("status") != "recorded":
        unresolved.append(f"Target-disease association: {assoc.get('status')} (not evidence of no association).")
    for c in a.get("candidates", []):
        for x in c.get("contradictions", []):
            unresolved.append(f"{c['name']}: unresolved provider disagreement ({x['type']}).")
        for m in c.get("missing_information", []):
            unresolved.append(f"{c['name']}: {m}.")
        mentions = (c.get("dimensions", {}).get("disease_association") or {}).get("unmapped_indication_text_mentioning_disease")
        if mentions:
            unresolved.append(f"{c['name']}: unmapped indication text possibly naming the disease: {mentions}.")
    unresolved.append("Whether any listed compound is effective or safe for this disease is not addressed by these "
                      "records and remains open.")
    L = [f"# Repurposing evidence inventory — {md((a.get('target') or {}).get('gene_symbol'))} x "
         f"{md((a.get('disease') or {}).get('name'))}", "",
         f"*Workspace:* {md(ws.title)} · *saved analysis* #{r.id} · *saved* {md((r.snapshot or {}).get('saved_at'))} UTC",
         "", "## Scope", "",
         f"- This is a **historical snapshot** of public provider records retrieved between {md(first)} and {md(last)} "
         f"(UTC). Sources and releases: {md(', '.join(f'{k} {v}' for k, v in (a.get('source_releases') or {}).items()))}.",
         "- It covers only what those providers returned for the stated queries; it is not a review of the "
         "literature, and absence of a record is not evidence of absence.",
         f"- Snapshot integrity: **{md(integ['status'])}**. Content fingerprint `{md(a.get('content_fingerprint'))}`; "
         f"software {md(a.get('software_versions'))}.",
         f"- {md(a.get('disclaimer'))}", "",
         "## How to read this report", "",
         "1. **Observed provider records** — ids, stages, indications, mechanisms, warnings, study registrations: "
         "copied from the providers, with the source call cited for each.",
         "2. **Computed summaries** — classification categories, evidence-completeness counts, contradiction "
         "detection, study match classes: computed by MOLECULAB from those records by fixed rules.",
         "3. **Researcher notes** — written by people; never verified evidence.",
         "4. **Computational predictions** — none. This analysis contains no predicted values.",
         "5. **Unresolved questions** — listed at the end.", "",
         "## 1-2. Provider records and computed summaries", "", body.strip(), "",
         "## 3. Researcher notes (researcher-authored; not verified evidence)", ""]
    L += [f"- [{md(n.category)}] {md(n.body)} — {md(n.author)}, {md(iso(n.updated_at))}" for n in notes] or ["- none"]
    L += ["", "## 4. Computational predictions", "", "- None. No value in this analysis is a prediction.", "",
          "## 5. Unresolved questions", ""] + [f"- {md(u)}" for u in unresolved]
    if integ["status"] != "verified":
        L += ["", f"**WARNING: snapshot integrity {md(integ['status'])}. {md(integ.get('detail'))}**"]
    return "\n".join(L) + "\n"
