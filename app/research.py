"""Research intelligence: gaps, contradictions, hypotheses and priorities.

Deterministic; no AI provider is needed. Rules that every output follows:

* Statements about missing evidence describe the MOLECULAB index ("No X evidence is currently indexed
  in MOLECULAB"), never the world's literature. `assert_dataset_scoped` enforces this on every output.
* Contradictions are described, not explained: documented context differences are listed, and fields
  MOLECULAB does not record are named as missing.
* Every documented observation shown with a hypothesis has passed the v0.3 claim verifier.
* Hypotheses are hedged, labelled unvalidated, and may not carry efficacy/clinical/safety/toxicity claims.
* No single priority score: separate, explained dimensions; uncomputable ones are "unavailable".
"""
from __future__ import annotations

import re
from collections import defaultdict
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.claims import ClaimCategory, Verdict, classify_claim
from app.models import Compound, EvidenceRecord, EvidenceType, Plant, Publication
from app.reasoning import QuestionType, classify_question, detect_entities
from app.services import effective_review_status
from app.textnorm import normalize

# ---------------------------------------------------------------- language guard

GLOBAL_CLAIM = re.compile(
    r"\b(never (been )?(tested|studied|investigated|reported|examined|evaluated)|"
    r"ha(s|ve) not been (tested|studied|investigated|reported|examined|evaluated)|not been studied|"
    r"no (studies|research|data|evidence|publications?) (exists?|has been|have been|is available anywhere)|"
    r"unstudied|unknown to science|nobody has|no one has|for the first time|novel discovery|"
    r"we discovered|has been discovered|proves?|proven|confirmed mechanism|will become|likely to (succeed|become))\b",
    re.I)


class ScopeViolation(ValueError):
    pass


