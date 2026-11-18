"""Research workspace service: an orchestration layer over the v0.3 verifier and the v0.4 research engine.

Nothing here re-implements verification, gap detection, contradiction detection or hypothesis generation;
it stores what a researcher collected and decided, and compiles reports from the live services.
Researcher-authored text (notes, researcher hypotheses, decisions) is stored and reported as such and is
never turned into a verified observation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import research
from app.claims import Verdict
from app.models import (CausalAttribution, Compound, EvidenceRecord, EvidenceType, HypothesisStatus, Plant,
                        Publication, ResearchNote, ResearchReport, ResearchTask, ResearchWorkspace, ResourceType,
                        TaskStatus, WorkspaceActivity, WorkspaceHypothesis, WorkspaceResource, WorkspaceStatus, utcnow)


class WorkspaceError(ValueError):
    """Invalid request about workspace content (mapped to 422/409 by the API)."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def iso(dt):
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------- activity

def log(s: Session, ws: ResearchWorkspace, action: str, detail: str, actor: str) -> None:
    s.add(WorkspaceActivity(workspace_id=ws.id, action=action, detail=detail[:2000], actor=actor))
    ws.updated_at = utcnow()


# ---------------------------------------------------------------- resources

def resolve_resource(s: Session, rtype: ResourceType, ref: str) -> tuple[str, dict | None]:
    """Return (label, snapshot) for an existing resource, or raise WorkspaceError."""
    ref = str(ref).strip()
    if rtype in (ResourceType.PLANT, ResourceType.COMPOUND, ResourceType.PUBLICATION, ResourceType.EVIDENCE):
        if not ref.isdigit():
            raise WorkspaceError(f"{rtype.value} reference must be a numeric id")
        model = {ResourceType.PLANT: Plant, ResourceType.COMPOUND: Compound, ResourceType.PUBLICATION: Publication,
                 ResourceType.EVIDENCE: EvidenceRecord}[rtype]
        obj = s.get(model, int(ref))
        if obj is None:
            raise WorkspaceError(f"{rtype.value} {ref} does not exist", 404)
        if rtype == ResourceType.PLANT:
            return obj.accepted_name, None
        if rtype == ResourceType.COMPOUND:
            return obj.name, None
        if rtype == ResourceType.PUBLICATION:
            return f"PMID {obj.pmid} ({obj.year}): {obj.title}"[:300], None
        return (f"Evidence #{obj.id}: {obj.plant.accepted_name}"
                + (f" · {obj.compound.name}" if obj.compound else "") + f" ({obj.evidence_type})")[:300], None
    if rtype == ResourceType.CONTRADICTION:
        for c in research.contradictions(s):
            if c["id"] == ref:
                return f"Conflict: {c['compound']['name']} in {c['plant']['accepted_name']}", c
        raise WorkspaceError(f"contradiction {ref} is not present in the current data", 404)
    if rtype == ResourceType.GAP:
        for g in research.detect_gaps(s):
            if g["id"] == ref:
                subject = (g["compound"] or {}).get("name") or (g["plant"] or {}).get("accepted_name") or ""
                return f"Gap ({g['category'].replace('_', ' ')}): {subject}"[:300], g
        raise WorkspaceError(f"gap {ref} is not present in the current data", 404)
    if rtype == ResourceType.REPURPOSING_ANALYSIS:
        raise WorkspaceError("repurposing analyses cannot be added as generic resources; save them with "
                             "POST /api/workspaces/{id}/repurposing-analyses, which runs the analysis server-side")
    raise WorkspaceError(f"unknown resource type {rtype}")


