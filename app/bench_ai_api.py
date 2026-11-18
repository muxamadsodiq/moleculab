"""Research Bench AI routes. Mounted at /api/v1/bench/ai.

Kept separate from `bench_api` so the Bench still works in full when no model is configured: these
routes return 503 with the reason, and nothing else on the Bench depends on them.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import bench_ai
from app.db import get_session
from app.llm import LLMError
from app.models import ResearchWorkspace

router = APIRouter(tags=["research-bench-ai"])

DB = Annotated[Session, Depends(get_session)]


@router.get("/status")
def ai_status() -> dict:
    """Whether real generation is available. MOLECULAB never simulates an AI answer."""
    return {**bench_ai.provider_status(), "version": bench_ai.BENCH_AI_VERSION}


class StructureAskIn(BaseModel):
    smiles: str = Field(min_length=1, max_length=100_000)
    question: str = Field(min_length=3, max_length=2000)
    session_id: int | None = None


@router.post("/structure")
def ai_structure(body: StructureAskIn, db: DB) -> dict:
    """Ask about a structure. Every number the model may cite is computed by RDKit first.

    The computed context is returned alongside the answer so a reader can check each number the
    model used against the panel beside it.
    """
    sess = None
    if body.session_id:
        ws = db.get(ResearchWorkspace, body.session_id)
        if ws is not None:
            sess = bench_ai.session_context(db, ws)
    try:
        r = bench_ai.ask_about_structure(body.smiles, body.question, session=sess)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(503, f"No model is configured, so no answer is produced: {exc}") from exc
    return {"answer": r.answer, "provider": r.provider, "model": r.model, "route": r.route,
            "computed_context": r.context, "limitations": r.limitations,
            "duration_ms": r.duration_ms}


class LiteratureAskIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    session_id: int | None = None


@router.post("/literature")
def ai_literature(body: LiteratureAskIn, db: DB) -> dict:
    """Ask what the indexed evidence establishes, through the existing verified path.

    Claim extraction, per-claim verification against cited excerpts, removal of unsupported claims
    and the abstention decision are unchanged from the standalone assistant.
    """
    ws = db.get(ResearchWorkspace, body.session_id) if body.session_id else None
    try:
        return bench_ai.ask_about_literature(db, body.question, workspace=ws)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(503, f"No model is configured, so no answer is produced: {exc}") from exc


@router.get("/context/{session_id}")
def ai_context(session_id: int, db: DB) -> dict:
    """Exactly what the assistant is told about this investigation, shown for transparency."""
    ws = db.get(ResearchWorkspace, session_id)
    if ws is None:
        raise HTTPException(404, f"No investigation {session_id}.")
    return bench_ai.session_context(db, ws)
