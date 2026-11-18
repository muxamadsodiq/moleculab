"""Evidence-grounded research assistant.

Pipeline:
1. classify the question (deterministic rules, app.reasoning);
2. detect the plants/compounds it names and retrieve their records;
3. evaluate answerability: which retrieved records can support THIS kind of question;
4. abstain when nothing compatible exists (no generation happens);
5. otherwise generate claims (LLM if configured, else extractive) from compatible records only;
6. validate every claim against its cited records; reject what is not supported;
7. show conflicting findings side by side.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import LLMError, LLMProvider
from app.models import Compound, EvidenceRecord, EvidenceType, Plant
from app.textnorm import fold, normalize
from app.claims import ABUNDANCE_CUE, Verdict, extract_atomic_claims
from app.config import settings
from app.reasoning import compound_keys, plant_keys
from app.verification import DeterministicSpanVerifier, LLMEntailmentVerifier, summarize, verify_claim
from app.reasoning import (MISSING_FOR, QUALIFIED, QUESTION_LABEL, TYPE_PHRASE, AnswerStatus, Entities, QuestionType,
                           classify_question, detect_entities, find_contradictions, support_level, validate_claim)
from app.services import effective_review_status, evidence_dict, list_evidence

BASE_LIMITATIONS = [
    "Answers only cover records indexed in MOLECULAB; the absence of a record is not evidence that no research exists.",
    "Records were curated from abstracts with AI assistance; none has been confirmed by a domain expert yet.",
]

SYSTEM_PROMPT = """You write answers for qualified researchers using ONLY the evidence records provided.

Rules:
- Every claim cites one or more record IDs from the list. Never cite anything else; never invent references.
- Only make claims of the kind the question type allows; records were pre-filtered for that.
- Keep each record's evidence type in the wording: tentative/secondary findings are "reported" or "tentatively identified"; computational results are "predicted"; traditional use is "traditionally used".
- Never state or imply that a plant or compound cures, treats, prevents or is safe for anything.
- Each claim must name the plant or compound it is about, and copy numbers exactly from the records.

