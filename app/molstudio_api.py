"""Molecular Studio API.

Serves structures that MOLECULAB already holds, plus computed depictions and conformers. Everything
returned is labelled with what kind of thing it is (`structure_origin`), because a 3D view is the
easiest place in the product to mistake a computed geometry for an experimental one.

Sources a structure can come from:
    compound/{id}   - a curated Compound record (the natural-product corpus)
    structure/{id}  - a MolecularStructure record (the 1.0 discovery tables)
    smiles          - an explicit SMILES string (POST), e.g. a screening candidate
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Compound, EvidenceRecord, MolecularStructure
from app.molstudio import compare as compare_structures
from app.molstudio import conformer_3d, depict_2d, studio_payload

router = APIRouter(tags=["molecular studio"])

MAX_SMILES = 4000


class SmilesIn(BaseModel):
    smiles: str = Field(min_length=1, max_length=MAX_SMILES)
    name: str | None = Field(default=None, max_length=200)
    want_3d: bool = True


class CompareIn(BaseModel):
    smiles_a: str = Field(min_length=1, max_length=MAX_SMILES)
    smiles_b: str = Field(min_length=1, max_length=MAX_SMILES)


@router.get("/")
def list_molecules(session: Annotated[Session, Depends(get_session)],
                   q: str | None = Query(default=None, max_length=120),
                   limit: int = Query(default=200, ge=1, le=500)) -> dict:
    """Compounds available for inspection, from records MOLECULAB already holds.

    Compounds without a stored structure are returned with `has_structure: false` rather than being
    hidden, so the library shows honestly what is and is not inspectable.
    """
    stmt = select(Compound).order_by(Compound.name)
    if q:
        stmt = stmt.where(Compound.name.ilike(f"%{q}%"))
    compounds = session.scalars(stmt.limit(limit)).all()

    evidence_counts = dict(session.execute(
        select(EvidenceRecord.compound_id, __import__("sqlalchemy").func.count(EvidenceRecord.id))
        .where(EvidenceRecord.compound_id.is_not(None))
        .group_by(EvidenceRecord.compound_id)).all())

    items = [{
        "source": "compound", "id": c.id, "name": c.name,
        "pubchem_cid": c.pubchem_cid, "inchikey": c.inchikey,
        "molecular_formula": c.molecular_formula, "molecular_weight": c.molecular_weight,
        "has_structure": bool(c.smiles),
        "evidence_count": evidence_counts.get(c.id, 0),
    } for c in compounds]

    structures = session.scalars(select(MolecularStructure).limit(limit)).all()
    items += [{
        "source": "structure", "id": s.id, "name": s.chembl_molecule_id or s.inchikey,
        "pubchem_cid": s.pubchem_cid, "inchikey": s.inchikey,
        "molecular_formula": (s.descriptors or {}).get("molecular_formula"),
        "molecular_weight": (s.descriptors or {}).get("molecular_weight"),
        "has_structure": True, "evidence_count": 0,
    } for s in structures]

    return {"count": len(items), "items": items,
            "note": "Structures are stored as SMILES imported from their source database. MOLECULAB "
                    "holds no experimentally determined 3D structures."}


@router.get("/compound/{compound_id}")
def compound_studio(compound_id: int, session: Annotated[Session, Depends(get_session)],
                    want_3d: bool = Query(default=True)) -> dict:
    compound = session.get(Compound, compound_id)
    if compound is None:
        raise HTTPException(404, "compound not found")
    if not compound.smiles:
        return {"ok": False, "name": compound.name,
                "error": "No structure is stored for this compound in MOLECULAB.",
                "identifiers": {"compound_id": compound.id, "pubchem_cid": compound.pubchem_cid,
                                "inchikey": compound.inchikey},
                "structure_available": False}
    payload = studio_payload(
        compound.smiles, name=compound.name, want_3d=want_3d,
        identifiers={"compound_id": compound.id, "pubchem_cid": compound.pubchem_cid,
                     "inchikey": compound.inchikey,
                     "molecular_formula": compound.molecular_formula},
        provenance={"source": "MOLECULAB compound record",
                    "identifier_source": compound.identifier_source,
                    "identifier_retrieved_at": compound.identifier_retrieved_at})
    payload["structure_available"] = payload.get("ok", False)
    payload["evidence_count"] = session.scalar(
        select(__import__("sqlalchemy").func.count(EvidenceRecord.id))
        .where(EvidenceRecord.compound_id == compound_id)) or 0
    return payload


@router.get("/structure/{structure_id}")
def structure_studio(structure_id: int, session: Annotated[Session, Depends(get_session)],
                     want_3d: bool = Query(default=True)) -> dict:
    structure = session.get(MolecularStructure, structure_id)
    if structure is None:
        raise HTTPException(404, "structure not found")
    payload = studio_payload(
        structure.standardized_smiles, name=structure.chembl_molecule_id or structure.inchikey,
        want_3d=want_3d,
        identifiers={"structure_id": structure.id, "inchikey": structure.inchikey,
                     "chembl_molecule_id": structure.chembl_molecule_id,
                     "pubchem_cid": structure.pubchem_cid},
        provenance={"source": structure.provider, "source_url": structure.source_url,
                    "retrieved_at": structure.retrieved_at,
                    "source_release": structure.source_release,
                    "transformation_version": structure.transformation_version})
    payload["structure_available"] = payload.get("ok", False)
    return payload


@router.post("/smiles")
def smiles_studio(body: Annotated[SmilesIn, Body()]) -> dict:
    """Inspect an explicit structure (e.g. a screening candidate not yet persisted).

    An invalid structure is reported as invalid; nothing is guessed or repaired.
    """
    payload = studio_payload(body.smiles, name=body.name, want_3d=body.want_3d,
                             provenance={"source": "supplied to the Molecular Studio by the researcher"})
    payload["structure_available"] = payload.get("ok", False)
    return payload


@router.post("/conformer")
def conformer(body: Annotated[SmilesIn, Body()]) -> dict:
    """3D conformer only, for lazy-loading the viewer after the 2D view has rendered."""
    return conformer_3d(body.smiles)


@router.post("/depiction")
def depiction(body: Annotated[SmilesIn, Body()]) -> dict:
    return depict_2d(body.smiles)


@router.post("/compare")
def compare(body: Annotated[CompareIn, Body()]) -> dict:
    result = compare_structures(body.smiles_a, body.smiles_b)
    if not result.get("ok"):
        raise HTTPException(422, result.get("error", "invalid structure"))
    return result
