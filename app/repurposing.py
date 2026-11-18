"""Drug-repurposing evidence analysis: organise public records about compounds acting on one target, in
the context of one disease.

What this module produces is an **evidence inventory**, not a recommendation:

* It lists compounds that public providers (Open Targets, ChEMBL) record against a target, and for each
  one keeps the documented target relationships, indications, development-stage labels, experimental
  activity record counts and registered-study counts **as separate, sourced dimensions**.
* It never combines dimensions into a score and never ranks candidates. Candidates are listed in
  alphabetical order of their name, which carries no meaning.
* A compound-target relationship, a target-disease association, a development stage or a registered
  study is not evidence that a compound is effective for a disease. Evidence recorded for one disease is
  never transferred to another: indications for other diseases are listed separately.
* Regulatory approval is jurisdiction-specific. Provider labels such as Open Targets "APPROVAL" or ChEMBL
  `max_phase = 4` are reported under those names; a candidate is classified as an approved drug **only**
  when a jurisdiction-specific regulatory record is supplied, which none of the current providers is.
* A provider call that fails makes the affected dimension **unavailable**. It is never read as "no
  evidence".

Every relationship carries the id of the source call it came from; `sources` holds each call's provenance
(provider, request URL, retrieval time, HTTP status, payload hash, cache flag). Re-running the same
configuration against the same provider releases reproduces the same content fingerprint.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from app.config import APP_VERSION
from app.textnorm import fold

REPURPOSING_VERSION = "1.1"  # 1.1: registered-study records kept with match classes; malformed-row handling
#: Version of the classification rules (CLASS_MEANING + classify). Stored in every analysis and snapshot.
CLASSIFICATION_RULES_VERSION = "1.0"

DISCLAIMER = ("This analysis describes indexed public research records about compounds and a biological "
              "target. It is not a treatment recommendation, not proof of clinical efficacy or safety, and "
              "not a statement of regulatory approval in any jurisdiction.")


class CandidateClass(StrEnum):
    APPROVED_DRUG = "approved_drug"
    INVESTIGATIONAL_DRUG = "investigational_drug"
    CLINICAL_STAGE_CANDIDATE = "clinical_stage_candidate"
    PRECLINICAL_COMPOUND = "preclinical_compound"
    EXPERIMENTAL_MOLECULE = "experimental_molecule"
    UNKNOWN = "classification_unknown"


CLASS_MEANING = {
    CandidateClass.APPROVED_DRUG: "A jurisdiction-specific regulatory record was supplied for this compound.",
    CandidateClass.INVESTIGATIONAL_DRUG: "Providers record clinical development (phase 1-3) and no approval-level stage.",
    CandidateClass.CLINICAL_STAGE_CANDIDATE: (
        "Providers record an approval-level development stage (Open Targets 'APPROVAL'/'PHASE_4' or ChEMBL "
        "max_phase 4), but no jurisdiction-specific regulatory record was available, so approval is not "
        "asserted."),
    CandidateClass.PRECLINICAL_COMPOUND: "Providers record a preclinical stage and no clinical stage.",
    CandidateClass.EXPERIMENTAL_MOLECULE: (
        "No development stage is recorded, but ChEMBL holds experimental activity records against the target."),
    CandidateClass.UNKNOWN: "No usable development-stage or activity information was available.",
}

#: Open Targets development-stage labels, ordered. Kept verbatim; never renamed to imply approval.
OT_STAGE_ORDER = ["PRECLINICAL", "EARLY_PHASE_1", "PHASE_1", "PHASE_1_2", "PHASE_2", "PHASE_2_3", "PHASE_3",
                  "PHASE_4", "APPROVAL"]
APPROVAL_LEVEL_OT = {"APPROVAL", "PHASE_4"}
CLINICAL_OT = {"EARLY_PHASE_1", "PHASE_1", "PHASE_1_2", "PHASE_2", "PHASE_2_3", "PHASE_3"}
#: ChEMBL max_phase -> the comparable Open Targets band (only used to detect disagreement between sources).
CHEMBL_PHASE_BAND = {4.0: "approval_level", 3.0: "clinical", 2.0: "clinical", 1.0: "clinical",
                     0.5: "clinical", -1.0: "unknown"}


class RepurposingError(Exception):
    def __init__(self, message: str, status: int = 422, detail: dict | None = None):
        super().__init__(message)
        self.status, self.detail = status, detail or {}


@dataclass
class RepurposingConfig:
    target_query: str
    disease_query: str
    include_chembl: bool = True
    include_activity_counts: bool = True
    include_registered_studies: bool = False
    studies_per_candidate: int = 10
    association_rows: int = 200
    max_candidates: int = 100
    regulatory_records: list[dict] = field(default_factory=list)  # [{chembl_id, jurisdiction, authority, reference}]

    def validate(self) -> list[str]:
        problems = []
        for name in ("target_query", "disease_query"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v.strip() or len(v) > 200:
                problems.append(f"{name} must be a non-empty string of at most 200 characters")
        if not 1 <= self.association_rows <= 1000:
            problems.append("association_rows must be between 1 and 1000")
        if not 1 <= self.studies_per_candidate <= 50:
            problems.append("studies_per_candidate must be between 1 and 50")
        if not 1 <= self.max_candidates <= 500:
            problems.append("max_candidates must be between 1 and 500")
        for r in self.regulatory_records:
            missing = [k for k in ("chembl_id", "jurisdiction", "authority", "reference") if not r.get(k)]
            if missing:
                problems.append(f"regulatory record {r} lacks {missing}; approval needs a jurisdiction-specific source")
        return problems

    def as_dict(self) -> dict:
        return {"target_query": self.target_query, "disease_query": self.disease_query,
                "include_chembl": self.include_chembl, "include_activity_counts": self.include_activity_counts,
                "include_registered_studies": self.include_registered_studies,
                "studies_per_candidate": self.studies_per_candidate,
                "association_rows": self.association_rows, "max_candidates": self.max_candidates,
                "regulatory_records": self.regulatory_records}


# ------------------------------------------------------------------------------------- source ledger

class Ledger:
    """Every provider call made during an analysis, with its provenance. Relationships cite ledger ids."""

    def __init__(self):
        self.entries: list[dict] = []

    def record(self, purpose: str, res) -> str:
        sid = f"src-{len(self.entries) + 1}"
        self.entries.append({"id": sid, "purpose": purpose, "ok": bool(res.ok), "error": res.error,
                             **res.provenance()})
        return sid


# ---------------------------------------------------------------------------------- classification

def _chembl_phase(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def classify(ot_stages: list[str | None], chembl_max_phase, has_activity_records: bool | None,
             regulatory: list[dict]) -> dict:
    """Classify one candidate from recorded labels. Approval is only asserted with a regulatory record."""
    stages = [s for s in ot_stages if s]
    phase = _chembl_phase(chembl_max_phase)
    basis = []
    if regulatory:
        cls = CandidateClass.APPROVED_DRUG
        basis.append("jurisdiction-specific regulatory record(s): " + "; ".join(
            f"{r['authority']} ({r['jurisdiction']}) {r['reference']}" for r in regulatory))
    elif any(s in APPROVAL_LEVEL_OT for s in stages) or phase == 4.0:
        cls = CandidateClass.CLINICAL_STAGE_CANDIDATE
    elif any(s in CLINICAL_OT for s in stages) or (phase is not None and 0.5 <= phase < 4):
        cls = CandidateClass.INVESTIGATIONAL_DRUG
    elif "PRECLINICAL" in stages:
        cls = CandidateClass.PRECLINICAL_COMPOUND
    elif has_activity_records:
        cls = CandidateClass.EXPERIMENTAL_MOLECULE
    else:
        cls = CandidateClass.UNKNOWN
    if stages:
        basis.append("Open Targets maximum clinical stage label(s): " + ", ".join(sorted(set(stages))))
    if phase is not None:
        basis.append(f"ChEMBL max_phase {phase:g}")
    approval = ("asserted from a supplied regulatory record" if regulatory else
                "not established: no jurisdiction-specific regulatory record was available")
    return {"category": cls, "meaning": CLASS_MEANING[cls], "basis": basis, "regulatory_approval": approval}


def _stage_band(stages: list[str | None]) -> str | None:
    stages = [s for s in stages if s]
    if not stages:
        return None
    if any(s in APPROVAL_LEVEL_OT for s in stages):
        return "approval_level"
    if any(s in CLINICAL_OT for s in stages):
        return "clinical"
    return "preclinical" if "PRECLINICAL" in stages else "unknown"


# --------------------------------------------------------------------------------------- resolution

def _resolve(ot, ledger: Ledger, query: str, entity: str) -> tuple[dict, str]:
    res = ot.search(query, [entity], 10)
    sid = ledger.record(f"resolve {entity} '{query}'", res)
    if not res.ok:
        raise RepurposingError(f"Open Targets {entity} search failed: {res.error}", 502)
    hits = [h for h in res.data if h.get("entity") == entity]
    exact = [h for h in hits if fold(h.get("name") or "") == fold(query) or fold(h.get("id") or "") == fold(query)]
    if len(exact) != 1:
        raise RepurposingError(
            f"'{query}' does not resolve to exactly one Open Targets {entity}; identifiers are never guessed. "
            "Use one of the listed names or ids.", 422,
            {"candidates": [{"id": h["id"], "name": h["name"]} for h in hits]})
    return exact[0], sid


def _resolve_chembl_target(chembl, ledger: Ledger, symbol: str) -> tuple[dict | None, str, str | None]:
    res = chembl.resolve_target(symbol)
    sid = ledger.record(f"resolve ChEMBL target for {symbol}", res)
    if not res.ok:
        return None, sid, f"ChEMBL target resolution unavailable: {res.error}"
    matches = [t for t in res.data if t.get("target_type") == "SINGLE PROTEIN"
               and t.get("organism") == "Homo sapiens" and symbol.upper() in {g.upper() for g in t.get("gene_symbols") or []}]
    if len(matches) != 1:
        return None, sid, (f"ChEMBL has {len(matches)} human SINGLE PROTEIN targets with gene symbol {symbol}; "
                           "none was chosen (identifiers are never guessed)")
    return matches[0], sid, None


# ----------------------------------------------------------------------------------------- analysis

def analyse(config: RepurposingConfig, *, ot=None, chembl=None, ct=None) -> dict:
    """Build the evidence inventory. Clients can be injected (tests); defaults are the real providers."""
    problems = config.validate()
    if problems:
        raise RepurposingError("; ".join(problems), 422)
    if ot is None:
        from app.clients.opentargets import OpenTargetsClient
        ot = OpenTargetsClient()
    if chembl is None and config.include_chembl:
        from app.clients.chembl import ChEMBLClient
        chembl = ChEMBLClient()
    if ct is None and config.include_registered_studies:
        from app.clients.clinicaltrials import ClinicalTrialsClient
        ct = ClinicalTrialsClient()

    ledger = Ledger()
    limitations: list[str] = []
    releases = {}
    rel = ot.release()
    ledger.record("Open Targets release", rel)
    releases["opentargets"] = rel.source_release if rel.ok else None

    target, target_src = _resolve(ot, ledger, config.target_query, "target")
    disease, disease_src = _resolve(ot, ledger, config.disease_query, "disease")
    ensembl_id, disease_id, disease_name = target["id"], disease["id"], disease["name"]
    symbol = target["name"]

    # target-disease association (context for the target, identical for every candidate)
    assoc = ot.target_diseases(ensembl_id, config.association_rows)
    assoc_src = ledger.record(f"associated diseases of {symbol}", assoc)
    association = {"source": assoc_src, "status": "unavailable", "overall_score": None, "datatype_scores": None,
                   # The same pair can carry slightly different scores depending on the query direction
                   # (observed 2026-11-17 on one release: Target.associatedDiseases 0.71405 vs
                   # Disease.associatedTargets 0.71869 for JAK1-RA), so the query used is always recorded.
                   "provider_query": "Open Targets Target.associatedDiseases",
                   "score_meaning": "Open Targets evidence-aggregation score for prioritisation; not a probability "
                                    "of drug success and not a measure of treatment efficacy."}
    if assoc.ok:
        row = next((r for r in assoc.data["rows"] if r["disease_id"] == disease_id), None)
        if row:
            association.update(status="recorded", overall_score=row["overall_score"],
                               datatype_scores=row["datatype_scores"])
        else:
            association["status"] = "not_in_returned_rows"
            limitations.append(f"{disease_name} was not among the {len(assoc.data['rows'])} associations returned for "
                               f"{symbol} (of {assoc.data['count']}); this is not evidence of no association.")
    else:
        limitations.append(f"Target-disease association unavailable: {assoc.error}")

    cand = ot.target_drug_candidates(ensembl_id)
    cand_src = ledger.record(f"drug and clinical candidates of {symbol}", cand)
    if not cand.ok:
        raise RepurposingError(f"Open Targets candidate list unavailable: {cand.error}", 502)
    rows, malformed = _normalise_rows((cand.data or {}).get("rows"))
    if malformed:
        limitations.append(f"{malformed} Open Targets candidate row(s) were malformed (missing drug id or not an "
                           "object) and are listed under unidentified ids or skipped; they were not repaired.")

    # ChEMBL context
    chembl_target, mechanisms, chembl_target_src, mech_src = None, None, None, None
    if config.include_chembl:
        r = chembl.release()
        ledger.record("ChEMBL release", r)
        releases["chembl"] = r.source_release if r.ok else None
        chembl_target, chembl_target_src, note = _resolve_chembl_target(chembl, ledger, symbol)
        if note:
            limitations.append(note)
        if chembl_target:
            m = chembl.mechanisms_for_target(chembl_target["chembl_id"])
            mech_src = ledger.record(f"ChEMBL mechanisms for {chembl_target['chembl_id']}", m)
            if m.ok:
                mechanisms = {}
                for row in m.data:
                    mechanisms.setdefault(row["molecule_chembl_id"], []).append(row)
            else:
                limitations.append(f"ChEMBL mechanisms unavailable: {m.error}")
    else:
        limitations.append("ChEMBL was not queried (include_chembl=false): no second-source corroboration, salt-form "
                           "grouping or activity counts.")

    # group: exact duplicate ids first, then salt/solvate forms under their ChEMBL parent
    by_id: dict[str, list[dict]] = {}
    for r in rows:
        by_id.setdefault(r["drug_chembl_id"] or f"unidentified:{r.get('candidate_id')}", []).append(r)
    molecules: dict[str, dict] = {}
    molecule_status: dict[str, str] = {}
    for mid in sorted(by_id):
        if config.include_chembl and not mid.startswith("unidentified:"):
            res = chembl.molecule(mid)
            sid = ledger.record(f"ChEMBL molecule {mid}", res)
            molecule_status[mid] = sid
            if res.ok:
                molecules[mid] = {**res.data, "_source": sid}
    groups: dict[str, list[str]] = {}
    for mid in sorted(by_id):
        parent = (molecules.get(mid) or {}).get("parent_chembl_id") or mid
        groups.setdefault(parent, []).append(mid)
    if config.include_chembl and len(molecules) < len([m for m in by_id if not m.startswith("unidentified:")]):
        limitations.append(f"ChEMBL molecule records were unavailable for "
                           f"{len([m for m in by_id if not m.startswith('unidentified:')]) - len(molecules)} of "
                           f"{len(by_id)} compounds; salt-form grouping and ChEMBL stage cross-checks are incomplete for those.")

    candidates = []
    for parent in sorted(groups):
        forms = groups[parent]
        src_rows = [r for f in forms for r in by_id[f]]
        candidates.append(_candidate(parent, forms, src_rows, molecules, molecule_status, mechanisms, mech_src,
                                     chembl_target, disease_id, disease_name, cand_src, config, chembl, ct, ledger))
    candidates.sort(key=lambda c: (c["name"] or c["candidate_id"]).lower())
    truncated = len(candidates) > config.max_candidates
    candidates = candidates[:config.max_candidates]
    if truncated:
        limitations.append(f"Only the first {config.max_candidates} candidates (alphabetical) are included.")

    limitations += [
        "Open Targets indication entries without an ontology id cannot be matched to the disease automatically; "
        "they are counted and their source text is kept.",
        "Disease matching uses the exact resolved ontology id; related or child terms are not traversed.",
        "Activity record counts are counts of deposited ChEMBL records, not potencies, and are not compared across "
        "endpoints or assays.",
        "Registered-study search totals describe registrations matching a text query; they are not outcomes and can "
        "include studies of other interventions that mention the compound. Only retrieved records are classified.",
    ]
    analysis = {
        "analysis_type": "repurposing_evidence_inventory",
        "disclaimer": DISCLAIMER,
        "parameters": config.as_dict(),
        "target": {"ensembl_id": ensembl_id, "gene_symbol": symbol, "source": target_src,
                   "chembl_target_id": chembl_target["chembl_id"] if chembl_target else None,
                   "chembl_target_source": chembl_target_src},
        "disease": {"ontology_id": disease_id, "name": disease_name, "source": disease_src},
        "target_disease_association": association,
        "candidate_count": len(candidates),
        "candidate_ordering": "Alphabetical by candidate name. The order carries no meaning; candidates are not ranked.",
        "candidates": candidates,
        "classification_rules": {c.value: m for c, m in CLASS_MEANING.items()},
        "limitations": limitations,
        "source_releases": releases,
        "software_versions": {"moleculab": APP_VERSION, "repurposing": REPURPOSING_VERSION,
                              "classification_rules": CLASSIFICATION_RULES_VERSION},
        "sources": ledger.entries,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    analysis["content_fingerprint"] = fingerprint(analysis)
    return analysis


def _normalise_rows(rows) -> tuple[list[dict], int]:
    """Give every candidate row the keys the analysis reads, without inventing values: absent lists become
    empty lists and absent scalars None. Rows that are not objects are dropped and counted."""
    out, malformed = [], 0
    if not isinstance(rows, list):
        return [], 1
    for r in rows:
        if not isinstance(r, dict):
            malformed += 1
            continue
        if not r.get("drug_chembl_id"):
            malformed += 1
        row = dict(r)
        for key in ("mechanisms_of_action", "drug_warnings", "indications"):
            row[key] = [x for x in (row.get(key) or []) if isinstance(x, dict)]
        for key in ("candidate_id", "drug_chembl_id", "drug_name", "drug_type", "maximum_clinical_stage",
                    "row_max_clinical_stage"):
            row.setdefault(key, None)
        row["indications"] = [{"disease_id": i.get("disease_id"), "disease_name": i.get("disease_name"),
                               "disease_from_source": i.get("disease_from_source")} for i in row["indications"]]
        out.append(row)
    return out, malformed


def _study_match(study: dict, disease_name: str, names: set[str]) -> str:
    """How a registered study relates to the query, judged only from fields the registry returned."""
    cond = {fold(c) for c in study.get("conditions") or [] if isinstance(c, str)}
    interventions = [fold(x) for i in study.get("interventions") or [] if isinstance(i, dict)
                     for x in [i.get("name") or "", *(i.get("other_names") or [])] if isinstance(x, str)]
    cond_listed = fold(disease_name) in cond
    intr_listed = any(n and n in x for n in names for x in interventions)
    if cond_listed and intr_listed:
        return "condition_and_intervention_listed"
    return "discovery_text_match_only"


def _indications(src_rows: list[dict], disease_id: str, disease_name: str) -> dict:
    seen, this, other, unmapped = set(), [], [], []
    name_key = fold(disease_name)
    for r in src_rows:
        for i in r["indications"]:
            key = (i.get("disease_id"), i.get("disease_from_source"))
            if key in seen or key == (None, None):
                continue
            seen.add(key)
            if i.get("disease_id") == disease_id:
                this.append(i)
            elif i.get("disease_id"):
                other.append(i)
            else:
                unmapped.append(i)
    possible = [i for i in unmapped if name_key and name_key in fold(i.get("disease_from_source") or "")]
    return {"this_disease": this, "other_diseases": sorted({(i["disease_id"], i["disease_name"]) for i in other}),
            "unmapped_count": len(unmapped), "unmapped_possible_mentions": [i["disease_from_source"] for i in possible]}


def _candidate(parent, forms, src_rows, molecules, molecule_status, mechanisms, mech_src, chembl_target,
               disease_id, disease_name, cand_src, config, chembl, ct, ledger) -> dict:
    form_mols = [molecules[f] for f in forms if f in molecules]
    parent_mol = molecules.get(parent) or (form_mols[0] if form_mols else None)
    name = (parent_mol or {}).get("pref_name") or src_rows[0].get("drug_name")
    contradictions, missing = [], []

    # target relationship: Open Targets mechanism rows, corroborated (or not) by ChEMBL target mechanisms
    ot_actions = sorted({m["action_type"] for r in src_rows for m in r["mechanisms_of_action"] if m.get("action_type")})
    chembl_rows = None
    if mechanisms is not None:
        chembl_rows = [m for f in {parent, *forms} for m in mechanisms.get(f, [])]
        chembl_actions = sorted({m["action_type"] for m in chembl_rows if m.get("action_type")})
        if chembl_rows and ot_actions and set(chembl_actions) != set(ot_actions):
            contradictions.append({"type": "action_type_disagreement", "opentargets": ot_actions,
                                   "chembl": chembl_actions, "sources": [cand_src, mech_src]})
        if not chembl_rows:
            missing.append("no ChEMBL mechanism record links this compound to the target")
    target_rel = {
        "opentargets": {"action_types": ot_actions, "source": cand_src,
                        "mechanisms": sorted({m["mechanism_of_action"] for r in src_rows for m in r["mechanisms_of_action"]
                                              if m.get("mechanism_of_action")})},
        "chembl": ({"status": "unavailable"} if mechanisms is None else
                   {"status": "recorded" if chembl_rows else "not_recorded", "source": mech_src,
                    "rows": [{k: m.get(k) for k in ("mechanism_of_action", "action_type", "direct_interaction",
                                                    "molecule_chembl_id")} for m in chembl_rows]}),
        "meaning": "A documented mechanism links the compound to the target. It does not establish effectiveness "
                   "for any disease.",
    }

    # development stages, from both providers, never merged
    ot_stages = sorted({r.get("maximum_clinical_stage") for r in src_rows if r.get("maximum_clinical_stage")},
                       key=lambda s: OT_STAGE_ORDER.index(s) if s in OT_STAGE_ORDER else -1)
    row_stages = sorted({r.get("row_max_clinical_stage") for r in src_rows if r.get("row_max_clinical_stage")})
    chembl_phase = None
    phases = [_chembl_phase(m.get("max_phase")) for m in form_mols]
    phases = [p for p in phases if p is not None]
    if phases:
        chembl_phase = max(phases)
    band_ot = _stage_band(ot_stages)
    band_chembl = CHEMBL_PHASE_BAND.get(chembl_phase) if chembl_phase is not None else None
    if band_ot and band_chembl and band_chembl != "unknown" and band_ot != band_chembl:
        contradictions.append({"type": "development_stage_disagreement", "opentargets": ot_stages,
                               "chembl_max_phase": chembl_phase,
                               "sources": [cand_src] + [m["_source"] for m in form_mols]})
    if chembl_phase is None:
        missing.append("ChEMBL max_phase unavailable" if config.include_chembl else "ChEMBL not queried")

    # experimental activity records (counts only)
    activity = {"status": "not_requested"}
    if config.include_chembl and config.include_activity_counts:
        if not chembl_target:
            activity = {"status": "unavailable", "reason": "no unambiguous ChEMBL target"}
        else:
            counts, srcs, failed = {}, [], []
            for f in sorted({parent, *forms}):
                res = chembl.activity_count_for_molecule(chembl_target["chembl_id"], f)
                sid = ledger.record(f"ChEMBL activity count {f} x {chembl_target['chembl_id']}", res)
                srcs.append(sid)
                if res.ok:
                    counts[f] = res.data
                else:
                    failed.append(f)
            activity = {"status": "unavailable" if failed and not counts else "partial" if failed else "recorded",
                        "record_counts": counts, "failed": failed, "sources": srcs,
                        "meaning": "Number of ChEMBL experimental activity records against the target (any endpoint). "
                                   "A count, not a potency."}
            if failed:
                missing.append(f"activity counts unavailable for {failed}")

    indications = _indications(src_rows, disease_id, disease_name)
    if not indications["this_disease"]:
        missing.append(f"no Open Targets indication record mapped to {disease_id}")

    studies = {"status": "not_requested"}
    if config.include_registered_studies:
        n = config.studies_per_candidate
        res = ct.search(condition=disease_name, intervention=name, page_size=n, max_records=n)
        sid = ledger.record(f"ClinicalTrials.gov registrations for '{name}' x '{disease_name}'", res)
        data = res.data if isinstance(res.data, dict) else {}
        raw = data.get("studies") if isinstance(data.get("studies"), list) else []
        if res.ok and isinstance(res.data, dict):
            names = {fold(x) for x in [name, *(f for f in forms)] if x} | \
                    {fold(r.get("drug_name") or "") for r in src_rows if r.get("drug_name")}
            names = {x for x in names if len(x) >= 4}
            records = []
            for st in raw:
                if not isinstance(st, dict):
                    continue
                records.append({
                    "nct_id": st.get("nct_id"), "title": st.get("title"), "overall_status": st.get("overall_status"),
                    "phases": st.get("phases") or [], "conditions": st.get("conditions") or [],
                    "interventions": [{"name": i.get("name"), "other_names": i.get("other_names") or []}
                                      for i in st.get("interventions") or [] if isinstance(i, dict)],
                    "has_posted_results": st.get("has_posted_results"), "source_url": st.get("source_url"),
                    "match": _study_match(st, disease_name, names),
                })
            missing_ids = sum(1 for r in records if not r["nct_id"])
            studies = {
                "status": "recorded", "source": sid, "query": {"condition": disease_name, "intervention": name},
                "discovery_search_total": data.get("total_count"),
                "records_retrieved": len(records), "records_limit": n,
                "condition_and_intervention_listed_in_retrieved": sum(r["match"] == "condition_and_intervention_listed"
                                                                      for r in records),
                "records": records,
                "meaning": ("discovery_search_total counts registry text-search hits and can include unrelated or "
                            "loosely related studies. A record is 'condition_and_intervention_listed' only when the "
                            "registry lists the disease as a condition and the compound as an intervention; that "
                            "classification covers retrieved records only. Registration is not an outcome: status "
                            "and the presence of a results section do not establish efficacy."),
            }
            if missing_ids:
                studies["records_without_nct_id"] = missing_ids
        else:
            studies = {"status": "unavailable", "source": sid, "error": res.error or "unexpected response shape"}

    has_activity = any(v > 0 for v in (activity.get("record_counts") or {}).values()) if activity.get("record_counts") else None
    regulatory = [r for r in config.regulatory_records if r["chembl_id"] in {parent, *forms}]
    classification = classify(ot_stages, chembl_phase, has_activity, regulatory)

    warnings = []
    seen_w = set()
    for r in src_rows:
        for w in r["drug_warnings"]:
            key = json.dumps(w, sort_keys=True)
            if key not in seen_w:
                seen_w.add(key)
                warnings.append(w)

    present = {
        "target_relationship_opentargets": bool(ot_actions),
        "target_relationship_chembl": bool(chembl_rows),
        "development_stage_opentargets": bool(ot_stages),
        "development_stage_chembl": chembl_phase is not None,
        "indication_for_this_disease": bool(indications["this_disease"]),
        "experimental_activity_records": activity.get("status") in ("recorded", "partial"),
        "registered_studies": studies.get("status") == "recorded",
    }
    return {
        "candidate_id": parent,
        "name": name,
        "forms": [{"chembl_id": f, "names": sorted({r["drug_name"] for r in src_rows if r["drug_chembl_id"] == f}),
                   "chembl_molecule_source": molecule_status.get(f),
                   "chembl_record": "retrieved" if f in molecules else ("unavailable" if f in molecule_status else "not_requested")}
                  for f in forms],
        "drug_types": sorted({r.get("drug_type") or "unknown" for r in src_rows}),
        "classification": classification,
        "dimensions": {
            "target_relationship": target_rel,
            "experimental_activity": activity,
            "disease_association": {
                "indication_records_for_this_disease": [{"disease_id": i["disease_id"], "disease_name": i["disease_name"]}
                                                        for i in indications["this_disease"]],
                "unmapped_indication_text_mentioning_disease": indications["unmapped_possible_mentions"],
                "source": cand_src,
                "meaning": "An indication record means a provider records development of the compound for that "
                           "disease. It is not proof of efficacy.",
            },
            "clinical_development_stage": {
                "opentargets_maximum_clinical_stage": ot_stages,
                "opentargets_stage_for_this_target_row": row_stages,
                "chembl_max_phase": chembl_phase,
                "sources": [cand_src] + [m["_source"] for m in form_mols],
                "meaning": "Highest development stage a provider records anywhere. Not regulatory approval.",
            },
            "other_indications": {"mapped": [{"disease_id": d, "disease_name": n} for d, n in indications["other_diseases"]],
                                  "unmapped_count": indications["unmapped_count"], "source": cand_src,
                                  "meaning": "Development recorded for other diseases. Evidence for those diseases is "
                                             "not evidence for this one."},
            "registered_studies": studies,
            "recorded_warnings": {"warnings": warnings, "source": cand_src,
                                  "meaning": "Curated warnings recorded by Open Targets. An empty list means none is "
                                             "recorded, not that the compound is safe."},
            "evidence_completeness": {"present": present,
                                      "present_count": sum(present.values()), "of": len(present)},
        },
        "contradictions": contradictions,
        "missing_information": missing,
    }


# ------------------------------------------------------------------------------------ reproducibility

_VOLATILE = re.compile(r"^(retrieved_at|generated_at|elapsed_ms|from_cache|content_fingerprint)$")


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if not _VOLATILE.match(k)}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def fingerprint(analysis: dict) -> str:
    """SHA-256 over the analysis content excluding wall-clock times and cache flags: same parameters,
    same provider releases and same provider data give the same fingerprint."""
    body = json.dumps(_strip({k: v for k, v in analysis.items() if k != "sources"}) |
                      {"sources": [{k: v for k, v in s.items() if k in ("purpose", "ok", "raw_payload_hash")}
                                   for s in analysis.get("sources", [])]},
                      sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------------------- summary

def summary_markdown(analysis: dict) -> str:
    """Deterministic Markdown summary (no timestamps other than the recorded generation time)."""
    from app.workspace import md
    t, d, a = analysis["target"], analysis["disease"], analysis["target_disease_association"]
    L = [f"# Repurposing evidence inventory: {md(t['gene_symbol'])} · {md(d['name'])}", "",
         f"> {md(analysis['disclaimer'])}", "",
         f"- Target: {md(t['gene_symbol'])} ({md(t['ensembl_id'])}; ChEMBL {md(t['chembl_target_id'] or 'not resolved')})",
         f"- Disease: {md(d['name'])} ({md(d['ontology_id'])})",
         f"- Target-disease association: {md(a['status'])}"
         + (f", Open Targets overall score {a['overall_score']:.4f}" if a.get("overall_score") is not None else ""),
         f"  ({md(a['score_meaning'])})",
         f"- Sources: {md(', '.join(f'{k} {v}' for k, v in analysis['source_releases'].items()))}",
         f"- Content fingerprint: `{analysis['content_fingerprint']}`", "",
         f"## Candidates ({analysis['candidate_count']}) — {md(analysis['candidate_ordering'])}", ""]
    for c in analysis["candidates"]:
        dims = c["dimensions"]
        L += [f"### {md(c['name'])} ({md(c['candidate_id'])})", "",
              f"- Classification: **{md(c['classification']['category'])}** — {md(c['classification']['meaning'])}",
              f"- Regulatory approval: {md(c['classification']['regulatory_approval'])}",
              f"- Forms: {md(', '.join(f['chembl_id'] for f in c['forms']))}",
              f"- Target relationship: Open Targets action types {md(', '.join(dims['target_relationship']['opentargets']['action_types']) or 'none')}; "
              f"ChEMBL {md(dims['target_relationship']['chembl'].get('status'))}",
              f"- Development stage labels: Open Targets {md(', '.join(dims['clinical_development_stage']['opentargets_maximum_clinical_stage']) or 'none')}; "
              f"ChEMBL max_phase {md(dims['clinical_development_stage']['chembl_max_phase'])}",
              f"- Indication records for {md(d['name'])}: {len(dims['disease_association']['indication_records_for_this_disease'])}; "
              f"other mapped indications: {len(dims['other_indications']['mapped'])}; unmapped: {dims['other_indications']['unmapped_count']}",
              f"- Experimental activity records: {md(dims['experimental_activity'].get('status'))}"
              + (f" {md(dims['experimental_activity'].get('record_counts'))}" if dims['experimental_activity'].get('record_counts') else ""),
              f"- Registered studies: {md(dims['registered_studies'].get('status'))}"
              + (f"; discovery text-search hits {dims['registered_studies'].get('discovery_search_total')}, "
                 f"{dims['registered_studies'].get('records_retrieved')} retrieved, of which "
                 f"{dims['registered_studies'].get('condition_and_intervention_listed_in_retrieved')} list both the "
                 f"disease as a condition and the compound as an intervention"
                 if dims['registered_studies'].get('status') == 'recorded' else ""),
              f"- Recorded warnings: {len(dims['recorded_warnings']['warnings'])} (none recorded ≠ safe)"]
        L += [f"- Contradiction: {md(x['type'])} — {md({k: v for k, v in x.items() if k not in ('type', 'sources')})}"
              for x in c["contradictions"]]
        L += [f"- Missing: {md(m)}" for m in c["missing_information"]]
        L.append("")
    L += ["## Limitations", ""] + [f"- {md(x)}" for x in analysis["limitations"]]
    L += ["", "## Source calls", ""] + [
        f"- {s['id']}: {md(s['purpose'])} — {'ok' if s['ok'] else 'FAILED: ' + md(s['error'])}"
        f" ({md(s.get('source'))}, retrieved {md(s.get('retrieved_at'))})" for s in analysis["sources"]]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------- persistence

def persist_context(session, analysis: dict) -> dict:
    """Store the resolved disease, target and their association (provenance included) in the existing
    1.0 tables. Candidates are not persisted: there is no table for them yet. Idempotent."""
    from sqlalchemy import select

    from app.models import BiologicalTarget, Disease, TargetDiseaseEvidence
    src = {s["id"]: s for s in analysis["sources"]}

    def prov(sid):
        s = src.get(sid) or {}
        return {"provider": s.get("source"), "source_url": s.get("request_url"), "retrieved_at": s.get("retrieved_at"),
                "raw_payload_hash": s.get("raw_payload_hash"), "transformation_version": s.get("transformation_version"),
                "source_release": analysis["source_releases"].get("opentargets")}

    d, t, a = analysis["disease"], analysis["target"], analysis["target_disease_association"]
    disease = session.scalar(select(Disease).where(Disease.ontology_id == d["ontology_id"]))
    created = {"disease": False, "target": False, "association": False}
    if disease is None:
        disease = Disease(ontology_id=d["ontology_id"], name=d["name"], provider_record_id=d["ontology_id"],
                          **prov(d["source"]))
        session.add(disease)
        created["disease"] = True
    target = session.scalar(select(BiologicalTarget).where(BiologicalTarget.ensembl_id == t["ensembl_id"]))
    if target is None:
        target = BiologicalTarget(ensembl_id=t["ensembl_id"], gene_symbol=t["gene_symbol"],
                                  chembl_target_id=t["chembl_target_id"], provider_record_id=t["ensembl_id"],
                                  **prov(t["source"]))
        session.add(target)
        created["target"] = True
    session.flush()
    if a["status"] == "recorded":
        link = session.scalar(select(TargetDiseaseEvidence).where(
            TargetDiseaseEvidence.disease_id == disease.id, TargetDiseaseEvidence.target_id == target.id,
            TargetDiseaseEvidence.provider == "opentargets"))
        if link is None:
            session.add(TargetDiseaseEvidence(disease_id=disease.id, target_id=target.id,
                                              overall_score=a["overall_score"], datatype_scores=a["datatype_scores"],
                                              provider_record_id=f"{t['ensembl_id']}|{d['ontology_id']}",
                                              **prov(a["source"])))
            created["association"] = True
    session.commit()
    return {"disease_id": disease.id, "target_id": target.id, "created": created}