Reply with JSON only:
{"claims": [{"statement": "...", "evidence_ids": [1, 2]}]}"""


def _all_entities(s: Session) -> tuple[list[Plant], list[Compound]]:
    return list(s.scalars(select(Plant))), list(s.scalars(select(Compound)))


def retrieve(s: Session, question: str, entities: Entities, limit: int = 40) -> list[EvidenceRecord]:
    """Records of the named entities (plant AND compound when both are named). Without named
    entities, fall back to whole-word matching in observations and excerpts."""
    found: dict[int, EvidenceRecord] = {}
    plant_ids = {p.id for p in entities.plants}
    compound_ids = {c.id for c in entities.compounds}
    if plant_ids or compound_ids:
        for e in s.scalars(select(EvidenceRecord)):
            if plant_ids and compound_ids:
                ok = e.plant_id in plant_ids and (e.compound_id in compound_ids or e.compound_id is None)
            else:
                ok = e.plant_id in plant_ids or e.compound_id in compound_ids
            if ok:
                found[e.id] = e
    else:
        stop = {"what", "which", "does", "with", "from", "that", "this", "there", "about", "have", "were"}
        for term in {t for t in re.findall(r"[a-z][a-z\-]{3,}", question.lower()) if t not in stop}:
            word = re.compile(rf"\b{re.escape(term)}(?:s|es)?\b", re.I)
            for e in list_evidence(s, q=term, limit=limit):
                if word.search(e.observation) or word.search(e.original_excerpt):
                    found[e.id] = e
    return sorted(found.values(), key=lambda e: e.id)[:limit]


def _describe(records: list[EvidenceRecord]) -> str:
    counts = Counter(EvidenceType(r.evidence_type) for r in records)
    parts = [f"{n} record{'s' if n > 1 else ''} of {TYPE_PHRASE[t]}" for t, n in counts.most_common()]
    return ", ".join(parts)


def evaluate(s: Session, question: str) -> tuple[dict, list[EvidenceRecord], list[EvidenceRecord]]:
    """Deterministic answerability stage. Returns (answerability, retrieved, compatible)."""
    qtype = classify_question(question)
    plants, compounds = _all_entities(s)
    entities = detect_entities(question, plants, compounds)
    retrieved = retrieve(s, question, entities)
    compatible = [r for r in retrieved if support_level(qtype, r)]
    full = [r for r in compatible if support_level(qtype, r) == "full"]
    contradictions = find_contradictions(compatible)
    subject = ", ".join([p.accepted_name for p in entities.plants] + [c.name for c in entities.compounds]) \
        or "the terms in this question"

    limitations = list(BASE_LIMITATIONS)
    missing: list[str] = []
    if qtype == QuestionType.CHEMICAL_PROPERTY:
        status = AnswerStatus.ANSWERABLE if entities.compounds else AnswerStatus.NOT_ANSWERABLE
        reason = (f"Chemical properties of {subject} are answered from the compound's PubChem record, not from plant "
                  "evidence." if entities.compounds else "No indexed compound was recognised in the question.")
        if not entities.compounds:
            missing.append(MISSING_FOR[qtype])
        compatible = []
    elif qtype == QuestionType.TAXONOMY:
        status = AnswerStatus.ANSWERABLE if entities.plants else AnswerStatus.NOT_ANSWERABLE
        reason = (f"Taxonomic identity of {subject} is answered from GBIF-resolved plant records, not from evidence records."
                  if entities.plants else "No indexed plant was recognised in the question.")
        if not entities.plants:
            missing.append(MISSING_FOR[qtype])
    elif not retrieved:
        status = AnswerStatus.NOT_ANSWERABLE
        reason = "No indexed plant or compound was recognised and no evidence text matched; the question is outside the indexed corpus."
        missing.append(MISSING_FOR[qtype])
    elif not compatible:
        status = AnswerStatus.NOT_ANSWERABLE
        what = f"against {entities.condition}" if entities.condition and qtype in (
            QuestionType.THERAPEUTIC_EFFICACY, QuestionType.CLINICAL) else ""
        reason = (f"The indexed records for {subject} contain {_describe(retrieved)}. None of these evidence categories "
                  f"can establish {QUESTION_LABEL[qtype]}{(' ' + what) if what else ''}.")
        missing.append(MISSING_FOR[qtype])
    elif qtype == QuestionType.ABUNDANCE and not [r for r in compatible if _has_quantity(r)]:
        status = AnswerStatus.NOT_ANSWERABLE
        reason = (f"The indexed records for {subject} identify the constituents ({_describe(compatible)}) but none "
                  "reports a quantity or comparison for the compound asked about. A list of identified compounds "
                  "does not establish abundance.")
        missing.append(MISSING_FOR[qtype])
    elif not full:
        status = AnswerStatus.PARTIAL
        reason = (f"Only weaker evidence ({_describe(compatible)}) addresses this question; "
                  f"any answer must be qualified.")
        missing.append(MISSING_FOR[qtype])
    else:
        status = AnswerStatus.ANSWERABLE
        reason = f"{len(full)} record(s) of a category that directly addresses this question ({_describe(full)})."

    if qtype == QuestionType.BIOLOGICAL_ACTIVITY and compatible:
        limitations.append("Computational predictions are not experimental results; a docking score does not establish binding or efficacy.")
    if any(EvidenceType(r.evidence_type) == EvidenceType.SECONDARY_SOURCE for r in compatible):
        limitations.append("Some supporting records are secondary-source statements, not the publication's own findings.")
    if any(EvidenceType(r.evidence_type) == EvidenceType.TRADITIONAL_USE for r in compatible):
        limitations.append("Traditional use is not proof of efficacy.")
    if compatible and all(effective_review_status(r) == "pending_expert_review" for r in compatible):
        limitations.append("All supporting records are pending expert review.")
    if contradictions:
        limitations.append("Indexed publications report conflicting quantities; both findings are shown.")
    if qtype == QuestionType.COMPOSITION:
        limitations.append("A compound occurring in a plant is not proof of any therapeutic benefit.")

    answerability = {
        "answerable": status != AnswerStatus.NOT_ANSWERABLE,
        "status": status, "reason": reason, "question_type": qtype,
        "evidence_ids": [r.id for r in compatible],
        "retrieved_evidence_ids": [r.id for r in retrieved],
        "limitations": limitations, "missing_evidence": missing, "contradictions": contradictions,
        "entities": {"plants": [p.accepted_name for p in entities.plants],
                     "compounds": [c.name for c in entities.compounds], "condition": entities.condition},
    }
    return answerability, retrieved, compatible


def _has_quantity(r: EvidenceRecord) -> bool:
    """True when the excerpt gives a quantity/comparison near the record's compound."""
    if not r.compound:
        return False
    from app.verification import _windows
    return any(re.search(ABUNDANCE_CUE, w, re.I) for w in _windows(r.original_excerpt, compound_keys(r.compound)))


