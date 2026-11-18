"""Scientific claim verification.

verify_claim() runs independent stages and keeps the WEAKEST verdict:

1. citation integrity      - IDs given, existing, inside the allowed context (hard reject otherwise)
2. entity grounding        - the claim's compound/plant match the cited record (species check)
3. compatibility ceiling   - claim category vs evidence category (app.claims.COMPATIBILITY_MATRIX)
4. source-text checks      - excerpt verified against source; compound named in the excerpt;
                             category-specific cues near the compound; numbers; superlatives; model
5. semantic entailment     - deterministic span-level check (always) + optional LLM assessor that can
                             only LOWER the verdict, never raise it, and must quote the excerpt

Structured-record claims (molecular properties, taxonomy) are checked against the compound/plant
record instead of evidence records. No numerical confidence score is produced.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from app.claims import (ABUNDANCE_CUE, COMPATIBILITY_MATRIX, STRENGTH, CheckResult, ClaimCategory, Verdict,
                        classify_claim, weakest)
from app.models import Compound, EvidenceRecord, EvidenceType, Plant, ReviewStatus
from app.reasoning import compound_keys, plant_keys
from app.textnorm import fold, normalize


# ---------- helpers ----------

def _mentioned(text_folded: str, keys: set[str]) -> list[str]:
    return [k for k in keys if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", text_folded)]


def entities_in(text: str, plants: list[Plant], compounds: list[Compound]) -> tuple[list[Plant], list[Compound]]:
    f = fold(text)
    return ([p for p in plants if _mentioned(f, plant_keys(p))],
            [c for c in compounds if _mentioned(f, compound_keys(c))])


def _windows(excerpt: str, keys: set[str], width: int = 200) -> list[str]:
    """The sentence(s) of the excerpt that mention the compound (long list sentences put the verb far
    from the compound). Very long sentences fall back to a window around the mention."""
    text = normalize(excerpt)
    spans = []
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", text):
        f = fold(sentence)
        for k in keys:
            for m in re.finditer(rf"(?<!\w){re.escape(k)}(?!\w)", f):
                spans.append(sentence if len(sentence) <= 500 else
                             sentence[max(0, m.start() - width): m.end() + width])
    return spans


_NUM = re.compile(r"\d+(?:\.\d+)?")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
SUPERLATIVE = re.compile(r"\b(most abundant|most common|highest|largest|richest|predominant\w*|dominant|"
                         r"dominated|main|major|principal|chief|primary)\b", re.I)
SOURCE_SUPERLATIVE = re.compile(r"(most abundant|highest|predominant|dominant|dominated|major|main|principal|"
                                r"chief|primary|richest|excellent source)", re.I)
SINGULAR_SUPERLATIVE = re.compile(r"\bthe (most abundant|most common|highest|largest|richest|predominant|dominant|"
                                  r"main|major|principal|chief|primary)\b", re.I)


def _own_percent(excerpt: str, ckeys: set[str]) -> float | None:
    """Percentage stated right after the compound's name, e.g. 'pulegone (62.17 %)'."""
    f = fold(excerpt)
    for k in ckeys:
        m = re.search(rf"(?<!\w){re.escape(k)}(?!\w)\s*\(?\s*(\d+(?:\.\d+)?)\s*%", f)
        if m:
            return float(m.group(1))
    return None


NEGATION = re.compile(r"\b(not (detected|found|present|identified|observed)|absent|no (significant )?"
                      r"(activity|effect)|did not|inactive|lack(ed|s)?)\b", re.I)
