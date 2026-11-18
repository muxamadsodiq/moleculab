"""Research Bench AI — the assistant, given the investigation's real context.

MOLECULAB already had a grounded assistant: retrieve records, check answerability, let a model draft
wording, split the draft into atomic claims, verify each against its cited excerpt, and delete the
ones that are not supported. The model can only ever LOSE a claim, never promote one.

What was missing was context. The assistant did not know which investigation you were in or what
structure you had drawn, so it could not help with the work in front of you.

This module supplies that context and nothing else. Specifically it does NOT:

- let the model produce a chemical number. Every descriptor in a prompt is computed first by RDKit
  through `app.calculations`, and the model is told it may only cite those values, never derive new
  ones.
- let the model write to the database. Notes and hypotheses are saved by the researcher's own
  explicit action through the existing workspace routes.
- bypass verification. Literature questions go through `assistant.answer`, which keeps the
  claim-by-claim check and the abstention path exactly as it was.

Structure questions take a different route from literature questions, on purpose. A question about
a molecule in front of you is answered from computed facts that are supplied in the prompt and
displayed beside the answer, so a reader can check every number against the panel. A question about
what the literature establishes must go through the evidence path, because that is where citations
and abstention live.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import assistant, bench, calculations
from app.llm import LLMError, get_provider
from app.models import (ResearchNote, ResearchWorkspace, WorkspaceHypothesis, WorkspaceResource)

BENCH_AI_VERSION = "1.0.0"

MAX_QUESTION_CHARS = 2000

# The model is given one job: organise and explain facts it was handed. This prompt is the whole
# contract, and it is deliberately blunt about the parts that matter.
STRUCTURE_SYSTEM = """You are MOLECULAB's structure explanation engine.

You will be given a molecular structure, descriptors that RDKit has ALREADY COMPUTED, and a
researcher's question. Your job is to explain and organise what you were given.

ABSOLUTE RULES:
- Never state a numeric property that is not in the data you were given. If a number is not there,
  say it has not been computed.
- Never claim a measured, experimental, or biological fact. You have no measurements. Descriptors
  you were given are COMPUTED values, and you must describe them that way.
- Never claim the molecule binds anything, is active, is safe, is toxic, is a drug, or has any
  pharmacological effect. You have no such data and none can be inferred from a structure.
- Never invent a citation, a publication, an assay, or a measurement.
- If the question cannot be answered from the supplied data, say exactly what is missing.

Answer under these five headings, using only these headings, and omit a heading only if it would be
empty:

WHAT WE KNOW
WHAT THE CALCULATIONS INDICATE
WHAT REMAINS UNCERTAIN
POSSIBLE RESEARCH DIRECTIONS
RECOMMENDED NEXT INVESTIGATION

