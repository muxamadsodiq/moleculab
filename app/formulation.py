"""Formulation research: constraint evaluation, trade-off surfacing and experiment prioritisation.

**What this module does not do.** It does not predict whether a formulation will work. MOLECULAB
has no model of tablet hardness, disintegration time or bioavailability, and does not pretend to.
There is no composite "formulation score" anywhere in this file.

**What it does.** It turns the researcher's own constraints and measurements into an honest status
picture:

* `evaluate_constraint` compares recorded measurements against an acceptance range. A constraint
  with no measurement is `not_measured` — never `satisfied`. A measurement whose uncertainty spans
  the acceptance boundary is `uncertain`, not a pass. Measurements that disagree give `conflicting`.
* `trade_offs` reports relationships between constraint categories drawn from a small, explicitly
  cited table of **general pharmaceutical principles**. These are labelled as general principles,
  never as predictions about the researcher's specific compound.
* `prioritise_experiments` ranks proposed experiments by how much unresolved constraint uncertainty
  each would remove, weighted by constraint priority, and shows its arithmetic. There is no opaque
  AI-generated priority number.

Every function returns its assumptions and limitations alongside its result.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import (ConstraintPriority, ConstraintStatus, MeasurementKind)

#: Measurement kinds that can establish that a physical requirement is met.
#: A computed descriptor or a model prediction cannot: they are evidence about a molecule, not
#: about the manufactured dosage form.
CONFIRMING_KINDS = {MeasurementKind.EXPERIMENTAL, MeasurementKind.LITERATURE,
                    MeasurementKind.SUPPLIER_SPECIFICATION}
#: Kinds that are recorded and shown, but cannot by themselves close a constraint.
SUPPORTING_ONLY_KINDS = {MeasurementKind.COMPUTED, MeasurementKind.PREDICTED,
                         MeasurementKind.RESEARCHER_ESTIMATE}

PRIORITY_WEIGHT = {
    ConstraintPriority.MANDATORY: 3.0,
    ConstraintPriority.PREFERRED: 2.0,
    ConstraintPriority.EXPLORATORY: 1.0,
    ConstraintPriority.UNKNOWN: 1.0,
    ConstraintPriority.NOT_APPLICABLE: 0.0,
}

#: How much uncertainty remains for a constraint in each status. Used only by the experiment
#: prioritiser, and shown to the user as part of its arithmetic.
UNCERTAINTY = {
    ConstraintStatus.NOT_MEASURED: 1.0,
    ConstraintStatus.CONFLICTING: 0.9,
    ConstraintStatus.UNCERTAIN: 0.6,
    ConstraintStatus.VIOLATED: 0.2,    # known to fail: little uncertainty, but a real problem
    ConstraintStatus.SATISFIED: 0.1,   # evidence can still be thin; never exactly zero
    ConstraintStatus.NOT_APPLICABLE: 0.0,
}

# ---------------------------------------------------------------------------
# General pharmaceutical principles used to surface trade-offs.
#
# These are textbook relationships between formulation properties, NOT predictions about any
# particular compound. Each carries a source so a reader can check it, and each is labelled as a
# general principle in the output. MOLECULAB never asserts that the relationship holds for the
# researcher's material; it flags the pair as worth investigating.
# ---------------------------------------------------------------------------
TRADE_OFF_PRINCIPLES = [
    {
        "between": ("mechanical", "disintegration"),
        "principle": "Increasing compression force raises tablet hardness and typically lengthens "
                     "disintegration time, because a denser compact admits water more slowly.",
        "direction": "Raising one tends to worsen the other.",
        "source": "Standard tablet-compaction behaviour; see e.g. Aulton's Pharmaceutics, "
                  "tablet design and manufacture.",
    },
    {
        "between": ("disintegration", "dissolution"),
        "principle": "Disintegration breaks the dosage form into granules or particles; dissolution "
                     "is the drug passing into solution. Fast disintegration does not guarantee fast "
                     "dissolution, and these are measured by different tests.",
        "direction": "Related but distinct; neither substitutes for the other.",
        "source": "USP <701> disintegration and USP <711> dissolution are separate compendial tests.",
    },
    {
        "between": ("dissolution", "bioavailability"),
        "principle": "For poorly soluble drugs dissolution can limit absorption, but bioavailability "
                     "also depends on permeability, first-pass metabolism and transit. In vitro "
                     "dissolution predicts in vivo performance only where an IVIVC has been established.",
        "direction": "Dissolution is necessary but not sufficient for bioavailability.",
        "source": "Biopharmaceutics Classification System (Amidon et al., 1995).",
    },
    {
        "between": ("solubility", "stability"),
        "principle": "Approaches that raise apparent solubility (amorphous forms, salts, "
                     "co-solvents) often reduce physical or chemical stability; amorphous material "
                     "can recrystallise over time.",
        "direction": "Raising one may worsen the other.",
        "source": "Amorphous solid dispersion literature; physical stability of amorphous drugs.",
    },
    {
        "between": ("compatibility", "active_integrity"),
        "principle": "Excipients can react with the active ingredient (for example Maillard "
                     "reactions between amines and reducing sugars such as lactose), degrading it "
                     "during storage or processing.",
        "direction": "An excipient choice can threaten active integrity.",
        "source": "Drug-excipient compatibility literature; Maillard reaction with lactose.",
    },
    {
        "between": ("processing", "active_integrity"),
        "principle": "Wet granulation, drying and compression apply moisture, heat and mechanical "
                     "stress, any of which can degrade a labile active or change its solid form.",
        "direction": "Processing conditions can threaten active integrity.",
        "source": "Unit operations in solid dosage manufacture; process-induced transformations.",
    },
    {
        "between": ("mechanical", "processing"),
        "principle": "Binder level and granulation method influence compactibility and therefore "
                     "achievable hardness and friability.",
        "direction": "Processing choices constrain achievable mechanical properties.",
        "source": "Granulation and compaction literature.",
    },
    {
        "between": ("stability", "processing"),
        "principle": "Elevated temperature or humidity during processing or storage accelerates "
                     "hydrolysis and oxidation of susceptible actives.",
        "direction": "Harsher conditions tend to reduce stability.",
        "source": "ICH Q1A(R2) stability testing guidance.",
    },
]


@dataclass
class ConstraintEvaluation:
    constraint_id: int
    name: str
    category: str
    priority: str
    status: str
    reason: str
    measurement_count: int
    confirming_count: int
    supporting_count: int
    acceptance: dict
    values: list

    def as_dict(self) -> dict:
        return {
            "constraint_id": self.constraint_id, "name": self.name, "category": self.category,
            "priority": self.priority, "status": self.status, "reason": self.reason,
            "measurement_count": self.measurement_count,
            "confirming_measurements": self.confirming_count,
            "supporting_only_measurements": self.supporting_count,
            "acceptance": self.acceptance, "values": self.values,
            "uncertainty_remaining": UNCERTAINTY.get(self.status, 1.0),
        }


def _in_range(value: float, low: float | None, high: float | None) -> bool:
    if low is not None and value < low:
        return False
    if high is not None and value > high:
        return False
    return True


def _spans_boundary(value: float, uncertainty: float | None,
                    low: float | None, high: float | None) -> bool:
    """True when the measurement's uncertainty interval crosses an acceptance boundary, so the
    constraint cannot honestly be called met or missed."""
    if not uncertainty:
        return False
    lo, hi = value - uncertainty, value + uncertainty
    for bound in (low, high):
        if bound is not None and lo <= bound <= hi:
            return True
    return False


def evaluate_constraint(constraint) -> ConstraintEvaluation:
    """Decide a constraint's status from its measurements. Missing data is never a pass."""
    low, high = constraint.min_value, constraint.max_value
    acceptance = {"min": low, "max": high, "target": constraint.target_value,
                  "unit": constraint.unit, "qualitative": constraint.qualitative_target}
    measurements = list(constraint.measurements or [])
    values = [{
        "id": m.id, "kind": m.kind, "value": m.value, "value_text": m.value_text,
        "unit": m.unit, "uncertainty": m.uncertainty, "n_replicates": m.n_replicates,
        "method": m.method, "conditions": m.conditions, "source": m.source,
        "evidence_record_id": m.evidence_record_id,
        "can_close_constraint": m.kind in CONFIRMING_KINDS,
    } for m in measurements]

    if constraint.priority == ConstraintPriority.NOT_APPLICABLE:
        return ConstraintEvaluation(constraint.id, constraint.name, constraint.category,
                                    constraint.priority, ConstraintStatus.NOT_APPLICABLE,
                                    "Marked not applicable to this formulation.",
                                    len(measurements), 0, 0, acceptance, values)

    confirming = [m for m in measurements if m.kind in CONFIRMING_KINDS and m.value is not None]
    supporting = [m for m in measurements if m.kind in SUPPORTING_ONLY_KINDS]

    if not measurements:
        return ConstraintEvaluation(
            constraint.id, constraint.name, constraint.category, constraint.priority,
            ConstraintStatus.NOT_MEASURED,
            "No measurement has been recorded. A requirement without evidence is an open question, "
            "not a satisfied one.", 0, 0, 0, acceptance, values)

    if not confirming:
        kinds = sorted({m.kind for m in measurements})
        return ConstraintEvaluation(
            constraint.id, constraint.name, constraint.category, constraint.priority,
            ConstraintStatus.NOT_MEASURED,
            f"Only non-confirming evidence is recorded ({', '.join(kinds)}). A computed descriptor, "
            "model prediction or estimate describes a molecule or an expectation, not the "
            "manufactured dosage form, so it cannot close this requirement.",
            len(measurements), 0, len(supporting), acceptance, values)

    if low is None and high is None:
        return ConstraintEvaluation(
            constraint.id, constraint.name, constraint.category, constraint.priority,
            ConstraintStatus.UNCERTAIN,
            "Measurements exist but no acceptance range is defined, so they cannot be judged "
            "against a requirement.", len(measurements), len(confirming), len(supporting),
            acceptance, values)

    verdicts = {_in_range(m.value, low, high) for m in confirming}
    boundary = any(_spans_boundary(m.value, m.uncertainty, low, high) for m in confirming)

    if len(verdicts) > 1:
        status, reason = (ConstraintStatus.CONFLICTING,
                          f"{len(confirming)} confirming measurements disagree about whether the "
                          "acceptance range is met. MOLECULAB does not choose between them.")
    elif boundary:
        status, reason = (ConstraintStatus.UNCERTAIN,
                          "The measurement's uncertainty interval crosses an acceptance boundary, "
                          "so the requirement cannot be called met or missed.")
    elif True in verdicts:
        status, reason = (ConstraintStatus.SATISFIED,
                          f"{len(confirming)} confirming measurement(s) fall within the acceptance "
                          "range.")
    else:
        status, reason = (ConstraintStatus.VIOLATED,
                          f"{len(confirming)} confirming measurement(s) fall outside the acceptance "
                          "range.")

    return ConstraintEvaluation(constraint.id, constraint.name, constraint.category,
                                constraint.priority, status, reason, len(measurements),
                                len(confirming), len(supporting), acceptance, values)


