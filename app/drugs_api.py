"""Drug intelligence API. Mounted at /api/v1/drugs. Every payload carries provenance and lists
unavailable providers explicitly. Database presence is never reported as regulatory approval."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app import drugs

router = APIRouter(tags=["drugs"])


@router.get("/search")
def search(q: str = Query(..., min_length=1)):
    return drugs.search_drugs(q)


@router.get("/ingredient/{name}")
def ingredient(name: str):
    return drugs.resolve_ingredient(name)


@router.get("/profile/{name}")
def profile(name: str):
    return drugs.build_drug_profile(name)


@router.get("/registry/uzbekistan")
def registry_uzbekistan():
    return drugs.uzbekistan_registry_status()


@router.get("/compare")
def compare(names: str = Query(...)):
    items = [n.strip() for n in names.split(",") if n.strip()]
    if not 2 <= len(items) <= 4:
        raise HTTPException(422, "names must list 2 to 4 comma-separated drugs")
    return drugs.compare_profiles(items)