def add_resource(s: Session, ws: ResearchWorkspace, rtype: ResourceType, ref: str, note: str | None, actor: str,
                 log_it: bool = True) -> tuple[WorkspaceResource, bool]:
    """Add one resource. Returns (resource, created); an existing resource is returned unchanged."""
    ref = str(ref).strip()
    existing = s.scalar(select(WorkspaceResource).where(WorkspaceResource.workspace_id == ws.id,
                                                         WorkspaceResource.resource_type == rtype,
                                                         WorkspaceResource.resource_ref == ref))
    if existing:
        return existing, False
    label, snapshot = resolve_resource(s, rtype, ref)
    r = WorkspaceResource(workspace_id=ws.id, resource_type=rtype, resource_ref=ref, label=label,
                          researcher_note=note, snapshot=snapshot)
    s.add(r)
    if log_it:
        action = {ResourceType.EVIDENCE: "evidence_collected", ResourceType.PUBLICATION: "publication_added",
                  ResourceType.CONTRADICTION: "contradiction_examined", ResourceType.GAP: "gap_investigated"}.get(
            rtype, f"{rtype.value}_added")
        log(s, ws, action, label, actor)
    return r, True


def collect_plant(s: Session, ws: ResearchWorkspace, plant_id: int, include: set[str], actor: str) -> dict:
    """Add a plant and, via existing relationships, its compounds, publications and/or evidence records."""
    plant = s.get(Plant, plant_id)
    if plant is None:
        raise WorkspaceError(f"plant {plant_id} does not exist", 404)
    added = {"plant": 0, "compound": 0, "publication": 0, "evidence": 0}
    _, c = add_resource(s, ws, ResourceType.PLANT, str(plant.id), None, actor, log_it=False)
    added["plant"] += c
    records = list(s.scalars(select(EvidenceRecord).where(EvidenceRecord.plant_id == plant.id).order_by(EvidenceRecord.id)))
    if "compounds" in include:
        for cid in sorted({r.compound_id for r in records if r.compound_id}):
            added["compound"] += add_resource(s, ws, ResourceType.COMPOUND, str(cid), None, actor, log_it=False)[1]
    if "publications" in include:
        for pid in sorted({r.publication_id for r in records}):
            added["publication"] += add_resource(s, ws, ResourceType.PUBLICATION, str(pid), None, actor, log_it=False)[1]
    if "evidence" in include:
        for r in records:
            added["evidence"] += add_resource(s, ws, ResourceType.EVIDENCE, str(r.id), None, actor, log_it=False)[1]
    log(s, ws, "plant_collected", f"{plant.accepted_name}: added " +
        ", ".join(f"{n} {k}" for k, n in added.items()) + " (existing items skipped)", actor)
    return added


def resource_dict(s: Session, r: WorkspaceResource) -> dict:
    if r.resource_type == ResourceType.REPURPOSING_ANALYSIS:
        from app.repurposing_workspace import snapshot_summary
        return {"id": r.id, "resource_type": r.resource_type, "resource_ref": r.resource_ref, "label": r.label,
                "researcher_note": r.researcher_note, "snapshot": snapshot_summary(r),
                "link": f"#/workspace/{r.workspace_id}/collection", "present_in_current_data": True,
                "historical_snapshot": True, "created_at": iso(r.created_at), "updated_at": iso(r.updated_at)}
    link = {"plant": f"#/plant/{r.resource_ref}", "compound": f"#/compound/{r.resource_ref}",
            "publication": f"#/publication/{r.resource_ref}", "evidence": f"#/evidence/{r.resource_ref}",
            "contradiction": "#/research/contradictions", "gap": "#/research/gaps"}[r.resource_type]
    try:
        resolve_resource(s, ResourceType(r.resource_type), r.resource_ref)
        current = True
    except WorkspaceError:
        current = False
    return {"id": r.id, "resource_type": r.resource_type, "resource_ref": r.resource_ref, "label": r.label,
            "researcher_note": r.researcher_note, "snapshot": r.snapshot, "link": link,
            "present_in_current_data": current, "created_at": iso(r.created_at), "updated_at": iso(r.updated_at)}


