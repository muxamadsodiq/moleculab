"""Research board API. Mounted at /api/v1/boards.

A board object POINTS at a real record (`ref_type` + `ref_id`); it never copies the science. Board
edges are the researcher's own reasoning and are labelled `researcher_asserted` so they can never be
mistaken for a provider-asserted relationship.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.models import (BoardObjectType, ResearchBoard, ResearchBoardEdge, ResearchBoardObject)

DB = Annotated[Session, Depends(get_session)]
router = APIRouter(tags=["research board"])

#: Where each pointer type resolves to in the UI, so an object can open its real record.
REF_ROUTES = {
    "compound": "#/compound/{id}", "molecular_structure": "#/studio",
    "evidence_record": "#/evidence/{id}", "publication": "#/publication/{id}",
    "formulation": "#/formulations?id={id}", "research_constraint": "#/formulations?id={id}",
    "experiment_plan": "#/formulations?id={id}", "material": "#/formulations",
    "disease": "#/diseases?id={id}", "biological_target": "#/targets?id={id}",
}


class BoardIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    workspace_id: int | None = None
    description: str | None = Field(default=None, max_length=4000)


class ObjectIn(BaseModel):
    object_type: BoardObjectType
    label: str = Field(min_length=1, max_length=300)
    ref_type: str | None = Field(default=None, max_length=40)
    ref_id: str | None = Field(default=None, max_length=80)
    content: dict | None = None
    x: float = 0.0
    y: float = 0.0
    width: float = Field(default=240.0, gt=20, le=2000)
    height: float = Field(default=140.0, gt=20, le=2000)


class ObjectPatch(BaseModel):
    label: str | None = Field(default=None, max_length=300)
    content: dict | None = None
    x: float | None = None
    y: float | None = None
    width: float | None = Field(default=None, gt=20, le=2000)
    height: float | None = Field(default=None, gt=20, le=2000)


class EdgeIn(BaseModel):
    from_object_id: int
    to_object_id: int
    label: str | None = Field(default=None, max_length=200)


def _board(session: Session, board_id: int) -> ResearchBoard:
    board = session.scalars(
        select(ResearchBoard)
        .options(selectinload(ResearchBoard.objects), selectinload(ResearchBoard.edges))
        .where(ResearchBoard.id == board_id)).first()
    if board is None:
        raise HTTPException(404, "board not found")
    return board


def _object_dict(o: ResearchBoardObject) -> dict:
    route = REF_ROUTES.get(o.ref_type or "")
    return {
        "id": o.id, "object_type": o.object_type, "label": o.label,
        "ref_type": o.ref_type, "ref_id": o.ref_id,
        "record_link": route.format(id=o.ref_id) if route and o.ref_id else None,
        "content": o.content, "x": o.x, "y": o.y, "width": o.width, "height": o.height,
        "group_id": o.group_id,
    }


@router.get("/object-types")
def object_types() -> dict:
    return {
        "types": [t.value for t in BoardObjectType],
        "linkable_ref_types": sorted(REF_ROUTES),
        "notes": {
            "pointers_not_copies":
                "A board object stores a reference to a record, not a copy of it. Opening the "
                "object navigates to the underlying record, so the board cannot drift out of step "
                "with the data.",
            "edges":
                "Board edges are researcher-asserted reasoning. They are not provider-asserted "
                "relationships and carry no evidential weight.",
            "hypothesis_objects":
                "A hypothesis placed on a board remains a hypothesis. Nothing on a board becomes "
                "verified evidence by being drawn.",
        },
    }


@router.get("")
def list_boards(session: DB, workspace_id: int | None = None) -> dict:
    stmt = select(ResearchBoard).order_by(ResearchBoard.id.desc())
    if workspace_id:
        stmt = stmt.where(ResearchBoard.workspace_id == workspace_id)
    rows = session.scalars(stmt).all()
    return {"count": len(rows), "items": [{
        "id": b.id, "title": b.title, "workspace_id": b.workspace_id,
        "revision": b.revision, "object_count": len(b.objects), "edge_count": len(b.edges),
    } for b in rows]}


@router.post("", status_code=201)
def create_board(body: Annotated[BoardIn, Body()], session: DB) -> dict:
    board = ResearchBoard(**body.model_dump())
    session.add(board)
    session.commit()
    return {"id": board.id, "title": board.title}


@router.get("/{board_id}")
def get_board(board_id: int, session: DB) -> dict:
    board = _board(session, board_id)
    return {
        "id": board.id, "title": board.title, "description": board.description,
        "workspace_id": board.workspace_id, "revision": board.revision,
        "objects": [_object_dict(o) for o in board.objects],
        "edges": [{"id": e.id, "from": e.from_object_id, "to": e.to_object_id,
                   "label": e.label, "relationship_kind": e.relationship_kind}
                  for e in board.edges],
        "edge_note": ("Edges on a board are researcher-asserted connections, not evidence of a "
                      "relationship in any database."),
    }


@router.post("/{board_id}/objects", status_code=201)
def add_object(board_id: int, body: Annotated[ObjectIn, Body()], session: DB) -> dict:
    board = _board(session, board_id)
    if body.ref_type and body.ref_type not in REF_ROUTES and body.ref_type != "simulation_result":
        raise HTTPException(422, f"unknown ref_type {body.ref_type!r}; "
                                 f"known: {sorted(REF_ROUTES)}")
    obj = ResearchBoardObject(board_id=board_id, **body.model_dump())
    session.add(obj)
    board.revision += 1
    session.commit()
    return _object_dict(obj)


@router.patch("/objects/{object_id}")
def update_object(object_id: int, body: Annotated[ObjectPatch, Body()], session: DB) -> dict:
    obj = session.get(ResearchBoardObject, object_id)
    if obj is None:
        raise HTTPException(404, "object not found")
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(obj, key, value)
    board = session.get(ResearchBoard, obj.board_id)
    if board:
        board.revision += 1
    session.commit()
    return _object_dict(obj)


@router.delete("/objects/{object_id}", status_code=204)
def delete_object(object_id: int, session: DB) -> None:
    """Removes the object from the board. The record it pointed at is untouched."""
    obj = session.get(ResearchBoardObject, object_id)
    if obj is None:
        raise HTTPException(404, "object not found")
    session.delete(obj)
    session.commit()


@router.post("/{board_id}/edges", status_code=201)
def add_edge(board_id: int, body: Annotated[EdgeIn, Body()], session: DB) -> dict:
    _board(session, board_id)
    if body.from_object_id == body.to_object_id:
        raise HTTPException(422, "an object cannot be connected to itself")
    for oid in (body.from_object_id, body.to_object_id):
        obj = session.get(ResearchBoardObject, oid)
        if obj is None or obj.board_id != board_id:
            raise HTTPException(404, f"object {oid} is not on this board")
    edge = ResearchBoardEdge(board_id=board_id, **body.model_dump())
    session.add(edge)
    session.commit()
    return {"id": edge.id, "relationship_kind": edge.relationship_kind}


@router.delete("/edges/{edge_id}", status_code=204)
def delete_edge(edge_id: int, session: DB) -> None:
    edge = session.get(ResearchBoardEdge, edge_id)
    if edge is None:
        raise HTTPException(404, "edge not found")
    session.delete(edge)
    session.commit()
