"""Experimental activity measurements: compatibility rules, unit normalization, aggregation.

This module exists to stop one specific class of scientific error: **silently combining measurements
that are not comparable.** It is the gatekeeper between raw provider rows (`app/clients/chembl.py`) and
anything that ranks, aggregates or trains on them.

The rules, each enforced by `compatibility_report` and tested:

1. **Endpoint identity.** IC50, Ki, Kd, EC50 and percent-inhibition are different quantities measured
   under different conditions. They are never interconverted and never pooled. A Ki is not an IC50
   (the Cheng-Prusoff relation needs substrate concentration and Km, which ChEMBL rows do not carry).
2. **Measurement relation.** Only `=` is an exact measurement. `>`, `<`, `>=`, `<=` and `~` are censored
   or approximate observations: they are retained and labelled, but excluded from numeric aggregation
   and from regression targets, because treating "IC50 > 10000 nM" as "10000 nM" invents precision.
3. **Units.** Only conversions within one physical quantity and with an exact factor are performed
   (molar concentration family: M/mM/uM/nM/pM). Mass-per-volume units (ug/mL) are NOT converted to
   molar without a molecular weight, and are kept separate. Dimensionless endpoints (%) never convert.
4. **Organism.** Human and non-human measurements remain distinguishable and are not pooled by default.
5. **Assay type and target identity.** Binding (B) and functional (F) assays answer different questions;
   ADMET (A) and physicochemical (P) are different again. Measurements against a PROTEIN COMPLEX or
   PROTEIN FAMILY are not measurements against a single protein.
6. **Data-quality flags.** ChEMBL's own `data_validity_comment` and `potential_duplicate` are honoured.

Nothing here invents a value. Every exclusion is recorded with a reason, and the counts of what was
included and excluded travel with the result so a report can state them exactly.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

#: Endpoints understood as concentration-type potency measures. Each stays in its own pool.
CONCENTRATION_ENDPOINTS = {"IC50", "EC50", "Ki", "Kd", "AC50", "GI50", "MIC", "XC50", "ED50"}
#: Endpoints that are not concentrations and must never be unit-converted or pooled with the above.
NON_CONCENTRATION_ENDPOINTS = {"Inhibition", "Activity", "Potency", "Efficacy", "T1/2", "Papp", "CC50"}

#: Exact conversion factors to nanomolar, within the molar concentration family only.
TO_NANOMOLAR = {"M": 1e9, "mM": 1e6, "uM": 1e3, "µM": 1e3, "nM": 1.0, "pM": 1e-3, "fM": 1e-6}
#: Units that are concentrations but not molar: convertible only with a molecular weight, so not here.
MASS_PER_VOLUME = {"ug.mL-1", "ug/mL", "mg.kg-1", "mg/kg", "ng/mL", "ug ml-1"}
DIMENSIONLESS = {"%", None, "", "nan"}

EXACT_RELATIONS = {"="}
CENSORED_RELATIONS = {">", "<", ">=", "<=", "~"}

#: ChEMBL assay_type codes.
ASSAY_TYPES = {"B": "binding", "F": "functional", "A": "ADMET", "T": "toxicity",
               "P": "physicochemical", "U": "unclassified"}

#: Target types that represent exactly one protein. Anything else mixes biological entities.
SINGLE_PROTEIN_TARGET_TYPES = {"SINGLE PROTEIN"}


@dataclass
class Measurement:
    """One experimental measurement, with its context retained."""
    molecule_chembl_id: str | None
    smiles: str | None
    standard_type: str | None
    standard_relation: str | None
    standard_value: float | None
    standard_units: str | None
    assay_chembl_id: str | None = None
    assay_type: str | None = None
    target_chembl_id: str | None = None
    target_organism: str | None = None
    document_chembl_id: str | None = None
    pchembl_value: float | None = None
    data_validity_comment: str | None = None
    potential_duplicate: bool = False
    # Derived, only when a legitimate exact conversion exists:
    value_nm: float | None = None
    p_activity: float | None = None   # -log10(molar), computed from value_nm only
    is_exact: bool = False

    @property
    def provenance(self) -> dict:
        return {"assay_chembl_id": self.assay_chembl_id, "target_chembl_id": self.target_chembl_id,
                "document_chembl_id": self.document_chembl_id, "assay_type": self.assay_type,
                "target_organism": self.target_organism, "standard_type": self.standard_type,
                "standard_relation": self.standard_relation, "standard_value": self.standard_value,
                "standard_units": self.standard_units}


@dataclass
class CurationResult:
    """Outcome of curating a set of raw rows into a comparable dataset."""
    endpoint: str
    units: str | None
    organism: str | None
    assay_type: str | None
    target_chembl_id: str | None
    included: list[Measurement] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)   # {row_index, reason, detail}
    censored: list[Measurement] = field(default_factory=list)

    @property
    def exclusion_counts(self) -> dict:
        return dict(Counter(e["reason"] for e in self.excluded))

    def summary(self) -> dict:
        unique = {m.molecule_chembl_id for m in self.included if m.molecule_chembl_id}
        return {
            "endpoint": self.endpoint,
            "requested_units": self.units,
            "organism": self.organism,
            "assay_type": self.assay_type,
            "target_chembl_id": self.target_chembl_id,
            "raw_rows": len(self.included) + len(self.excluded) + len(self.censored),
            "included_measurements": len(self.included),
            "unique_compounds": len(unique),
            "censored_measurements_excluded_from_numeric_use": len(self.censored),
            "excluded_rows": len(self.excluded),
            "exclusion_reasons": self.exclusion_counts,
        }


# ------------------------------------------------------------------ compatibility

def endpoint_family(standard_type: str | None) -> str:
    if not standard_type:
        return "unknown"
    if standard_type in CONCENTRATION_ENDPOINTS:
        return "concentration"
    if standard_type in NON_CONCENTRATION_ENDPOINTS:
        return "non_concentration"
    return "unknown"


def units_comparable(unit_a: str | None, unit_b: str | None) -> bool:
    """True only when two units describe the same physical quantity with an exact conversion."""
    if unit_a == unit_b:
        return True
    if unit_a in TO_NANOMOLAR and unit_b in TO_NANOMOLAR:
        return True
    # ug/mL <-> molar needs a molecular weight; % has no conversion at all.
    return False


def compatibility_report(a: Measurement, b: Measurement) -> dict:
    """Decide whether two measurements may be pooled, and say exactly why not.

    Returns {"compatible": bool, "reasons": [...]} where reasons name each blocking difference.
    """
    reasons: list[str] = []

    if (a.standard_type or "") != (b.standard_type or ""):
        reasons.append(
            f"different endpoints ({a.standard_type} vs {b.standard_type}): different quantities "
            "measured under different conditions; no conversion is applied between them")
    if not units_comparable(a.standard_units, b.standard_units):
        reasons.append(
            f"incomparable units ({a.standard_units} vs {b.standard_units}): conversion would require "
            "information the records do not carry (e.g. a molecular weight)")
    if (a.target_organism or "") != (b.target_organism or ""):
        reasons.append(
            f"different organisms ({a.target_organism} vs {b.target_organism}): measurements in "
            "different species are not interchangeable")
    if (a.assay_type or "") != (b.assay_type or ""):
        reasons.append(
            f"different assay types ({ASSAY_TYPES.get(a.assay_type, a.assay_type)} vs "
            f"{ASSAY_TYPES.get(b.assay_type, b.assay_type)}): these answer different questions")
    if (a.target_chembl_id or "") != (b.target_chembl_id or ""):
        reasons.append(
            f"different targets ({a.target_chembl_id} vs {b.target_chembl_id})")
    return {"compatible": not reasons, "reasons": reasons}


def _to_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def to_nanomolar(value, units: str | None) -> tuple[float | None, str | None]:
    """Convert a concentration to nM. Returns (value, error).

    Only exact, same-quantity conversions are performed. Mass-per-volume and dimensionless units are
    refused rather than approximated.
    """
    number = _to_float(value)
    if number is None:
        return None, "non-numeric value"
    if number <= 0:
        # A non-positive concentration cannot be log-transformed and is not a usable measurement.
        return None, "non-positive concentration"
    if units in TO_NANOMOLAR:
        return number * TO_NANOMOLAR[units], None
    if units in MASS_PER_VOLUME:
        return None, f"unit '{units}' requires a molecular weight to convert to molar; not converted"
    return None, f"unit '{units}' is not a molar concentration"


def p_activity_from_nm(value_nm: float | None) -> float | None:
    """pActivity = -log10(molar). Computed only from an exact molar value."""
    if not value_nm or value_nm <= 0:
        return None
    return round(-math.log10(value_nm * 1e-9), 4)


# ----------------------------------------------------------------------- curation

def curate(rows: list[dict], endpoint: str, target_chembl_id: str | None = None,
           organism: str | None = "Homo sapiens", assay_type: str | None = "B",
           require_single_protein_target_type: str | None = None,
           exclude_censored: bool = True) -> CurationResult:
    """Turn raw ChEMBL activity rows into one comparable dataset, recording every exclusion.

    Every filter is explicit and every dropped row is accounted for, so a model or ranking can state
    exactly what it was built from. `require_single_protein_target_type` is the caller-supplied
    `target_type` of the requested target: if it is not SINGLE PROTEIN, curation refuses the whole set
    rather than mixing measurements made against different biological entities.
    """
    result = CurationResult(endpoint=endpoint, units="nM", organism=organism,
                            assay_type=assay_type, target_chembl_id=target_chembl_id)

    if require_single_protein_target_type is not None and \
            require_single_protein_target_type not in SINGLE_PROTEIN_TARGET_TYPES:
        result.excluded = [{"row_index": -1, "reason": "target_not_single_protein",
                            "detail": f"target_type '{require_single_protein_target_type}' aggregates "
                                      "several proteins; measurements against it are not measurements "
                                      "against one protein"}]
        return result

    if endpoint not in CONCENTRATION_ENDPOINTS:
        result.excluded = [{"row_index": -1, "reason": "unsupported_endpoint",
                            "detail": f"'{endpoint}' is not handled as a concentration endpoint; "
                                      f"supported: {sorted(CONCENTRATION_ENDPOINTS)}"}]
        return result

    for index, row in enumerate(rows):
        def drop(reason: str, detail: str = "") -> None:
            result.excluded.append({"row_index": index, "reason": reason, "detail": detail})

        if (row.get("standard_type") or "") != endpoint:
            drop("endpoint_mismatch", f"{row.get('standard_type')} != {endpoint}")
            continue
        if target_chembl_id and (row.get("target_chembl_id") or "") != target_chembl_id:
            drop("target_mismatch", str(row.get("target_chembl_id")))
            continue
        if organism is not None and (row.get("target_organism") or "") != organism:
            drop("organism_mismatch", str(row.get("target_organism")))
            continue
        if assay_type is not None and (row.get("assay_type") or "") != assay_type:
            drop("assay_type_mismatch", str(row.get("assay_type")))
            continue
        if row.get("data_validity_comment"):
            drop("data_validity_flag", str(row.get("data_validity_comment")))
            continue
        if row.get("potential_duplicate"):
            drop("potential_duplicate", "flagged by ChEMBL")
            continue
        if not row.get("canonical_smiles"):
            drop("missing_structure", "no SMILES on the activity row")
            continue

        relation = row.get("standard_relation")
        value_nm, unit_error = to_nanomolar(row.get("standard_value"), row.get("standard_units"))
        if unit_error:
            drop("unit_not_convertible", unit_error)
            continue

        measurement = Measurement(
            molecule_chembl_id=row.get("molecule_chembl_id"),
            smiles=row.get("canonical_smiles"),
            standard_type=row.get("standard_type"),
            standard_relation=relation,
            standard_value=_to_float(row.get("standard_value")),
            standard_units=row.get("standard_units"),
            assay_chembl_id=row.get("assay_chembl_id"),
            assay_type=row.get("assay_type"),
            target_chembl_id=row.get("target_chembl_id"),
            target_organism=row.get("target_organism"),
            document_chembl_id=row.get("document_chembl_id"),
            pchembl_value=_to_float(row.get("pchembl_value")),
            data_validity_comment=row.get("data_validity_comment"),
            potential_duplicate=bool(row.get("potential_duplicate")),
            value_nm=value_nm,
            p_activity=p_activity_from_nm(value_nm),
            is_exact=relation in EXACT_RELATIONS,
        )

        if relation not in EXACT_RELATIONS:
            if relation in CENSORED_RELATIONS:
                # Retained and labelled, but kept out of numeric aggregation: treating "> 10000"
                # as "10000" would invent precision the experiment did not provide.
                result.censored.append(measurement)
                if exclude_censored:
                    continue
            else:
                drop("unknown_relation", str(relation))
                continue

        result.included.append(measurement)

    return result


def aggregate_by_compound(measurements: list[Measurement],
                          max_fold_spread: float = 100.0) -> tuple[list[dict], list[dict]]:
    """Collapse replicate measurements of the same compound into one value.

    Uses the **median** of pActivity, which is robust to a single outlying replicate. Compounds whose
    replicates disagree by more than `max_fold_spread` are returned separately as unresolved rather
    than averaged, because a 1000-fold disagreement is a data problem, not a value to be smoothed.
    """
    by_compound: dict[str, list[Measurement]] = {}
    for m in measurements:
        if m.molecule_chembl_id and m.p_activity is not None:
            by_compound.setdefault(m.molecule_chembl_id, []).append(m)

    resolved, unresolved = [], []
    for compound, group in by_compound.items():
        values = sorted(m.p_activity for m in group)
        nanomolar = [m.value_nm for m in group if m.value_nm]
        spread = (max(nanomolar) / min(nanomolar)) if len(nanomolar) > 1 and min(nanomolar) > 0 else 1.0
        middle = len(values) // 2
        median = values[middle] if len(values) % 2 else round((values[middle - 1] + values[middle]) / 2, 4)
        record = {
            "molecule_chembl_id": compound,
            "smiles": group[0].smiles,
            "p_activity_median": median,
            "replicate_count": len(group),
            "fold_spread": round(spread, 2),
            "assay_ids": sorted({m.assay_chembl_id for m in group if m.assay_chembl_id}),
            "document_ids": sorted({m.document_chembl_id for m in group if m.document_chembl_id}),
            "measurement_type": "experimental",   # never a prediction
        }
        (unresolved if spread > max_fold_spread else resolved).append(
            {**record, "reason": f"replicates disagree by {spread:.0f}-fold"} if spread > max_fold_spread
            else record)
    resolved.sort(key=lambda r: r["p_activity_median"], reverse=True)
    return resolved, unresolved


def dataset_profile(rows: list[dict]) -> dict:
    """Descriptive profile of raw rows, for an audit before any curation decision is made."""
    def counts(key):
        return dict(Counter(str(r.get(key)) for r in rows).most_common(12))
    return {
        "raw_rows": len(rows),
        "unique_compounds": len({r.get("molecule_chembl_id") for r in rows if r.get("molecule_chembl_id")}),
        "unique_assays": len({r.get("assay_chembl_id") for r in rows if r.get("assay_chembl_id")}),
        "unique_documents": len({r.get("document_chembl_id") for r in rows if r.get("document_chembl_id")}),
        "by_standard_type": counts("standard_type"),
        "by_relation": counts("standard_relation"),
        "by_units": counts("standard_units"),
        "by_organism": counts("target_organism"),
        "by_assay_type": counts("assay_type"),
        "by_target": counts("target_chembl_id"),
        "with_data_validity_comment": sum(1 for r in rows if r.get("data_validity_comment")),
        "flagged_potential_duplicate": sum(1 for r in rows if r.get("potential_duplicate")),
        "missing_smiles": sum(1 for r in rows if not r.get("canonical_smiles")),
    }
