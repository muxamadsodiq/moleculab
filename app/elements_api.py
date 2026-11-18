"""Periodic table, atom bookkeeping and SMILES validation API (mounted at /api/v1/elements)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app import elements as el
from app.molecules import resolve_structure

router = APIRouter(tags=["elements"])

MAX_SMILES = 4000


class AtomIn(BaseModel):
    protons: int = Field(ge=1, le=118)
    neutrons: int = Field(ge=0, le=300)
    electrons: int = Field(ge=0, le=300)


class MoleculeIn(BaseModel):
    smiles: str = Field(min_length=1, max_length=MAX_SMILES)


@router.get("/")
def list_elements(q: str | None = Query(default=None, max_length=100),
                  category: str | None = Query(default=None, max_length=60)):
    items = el.search_elements(q) if q else el.all_elements()
    if category:
        items = [e for e in items if e["category"] == category.lower()]
    return {"count": len(items), "categories": el.CATEGORIES, "elements": items,
            "source_note": "Atomic weights: IUPAC (CIAAW). Synthetic elements have estimated or "
                           "unavailable values; unknown fields are reported as null."}


@router.post("/atom/validate")
def validate_atom(body: AtomIn):
    return el.validate_atom(body.protons, body.neutrons, body.electrons)


@router.post("/molecule/validate")
def validate_molecule(body: MoleculeIn):
    record = resolve_structure(body.smiles, "smiles").as_dict()
    record["note"] = ("SMILES-based validation. Invalid structures are reported with the parser's "
                      "message and are never auto-repaired.")
    return record


@router.get("/{symbol}")
def get_element(symbol: str):
    e = el.get_element(symbol)
    if e is None:
        raise HTTPException(status_code=404, detail="unknown element: %s" % symbol[:20])
    return {"element": e, "isotope_info": el.isotope_info(e["symbol"])}