def collected(s: Session, ws: ResearchWorkspace, rtype: ResourceType) -> list[str]:
    return list(s.scalars(select(WorkspaceResource.resource_ref).where(
        WorkspaceResource.workspace_id == ws.id, WorkspaceResource.resource_type == rtype).order_by(WorkspaceResource.id)))


# ---------------------------------------------------------------- notes

def validate_link(s: Session, rtype: str | None, ref: str | None, ws: ResearchWorkspace | None = None) -> None:
    if (rtype is None) != (ref is None):
        raise WorkspaceError("resource_type and resource_ref must be given together")
    if rtype == ResourceType.REPURPOSING_ANALYSIS:
        # analyses exist only inside a workspace: the link must point at one saved in this workspace
        if ws is None or s.scalar(select(WorkspaceResource.id).where(
                WorkspaceResource.workspace_id == ws.id, WorkspaceResource.resource_type == rtype,
                WorkspaceResource.resource_ref == str(ref))) is None:
            raise WorkspaceError(f"repurposing analysis {ref} is not saved in this workspace", 404)
        return
    if rtype is not None:
        resolve_resource(s, ResourceType(rtype), ref)


def note_dict(n: ResearchNote) -> dict:
    return {"id": n.id, "category": n.category, "body": n.body, "resource_type": n.resource_type,
            "resource_ref": n.resource_ref, "author": n.author, "content_origin": "researcher_authored",
            "verification_status": "not_verified (researcher note)",
            "created_at": iso(n.created_at), "updated_at": iso(n.updated_at)}


# ---------------------------------------------------------------- hypotheses

def validate_evidence_ids(s: Session, ids: list[int]) -> list[int]:
    ids = sorted({int(i) for i in ids})
    if ids:
        found = set(s.scalars(select(EvidenceRecord.id).where(EvidenceRecord.id.in_(ids))))
        missing = [i for i in ids if i not in found]
        if missing:
            raise WorkspaceError(f"evidence records do not exist: {missing}", 404)
    return ids


def check_status_rules(s: Session, h: WorkspaceHypothesis) -> None:
    if h.status == HypothesisStatus.PRELIMINARY_SUPPORT and not h.supporting_evidence_ids:
        raise WorkspaceError("'supported_by_preliminary_evidence' requires at least one linked supporting evidence record")
    if h.status == HypothesisStatus.CONTRADICTED and not h.contradicting_evidence_ids:
        raise WorkspaceError("'contradicted' requires at least one linked contradicting evidence record")
    if h.causal_attribution == CausalAttribution.SUPPORTED_BY_COMPOUND_EXPERIMENT:
        recs = [s.get(EvidenceRecord, i) for i in h.supporting_evidence_ids]
        experimental = [r for r in recs if r and EvidenceType(r.evidence_type) in
                        (EvidenceType.EXPERIMENTAL_BIOACTIVITY, EvidenceType.CLINICAL_STUDY) and r.compound
                        and r.compound.name.lower() in h.statement.lower()]
        if not experimental:
            raise WorkspaceError("causal attribution can be marked as supported only with a linked compound-level "
                                 "experimental record (experimental_bioactivity or clinical_study) for the named compound. "
                                 "Occurrence in a plant plus activity of an extract does not establish causation.")


def hypothesis_dict(h: WorkspaceHypothesis) -> dict:
    unresolved = []
    if h.causal_attribution == CausalAttribution.UNTESTED:
        unresolved.append("Unresolved: whether the named compound causes the activity reported for the material is "
                          "untested. Occurrence of a compound in a plant and activity of an extract do not establish causation.")
    return {"id": h.id, "origin": h.origin, "statement": h.statement, "category": h.category, "status": h.status,
            "status_meaning": "investigation progress, not scientific proof",
            "validation_state": "unvalidated", "causal_attribution": h.causal_attribution,
            "unresolved_questions": unresolved,
            "supporting_evidence_ids": h.supporting_evidence_ids or [],
            "contradicting_evidence_ids": h.contradicting_evidence_ids or [],
            "alternative_explanations": h.alternative_explanations or [],
            "validation_methods": h.validation_methods or [], "generator_snapshot": h.generator_snapshot,
            "author": h.author, "created_at": iso(h.created_at), "updated_at": iso(h.updated_at)}