def _extractive_claims(qtype: QuestionType, records: list[EvidenceRecord]) -> list[dict]:
    claims = []
    for e in records:
        t = EvidenceType(e.evidence_type)
        subject = e.plant.accepted_name + (f" ({e.plant_part})" if e.plant_part else "")
        lead = {EvidenceType.TRADITIONAL_USE: "Traditional use reported for",
                EvidenceType.COMPUTATIONAL: "Computational prediction for",
                EvidenceType.TENTATIVE: "Tentatively reported for",
                EvidenceType.SECONDARY_SOURCE: "Secondary-source report for"}.get(t, "Reported for")
        detail = e.observation
        if qtype == QuestionType.METHODOLOGY:
            detail = f"{e.compound.name if e.compound else 'constituents'} identified using {e.identification_method}"
        claims.append({"statement": f"{lead} {subject}: {detail}", "evidence_ids": [e.id]})
    return claims


PROPERTY_FIELDS = [  # (question pattern, attribute, label)
    (r"formula", "molecular_formula", "molecular formula"),
    (r"weight|mass", "molecular_weight", "molecular weight (g/mol)"),
    (r"smiles|structure", "smiles", "SMILES"),
    (r"inchi", "inchikey", "InChIKey"),
    (r"\bcid\b|pubchem", "pubchem_cid", "PubChem CID"),
    (r"iupac", "iupac_name", "IUPAC name"),
]


def _verifiers(provider):
    vs = [DeterministicSpanVerifier()]
    if provider is not None and settings.semantic_verifier == "llm":
        vs.append(LLMEntailmentVerifier(provider))
    return vs


def _ctx(s: Session):
    records = {r.id: r for r in s.scalars(select(EvidenceRecord))}
    plants, compounds = _all_entities(s)
    return records, plants, compounds


def _hypothesis(question: str, plants: list[Plant], compounds: list[Compound]) -> str | None:
    """Turn a yes/no question into the claim it asks about: 'Is X the most abundant...?' -> 'X is the most
    abundant...'."""
    q = normalize(question).rstrip("?").strip()
    m = re.match(r"^(is|are|was|were|does|do|did|has|have|can)\s+(.*)$", q, re.I)
    if not m:
        return None
    aux, body = m.group(1).lower(), m.group(2)
    body_f = fold(body)
    ends = []
    for p in plants:
        ends += [mm.end() for k in plant_keys(p) for mm in re.finditer(rf"(?<!\w){re.escape(k)}(?!\w)", body_f)]
    for c in compounds:
        ends += [mm.end() for k in compound_keys(c) for mm in re.finditer(rf"(?<!\w){re.escape(k)}(?!\w)", body_f)]
    if not ends:
        return None
    cut = min(ends)
    if aux in ("is", "are", "was", "were", "has", "have", "can"):
        claim = f"{body[:cut]} {aux} {body[cut:].strip()}"
    else:
        claim = body
    return claim[0].upper() + claim[1:] + "."


