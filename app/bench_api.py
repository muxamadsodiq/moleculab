"""Research Bench API. Mounted at /api/v1/bench.

The Bench is the product's front door, so this router is deliberately thin: it validates input,
calls an existing engine, and returns the result with its provenance. No science is implemented
here.

Routes group into the seven stages a researcher moves through:

    research   /start, /sessions, /sessions/{id}
    evidence   handled by the existing /api/research and /api/workspaces routes
    explore    /search, /formulations/{id}, /formulations/compare, /structure
    calculate  /calculations, /calculations/{id}/run
    compare    /structures/compare
    record     handled by the existing /api/workspaces/{id}/notes|hypotheses routes
    report     handled by the existing /api/workspaces/{id}/reports route

Reusing the workspace routes for evidence, record and report is the point: the Bench gives those
capabilities one coherent surface instead of replacing them.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import bench, calculations, molecules, molstudio
from app.db import get_session
from app.models import Compound, ResearchWorkspace
from app.workspace_auth import current_researcher

router = APIRouter(tags=["research-bench"])

DB = Annotated[Session, Depends(get_session)]
# The Bench must own its sessions under the SAME identity the workspace routes check, or every
# attach, note and report on a Bench session 404s.
WHO = Annotated[str, Depends(current_researcher)]


def _commit(db: Session) -> None:
    db.commit()


# --------------------------------------------------------------------------- research stage

@router.get("/start")
def start(db: DB, who: WHO) -> dict:
    """The opening screen: modes, stages, examples, and any investigation already in progress."""
    opts = bench.start_options()
    rows = db.execute(
        select(ResearchWorkspace).where(ResearchWorkspace.owner == who)
        .order_by(ResearchWorkspace.updated_at.desc()).limit(10)
    ).scalars().all()
    opts["sessions"] = [
        {"session_id": w.id, "title": w.title, "objective": w.research_question,
         "mode": w.research_domain, "status": w.status,
         "updated_at": w.updated_at.isoformat() if w.updated_at else None}
        for w in rows
    ]
    opts["catalogue_summary"] = {
        "calculations_available": len(calculations.REGISTRY),
        "capabilities_unavailable": len(calculations.UNAVAILABLE),
    }
    return opts


class SessionIn(BaseModel):
    objective: str = Field(min_length=3, max_length=2000)
    mode: str = Field(default="open")
    title: str | None = Field(default=None, max_length=200)


@router.post("/sessions", status_code=201)
def create_session(body: SessionIn, db: DB, who: WHO) -> dict:
    try:
        ws = bench.create_session(db, objective=body.objective, mode=body.mode,
                                  title=body.title, owner=who)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _commit(db)
    return bench.session_state(db, ws)


@router.get("/sessions")
def list_sessions(db: DB, who: WHO, limit: int = Query(25, ge=1, le=100)) -> dict:
    rows = db.execute(
        select(ResearchWorkspace).where(ResearchWorkspace.owner == who)
        .order_by(ResearchWorkspace.updated_at.desc()).limit(limit)
    ).scalars().all()
    return {"count": len(rows),
            "items": [bench.session_state(db, w) for w in rows]}


@router.get("/sessions/{session_id}")
def get_session_state(session_id: int, db: DB) -> dict:
    ws = db.get(ResearchWorkspace, session_id)
    if ws is None:
        raise HTTPException(404, f"No investigation {session_id}.")
    return bench.session_state(db, ws)


# --------------------------------------------------------------------------- explore stage

@router.get("/search")
def search(db: DB, q: str = Query(min_length=1, max_length=200),
           kinds: str | None = Query(None, description="Comma-separated record kinds."),
           limit: int = Query(10, ge=1, le=50)) -> dict:
    """One search box over every curated record a researcher might start from."""
    kind_list = [k.strip() for k in kinds.split(",")] if kinds else None
    return bench.find_starting_material(db, q, kinds=kind_list, limit=limit)


@router.get("/formulations/compare")
def compare_formulations(db: DB, ids: str = Query(description="Comma-separated formulation ids.")) -> dict:
    """Declared before /formulations/{id} so the literal path is not captured by the parameter."""
    try:
        parsed = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError as exc:
        raise HTTPException(422, "ids must be comma-separated integers.") from exc
    try:
        return bench.compare_formulations(db, parsed)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, f"No formulation {exc}.") from exc


@router.get("/formulations/{formulation_id}")
def formulation_detail(formulation_id: int, db: DB) -> dict:
    try:
        return bench.formulation_detail(db, formulation_id)
    except KeyError as exc:
        raise HTTPException(404, f"No formulation {formulation_id}.") from exc


class StructureIn(BaseModel):
    smiles: str | None = Field(default=None, max_length=100_000)
    compound_id: int | None = None
    include_3d: bool = True


@router.post("/structure")
def structure(body: StructureIn, db: DB) -> dict:
    """2D depiction, optional 3D conformer and descriptors for one structure.

    Accepts either a SMILES (from the Molecular Playground or typed in) or a curated compound id.
    This is the bridge between the visual editor and the authoritative chemistry engine: the
    editor's output is never trusted, it is re-parsed here by RDKit.
    """
    smiles = (body.smiles or "").strip()
    source = "researcher_supplied"
    if not smiles and body.compound_id:
        c = db.get(Compound, body.compound_id)
        if c is None:
            raise HTTPException(404, f"No compound {body.compound_id}.")
        if not c.smiles:
            raise HTTPException(422, f"Compound {body.compound_id} ({c.name}) has no structure "
                                     "recorded, so nothing can be depicted or computed.")
        smiles, source = c.smiles, f"curated_compound:{c.id}"
    if not smiles:
        raise HTTPException(422, "Supply either smiles or compound_id.")

    try:
        payload = molstudio.studio_payload(smiles, want_3d=body.include_3d)
    except Exception as exc:                                       # noqa: BLE001
        raise HTTPException(422, f"Structure could not be processed: {exc}") from exc
    payload["structure_source"] = source
    payload["epistemic_note"] = (
        "The 2D depiction and every descriptor here are COMPUTED from the structure by RDKit. "
        "A 3D conformer is computationally generated, not experimentally determined. None of "
        "these values is a measurement."
    )
    return payload


# --------------------------------------------------------------------------- calculate stage

@router.get("/calculations")
def calculation_catalogue() -> dict:
    """Exactly what the Bench may offer, plus what it must honestly decline and why."""
    return calculations.catalogue()


@router.post("/calculations/{calc_id}/run")
def run_calculation(calc_id: str, params: Annotated[dict[str, Any], Body(default_factory=dict)]) -> dict:
    """Execute a registered calculation. Every run carries its own provenance.

    A declared-unavailable capability (docking, QSAR, dissolution prediction) is refused with its
    reason rather than returning a plausible-looking number.
    """
    try:
        return calculations.execute(calc_id, params or {})
    except KeyError as exc:
        raise HTTPException(404, f"No calculation {calc_id!r}. See GET /calculations.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# --------------------------------------------------------------------------- compare stage

class StructureCompareIn(BaseModel):
    structures: list[dict] = Field(min_length=2, max_length=6)


@router.post("/structures/compare")
def compare_structures(body: StructureCompareIn) -> dict:
    """Compare two to six structures: descriptors side by side, plus pairwise similarity.

    The intended use is original versus modified candidate from the Molecular Playground, which
    is why each entry carries a free-text label.
    """
    parsed, failed = [], []
    for item in body.structures:
        smi = str(item.get("smiles") or "").strip()
        label = str(item.get("label") or smi[:28] or "(unlabelled)")
        if not smi:
            failed.append({"label": label, "error": "No SMILES supplied."})
            continue
        mol, err = molecules.parse_smiles(smi)
        if mol is None:
            failed.append({"label": label, "smiles": smi, "error": err})
            continue
        std, steps = molecules.standardize(mol)
        parsed.append({
            "label": label, "input_smiles": smi,
            "standardized_smiles": molecules.Chem.MolToSmiles(std),
            "inchikey": molecules.Chem.MolToInchiKey(std),
            "descriptors": molecules.descriptors(std),
            "scaffold": molecules.murcko_scaffold(std),
            "standardization_changed_structure": bool(steps),
            "_mol": std,
        })
    if len(parsed) < 2:
        raise HTTPException(422, {"message": "At least two structures must parse to compare.",
                                  "failed": failed})

    pairs = []
    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            a, b = parsed[i], parsed[j]
            pairs.append({
                "a": a["label"], "b": b["label"],
                "tanimoto": molecules.tanimoto(a["_mol"], b["_mol"]),
                "identical_structure": a["inchikey"] == b["inchikey"],
                "shares_scaffold": a["scaffold"] == b["scaffold"],
            })

    keys = ["molecular_formula", "molecular_weight", "clogp", "tpsa", "h_bond_donors",
            "h_bond_acceptors", "rotatable_bonds", "heavy_atom_count", "ring_count",
            "aromatic_ring_count", "fraction_csp3"]
    differences = []
    for k in keys:
        values = [p["descriptors"].get(k) for p in parsed]
        numeric = [v for v in values if isinstance(v, (int, float))]
        differences.append({
            "descriptor": k,
            "values": values,
            "differs": len({str(v) for v in values}) > 1,
            "delta": (round(max(numeric) - min(numeric), 4) if len(numeric) == len(values)
                      and len(numeric) > 1 else None),
        })

    for p in parsed:
        p.pop("_mol", None)
    return {
        "structures": parsed, "failed": failed,
        "pairwise_similarity": pairs,
        "descriptor_differences": differences,
        "fingerprint": molecules.FINGERPRINT_SPEC,
        "software_versions": calculations._versions(),
        "interpretation": (
            "Descriptors are computed on the standardized structure of each entry. Tanimoto "
            "similarity measures shared substructure on Morgan fingerprints."
        ),
        "limitations": [
            "Structural similarity is NOT pharmacological equivalence. Two compounds can be "
            "highly similar and differ in potency by orders of magnitude.",
            "A descriptor difference is a computed difference, not a measured one, and does not "
            "establish that any biological or physical property differs.",
            "No composite score is produced; compare the individual descriptors.",
        ],
    }


# --------------------------------------------------------------------------- dosing

@router.get("/dosing/options")
def dosing_options() -> dict:
    """Species reference weights, allometric exponents and reference drug parameters."""
    from app import pharmacokinetics
    return pharmacokinetics.species_table()


class ScenarioSet(BaseModel):
    scenarios: list[dict] = Field(min_length=2, max_length=4)


@router.post("/dosing/compare")
def dosing_compare(body: ScenarioSet) -> dict:
    """Run two to four dosing scenarios and line up their exposure metrics."""
    from app import pharmacokinetics
    try:
        return pharmacokinetics.compare([dict(s) for s in body.scenarios])
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


class SurfaceIn(BaseModel):
    dose_min_mg: float = Field(gt=0)
    dose_max_mg: float = Field(gt=0)
    dose_levels: int = Field(default=12, ge=3, le=24)
    time_points: int = Field(default=60, ge=10, le=200)
    interval_h: float = 0
    duration_h: float = 72
    clearance_l_per_h: float = 6.0
    volume_l: float = 36.0
    ka_per_h: float = 1.5
    bioavailability: float = 1.0
    species: str = "human"
    scale_from_human: bool = False


@router.post("/dosing/surface")
def dosing_surface(body: SurfaceIn) -> dict:
    """Sweep a dose range. Every level is a full integration; nothing is interpolated."""
    from app import pharmacokinetics
    d = body.model_dump()
    lo, hi = d.pop("dose_min_mg"), d.pop("dose_max_mg")
    levels, pts = d.pop("dose_levels"), d.pop("time_points")
    try:
        return pharmacokinetics.surface(dose_min_mg=lo, dose_max_mg=hi, dose_levels=levels,
                                        time_points=pts, **d)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/pbpk/physiology")
def pbpk_physiology() -> dict:
    """Reference organ volumes and blood flows per species, with their provenance caveats."""
    from app import pbpk
    return pbpk.physiology_table()