def workspace_scope(s: Session, ws: ResearchWorkspace) -> tuple[list[int], list[int]]:
    return ([int(x) for x in collected(s, ws, ResourceType.PLANT)],
            [int(x) for x in collected(s, ws, ResourceType.COMPOUND)])


def generate_candidates(s: Session, ws: ResearchWorkspace, plant_id=None, compound_id=None, question=None) -> list[dict]:
    """Run the v0.4 generator for the given scope (default: every collected plant)."""
    if plant_id or compound_id or question:
        runs = [research.hypotheses(s, plant_id=plant_id, compound_id=compound_id, question=question)]
        sources = [{"plant_id": plant_id, "compound_id": compound_id, "question": question}]
    else:
        plants, _ = workspace_scope(s, ws)
        if not plants:
            raise WorkspaceError("collect a plant first, or pass plant_id / compound_id / question")
        runs = [research.hypotheses(s, plant_id=p) for p in plants]
        sources = [{"plant_id": p, "compound_id": None, "question": None} for p in plants]
    out = []
    for run, src in zip(runs, sources):
        out.append({"source": src, "hypotheses": run["hypotheses"], "abstentions": run["abstentions"],
                    "documented_observations": run["documented_observations"],
                    "rejected_hypotheses": run["rejected_hypotheses"]})
    return out


def save_generated(s: Session, ws: ResearchWorkspace, source: dict, statement: str, actor: str) -> WorkspaceHypothesis:
    """Save a generator hypothesis. It is regenerated server-side and must match exactly: a client cannot
    submit altered generator output."""
    run = research.hypotheses(s, plant_id=source.get("plant_id"), compound_id=source.get("compound_id"),
                              question=source.get("question"))
    match = next((h for h in run["hypotheses"] if h["hypothesis"] == statement), None)
    if match is None:
        raise WorkspaceError("this statement is not produced by the generator for the given source; save it as a "
                             "researcher hypothesis instead")
    h = WorkspaceHypothesis(
        workspace_id=ws.id, origin="generated", statement=match["hypothesis"], category=match["category"],
        status=HypothesisStatus.PROPOSED,
        causal_attribution=CausalAttribution.UNTESTED if match["category"] == "activity_attribution"
        else CausalAttribution.NOT_APPLICABLE,
        supporting_evidence_ids=sorted(match["supporting_evidence_ids"]), contradicting_evidence_ids=[],
        alternative_explanations=[l for l in match["limitations"] if "lternative" in l],
        validation_methods=match["suggested_validation"], generator_snapshot=match, author=actor)
    s.add(h)
    log(s, ws, "hypothesis_generated", h.statement, actor)
    return h


# ---------------------------------------------------------------- tasks

def task_dict(t: ResearchTask) -> dict:
    return {"id": t.id, "title": t.title, "description": t.description, "status": t.status, "priority": t.priority,
            "due_date": t.due_date, "related_resources": t.related_resources or [], "completed_at": iso(t.completed_at),
            "created_at": iso(t.created_at), "updated_at": iso(t.updated_at)}


def ws_dict(s: Session, ws: ResearchWorkspace) -> dict:
    counts = dict(s.execute(select(WorkspaceResource.resource_type, func.count()).where(
        WorkspaceResource.workspace_id == ws.id).group_by(WorkspaceResource.resource_type)).all())
    open_tasks = s.scalar(select(func.count()).select_from(ResearchTask).where(
        ResearchTask.workspace_id == ws.id, ResearchTask.status != TaskStatus.DONE))
    n = lambda m: s.scalar(select(func.count()).select_from(m).where(m.workspace_id == ws.id))  # noqa: E731
    return {"id": ws.id, "title": ws.title, "research_question": ws.research_question, "description": ws.description,
            "research_domain": ws.research_domain, "scope": ws.scope, "status": ws.status, "owner": ws.owner,
            "created_at": iso(ws.created_at), "updated_at": iso(ws.updated_at), "archived_at": iso(ws.archived_at),
            "resource_counts": counts, "outstanding_tasks": open_tasks, "notes": n(ResearchNote),
            "hypotheses": n(WorkspaceHypothesis), "reports": n(ResearchReport)}


