"""Virtual screening API. Mounted at /api/v1/screening.

This exposes `app.screening.run_screening`, which until now was reachable only from its tests: a
fully implemented, reproducible engine that no part of the product could call. Nothing about the
engine's science changes here; this is the missing transport layer.

**Cost warning.** A screening run downloads every activity record for one target/endpoint from
ChEMBL (JAK1 IC50 is ~15,000 rows) and standardises the ranked candidates with RDKit. The first run
for a target takes roughly a minute; later runs are served from the on-disk provider cache. The
request therefore caps `max_records` and `top_n`, and `estimate` lets a caller see the dataset size
before committing to a full run.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.activity import CONCENTRATION_ENDPOINTS
from app.clients.chembl import ChEMBLClient
from app.db import get_session
from app.models import BiologicalTarget, MolecularStructure, ScreeningResult, ScreeningRun
from app.screening import (DEFAULT_MAX_RECORDS, DEFAULT_TOP_N, PROPERTY_FILTERS, RANKABLE,
                           ScreeningConfig, ScreeningError, run_screening, software_versions)

log = logging.getLogger("moleculab")
DB = Annotated[Session, Depends(get_session)]
router = APIRouter(tags=["virtual screening"])


class ScreeningIn(BaseModel):
    chembl_target_id: str = Field(min_length=3, max_length=40)
    endpoint: str = Field(default="IC50", max_length=20)
    organism: str | None = Field(default="Homo sapiens", max_length=120)
    assay_type: str | None = Field(default="B", max_length=4)
    reference_smiles: str | None = Field(default=None, max_length=4000)
    min_similarity: float | None = Field(default=None, ge=0, le=1)
    min_p_activity: float | None = Field(default=None, ge=0, le=15)
    property_filters: dict[str, float] = Field(default_factory=dict)
    rank_by: str = Field(default="measured_p_activity", max_length=40)
    top_n: int = Field(default=DEFAULT_TOP_N, ge=1, le=500)
    max_records: int = Field(default=DEFAULT_MAX_RECORDS, ge=100, le=50_000)
    exclude_censored: bool = True
    disease_ontology_id: str | None = Field(default=None, max_length=60)
    persist: bool = Field(default=True, description="Store the run and its ranked results")


@router.get("/options")
def options() -> dict:
    """What a screening run can be configured with, and what it deliberately will not do."""
    return {
        "endpoints": sorted(CONCENTRATION_ENDPOINTS),
        "rank_by": RANKABLE,
        "property_filters": list(PROPERTY_FILTERS),
        "defaults": {"top_n": DEFAULT_TOP_N, "max_records": DEFAULT_MAX_RECORDS,
                     "organism": "Homo sapiens", "assay_type": "B"},
        "software": software_versions(),
        "notes": {
            "what_this_ranks": "Compounds that already have real experimental measurements against "
                               "the chosen target and endpoint in ChEMBL. Absence from a ranking is "
                               "not evidence of inactivity.",
            "single_endpoint": "One endpoint per run. IC50, Ki, Kd and EC50 are never pooled.",
            "single_protein": "Only SINGLE PROTEIN targets are accepted; a PROTEIN COMPLEX or FAMILY "
                              "aggregates different biological entities.",
            "no_docking": "No docking is performed and no docking score exists.",
            "no_composite_score": "Each ranking dimension is reported separately; there is no "
                                  "combined score.",
            "cost": "A first run for a target downloads its full activity set from ChEMBL and can "
                    "take about a minute.",
        },
    }


@router.get("/estimate")
def estimate(chembl_target_id: str = Query(min_length=3, max_length=40),
             endpoint: str = Query(default="IC50", max_length=20)) -> dict:
    """Dataset size and target validity, without running the screen.

    Lets the interface warn before a long call and refuses an unusable target up front.
    """
    if endpoint not in CONCENTRATION_ENDPOINTS:
        raise HTTPException(422, f"endpoint {endpoint!r} is not a supported concentration endpoint")
    chembl = ChEMBLClient()
    target = chembl.target(chembl_target_id)
    if not target.ok:
        raise HTTPException(404, f"target not retrieved: {target.error}")
    target_type = target.data.get("target_type")
    count = chembl.activity_count(chembl_target_id, endpoint)
    return {
        "chembl_target_id": chembl_target_id,
        "name": target.data.get("pref_name"),
        "organism": target.data.get("organism"),
        "target_type": target_type,
        "screenable": target_type == "SINGLE PROTEIN",
        "refusal_reason": (None if target_type == "SINGLE PROTEIN" else
                           f"target_type {target_type!r} aggregates several proteins, so its "
                           "measurements are not comparable"),
        "endpoint": endpoint,
        "exact_measurement_count": count.data if count.ok else None,
        "count_note": "Measurements with standard_relation '=' only; censored values are counted "
                      "separately during curation.",
        "source_release": (chembl.release().source_release if count.ok else None),
    }


@router.post("/runs", status_code=201)
def create_run(body: Annotated[ScreeningIn, Body()], session: DB) -> dict:
    """Execute one screening run.

    Refuses rather than returning an empty or fabricated ranking: an unusable target, an unsupported
    endpoint, or filters that remove every candidate all raise 422 with the reason.
    """
    data = body.model_dump()
    persist = data.pop("persist")
    try:
        config = ScreeningConfig(**data)
    except TypeError as exc:
        raise HTTPException(422, f"invalid configuration: {exc}") from exc

    problems = config.validate()
    if problems:
        raise HTTPException(422, "; ".join(problems))

    try:
        result = run_screening(session, config, executed_by="api", persist=persist)
    except ScreeningError as exc:
        # A refusal is a 422: the request was understood and deliberately not performed.
        raise HTTPException(422, str(exc)) from exc
    if persist:
        session.commit()
    return result


@router.get("/runs")
def list_runs(session: DB, limit: int = Query(default=50, ge=1, le=200)) -> dict:
    runs = session.scalars(
        select(ScreeningRun).order_by(desc(ScreeningRun.created_at)).limit(limit)).all()
    return {"count": len(runs), "items": [{
        "id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None,
        "executed_by": r.executed_by, "run_type": r.run_type,
        "target_chembl_id": (r.parameters or {}).get("chembl_target_id"),
        "endpoint": (r.parameters or {}).get("endpoint"),
        "result_count": r.result_count,
        "source_release": (r.dataset_snapshot or {}).get("source_release"),
        "content_fingerprint": r.content_fingerprint,
    } for r in runs]}


@router.get("/runs/{run_id}")
def get_run(run_id: int, session: DB) -> dict:
    """A stored run with its ranked results — the reproducibility record.

    Returns the parameters, dataset snapshot, software versions, curation accounting, limitations and
    content fingerprint alongside the ranking, so a past run can be inspected and re-verified.
    """
    run = session.scalars(
        select(ScreeningRun).options(selectinload(ScreeningRun.results))
        .where(ScreeningRun.id == run_id)).first()
    if run is None:
        raise HTTPException(404, "screening run not found")

    structures = {s.id: s for s in session.scalars(select(MolecularStructure).where(
        MolecularStructure.id.in_([r.structure_id for r in run.results] or [0]))).all()}
    target = session.get(BiologicalTarget, run.target_id) if run.target_id else None

    rows = sorted(run.results, key=lambda r: r.rank)
    return {
        "id": run.id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "executed_by": run.executed_by, "run_type": run.run_type,
        "target": ({"chembl_target_id": target.chembl_target_id, "gene_symbol": target.gene_symbol,
                    "name": target.target_name, "organism": target.organism,
                    "target_type": target.chembl_target_type} if target else None),
        "parameters": run.parameters,
        "dataset_snapshot": run.dataset_snapshot,
        "software_versions": run.software_versions,
        "ranking_method": run.ranking_method,
        "inclusion_summary": run.inclusion_summary,
        "exclusion_reasons": run.exclusion_reasons,
        "limitations": run.limitations,
        "content_fingerprint": run.content_fingerprint,
        "results": [{
            "rank": r.rank,
            "inchikey": (structures.get(r.structure_id) or {}) and
                        getattr(structures.get(r.structure_id), "inchikey", None),
            "chembl_molecule_id": getattr(structures.get(r.structure_id), "chembl_molecule_id", None),
            "smiles": getattr(structures.get(r.structure_id), "standardized_smiles", None),
            "structure_id": r.structure_id,
            "measured_p_activity": r.measured_p_activity,
            "measurement_count": r.measurement_count,
            "dimensions": r.dimensions,
            "basis": r.basis,
            "evidence_refs": r.evidence_refs,
        } for r in rows],
        "reproducibility_note": (
            "Re-running with these parameters against the same provider release reproduces this "
            "content fingerprint. A different ChEMBL release is a different run."),
    }


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: int, session: DB) -> None:
    """Deletes the run and its ranked rows. The structures and the curated corpus are untouched."""
    run = session.get(ScreeningRun, run_id)
    if run is None:
        raise HTTPException(404, "screening run not found")
    session.delete(run)
    session.commit()
