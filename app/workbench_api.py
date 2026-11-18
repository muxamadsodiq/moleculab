"""Research workbench: an aggregation layer over existing workspace records. Mounted at /api/v1/workbench.

No tables of its own. Every read goes through the workspace ownership check in app.workspace_api (`_ws`) and
every write delegates to the existing workspace resource endpoints, so auth, archive read-only rules and the
activity log behave exactly as they do there.
"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import workspace as svc
from app import workspace_api as wsapi
from app.db import get_session
from app.formulation import evaluate_formulation
from app.models import (ExperimentPlan, Formulation, ResearchBoard, ResearchBoardObject, ResearchNote,
                        ResearchReport, ResearchTask, ResearchWorkspace, ResourceType, WorkspaceActivity,
                        WorkspaceHypothesis, WorkspaceResource)
from app.workspace_auth import current_researcher

DB = Annotated[Session, Depends(get_session)]
WHO = Annotated[str, Depends(current_researcher)]
router = APIRouter(tags=["research workbench"])

CONTEXT_CAP = 4000
#: repurposing analyses are created only by the server-side analysis, never attached generically.
ATTACHABLE = [t.value for t in ResourceType if t != ResourceType.REPURPOSING_ANALYSIS]
FORM_LINK = "#/formulations?id={id}"
BOARD_LINK = "#/boards?id={id}"


class AttachIn(BaseModel):
    ref_type: str = Field(min_length=1, max_length=40)
    ref_id: str | int
    label: str | None = Field(None, max_length=300)  # accepted for symmetry; the label is always resolved server-side
    note: str | None = Field(None, max_length=5000)


def _counts(s: Session, ws: ResearchWorkspace) -> dict:
    d = svc.ws_dict(s, ws)

    def n(m):
        return s.scalar(select(func.count()).select_from(m).where(m.workspace_id == ws.id))

    return {"resources": sum(d["resource_counts"].values()), "resources_by_type": d["resource_counts"],
            "notes": d["notes"], "hypotheses": d["hypotheses"], "reports": d["reports"],
            "tasks": n(ResearchTask), "outstanding_tasks": d["outstanding_tasks"],
            "formulations": n(Formulation), "experiments": n(ExperimentPlan), "boards": n(ResearchBoard)}


def _formulations(s, ws):
    return list(s.scalars(select(Formulation).where(Formulation.workspace_id == ws.id).order_by(Formulation.id)))


def _open_questions(s, ws) -> list[dict]:
    out = []
    for f in _formulations(s, ws):
        ev = evaluate_formulation(f)
        link = FORM_LINK.format(id=f.id)
        for name in ev["violated_mandatory"]:
            out.append({"kind": "violated_mandatory_constraint",
                        "text": f"{f.name}: mandatory constraint contradicted: {name}", "link": link})
        for name in ev["unresolved_mandatory"]:
            out.append({"kind": "unresolved_mandatory_constraint",
                        "text": f"{f.name}: mandatory constraint unresolved: {name}", "link": link})
    for r in s.scalars(select(WorkspaceResource).where(WorkspaceResource.workspace_id == ws.id,
                                                       WorkspaceResource.resource_type == ResourceType.GAP)):
        out.append({"kind": "research_gap", "text": r.label, "link": "#/research/gaps"})
    for r in s.scalars(select(WorkspaceResource).where(WorkspaceResource.workspace_id == ws.id,
                                                       WorkspaceResource.resource_type == ResourceType.CONTRADICTION)):
        out.append({"kind": "contradiction", "text": r.label, "link": "#/research/contradictions"})
    for n in s.scalars(select(ResearchNote).where(ResearchNote.workspace_id == ws.id,
                                                  ResearchNote.category == "question")):
        out.append({"kind": "researcher_question", "text": n.body[:300], "link": None})
    return out


@router.get("/investigations")
def list_investigations(s: DB, who: WHO, include_archived: bool = True, limit: int = Query(100, ge=1, le=200)):
    rows = list(s.scalars(select(ResearchWorkspace).where(ResearchWorkspace.owner == who)
                          .order_by(ResearchWorkspace.updated_at.desc(), ResearchWorkspace.id.desc())))
    if not include_archived:
        rows = [w for w in rows if w.status != "archived"]
    return [{"id": w.id, "title": w.title, "research_question": w.research_question, "status": w.status,
             "research_domain": w.research_domain, "updated_at": svc.iso(w.updated_at), "counts": _counts(s, w)}
            for w in rows[:limit]]


@router.get("/investigations/{investigation_id}/overview")
def overview(investigation_id: int, s: DB, who: WHO):
    ws = wsapi._ws(s, investigation_id, who)
    resources = [svc.resource_dict(s, r) for r in s.scalars(select(WorkspaceResource).where(
        WorkspaceResource.workspace_id == ws.id).order_by(WorkspaceResource.resource_type, WorkspaceResource.id))]
    by_type: dict[str, list] = {}
    for r in resources:
        by_type.setdefault(r["resource_type"], []).append(r)
    notes = [svc.note_dict(n) for n in s.scalars(select(ResearchNote).where(ResearchNote.workspace_id == ws.id)
                                                 .order_by(ResearchNote.id.desc()))]
    hyps = [svc.hypothesis_dict(h) for h in s.scalars(select(WorkspaceHypothesis).where(
        WorkspaceHypothesis.workspace_id == ws.id).order_by(WorkspaceHypothesis.id.desc()))]
    tasks = [svc.task_dict(t) for t in s.scalars(select(ResearchTask).where(ResearchTask.workspace_id == ws.id)
                                                 .order_by(ResearchTask.id.desc()))]
    reports = [svc.report_dict(r, include_markdown=False) for r in s.scalars(
        select(ResearchReport).where(ResearchReport.workspace_id == ws.id).order_by(ResearchReport.id.desc()))]
    activity = [{"id": a.id, "action": a.action, "detail": a.detail, "actor": a.actor, "at": svc.iso(a.created_at)}
                for a in s.scalars(select(WorkspaceActivity).where(WorkspaceActivity.workspace_id == ws.id)
                                   .order_by(WorkspaceActivity.created_at.desc(), WorkspaceActivity.id.desc()).limit(30))]
    forms = []
    for f in _formulations(s, ws):
        ev = evaluate_formulation(f)
        forms.append({"id": f.id, "name": f.name, "dosage_form": f.dosage_form, "status": f.status,
                      "research_question": f.research_question, "notes": f.notes,
                      "constraint_count": ev["constraint_count"], "status_counts": ev["status_counts"],
                      "unresolved_mandatory": ev["unresolved_mandatory"], "violated_mandatory": ev["violated_mandatory"],
                      "readiness_statement": ev["readiness_statement"], "link": FORM_LINK.format(id=f.id)})
    exps = [{"id": e.id, "title": e.title, "status": e.status, "formulation_id": e.formulation_id,
             "hypothesis": e.hypothesis, "rationale": e.rationale, "acceptance_criteria": e.acceptance_criteria,
             "limitations": e.limitations, "link": FORM_LINK.format(id=e.formulation_id) if e.formulation_id else "#/formulations"}
            for e in s.scalars(select(ExperimentPlan).where(ExperimentPlan.workspace_id == ws.id).order_by(ExperimentPlan.id))]
    boards = [{"id": b.id, "title": b.title, "description": b.description, "revision": b.revision,
               "object_count": len(b.objects), "link": BOARD_LINK.format(id=b.id)}
              for b in s.scalars(select(ResearchBoard).where(ResearchBoard.workspace_id == ws.id).order_by(ResearchBoard.id))]
    return {"workspace": svc.ws_dict(s, ws), "counts": _counts(s, ws), "resources": resources,
            "resources_by_type": by_type, "notes": notes, "hypotheses": hyps, "tasks": tasks, "reports": reports,
            "activity": activity, "formulations": forms, "experiments": exps, "boards": boards,
            "open_questions": _open_questions(s, ws), "attachable_types": ATTACHABLE,
            "read_only": ws.status == "archived"}


@router.post("/investigations/{investigation_id}/attach", status_code=201)
def attach(investigation_id: int, body: AttachIn, s: DB, who: WHO, response: Response):
    if body.ref_type not in ATTACHABLE:
        raise HTTPException(422, {"message": f"unknown ref_type {body.ref_type!r}", "accepted_ref_types": ATTACHABLE})
    # Delegates to the existing resource endpoint: ownership, archive guard, validation, dedupe and logging.
    return wsapi.add_resource(investigation_id, wsapi.ResourceIn(
        resource_type=ResourceType(body.ref_type), resource_ref=str(body.ref_id), researcher_note=body.note),
        s, who, response)


@router.delete("/investigations/{investigation_id}/attach/{resource_id}", status_code=204)
def detach(investigation_id: int, resource_id: int, s: DB, who: WHO):
    """Removes only the reference. The compound, evidence or other scientific record is untouched."""
    return wsapi.remove_resource(investigation_id, resource_id, s, who)


@router.get("/investigations/{investigation_id}/context")
def context(investigation_id: int, s: DB, who: WHO):
    ws = wsapi._ws(s, investigation_id, who)
    res = list(s.scalars(select(WorkspaceResource).where(WorkspaceResource.workspace_id == ws.id)
                         .order_by(WorkspaceResource.id)))

    def ids(t):
        return [{"id": r.resource_ref, "label": r.label[:80]} for r in res if r.resource_type == t]

    board_ids = [b.id for b in s.scalars(select(ResearchBoard).where(ResearchBoard.workspace_id == ws.id))]
    objs = list(s.scalars(select(ResearchBoardObject).where(ResearchBoardObject.board_id.in_(board_ids)))) if board_ids else []

    def pick(t):
        return [{"id": i, "label": l} for i, l in sorted({(str(o.ref_id), o.label[:80]) for o in objs
                                                          if o.ref_type == t and o.ref_id})]

    hyps = list(s.scalars(select(WorkspaceHypothesis).where(WorkspaceHypothesis.workspace_id == ws.id)
                          .order_by(WorkspaceHypothesis.id.desc()).limit(5)))
    ctx = {"investigation_id": ws.id, "title": ws.title[:120], "research_question": ws.research_question[:600],
           "status": ws.status, "domain": ws.research_domain,
           "compounds": ids("compound")[:15], "plants": ids("plant")[:10],
           "targets": pick("biological_target")[:10], "diseases": pick("disease")[:10],
           "open_questions": [q["text"][:160] for q in _open_questions(s, ws)][:8],
           "recent_hypotheses": [{"id": h.id, "origin": h.origin, "status": h.status, "statement": h.statement[:200]}
                                 for h in hyps],
           "provenance_note": "Researcher hypotheses are unvalidated. Notes are researcher-authored, not verified evidence."}
    # Shrink lists until the serialised block fits the cap; the research question is never dropped.
    for key in ("recent_hypotheses", "open_questions", "compounds", "plants", "targets", "diseases"):
        while len(json.dumps(ctx)) > CONTEXT_CAP and ctx[key]:
            ctx[key].pop()
    return ctx