CATEGORY_CUES: dict[ClaimCategory, str] = {
    ClaimCategory.CHEMICAL_IDENTIFICATION: r"(identif|isolat|characteri|elucidat|detect|assign|yield|obtain|purif|"
                                           r"confirm|nmr|ms\b|hplc|gc)",
    # a quantity or ranking of the compound in the material also documents its occurrence
    ClaimCategory.CHEMICAL_OCCURRENCE: r"(identif|isolat|detect|assign|yield|obtain|contain|present|found|constituent|"
                                       r"compound|component|source|revealed|rich|dominat|major|presence|from|" + ABUNDANCE_CUE + ")",
    ClaimCategory.CHEMICAL_ABUNDANCE: ABUNDANCE_CUE,
    ClaimCategory.TRADITIONAL_USE: r"(tradition|folk|ethno|used as|valued)",
    ClaimCategory.COMPUTATIONAL_PREDICTION: r"(docking|in silico|simulat|predict|computational|binding affinit)",
    ClaimCategory.CLINICAL_EFFICACY: r"(patient|clinical|trial|human|participant|volunteer)",
    ClaimCategory.THERAPEUTIC_EFFICACY: r"(patient|clinical|trial|human|participant)",
}
ACTIVITY_TERMS = re.compile(r"\b(antioxidant|anti[- ]?inflammator\w*|antimicrob\w*|antibacter\w*|antifung\w*|"
                            r"antivir\w*|cytotox\w*|anticancer|antitumou?r|neuroprotect\w*|hepatoprotect\w*|"
                            r"inhibit\w*)\b", re.I)
IN_VITRO = re.compile(r"\b(in vitro|cell(s| line)|cultured|enzyme assay|assay)\b", re.I)
ANIMAL = re.compile(r"\b(mice|mouse|rats?|in vivo|animal|zebrafish|rabbits?)\b", re.I)
HUMAN = re.compile(r"\b(patients?|humans?|clinical trial|participants|volunteers|randomi[sz]ed)\b", re.I)


# ---------- semantic verifiers ----------

@dataclass
class Assessment:
    verdict: Verdict
    explanation: str
    span: str | None = None
    verifier: str = "deterministic-span"


class EntailmentVerifier(Protocol):
    name: str

    def assess(self, claim: str, category: ClaimCategory, excerpt: str, compound_keys_: set[str]) -> Assessment: ...


class DeterministicSpanVerifier:
    """Checks that the excerpt span around the compound expresses the claim's predicate."""
    name = "deterministic-span"

    def assess(self, claim: str, category: ClaimCategory, excerpt: str, ckeys: set[str]) -> Assessment:
        spans = _windows(excerpt, ckeys) if ckeys else [normalize(excerpt)]
        if not spans:
            return Assessment(Verdict.INSUFFICIENT, "The source excerpt does not mention the claimed compound.")
        span = spans[0]
        if any(NEGATION.search(s) for s in spans):
            return Assessment(Verdict.CONTRADICTED, "The source span negates the finding.", span)
        cue = CATEGORY_CUES.get(category)
        if cue and not any(re.search(cue, s, re.I) for s in spans):
            return Assessment(Verdict.NOT_SUPPORTED,
                              f"The source mentions the compound but does not express a {category.value.replace('_', ' ')} "
                              "statement about it.", span)
        if category == ClaimCategory.BIOLOGICAL_ACTIVITY or category == ClaimCategory.MECHANISM_OF_ACTION:
            terms = {m.group(0).lower() for m in ACTIVITY_TERMS.finditer(claim)}
            missing = [t for t in terms if not any(t[:7] in fold(s) for s in spans)]
            if not terms or missing:
                return Assessment(Verdict.NOT_SUPPORTED, "The source span does not report the claimed activity"
                                  + (f" ({', '.join(missing)})" if missing else "") + ".", span)
        claim_pcts = [float(x) for x in _PCT.findall(claim)]
        if claim_pcts:
            span_pcts = [float(x) for s in spans for x in _PCT.findall(s)]
            if span_pcts and not any(abs(a - b) < 1e-9 for a in claim_pcts for b in span_pcts):
                return Assessment(Verdict.CONTRADICTED, f"The source reports {span_pcts} % for this compound, "
                                  f"not {claim_pcts} %.", span)
        if SINGULAR_SUPERLATIVE.search(claim) and ckeys:
            own = _own_percent(excerpt, ckeys)
            all_pcts = [float(x) for x in _PCT.findall(excerpt)]
            if own is not None and all_pcts and own < max(all_pcts):
                return Assessment(Verdict.CONTRADICTED, f"The claim says it is the top constituent, but this source "
                                  f"reports it at {own} % while another constituent is at {max(all_pcts)} %.", span)
        if SUPERLATIVE.search(claim) and not any(SOURCE_SUPERLATIVE.search(s) for s in spans):
            return Assessment(Verdict.NOT_SUPPORTED, "The claim is comparative/superlative, but the source span "
                              "gives no comparison between constituents.", span)
        return Assessment(Verdict.SUPPORTED, "The source span about this compound expresses the claimed "
                          f"{category.value.replace('_', ' ')}.", span)


