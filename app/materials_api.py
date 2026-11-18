"""Material and processing-condition explorer. Mounted at /api/v1/materials-explorer.

Read-only aggregation over existing tables (material, formulation_component, compatibility_assessment,
processing_condition, molecular_structure, compound). No new tables, no generated values.

Integrity rules enforced here:
  * a pair of materials with no assessment is `not_evaluated`, never compatible;
  * a property with no recorded value is listed as missing, never defaulted;
  * a ProcessingCondition is labelled `target` or `measured` from its is_target flag.
"""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (CompatibilityAssessment, CompatibilityVerdict, Compound, Formulation,
                        FormulationComponent, Material, MaterialRole, MolecularStructure,
                        ProcessingCondition)

DB = Annotated[Session, Depends(get_session)]
router = APIRouter(tags=["materials explorer"])

# Properties a formulation scientist would normally want on record. This is a CHECKLIST of what is
# absent, not a claim that every material has these properties.
EXPECTED_PROPERTIES = [
    "molecular weight", "melting point", "solubility", "pka", "hygroscopicity",
    "particle size", "bulk density", "true density", "polymorphic form", "glass transition",
]
_ALIASES = {
    "mw": "molecular weight", "molecular_weight": "molecular weight", "molar mass": "molecular weight",
    "mp": "melting point", "melting_point": "melting point", "tg": "glass transition",
    "glass_transition": "glass transition", "pk_a": "pka",
    "particle_size": "particle size", "bulk_density": "bulk density",
    "true_density": "true density", "polymorph": "polymorphic form",
}

NOT_EVALUATED_NOTE = "No assessment recorded. This is NOT evidence of compatibility."


def _norm(key) -> str:
    k = re.sub(r"\s+", " ", str(key or "").strip().lower())
    return _ALIASES.get(k, k)


def _property_entries(m: Material) -> list[dict]:
    """Normalise the free-form JSON list. Entries lacking a value are kept as unrecorded."""
    out = []
    for raw in (m.properties or []):
        if not isinstance(raw, dict):
            continue
        name = raw.get("name") or raw.get("property") or raw.get("key")
        if not name:
            continue
        value = raw.get("value", raw.get("value_text"))
        recorded = value is not None and str(value).strip() != ""
        out.append({
            "key": _norm(name), "name": str(name), "value": value if recorded else None,
            "unit": raw.get("unit"), "recorded": recorded,
            "kind": raw.get("kind"), "method": raw.get("method"),
            "source": raw.get("source"), "source_url": raw.get("source_url"),
            "has_source": bool(raw.get("source") or raw.get("source_url")),
        })
    return out


def _structure_payload(session: Session, m: Material) -> dict | None:
    if m.structure_id is None:
        return None
    s = session.get(MolecularStructure, m.structure_id)
    if s is None:
        return None
    return {"id": s.id, "inchikey": s.inchikey, "standardized_smiles": s.standardized_smiles,
            "descriptors": s.descriptors, "chemistry_version": s.chemistry_version,
            "descriptors_origin": "computed (RDKit) at structure registration; read from storage, not recomputed",
            "provider": s.provider, "source_url": s.source_url}


def _compound_payload(session: Session, m: Material) -> dict | None:
    if m.compound_id is None:
        return None
    c = session.get(Compound, m.compound_id)
    if c is None:
        return None
    return {"id": c.id, "name": c.name, "pubchem_cid": c.pubchem_cid, "inchikey": c.inchikey,
            "molecular_formula": c.molecular_formula, "molecular_weight": c.molecular_weight,
            "identifier_source": c.identifier_source}


def _recorded_keys(session: Session, m: Material) -> set[str]:
    keys = {e["key"] for e in _property_entries(m) if e["recorded"]}
    if m.structure_id is not None:
        s = session.get(MolecularStructure, m.structure_id)
        if s and s.descriptors and any(k in s.descriptors for k in ("MolWt", "mol_weight", "molecular_weight")):
            keys.add("molecular weight")
    if m.compound_id is not None:
        c = session.get(Compound, m.compound_id)
        if c and c.molecular_weight is not None:
            keys.add("molecular weight")
    return keys


def _missing(session: Session, m: Material) -> list[dict]:
    have = _recorded_keys(session, m)
    out = [{"field": p, "kind": "property", "reason": "no recorded value"}
           for p in EXPECTED_PROPERTIES if p not in have]
    if not m.cas_number:
        out.append({"field": "CAS number", "kind": "identifier", "reason": "not recorded"})
    if m.structure_id is None:
        out.append({"field": "molecular structure", "kind": "link",
                    "reason": "no MolecularStructure linked (may legitimately be a polymer or mixture)"})
    if m.compound_id is None:
        out.append({"field": "compound record", "kind": "link", "reason": "no Compound linked"})
    if not m.description:
        out.append({"field": "description", "kind": "identifier", "reason": "not recorded"})
    return out


