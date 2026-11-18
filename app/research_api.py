"""Research-intelligence endpoints. Mounted at /api/research (project convention) and /api/v1/research."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app import research
from app.db import get_session
from app.models import Compound, Plant

DB = Annotated[Session, Depends(get_session)]
router = APIRouter(tags=["research intelligence"])


class Ref(BaseModel):
    id: int
    model_config = ConfigDict(extra="allow")


class Gap(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    category: research.GapCategory
    kind: research.GapKind
    certainty: research.Certainty
    plant: dict | None
    compound: dict | None
    related_compounds: list[dict]
    explanation: str
    evidence_ids: list[int]
    missing_information: list[str]
    dataset_limitations: str
    suggested_next_investigation: str
    abstained: bool


class GapList(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[Gap]
    note: str


class Contradiction(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    plant: dict
    compound: dict
    evidence_ids: list[int]
    conflict_type: str
    findings: list[dict]
    context_comparison: list[dict]
    documented_differences: list[str]
    missing_context: list[str]
    assessment: str = Field(description="directly_comparable_conflict | not_directly_comparable | insufficient_context")
    assessment_explanation: str
    interpretation_note: str


class HypothesisRequest(BaseModel):
    plant_id: int | None = Field(None, ge=1)
    compound_id: int | None = Field(None, ge=1)
    question: str | None = Field(None, min_length=3, max_length=500)

    @model_validator(mode="after")
    def _one_of(self):
        if self.plant_id is None and self.compound_id is None and not self.question:
            raise ValueError("provide plant_id, compound_id or question")
        return self


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="allow")
    category: str
    status: str
    research_question: str
    hypothesis: str
    reasoning: list[str]
    supporting_evidence_ids: list[int]
    supporting_excerpts: list[dict]
    missing_evidence: list[str]
    suggested_validation: list[str]
    limitations: list[str]


class HypothesisResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    scope: dict
    question_assessment: dict | None
    documented_observations: list[dict]
    unverified_records: list[dict]
    hypotheses: list[Hypothesis]
    rejected_hypotheses: list[dict]
    abstentions: list[str]
    gaps: list[Gap]
    status_note: str


class PriorityList(BaseModel):
    model_config = ConfigDict(extra="allow")
    kind: str
    items: list[dict]
    ordering: str
    disclaimer: str


NOTE = ("Gaps describe the MOLECULAB index. 'No evidence indexed' never means that no research exists. "
        "An empty list means no gap rule fired for this scope, or that nothing is indexed.")


def _page(items, response: Response, offset: int, limit: int) -> GapList:
    response.headers["X-Total-Count"] = str(len(items))
    return GapList(total=len(items), offset=offset, limit=limit, items=items[offset:offset + limit], note=NOTE)


@router.get("/gaps", response_model=GapList)
def list_gaps(s: DB, response: Response, category: research.GapCategory | None = None,
              kind: research.GapKind | None = None, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
    items = research.gaps_for(s, category=category)
    if kind:
        items = [g for g in items if g["kind"] == kind]
    return _page(items, response, offset, limit)


@router.get("/gaps/{gap_id:path}", response_model=Gap)
def get_gap(gap_id: str, s: DB):
    if len(gap_id) > 200:
        raise HTTPException(422, "gap id too long")
    for g in research.detect_gaps(s):
        if g["id"] == gap_id:
            return g
    raise HTTPException(404, "gap not found (gap ids are recomputed from current data)")


@router.get("/plants/{plant_id}/gaps", response_model=GapList)
def plant_gaps(plant_id: int, s: DB, response: Response, offset: int = Query(0, ge=0),
               limit: int = Query(100, ge=1, le=500)):
    if s.get(Plant, plant_id) is None:
        raise HTTPException(404, "plant not found")
    return _page(research.gaps_for(s, plant_id=plant_id), response, offset, limit)


@router.get("/compounds/{compound_id}/gaps", response_model=GapList)
def compound_gaps(compound_id: int, s: DB, response: Response, offset: int = Query(0, ge=0),
                  limit: int = Query(100, ge=1, le=500)):
    if s.get(Compound, compound_id) is None:
        raise HTTPException(404, "compound not found")
    return _page(research.gaps_for(s, compound_id=compound_id), response, offset, limit)


@router.get("/contradictions", response_model=list[Contradiction])
def list_contradictions(s: DB, compound_id: int | None = Query(None, ge=1)):
    return research.contradictions(s, compound_id=compound_id)


@router.get("/contradictions/{contradiction_id:path}", response_model=Contradiction)
def get_contradiction(contradiction_id: str, s: DB):
    for c in research.contradictions(s):
        if c["id"] == contradiction_id:
            return c
    raise HTTPException(404, "contradiction not found")


@router.get("/plants/{plant_id}/contradictions", response_model=list[Contradiction])
def plant_contradictions(plant_id: int, s: DB):
    if s.get(Plant, plant_id) is None:
        raise HTTPException(404, "plant not found")
    return research.contradictions(s, plant_id=plant_id)


@router.post("/hypotheses", response_model=HypothesisResponse)
def generate_hypotheses(body: HypothesisRequest, s: DB):
    if body.plant_id and s.get(Plant, body.plant_id) is None:
        raise HTTPException(404, "plant not found")
    if body.compound_id and s.get(Compound, body.compound_id) is None:
        raise HTTPException(404, "compound not found")
    return research.hypotheses(s, plant_id=body.plant_id, compound_id=body.compound_id, question=body.question)


@router.get("/priorities", response_model=PriorityList)
def get_priorities(s: DB, kind: str = Query("plant", pattern="^(plant|compound)$")):
    return research.priorities(s, kind)