# ---------------------------------------------------------------- reports

LIMITATIONS = [
    "This report reflects the MOLECULAB index at generation time (evidence curated with AI assistance from PubMed "
    "titles/abstracts), not the entirety of the published literature. 'Not indexed' does not mean 'not studied'.",
    "No evidence record has been confirmed by a domain expert unless its review status says so.",
    "Observations were checked with MOLECULAB's rule-based claim verifier against the stored excerpt; full texts were not read.",
    "Hypotheses are unvalidated research directions. Their status records investigation progress, not proof.",
    "Occurrence of a compound in a plant and activity reported for an extract or fraction do not establish that the "
    "compound causes that activity.",
    "Researcher notes are researcher-authored and are not verified evidence.",
    "Contradictions are shown with their documented context; MOLECULAB does not decide which report is correct.",
]


def md(text) -> str:
    """Escape text for safe Markdown (no raw HTML, no accidental formatting or table breaks)."""
    t = str(text if text is not None else "")
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for ch in "\\`*_[]#|":
        t = t.replace(ch, "\\" + ch)
    return " ".join(t.split())


def _fingerprint(s: Session, content: dict) -> dict:
    from app.main import app as fastapi_app
    rev = s.execute(select(func.max(EvidenceRecord.id))).scalar()
    try:
        schema = s.connection().exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    except Exception:  # tests build the schema without Alembic
        schema = None
    counts = {name: s.scalar(select(func.count()).select_from(m)) for name, m in
              (("plants", Plant), ("compounds", Compound), ("publications", Publication), ("evidence_records", EvidenceRecord))}
    body = json.dumps({k: v for k, v in content.items() if k != "generated_at"}, sort_keys=True, default=str)
    return {"app_version": fastapi_app.version, "schema_revision": schema, "max_evidence_id": rev, **counts,
            "content_sha256": hashlib.sha256(body.encode()).hexdigest()}