class LLMEntailmentVerifier:
    """Optional second opinion from an LLM provider. Its output is an assessment, never ground truth:
    malformed output, or a quoted span that is not in the excerpt, yields INSUFFICIENT."""

    PROMPT = ("You check whether a source excerpt supports a scientific claim. Judge ONLY from the excerpt. "
              "Reply with JSON only: {\"verdict\": one of [\"supported\", \"partially_supported\", \"contradicted\", "
              "\"not_supported\", \"insufficient_information\"], \"explanation\": \"one or two sentences\", "
              "\"quoted_span\": \"the exact words of the excerpt you relied on, copied verbatim\"}")

    def __init__(self, provider):
        self.provider = provider
        self.name = f"llm:{getattr(provider, 'model', 'unknown')}"

    def assess(self, claim: str, category: ClaimCategory, excerpt: str, ckeys: set[str]) -> Assessment:
        try:
            raw = self.provider.complete(self.PROMPT, f"Claim: {claim}\nClaim category: {category.value}\n"
                                                      f"Excerpt: {excerpt}")
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0)) if m else None
            verdict = Verdict(data["verdict"])
            explanation = str(data["explanation"]).strip()[:500]
            span = str(data.get("quoted_span", "")).strip()
        except Exception as exc:  # any provider/format failure means: no assessment
            return Assessment(Verdict.INSUFFICIENT, f"Semantic verifier unavailable or malformed output ({type(exc).__name__}).",
                              verifier=self.name)
        if not explanation or (verdict in (Verdict.SUPPORTED, Verdict.PARTIALLY_SUPPORTED)
                               and (not span or fold(span) not in fold(excerpt))):
            return Assessment(Verdict.INSUFFICIENT, "Semantic verifier did not quote the excerpt verbatim; "
                              "its assessment was discarded.", verifier=self.name)
        return Assessment(verdict, explanation, span or None, verifier=self.name)


# ---------- claim verification ----------

def _expert_reviewed(r: EvidenceRecord) -> bool:
    if not r.reviews:
        return False
    return max(r.reviews, key=lambda x: (x.created_at, x.id)).review_status == ReviewStatus.EXPERT_CONFIRMED


def _plant_in_source(r: EvidenceRecord) -> str | None:
    keys = plant_keys(r.plant) | {fold(f"{r.plant.accepted_name.split()[0][0]}. {r.plant.accepted_name.split()[1]}")}
    for where, text in (("excerpt", r.original_excerpt), ("title", r.publication.title or ""),
                        ("abstract", r.publication.abstract or "")):
        if _mentioned(fold(text), keys):
            return where
    return None