def evaluate_formulation(formulation) -> dict:
    """Status of every constraint on a formulation, plus an honest roll-up.

    The roll-up is a count per status, never a score. A formulation with nine satisfied
    constraints and one unmeasured mandatory one is not "90% ready".
    """
    evaluations = [evaluate_constraint(c) for c in (formulation.constraints or [])]
    counts: dict[str, int] = {}
    for e in evaluations:
        counts[e.status] = counts.get(e.status, 0) + 1

    unresolved_mandatory = [e for e in evaluations
                            if e.priority == ConstraintPriority.MANDATORY
                            and e.status in (ConstraintStatus.NOT_MEASURED,
                                             ConstraintStatus.UNCERTAIN,
                                             ConstraintStatus.CONFLICTING)]
    violated_mandatory = [e for e in evaluations
                          if e.priority == ConstraintPriority.MANDATORY
                          and e.status == ConstraintStatus.VIOLATED]

    return {
        "formulation_id": formulation.id,
        "name": formulation.name,
        "dosage_form": formulation.dosage_form,
        "constraint_count": len(evaluations),
        "status_counts": counts,
        "constraints": [e.as_dict() for e in evaluations],
        "unresolved_mandatory": [e.name for e in unresolved_mandatory],
        "violated_mandatory": [e.name for e in violated_mandatory],
        "readiness_statement": _readiness(evaluations, unresolved_mandatory, violated_mandatory),
        "no_composite_score": (
            "MOLECULAB does not compute an overall formulation score. Constraints are reported "
            "individually because a single number would hide which requirement is unmet."),
    }