def _verdict_str(v) -> str:
    return getattr(v, "value", v)


def _assessment_row(a: CompatibilityAssessment, own_id: int) -> dict:
    other = a.material_b if a.material_a_id == own_id else a.material_a
    return {"id": a.id, "other_material": {"id": other.id, "name": other.name, "role": other.role},
            "verdict": _verdict_str(a.verdict), "interaction_type": a.interaction_type,
            "conditions": a.conditions_summary, "observation": a.observation, "method": a.method,
            "source": a.source, "source_url": a.source_url}


def _pair_status(session: Session, a_id: int, b_id: int) -> dict:
    rows = session.scalars(select(CompatibilityAssessment).where(or_(
        (CompatibilityAssessment.material_a_id == a_id) & (CompatibilityAssessment.material_b_id == b_id),
        (CompatibilityAssessment.material_a_id == b_id) & (CompatibilityAssessment.material_b_id == a_id)))).all()
    if not rows:
        return {"verdict": CompatibilityVerdict.NOT_EVALUATED.value, "assessments": [],
                "note": NOT_EVALUATED_NOTE}
    return {"verdict": "assessed", "assessments": [
        {"id": r.id, "verdict": _verdict_str(r.verdict), "conditions": r.conditions_summary,
         "interaction_type": r.interaction_type, "source": r.source, "source_url": r.source_url}
        for r in rows], "note": "Verdicts apply only to the stated conditions."}


def _parse_ids(raw: str, label: str) -> list[int]:
    try:
        ids = [int(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, f"{label} must be comma-separated integers") from None
    if len(set(ids)) != len(ids):
        raise HTTPException(422, f"{label} must not contain duplicates")
    return ids


# ------------------------------------------------------------------ materials

@router.get("/materials")
def list_materials(session: DB, q: str | None = Query(None, max_length=200),
                   role: MaterialRole | None = None,
                   limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict:
    stmt = select(Material)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Material.name.ilike(like), Material.cas_number.ilike(like)))
    if role:
        stmt = stmt.where(Material.role == role.value)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(Material.name).limit(limit).offset(offset)).all()
    items = []
    for m in rows:
        n_form = session.scalar(select(func.count(func.distinct(FormulationComponent.formulation_id)))
                                .where(FormulationComponent.material_id == m.id)) or 0
        n_comp = session.scalar(select(func.count()).select_from(CompatibilityAssessment).where(or_(
            CompatibilityAssessment.material_a_id == m.id,
            CompatibilityAssessment.material_b_id == m.id))) or 0
        missing = _missing(session, m)
        items.append({
            "id": m.id, "name": m.name, "role": m.role, "cas_number": m.cas_number,
            "has_structure": m.structure_id is not None, "has_compound": m.compound_id is not None,
            "formulation_count": n_form, "compatibility_assessment_count": n_comp,
            "property_count": len([e for e in _property_entries(m) if e["recorded"]]),
            "missing_properties": [x["field"] for x in missing if x["kind"] == "property"],
            "missing_information": missing,
        })
    return {"total": total, "limit": limit, "offset": offset, "count": len(items), "items": items,
            "interpretation": "Materials recorded in this MOLECULAB database. The list is the set of "
                              "materials someone entered, not a catalogue of all excipients.",
            "limitations": ["Missing-property lists are measured against a fixed checklist ("
                            + ", ".join(EXPECTED_PROPERTIES) + "); a missing item is an information gap, "
                            "not a finding about the material.",
                            "Counts reflect only records in this database."]}