def verify_against_record(claim: str, category: ClaimCategory, r: EvidenceRecord, claim_plants: list[Plant],
                          claim_compounds: list[Compound], verifiers: list) -> dict:
    checks: list[CheckResult] = []
    limitations: list[str] = []
    req = COMPATIBILITY_MATRIX[category]
    verdicts: list[Verdict] = []

    def stage(name: str, ok: bool, detail: str, fail: Verdict | None = None):
        checks.append(CheckResult(name, ok, detail))
        if not ok and fail is not None:
            verdicts.append(fail)

    # 2. entity grounding
    if claim_compounds:
        ok = r.compound is not None and r.compound.id in {c.id for c in claim_compounds}
        stage("compound_match", ok, f"record concerns {r.compound.name if r.compound else 'no compound'}; claim "
              f"names {', '.join(c.name for c in claim_compounds)}", Verdict.NOT_SUPPORTED)
    elif req.needs_compound_in_excerpt and r.compound is None:
        stage("compound_match", False, "claim names no indexed compound and the record has none", Verdict.INSUFFICIENT)
    if claim_plants:
        ok = r.plant_id in {p.id for p in claim_plants}
        stage("plant_match", ok, f"record concerns {r.plant.accepted_name}; claim names "
              f"{', '.join(p.accepted_name for p in claim_plants)}", Verdict.NOT_SUPPORTED)

    # 3. compatibility ceiling
    ceiling = req.evidence.get(EvidenceType(r.evidence_type))
    stage("evidence_category", ceiling is not None,
          f"{r.evidence_type} {'can' if ceiling else 'cannot'} support a {category.value} claim"
          + (f" (at most {ceiling.value})" if ceiling else "") + f". {req.note}", Verdict.NOT_SUPPORTED)
    if ceiling:
        verdicts.append(ceiling)

    # 4. source-text checks
    stage("excerpt_matches_source", r.verification_status == "excerpt_verified",
          f"excerpt status {r.verification_status}", Verdict.INSUFFICIENT)
    ckeys = compound_keys(r.compound) if r.compound else set()
    if req.needs_compound_in_excerpt and r.compound:
        named = bool(_mentioned(fold(r.original_excerpt), ckeys))
        stage("compound_in_excerpt", named, f"the excerpt {'names' if named else 'does not name'} {r.compound.name}",
              Verdict.INSUFFICIENT)
    where = _plant_in_source(r)
    stage("plant_in_source", where is not None,
          f"plant named in the {where}" if where else "plant not named in the excerpt, title or abstract",
          Verdict.INSUFFICIENT)
    if where and where != "excerpt":
        limitations.append(f"The plant link comes from the publication {where}, not the excerpt itself.")
    source_text = normalize(f"{r.original_excerpt} {r.observation}")
    stray = [n for n in _NUM.findall(claim) if n not in source_text and n not in fold(" ".join(ckeys))]
    stage("numbers_in_source", not stray, "all numbers appear in the record" if not stray
          else f"numbers not in the record: {stray}", Verdict.NOT_SUPPORTED)
    if category in (ClaimCategory.CLINICAL_EFFICACY, ClaimCategory.THERAPEUTIC_EFFICACY):
        human = bool(HUMAN.search(r.original_excerpt))
        model = "human" if human else "animal" if ANIMAL.search(r.original_excerpt) else \
            "in vitro" if IN_VITRO.search(r.original_excerpt) else "unstated"
        stage("experimental_model", human, f"experimental model in source: {model}; efficacy in humans needs human "
              "studies", Verdict.NOT_SUPPORTED)
    if category == ClaimCategory.BIOLOGICAL_ACTIVITY and IN_VITRO.search(r.original_excerpt):
        limitations.append("In-vitro finding: it does not establish activity in animals or humans.")

    # 5. semantic entailment (each verifier can only lower the verdict)
    assessments = [v.assess(claim, category, r.original_excerpt, ckeys if req.needs_compound_in_excerpt else set())
                   for v in verifiers]
    for a in assessments:
        stage(f"semantic:{a.verifier}", a.verdict in (Verdict.SUPPORTED, Verdict.PARTIALLY_SUPPORTED), a.explanation)
        verdicts.append(a.verdict)

    final = weakest(*verdicts) if verdicts else Verdict.INSUFFICIENT
    if EvidenceType(r.evidence_type) in (EvidenceType.SECONDARY_SOURCE, EvidenceType.TENTATIVE):
        limitations.append(f"Evidence is {r.evidence_type.replace('_', ' ')}.")
    span = next((a.span for a in assessments if a.span), None)
    return {"evidence_id": r.id, "verdict": final, "checks": [c.__dict__ for c in checks],
            "limitations": limitations, "excerpt": r.original_excerpt, "matched_span": span,
            "publication": {"pmid": r.publication.pmid, "doi": r.publication.doi, "year": r.publication.year,
                            "title": r.publication.title},
            "evidence_type": r.evidence_type, "expert_reviewed": _expert_reviewed(r),
            "explanation": next((a.explanation for a in assessments), "")}