def _readiness(evaluations, unresolved_mandatory, violated_mandatory) -> str:
    if not evaluations:
        return "No constraints have been defined for this formulation yet."
    if violated_mandatory:
        return (f"{len(violated_mandatory)} mandatory requirement(s) are contradicted by "
                "measurements. This formulation does not currently meet its own specification.")
    if unresolved_mandatory:
        return (f"{len(unresolved_mandatory)} mandatory requirement(s) have no confirming "
                "measurement or remain uncertain. The formulation is not shown as meeting its "
                "specification, because unmeasured is not the same as met.")
    return ("Every mandatory requirement has at least one confirming measurement inside its "
            "acceptance range. This reflects the evidence recorded here, not an independent "
            "assessment of the formulation.")


def trade_offs(formulation) -> dict:
    """Surface general pharmaceutical principles connecting the categories actually in use.

    Only principles whose BOTH categories appear among this formulation's constraints are shown,
    so the list is relevant rather than a textbook dump. Each is explicitly a general principle.
    """
    categories = {c.category for c in (formulation.constraints or [])}
    by_category: dict[str, list] = {}
    for c in (formulation.constraints or []):
        by_category.setdefault(c.category, []).append(c)

    relevant = []
    for principle in TRADE_OFF_PRINCIPLES:
        a, b = principle["between"]
        if a in categories and b in categories:
            relevant.append({
                **principle,
                "between": [a, b],
                "constraints_involved": {
                    a: [c.name for c in by_category.get(a, [])],
                    b: [c.name for c in by_category.get(b, [])],
                },
                "evidence_status": "general pharmaceutical principle",
                "applies_to_this_material": "not established — investigate",
            })

    return {
        "formulation_id": formulation.id,
        "categories_in_use": sorted(categories),
        "trade_offs": relevant,
        "interpretation": (
            "These are general relationships between formulation properties, cited from the "
            "pharmaceutical literature. They are NOT predictions about this active ingredient or "
            "this excipient set. MOLECULAB flags them so the pair can be investigated; whether the "
            "relationship holds here is an experimental question."),
        "limitations": [
            "Only relationships between categories you have actually constrained are listed.",
            "A relationship absent from this list is not evidence that no interaction exists.",
            "No magnitude is given: the literature principle states a direction, not a coefficient "
            "for your material.",
        ],
    }