def build_report(s: Session, ws: ResearchWorkspace, actor: str) -> ResearchReport:
    plants_all, compounds_all, records_all = research._load(s)
    rec_by_id = {r.id: r for r in records_all}
    plant_ids, compound_ids = workspace_scope(s, ws)
    pub_ids = [int(x) for x in collected(s, ws, ResourceType.PUBLICATION)]
    ev_ids = [int(x) for x in collected(s, ws, ResourceType.EVIDENCE)]
    ev_recs = [rec_by_id[i] for i in ev_ids if i in rec_by_id]
    accepted, rejected = research._observations(records_all, plants_all, compounds_all, ev_recs)
    not_collected = sorted(r.id for r in records_all if r.plant_id in plant_ids and r.id not in ev_ids)

    # contradictions: everything in scope (never silently omitted) + collected snapshots
    conflicts = {c["id"]: {**c, "collected": False} for p in plant_ids for c in research.contradictions(s, plant_id=p)}
    for cid in compound_ids:
        for c in research.contradictions(s, compound_id=cid):
            conflicts.setdefault(c["id"], {**c, "collected": False})
    for r in s.scalars(select(WorkspaceResource).where(WorkspaceResource.workspace_id == ws.id,
                                                       WorkspaceResource.resource_type == ResourceType.CONTRADICTION)):
        conflicts.setdefault(r.resource_ref, {**(r.snapshot or {}), "present_in_current_data": False})
        conflicts[r.resource_ref]["collected"] = True
    gaps = {g["id"]: {**g, "collected": False} for p in plant_ids for g in research.gaps_for(s, plant_id=p)}
    for r in s.scalars(select(WorkspaceResource).where(WorkspaceResource.workspace_id == ws.id,
                                                       WorkspaceResource.resource_type == ResourceType.GAP)):
        gaps.setdefault(r.resource_ref, {**(r.snapshot or {}), "present_in_current_data": False})
        gaps[r.resource_ref]["collected"] = True
    hyps = [hypothesis_dict(h) for h in s.scalars(select(WorkspaceHypothesis).where(
        WorkspaceHypothesis.workspace_id == ws.id).order_by(WorkspaceHypothesis.id))]
    notes = [note_dict(n) for n in s.scalars(select(ResearchNote).where(ResearchNote.workspace_id == ws.id).order_by(ResearchNote.id))]
    tasks = [task_dict(t) for t in s.scalars(select(ResearchTask).where(ResearchTask.workspace_id == ws.id).order_by(ResearchTask.id))]
    content = {
        "generated_at": utcnow().isoformat(timespec="seconds"),
        "workspace": {k: v for k, v in ws_dict(s, ws).items() if k in ("id", "title", "research_question", "description",
                                                                        "research_domain", "scope", "status")},
        "plants": [{"id": p.id, "name": p.accepted_name, "family": p.family} for p in plants_all if p.id in plant_ids],
        "compounds": [{"id": c.id, "name": c.name, "pubchem_cid": c.pubchem_cid, "formula": c.molecular_formula}
                      for c in compounds_all if c.id in compound_ids],
        "publications": [{"id": p.id, "pmid": p.pmid, "doi": p.doi, "year": p.year, "title": p.title}
                         for p in s.scalars(select(Publication).where(Publication.id.in_(pub_ids)).order_by(Publication.id))],
        "verified_observations": accepted, "unverified_collected_records": rejected,
        "indexed_records_not_collected": not_collected,
        "contradictions": [conflicts[k] for k in sorted(conflicts)], "gaps": [gaps[k] for k in sorted(gaps)],
        "hypotheses": hyps, "notes": notes,
        "tasks": {"completed": [t for t in tasks if t["status"] == TaskStatus.DONE],
                  "outstanding": [t for t in tasks if t["status"] != TaskStatus.DONE]},
        "limitations": LIMITATIONS,
    }
    analyses = list(s.scalars(select(WorkspaceResource).where(
        WorkspaceResource.workspace_id == ws.id,
        WorkspaceResource.resource_type == ResourceType.REPURPOSING_ANALYSIS).order_by(WorkspaceResource.id)))
    if analyses:  # key added only when present, so reports of workspaces without analyses hash as before
        from app.repurposing_workspace import snapshot_summary
        content["repurposing_analyses"] = [{"resource_id": r.id, **snapshot_summary(r)} for r in analyses]
    content = json.loads(json.dumps(content, default=str))
    fp = _fingerprint(s, content)
    report = ResearchReport(workspace_id=ws.id, generated_by=actor, markdown=render_markdown(content, fp),
                            content=content, dataset_fingerprint=fp)
    s.add(report)
    log(s, ws, "report_generated", f"Report with {len(accepted)} verified observations, {len(conflicts)} contradictions, "
                                   f"{len(gaps)} gaps, {len(hyps)} hypotheses", actor)
    return report