def _record_free(claim: str, category: ClaimCategory, plants: list[Plant], compounds: list[Compound]) -> dict:
    """Molecular properties against the compound record; taxonomy against the plant record."""
    checks, sources = [], []
    verdict, why = Verdict.INSUFFICIENT, "No structured record named in the claim."
    if category == ClaimCategory.CHEMICAL_PROPERTY and compounds:
        c = compounds[0]
        values = {"molecular formula": c.molecular_formula, "molecular weight": c.molecular_weight,
                  "inchikey": c.inchikey, "smiles": c.smiles, "pubchem cid": c.pubchem_cid}
        claim_norm = normalize(claim).replace(" ", "")
        matched = [k for k, v in values.items() if v is not None and str(v).replace(" ", "") in claim_norm]
        formula_tokens = re.findall(r"\b(?:[A-Z][a-z]?\d*){2,}\b", claim)
        wrong = [t for t in formula_tokens if c.molecular_formula and t != c.molecular_formula and re.search(r"\d", t)]
        if wrong:
            verdict, why = Verdict.CONTRADICTED, f"PubChem lists {c.molecular_formula} for {c.name}, not {wrong[0]}."
        elif matched:
            verdict, why = Verdict.SUPPORTED, f"Matches the PubChem record of {c.name} ({', '.join(matched)})."
        elif c.pubchem_cid is None:
            verdict, why = Verdict.INSUFFICIENT, f"{c.name} has no PubChem record in MOLECULAB."
        else:
            verdict, why = Verdict.NOT_SUPPORTED, f"The claimed value does not match the PubChem record of {c.name}."
        sources.append({"type": "pubchem_compound_record", "compound": c.name, "pubchem_cid": c.pubchem_cid,
                        "url": c.identifier_source, "retrieved_at": c.identifier_retrieved_at})
        checks.append(CheckResult("structured_record", verdict == Verdict.SUPPORTED, why).__dict__)
    elif category == ClaimCategory.TAXONOMIC_IDENTITY and plants:
        p = plants[0]
        if p.family and fold(p.family) in fold(claim):
            verdict, why = Verdict.SUPPORTED, f"GBIF lists {p.accepted_name} in {p.family}."
        else:
            verdict, why = Verdict.NOT_SUPPORTED, f"GBIF lists {p.accepted_name} in {p.family or 'an unknown family'}."
        sources.append({"type": "gbif_plant_record", "plant": p.accepted_name, "gbif_key": p.gbif_key,
                        "url": p.taxonomy_source})
        checks.append(CheckResult("structured_record", verdict == Verdict.SUPPORTED, why).__dict__)
    return {"status": verdict, "explanation": why, "structured_sources": sources, "checks": checks}


