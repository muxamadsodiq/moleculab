"""Simulation Lab API. Mounted at /api/v1/simulations.

Each endpoint returns the computation plus its `method`, `result_kind`, `interpretation` and
`limitations`, so a result cannot be read out of context. The protein-ligand endpoint deliberately
returns an unavailable status rather than any number.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import simulations
from app.db import get_session
from app.models import Compound, MolecularStructure

router = APIRouter(tags=["simulation lab"])

MAX_SMILES = 4000


class ConformerIn(BaseModel):
    smiles: str = Field(min_length=1, max_length=MAX_SMILES)
    n_conformers: int = Field(10, ge=1, le=simulations.MAX_CONFORMERS)
    optimise: bool = True
    prune_rms: float = Field(0.5, ge=0.0, le=3.0)


class SimilarityIn(BaseModel):
    smiles: str = Field(min_length=1, max_length=MAX_SMILES)
    top_n: int = Field(25, ge=1, le=200)
    include_activity_dataset: bool = Field(
        False, description="Also search the curated JAK1 activity dataset (7,777 compounds)")


def _compound_library(session: Session) -> list[dict]:
    """Structures MOLECULAB holds locally: the curated corpus plus any imported structures."""
    library = [{"source": "compound", "id": c.id, "name": c.name, "smiles": c.smiles,
                "inchikey": c.inchikey}
               for c in session.scalars(select(Compound).where(Compound.smiles.is_not(None))).all()]
    library += [{"source": "structure", "id": s.id,
                 "name": s.chembl_molecule_id or s.inchikey, "smiles": s.standardized_smiles,
                 "inchikey": s.inchikey}
                for s in session.scalars(select(MolecularStructure)).all()]
    return library


@router.get("/")
def catalogue() -> dict:
    """What the Simulation Lab can and cannot do, stated up front."""
    dataset = simulations.activity_dataset_info()
    return {
        "simulations": [
            {"id": "conformers", "name": "Conformer exploration", "status": "available",
             "result_kind": "computed",
             "summary": "Generate a 3D conformer ensemble with RDKit ETKDGv3 and optimise it with "
                        "MMFF94/UFF. Real calculation; energies are intramolecular strain, not binding."},
            {"id": "properties", "name": "Molecular property explorer", "status": "available",
             "result_kind": "computed",
             "summary": "Compute RDKit descriptors across the compound library and explore their "
                        "relationships. Calculated values, not measurements."},
            {"id": "similarity", "name": "Structure similarity explorer", "status": "available",
             "result_kind": "computed",
             "summary": "Morgan fingerprint / Tanimoto similarity against the library. Structural "
                        "similarity only; not evidence of shared activity."},
            {"id": "activity", "name": "Activity data explorer", "status":
                "available" if dataset["available"] else "unavailable",
             "result_kind": "experimental",
             "summary": "Explore real IC50 measurements curated from ChEMBL for JAK1, with "
                        "compatibility rules enforced."},
            {"id": "protein_ligand", "name": "Protein-ligand interaction", "status": "unavailable",
             "result_kind": "unavailable",
             "summary": "No docking engine, protein structures or preparation pipeline exist. "
                        "Nothing is computed or displayed."},
        ],
        "software": simulations.software_versions(),
    }


@router.post("/conformers")
def conformers(body: Annotated[ConformerIn, Body()]) -> dict:
    result = simulations.conformer_ensemble(body.smiles, body.n_conformers, body.optimise,
                                            body.prune_rms)
    if not result.get("ok"):
        raise HTTPException(422, result.get("error", "conformer generation failed"))
    return result


@router.post("/similarity")
def similarity(body: Annotated[SimilarityIn, Body()],
               session: Annotated[Session, Depends(get_session)]) -> dict:
    library = _compound_library(session)
    if body.include_activity_dataset:
        library += [{"source": "chembl_jak1", "id": r.get("molecule_chembl_id"),
                     "name": r.get("molecule_chembl_id"), "smiles": r.get("smiles"),
                     "p_activity_median": r.get("p_activity_median")}
                    for r in simulations._load_activity_dataset()]
    result = simulations.similarity_search(body.smiles, library, body.top_n)
    if not result.get("ok"):
        raise HTTPException(422, result.get("error", "similarity search failed"))
    return result


@router.get("/properties")
def properties(session: Annotated[Session, Depends(get_session)]) -> dict:
    return simulations.property_matrix(_compound_library(session))


@router.get("/activity")
def activity(min_p_activity: float | None = Query(default=None, ge=0, le=15),
             max_p_activity: float | None = Query(default=None, ge=0, le=15),
             min_replicates: int = Query(default=1, ge=1, le=50),
             scaffold: str | None = Query(default=None, max_length=400),
             search: str | None = Query(default=None, max_length=60),
             limit: int = Query(default=200, ge=1, le=1000)) -> dict:
    result = simulations.activity_explore(min_p_activity, max_p_activity, min_replicates,
                                          scaffold, search, limit)
    if not result.get("ok"):
        raise HTTPException(503, result.get("error", "activity dataset unavailable"))
    return result


@router.get("/activity/dataset")
def activity_dataset() -> dict:
    return simulations.activity_dataset_info()


@router.get("/protein-ligand")
def protein_ligand() -> dict:
    """Always reports unavailable. Deliberately not a 404: the absence is the information."""
    return simulations.protein_ligand_status()