def prioritise_experiments(experiments, evaluations_by_id: dict) -> dict:
    """Rank proposed experiments by the constraint uncertainty each would remove.

    The arithmetic, shown in full for every experiment:

        information_value = sum over addressed constraints of
                            (priority weight x remaining uncertainty)

    Cost-effectiveness is reported only where the researcher supplied a cost estimate, and is
    `information_value / cost`. MOLECULAB does not know laboratory prices and never invents one.

    This is a transparent bookkeeping aid, not a decision. The assumptions are returned with it.
    """
    ranked = []
    for experiment in experiments:
        addressed = [cid for cid in (experiment.addresses_constraint_ids or [])
                     if cid in evaluations_by_id]
        contributions = []
        total = 0.0
        for cid in addressed:
            evaluation = evaluations_by_id[cid]
            weight = PRIORITY_WEIGHT.get(evaluation.priority, 1.0)
            remaining = UNCERTAINTY.get(evaluation.status, 1.0)
            value = round(weight * remaining, 4)
            total += value
            contributions.append({
                "constraint_id": cid, "constraint": evaluation.name,
                "priority": evaluation.priority, "priority_weight": weight,
                "status": evaluation.status, "uncertainty_remaining": remaining,
                "contribution": value,
            })

        redundant = bool(addressed) and all(
            evaluations_by_id[cid].status == ConstraintStatus.SATISFIED for cid in addressed)
        entry = {
            "experiment_id": experiment.id, "title": experiment.title,
            "status": experiment.status,
            "addresses_constraints": len(addressed),
            "information_value": round(total, 4),
            "contributions": contributions,
            "estimated_cost": experiment.estimated_cost,
            "cost_currency": experiment.cost_currency,
            "estimated_duration_hours": experiment.estimated_duration_hours,
            "estimate_basis": experiment.estimate_basis,
            "cost_effectiveness": (round(total / experiment.estimated_cost, 6)
                                   if experiment.estimated_cost else None),
            "cost_effectiveness_note": (
                None if experiment.estimated_cost else
                "No cost estimate supplied by the researcher, so cost-effectiveness is not computed."),
            "likely_redundant": redundant,
            "redundancy_note": (
                "Every constraint this experiment addresses already has a confirming measurement "
                "inside its acceptance range. Re-measuring may still be justified for replication, "
                "but it would not resolve an open question." if redundant else None),
        }
        ranked.append(entry)

    ranked.sort(key=lambda e: e["information_value"], reverse=True)
    for position, entry in enumerate(ranked, start=1):
        entry["rank"] = position

    return {
        "experiments": ranked,
        "method": {
            "formula": "information_value = Σ (priority_weight × uncertainty_remaining) over the "
                       "constraints an experiment addresses",
            "priority_weights": {k: v for k, v in PRIORITY_WEIGHT.items()},
            "uncertainty_by_status": {k: v for k, v in UNCERTAINTY.items()},
            "ordering": "descending information value; ties keep input order",
        },
        "assumptions": [
            "An experiment is assumed to fully resolve each constraint it is linked to. Real "
            "experiments often only partially reduce uncertainty.",
            "Constraint priority weights (mandatory 3, preferred 2, exploratory 1) are a convention "
            "chosen for this tool, not a derived quantity.",
            "The uncertainty assigned to each status is likewise a convention, shown above so it "
            "can be disagreed with.",
            "Cost and duration are researcher estimates. MOLECULAB supplies no price data.",
            "Linkage between an experiment and a constraint is declared by the researcher; the "
            "tool does not infer which experiment measures what.",
        ],
        "limitations": [
            "This ranking is bookkeeping over your own inputs. It is not a recommendation, and it "
            "cannot know which result would change your decision.",
            "An experiment that addresses no recorded constraint scores zero. That means it is "
            "unlinked, not that it is worthless.",
        ],
    }