@router.get("/materials/compare")
def compare_materials(session: DB, ids: str = Query(..., description="2-4 comma-separated material ids")) -> dict:
    id_list = _parse_ids(ids, "ids")
    if not 2 <= len(id_list) <= 4:
        raise HTTPException(422, "ids must contain between 2 and 4 distinct material ids")
    mats = []
    for i in id_list:
        m = session.get(Material, i)
        if m is None:
            raise HTTPException(404, f"material {i} not found")
        mats.append(m)
    per = {m.id: {} for m in mats}
    names: dict[str, str] = {}
    for m in mats:
        for e in _property_entries(m):
            if e["recorded"]:
                per[m.id].setdefault(e["key"], []).append(e)
                names.setdefault(e["key"], e["name"])
    keys = sorted(set(names) | set(EXPECTED_PROPERTIES))
    rows = []
    for k in keys:
        cells = []
        for m in mats:
            es = per[m.id].get(k)
            if es:
                cells.append({"material_id": m.id, "recorded": True, "entries": [
                    {"value": e["value"], "unit": e["unit"], "source": e["source"],
                     "source_url": e["source_url"], "method": e["method"]} for e in es]})
            else:
                cells.append({"material_id": m.id, "recorded": False, "entries": [],
                              "label": "not recorded"})
        n_rec = sum(c["recorded"] for c in cells)
        rows.append({"property": names.get(k, k), "key": k, "recorded_for": n_rec,
                     "shared": n_rec == len(mats), "values": cells})
    pairs = []
    for i, a in enumerate(mats):
        for b in mats[i + 1:]:
            pairs.append({"material_a": {"id": a.id, "name": a.name},
                          "material_b": {"id": b.id, "name": b.name},
                          **_pair_status(session, a.id, b.id)})
    return {
        "materials": [{"id": m.id, "name": m.name, "role": m.role, "cas_number": m.cas_number,
                       "has_structure": m.structure_id is not None} for m in mats],
        "properties": rows,
        "shared_property_keys": [r["key"] for r in rows if r["shared"]],
        "compatibility_pairs": pairs,
        "interpretation": "Side-by-side of recorded values only. A cell reading 'not recorded' means no "
                          "value exists in this database; it is not zero and not a default. A pair with "
                          "verdict not_evaluated has never been assessed and must not be read as compatible.",
        "limitations": ["Values from different sources, methods or conditions are not normalised "
                        "or reconciled here.",
                        "Compatibility verdicts apply only to the conditions stated on each assessment."],
    }


@router.get("/materials/{material_id}")
def material_detail(material_id: int, session: DB) -> dict:
    m = session.get(Material, material_id)
    if m is None:
        raise HTTPException(404, f"material {material_id} not found")
    comps = session.scalars(select(FormulationComponent).where(
        FormulationComponent.material_id == m.id).order_by(FormulationComponent.formulation_id)).all()
    forms = []
    for c in comps:
        f = session.get(Formulation, c.formulation_id)
        forms.append({"formulation_id": c.formulation_id, "formulation_name": f.name if f else None,
                      "role": c.role, "amount": c.amount, "amount_unit": c.amount_unit,
                      "amount_basis": c.amount_basis, "rationale": c.rationale})
    assessments = session.scalars(select(CompatibilityAssessment).where(or_(
        CompatibilityAssessment.material_a_id == m.id,
        CompatibilityAssessment.material_b_id == m.id)).order_by(CompatibilityAssessment.id)).all()
    assessed_ids = {(a.material_b_id if a.material_a_id == m.id else a.material_a_id) for a in assessments}
    unassessed = [{"id": o.id, "name": o.name, "verdict": CompatibilityVerdict.NOT_EVALUATED.value,
                   "note": NOT_EVALUATED_NOTE}
                  for o in session.scalars(select(Material).where(Material.id != m.id).order_by(Material.name))
                  if o.id not in assessed_ids]
    return {
        "id": m.id, "name": m.name, "role": m.role, "cas_number": m.cas_number,
        "description": m.description, "function_notes": m.function_notes,
        "provider": m.provider, "source_url": m.source_url, "retrieved_at": m.retrieved_at,
        "structure": _structure_payload(session, m), "compound": _compound_payload(session, m),
        "properties": _property_entries(m),
        "formulations": forms,
        "compatibility_assessments": [_assessment_row(a, m.id) for a in assessments],
        "unassessed_partners": unassessed,
        "missing_information": _missing(session, m),
        "interpretation": "Everything shown is a stored record with its own provenance. Properties are "
                          "listed with the source each entry carries; entries with no source are flagged "
                          "as unsourced. Absence of an assessment against another material is "
                          "'not_evaluated', never compatibility.",
        "limitations": ["Descriptors, when shown, were computed at structure registration and are not "
                        "measurements.",
                        "Material behaviour is not inferred from role or name.",
                        "Many excipients are polymers or mixtures and have no single structure."],
    }


# ------------------------------------------------------------------ conditions

def _cond_row(c: ProcessingCondition, fname: str | None) -> dict:
    has_value = c.value is not None or bool(c.value_text)
    return {
        "id": c.id, "formulation_id": c.formulation_id, "formulation_name": fname,
        "formulation_link": f"#/formulations?id={c.formulation_id}",
        "process_step": c.process_step, "parameter": c.parameter,
        "value": c.value, "value_text": c.value_text, "unit": c.unit, "has_value": has_value,
        "is_target": bool(c.is_target),
        "value_kind": "target" if c.is_target else "measured",
        "value_kind_note": ("Intended/planned setting. NOT a measurement." if c.is_target
                            else "Measured value."),
        "uncertainty": c.uncertainty, "physical_state": c.physical_state,
        "method": c.method, "rationale": c.rationale, "source": c.source,
    }


def _norm_param(p: str) -> str:
    return re.sub(r"\s+", " ", (p or "").strip().lower())