def _verify_drafts(drafts, allowed, records, plants, compounds, verifiers) -> list[dict]:
    """Stages 4-8: atomic extraction, evidence association (inherited citations), verification."""
    out = []
    for d in drafts:
        statement = str(d.get("statement", "")) if isinstance(d, dict) else str(d)
        ids = d.get("evidence_ids") if isinstance(d, dict) else []
        atoms = extract_atomic_claims(statement) or ([statement] if statement.strip() else [])
        for atom in atoms:
            v = verify_claim(atom, ids, records=records, existing_ids=set(records), allowed_ids=allowed,
                             plants=plants, compounds=compounds, verifiers=verifiers)
            v["draft"] = statement if len(atoms) > 1 else None
            out.append(v)
    return out


ACCEPTED = (Verdict.SUPPORTED, Verdict.PARTIALLY_SUPPORTED)


def _as_claim(v: dict) -> dict:
    return {**v, "support": "full" if v["status"] == Verdict.SUPPORTED else QUALIFIED}


def _as_rejected(v: dict) -> dict:
    return {**v, "reason": f"{Verdict(v['status']).value}: {v['explanation']}"}


def _prose(claims: list[dict], contradictions: list[dict]) -> str:
    text = " ".join(
        ("Partially supported: " if c["status"] == Verdict.PARTIALLY_SUPPORTED else "")
        + f"{c['statement'].rstrip('.')} [{', '.join('#' + str(i) for i in c['evidence_ids'])}]." for c in claims)
    for c in contradictions:
        text += (f" Note: reports on {c['compound']} in {c['plant']} conflict — "
                 + "; ".join(f"#{f['evidence_id']}: {f['observation']}" for f in c["findings"]) + ".")
    return text.strip()


def _property_answer(s: Session, question: str, compound_names: list[str], live_lookup: bool) -> dict:
    records, plants, compounds = _ctx(s)
    wanted = [(attr, label) for pat, attr, label in PROPERTY_FIELDS if re.search(pat, question, re.I)] or \
        [(attr, label) for _, attr, label in PROPERTY_FIELDS]
    claims, rejected, properties, missing = [], [], [], []
    for c in (x for x in compounds if x.name in compound_names):
        values, source = {a: getattr(c, a) for a, _ in wanted}, {"type": "pubchem_compound_record",
                                                                  "url": c.identifier_source,
                                                                  "retrieved_at": c.identifier_retrieved_at,
                                                                  "stored": True}
        if live_lookup and any(v is None for v in values.values()):
            from app.clients.pubchem import PubChemClient
            res = PubChemClient().search_name(c.name)
            if res.ok and res.data:
                live = res.data[0]
                values = {a: values[a] if values[a] is not None else live.get(a) for a in values}
                source = {"type": "pubchem_live_lookup", **res.provenance(), "stored": False}
            else:
                missing.append(f"PubChem has no record for '{c.name}' ({res.error}).")
        for attr, label in wanted:
            val = values.get(attr)
            properties.append({"compound": c.name, "property": label, "value": val, "source": source})
            if val is None:
                missing.append(f"{label} of {c.name} is not available.")
                continue
            claim = f"The {label} of {c.name} is {val}."
            v = verify_claim(claim, [], records=records, existing_ids=set(records), allowed_ids=set(),
                             plants=plants, compounds=compounds)
            if v["status"] != Verdict.SUPPORTED and not source.get("stored", True):
                v = {**v, "status": Verdict.SUPPORTED, "explanation": "Value taken from a live PubChem lookup.",
                     "structured_sources": [source]}
            (claims if v["status"] in ACCEPTED else rejected).append(v)
    answer = " ".join(c["statement"] for c in claims)
    if answer:
        answer += (" These values identify the molecule (source: PubChem). They do not establish that it occurs in "
                   "any plant or has any biological effect.")
    else:
        answer = "No verified chemical property is available for this compound. " + " ".join(missing)
    return {"claims": [_as_claim(c) for c in claims], "rejected_claims": [_as_rejected(r) for r in rejected],
            "claim_verifications": claims + rejected, "properties": properties, "answer": answer,
            "missing": missing}


