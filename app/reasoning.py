"""Deterministic scientific-reasoning rules for the research assistant.

Nothing in this module calls an LLM. It decides what kind of question was
asked, which evidence categories can support an answer to that kind of
question, whether the retrieved records are sufficient, where they conflict,
and whether a generated claim stays within what its cited records support.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.models import Compound, EvidenceRecord, EvidenceType, Plant
from app.textnorm import fold, normalize


class QuestionType(StrEnum):
    TAXONOMY = "botanical_identity_taxonomy"
    COMPOSITION = "chemical_composition"
    TRADITIONAL_USE = "traditional_medicinal_use"
    BIOLOGICAL_ACTIVITY = "biological_activity"
    THERAPEUTIC_EFFICACY = "therapeutic_efficacy"
    CLINICAL = "clinical_evidence"
    TOXICITY = "toxicity_safety"
    PHARMACOKINETICS = "pharmacokinetics"
    METHODOLOGY = "research_methodology"
    GENERAL = "general_scientific"
    # v0.3 routing
    CHEMICAL_PROPERTY = "chemical_property"
    ABUNDANCE = "chemical_abundance"
    MECHANISM = "mechanism_of_action"
    PUBLICATION_LOOKUP = "publication_lookup"


QUESTION_LABEL = {
    QuestionType.TAXONOMY: "botanical identity or taxonomy", QuestionType.COMPOSITION: "chemical composition",
    QuestionType.TRADITIONAL_USE: "traditional medicinal use", QuestionType.BIOLOGICAL_ACTIVITY: "biological activity",
    QuestionType.THERAPEUTIC_EFFICACY: "therapeutic efficacy", QuestionType.CLINICAL: "clinical evidence",
    QuestionType.TOXICITY: "toxicity or safety", QuestionType.PHARMACOKINETICS: "pharmacokinetics",
    QuestionType.METHODOLOGY: "research methodology", QuestionType.GENERAL: "a general scientific conclusion",
    QuestionType.CHEMICAL_PROPERTY: "chemical properties", QuestionType.ABUNDANCE: "relative chemical abundance",
    QuestionType.MECHANISM: "a mechanism of action", QuestionType.PUBLICATION_LOOKUP: "which publications report it",
}


class AnswerStatus(StrEnum):
    ANSWERABLE = "answerable"
    PARTIAL = "partially_answerable"  # only weaker evidence; every claim must be qualified
    NOT_ANSWERABLE = "not_answerable"


# Ordered: the first matching rule wins, so the most demanding intent is detected first
# ("does it cure cancer in clinical trials" is CLINICAL, not COMPOSITION).
_RULES: list[tuple[QuestionType, re.Pattern]] = [
    (QuestionType.CLINICAL, re.compile(
        r"\b(clinical|trials?|patients?|randomi[sz]ed|placebo|human stud(y|ies)|in humans)\b")),
    (QuestionType.THERAPEUTIC_EFFICACY, re.compile(
        r"\b(cures?|curing|cured|treats?|treating|treatment|therap(y|ies|eutic)|effective|efficacy|heals?|"
        r"healing|remed(y|ies) for|good for|help(s)? (with|against)|prevents?|fight(s)? |works? (for|against)|"
        r"medicine for|drug for|beat(s)? )")),
    (QuestionType.TOXICITY, re.compile(
        r"\b(toxic\w*|safe|safety|side[- ]effects?|adverse|poison\w*|harmful|dangerous|contraindicat\w*|"
        r"overdose|lethal)\b")),
    (QuestionType.PHARMACOKINETICS, re.compile(
        r"\b(pharmacokinetic\w*|absorb\w*|absorption|bioavailab\w*|metaboli[sz]\w*|half[- ]life|excret\w*|"
        r"distribution in the body|plasma levels?)\b")),
    (QuestionType.CHEMICAL_PROPERTY, re.compile(
        r"\b(molecular (formula|weight|mass)|formula|smiles|inchi\w*|pubchem( cid| id)?|cid|molar mass|iupac( name)?|"
        r"chemical structure|structure of|molecular structure|identifiers?)\b")),
    (QuestionType.ABUNDANCE, re.compile(
        r"(\bmost abundant\b|\babundan\w*|\bhow much\b|\bpercentage\b|\bproportion\b|\bcontent of\b|"
        r"\bconcentration\b|\bpredominant\b|\bdominant\b|\bmain (constituent|compound|component)\b|"
        r"\bmajor (constituent|compound|component)\b|\bhighest\b|\bquantity\b|\bpulegone content\b)")),
    (QuestionType.MECHANISM, re.compile(
        r"\b(mechanism\w*|how does .* work|pathways?|molecular targets?|mode of action)\b")),
    (QuestionType.PUBLICATION_LOOKUP, re.compile(
        r"\b((what|which) (research|papers?|publications?|studies|articles|literature|sources?|references?)|"
        r"who (reported|identified|isolated|described)|literature on|research on|papers? (on|about))\b")),
    (QuestionType.BIOLOGICAL_ACTIVITY, re.compile(
        r"\b(activit(y|ies)|antioxidant|anti[- ]?\w+(al|ory|ic)\b|antimicrobial|antibacterial|antifungal|"
        r"antiviral|cytotoxic\w*|inhibit\w*|in vitro|in vivo|bioactiv\w*|bind(s|ing)?|docking|mechanism)")),
    (QuestionType.TRADITIONAL_USE, re.compile(
        r"\b(traditional\w*|folk|ethno\w*|tea|customar\w*|historically used|used by)\b")),
    (QuestionType.METHODOLOGY, re.compile(
        r"\b(methods?|methodolog\w*|techniques?|how (was|were|is|are) .* (identified|measured|isolated|"
        r"quantified|detected)|identified by|gc[- ]?ms|hplc|nmr|lc[- ]?ms|extraction|chromatograph\w*|"
        r"spectroscop\w*)\b")),
    (QuestionType.TAXONOMY, re.compile(
        r"\b(famil(y|ies)|taxonom\w*|synonyms?|scientific name|accepted name|genus|classif\w*|"
        r"also known as|other names?|called in|names? in (uzbek|russian)|authority)\b")),
    (QuestionType.COMPOSITION, re.compile(
        r"\b(compounds?|constituents?|contains?|containing|chemical\w*|composition|components?|present in|"
        r"isolated|occur\w*|essential oil|alkaloids?|flavonoids?|phenolic\w*|terpen\w*|saponins?|content|"
        r"major|dominant|metabolites?|phytochemi\w*|found in|reported in)\b")),
]


def classify_question(question: str) -> QuestionType:
    q = normalize(question).lower()
    for qtype, pattern in _RULES:
        if pattern.search(q):
            return qtype
    return QuestionType.GENERAL


# Evidence categories that can support an answer, per question type. Question types missing
# here (efficacy, clinical, toxicity, pharmacokinetics) have NO supporting category in the
# current evidence model: occurrence and traditional-use records cannot establish them.
QUALIFIED = "qualified"  # may support only an explicitly qualified (e.g. "predicted") claim
_IDENT = {EvidenceType.DIRECT_EXPERIMENTAL: "full", EvidenceType.TENTATIVE: QUALIFIED,
          EvidenceType.SECONDARY_SOURCE: QUALIFIED}
COMPATIBILITY: dict[QuestionType, dict[EvidenceType, str]] = {
    QuestionType.ABUNDANCE: dict(_IDENT),
    QuestionType.PUBLICATION_LOOKUP: {t: "full" for t in EvidenceType},
    QuestionType.MECHANISM: {EvidenceType.EXPERIMENTAL_BIOACTIVITY: QUALIFIED},
    QuestionType.CLINICAL: {EvidenceType.CLINICAL_STUDY: "full"},
    QuestionType.THERAPEUTIC_EFFICACY: {EvidenceType.CLINICAL_STUDY: QUALIFIED},
    QuestionType.COMPOSITION: {EvidenceType.DIRECT_EXPERIMENTAL: "full", EvidenceType.TENTATIVE: QUALIFIED,
                               EvidenceType.SECONDARY_SOURCE: QUALIFIED},
    QuestionType.TRADITIONAL_USE: {EvidenceType.TRADITIONAL_USE: "full"},
    QuestionType.BIOLOGICAL_ACTIVITY: {EvidenceType.EXPERIMENTAL_BIOACTIVITY: "full", EvidenceType.CLINICAL_STUDY: "full",
                                       EvidenceType.COMPUTATIONAL: QUALIFIED},
    QuestionType.METHODOLOGY: {EvidenceType.DIRECT_EXPERIMENTAL: "full", EvidenceType.TENTATIVE: "full",
                               EvidenceType.COMPUTATIONAL: "full"},
    QuestionType.GENERAL: {t: QUALIFIED for t in EvidenceType},
}

MISSING_FOR: dict[QuestionType, str] = {
    QuestionType.THERAPEUTIC_EFFICACY: "experimental or clinical studies of therapeutic efficacy for the condition asked about",
    QuestionType.CLINICAL: "clinical studies in humans (trials, cohort or case data)",
    QuestionType.TOXICITY: "toxicology or safety studies",
    QuestionType.PHARMACOKINETICS: "pharmacokinetic studies (absorption, metabolism, excretion)",
    QuestionType.BIOLOGICAL_ACTIVITY: "experimental (in vitro or in vivo) bioactivity measurements",
    QuestionType.TRADITIONAL_USE: "documented traditional-use records",
    QuestionType.COMPOSITION: "direct experimental identification of constituents",
    QuestionType.METHODOLOGY: "records that describe the identification method",
    QuestionType.TAXONOMY: "an indexed plant matching the question",
    QuestionType.GENERAL: "records matching the question",
    QuestionType.CHEMICAL_PROPERTY: "a PubChem record for the compound",
    QuestionType.ABUNDANCE: "quantitative or comparative measurements of the compound's content in the plant",
    QuestionType.MECHANISM: "mechanistic experimental studies",
    QuestionType.PUBLICATION_LOOKUP: "indexed publications mentioning the subject",
}

TYPE_PHRASE = {
    EvidenceType.DIRECT_EXPERIMENTAL: "direct experimental identification of constituents",
    EvidenceType.TENTATIVE: "tentative identification of constituents",
    EvidenceType.SECONDARY_SOURCE: "secondary-source reports of constituents",
    EvidenceType.TRADITIONAL_USE: "traditional use",
    EvidenceType.COMPUTATIONAL: "computational (docking/in silico) predictions",
    EvidenceType.UNVERIFIED: "unverified claims",
    EvidenceType.EXPERIMENTAL_BIOACTIVITY: "experimental bioactivity measurements",
    EvidenceType.CLINICAL_STUDY: "clinical studies",
}


def support_level(qtype: QuestionType, record: EvidenceRecord) -> str | None:
    """'full', 'qualified', or None when the record cannot support an answer of this type."""
    if qtype == QuestionType.METHODOLOGY and not record.identification_method:
        return None
    if qtype == QuestionType.COMPOSITION and record.compound is None:
        return None
    return COMPATIBILITY.get(qtype, {}).get(EvidenceType(record.evidence_type))


# ---------- entities ----------

@dataclass
class Entities:
    plants: list[Plant] = field(default_factory=list)
    compounds: list[Compound] = field(default_factory=list)
    condition: str | None = None  # e.g. "cancer" in "does X cure cancer"


def plant_keys(p: Plant) -> set[str]:
    keys = {fold(p.accepted_name), fold(p.accepted_name.split()[0])}
    keys |= {fold(a.name) for a in p.aliases}
    return {k for k in keys if len(k) >= 4}


def compound_keys(c: Compound) -> set[str]:
    keys = {fold(c.name)} | {fold(str(s)) for s in (c.synonyms or []) if len(str(s)) <= 40}
    return {k for k in keys if len(k) >= 4}


def _mentions(text_folded: str, key: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(key)}(?!\w)", text_folded) is not None


_CONDITION = re.compile(r"\b(?:cure|cures|treat|treats|against|for|prevent|prevents|heal|heals)\s+"
                        r"([a-z][a-z\- ]{2,40}?)(?:\?|\.|,|$| in | with | and )")


def detect_entities(question: str, plants: list[Plant], compounds: list[Compound]) -> Entities:
    q = fold(question)
    found = Entities(
        plants=[p for p in plants if any(_mentions(q, k) for k in plant_keys(p))],
        compounds=[c for c in compounds if any(_mentions(q, k) for k in compound_keys(c))],
    )
    m = _CONDITION.search(normalize(question).lower())
    if m:
        found.condition = m.group(1).strip()
    return found


# ---------- contradictions ----------

_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def find_contradictions(records: list[EvidenceRecord]) -> list[dict]:
    """Same plant + compound reported with quantities differing more than 2x, or flagged by a curator."""
    groups: dict[tuple[int, int], list[EvidenceRecord]] = {}
    for r in records:
        if r.compound:
            groups.setdefault((r.plant_id, r.compound_id), []).append(r)
    out = []
    for recs in groups.values():
        if len({r.publication_id for r in recs}) < 2:
            continue
        values = {}
        for r in recs:
            m = _PERCENT.search(r.observation)
            if m:
                values[r.id] = float(m.group(1))
        flagged = any(r.curator_notes and "variation" in r.curator_notes.lower() for r in recs)
        spread = len(values) >= 2 and max(values.values()) > 2 * min(values.values())
        if spread or flagged:
            out.append({
                "plant": recs[0].plant.accepted_name,
                "compound": recs[0].compound.name,
                "evidence_ids": [r.id for r in recs],
                "findings": [{"evidence_id": r.id, "observation": r.observation,
                              "value_percent": values.get(r.id),
                              "geographic_origin": r.geographic_origin, "plant_part": r.plant_part,
                              "method": r.identification_method,
                              "publication": f"PMID {r.publication.pmid} ({r.publication.year})"} for r in recs],
                "explanation": "Reported quantities differ between publications. Documented context: "
                               + "; ".join(f"#{r.id}: origin {r.geographic_origin or 'not stated'}, "
                                           f"part {r.plant_part or 'not stated'}" for r in recs)
                               + ". Differences may reflect chemotype, origin or method; the sources do not "
                                 "establish which value is typical.",
            })
    return out


# ---------- claim validation ----------

EFFICACY_LANGUAGE = re.compile(
    r"\b(cures?|cured|treats?|treatment of|effective (against|for|in)|efficacy|prevents?|heals?|proven|"
    r"clinically|therapeutic benefit|beneficial for|protects? against|safe (to|for))\b", re.I)
PREDICTION_QUALIFIER = re.compile(r"\b(predict\w*|docking|in silico|computational\w*|simulat\w*|model(l)?ed)\b", re.I)
TRADITION_QUALIFIER = re.compile(r"\b(tradition\w*|folk|ethno\w*|customar\w*|used as)\b", re.I)
TENTATIVE_QUALIFIER = re.compile(r"\b(tentativ\w*|assigned|putativ\w*|reported|detected|profil\w*|suggest\w*|"
                                 r"may|possibly|described)\b", re.I)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def validate_claim(claim: dict, records_by_id: dict[int, EvidenceRecord], existing_ids: set[int],
                   qtype: QuestionType) -> str | None:
    """Return None if the claim is acceptable, otherwise the reason it is rejected."""
    statement = str(claim.get("statement", "")).strip()
    if not statement:
        return "empty statement"
    try:
        ids = [int(i) for i in claim.get("evidence_ids") or []]
    except (TypeError, ValueError):
        return "evidence IDs are not integers"
    if not ids:
        return "no evidence IDs cited"
    unknown = [i for i in ids if i not in existing_ids]
    if unknown:
        return f"cites evidence IDs that do not exist: {unknown}"
    outside = [i for i in ids if i not in records_by_id]
    if outside:
        return f"cites records that were not part of the retrieved evidence: {outside}"
    cited = [records_by_id[i] for i in ids]
    irrelevant = [r.id for r in cited if support_level(qtype, r) is None]
    if irrelevant:
        return f"cited records {irrelevant} are of a category that cannot support a {qtype.value} answer"
    s_fold = fold(statement)
    for r in cited:
        keys = plant_keys(r.plant) | (compound_keys(r.compound) if r.compound else set())
        if not any(_mentions(s_fold, k) for k in keys):
            return f"claim does not mention the plant or compound of cited record #{r.id}"
    if EFFICACY_LANGUAGE.search(statement):
        return "claim asserts therapeutic efficacy or safety, which none of the cited evidence categories can establish"
    types = {EvidenceType(r.evidence_type) for r in cited}
    if EvidenceType.COMPUTATIONAL in types and not PREDICTION_QUALIFIER.search(statement):
        return "computational evidence is stated as fact; the claim must say it is a prediction"
    if EvidenceType.TRADITIONAL_USE in types and not TRADITION_QUALIFIER.search(statement):
        return "traditional-use evidence must be described as traditional use"
    if types & {EvidenceType.TENTATIVE, EvidenceType.SECONDARY_SOURCE} and not types & {EvidenceType.DIRECT_EXPERIMENTAL} \
            and not TENTATIVE_QUALIFIER.search(statement):
        return "tentative or secondary evidence is stated as established; the claim must be qualified"
    source_text = normalize(" ".join(f"{r.observation} {r.original_excerpt}" for r in cited))
    stray = [n for n in _NUMBER.findall(statement) if n not in source_text and n not in "".join(map(str, ids))]
    if stray:
        return f"claim contains numbers not found in the cited records: {stray}"
    return None