def assert_dataset_scoped(obj, path: str = "output") -> None:
    """Raise if any string in obj makes a claim about the global literature or a discovery."""
    if isinstance(obj, str):
        m = GLOBAL_CLAIM.search(obj)
        if m:
            raise ScopeViolation(f"{path}: '{m.group(0)}' makes a claim beyond the indexed dataset: {obj[:120]}")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            assert_dataset_scoped(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_dataset_scoped(v, f"{path}[{i}]")


DATASET_NOTE = ("Describes the MOLECULAB index ({n} evidence records curated from PubMed abstracts), "
                "not the complete scientific literature.")


class GapCategory(StrEnum):
    PLANT_COVERAGE = "plant_research_coverage"
    COMPOUND_IDENTIFICATION = "compound_identification"
    UNRESOLVED_STRUCTURE = "unresolved_structure"
    MISSING_PLANT_PART = "missing_plant_part"
    MISSING_GEOGRAPHY = "missing_geographic_origin"
    MISSING_METHOD = "missing_method"
    MISSING_BIOACTIVITY = "missing_biological_activity_evidence"
    MISSING_TOXICITY = "missing_toxicity_evidence"
    MISSING_PHARMACOKINETICS = "missing_pharmacokinetic_evidence"
    MISSING_PUBLICATION_REFERENCE = "missing_publication_reference"
    CONFLICTING_FINDINGS = "conflicting_findings"
    POORLY_SUPPORTED = "poorly_supported_relationship"


class GapKind(StrEnum):
    DATASET_COVERAGE = "dataset_coverage"  # what the index lacks; says nothing about the literature
    EVIDENCE_QUALITY = "evidence_quality"  # weaknesses of indexed records
    CONFLICT = "conflict"


class Certainty(StrEnum):
    DATASET_FACT = "dataset_fact"  # a certain statement about the contents of the index
    RULE_ASSESSMENT = "rule_based_assessment"  # outcome of deterministic rules; expert confirmation advised
    UNRESOLVED = "unresolved"  # conflicting evidence whose cause cannot be determined from the index


# ---------------------------------------------------------------- loading

def _load(s: Session):
    plants = list(s.scalars(select(Plant).options(selectinload(Plant.aliases)).order_by(Plant.accepted_name)))
    records = list(s.scalars(select(EvidenceRecord).options(
        selectinload(EvidenceRecord.plant), selectinload(EvidenceRecord.compound),
        selectinload(EvidenceRecord.publication), selectinload(EvidenceRecord.reviews)).order_by(EvidenceRecord.id)))
    compounds = list(s.scalars(select(Compound).order_by(Compound.name)))
    return plants, compounds, records


def _pub(p: Publication) -> dict:
    return {"id": p.id, "pmid": p.pmid, "doi": p.doi, "year": p.year, "title": p.title}


def _has_quantity(r: EvidenceRecord) -> bool:
    from app.assistant import _has_quantity as hq
    return hq(r)


IDENT_TYPES = {EvidenceType.DIRECT_EXPERIMENTAL, EvidenceType.TENTATIVE, EvidenceType.SECONDARY_SOURCE}
EVIDENCE_AREAS = {  # area -> (test on a record, human label)
    "identification": (lambda r: r.compound is not None and EvidenceType(r.evidence_type) in IDENT_TYPES,
                       "chemical identification"),
    "quantification": (lambda r: _has_quantity(r), "quantitative content"),
    "biological_activity": (lambda r: EvidenceType(r.evidence_type) == EvidenceType.EXPERIMENTAL_BIOACTIVITY,
                            "experimental biological activity"),
    "clinical": (lambda r: EvidenceType(r.evidence_type) == EvidenceType.CLINICAL_STUDY, "clinical studies"),
    "toxicity": (lambda r: False, "toxicity"),  # no toxicity evidence category exists in MOLECULAB yet
    "pharmacokinetics": (lambda r: False, "pharmacokinetics"),  # no PK category exists yet
    "traditional_use": (lambda r: EvidenceType(r.evidence_type) == EvidenceType.TRADITIONAL_USE, "traditional use"),
    "computational": (lambda r: EvidenceType(r.evidence_type) == EvidenceType.COMPUTATIONAL, "computational predictions"),
}
NO_CATEGORY = {"toxicity", "pharmacokinetics"}


def _verify(s_records, plants, compounds, claim: str, ids: list[int]) -> dict:
    from app.verification import verify_claim
    by_id = {r.id: r for r in s_records}
    return verify_claim(claim, ids, records=by_id, existing_ids=set(by_id), allowed_ids=set(ids),
                        plants=plants, compounds=compounds)


# ---------------------------------------------------------------- contradictions

_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _value(r: EvidenceRecord) -> float | None:
    m = _PCT.search(r.observation)
    return float(m.group(1)) if m else None


def _rank_in_source(r: EvidenceRecord) -> str | None:
    from app.reasoning import compound_keys
    from app.verification import _own_percent
    own = _own_percent(r.original_excerpt, compound_keys(r.compound)) if r.compound else None
    pcts = [float(x) for x in _PCT.findall(r.original_excerpt)]
    if own is None or len(pcts) < 2:
        return None
    return "highest value in its source" if own >= max(pcts) else f"not the highest value in its source (max {max(pcts)} %)"


CONTEXT_FIELDS = [("plant_part", "plant part"), ("geographic_origin", "geographic origin"),
                  ("identification_method", "analytical method")]
NOT_INDEXED_CONTEXT = ["chemotype classification", "harvest stage and season", "extraction/distillation conditions",
                       "voucher specimen identity"]


def _compare_context(recs: list[EvidenceRecord]) -> tuple[list[dict], list[str], list[str]]:
    comparison, differences, missing = [], [], []
    for attr, label in CONTEXT_FIELDS:
        values = {r.id: getattr(r, attr) for r in recs}
        known = {v for v in values.values() if v}
        unknown = [i for i, v in values.items() if not v]
        comparable = None if unknown else len(known) == 1
        comparison.append({"field": label, "values": {str(i): v for i, v in values.items()}, "same": comparable})
        if len(known) > 1:
            differences.append(f"{label} differs: " + "; ".join(f"#{i}: {v}" for i, v in values.items() if v))
        if unknown:
            missing.append(f"{label} not stated for " + ", ".join(f"#{i}" for i in unknown))
    years = {r.id: r.publication.year for r in recs}
    comparison.append({"field": "publication year", "values": {str(i): y for i, y in years.items()}, "same": len(set(years.values())) == 1})
    return comparison, differences, missing


def contradictions(s: Session, plant_id: int | None = None, compound_id: int | None = None) -> list[dict]:
    plants, compounds, records = _load(s)
    groups: dict[tuple[int, int], list[EvidenceRecord]] = defaultdict(list)
    for r in records:
        if r.compound and (plant_id is None or r.plant_id == plant_id) and (compound_id is None or r.compound_id == compound_id):
            groups[(r.plant_id, r.compound_id)].append(r)
    out = []
    for (pid, cid), recs in sorted(groups.items()):
        if len({r.publication_id for r in recs}) < 2:
            continue
        vals = {r.id: _value(r) for r in recs}
        numeric = {i: v for i, v in vals.items() if v is not None}
        ranks = {r.id: _rank_in_source(r) for r in recs}
        spread = len(numeric) >= 2 and max(numeric.values()) > 2 * min(numeric.values())
        rank_conflict = len({("highest" in x) for x in ranks.values() if x}) > 1
        if not (spread or rank_conflict):
            continue
        comparison, differences, missing = _compare_context(recs)
        if missing:
            assessment = "insufficient_context"
            why = ("The reports cannot be judged as directly comparable because required context is not stated: "
                   + "; ".join(missing) + ".")
        elif differences:
            assessment = "not_directly_comparable"
            why = "The reports differ in documented context (" + "; ".join(differences) + "), so they are not directly comparable."
        else:
            assessment = "directly_comparable_conflict"
            why = "The reports share the documented context fields yet report different values."
        if differences and missing:
            why += " Documented differences: " + "; ".join(differences) + "."
        rec0 = recs[0]
        item = {
            "id": f"conflict:p{pid}:c{cid}", "plant": {"id": pid, "accepted_name": rec0.plant.accepted_name},
            "compound": {"id": cid, "name": rec0.compound.name}, "evidence_ids": [r.id for r in recs],
            "conflict_type": ", ".join(t for t, ok in (("quantity", spread), ("rank", rank_conflict)) if ok),
            "findings": [{"evidence_id": r.id, "observation": r.observation, "value_percent": vals[r.id],
                          "rank_in_source": ranks[r.id], "excerpt": r.original_excerpt, "plant_part": r.plant_part,
                          "geographic_origin": r.geographic_origin, "method": r.identification_method,
                          "evidence_type": r.evidence_type, "publication": _pub(r.publication)} for r in recs],
            "context_comparison": comparison, "documented_differences": differences,
            "missing_context": missing + [f"{x} (not recorded in MOLECULAB)" for x in NOT_INDEXED_CONTEXT],
            "assessment": assessment, "assessment_explanation": why,
            "interpretation_note": "MOLECULAB does not decide which report is correct and does not infer the cause "
                                   "of the difference from the indexed data.",
        }
        assert_dataset_scoped(item)
        out.append(item)
    return out


# ---------------------------------------------------------------- gaps

SUGGEST = {
    GapCategory.PLANT_COVERAGE: "Search PubMed and regional (Russian/Uzbek) literature for further studies of {plant} and curate relevant records into MOLECULAB.",
    GapCategory.MISSING_TOXICITY: "Search toxicology literature (e.g. PubMed, TOXNET successors) for {subject}; MOLECULAB also needs a toxicity evidence category before such records can be indexed.",
    GapCategory.MISSING_PHARMACOKINETICS: "Search pharmacokinetic literature for {subject}; MOLECULAB also needs a pharmacokinetic evidence category before such records can be indexed.",
    GapCategory.MISSING_BIOACTIVITY: "Search the literature for experimental (in vitro / in vivo) activity studies of {subject} and curate them with their experimental model.",
    GapCategory.COMPOUND_IDENTIFICATION: "Look for publications that isolate {compound} from {plant} and confirm its structure (NMR/MS with a reference standard).",
    GapCategory.UNRESOLVED_STRUCTURE: "Resolve the structure of {compound} from the source publication's full text and register it in PubChem-compatible form.",
    GapCategory.MISSING_PLANT_PART: "Check the full texts of the listed records for the plant part analysed.",
    GapCategory.MISSING_GEOGRAPHY: "Check the full texts of the listed records for collection locality.",
    GapCategory.MISSING_METHOD: "Check the full texts of the listed records for the identification method.",
    GapCategory.MISSING_PUBLICATION_REFERENCE: "Find and attach a PMID or DOI for the listed records.",
    GapCategory.CONFLICTING_FINDINGS: "Open the contradiction explorer; compare full-text methods and sample origins before treating either value as typical.",
    GapCategory.POORLY_SUPPORTED: "Re-read the source and correct the record's excerpt or classification.",
}


def _gap(category, kind, certainty, *, plant=None, compound=None, explanation, evidence_ids=(), missing,
         n_records, related_compounds=(), suggest_fmt=None, abstained=False, extra_id=""):
    pid = f"p{plant.id}" if plant else ""
    cid = f"c{compound.id}" if compound else ""
    gid = ":".join(x for x in (category.value, pid, cid, extra_id) if x)
    subject = compound.name if compound else plant.accepted_name if plant else "the dataset"
    g = {
        "id": gid, "category": category, "kind": kind, "certainty": certainty,
        "plant": {"id": plant.id, "accepted_name": plant.accepted_name} if plant else None,
        "compound": {"id": compound.id, "name": compound.name} if compound else None,
        "related_compounds": [{"id": c.id, "name": c.name} for c in related_compounds],
        "explanation": explanation, "evidence_ids": list(evidence_ids), "missing_information": missing,
        "dataset_limitations": DATASET_NOTE.format(n=n_records),
        "suggested_next_investigation": (suggest_fmt or SUGGEST[category]).format(
            plant=plant.accepted_name if plant else "", compound=compound.name if compound else "", subject=subject),
        "abstained": abstained,
    }
    assert_dataset_scoped(g)
    return g


def detect_gaps(s: Session) -> list[dict]:
    plants, compounds, records = _load(s)
    n = len(records)
    if n == 0:
        return []
    gaps: list[dict] = []
    by_plant: dict[int, list[EvidenceRecord]] = defaultdict(list)
    for r in records:
        by_plant[r.plant_id].append(r)

    for p in plants:
        recs = by_plant.get(p.id, [])
        if not recs:
            gaps.append(_gap(GapCategory.PLANT_COVERAGE, GapKind.DATASET_COVERAGE, Certainty.DATASET_FACT, plant=p,
                             explanation=f"No evidence records are currently indexed in MOLECULAB for {p.accepted_name}; "
                                         "MOLECULAB cannot assess its research coverage.",
                             missing=["any indexed evidence"], n_records=n, abstained=True))
            continue
        pubs = {r.publication_id for r in recs}
        comps = sorted({r.compound for r in recs if r.compound}, key=lambda c: c.name)
        if len(pubs) < 3:
            gaps.append(_gap(GapCategory.PLANT_COVERAGE, GapKind.DATASET_COVERAGE, Certainty.DATASET_FACT, plant=p,
                             explanation=f"Only {len(pubs)} publication(s) about {p.accepted_name} are currently indexed "
                                         f"in MOLECULAB ({len(recs)} evidence records).",
                             evidence_ids=[r.id for r in recs], missing=["further indexed publications"], n_records=n))
        for area, cat in (("biological_activity", GapCategory.MISSING_BIOACTIVITY),
                          ("toxicity", GapCategory.MISSING_TOXICITY),
                          ("pharmacokinetics", GapCategory.MISSING_PHARMACOKINETICS)):
            test, label = EVIDENCE_AREAS[area]
            if not any(test(r) for r in recs) and comps:
                note = (" MOLECULAB currently has no evidence category for this kind of data." if area in NO_CATEGORY else "")
                gaps.append(_gap(cat, GapKind.DATASET_COVERAGE, Certainty.DATASET_FACT, plant=p,
                                 explanation=f"No {label} evidence is currently indexed in MOLECULAB for {p.accepted_name} "
                                             f"or its {len(comps)} indexed compound(s).{note}",
                                 missing=[f"{label} evidence"], related_compounds=comps, n_records=n))
        for attr, cat, label in (("plant_part", GapCategory.MISSING_PLANT_PART, "the plant part"),
                                 ("geographic_origin", GapCategory.MISSING_GEOGRAPHY, "the geographic origin"),
                                 ("identification_method", GapCategory.MISSING_METHOD, "the identification method")):
            lacking = [r for r in recs if not getattr(r, attr) or str(getattr(r, attr)).lower().startswith("not described")]
            if lacking:
                gaps.append(_gap(cat, GapKind.EVIDENCE_QUALITY, Certainty.DATASET_FACT, plant=p,
                                 explanation=f"{len(lacking)} of {len(recs)} indexed records for {p.accepted_name} do not "
                                             f"state {label} in the curated abstract data.",
                                 evidence_ids=[r.id for r in lacking], missing=[label], n_records=n))
        noref = [r for r in recs if not r.publication.pmid and not r.publication.doi]
        if noref:
            gaps.append(_gap(GapCategory.MISSING_PUBLICATION_REFERENCE, GapKind.EVIDENCE_QUALITY, Certainty.DATASET_FACT,
                             plant=p, explanation=f"{len(noref)} indexed record(s) for {p.accepted_name} cite a publication "
                                                  "without a PMID or DOI, so the source cannot be checked.",
                             evidence_ids=[r.id for r in noref], missing=["PMID or DOI"], n_records=n))

        pairs: dict[int, list[EvidenceRecord]] = defaultdict(list)
        for r in recs:
            if r.compound:
                pairs[r.compound_id].append(r)
        for cid, prs in pairs.items():
            c = prs[0].compound
            types = {EvidenceType(r.evidence_type) for r in prs}
            if types & IDENT_TYPES and EvidenceType.DIRECT_EXPERIMENTAL not in types:
                gaps.append(_gap(GapCategory.COMPOUND_IDENTIFICATION, GapKind.EVIDENCE_QUALITY, Certainty.DATASET_FACT,
                                 plant=p, compound=c,
                                 explanation=f"The indexed evidence for {c.name} in {p.accepted_name} is only "
                                             + " and ".join(sorted(t.value.replace('_', ' ') for t in types & IDENT_TYPES))
                                             + "; no direct experimental identification is indexed.",
                                 evidence_ids=[r.id for r in prs], missing=["direct experimental identification"], n_records=n))
            if not c.inchikey:
                gaps.append(_gap(GapCategory.UNRESOLVED_STRUCTURE, GapKind.EVIDENCE_QUALITY, Certainty.DATASET_FACT,
                                 plant=p, compound=c, explanation=f"{c.name} has no resolved PubChem identifier in MOLECULAB, "
                                                                 "so its structure cannot be checked here.",
                                 evidence_ids=[r.id for r in prs], missing=["structure identifier (PubChem CID / InChIKey)"],
                                 n_records=n))
            for r in prs:  # each record must support at least the occurrence it is filed under
                claim = (f"Docking predicted that {c.name} from {p.accepted_name} binds its targets."
                         if EvidenceType(r.evidence_type) == EvidenceType.COMPUTATIONAL
                         else f"{c.name} was reported in {p.accepted_name}.")
                v = _verify(records, plants, compounds, claim, [r.id])
                if v["status"] in (Verdict.INSUFFICIENT, Verdict.NOT_SUPPORTED, Verdict.CONTRADICTED):
                    gaps.append(_gap(GapCategory.POORLY_SUPPORTED, GapKind.EVIDENCE_QUALITY, Certainty.RULE_ASSESSMENT,
                                     plant=p, compound=c, extra_id=f"r{r.id}",
                                     explanation=f"Record #{r.id} is filed as evidence about {c.name} in {p.accepted_name}, but "
                                                 f"the claim verifier returns '{Verdict(v['status']).value}': {v['explanation']}",
                                     evidence_ids=[r.id], missing=["a source excerpt that supports the recorded relationship"],
                                     n_records=n))

    for x in contradictions(s):
        p = next(pp for pp in plants if pp.id == x["plant"]["id"])
        c = next(cc for cc in compounds if cc.id == x["compound"]["id"])
        gaps.append(_gap(GapCategory.CONFLICTING_FINDINGS, GapKind.CONFLICT, Certainty.UNRESOLVED, plant=p, compound=c,
                         explanation=f"Indexed publications report conflicting {x['conflict_type']} findings for {c.name} in "
                                     f"{p.accepted_name} (assessment: {x['assessment'].replace('_', ' ')}).",
                         evidence_ids=x["evidence_ids"], missing=x["missing_context"][:3], n_records=n))
    return gaps


def gaps_for(s: Session, *, plant_id=None, compound_id=None, category=None) -> list[dict]:
    out = detect_gaps(s)
    if plant_id is not None:
        out = [g for g in out if g["plant"] and g["plant"]["id"] == plant_id]
    if compound_id is not None:
        out = [g for g in out if (g["compound"] and g["compound"]["id"] == compound_id)
               or any(c["id"] == compound_id for c in g["related_compounds"])]
    if category is not None:
        out = [g for g in out if g["category"] == category]
    return out


# ---------------------------------------------------------------- hypotheses

ACTIVITY_CONTEXT = re.compile(r"\b(anti-?[a-z]+(ic|al|ory|ive|like)?|antioxidant|neuroprotect\w*|hepatoprotect\w*|"
                              r"cytotox\w*|inhibit\w*|antidepressant-like|activity|active fraction)\b", re.I)
MATERIAL = re.compile(r"\b(total extract|extract|fraction|essential oil|oil|nanoparticles?|conjugates?|resin)\b", re.I)
HEDGE = re.compile(r"\b(may|might|could)\b", re.I)
FORBIDDEN_HYPOTHESIS = {ClaimCategory.THERAPEUTIC_EFFICACY, ClaimCategory.CLINICAL_EFFICACY, ClaimCategory.SAFETY,
                        ClaimCategory.TOXICITY}


def check_hypothesis(statement: str) -> str | None:
    """Return a rejection reason, or None if the hypothesis wording is acceptable."""
    body = statement.removeprefix("Hypothesis (unvalidated): ")
    if not statement.startswith("Hypothesis (unvalidated): "):
        return "not labelled as an unvalidated hypothesis"
    if not HEDGE.search(body):
        return "not hedged (needs 'may', 'might' or 'could')"
    cat = classify_claim(body)
    if cat in FORBIDDEN_HYPOTHESIS:
        return f"asserts {cat.value.replace('_', ' ')}, which a research hypothesis here may not do"
    try:
        assert_dataset_scoped(statement)
    except ScopeViolation as exc:
        return str(exc)
    return None


def _observations(records, plants, compounds, recs: list[EvidenceRecord]) -> tuple[list[dict], list[dict]]:
    """Documented observations = record statements that pass the v0.3 verifier."""
    accepted, rejected = [], []
    for r in recs:
        t = EvidenceType(r.evidence_type)
        if t == EvidenceType.TRADITIONAL_USE:
            claim = f"{r.plant.accepted_name} is traditionally used: {r.observation}"
        elif t == EvidenceType.COMPUTATIONAL and r.compound:
            claim = f"Docking predicted that {r.compound.name} from {r.plant.accepted_name} complements its targets."
        elif r.compound:
            claim = f"{r.compound.name} was reported in {r.plant.accepted_name}" + \
                    (f" ({r.plant_part})." if r.plant_part else ".")
        else:
            continue
        v = _verify(records, plants, compounds, claim, [r.id])
        entry = {"statement": claim, "evidence_ids": [r.id], "status": v["status"], "evidence_type": r.evidence_type,
                 "excerpt": r.original_excerpt, "explanation": v["explanation"],
                 "publication": _pub(r.publication)}
        (accepted if v["status"] in (Verdict.SUPPORTED, Verdict.PARTIALLY_SUPPORTED) else rejected).append(entry)
    return accepted, rejected


def _attribution(records, plants, compounds, recs, obs_ids) -> tuple[list[dict], list[str]]:
    """Pattern 1: a compound isolated/identified from a material whose source reports an activity."""
    out, abstain = [], []
    has_bio = {r.compound_id for r in records if EvidenceType(r.evidence_type) == EvidenceType.EXPERIMENTAL_BIOACTIVITY}
    seen = set()
    for r in recs:
        if not r.compound or r.id not in obs_ids or r.compound_id in has_bio or EvidenceType(r.evidence_type) != EvidenceType.DIRECT_EXPERIMENTAL:
            continue
        title = r.publication.title or ""
        m = ACTIVITY_CONTEXT.search(title)
        if not m or (r.compound_id, r.publication_id) in seen:
            continue
        seen.add((r.compound_id, r.publication_id))
        material = MATERIAL.search(title)
        level = material.group(0).lower() if material else "unspecified material"
        out.append({
            "category": "activity_attribution",
            "research_question": f"Does the indexed literature characterise the biological activity of {r.compound.name} "
                                 f"from {r.plant.accepted_name} itself, or only of the {level} studied?",
            "hypothesis": f"Hypothesis (unvalidated): {r.compound.name} might contribute to the activity described in the "
                          f"source publication (\"{m.group(0)}\", reported at the level of: {level}).",
            "reasoning": [f"Record #{r.id} reports {r.compound.name} in {r.plant.accepted_name} (verified against the excerpt).",
                          f"The same publication's title describes an activity: \"{title}\".",
                          f"No experimental activity evidence for isolated {r.compound.name} is currently indexed in MOLECULAB."],
            "supporting_evidence_ids": [r.id],
            "source_context": {"evidence_id": r.id, "publication": _pub(r.publication), "title": title,
                               "excerpt": r.original_excerpt},
            "missing_evidence": [f"activity of isolated {r.compound.name} measured in the same assay",
                                 f"comparison between {r.compound.name} and the whole {level}",
                                 "dose-response data"],
            "suggested_validation": [
                f"Read the full text: check whether {r.compound.name} itself was tested in the reported assay.",
                f"If not, test isolated {r.compound.name} (reference standard) in the same assay as the {level}, "
                "with the {level} as comparator.".replace("{level}", level),
                "Search the literature for independent activity studies of the compound."],
            "limitations": [f"The activity was described for the {level}, which contains other constituents.",
                            "A title is a summary; the experimental model (in vitro, animal) must be read in the full text.",
                            "Alternative explanation: other constituents, or combined effects, may account for the activity."],
        })
    return out, abstain


def _chemotype(conflicts: list[dict]) -> list[dict]:
    out = []
    for x in conflicts:
        vals = ", ".join(f"{f['value_percent']} % (#{f['evidence_id']})" for f in x["findings"] if f["value_percent"] is not None)
        out.append({
            "category": "compositional_variation",
            "research_question": f"Why do indexed reports of {x['compound']['name']} content in "
                                 f"{x['plant']['accepted_name']} differ ({vals})?",
            "hypothesis": f"Hypothesis (unvalidated): the reported differences in {x['compound']['name']} content could "
                          "reflect chemotype, geographic origin, plant part or analytical-method differences between samples.",
            "reasoning": [f"Contradiction explorer: {x['assessment_explanation']}"] + x["documented_differences"],
            "supporting_evidence_ids": x["evidence_ids"],
            "source_context": {"contradiction_id": x["id"]},
            "missing_evidence": x["missing_context"],
            "suggested_validation": [
                "Analyse vouchered samples from documented localities with one standardised GC-MS protocol.",
                "Record plant part, phenological stage and distillation conditions for every sample.",
                "Check the full texts of the cited studies for sample origin and method details."],
            "limitations": ["Only the indexed abstracts were compared.",
                            "Measurement error or reporting differences are alternative explanations."],
        })
    return out


def _confirmation(recs, obs) -> list[dict]:
    out = []
    by_c: dict[int, list[EvidenceRecord]] = defaultdict(list)
    for r in recs:
        if r.compound:
            by_c[r.compound_id].append(r)
    accepted_ids = {i for o in obs for i in o["evidence_ids"]}
    for prs in by_c.values():
        types = {EvidenceType(r.evidence_type) for r in prs}
        if types & IDENT_TYPES and EvidenceType.DIRECT_EXPERIMENTAL not in types:
            ids = [r.id for r in prs if r.id in accepted_ids]
            if not ids:
                continue
            c, p = prs[0].compound, prs[0].plant
            out.append({
                "category": "identification_confirmation",
                "research_question": f"Is the presence of {c.name} in {p.accepted_name} established by direct identification?",
                "hypothesis": f"Hypothesis (unvalidated): {c.name} may be present in {p.accepted_name} at detectable levels, "
                              "as tentative or secondary reports suggest.",
                "reasoning": [f"Indexed evidence for {c.name} in {p.accepted_name} is only "
                              + ", ".join(sorted(t.value.replace('_', ' ') for t in types)) + "."],
                "supporting_evidence_ids": ids, "source_context": {},
                "missing_evidence": ["isolation with spectroscopic structure confirmation"],
                "suggested_validation": [f"Isolate or detect {c.name} against a reference standard (NMR, LC-MS/MS) in "
                                         f"authenticated {p.accepted_name} material."],
                "limitations": ["Tentative assignments and secondary statements can be wrong; the hypothesis may be false."],
            })
    return out


def _prediction_testing(obs) -> list[dict]:
    out = []
    for o in obs:
        if o["evidence_type"] == EvidenceType.COMPUTATIONAL:
            out.append({
                "category": "prediction_testing",
                "research_question": "Does the predicted interaction hold experimentally?",
                "hypothesis": "Hypothesis (unvalidated): the docking prediction in the cited record could be tested in a binding assay.",
                "reasoning": [o["statement"]], "supporting_evidence_ids": o["evidence_ids"], "source_context": {},
                "missing_evidence": ["experimental binding or activity data"],
                "suggested_validation": ["Run a biochemical binding assay against the predicted targets."],
                "limitations": ["Docking scores do not establish binding."],
            })
    return out


def hypotheses(s: Session, *, plant_id: int | None = None, compound_id: int | None = None,
               question: str | None = None) -> dict:
    plants, compounds, records = _load(s)
    plant = next((p for p in plants if p.id == plant_id), None) if plant_id else None
    compound = next((c for c in compounds if c.id == compound_id), None) if compound_id else None
    assessment = None
    if question:
        from app.assistant import evaluate
        ab = evaluate(s, question)[0]
        ent = detect_entities(question, plants, compounds)
        plant = plant or (ent.plants[0] if ent.plants else None)
        compound = compound or (ent.compounds[0] if ent.compounds else None)
        assessment = {"question": question, "question_type": ab["question_type"], "answerable": ab["answerable"],
                      "status": ab["status"], "reason": ab["reason"], "missing_evidence": ab["missing_evidence"]}
    scope = [r for r in records if (plant is None or r.plant_id == plant.id) and (compound is None or r.compound_id == compound.id)]
    subject = " / ".join(x for x in (plant.accepted_name if plant else None, compound.name if compound else None) if x)
    result = {"scope": {"plant": {"id": plant.id, "accepted_name": plant.accepted_name} if plant else None,
                        "compound": {"id": compound.id, "name": compound.name} if compound else None},
              "question_assessment": assessment, "documented_observations": [], "unverified_records": [],
              "hypotheses": [], "rejected_hypotheses": [], "abstentions": [], "gaps": [],
              "status_note": "Hypotheses are unvalidated research directions generated by fixed rules from indexed "
                             "evidence. They are not findings, mechanisms or recommendations."}
    if plant is None and compound is None:
        result["abstentions"].append("No indexed plant or compound was selected or recognised in the question.")
        return result
    if not scope:
        result["abstentions"].append(f"No evidence records are currently indexed in MOLECULAB for {subject}.")
        return result
    obs, unverified = _observations(records, plants, compounds, scope)
    result["documented_observations"], result["unverified_records"] = obs, unverified
    obs_ids = {i for o in obs for i in o["evidence_ids"]}
    if not obs:
        result["abstentions"].append(f"No indexed record for {subject} passed claim verification; no hypothesis is generated.")
        return result
    conflicts = contradictions(s, plant.id if plant else None, compound.id if compound else None)
    attr, _ = _attribution(records, plants, compounds, scope, obs_ids)
    candidates = attr + _chemotype(conflicts) + _confirmation(scope, obs) + _prediction_testing(obs)
    for h in candidates:
        reason = check_hypothesis(h["hypothesis"])
        bad_ids = [i for i in h["supporting_evidence_ids"] if i not in {r.id for r in records}]
        if bad_ids:
            reason = f"supporting evidence IDs do not exist: {bad_ids}"
        if reason:
            result["rejected_hypotheses"].append({"hypothesis": h["hypothesis"], "reason": reason})
            continue
        h["status"] = "unvalidated_hypothesis"
        h["supporting_excerpts"] = [{"evidence_id": r.id, "excerpt": r.original_excerpt, "publication": _pub(r.publication)}
                                    for r in records if r.id in h["supporting_evidence_ids"]]
        result["hypotheses"].append(h)
    if assessment and not assessment["answerable"]:
        result["abstentions"].append(f"The question cannot be answered from the indexed evidence: {assessment['reason']}")
    if not result["hypotheses"]:
        result["abstentions"].append(
            f"No hypothesis generated for {subject}: the indexed evidence ({len(obs)} verified observation(s)) contains no "
            "documented activity context, conflict, tentative-only identification or computational prediction to motivate one. "
            "MOLECULAB does not generate hypotheses without a documented motivating observation.")
    result["gaps"] = [g for g in gaps_for(s, plant_id=plant.id if plant else None,
                                          compound_id=compound.id if compound else None)][:12]
    assert_dataset_scoped(result)
    return result


# ---------------------------------------------------------------- priorities

def _dims(recs: list[EvidenceRecord], conflicts: int) -> dict:
    if not recs:
        return {}
    areas = [a for a, (test, _) in EVIDENCE_AREAS.items() if any(test(r) for r in recs)]
    types = defaultdict(int)
    for r in recs:
        types[r.evidence_type] += 1
    pubs = {r.publication_id for r in recs}
    lacking = sum(1 for r in recs if not r.identification_method or not r.plant_part or not r.geographic_origin)
    reviewed = sum(1 for r in recs if effective_review_status(r) == "expert_confirmed")
    return {
        "evidence_coverage": {"value": f"{len(areas)} of {len(EVIDENCE_AREAS)} evidence areas",
                              "areas_covered": areas, "available": True,
                              "how": "Counts evidence areas (identification, quantification, biological activity, clinical, "
                                     "toxicity, pharmacokinetics, traditional use, computational) with at least one indexed record. "
                                     "Toxicity and pharmacokinetics cannot be covered yet: no such category exists."},
        "evidence_quality": {"value": dict(types), "expert_reviewed": reviewed, "available": True,
                             "how": "Number of indexed records per evidence category, and how many have an expert confirmation."},
        "independent_publications": {"value": len(pubs), "available": True,
                                     "how": "Distinct indexed publications (by PMID/record). Shared authorship or "
                                            "laboratories are not detected, so independence may be overstated."},
        "contradictions": {"value": conflicts, "available": True,
                           "how": "Conflicts found by the contradiction explorer (quantities differing >2x, or rank conflicts)."},
        "missing_experimental_information": {"value": f"{lacking} of {len(recs)} records", "available": True,
                                             "how": "Records lacking a stated method, plant part or geographic origin."},
        "research_feasibility": {"value": None, "available": False,
                                 "how": "Unavailable: MOLECULAB holds no data on material availability, cost, assays or "
                                        "regulatory constraints, so feasibility is not estimated."},
    }


def _signals(recs, conflicts: int, gaps: list[dict]) -> list[str]:
    sig = []
    if conflicts:
        sig.append(f"{conflicts} unresolved conflict(s) between indexed reports")
    tentative = {g["compound"]["name"] for g in gaps if g["category"] == GapCategory.COMPOUND_IDENTIFICATION}
    if tentative:
        sig.append("tentative/secondary-only identification for: " + ", ".join(sorted(tentative)))
    act = [r for r in recs if r.compound and ACTIVITY_CONTEXT.search(r.publication.title or "")]
    if act:
        sig.append(f"{len({r.publication_id for r in act})} indexed publication(s) describe an activity of a material "
                   "containing these compounds, without compound-level activity evidence indexed")
    poorly = [g for g in gaps if g["category"] == GapCategory.POORLY_SUPPORTED]
    if poorly:
        sig.append(f"{len(poorly)} record(s) fail verification of their recorded relationship")
    return sig


def priorities(s: Session, kind: str = "plant") -> dict:
    plants, compounds, records = _load(s)
    gaps = detect_gaps(s)
    conflicts = contradictions(s)
    items = []
    if kind == "plant":
        for p in plants:
            recs = [r for r in records if r.plant_id == p.id]
            n_conf = sum(1 for x in conflicts if x["plant"]["id"] == p.id)
            pg = [g for g in gaps if g["plant"] and g["plant"]["id"] == p.id]
            items.append({"type": "plant", "id": p.id, "name": p.accepted_name, "evidence_ids": [r.id for r in recs],
                          "dimensions": _dims(recs, n_conf), "investigation_signals": _signals(recs, n_conf, pg),
                          "open_gaps": len(pg)})
    else:
        for c in compounds:
            recs = [r for r in records if r.compound_id == c.id]
            n_conf = sum(1 for x in conflicts if x["compound"]["id"] == c.id)
            cg = [g for g in gaps if (g["compound"] and g["compound"]["id"] == c.id)]
            items.append({"type": "compound", "id": c.id, "name": c.name, "evidence_ids": [r.id for r in recs],
                          "dimensions": _dims(recs, n_conf), "investigation_signals": _signals(recs, n_conf, cg),
                          "open_gaps": len(cg)})
    items = [i for i in items if i["evidence_ids"]] + [
        {**i, "dimensions": {}, "investigation_signals": [],
         "note": "No indexed evidence; dimensions unavailable."} for i in items if not i["evidence_ids"]]
    items.sort(key=lambda i: (-len(i["investigation_signals"]),
                              -(i["dimensions"].get("independent_publications", {}).get("value") or 0), i["name"]))
    out = {"kind": kind, "items": items,
           "ordering": "Items are listed by the number of open investigation signals, then by the number of independent "
                       "indexed publications. This is a reading order, not a score: no dimension is weighted or combined.",
           "disclaimer": "Prioritisation reflects the MOLECULAB index only. It does not estimate the likelihood that any "
                         "compound or plant yields a medicine."}
    assert_dataset_scoped(out)
    return out