def answer(s: Session, question: str, provider: LLMProvider | None, live_lookup: bool = False) -> dict:
    question = question.strip()
    ab, retrieved, compatible = evaluate(s, question)
    qtype = ab["question_type"]
    records, plants, compounds = _ctx(s)
    verifiers = _verifiers(provider)
    refs = {r.id: evidence_dict(r) for r in retrieved}
    base = {"question": question, "question_type": qtype, "answerability": ab, "limitations": ab["limitations"],
            "missing_evidence": ab["missing_evidence"], "contradictions": ab["contradictions"],
            "answerable": ab["answerable"], "retrieved_evidence_ids": ab["retrieved_evidence_ids"],
            "llm_used": False, "rejected_claims": [], "claim_verifications": [], "hypothesis": None,
            "established_findings": [], "properties": [], "publications": [],
            "verifiers": [v.name for v in verifiers]}

    def finish(out: dict) -> dict:
        # stage 10: re-validate every citation in the final answer before returning it
        allowed = set(records)
        for c in out.get("claims", []):
            assert all(i in allowed for i in c["evidence_ids"]), "final citation check failed"
        out["verification_summary"] = summarize(out.get("claim_verifications", []))
        out["supporting_evidence"] = out.get("references", [])
        return out

    if qtype == QuestionType.CHEMICAL_PROPERTY and ab["answerable"]:
        pa = _property_answer(s, question, ab["entities"]["compounds"], live_lookup)
        return finish({**base, **{k: pa[k] for k in ("claims", "rejected_claims", "claim_verifications",
                                                     "properties", "answer")},
                       "missing_evidence": ab["missing_evidence"] + pa["missing"], "mode": "structured_record",
                       "references": [], "message": "Answered from the compound's PubChem record."})
    if qtype == QuestionType.TAXONOMY and ab["answerable"]:
        p = next(x for x in plants if x.accepted_name in ab["entities"]["plants"])
        v = verify_claim(f"{p.accepted_name} belongs to the family {p.family}.", [], records=records,
                         existing_ids=set(records), allowed_ids=set(), plants=plants, compounds=compounds)
        names = "; ".join(f"{a.name} ({a.language}, uncited)" for a in p.aliases) or "none"
        return finish({**base, "mode": "structured_record", "claims": [_as_claim(v)] if v["status"] in ACCEPTED else [],
                       "claim_verifications": [v], "references": [],
                       "answer": f"{p.accepted_name} {p.authority or ''} belongs to the family {p.family} "
                                 f"(GBIF key {p.gbif_key}). Other recorded names: {names}.",
                       "message": "Answered from the GBIF-resolved plant record; vernacular names are uncited."})

    hypothesis_text = _hypothesis(question, plants, compounds)
    if not ab["answerable"]:
        mode = "no_evidence" if not retrieved else "abstained"
        subject = ", ".join(ab["entities"]["plants"] + ab["entities"]["compounds"]) or "this question"
        text = ab["reason"] + (f" MOLECULAB therefore cannot draw the requested conclusion about {subject} "
                               "from the available evidence." if retrieved else "")
        if ab["missing_evidence"]:
            text += " Evidence that would be needed: " + "; ".join(ab["missing_evidence"]) + "."
        established = [v for v in _verify_drafts(_extractive_claims(QuestionType.COMPOSITION, retrieved),
                                                 {r.id for r in retrieved}, records, plants, compounds,
                                                 [DeterministicSpanVerifier()]) if v["status"] in ACCEPTED]
        hyp = None
        if hypothesis_text and retrieved:
            hyp = verify_claim(hypothesis_text, [r.id for r in retrieved], records=records, existing_ids=set(records),
                               allowed_ids={r.id for r in retrieved}, plants=plants, compounds=compounds,
                               verifiers=[DeterministicSpanVerifier()])
            if hyp["status"] in ACCEPTED:  # never let a structural accident turn an abstention into a yes
                hyp = {**hyp, "status": Verdict.INSUFFICIENT,
                       "explanation": "Not assessed: the question type cannot be answered from the indexed evidence."}
        if established:
            text += " What the indexed evidence does establish: " + " ".join(
                f"{c['statement'].rstrip('.')} [{', '.join('#' + str(i) for i in c['evidence_ids'])}]." for c in established[:6])
        return finish({**base, "mode": mode, "claims": [], "references": list(refs.values()), "answer": text,
                       "hypothesis": hyp, "established_findings": [_as_claim(c) for c in established],
                       "claim_verifications": ([hyp] if hyp else []),
                       "message": "Abstained: the indexed evidence does not support an answer to this question."})

    allowed = {r.id for r in compatible}
    drafts, llm_info, message = None, {}, ""
    if provider is not None:  # stage 3: draft answer
        try:
            context = json.dumps([{"id": r.id, "plant": r.plant.accepted_name,
                                   "compound": r.compound.name if r.compound else None,
                                   "evidence_type": r.evidence_type, "plant_part": r.plant_part,
                                   "origin": r.geographic_origin, "observation": r.observation,
                                   "method": r.identification_method, "excerpt": r.original_excerpt}
                                  for r in compatible], ensure_ascii=False, indent=1)
            raw = provider.complete(SYSTEM_PROMPT, f"Question type: {qtype.value}\nQuestion: {question}\n\n"
                                                   f"Evidence records:\n{context}")
            m = re.search(r"\{.*\}", raw, re.S)
            drafts = json.loads(m.group(0))["claims"] if m else None
            if not isinstance(drafts, list):
                drafts, message = None, "LLM output was not valid JSON; showing extractive claims instead. "
            else:
                llm_info = {"llm_used": True, "llm_provider": provider.name, "llm_model": provider.model}
        except (LLMError, ValueError, KeyError, TypeError) as exc:
            drafts, message = None, f"LLM generation failed ({exc}); showing extractive claims instead. "
    if drafts is None:
        drafts = _extractive_claims(qtype, compatible)

    verifications = _verify_drafts(drafts, allowed, records, plants, compounds, verifiers)  # stages 4-8
    claims = [_as_claim(v) for v in verifications if v["status"] in ACCEPTED]
    rejected = [_as_rejected(v) for v in verifications if v["status"] not in ACCEPTED]
    hyp = None
    if hypothesis_text:
        hyp = verify_claim(hypothesis_text, sorted(allowed), records=records, existing_ids=set(records),
                           allowed_ids=allowed, plants=plants, compounds=compounds, verifiers=verifiers)
        verifications.append(hyp)

    cited = {i for c in claims for i in c["evidence_ids"]} | {i for x in ab["contradictions"] for i in x["evidence_ids"]}
    text = _prose(claims, ab["contradictions"])
    if hyp is not None:
        verdict = Verdict(hyp["status"])
        lead = {Verdict.SUPPORTED: "The indexed evidence supports this.",
                Verdict.PARTIALLY_SUPPORTED: "The indexed evidence partially supports this.",
                Verdict.CONTRADICTED: "The indexed evidence conflicts on this.",
                }.get(verdict, "The indexed evidence does not establish this.")
        text = f"{lead} {hyp['explanation']} " + text
    if qtype == QuestionType.PUBLICATION_LOOKUP:
        pubs = {r.publication.id: r.publication for r in compatible}
        base["publications"] = [{"id": p.id, "pmid": p.pmid, "doi": p.doi, "year": p.year, "title": p.title}
                                for p in sorted(pubs.values(), key=lambda p: -(p.year or 0))]
        text = (f"{len(pubs)} indexed publication(s) report on this: "
                + "; ".join(f"PMID {p.pmid} ({p.year})" for p in sorted(pubs.values(), key=lambda p: -(p.year or 0)))
                + ". " + text)
    mode = "llm_grounded" if llm_info else "retrieval_only"
    if not claims:
        message += "No drafted claim passed verification."
    elif llm_info:
        message += "AI-drafted wording; every atomic claim was verified against its cited records."
    else:
        message += "No LLM configured: claims are the compatible records themselves, verified claim by claim."
    return finish({**base, **llm_info, "mode": mode, "claims": claims, "rejected_claims": rejected,
                   "claim_verifications": verifications, "hypothesis": hyp,
                   "references": [refs[i] for i in sorted(cited) if i in refs], "answer": text.strip(),
                   "message": message.strip()})