def verify_claim(claim: str, evidence_ids: list | None, *, records: dict[int, EvidenceRecord],
                 existing_ids: set[int], allowed_ids: set[int] | None, plants: list[Plant],
                 compounds: list[Compound], verifiers: list | None = None) -> dict:
    """Verify one atomic claim against the records it cites. Returns a ClaimVerification dict."""
    verifiers = verifiers or [DeterministicSpanVerifier()]
    claim = normalize(claim)
    category = classify_claim(claim)
    claim_plants, claim_compounds = entities_in(claim, plants, compounds)
    base = {"statement": claim, "category": category, "evidence_ids": [], "source_excerpts": [],
            "structured_sources": [], "checks": [], "limitations": [], "expert_review_required": True,
            "verifiers": [v.name for v in verifiers]}

    if COMPATIBILITY_MATRIX[category].record_free:
        rf = _record_free(claim, category, claim_plants, claim_compounds)
        return {**base, **rf, "expert_review_required": False,
                "limitations": ["Chemical identity and properties do not establish that the compound occurs in any "
                                "plant or has any biological effect."] if category == ClaimCategory.CHEMICAL_PROPERTY
                else []}

    def reject(status: Verdict, why: str, ids=None) -> dict:
        return {**base, "evidence_ids": ids or [], "status": status, "explanation": why,
                "checks": [CheckResult("citation", False, why).__dict__]}

    # 1. citation integrity (hard rejects)
    try:
        ids = [int(i) for i in (evidence_ids or [])]
    except (TypeError, ValueError):
        return reject(Verdict.NOT_SUPPORTED, "Evidence IDs are not integers.")
    if not ids:
        return reject(Verdict.NOT_SUPPORTED, "No evidence cited for this claim.")
    unknown = [i for i in ids if i not in existing_ids]
    if unknown:
        return reject(Verdict.NOT_SUPPORTED, f"Cited evidence IDs do not exist: {unknown}.", ids)
    if allowed_ids is not None and (outside := [i for i in ids if i not in allowed_ids]):
        return reject(Verdict.NOT_SUPPORTED, f"Cited records were not part of the retrieved evidence: {outside}.", ids)

    per_record = [verify_against_record(claim, category, records[i], claim_plants, claim_compounds, verifiers)
                  for i in ids]
    verdicts = [p["verdict"] for p in per_record]
    best = max(verdicts, key=lambda v: STRENGTH[v])
    contradicted = [p for p in per_record if p["verdict"] == Verdict.CONTRADICTED]
    supporting = [p for p in per_record if p["verdict"] in (Verdict.SUPPORTED, Verdict.PARTIALLY_SUPPORTED)]
    if contradicted and supporting:
        status = Verdict.CONTRADICTED
        why = ("Cited records disagree: " + "; ".join(f"#{p['evidence_id']} {p['verdict'].value}" for p in per_record) + ".")
    else:
        status = best
        top = next(p for p in per_record if p["verdict"] == best)
        if best in (Verdict.SUPPORTED, Verdict.PARTIALLY_SUPPORTED):
            why = top["explanation"]
        else:  # say why EACH cited record fails, not just the first
            reasons = [f"#{p['evidence_id']}: " + next((c["detail"] for c in p["checks"] if not c["passed"]),
                                                       p["explanation"]) for p in per_record]
            why = " ".join(reasons)
    limitations = sorted({l for p in (supporting or per_record) for l in p["limitations"]})
    limitations.append("Assessment is based on the abstract/excerpt only, not the full text.")
    return {**base, "status": status, "evidence_ids": ids, "explanation": why,
            "source_excerpts": [{k: p[k] for k in ("evidence_id", "excerpt", "matched_span", "publication",
                                                   "evidence_type", "verdict")} for p in per_record],
            "checks": [{"evidence_id": p["evidence_id"], **c} for p in per_record for c in p["checks"]],
            "limitations": limitations,
            "expert_review_required": not all(p["expert_reviewed"] for p in supporting) if supporting else True}


def summarize(verifications: list[dict]) -> dict:
    out = {v.value: 0 for v in Verdict}
    for v in verifications:
        out[Verdict(v["status"]).value] += 1
    return out
