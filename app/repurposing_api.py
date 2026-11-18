"""Repurposing evidence-inventory endpoints. Mounted at /api/repurposing and /api/v1/repurposing.

The analysis queries public providers inside the request (cached on disk by the provider clients). It is
read-only with respect to the database unless `persist_context` is requested, which stores only the
resolved disease, target and their association in the existing 1.0 tables.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import repurposing
from app.db import get_session

DB = Annotated[Session, Depends(get_session)]
router = APIRouter(tags=["repurposing evidence"])


class RegulatoryRecordIn(BaseModel):
    chembl_id: str = Field(min_length=3, max_length=40)
    jurisdiction: str = Field(min_length=2, max_length=80)
    authority: str = Field(min_length=2, max_length=120)
    reference: str = Field(min_length=3, max_length=300)


class AnalysisIn(BaseModel):
    target: str = Field(min_length=1, max_length=200, description="Gene symbol or Ensembl id, resolved via Open Targets")
    disease: str = Field(min_length=1, max_length=200, description="Disease name or ontology id, resolved via Open Targets")
    include_chembl: bool = True
    include_activity_counts: bool = True
    include_registered_studies: bool = False
    association_rows: int = Field(200, ge=1, le=1000)
    max_candidates: int = Field(100, ge=1, le=500)
    regulatory_records: list[RegulatoryRecordIn] = Field([], max_length=200)
    persist_context: bool = False


def _clients():
    """Provider clients for a request. Replaced in tests with offline fakes."""
    return {}


@router.get("/classification-rules")
def classification_rules():
    return {"categories": {c.value: m for c, m in repurposing.CLASS_MEANING.items()},
            "approval_rule": "approved_drug requires a supplied jurisdiction-specific regulatory record; provider "
                             "development-stage labels never establish approval.",
            "disclaimer": repurposing.DISCLAIMER}


@router.post("/analyses")
def create_analysis(body: AnalysisIn, s: DB, format: str = Query("json", pattern="^(json|markdown)$")):
    cfg = repurposing.RepurposingConfig(
        target_query=body.target, disease_query=body.disease, include_chembl=body.include_chembl,
        include_activity_counts=body.include_activity_counts,
        include_registered_studies=body.include_registered_studies, association_rows=body.association_rows,
        max_candidates=body.max_candidates, regulatory_records=[r.model_dump() for r in body.regulatory_records])
    try:
        analysis = repurposing.analyse(cfg, **_clients())
    except repurposing.RepurposingError as e:
        raise HTTPException(e.status, {"error": str(e), **e.detail})
    if body.persist_context:
        analysis["persisted"] = repurposing.persist_context(s, analysis)
    if format == "markdown":
        return PlainTextResponse(repurposing.summary_markdown(analysis), media_type="text/markdown; charset=utf-8")
    return analysis
