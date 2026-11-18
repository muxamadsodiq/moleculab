"""Research workspace endpoints. Mounted at /api/workspaces (project convention) and /api/v1/workspaces.

Every endpoint resolves the workspace through `_ws`, which enforces ownership: a workspace owned by
someone else is reported as 404, exactly like a missing one.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import workspace as svc
from app.db import get_session
from app.models import (CausalAttribution, HypothesisStatus, NoteCategory, ResearchNote, ResearchReport, ResearchTask,
                        ResearchWorkspace, ResourceType, TaskPriority, TaskStatus, WorkspaceActivity,
                        WorkspaceHypothesis, WorkspaceResource, WorkspaceStatus, utcnow)
from app.research import check_hypothesis
from app.workspace_auth import access_mode, current_researcher

DB = Annotated[Session, Depends(get_session)]
WHO = Annotated[str, Depends(current_researcher)]
router = APIRouter(tags=["research workspace"])


def _ws(s: Session, workspace_id: int, who: str) -> ResearchWorkspace:
    ws = s.get(ResearchWorkspace, workspace_id)
    if ws is None or ws.owner != who:
        raise HTTPException(404, "workspace not found")
    return ws


def _child(s: Session, model, obj_id: int, ws: ResearchWorkspace):
    obj = s.get(model, obj_id)
    if obj is None or obj.workspace_id != ws.id:
        raise HTTPException(404, f"{model.__tablename__.replace('_', ' ')} not found in this workspace")
    return obj


def _commit(s: Session):
    try:
        s.commit()
    except Exception:
        s.rollback()
        raise


def _err(e: svc.WorkspaceError):
    raise HTTPException(e.status, str(e))


Text = Annotated[str, Field(min_length=1, max_length=10000)]


# ---------------------------------------------------------------- workspaces

class WorkspaceIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    research_question: str = Field(min_length=10, max_length=2000)
    description: str | None = Field(None, max_length=10000)
    research_domain: str | None = Field(None, max_length=120)
    scope: str | None = Field(None, max_length=5000)
    status: WorkspaceStatus = WorkspaceStatus.PLANNING

    @field_validator("status")
    @classmethod
    def _not_archived(cls, v):
        if v == WorkspaceStatus.ARCHIVED:
            raise ValueError("create the workspace first, then archive it")
        return v


class WorkspacePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=3, max_length=200)
    research_question: str | None = Field(None, min_length=10, max_length=2000)
    description: str | None = Field(None, max_length=10000)
    research_domain: str | None = Field(None, max_length=120)
    scope: str | None = Field(None, max_length=5000)
    status: WorkspaceStatus | None = None


@router.get("/access")
def access(who: WHO):
    return {"mode": access_mode(), "researcher": who,
            "private": access_mode() == "token",
            "note": "Local single-user mode: workspaces are NOT private; anyone with access to this machine can read "
                    "and modify them." if access_mode() != "token" else "Workspaces are visible only to their owner's token."}


@router.post("", status_code=201)
def create_workspace(body: WorkspaceIn, s: DB, who: WHO):
    ws = ResearchWorkspace(owner=who, title=body.title.strip(), research_question=body.research_question.strip(),
                           description=body.description, research_domain=body.research_domain, scope=body.scope,
                           status=body.status)
    s.add(ws)
    s.flush()
    svc.log(s, ws, "investigation_created", f"{ws.title} — {ws.research_question}", who)
    _commit(s)
    return svc.ws_dict(s, ws)


@router.get("")
def list_workspaces(s: DB, who: WHO, response: Response, status: WorkspaceStatus | None = None,
                    include_archived: bool = True, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    stmt = select(ResearchWorkspace).where(ResearchWorkspace.owner == who)
    if status:
        stmt = stmt.where(ResearchWorkspace.status == status)
    if not include_archived:
        stmt = stmt.where(ResearchWorkspace.status != WorkspaceStatus.ARCHIVED)
    rows = list(s.scalars(stmt.order_by(ResearchWorkspace.updated_at.desc(), ResearchWorkspace.id.desc())))
    response.headers["X-Total-Count"] = str(len(rows))
    return [svc.ws_dict(s, w) for w in rows[offset:offset + limit]]


@router.get("/{workspace_id}")
def get_workspace(workspace_id: int, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    recent = [{"action": a.action, "detail": a.detail, "at": svc.iso(a.created_at)} for a in s.scalars(
        select(WorkspaceActivity).where(WorkspaceActivity.workspace_id == ws.id)
        .order_by(WorkspaceActivity.created_at.desc(), WorkspaceActivity.id.desc()).limit(8))]
    return {**svc.ws_dict(s, ws), "recent_activity": recent}


@router.patch("/{workspace_id}")
def update_workspace(workspace_id: int, body: WorkspacePatch, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    changes = body.model_dump(exclude_unset=True)
    if ws.status == WorkspaceStatus.ARCHIVED and set(changes) - {"status"}:
        raise HTTPException(409, "archived investigations are read-only; reopen it first")
    new_status = changes.pop("status", None)
    for k, v in changes.items():
        setattr(ws, k, v.strip() if isinstance(v, str) else v)
    if changes:
        svc.log(s, ws, "investigation_updated", "Updated " + ", ".join(sorted(changes)), who)
    if new_status and new_status != ws.status:
        if new_status == WorkspaceStatus.ARCHIVED:
            ws.status_before_archive, ws.archived_at = ws.status, utcnow()
            svc.log(s, ws, "investigation_archived", "Archived", who)
        elif ws.status == WorkspaceStatus.ARCHIVED:
            ws.archived_at = None
            svc.log(s, ws, "investigation_reopened", f"Reopened as {new_status}", who)
        else:
            svc.log(s, ws, "status_changed", f"{ws.status} → {new_status}", who)
        ws.status = new_status
    _commit(s)
    return svc.ws_dict(s, ws)


@router.post("/{workspace_id}/archive")
def archive_workspace(workspace_id: int, s: DB, who: WHO):
    return update_workspace(workspace_id, WorkspacePatch(status=WorkspaceStatus.ARCHIVED), s, who)


@router.post("/{workspace_id}/reopen")
def reopen_workspace(workspace_id: int, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    if ws.status != WorkspaceStatus.ARCHIVED:
        raise HTTPException(409, "investigation is not archived")
    return update_workspace(workspace_id, WorkspacePatch(status=ws.status_before_archive or WorkspaceStatus.ACTIVE), s, who)


@router.delete("/{workspace_id}", status_code=204)
def delete_workspace(workspace_id: int, s: DB, who: WHO):
    """Deletion is permitted only for archived investigations (archive first). It removes the workspace and its
    own records (resources, notes, hypotheses, tasks, activity, reports), never shared scientific records."""
    ws = _ws(s, workspace_id, who)
    if ws.status != WorkspaceStatus.ARCHIVED:
        raise HTTPException(409, "archive the investigation before deleting it")
    s.delete(ws)
    _commit(s)
    return Response(status_code=204)


# ---------------------------------------------------------------- resources

class ResourceIn(BaseModel):
    resource_type: ResourceType
    resource_ref: str = Field(min_length=1, max_length=200)
    researcher_note: str | None = Field(None, max_length=5000)


class CollectPlantIn(BaseModel):
    plant_id: int = Field(ge=1)
    include: list[str] = Field(default=["compounds", "publications"])

    @field_validator("include")
    @classmethod
    def _inc(cls, v):
        bad = set(v) - {"compounds", "publications", "evidence"}
        if bad:
            raise ValueError(f"unknown include values: {sorted(bad)}")
        return v


class ResourcePatch(BaseModel):
    researcher_note: str | None = Field(None, max_length=5000)


def _writable(ws: ResearchWorkspace):
    if ws.status == WorkspaceStatus.ARCHIVED:
        raise HTTPException(409, "archived investigations are read-only; reopen it first")


@router.post("/{workspace_id}/resources", status_code=201)
def add_resource(workspace_id: int, body: ResourceIn, s: DB, who: WHO, response: Response):
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    try:
        r, created = svc.add_resource(s, ws, body.resource_type, body.resource_ref, body.researcher_note, who)
    except svc.WorkspaceError as e:
        _err(e)
    _commit(s)
    if not created:
        response.status_code = 200
    return {**svc.resource_dict(s, r), "created": created}


@router.post("/{workspace_id}/collect-plant", status_code=201)
def collect_plant(workspace_id: int, body: CollectPlantIn, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    try:
        added = svc.collect_plant(s, ws, body.plant_id, set(body.include), who)
    except svc.WorkspaceError as e:
        _err(e)
    _commit(s)
    return {"added": added}


@router.get("/{workspace_id}/resources")
def list_resources(workspace_id: int, s: DB, who: WHO, response: Response, resource_type: ResourceType | None = None,
                   q: str | None = Query(None, max_length=200), offset: int = Query(0, ge=0),
                   limit: int = Query(200, ge=1, le=1000)):
    ws = _ws(s, workspace_id, who)
    stmt = select(WorkspaceResource).where(WorkspaceResource.workspace_id == ws.id)
    if resource_type:
        stmt = stmt.where(WorkspaceResource.resource_type == resource_type)
    rows = list(s.scalars(stmt.order_by(WorkspaceResource.resource_type, WorkspaceResource.id)))
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in (r.label or "").lower() or needle in (r.researcher_note or "").lower()
                or needle == r.resource_ref.lower()]
    response.headers["X-Total-Count"] = str(len(rows))
    return [svc.resource_dict(s, r) for r in rows[offset:offset + limit]]


@router.patch("/{workspace_id}/resources/{resource_id}")
def update_resource(workspace_id: int, resource_id: int, body: ResourcePatch, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    r = _child(s, WorkspaceResource, resource_id, ws)
    r.researcher_note = body.researcher_note
    svc.log(s, ws, "resource_annotated", r.label, who)
    _commit(s)
    return svc.resource_dict(s, r)


@router.delete("/{workspace_id}/resources/{resource_id}", status_code=204)
def remove_resource(workspace_id: int, resource_id: int, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    r = _child(s, WorkspaceResource, resource_id, ws)
    svc.log(s, ws, "resource_removed", r.label, who)
    s.delete(r)
    _commit(s)
    return Response(status_code=204)


# ---------------------------------------------------------------- notes

class NoteIn(BaseModel):
    category: NoteCategory
    body: Text
    resource_type: ResourceType | None = None
    resource_ref: str | None = Field(None, max_length=200)


class NotePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: NoteCategory | None = None
    body: str | None = Field(None, min_length=1, max_length=10000)


@router.post("/{workspace_id}/notes", status_code=201)
def create_note(workspace_id: int, body: NoteIn, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    try:
        svc.validate_link(s, body.resource_type, body.resource_ref, ws)
    except svc.WorkspaceError as e:
        _err(e)
    n = ResearchNote(workspace_id=ws.id, category=body.category, body=body.body, resource_type=body.resource_type,
                     resource_ref=body.resource_ref, author=who)
    s.add(n)
    svc.log(s, ws, "note_created", f"[{body.category}] {body.body[:120]}", who)
    _commit(s)
    return svc.note_dict(n)


@router.get("/{workspace_id}/notes")
def list_notes(workspace_id: int, s: DB, who: WHO, category: NoteCategory | None = None,
               resource_type: ResourceType | None = None, resource_ref: str | None = None):
    ws = _ws(s, workspace_id, who)
    stmt = select(ResearchNote).where(ResearchNote.workspace_id == ws.id)
    if category:
        stmt = stmt.where(ResearchNote.category == category)
    if resource_type:
        stmt = stmt.where(ResearchNote.resource_type == resource_type)
    if resource_ref:
        stmt = stmt.where(ResearchNote.resource_ref == resource_ref)
    return [svc.note_dict(n) for n in s.scalars(stmt.order_by(ResearchNote.id.desc()))]


@router.patch("/{workspace_id}/notes/{note_id}")
def update_note(workspace_id: int, note_id: int, body: NotePatch, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    n = _child(s, ResearchNote, note_id, ws)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(n, k, v)
    svc.log(s, ws, "note_updated", f"note {n.id}", who)
    _commit(s)
    return svc.note_dict(n)


@router.delete("/{workspace_id}/notes/{note_id}", status_code=204)
def delete_note(workspace_id: int, note_id: int, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    n = _child(s, ResearchNote, note_id, ws)
    svc.log(s, ws, "note_deleted", f"note {n.id}", who)
    s.delete(n)
    _commit(s)
    return Response(status_code=204)


# ---------------------------------------------------------------- hypotheses

class GenerateIn(BaseModel):
    plant_id: int | None = Field(None, ge=1)
    compound_id: int | None = Field(None, ge=1)
    question: str | None = Field(None, min_length=3, max_length=500)


class HypothesisIn(BaseModel):
    origin: str = Field(pattern="^(generated|researcher)$")
    statement: str = Field(min_length=10, max_length=2000)
    source: GenerateIn | None = None  # required for generated
    supporting_evidence_ids: list[int] = Field(default=[], max_length=100)
    contradicting_evidence_ids: list[int] = Field(default=[], max_length=100)
    alternative_explanations: list[Annotated[str, Field(max_length=1000)]] = Field(default=[], max_length=30)
    validation_methods: list[Annotated[str, Field(max_length=1000)]] = Field(default=[], max_length=30)
    causal_attribution: CausalAttribution = CausalAttribution.NOT_APPLICABLE

    @model_validator(mode="after")
    def _src(self):
        if self.origin == "generated" and self.source is None:
            raise ValueError("generated hypotheses need the generator source (plant_id / compound_id / question)")
        return self


class HypothesisPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str | None = Field(None, min_length=10, max_length=2000)
    status: HypothesisStatus | None = None
    causal_attribution: CausalAttribution | None = None
    supporting_evidence_ids: list[int] | None = Field(None, max_length=100)
    contradicting_evidence_ids: list[int] | None = Field(None, max_length=100)
    alternative_explanations: list[Annotated[str, Field(max_length=1000)]] | None = Field(None, max_length=30)
    validation_methods: list[Annotated[str, Field(max_length=1000)]] | None = Field(None, max_length=30)


def _researcher_statement(statement: str) -> str:
    st = statement.strip()
    if not st.lower().startswith("hypothesis"):
        st = "Hypothesis (researcher, unvalidated): " + st
    return st


@router.post("/{workspace_id}/hypotheses/generate")
def generate(workspace_id: int, body: GenerateIn, s: DB, who: WHO):
    """Run the v0.4 generator for this investigation (nothing is saved)."""
    ws = _ws(s, workspace_id, who)
    try:
        return svc.generate_candidates(s, ws, body.plant_id, body.compound_id, body.question)
    except svc.WorkspaceError as e:
        _err(e)


@router.post("/{workspace_id}/hypotheses", status_code=201)
def create_hypothesis(workspace_id: int, body: HypothesisIn, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    try:
        if body.origin == "generated":
            h = svc.save_generated(s, ws, body.source.model_dump(), body.statement, who)
        else:
            statement = _researcher_statement(body.statement)
            reason = check_hypothesis(statement.replace("Hypothesis (researcher, unvalidated): ", "Hypothesis (unvalidated): "))
            if reason and "beyond the indexed dataset" in reason:
                raise svc.WorkspaceError(f"statement rejected: {reason}")
            h = WorkspaceHypothesis(workspace_id=ws.id, origin="researcher", statement=statement, category="researcher",
                                    status=HypothesisStatus.PROPOSED, causal_attribution=body.causal_attribution,
                                    supporting_evidence_ids=svc.validate_evidence_ids(s, body.supporting_evidence_ids),
                                    contradicting_evidence_ids=svc.validate_evidence_ids(s, body.contradicting_evidence_ids),
                                    alternative_explanations=body.alternative_explanations,
                                    validation_methods=body.validation_methods, author=who)
            svc.check_status_rules(s, h)
            s.add(h)
            svc.log(s, ws, "hypothesis_recorded", h.statement, who)
    except svc.WorkspaceError as e:
        s.rollback()
        _err(e)
    _commit(s)
    return svc.hypothesis_dict(h)


@router.get("/{workspace_id}/hypotheses")
def list_hypotheses(workspace_id: int, s: DB, who: WHO, status: HypothesisStatus | None = None):
    ws = _ws(s, workspace_id, who)
    stmt = select(WorkspaceHypothesis).where(WorkspaceHypothesis.workspace_id == ws.id)
    if status:
        stmt = stmt.where(WorkspaceHypothesis.status == status)
    return [svc.hypothesis_dict(h) for h in s.scalars(stmt.order_by(WorkspaceHypothesis.id))]


@router.patch("/{workspace_id}/hypotheses/{hypothesis_id}")
def update_hypothesis(workspace_id: int, hypothesis_id: int, body: HypothesisPatch, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    h = _child(s, WorkspaceHypothesis, hypothesis_id, ws)
    changes = body.model_dump(exclude_unset=True)
    try:
        if "statement" in changes:
            if h.origin == "generated":
                raise svc.WorkspaceError("generated hypotheses keep the generator's wording; record a researcher "
                                         "hypothesis to rephrase it")
            changes["statement"] = _researcher_statement(changes["statement"])
        for key in ("supporting_evidence_ids", "contradicting_evidence_ids"):
            if key in changes:
                changes[key] = svc.validate_evidence_ids(s, changes[key])
        for k, v in changes.items():
            setattr(h, k, v)
        svc.check_status_rules(s, h)
    except svc.WorkspaceError as e:
        s.rollback()
        _err(e)
    svc.log(s, ws, "hypothesis_updated", f"H{h.id}: " + ", ".join(f"{k}={v}" for k, v in changes.items()
                                                                   if k in ("status", "causal_attribution")) or f"H{h.id} edited", who)
    _commit(s)
    return svc.hypothesis_dict(h)


@router.delete("/{workspace_id}/hypotheses/{hypothesis_id}", status_code=204)
def delete_hypothesis(workspace_id: int, hypothesis_id: int, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    h = _child(s, WorkspaceHypothesis, hypothesis_id, ws)
    svc.log(s, ws, "hypothesis_deleted", h.statement, who)
    s.delete(h)
    _commit(s)
    return Response(status_code=204)


# ---------------------------------------------------------------- tasks

class RelatedRef(BaseModel):
    resource_type: ResourceType
    resource_ref: str = Field(min_length=1, max_length=200)


class TaskIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(None, max_length=5000)
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.OPEN
    due_date: date | None = None
    related_resources: list[RelatedRef] = Field(default=[], max_length=50)


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=3, max_length=200)
    description: str | None = Field(None, max_length=5000)
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    due_date: date | None = None
    related_resources: list[RelatedRef] | None = Field(None, max_length=50)


def _validate_related(s: Session, refs: list[RelatedRef], ws: ResearchWorkspace | None = None) -> list[dict]:
    out = []
    for r in refs:
        svc.validate_link(s, r.resource_type, r.resource_ref, ws)
        out.append({"resource_type": r.resource_type.value, "resource_ref": r.resource_ref})
    return out


@router.post("/{workspace_id}/tasks", status_code=201)
def create_task(workspace_id: int, body: TaskIn, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    try:
        related = _validate_related(s, body.related_resources, ws)
    except svc.WorkspaceError as e:
        _err(e)
    t = ResearchTask(workspace_id=ws.id, title=body.title, description=body.description, priority=body.priority,
                     status=body.status, due_date=body.due_date.isoformat() if body.due_date else None,
                     related_resources=related, completed_at=utcnow() if body.status == TaskStatus.DONE else None)
    s.add(t)
    svc.log(s, ws, "task_created", t.title, who)
    _commit(s)
    return svc.task_dict(t)


@router.get("/{workspace_id}/tasks")
def list_tasks(workspace_id: int, s: DB, who: WHO, status: TaskStatus | None = None, outstanding: bool = False):
    ws = _ws(s, workspace_id, who)
    stmt = select(ResearchTask).where(ResearchTask.workspace_id == ws.id)
    if status:
        stmt = stmt.where(ResearchTask.status == status)
    if outstanding:
        stmt = stmt.where(ResearchTask.status != TaskStatus.DONE)
    return [svc.task_dict(t) for t in s.scalars(stmt.order_by(ResearchTask.id))]


@router.patch("/{workspace_id}/tasks/{task_id}")
def update_task(workspace_id: int, task_id: int, body: TaskPatch, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    t = _child(s, ResearchTask, task_id, ws)
    changes = body.model_dump(exclude_unset=True)
    try:
        if "related_resources" in changes:
            changes["related_resources"] = _validate_related(s, body.related_resources or [], ws)
    except svc.WorkspaceError as e:
        _err(e)
    if "due_date" in changes and changes["due_date"] is not None:
        changes["due_date"] = changes["due_date"].isoformat()
    was_done = t.status == TaskStatus.DONE
    for k, v in changes.items():
        setattr(t, k, v)
    if t.status == TaskStatus.DONE and not was_done:
        t.completed_at = utcnow()
        svc.log(s, ws, "task_completed", t.title, who)
    elif t.status != TaskStatus.DONE and was_done:
        t.completed_at = None
        svc.log(s, ws, "task_reopened", t.title, who)
    else:
        svc.log(s, ws, "task_updated", t.title, who)
    _commit(s)
    return svc.task_dict(t)


@router.delete("/{workspace_id}/tasks/{task_id}", status_code=204)
def delete_task(workspace_id: int, task_id: int, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    t = _child(s, ResearchTask, task_id, ws)
    svc.log(s, ws, "task_deleted", t.title, who)
    s.delete(t)
    _commit(s)
    return Response(status_code=204)


# ---------------------------------------------------------------- repurposing analyses (v1.0)

class RepurposingAnalysisIn(BaseModel):
    """Same inputs as POST /api/v1/repurposing/analyses. The server runs the analysis and saves the result;
    analyses are never accepted as client-supplied JSON."""
    target: str = Field(min_length=1, max_length=200)
    disease: str = Field(min_length=1, max_length=200)
    include_chembl: bool = True
    include_activity_counts: bool = True
    include_registered_studies: bool = False
    studies_per_candidate: int = Field(10, ge=1, le=50)
    association_rows: int = Field(200, ge=1, le=1000)
    max_candidates: int = Field(100, ge=1, le=500)
    researcher_note: str | None = Field(None, max_length=5000)


@router.post("/{workspace_id}/repurposing-analyses", status_code=201)
def save_repurposing_analysis(workspace_id: int, body: RepurposingAnalysisIn, s: DB, who: WHO, response: Response):
    from app import repurposing, repurposing_api, repurposing_workspace as rw
    ws = _ws(s, workspace_id, who)
    _writable(ws)
    cfg = repurposing.RepurposingConfig(
        target_query=body.target, disease_query=body.disease, include_chembl=body.include_chembl,
        include_activity_counts=body.include_activity_counts, include_registered_studies=body.include_registered_studies,
        studies_per_candidate=body.studies_per_candidate, association_rows=body.association_rows,
        max_candidates=body.max_candidates)
    try:
        analysis = repurposing.analyse(cfg, **repurposing_api._clients())
    except repurposing.RepurposingError as e:
        raise HTTPException(e.status, {"error": str(e), **e.detail})
    try:
        r, created = rw.save_analysis(s, ws, analysis, who)
        if created and body.researcher_note:
            r.researcher_note = body.researcher_note
    except svc.WorkspaceError as e:
        _err(e)
    _commit(s)
    if not created:
        response.status_code = 200
    return {**rw.saved_dict(r), "created": created,
            "duplicate_of_existing": None if created else r.id}


@router.get("/{workspace_id}/repurposing-analyses")
def list_repurposing_analyses(workspace_id: int, s: DB, who: WHO):
    from app import repurposing_workspace as rw
    ws = _ws(s, workspace_id, who)
    return [rw.saved_dict(r, full=False) for r in rw.list_saved(s, ws)]


@router.get("/{workspace_id}/repurposing-analyses/compare")
def compare_repurposing_analyses(workspace_id: int, s: DB, who: WHO, base: int = Query(..., ge=1),
                                 other: int = Query(..., ge=1)):
    from app import repurposing_workspace as rw
    ws = _ws(s, workspace_id, who)
    try:
        return rw.compare(rw.get_saved(s, ws, base), rw.get_saved(s, ws, other))
    except svc.WorkspaceError as e:
        _err(e)


@router.get("/{workspace_id}/repurposing-analyses/{analysis_id}")
def get_repurposing_analysis(workspace_id: int, analysis_id: int, s: DB, who: WHO):
    from app import repurposing_workspace as rw
    ws = _ws(s, workspace_id, who)
    try:
        return rw.saved_dict(rw.get_saved(s, ws, analysis_id))
    except svc.WorkspaceError as e:
        _err(e)


@router.get("/{workspace_id}/repurposing-analyses/{analysis_id}/report")
def repurposing_analysis_report(workspace_id: int, analysis_id: int, s: DB, who: WHO):
    from app import repurposing_workspace as rw
    ws = _ws(s, workspace_id, who)
    try:
        r = rw.get_saved(s, ws, analysis_id)
    except svc.WorkspaceError as e:
        _err(e)
    return PlainTextResponse(rw.report_markdown(s, ws, r), media_type="text/markdown; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="moleculab-repurposing-{ws.id}-{r.id}.md"',
        "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- timeline & reports

@router.get("/{workspace_id}/timeline")
def timeline(workspace_id: int, s: DB, who: WHO, response: Response, offset: int = Query(0, ge=0),
             limit: int = Query(200, ge=1, le=1000)):
    ws = _ws(s, workspace_id, who)
    rows = list(s.scalars(select(WorkspaceActivity).where(WorkspaceActivity.workspace_id == ws.id)
                          .order_by(WorkspaceActivity.created_at, WorkspaceActivity.id)))
    response.headers["X-Total-Count"] = str(len(rows))
    return [{"id": a.id, "action": a.action, "detail": a.detail, "actor": a.actor, "at": svc.iso(a.created_at)}
            for a in rows[offset:offset + limit]]


@router.post("/{workspace_id}/reports", status_code=201)
def create_report(workspace_id: int, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    r = svc.build_report(s, ws, who)
    _commit(s)
    return svc.report_dict(r)


@router.get("/{workspace_id}/reports")
def list_reports(workspace_id: int, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    return [svc.report_dict(r, include_markdown=False) for r in s.scalars(
        select(ResearchReport).where(ResearchReport.workspace_id == ws.id).order_by(ResearchReport.id.desc()))]


@router.get("/{workspace_id}/reports/{report_id}")
def get_report(workspace_id: int, report_id: int, s: DB, who: WHO):
    ws = _ws(s, workspace_id, who)
    return svc.report_dict(_child(s, ResearchReport, report_id, ws))


@router.get("/{workspace_id}/reports/{report_id}/export")
def export_report(workspace_id: int, report_id: int, s: DB, who: WHO,
                  format: str = Query("markdown", pattern="^(markdown|md)$")):
    ws = _ws(s, workspace_id, who)
    r = _child(s, ResearchReport, report_id, ws)
    slug = re.sub(r"[^a-z0-9]+", "-", ws.title.lower()).strip("-")[:60] or "investigation"
    return PlainTextResponse(r.markdown, media_type="text/markdown; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="moleculab-{slug}-report-{r.id}.md"',
        "X-Content-Type-Options": "nosniff"})