Be concise and concrete. Name functional groups and structural features you can actually see in the
SMILES. Under POSSIBLE RESEARCH DIRECTIONS, anything you suggest is a hypothesis for the researcher
to test, and you must say so. No preamble, no sign-off."""


@dataclass(frozen=True)
class AIResult:
    answer: str
    provider: str
    model: str
    route: str
    context: dict
    limitations: list[str]
    duration_ms: int


def provider_status() -> dict:
    """Whether real generation is available, stated honestly."""
    try:
        p = get_provider()
    except LLMError as exc:
        return {"available": False, "reason": str(exc),
                "note": ("MOLECULAB does not simulate AI answers. With no provider configured the "
                         "assistant is unavailable and says so.")}
    return {"available": True, "provider": type(p).__name__,
            "model": getattr(p, "model", "unknown")}


# --------------------------------------------------------------------- context builders

def session_context(db: Session, workspace: ResearchWorkspace, limit: int = 4000) -> dict:
    """What this investigation actually contains, as plain data."""
    state = bench.session_state(db, workspace)
    resources = db.execute(
        select(WorkspaceResource).where(WorkspaceResource.workspace_id == workspace.id)
    ).scalars().all()
    notes = db.execute(
        select(ResearchNote).where(ResearchNote.workspace_id == workspace.id)
    ).scalars().all()
    hyps = db.execute(
        select(WorkspaceHypothesis).where(WorkspaceHypothesis.workspace_id == workspace.id)
    ).scalars().all()

    ctx = {
        "objective": workspace.research_question,
        "mode": state["mode_label"],
        "counts": state["counts"],
        "next_suggested_stage": state["next_step"],
        "attached": [{"kind": str(getattr(r.resource_type, "value", r.resource_type)),
                      "label": r.label or r.resource_ref} for r in resources[:40]],
        "researcher_notes": [{"category": str(getattr(n.category, "value", n.category)),
                              "body": (n.body or "")[:400]} for n in notes[:12]],
        "hypotheses": [{"status": str(getattr(h.status, "value", h.status)),
                        "statement": (h.statement or "")[:300]} for h in hyps[:12]],
    }
    return ctx


def structure_context(smiles: str) -> dict:
    """Compute the facts about a structure BEFORE the model sees it.

    This is the mechanism that stops the model inventing chemistry: every number it is allowed to
    mention is produced here by RDKit, and the same numbers are returned to the browser so the
    researcher can check the answer against the panel.
    """
    validation = calculations.execute("structure_validation", {"smiles": smiles})
    if not validation["succeeded"] or not validation["result"].get("valid"):
        raise ValueError(
            "That structure is not valid, so nothing can be computed for it and no question about "
            "it can be answered: "
            + str(validation["result"].get("error") if validation["result"] else validation["error"])
        )
    desc = calculations.execute("molecular_descriptors", {"smiles": smiles})
    lip = calculations.execute("lipinski", {"smiles": smiles})
    scaf = calculations.execute("scaffold", {"smiles": smiles})

    v = validation["result"]
    return {
        "input_smiles": smiles,
        "canonical_smiles": v.get("canonical_smiles"),
        "standardized_smiles": v.get("standardized_smiles"),
        "inchikey": v.get("inchikey"),
        "heavy_atom_count": v.get("atom_count"),
        "standardization_changed_structure": v.get("changed_by_standardization"),
        "computed_descriptors": (desc["result"] or {}).get("descriptors", {}),
        "stereochemistry": (desc["result"] or {}).get("stereochemistry", {}),
        "lipinski_criteria": (lip["result"] or {}).get("criteria", []),
        "murcko_scaffold": (scaf["result"] or {}).get("murcko_scaffold"),
        "software_versions": desc["software_versions"],
        "descriptor_fingerprint": desc["reproducibility"]["fingerprint"],
        "what_is_not_available": [
            "No measured property of this molecule is available.",
            "No binding affinity, docking pose or activity prediction exists "
            "(docking and QSAR are unavailable in MOLECULAB and return no numbers).",
            "No toxicity, solubility, stability or bioavailability measurement exists.",
        ],
    }


# --------------------------------------------------------------------- the two routes

def ask_about_structure(smiles: str, question: str, *,
                        session: dict | None = None) -> AIResult:
    """Answer a question about a structure from facts computed first by RDKit."""
    import time

    question = (question or "").strip()
    if not question:
        raise ValueError("A question is required.")
    if len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f"Question is too long (limit {MAX_QUESTION_CHARS} characters).")

    ctx = structure_context(smiles)
    provider = get_provider()

    lines = [
        "RESEARCHER QUESTION:",
        question,
        "",
        "STRUCTURE (drawn or supplied by the researcher; not a characterised compound):",
        f"  canonical SMILES: {ctx['canonical_smiles']}",
        f"  standardized SMILES: {ctx['standardized_smiles']}",
        f"  InChIKey: {ctx['inchikey']}",
        f"  heavy atoms: {ctx['heavy_atom_count']}",
        f"  Murcko scaffold: {ctx['murcko_scaffold'] or '(none; acyclic)'}",
        "",
        "DESCRIPTORS ALREADY COMPUTED BY RDKIT (these are the only numbers you may cite):",
    ]
    for k, v in (ctx["computed_descriptors"] or {}).items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("LIPINSKI CRITERIA (computed, a heuristic, not a prediction of absorption):")
    for c in ctx["lipinski_criteria"]:
        lines.append(f"  {c.get('criterion')}: value={c.get('value')} "
                     f"satisfied={c.get('satisfied')}")
    stereo = ctx.get("stereochemistry") or {}
    if stereo:
        lines.append("")
        lines.append(f"STEREOCHEMISTRY: {stereo}")
    lines.append("")
    lines.append("DATA THAT DOES NOT EXIST (you must not supply any of it):")
    for n in ctx["what_is_not_available"]:
        lines.append(f"  - {n}")
    if session:
        lines.append("")
        lines.append("THE RESEARCHER'S CURRENT INVESTIGATION:")
        lines.append(f"  objective: {session.get('objective')}")
        if session.get("hypotheses"):
            lines.append("  existing hypotheses (unvalidated):")
            for h in session["hypotheses"][:4]:
                lines.append(f"    - {h.get('statement')}")

    started = time.time()
    answer = provider.complete(STRUCTURE_SYSTEM, "\n".join(lines))
    duration = int((time.time() - started) * 1000)

    return AIResult(
        answer=answer.strip(),
        provider=type(provider).__name__,
        model=getattr(provider, "model", "unknown"),
        route="structure",
        context=ctx,
        limitations=[
            "The assistant was given only the computed descriptors shown beside this answer. It "
            "has no measured data for this molecule and cannot cite any.",
            "Descriptors are COMPUTED by RDKit from the structure. None is an experimental "
            "measurement.",
            "Any suggestion under research directions is a hypothesis for you to test, not a "
            "finding.",
            "This route does not search the literature. For what published evidence establishes, "
            "ask a literature question, which goes through claim-by-claim verification against "
            "cited records.",
        ],
        duration_ms=duration,
    )


def ask_about_literature(db: Session, question: str, *,
                         workspace: ResearchWorkspace | None = None) -> dict:
    """Answer from the indexed evidence, through the existing verified path.

    This deliberately reuses `assistant.answer` unchanged, so claim extraction, per-claim
    verification against cited excerpts, deletion of unsupported claims and the abstention
    decision all behave exactly as they do on the standalone assistant page.
    """
    import time

    question = (question or "").strip()
    if not question:
        raise ValueError("A question is required.")
    if len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f"Question is too long (limit {MAX_QUESTION_CHARS} characters).")

    provider = get_provider()
    started = time.time()
    result = assistant.answer(db, question, provider)
    duration = int((time.time() - started) * 1000)

    payload = result if isinstance(result, dict) else result.model_dump()
    payload["route"] = "literature"
    payload["duration_ms"] = duration
    payload["verification_note"] = (
        "Every claim in this answer was checked against the evidence record it cites. Claims that "
        "the cited record does not support were removed rather than softened. If the indexed "
        "evidence cannot answer the question, the assistant abstains and says so."
    )
    if workspace is not None:
        payload["investigation"] = {"session_id": workspace.id,
                                    "objective": workspace.research_question}
    return payload
