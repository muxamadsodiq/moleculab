"""Research Bench orchestration.

The Bench is the product. Everything else in MOLECULAB is a capability it coordinates.

This module owns no science. It resolves a researcher's stated objective into the existing
engines and records what happened, so the researcher moves through

    Research -> Evidence -> Explore -> Calculate -> Compare -> Record -> Report

without choosing a destination first. Before the Bench, a researcher had to pick one of 23
navigation entries before doing anything at all.

**Reuse, not duplication.** A research session IS a `ResearchWorkspace`: the objective lives in
`research_question`, the mode in `research_domain`, and every note, hypothesis, task, attached
record and report already hangs off it. No new table and no migration were needed. Evidence comes
from `app.research`, structures from `app.molstudio`, calculations from `app.calculations`, and
formulation evaluation from `app.formulation`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import calculations, workspace as ws_svc
from app.workspace_auth import LOCAL_USER
from app.workspace_auth import LOCAL_USER
from app.config import APP_VERSION
from app.models import (Compound, Formulation, FormulationComponent, Material, Plant,
                        PlantAlias, Publication, ResearchNote, ResearchReport, ResearchTask,
                        ResearchWorkspace, WorkspaceHypothesis, WorkspaceResource)

BENCH_VERSION = "1.0.0"


# --------------------------------------------------------------------------- research modes

@dataclass(frozen=True)
class Mode:
    id: str
    label: str
    prompt: str                 # what we ask the researcher for, in their language
    starting_material: str      # what kind of thing starts this investigation
    first_steps: tuple[str, ...]


MODES: tuple[Mode, ...] = (
    Mode("product", "Investigate an existing pharmaceutical product",
         "Which product do you want to investigate?", "product",
         ("Identify the active ingredient", "Find documented components",
          "Retrieve the ingredient's structure", "Collect scientific references")),
    Mode("compound", "Investigate a compound",
         "Which compound, or what structure?", "compound",
         ("Validate and standardize the structure", "Compute descriptors",
          "Find measured activity", "Collect supporting evidence")),
    Mode("formulation", "Explore a tablet formulation",
         "Which formulation, or what composition?", "formulation",
         ("List the components", "Compute composition totals",
          "Check which component information is missing", "Review constraints")),
    Mode("compare_formulations", "Compare formulations",
         "Which two compositions do you want to compare?", "formulation_pair",
         ("Align the components", "Compare amounts and roles",
          "Show what differs", "Identify missing information in each")),
    Mode("target", "Investigate a disease or biological target",
         "Which disease or target?", "target",
         ("Resolve to a stable ontology identifier", "Review evidence by datatype",
          "Find compounds with measured activity", "Check what is unestablished")),
    Mode("open", "Start an open-ended research question",
         "What do you want to find out?", "question",
         ("Interpret the question", "Search the indexed evidence",
          "Report what the evidence does and does not establish",
          "Suggest the next investigation")),
)

MODE_IDS = {m.id for m in MODES}

# The seven stages the researcher moves through. Deliberately plain words: no "workspace
# resource", no "investigation snapshot", no internal model names.
STAGES: tuple[dict, ...] = (
    {"id": "research", "label": "Research", "question": "What am I investigating?"},
    {"id": "evidence", "label": "Evidence", "question": "What does the literature establish?"},
    {"id": "assist", "label": "Assist", "question": "What does the assistant make of this?"},
    {"id": "explore", "label": "Explore", "question": "What are the structures and components?"},
    {"id": "calculate", "label": "Calculate", "question": "What can I compute?"},
    {"id": "compare", "label": "Compare", "question": "How do the candidates differ?"},
    {"id": "record", "label": "Record", "question": "What have I concluded?"},
    {"id": "report", "label": "Report", "question": "What can I hand over?"},
)


def start_options() -> dict:
    """Everything the opening screen needs, with no database access."""
    return {
        "modes": [{"id": m.id, "label": m.label, "prompt": m.prompt,
                   "starting_material": m.starting_material,
                   "first_steps": list(m.first_steps)} for m in MODES],
        "stages": list(STAGES),
        "examples": [
            "I want to investigate how an existing tablet formulation could be improved.",
            "I want to investigate a compound and its biological activity.",
            "I want to compare two existing pharmaceutical formulations.",
            "I want to investigate a molecule against a specific biological target.",
            "I have an existing formulation and want to understand its components.",
        ],
        "bench_version": BENCH_VERSION,
        "note": ("You can describe your objective in your own words. A mode only decides which "
                 "starting material MOLECULAB asks for first; it does not restrict what you can "
                 "do afterwards."),
    }


# --------------------------------------------------------------------------- sessions

def create_session(db: Session, *, objective: str, mode: str, owner: str = LOCAL_USER,
                   title: str | None = None) -> ResearchWorkspace:
    """A Bench session is a ResearchWorkspace. No new table, no migration.

    The caller commits. `research_domain` carries the Bench mode, which is why no schema change
    was needed to add modes.
    """
    if mode not in MODE_IDS:
        raise ValueError(f"Unknown research mode {mode!r}. Known: {sorted(MODE_IDS)}")
    objective = (objective or "").strip()
    if not objective:
        raise ValueError("A research objective is required to start an investigation.")
    ws = ResearchWorkspace(owner=owner, title=(title or _title_from(objective)),
                           research_question=objective, research_domain=mode)
    db.add(ws)
    db.flush()
    ws_svc.log(db, ws, "investigation_created",
               f"Research Bench ({mode}) - {objective}", owner)
    return ws


def _title_from(objective: str) -> str:
    """A short, human title from the objective, so the researcher never names a workspace."""
    text = " ".join(objective.split())
    for lead in ("I want to investigate ", "I want to ", "I have ", "Investigate ", "I would like to "):
        if text.lower().startswith(lead.lower()):
            text = text[len(lead):]
            break
    text = text[:1].upper() + text[1:] if text else "Untitled investigation"
    return (text[:77] + "...") if len(text) > 80 else text


def session_state(db: Session, workspace: ResearchWorkspace) -> dict:
    """The one payload the Bench renders: objective, progress, and what is attached."""
    counts = _counts(db, workspace.id)
    mode = next((m for m in MODES if m.id == (workspace.research_domain or "")), None)
    progress = _progress(counts)
    return {
        "session_id": workspace.id,
        "title": workspace.title,
        "objective": workspace.research_question,
        "mode": workspace.research_domain,
        "mode_label": mode.label if mode else "Open research question",
        "starting_material": mode.starting_material if mode else "question",
        "first_steps": list(mode.first_steps) if mode else [],
        "status": workspace.status,
        "read_only": workspace.status == "archived",
        "created_at": workspace.created_at.isoformat() if workspace.created_at else None,
        "counts": counts,
        "stages": progress,
        "next_step": _next_step(progress),
        "bench_version": BENCH_VERSION,
        "app_version": APP_VERSION,
    }


def _counts(db: Session, ws_id: int) -> dict:
    """Counts are queried, not read off relationships: ResearchWorkspace declares none.

    `app.workspace.ws_dict` counts the same way, so the Bench and the existing workspace views
    cannot drift apart.
    """
    res = db.execute(
        select(WorkspaceResource.resource_type, func.count())
        .where(WorkspaceResource.workspace_id == ws_id)
        .group_by(WorkspaceResource.resource_type)).all()
    by_type = {str(getattr(r[0], "value", r[0])): r[1] for r in res}

    def n(model) -> int:
        return db.scalar(select(func.count()).select_from(model)
                         .where(model.workspace_id == ws_id)) or 0

    return {
        "starting_materials": sum(by_type.get(k, 0) for k in ("compound", "plant")),
        "evidence": by_type.get("evidence", 0),
        "publications": by_type.get("publication", 0),
        "gaps": by_type.get("gap", 0) + by_type.get("contradiction", 0),
        "attached_total": sum(by_type.values()),
        "by_type": by_type,
        "notes": n(ResearchNote),
        "hypotheses": n(WorkspaceHypothesis),
        "tasks": n(ResearchTask),
        "reports": n(ResearchReport),
    }


def _progress(counts: dict) -> list[dict]:
    """Stage completion derived from what actually exists. Never a decorative progress bar."""
    done = {
        "research": True,                                   # the session exists, so this is done
        "evidence": counts["evidence"] + counts["publications"] > 0,
        "explore": counts["starting_materials"] > 0,
        "assist": False,                                    # set when an AI answer is kept
        "calculate": False,                                 # set by the caller from run history
        "compare": False,
        "record": counts["notes"] + counts["hypotheses"] > 0,
        "report": counts["reports"] > 0,
    }
    out = []
    for s in STAGES:
        out.append({**s, "complete": bool(done.get(s["id"])),
                    "evidence_for_completion": _why(s["id"], counts)})
    return out


def _why(stage: str, c: dict) -> str:
    if stage == "research":
        return "Objective recorded."
    if stage == "evidence":
        n = c["evidence"] + c["publications"]
        return f"{n} evidence record(s) and publication(s) attached." if n else "Nothing attached yet."
    if stage == "explore":
        return (f"{c['starting_materials']} starting material(s) attached."
                if c["starting_materials"] else "No compound or plant attached yet.")
    if stage == "assist":
        return ("The assistant runs on the operator's local Claude Code CLI. Answers you keep "
                "are saved as your own notes.")
    if stage == "calculate":
        return "Calculation runs are recorded per session in this browser."
    if stage == "compare":
        return "A comparison needs at least two candidates."
    if stage == "record":
        n = c["notes"] + c["hypotheses"]
        return f"{c['notes']} note(s), {c['hypotheses']} hypothesis/es." if n else "Nothing recorded yet."
    return f"{c['reports']} report(s) generated." if c["reports"] else "No report yet."


def _next_step(progress: list[dict]) -> dict:
    for s in progress:
        if not s["complete"]:
            return {"stage": s["id"], "label": s["label"], "question": s["question"]}
    return {"stage": "report", "label": "Report",
            "question": "Every stage has something in it. Review the report."}


# --------------------------------------------------------------------------- starting material

def find_starting_material(db: Session, query: str, kinds: list[str] | None = None,
                           limit: int = 10) -> dict:
    """One search box over everything a researcher might start from.

    Searches the curated local records only. Live provider lookups stay on their own endpoints,
    because they are slow and rate-limited and must not block the opening screen.
    """
    q = (query or "").strip()
    if len(q) < 2:
        return {"query": q, "results": [], "note": "Enter at least two characters."}
    kinds = kinds or ["compound", "product", "formulation", "material", "publication", "plant"]
    like = f"%{q}%"
    out: list[dict] = []

    if "compound" in kinds:
        for c in db.execute(select(Compound).where(
                or_(Compound.name.ilike(like), Compound.iupac_name.ilike(like),
                    Compound.smiles.ilike(like))).limit(limit)).scalars():
            out.append({"kind": "compound", "id": c.id, "label": c.name,
                        "detail": c.molecular_formula or c.iupac_name,
                        "smiles": c.smiles, "has_structure": bool(c.smiles),
                        "route": f"#/compound/{c.id}"})
    if "formulation" in kinds or "product" in kinds:
        for f in db.execute(select(Formulation).where(
                or_(Formulation.name.ilike(like),
                    Formulation.dosage_form.ilike(like))).limit(limit)).scalars():
            n = db.execute(select(func.count()).select_from(FormulationComponent)
                           .where(FormulationComponent.formulation_id == f.id)).scalar() or 0
            out.append({"kind": "formulation", "id": f.id, "label": f.name,
                        "detail": f"{f.dosage_form or 'formulation'} - {n} component(s)",
                        "component_count": n, "route": f"#/formulations?id={f.id}"})
    if "material" in kinds:
        for m in db.execute(select(Material).where(
                Material.name.ilike(like)).limit(limit)).scalars():
            out.append({"kind": "material", "id": m.id, "label": m.name,
                        "detail": m.role or "material", "route": f"#/materials?id={m.id}"})
    if "plant" in kinds:
        # Plants are matched on the accepted botanical name and on their aliases, because a
        # researcher here is as likely to type a local Uzbek name as a Latin binomial.
        hits = {p.id: p for p in db.execute(select(Plant).where(
            or_(Plant.accepted_name.ilike(like), Plant.family.ilike(like))).limit(limit)).scalars()}
        for a in db.execute(select(PlantAlias).where(
                PlantAlias.name.ilike(like)).limit(limit)).scalars():
            p = db.get(Plant, a.plant_id)
            if p is not None and p.id not in hits:
                hits[p.id] = p
        for p in list(hits.values())[:limit]:
            out.append({"kind": "plant", "id": p.id, "label": p.accepted_name,
                        "detail": p.family or p.authority, "route": f"#/plant/{p.id}"})
    if "publication" in kinds:
        for p in db.execute(select(Publication).where(
                Publication.title.ilike(like)).limit(limit)).scalars():
            out.append({"kind": "publication", "id": p.id, "label": p.title[:110],
                        "detail": f"{p.journal or ''} {p.year or ''}".strip(),
                        "route": f"#/publication/{p.id}"})

    return {
        "query": q, "results": out[: limit * 2], "count": len(out),
        "searched": kinds,
        "note": ("These are MOLECULAB's curated records. A product you cannot find here may still "
                 "be searchable against live providers from the Evidence stage."),
    }


def formulation_detail(db: Session, formulation_id: int) -> dict:
    """A formulation as the Bench shows it: composition first, with missing data named."""
    f = db.get(Formulation, formulation_id)
    if f is None:
        raise KeyError(formulation_id)
    comps = db.execute(select(FormulationComponent).where(
        FormulationComponent.formulation_id == f.id)).scalars().all()

    rows, for_calc = [], []
    for c in comps:
        mat = db.get(Material, c.material_id) if c.material_id else None
        compound = None
        if mat is not None and getattr(mat, "compound_id", None):
            compound = db.get(Compound, mat.compound_id)
        rows.append({
            "component_id": c.id,
            "name": (mat.name if mat else None) or "(unnamed component)",
            "role": c.role or (mat.role if mat else None),
            "amount": c.amount, "unit": c.amount_unit,
            "amount_recorded": c.amount is not None,
            "material_id": c.material_id,
            "compound_id": compound.id if compound else None,
            "smiles": compound.smiles if compound else None,
            "has_structure": bool(compound and compound.smiles),
            "rationale": c.rationale,
            "amount_basis": c.amount_basis,
            "source": (mat.provider if mat else None),
        })
        for_calc.append({"name": rows[-1]["name"], "amount": c.amount,
                         "unit": c.amount_unit, "role": rows[-1]["role"]})

    totals = calculations.execute("composition_totals", {"components": for_calc}) if for_calc else None
    missing = [r["name"] for r in rows if not r["amount_recorded"]]
    no_struct = [r["name"] for r in rows if not r["has_structure"]]

    return {
        "formulation": {"id": f.id, "name": f.name, "dosage_form": f.dosage_form,
                        "status": f.status,
                        "research_question": f.research_question,
                        "notes": f.notes},
        "components": rows,
        "component_count": len(rows),
        "composition_totals": totals,
        "missing_information": {
            "components_without_amount": missing,
            "components_without_structure": no_struct,
        },
        "distinction": ("A tablet is a composition, not a single molecule. Each component may have "
                        "its own structure; the formulation has its own composition and properties. "
                        "A structure shown here describes one ingredient, never the tablet."),
        "limitations": [
            ("Amounts are missing for: " + ", ".join(missing) + ". They are not estimated.")
            if missing else "Every component has a recorded amount.",
            ("No molecular structure is linked for: " + ", ".join(no_struct) + ".")
            if no_struct else "Every component has a linked structure.",
            "Public drug databases rarely publish complete tablet compositions. Absence of a "
            "component here does not mean it is absent from the real product.",
        ],
    }


def compare_formulations(db: Session, ids: list[int]) -> dict:
    """Align two or more compositions component by component. No composite score."""
    if not 2 <= len(ids) <= 4:
        raise ValueError("Compare between two and four formulations.")
    details = [formulation_detail(db, i) for i in ids]
    names: list[str] = []
    for d in details:
        for c in d["components"]:
            key = (c["name"] or "").strip().lower()
            if key and key not in names:
                names.append(key)

    table = []
    for key in names:
        row = {"component": key, "cells": []}
        for d in details:
            match = next((c for c in d["components"]
                          if (c["name"] or "").strip().lower() == key), None)
            row["cells"].append({
                "present": match is not None,
                "amount": match["amount"] if match else None,
                "unit": match["unit"] if match else None,
                "role": match["role"] if match else None,
                "state": ("present" if match and match["amount_recorded"]
                          else "amount not recorded" if match else "not documented"),
            })
        row["differs"] = len({(c["present"], c["amount"], c["unit"]) for c in row["cells"]}) > 1
        table.append(row)

    return {
        "formulations": [{"id": d["formulation"]["id"], "name": d["formulation"]["name"],
                          "dosage_form": d["formulation"]["dosage_form"],
                          "component_count": d["component_count"]} for d in details],
        "components": table,
        "differing_components": sum(1 for r in table if r["differs"]),
        "shared_components": sum(1 for r in table if all(c["present"] for c in r["cells"])),
        "interpretation": ("Components are aligned by name. 'Not documented' means this "
                           "composition has no such component recorded, which is not the same as "
                           "the real product not containing it."),
        "limitations": [
            "Alignment is by component name, so a differently-named equivalent reads as absent.",
            "No composite similarity score is computed. Compare the rows directly.",
            "A difference in composition does not by itself imply a difference in performance.",
        ],
    }