@router.get("/conditions")
def list_conditions(session: DB, parameter: str | None = Query(None, max_length=120),
                    process_step: str | None = Query(None, max_length=120),
                    is_target: bool | None = None,
                    limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)) -> dict:
    stmt = select(ProcessingCondition, Formulation.name).join(
        Formulation, Formulation.id == ProcessingCondition.formulation_id)
    if parameter:
        stmt = stmt.where(func.lower(ProcessingCondition.parameter) == parameter.strip().lower())
    if process_step:
        stmt = stmt.where(func.lower(ProcessingCondition.process_step) == process_step.strip().lower())
    if is_target is not None:
        stmt = stmt.where(ProcessingCondition.is_target == is_target)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.execute(stmt.order_by(ProcessingCondition.parameter, ProcessingCondition.id)
                           .limit(limit).offset(offset)).all()
    return {"total": total, "limit": limit, "offset": offset, "count": len(rows),
            "items": [_cond_row(c, n) for c, n in rows],
            "interpretation": "Documented processing conditions. Each row is either a TARGET (intended "
                              "setting) or a MEASURED value; a target says what was planned, not what happened.",
            "limitations": ["Conditions are entered by researchers; their completeness is unknown.",
                            "Values with different units for the same parameter are not converted."]}


@router.get("/conditions/parameters")
def condition_parameters(session: DB) -> dict:
    rows = session.scalars(select(ProcessingCondition)).all()
    agg: dict[str, dict] = {}
    steps: dict[str, int] = {}
    for c in rows:
        k = _norm_param(c.parameter)
        a = agg.setdefault(k, {"parameter": c.parameter, "count": 0, "units": set(), "target_count": 0,
                               "measured_count": 0, "numeric_count": 0, "formulation_ids": set()})
        a["count"] += 1
        if c.unit:
            a["units"].add(c.unit)
        a["target_count" if c.is_target else "measured_count"] += 1
        if c.value is not None:
            a["numeric_count"] += 1
        a["formulation_ids"].add(c.formulation_id)
        if c.process_step:
            s = c.process_step.strip().lower()
            steps[s] = steps.get(s, 0) + 1
    params = [{"parameter": a["parameter"], "count": a["count"], "units": sorted(a["units"]),
               "mixed_units": len(a["units"]) > 1, "target_count": a["target_count"],
               "measured_count": a["measured_count"], "numeric_count": a["numeric_count"],
               "formulation_count": len(a["formulation_ids"])}
              for a in sorted(agg.values(), key=lambda x: x["parameter"].lower())]
    return {"parameters": params,
            "process_steps": [{"process_step": s, "count": n} for s, n in sorted(steps.items())],
            "total_conditions": len(rows),
            "interpretation": "Parameters and steps actually present in the database, with units as entered.",
            "limitations": ["Parameter names are matched case-insensitively but not otherwise "
                            "harmonised; synonyms appear as separate parameters."]}


@router.get("/conditions/compare")
def compare_conditions(session: DB, formulation_ids: str = Query(..., description="2-4 comma-separated ids")) -> dict:
    ids = _parse_ids(formulation_ids, "formulation_ids")
    if not 2 <= len(ids) <= 4:
        raise HTTPException(422, "formulation_ids must contain between 2 and 4 distinct ids")
    forms = []
    for i in ids:
        f = session.get(Formulation, i)
        if f is None:
            raise HTTPException(404, f"formulation {i} not found")
        forms.append(f)
    table: dict[tuple, dict] = {}
    for f in forms:
        for c in session.scalars(select(ProcessingCondition).where(ProcessingCondition.formulation_id == f.id)):
            key = (_norm_param(c.parameter), (c.process_step or "").strip().lower())
            row = table.setdefault(key, {"parameter": c.parameter, "process_step": c.process_step,
                                         "by_formulation": {x.id: [] for x in forms}})
            row["by_formulation"][f.id].append(_cond_row(c, f.name))
    rows = []
    for row in sorted(table.values(), key=lambda r: (r["parameter"].lower(), r["process_step"] or "")):
        cells = []
        for f in forms:
            es = row["by_formulation"][f.id]
            cell = {"formulation_id": f.id, "recorded": bool(es), "entries": es}
            if not es:
                cell["label"] = "not documented"
            cells.append(cell)
        n = sum(c["recorded"] for c in cells)
        rows.append({"parameter": row["parameter"], "process_step": row["process_step"],
                     "documented_in": n, "gap": n < len(forms), "values": cells})
    return {"formulations": [{"id": f.id, "name": f.name} for f in forms], "rows": rows,
            "gap_count": sum(r["gap"] for r in rows),
            "interpretation": "Parameter-by-parameter comparison of documented conditions. 'not documented' "
                              "means no record in this database, not that the condition was zero or equal.",
            "limitations": ["Target and measured values are kept separate on each entry; do not compare a "
                            "target in one formulation to a measurement in another as like with like.",
                            "Units are shown as entered and are not converted."]}
