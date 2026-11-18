"""Atomic claim extraction, claim categories and the claim-evidence compatibility matrix.

All deterministic. The matrix says, for every claim category, which evidence
categories can support it and how far ('supported' or at most 'partially
supported'), plus extra conditions on the source excerpt (e.g. an abundance
claim needs a quantitative or comparative statement about that compound).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.models import EvidenceType
from app.textnorm import normalize


class ClaimCategory(StrEnum):
    CHEMICAL_IDENTIFICATION = "chemical_identification"
    CHEMICAL_OCCURRENCE = "chemical_occurrence"
    CHEMICAL_ABUNDANCE = "chemical_abundance"
    CHEMICAL_PROPERTY = "chemical_property"
    BIOLOGICAL_ACTIVITY = "biological_activity"
    MECHANISM_OF_ACTION = "mechanism_of_action"
    THERAPEUTIC_EFFICACY = "therapeutic_efficacy"
    CLINICAL_EFFICACY = "clinical_efficacy"
    TOXICITY = "toxicity"
    SAFETY = "safety"
    PHARMACOKINETICS = "pharmacokinetics"
    TAXONOMIC_IDENTITY = "taxonomic_identity"
    TRADITIONAL_USE = "traditional_use"
    COMPUTATIONAL_PREDICTION = "computational_prediction"
    EXPERIMENTAL_OBSERVATION = "experimental_observation"


class Verdict(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTRADICTED = "contradicted"
    NOT_SUPPORTED = "not_supported"
    INSUFFICIENT = "insufficient_information"


# strength order used to combine stages: the final verdict is the weakest stage verdict
STRENGTH = {Verdict.SUPPORTED: 4, Verdict.PARTIALLY_SUPPORTED: 3, Verdict.INSUFFICIENT: 2,
            Verdict.NOT_SUPPORTED: 1, Verdict.CONTRADICTED: 0}


def weakest(*verdicts: Verdict) -> Verdict:
    return min(verdicts, key=lambda v: STRENGTH[v])


# ---------- claim classification ----------
# Ordered; the first match wins, so stronger/more specific categories are checked first.
_CLAIM_RULES: list[tuple[ClaimCategory, re.Pattern]] = [
    (ClaimCategory.COMPUTATIONAL_PREDICTION, re.compile(
        r"\b(predict\w*|docking|in silico|computational\w*|simulat\w*|virtual screen\w*|modell?ed)\b")),
    (ClaimCategory.CLINICAL_EFFICACY, re.compile(
        r"\b(clinical\w*|in humans|human (subjects|patients|trials?)|patients?|randomi[sz]ed|placebo)\b")),
    (ClaimCategory.THERAPEUTIC_EFFICACY, re.compile(
        r"\b(cures?|cured|curing|treats?|treated|treating|treatments?|therap(y|ies|eutic)|effective\w*|"
        r"efficacy|efficacious|heals?|prevents?|remed(y|ies)|alleviat\w*|beneficial|works? (for|against)|"
        r"helps? (with|against))\b")),
    (ClaimCategory.TOXICITY, re.compile(r"\b(toxic\w*|poison\w*|lethal|ld50|hepatotox\w*|nephrotox\w*)\b")),
    (ClaimCategory.SAFETY, re.compile(r"\b(safe|safety|side[- ]effects?|adverse|well[- ]tolerated|contraindicat\w*)\b")),
    (ClaimCategory.PHARMACOKINETICS, re.compile(
        r"\b(pharmacokinetic\w*|absorb\w*|absorption|bioavailab\w*|metaboli[sz]ed|half[- ]life|excret\w*|plasma)\b")),
    (ClaimCategory.MECHANISM_OF_ACTION, re.compile(
        r"\b(mechanism\w*|by (inhibiting|activating|blocking|binding)|pathway\w*|signal\w*|binds?|binding to|"
        r"targets?|receptor\w*|modulat\w*|downregulat\w*|upregulat\w*)\b")),
    (ClaimCategory.CHEMICAL_PROPERTY, re.compile(
        r"\b(molecular (formula|weight|mass)|formula|smiles|inchi\w*|pubchem cid|cid|molar mass|iupac)\b")),
    (ClaimCategory.CHEMICAL_ABUNDANCE, re.compile(
        r"(\bmost abundant\b|\babundan\w*|\bpredominant\w*|\bdominant\b|\bdominated\b|\bmajor (constituent|compound|component)|"
        r"\bmain (constituent|compound|component)|\bprincipal\b|\bchief\b|\bhighest (content|concentration|level)|"
        r"\bcontent\b|\bconcentration\b|\bquantit\w*|\d+(\.\d+)?\s*(%|mg|µg|ug|g/)|\brich (in|source)|\bexcellent source)",
        re.I)),
    (ClaimCategory.BIOLOGICAL_ACTIVITY, re.compile(
        r"\b(antioxidant\w*|anti[- ]?inflammator\w*|antimicrob\w*|antibacter\w*|antifung\w*|antivir\w*|"
        r"cytotox\w*|anticancer|antitumou?r|inhibit\w*|activit(y|ies)|active against|bioactiv\w*|potent|"
        r"neuroprotect\w*|hepatoprotect\w*|ic50)\b")),
    (ClaimCategory.TRADITIONAL_USE, re.compile(r"\b(tradition\w*|folk|ethno\w*|customar\w*|used as a)\b")),
    (ClaimCategory.TAXONOMIC_IDENTITY, re.compile(
        r"\b(famil(y|ies)|belongs? to|genus|synonym\w*|accepted name|taxonom\w*|also known as|is a species)\b")),
    (ClaimCategory.CHEMICAL_IDENTIFICATION, re.compile(
        r"\b(identified|isolated|characteri[sz]ed|structure (was|were) (determined|elucidated)|purified|"
        r"assigned|detected)\b")),
    (ClaimCategory.CHEMICAL_OCCURRENCE, re.compile(
        r"\b(present in|occurs?|occurring|found in|reported (in|from)|contains?|constituent|component of|"
        r"from the|in the (plant|root|leaf|seed|fruit|oil|resin))\b")),
]


def classify_claim(text: str) -> ClaimCategory:
    t = normalize(text).lower()
    for cat, pattern in _CLAIM_RULES:
        if pattern.search(t):
            return cat
    return ClaimCategory.EXPERIMENTAL_OBSERVATION


# ---------- atomic claim extraction ----------
_VERB = (r"(?:is|are|was|were|has|have|had|shows?|showed|exhibits?|exhibited|contains?|contained|occurs?|"
         r"inhibits?|inhibited|binds?|bound|treats?|cures?|prevents?|reduces?|reduced|induces?|increases?|"
         r"acts?|acted|displays?|demonstrates?|demonstrated|possess(?:es)?|may|can|could|will)")
_CLAUSE_SPLIT = re.compile(rf"(?:,\s*(?:and|but|while)\s+|;\s*|,\s+(?={_VERB}\b)|\s+and\s+(?={_VERB}\b)|"
                           rf"\s+but\s+(?={_VERB}\b))", re.I)
_FIRST_VERB = re.compile(rf"\b{_VERB}\b", re.I)


_CAUSAL_SPLIT = re.compile(r"\s*(?:,\s*)?\b(?:because|since|therefore|thus|hence|so that|which means that|"
                           r"which shows that|indicating that|suggesting that|proving that)\b\s*", re.I)
_PRONOUN = re.compile(r"^(it|they|this|these|this compound|this plant)\b", re.I)


def extract_atomic_claims(text: str) -> list[str]:
    """Split prose into single factual statements. Clauses that lost their subject get the
    sentence subject back: 'X is present in Y, has A, and is B' -> 'X is present in Y',
    'X has A', 'X is B'."""
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", normalize(text)):
        sentence = sentence.strip().rstrip(".!?").strip()
        if not sentence:
            continue
        m = _FIRST_VERB.search(sentence)
        subject = sentence[:m.start()].strip(" ,") if m else ""
        # causal/inferential connectives join two separate claims ("X is effective because it is used as...")
        parts = [p.strip(" ,") for chunk in _CAUSAL_SPLIT.split(sentence)
                 for p in _CLAUSE_SPLIT.split(chunk) if p and p.strip(" ,")]
        if subject:
            parts = [_PRONOUN.sub(subject, p, count=1) for p in parts]
        for i, part in enumerate(parts):
            starts_with_verb = re.match(rf"{_VERB}\b", part, re.I)
            if i > 0 and starts_with_verb and subject:
                part = f"{subject} {part}"
            if len(part.split()) >= 3:
                out.append(part[0].upper() + part[1:] + ".")
    return out


# ---------- compatibility matrix ----------

@dataclass(frozen=True)
class Requirement:
    """Evidence categories that can support a claim category, with the maximum verdict each allows."""
    evidence: dict[EvidenceType, Verdict]
    needs_compound_in_excerpt: bool = True
    excerpt_cue: str | None = None  # regex the excerpt must match near the compound
    note: str = ""
    record_free: bool = False  # answered from structured records (PubChem / GBIF), not evidence records


S, P = Verdict.SUPPORTED, Verdict.PARTIALLY_SUPPORTED
ABUNDANCE_CUE = (r"(\d+(\.\d+)?\s*(%|mg|µg|ug)|most abundant|abundan|predominant|dominant|dominated|major|main|"
                 r"principal|chief|highest|rich in|richest|excellent source|quantif)")
ACTIVITY_CUE = r"(inhibit|activit|antioxidant|anti-?inflammator|antimicrob|antibacter|antifung|cytotox|ic50|active)"

COMPATIBILITY_MATRIX: dict[ClaimCategory, Requirement] = {
    ClaimCategory.CHEMICAL_IDENTIFICATION: Requirement(
        {EvidenceType.DIRECT_EXPERIMENTAL: S, EvidenceType.TENTATIVE: P, EvidenceType.SECONDARY_SOURCE: P},
        note="Identification needs a source that names the compound in this plant; tentative and "
             "secondary reports support it only partially."),
    ClaimCategory.CHEMICAL_OCCURRENCE: Requirement(
        {EvidenceType.DIRECT_EXPERIMENTAL: S, EvidenceType.TENTATIVE: P, EvidenceType.SECONDARY_SOURCE: P},
        note="Occurrence is established by identification evidence for the same plant."),
    ClaimCategory.CHEMICAL_ABUNDANCE: Requirement(
        {EvidenceType.DIRECT_EXPERIMENTAL: S, EvidenceType.TENTATIVE: P, EvidenceType.SECONDARY_SOURCE: P},
        excerpt_cue=ABUNDANCE_CUE,
        note="Abundance needs quantitative or comparative data about that compound; a list of identified "
             "compounds does not establish abundance."),
    ClaimCategory.CHEMICAL_PROPERTY: Requirement(
        {}, record_free=True,
        note="Molecular properties come from the compound's PubChem record, not from plant evidence."),
    ClaimCategory.BIOLOGICAL_ACTIVITY: Requirement(
        {EvidenceType.EXPERIMENTAL_BIOACTIVITY: S, EvidenceType.CLINICAL_STUDY: S}, excerpt_cue=ACTIVITY_CUE,
        note="Activity needs an experimental measurement. Identification, traditional use and computational "
             "predictions do not establish activity."),
    ClaimCategory.MECHANISM_OF_ACTION: Requirement(
        {EvidenceType.EXPERIMENTAL_BIOACTIVITY: P},
        note="Mechanism claims need mechanistic experiments; a single activity measurement supports them only partially."),
    ClaimCategory.THERAPEUTIC_EFFICACY: Requirement(
        {EvidenceType.CLINICAL_STUDY: P},
        note="Therapeutic efficacy needs controlled clinical evidence. In-vitro and animal findings do not "
             "establish it; a single clinical study supports it only partially."),
    ClaimCategory.CLINICAL_EFFICACY: Requirement(
        {EvidenceType.CLINICAL_STUDY: S},
        note="Clinical efficacy in humans needs human clinical studies."),
    ClaimCategory.TOXICITY: Requirement(
        {}, note="No toxicology evidence category exists in MOLECULAB yet; toxicity claims cannot be supported."),
    ClaimCategory.SAFETY: Requirement(
        {}, note="No safety evidence category exists in MOLECULAB yet; safety claims cannot be supported."),
    ClaimCategory.PHARMACOKINETICS: Requirement(
        {}, note="No pharmacokinetic evidence category exists in MOLECULAB yet."),
    ClaimCategory.TAXONOMIC_IDENTITY: Requirement(
        {}, record_free=True, note="Taxonomic identity comes from the GBIF-resolved plant record."),
    ClaimCategory.TRADITIONAL_USE: Requirement(
        {EvidenceType.TRADITIONAL_USE: S}, needs_compound_in_excerpt=False,
        note="Traditional-use records support only the statement that the plant is traditionally used, "
             "never that it is effective."),
    ClaimCategory.COMPUTATIONAL_PREDICTION: Requirement(
        {EvidenceType.COMPUTATIONAL: S},
        note="A computational record supports only a claim worded as a prediction."),
    ClaimCategory.EXPERIMENTAL_OBSERVATION: Requirement(
        {EvidenceType.DIRECT_EXPERIMENTAL: P, EvidenceType.EXPERIMENTAL_BIOACTIVITY: P},
        note="Unclassified observations are at most partially supported and need expert reading."),
}


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class CompatibilityResult:
    ceiling: Verdict
    checks: list[CheckResult] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def matrix_table() -> list[dict]:
    """The compatibility matrix as plain rows (served by the API and printed in the report)."""
    return [{"claim_category": c.value,
             "supported_by": {e.value: v.value for e, v in r.evidence.items()},
             "requires_compound_in_excerpt": r.needs_compound_in_excerpt and not r.record_free,
             "excerpt_condition": r.excerpt_cue, "answered_from_structured_records": r.record_free,
             "rule": r.note} for c, r in COMPATIBILITY_MATRIX.items()]
