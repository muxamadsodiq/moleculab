"""Formulation research API. Mounted at /api/v1/formulations.

Read and write endpoints for formulation candidates, their components, constraints, measurements,
processing conditions, material compatibility and proposed experiments — plus the evaluation,
trade-off and experiment-prioritisation views computed by app.formulation.

Nothing here generates a scientific value. Every number stored arrives from the researcher or from
a cited source, and every evaluation is recomputed from those records on read.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app import formulation as engine
from app.db import get_session
from app.models import (CompatibilityAssessment, CompatibilityVerdict, ConstraintCategory,
                        ConstraintMeasurement, ConstraintPriority, DosageForm, ExperimentPlan,
                        ExperimentStatus, Formulation, FormulationComponent, Material,
                        MaterialRole, MeasurementKind, ProcessingCondition, ResearchConstraint)

DB = Annotated[Session, Depends(get_session)]
router = APIRouter(tags=["formulation research"])


# ------------------------------------------------------------------ schemas

class MaterialIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: MaterialRole = MaterialRole.OTHER
    description: str | None = Field(default=None, max_length=4000)
    cas_number: str | None = Field(default=None, max_length=20)
    function_notes: str | None = Field(default=None, max_length=4000)
    structure_id: int | None = None
    compound_id: int | None = None


class FormulationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    workspace_id: int | None = None
    research_question: str | None = Field(default=None, max_length=4000)
    dosage_form: DosageForm = DosageForm.TABLET
    intended_release: str | None = Field(default=None, max_length=80)
    route_of_administration: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=8000)


class ComponentIn(BaseModel):
    material_id: int
    role: MaterialRole = MaterialRole.OTHER
    amount: float | None = Field(default=None, ge=0)
    amount_unit: str | None = Field(default=None, max_length=20)
    amount_basis: str | None = Field(default=None, max_length=40)
    rationale: str | None = Field(default=None, max_length=2000)


class ConstraintIn(BaseModel):
    category: ConstraintCategory
    name: str = Field(min_length=1, max_length=200)
    priority: ConstraintPriority = ConstraintPriority.MANDATORY
    property_name: str | None = Field(default=None, max_length=120)
    unit: str | None = Field(default=None, max_length=30)
    min_value: float | None = None
    max_value: float | None = None
    target_value: float | None = None
    qualitative_target: str | None = Field(default=None, max_length=2000)
    rationale: str | None = Field(default=None, max_length=4000)
    validation_method: str | None = Field(default=None, max_length=1000)


class MeasurementIn(BaseModel):
    kind: MeasurementKind
    value: float | None = None
    value_text: str | None = Field(default=None, max_length=2000)
    unit: str | None = Field(default=None, max_length=30)
    uncertainty: float | None = Field(default=None, ge=0)
    n_replicates: int | None = Field(default=None, ge=1, le=10_000)
    method: str | None = Field(default=None, max_length=2000)
    conditions: dict | None = None
    source: str | None = Field(default=None, max_length=2000)
    source_url: str | None = Field(default=None, max_length=1000)
    evidence_record_id: int | None = None
    publication_id: int | None = None
    limitations: str | None = Field(default=None, max_length=2000)


class ConditionIn(BaseModel):
    process_step: str | None = Field(default=None, max_length=120)
    parameter: str = Field(min_length=1, max_length=120)
    value: float | None = None
    value_text: str | None = Field(default=None, max_length=200)
    unit: str | None = Field(default=None, max_length=30)
    is_target: bool = True
    uncertainty: float | None = Field(default=None, ge=0)
    physical_state: str | None = Field(default=None, max_length=40)
    method: str | None = Field(default=None, max_length=2000)
    rationale: str | None = Field(default=None, max_length=2000)
    source: str | None = Field(default=None, max_length=1000)


class CompatibilityIn(BaseModel):
    material_a_id: int
    material_b_id: int
    verdict: CompatibilityVerdict = CompatibilityVerdict.NOT_EVALUATED
    interaction_type: str | None = Field(default=None, max_length=80)
    conditions_summary: str = Field(default="unspecified", max_length=200)
    observation: str | None = Field(default=None, max_length=4000)
    method: str | None = Field(default=None, max_length=2000)
    source: str | None = Field(default=None, max_length=2000)
    source_url: str | None = Field(default=None, max_length=1000)
    evidence_record_id: int | None = None


class ExperimentIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    workspace_id: int | None = None
    formulation_id: int | None = None
    research_question: str | None = Field(default=None, max_length=4000)
    hypothesis: str | None = Field(default=None, max_length=4000)
    status: ExperimentStatus = ExperimentStatus.PROPOSED
    independent_variables: list[str] = Field(default_factory=list, max_length=50)
    dependent_variables: list[str] = Field(default_factory=list, max_length=50)
    controlled_conditions: list[str] = Field(default_factory=list, max_length=50)
    materials_required: list[str] = Field(default_factory=list, max_length=100)
    equipment_required: list[str] = Field(default_factory=list, max_length=50)
    measurement_methods: list[str] = Field(default_factory=list, max_length=50)
    acceptance_criteria: str | None = Field(default=None, max_length=4000)
    rationale: str | None = Field(default=None, max_length=4000)
    limitations: str | None = Field(default=None, max_length=4000)
    estimated_cost: float | None = Field(default=None, ge=0)
    cost_currency: str | None = Field(default=None, max_length=10)
    estimated_duration_hours: float | None = Field(default=None, ge=0)
    estimate_basis: str | None = Field(default=None, max_length=2000)
    addresses_constraint_ids: list[int] = Field(default_factory=list, max_length=200)


# ----------------------------------------------------------------- helpers

def _formulation(session: Session, formulation_id: int) -> Formulation:
    stmt = (select(Formulation)
            .options(selectinload(Formulation.constraints).selectinload(ResearchConstraint.measurements),
                     selectinload(Formulation.components).selectinload(FormulationComponent.material),
                     selectinload(Formulation.conditions))
            .where(Formulation.id == formulation_id))
    found = session.scalars(stmt).first()
    if found is None:
        raise HTTPException(404, "formulation not found")
    return found


# -------------------------------------------------------------- vocabularies

@router.get("/vocabulary")
def vocabulary() -> dict:
    """The controlled vocabularies, with the distinctions that matter spelled out."""
    return {
        "dosage_forms": [d.value for d in DosageForm],
        "material_roles": [r.value for r in MaterialRole],
        "constraint_categories": [c.value for c in ConstraintCategory],
        "constraint_priorities": [p.value for p in ConstraintPriority],
        "measurement_kinds": [k.value for k in MeasurementKind],
        "compatibility_verdicts": [v.value for v in CompatibilityVerdict],
        "experiment_statuses": [s.value for s in ExperimentStatus],
        "confirming_measurement_kinds": sorted(engine.CONFIRMING_KINDS),
        "notes": {
            "disintegration_vs_dissolution":
                "Disintegration is the dosage form breaking apart (USP <701>). Dissolution is the "
                "drug going into solution (USP <711>). They are different tests measuring "
                "different things, and neither is 'evaporation'.",
            "dissolution_vs_bioavailability":
                "Dissolution is in vitro. Bioavailability is the fraction reaching systemic "
                "circulation and depends on permeability and metabolism as well.",
            "missing_data":
                "A constraint with no confirming measurement is reported as not_measured, never as "
                "satisfied.",
            "confirming_kinds":
                "Only experimental, literature and supplier-specification measurements can close a "
                "constraint. Computed descriptors, model predictions and researcher estimates are "
                "recorded and displayed, but describe a molecule or an expectation rather than the "
                "manufactured dosage form.",
            "no_composite_score":
                "MOLECULAB does not compute an overall formulation score.",
        },
    }


# ------------------------------------------------------------------ materials

@router.get("/materials")
def list_materials(session: DB, q: str | None = Query(default=None, max_length=120),
                   role: MaterialRole | None = None, limit: int = Query(200, ge=1, le=1000)) -> dict:
    stmt = select(Material).order_by(Material.name)
    if q:
        stmt = stmt.where(or_(Material.name.ilike(f"%{q}%"), Material.cas_number.ilike(f"%{q}%")))
    if role:
        stmt = stmt.where(Material.role == role)
    rows = session.scalars(stmt.limit(limit)).all()
    return {"count": len(rows), "items": [{
        "id": m.id, "name": m.name, "role": m.role, "cas_number": m.cas_number,
        "description": m.description, "structure_id": m.structure_id,
        "compound_id": m.compound_id, "provider": m.provider, "source_url": m.source_url,
    } for m in rows]}


@router.post("/materials", status_code=201)
def create_material(body: Annotated[MaterialIn, Body()], session: DB) -> dict:
    existing = session.scalar(select(Material).where(Material.name == body.name))
    if existing:
        raise HTTPException(409, f"a material named {body.name!r} already exists (id {existing.id})")
    material = Material(**body.model_dump(), provider="researcher-entered",
                        transformation_version="1.0")
    session.add(material)
    session.commit()
    return {"id": material.id, "name": material.name, "role": material.role}


# --------------------------------------------------------------- formulations

@router.get("")
def list_formulations(session: DB, workspace_id: int | None = None) -> dict:
    stmt = select(Formulation).order_by(Formulation.id.desc())
    if workspace_id:
        stmt = stmt.where(Formulation.workspace_id == workspace_id)
    rows = session.scalars(stmt).all()
    return {"count": len(rows), "items": [{
        "id": f.id, "name": f.name, "dosage_form": f.dosage_form, "status": f.status,
        "workspace_id": f.workspace_id, "research_question": f.research_question,
        "component_count": len(f.components), "constraint_count": len(f.constraints),
    } for f in rows]}


@router.post("", status_code=201)
def create_formulation(body: Annotated[FormulationIn, Body()], session: DB) -> dict:
    record = Formulation(**body.model_dump())
    session.add(record)
    session.commit()
    return {"id": record.id, "name": record.name}


# Declared before /{formulation_id}: FastAPI matches routes in declaration order, so a
# literal path that could also parse as the parameter must come first, or "compare" is
# read as a formulation id and the request 422s.
@router.get("/compare")
def compare(session: DB, ids: str = Query(description="comma-separated formulation ids")) -> dict:
    """Compare candidates dimension by dimension. No overall winner is declared."""
    try:
        wanted = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, "ids must be comma-separated integers")
    if not 2 <= len(wanted) <= 6:
        raise HTTPException(422, "compare between 2 and 6 formulations")

    evaluations = [engine.evaluate_formulation(_formulation(session, i)) for i in wanted]
    categories = sorted({c["category"] for e in evaluations for c in e["constraints"]})
    matrix = []
    for category in categories:
        row = {"category": category, "candidates": {}}
        for evaluation_ in evaluations:
            relevant = [c for c in evaluation_["constraints"] if c["category"] == category]
            row["candidates"][evaluation_["formulation_id"]] = {
                "constraints": len(relevant),
                "statuses": {s: sum(1 for c in relevant if c["status"] == s)
                             for s in {c["status"] for c in relevant}},
            }
        matrix.append(row)
    return {
        "formulations": [{"id": e["formulation_id"], "name": e["name"],
                          "dosage_form": e["dosage_form"],
                          "status_counts": e["status_counts"],
                          "readiness_statement": e["readiness_statement"]} for e in evaluations],
        "by_category": matrix,
        "interpretation": (
            "Candidates are compared per constraint category. MOLECULAB does not rank them "
            "overall: a candidate that is strong on one requirement and unmeasured on another is "
            "not comparable to a balanced one by a single number."),
    }


@router.get("/{formulation_id}")
def get_formulation(formulation_id: int, session: DB) -> dict:
    record = _formulation(session, formulation_id)
    return {
        "id": record.id, "name": record.name, "dosage_form": record.dosage_form,
        "research_question": record.research_question, "status": record.status,
        "intended_release": record.intended_release,
        "route_of_administration": record.route_of_administration,
        "workspace_id": record.workspace_id, "notes": record.notes,
        "components": [{
            "id": c.id, "material_id": c.material_id, "material": c.material.name,
            "role": c.role, "amount": c.amount, "amount_unit": c.amount_unit,
            "amount_basis": c.amount_basis, "rationale": c.rationale,
        } for c in record.components],
        "conditions": [{
            "id": p.id, "process_step": p.process_step, "parameter": p.parameter,
            "value": p.value, "value_text": p.value_text, "unit": p.unit,
            "is_target": p.is_target, "uncertainty": p.uncertainty,
            "physical_state": p.physical_state, "method": p.method, "source": p.source,
        } for p in record.conditions],
        "evaluation": engine.evaluate_formulation(record),
        "trade_offs": engine.trade_offs(record),
    }


@router.post("/{formulation_id}/components", status_code=201)
def add_component(formulation_id: int, body: Annotated[ComponentIn, Body()], session: DB) -> dict:
    _formulation(session, formulation_id)
    if session.get(Material, body.material_id) is None:
        raise HTTPException(404, "material not found")
    component = FormulationComponent(formulation_id=formulation_id, **body.model_dump())
    session.add(component)
    session.commit()
    return {"id": component.id}


@router.post("/{formulation_id}/conditions", status_code=201)
def add_condition(formulation_id: int, body: Annotated[ConditionIn, Body()], session: DB) -> dict:
    _formulation(session, formulation_id)
    condition = ProcessingCondition(formulation_id=formulation_id, **body.model_dump())
    session.add(condition)
    session.commit()
    return {"id": condition.id}


# ---------------------------------------------------------------- constraints

@router.post("/{formulation_id}/constraints", status_code=201)
def add_constraint(formulation_id: int, body: Annotated[ConstraintIn, Body()], session: DB) -> dict:
    _formulation(session, formulation_id)
    data = body.model_dump()
    if (data["min_value"] is not None and data["max_value"] is not None
            and data["min_value"] > data["max_value"]):
        raise HTTPException(422, "min_value cannot exceed max_value")
    constraint = ResearchConstraint(formulation_id=formulation_id, **data)
    session.add(constraint)
    session.commit()
    return {"id": constraint.id, "name": constraint.name, "status": "not_measured"}


@router.post("/constraints/{constraint_id}/measurements", status_code=201)
def add_measurement(constraint_id: int, body: Annotated[MeasurementIn, Body()], session: DB) -> dict:
    constraint = session.get(ResearchConstraint, constraint_id)
    if constraint is None:
        raise HTTPException(404, "constraint not found")
    if body.value is None and not body.value_text:
        raise HTTPException(422, "a measurement needs either a numeric value or value_text")
    measurement = ConstraintMeasurement(constraint_id=constraint_id, **body.model_dump())
    session.add(measurement)
    session.commit()
    session.refresh(constraint)
    return {"id": measurement.id,
            "constraint_status_now": engine.evaluate_constraint(constraint).as_dict()}


@router.get("/{formulation_id}/evaluation")
def evaluation(formulation_id: int, session: DB) -> dict:
    return engine.evaluate_formulation(_formulation(session, formulation_id))


@router.get("/{formulation_id}/trade-offs")
def formulation_trade_offs(formulation_id: int, session: DB) -> dict:
    return engine.trade_offs(_formulation(session, formulation_id))


# -------------------------------------------------------------- compatibility

@router.get("/compatibility/matrix")
def compatibility_matrix(session: DB, formulation_id: int | None = None) -> dict:
    """Pairwise compatibility for a formulation's materials.

    Pairs with no assessment are returned as `not_evaluated` — explicitly an open question, never
    an implied pass.
    """
    if formulation_id:
        record = _formulation(session, formulation_id)
        materials = [c.material for c in record.components]
    else:
        materials = session.scalars(select(Material).order_by(Material.name).limit(60)).all()

    assessments = session.scalars(select(CompatibilityAssessment)).all()
    index: dict[tuple[int, int], list] = {}
    for a in assessments:
        index.setdefault(tuple(sorted((a.material_a_id, a.material_b_id))), []).append(a)

    pairs = []
    for i, a in enumerate(materials):
        for b in materials[i + 1:]:
            found = index.get(tuple(sorted((a.id, b.id))), [])
            pairs.append({
                "material_a": {"id": a.id, "name": a.name, "role": a.role},
                "material_b": {"id": b.id, "name": b.name, "role": b.role},
                "verdict": found[0].verdict if found else CompatibilityVerdict.NOT_EVALUATED.value,
                "assessments": [{
                    "id": x.id, "verdict": x.verdict, "interaction_type": x.interaction_type,
                    "conditions": x.conditions_summary, "observation": x.observation,
                    "method": x.method, "source": x.source, "source_url": x.source_url,
                } for x in found],
            })
    return {
        "material_count": len(materials),
        "pair_count": len(pairs),
        "evaluated_pairs": sum(1 for p in pairs if p["verdict"] != "not_evaluated"),
        "pairs": pairs,
        "interpretation": (
            "An unevaluated pair means no compatibility assessment has been recorded. It does NOT "
            "mean the materials are compatible. Absence of a negative finding is not evidence of "
            "compatibility."),
    }


@router.post("/compatibility", status_code=201)
def add_compatibility(body: Annotated[CompatibilityIn, Body()], session: DB) -> dict:
    if body.material_a_id == body.material_b_id:
        raise HTTPException(422, "a material cannot be assessed against itself")
    for mid in (body.material_a_id, body.material_b_id):
        if session.get(Material, mid) is None:
            raise HTTPException(404, f"material {mid} not found")
    if body.verdict in (CompatibilityVerdict.DEMONSTRATED_COMPATIBLE,
                        CompatibilityVerdict.DEMONSTRATED_INCOMPATIBLE) and not (
            body.source or body.evidence_record_id):
        raise HTTPException(
            422, "a demonstrated compatibility verdict requires a source or an evidence record; "
                 "use 'hypothesis' or 'not_evaluated' when there is no evidence")
    assessment = CompatibilityAssessment(**body.model_dump())
    session.add(assessment)
    session.commit()
    return {"id": assessment.id, "verdict": assessment.verdict}


# ------------------------------------------------------------------ experiments

@router.get("/experiments/list")
def list_experiments(session: DB, workspace_id: int | None = None,
                     formulation_id: int | None = None) -> dict:
    stmt = select(ExperimentPlan).order_by(ExperimentPlan.id.desc())
    if workspace_id:
        stmt = stmt.where(ExperimentPlan.workspace_id == workspace_id)
    if formulation_id:
        stmt = stmt.where(ExperimentPlan.formulation_id == formulation_id)
    rows = session.scalars(stmt).all()
    return {"count": len(rows), "items": [{
        "id": e.id, "title": e.title, "status": e.status,
        "formulation_id": e.formulation_id,
        "addresses_constraint_ids": e.addresses_constraint_ids or [],
        "estimated_cost": e.estimated_cost, "cost_currency": e.cost_currency,
        "estimated_duration_hours": e.estimated_duration_hours,
    } for e in rows]}


@router.post("/experiments", status_code=201)
def create_experiment(body: Annotated[ExperimentIn, Body()], session: DB) -> dict:
    experiment = ExperimentPlan(**body.model_dump())
    session.add(experiment)
    session.commit()
    return {"id": experiment.id, "title": experiment.title, "status": experiment.status}


@router.get("/{formulation_id}/experiments/priority")
def experiment_priority(formulation_id: int, session: DB) -> dict:
    """Rank this formulation's proposed experiments by the constraint uncertainty each removes."""
    record = _formulation(session, formulation_id)
    evaluations = {c.id: engine.evaluate_constraint(c) for c in record.constraints}
    experiments = session.scalars(
        select(ExperimentPlan).where(ExperimentPlan.formulation_id == formulation_id)).all()
    result = engine.prioritise_experiments(experiments, evaluations)
    result["formulation_id"] = formulation_id
    result["constraint_count"] = len(evaluations)
    return result