def render_markdown(c: dict, fp: dict) -> str:
    w = c["workspace"]
    L = [f"# {md(w['title'])}", "",
         f"*MOLECULAB research report · generated {c['generated_at']} UTC · status: {md(w['status'])}*", "",
         "## 1. Research question", "", md(w["research_question"]), ""]
    if w.get("description") or w.get("scope") or w.get("research_domain"):
        L += ["## 2. Scope and description", "",
              f"- **Domain:** {md(w.get('research_domain') or '—')}", f"- **Scope:** {md(w.get('scope') or '—')}",
              f"- **Description:** {md(w.get('description') or '—')}", ""]
    L += ["## 3. Methodology", "",
          "This report was compiled automatically from the MOLECULAB index. It reflects the **currently indexed "
          "dataset**, not the entirety of the published literature. Observations are evidence records collected in this "
          "investigation, each re-checked with MOLECULAB's rule-based claim verifier against its stored source excerpt. "
          "Contradictions and research gaps are computed by MOLECULAB's research engine for every collected plant and "
          "compound and are all listed. Hypotheses and notes are recorded by the researcher or the rule-based generator "
          "and are not findings.", "",
          "## 4. Collected plants and compounds", ""]
    L += [f"- *{md(p['name'])}* ({md(p['family'] or 'family unknown')}) [plant #{p['id']}]" for p in c["plants"]] or ["- none"]
    L += [f"- {md(x['name'])} — {md(x['formula'] or 'no formula')}, PubChem CID {x['pubchem_cid'] or 'not resolved'}"
          for x in c["compounds"]]
    L += ["", "## 5. Publications", ""]
    L += [f"- PMID {md(p['pmid'])} · DOI {md(p['doi'])} ({p['year']}): {md(p['title'])}" for p in c["publications"]] or ["- none collected"]
    L += ["", "## 6. Verified scientific observations", "",
          "Each observation below passed the claim verifier against the quoted excerpt. Verification checks the excerpt, "
          "not the full text, and is not expert review.", ""]
    for o in c["verified_observations"]:
        p = o["publication"]
        L += [f"- **{md(o['statement'])}** — {md(o['status'])} · evidence #{o['evidence_ids'][0]} · "
              f"{md(o['evidence_type'])} · PMID {md(p['pmid'])} · DOI {md(p['doi'])}",
              f"  > {md(o['excerpt'])}"]
    if not c["verified_observations"]:
        L.append("- No collected evidence record passed verification.")
    if c["unverified_collected_records"]:
        L += ["", "Collected records that **failed** verification (not presented as observations):", ""]
        L += [f"- evidence #{o['evidence_ids'][0]}: {md(o['explanation'])}" for o in c["unverified_collected_records"]]
    if c["indexed_records_not_collected"]:
        L += ["", f"Indexed records for collected plants that were **not** collected: "
                  + ", ".join(f"#{i}" for i in c["indexed_records_not_collected"])]
    L += ["", "## 7. Contradictions", ""]
    for x in c["contradictions"]:
        L += [f"### {md(x.get('compound', {}).get('name'))} in {md(x.get('plant', {}).get('accepted_name'))}"
              + (" (collected)" if x.get("collected") else ""), "",
              f"Assessment: **{md(x.get('assessment', 'unknown'))}**. {md(x.get('assessment_explanation', ''))}", ""]
        for f in x.get("findings", []):
            L += [f"- evidence #{f['evidence_id']} · PMID {md(f['publication']['pmid'])}: {md(f['observation'])} "
                  f"(part: {md(f['plant_part'] or 'not stated')}; origin: {md(f['geographic_origin'] or 'not stated')})",
                  f"  > {md(f['excerpt'])}"]
        L += ["", f"Missing context: {md('; '.join(x.get('missing_context', [])))}",
              "", md(x.get("interpretation_note", "")), ""]
    if not c["contradictions"]:
        L.append("No conflicting findings were detected in the indexed records for this scope.")
    L += ["", "## 8. Research gaps (dataset scope)", ""]
    for g in c["gaps"]:
        L.append(f"- [{md(g.get('kind'))}] {md(g.get('explanation'))}"
                 + (f" Evidence: {', '.join('#' + str(i) for i in g.get('evidence_ids', []))}." if g.get("evidence_ids") else "")
                 + (" (collected)" if g.get("collected") else ""))
    if not c["gaps"]:
        L.append("- No gap rule fired for this scope.")
    L += ["", "## 9. Hypotheses — UNVALIDATED", "",
          "Statuses describe investigation progress only. No hypothesis below is an established finding.", ""]
    for h in c["hypotheses"]:
        L += [f"### H{h['id']} ({md(h['origin'])}, status: {md(h['status'])})", "", f"> {md(h['statement'])}", "",
              f"- Causal attribution: {md(h['causal_attribution'])}",
              f"- Supporting evidence: {', '.join('#' + str(i) for i in h['supporting_evidence_ids']) or 'none linked'}",
              f"- Contradicting evidence: {', '.join('#' + str(i) for i in h['contradicting_evidence_ids']) or 'none linked'}"]
        L += [f"- Unresolved: {md(u)}" for u in h["unresolved_questions"]]
        L += [f"- Alternative explanation: {md(a)}" for a in h["alternative_explanations"]]
        L += [f"- Proposed validation: {md(v)}" for v in h["validation_methods"]]
        L.append("")
    if not c["hypotheses"]:
        L.append("No hypotheses recorded.")
    L += ["", "## 10. Researcher notes (researcher-authored; not verified evidence)", ""]
    L += [f"- [{md(n['category'])}] {md(n['body'])}" + (f" (re: {md(n['resource_type'])} {md(n['resource_ref'])})"
                                                        if n["resource_type"] else "") for n in c["notes"]] or ["- none"]
    L += ["", "## 11. Tasks", "", "Completed:"]
    L += [f"- {md(t['title'])}" for t in c["tasks"]["completed"]] or ["- none"]
    L += ["", "Outstanding:"]
    L += [f"- [{md(t['status'])}, {md(t['priority'])}] {md(t['title'])}" for t in c["tasks"]["outstanding"]] or ["- none"]
    if c.get("repurposing_analyses"):
        L += ["", "## 11b. Saved repurposing evidence inventories (historical snapshots)", "",
              "Each is a frozen snapshot of provider records as retrieved at the stated time; see its own report "
              "for the records. Inventories are not treatment recommendations and do not rank candidates.", ""]
        L += [f"- Analysis #{a['resource_id']}: {md(a['target'])} x {md(a['disease'])} — {a['candidate_count']} "
              f"candidates, retrieved {md(a['retrieved_from'])} to {md(a['retrieved_to'])}, fingerprint "
              f"`{a['content_fingerprint']}`, integrity {md(a['integrity'])}" for a in c["repurposing_analyses"]]
    L += ["", "## 12. Scientific limitations", ""] + [f"- {md(x)}" for x in c["limitations"]]
    L += ["", "## 13. Reproducibility", "",
          f"- Generated: {c['generated_at']} UTC by MOLECULAB {md(fp['app_version'])}, schema revision {md(fp['schema_revision'])}",
          f"- Dataset at generation: {fp['plants']} plants, {fp['compounds']} compounds, {fp['publications']} publications, "
          f"{fp['evidence_records']} evidence records (max evidence id {fp['max_evidence_id']})",
          f"- Content SHA-256 (excluding timestamp): `{fp['content_sha256']}`",
          "- Regenerating on the same dataset and workspace state yields the same content hash.", ""]
    return "\n".join(L)


def report_dict(r: ResearchReport, include_markdown: bool = True) -> dict:
    d = {"id": r.id, "workspace_id": r.workspace_id, "created_at": iso(r.created_at), "generated_by": r.generated_by,
         "dataset_fingerprint": r.dataset_fingerprint,
         "summary": {"verified_observations": len(r.content.get("verified_observations", [])),
                     "contradictions": len(r.content.get("contradictions", [])),
                     "gaps": len(r.content.get("gaps", [])), "hypotheses": len(r.content.get("hypotheses", [])),
                     "notes": len(r.content.get("notes", []))}}
    if include_markdown:
        d["markdown"] = r.markdown
        d["content"] = r.content
    return d
