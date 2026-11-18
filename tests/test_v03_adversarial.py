"""v0.3 adversarial verification tests, on the real seed data unless marked SYNTHETIC.

SYNTHETIC fixtures (in-vitro / clinical / negation records) are inserted only into the temporary test
database to exercise evidence categories that the real corpus does not contain yet. They never reach
data/seed or the application database.
"""
import json

import pytest
from sqlalchemy import select

from app import assistant
from app.claims import ClaimCategory, Verdict, classify_claim, extract_atomic_claims
from app.models import Compound, EvidenceRecord, Plant, Publication
from app.reasoning import QuestionType, classify_question
from app.verification import DeterministicSpanVerifier, LLMEntailmentVerifier, verify_claim

SYN = "SYNTHETIC TEST FIXTURE - not real data"


@pytest.fixture()
def ctx(session):
    def build():
        records = {r.id: r for r in session.scalars(select(EvidenceRecord))}
        return records, list(session.scalars(select(Plant))), list(session.scalars(select(Compound)))

    def verify(claim, ids, verifiers=None, allowed=None):
        records, plants, compounds = build()
        return verify_claim(claim, ids, records=records, existing_ids=set(records), allowed_ids=allowed,
                            plants=plants, compounds=compounds, verifiers=verifiers)
    return verify


def _rec(session, plant, compound, pmid):
    return session.scalar(select(EvidenceRecord).join(EvidenceRecord.publication).join(EvidenceRecord.plant)
                          .where(Plant.accepted_name == plant, Publication.pmid == pmid,
                                 EvidenceRecord.compound.has(Compound.name == compound)))


@pytest.fixture()
def glabridin_id(session):
    return _rec(session, "Glycyrrhiza glabra", "glabridin", "38667552").id  # identification list (PMID 38667552)


def _synthetic(session, etype, excerpt, compound="glabridin", plant="Glycyrrhiza glabra"):
    pub = Publication(title=f"{SYN}: {etype} study of {plant}", authors=[], year=2026, pmid=None, doi=None,
                      abstract=f"{SYN}. {excerpt}", metadata_source=SYN)
    rec = EvidenceRecord(plant=session.scalar(select(Plant).where(Plant.accepted_name == plant)),
                         compound=session.scalar(select(Compound).where(Compound.name == compound)),
                         publication=pub, observation=f"{SYN}: {excerpt}", identification_method=SYN,
                         evidence_type=etype, source_location="abstract", original_excerpt=excerpt,
                         verification_status="excerpt_verified", curator_notes=SYN)
    session.add(rec)
    session.commit()
    return rec.id


# --- A: supported identification ---------------------------------------------------------------

def test_A_identification_supported(ctx, glabridin_id):
    v = ctx("Glabridin was identified in the reported plant extract.", [glabridin_id])
    assert v["category"] == ClaimCategory.CHEMICAL_IDENTIFICATION
    assert v["status"] == Verdict.SUPPORTED
    plant_check = next(c for c in v["checks"] if c["name"] == "plant_in_source")
    # the plant link must be explicit in the source (here the publication title names Glycyrrhiza glabra)
    assert plant_check["passed"] and ("title" in plant_check["detail"] or "abstract" in plant_check["detail"])
    assert v["source_excerpts"][0]["matched_span"] and "glabridin" in v["source_excerpts"][0]["matched_span"].lower()
    assert v["expert_review_required"] is True


# --- B: unsupported antioxidant activity ----------------------------------------------------------

def test_B_antioxidant_not_supported(ctx, glabridin_id):
    v = ctx("Glabridin is a potent antioxidant.", [glabridin_id])
    assert v["category"] == ClaimCategory.BIOLOGICAL_ACTIVITY
    assert v["status"] == Verdict.NOT_SUPPORTED
    assert "cannot support a biological_activity claim" in v["explanation"]


# --- C: unsupported abundance -------------------------------------------------------------------

def test_C_abundance_not_supported(ctx, session, glabridin_id):
    v = ctx("Glabridin is the most abundant compound in the plant.", [glabridin_id])
    assert v["category"] == ClaimCategory.CHEMICAL_ABUNDANCE and v["status"] == Verdict.NOT_SUPPORTED
    # even with the record that does carry numbers (isolation yield 0.17 %), no comparison exists
    yield_rec = _rec(session, "Glycyrrhiza glabra", "glabridin", "42513095").id
    v = ctx("Glabridin is the most abundant compound in Glycyrrhiza glabra.", [glabridin_id, yield_rec])
    assert v["status"] == Verdict.NOT_SUPPORTED and f"#{yield_rec}" in v["explanation"]


def test_C_abundance_question_end_to_end(session):
    out = assistant.answer(session, "Is glabridin the most abundant compound in licorice?", provider=None)
    assert out["question_type"] == QuestionType.ABUNDANCE
    assert out["hypothesis"]["status"] == Verdict.NOT_SUPPORTED
    assert out["answer"].startswith("The indexed evidence does not establish this.")


# --- D: clinical efficacy -----------------------------------------------------------------------

@pytest.mark.parametrize("q", ["Does Glycyrrhiza glabra cure cancer?", "Is glabridin proven to cure cancer?",
                               "Does licorice treat cancer?"])
def test_D_efficacy_abstains(session, q):
    class Eager:
        name, model = "fake", "fake"
        calls = 0

        def complete(self, system, user):
            Eager.calls += 1
            return json.dumps({"claims": [{"statement": "It cures cancer.", "evidence_ids": [1]}]})
    out = assistant.answer(session, q, provider=Eager())
    assert out["answerable"] is False and out["mode"] == "abstained" and out["claims"] == []
    assert Eager.calls == 0
    assert "cannot draw the requested conclusion" in out["answer"]
    assert all(c["category"] != ClaimCategory.THERAPEUTIC_EFFICACY for c in out["established_findings"])


# --- E: in vitro vs clinical (SYNTHETIC in-vitro record) ------------------------------------------

def test_E_in_vitro_is_not_clinical(ctx, session):
    rid = _synthetic(session, "experimental_bioactivity",
                     "In vitro, glabridin inhibited tyrosinase activity in cultured B16 cells (IC50 reported).")
    activity = ctx("Glabridin inhibited tyrosinase activity in cultured cells.", [rid])
    assert activity["status"] == Verdict.SUPPORTED
    assert any("In-vitro finding" in l for l in activity["limitations"])
    clinical = ctx("Glabridin is clinically effective in humans.", [rid])
    assert clinical["category"] == ClaimCategory.CLINICAL_EFFICACY and clinical["status"] == Verdict.NOT_SUPPORTED
    efficacy = ctx("Glabridin is an effective treatment for hyperpigmentation.", [rid])
    assert efficacy["status"] == Verdict.NOT_SUPPORTED


def test_E_clinical_record_supports_only_clinical_scope(ctx, session):
    rid = _synthetic(session, "clinical_study",
                     "In a randomized placebo-controlled trial, patients receiving glabridin cream showed reduced "
                     "pigmentation scores.")
    assert ctx("Glabridin cream reduced pigmentation scores in a clinical trial in patients.", [rid])["status"] \
        == Verdict.SUPPORTED
    assert ctx("Glabridin is a potent antioxidant.", [rid])["status"] == Verdict.NOT_SUPPORTED


# --- F: computational prediction ------------------------------------------------------------------

def test_F_computational_is_not_experimental(ctx, session):
    comp = session.scalar(select(EvidenceRecord).where(EvidenceRecord.evidence_type == "computational_prediction"))
    v = ctx("Glycyrrhizin experimentally binds to lupus targets.", [comp.id])
    assert v["category"] == ClaimCategory.MECHANISM_OF_ACTION and v["status"] == Verdict.NOT_SUPPORTED
    rid = _synthetic(session, "computational_prediction",
                     "Molecular docking predicted that glabridin binds tyrosinase with a score of -9.1 kcal/mol.")
    assert ctx("Docking predicted that glabridin binds tyrosinase.", [rid])["status"] == Verdict.SUPPORTED
    assert ctx("Glabridin binds tyrosinase.", [rid])["status"] == Verdict.NOT_SUPPORTED
    assert ctx("Glabridin inhibits tyrosinase.", [rid])["status"] == Verdict.NOT_SUPPORTED


# --- G: contradictions ------------------------------------------------------------------------

def test_G_conflicting_quantities_preserved(ctx, session):
    hi = _rec(session, "Ziziphora clinopodioides", "pulegone", "37505209").id  # 62.17 %
    lo = _rec(session, "Ziziphora clinopodioides", "pulegone", "41663704").id  # 7.9 %, Türkiye
    v = ctx("Pulegone makes up 62.17% of Ziziphora clinopodioides essential oil.", [hi, lo])
    assert v["status"] == Verdict.CONTRADICTED
    assert {s["evidence_id"]: s["verdict"] for s in v["source_excerpts"]} == {hi: Verdict.SUPPORTED, lo: Verdict.CONTRADICTED}
    out = assistant.answer(session, "Is pulegone the dominant constituent of Ziziphora clinopodioides essential oil?",
                           provider=None)
    assert out["hypothesis"]["status"] == Verdict.CONTRADICTED
    [c] = out["contradictions"]
    assert {f["geographic_origin"] for f in c["findings"]} >= {"Türkiye"}


# --- H: citation laundering -------------------------------------------------------------------

def test_H_one_citation_five_claims(session, glabridin_id):
    paragraph = ("Glabridin was isolated from Glycyrrhiza glabra, is a potent antioxidant, is the most abundant "
                 "compound in the plant, is clinically proven to treat inflammation, and is safe for daily use.")

    class Launderer:
        name, model = "fake", "fake"

        def complete(self, system, user):
            return json.dumps({"claims": [{"statement": paragraph, "evidence_ids": [glabridin_id]}]})
    out = assistant.answer(session, "Which compounds are reported in Glycyrrhiza glabra?", provider=Launderer())
    atoms = [v for v in out["claim_verifications"] if v.get("draft") == paragraph]
    assert len(atoms) == 5
    accepted = [v for v in atoms if v["status"] in (Verdict.SUPPORTED, Verdict.PARTIALLY_SUPPORTED)]
    assert [v["statement"] for v in accepted] == ["Glabridin was isolated from Glycyrrhiza glabra."]
    assert {v["category"] for v in atoms} == {ClaimCategory.CHEMICAL_IDENTIFICATION, ClaimCategory.BIOLOGICAL_ACTIVITY,
                                              ClaimCategory.CHEMICAL_ABUNDANCE, ClaimCategory.CLINICAL_EFFICACY,
                                              ClaimCategory.SAFETY}
    assert "potent antioxidant" not in out["answer"] and "clinically" not in out["answer"]


def test_H_causal_laundering(ctx, session):
    trad = session.scalar(select(EvidenceRecord).where(EvidenceRecord.evidence_type == "traditional_use"))
    atoms = extract_atomic_claims(f"{trad.plant.accepted_name} is an effective treatment because it is "
                                  "traditionally used as a tea.")
    assert [classify_claim(a) for a in atoms] == [ClaimCategory.THERAPEUTIC_EFFICACY, ClaimCategory.TRADITIONAL_USE]
    assert ctx(atoms[0], [trad.id])["status"] == Verdict.NOT_SUPPORTED


# --- I: invented citation ---------------------------------------------------------------------

@pytest.mark.parametrize("ids", [[999999], [1, 999999], ["x"], []])
def test_I_invented_or_missing_citation(ctx, ids):
    v = ctx("Glabridin was isolated from Glycyrrhiza glabra.", ids)
    assert v["status"] == Verdict.NOT_SUPPORTED and v["checks"][0]["name"] == "citation"


def test_I_citation_outside_retrieved_context(ctx, glabridin_id):
    v = ctx("Glabridin was isolated from Glycyrrhiza glabra.", [glabridin_id], allowed={glabridin_id + 1})
    assert v["status"] == Verdict.NOT_SUPPORTED and "not part of the retrieved" in v["explanation"]


# --- J: publication exists, no relevant excerpt ------------------------------------------------

def test_J_missing_source_text_is_insufficient(ctx, session):
    # real record flagged by the v0.2 data audit: its excerpt does not name glycyrrhizin
    comp = session.scalar(select(EvidenceRecord).where(EvidenceRecord.evidence_type == "computational_prediction"))
    v = ctx("Docking predicted that glycyrrhizin complements lupus-associated targets.", [comp.id])
    assert v["status"] == Verdict.INSUFFICIENT and "does not name glycyrrhizin" in v["explanation"]


def test_J_excerpt_mismatch_is_insufficient(ctx, session, glabridin_id):
    rec = session.get(EvidenceRecord, glabridin_id)
    rec.verification_status = "excerpt_mismatch"
    session.commit()
    assert ctx("Glabridin was isolated from Glycyrrhiza glabra.", [glabridin_id])["status"] == Verdict.INSUFFICIENT


# --- extra adversarial cases -------------------------------------------------------------------

def test_wrong_plant_is_rejected(ctx, glabridin_id):
    v = ctx("Glabridin was isolated from Ziziphora clinopodioides.", [glabridin_id])
    assert v["status"] == Verdict.NOT_SUPPORTED and any(c["name"] == "plant_match" and not c["passed"] for c in v["checks"])


def test_wrong_compound_is_rejected(ctx, glabridin_id):
    assert ctx("Quercetin was isolated from Glycyrrhiza glabra.", [glabridin_id])["status"] == Verdict.NOT_SUPPORTED


def test_inflated_number_is_rejected(ctx, session):
    rid = _rec(session, "Glycyrrhiza glabra", "glabridin", "42513095").id
    assert ctx("Glabridin was isolated from Glycyrrhiza glabra with a purity of 87.9%.", [rid])["status"] == Verdict.SUPPORTED
    assert ctx("Glabridin was isolated from Glycyrrhiza glabra with a purity of 99.9%.", [rid])["status"] in (
        Verdict.NOT_SUPPORTED, Verdict.CONTRADICTED)


def test_negated_source_is_contradiction(ctx, session):
    rid = _synthetic(session, "experimental_bioactivity", "Glabridin showed no significant activity against "
                     "Escherichia coli in vitro.")
    assert ctx("Glabridin is active against Escherichia coli.", [rid])["status"] == Verdict.CONTRADICTED


def test_property_claims_checked_against_pubchem_record(ctx):
    assert ctx("The molecular formula of glabridin is C20H20O4.", [])["status"] == Verdict.SUPPORTED
    assert ctx("The molecular formula of glabridin is C21H22O4.", [])["status"] == Verdict.CONTRADICTED


def test_property_claim_never_supports_occurrence(session):
    out = assistant.answer(session, "What is the molecular formula of glabridin?", provider=None)
    assert out["question_type"] == QuestionType.CHEMICAL_PROPERTY and out["mode"] == "structured_record"
    assert [c["statement"] for c in out["claims"]] == ["The molecular formula of glabridin is C20H20O4."]
    assert out["claims"][0]["structured_sources"][0]["type"] == "pubchem_compound_record"
    assert "do not establish that it occurs in any plant" in out["answer"]


def test_missing_property_is_reported_not_invented(session):
    out = assistant.answer(session, "What is the molecular formula of 8-acetoxy-5-hydroxyumbelliprenin?", provider=None)
    assert out["claims"] == [] and "not available" in out["answer"]


# --- semantic verifier: LLM is an assessor, never an authority ---------------------------------

class FakeLLM:
    name, model = "fake", "fake-judge"

    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user):
        return self.reply


def test_llm_verifier_cannot_upgrade_deterministic_rejection(ctx, glabridin_id):
    judge = LLMEntailmentVerifier(FakeLLM(json.dumps({"verdict": "supported", "explanation": "Looks right.",
                                                      "quoted_span": "glabridin"})))
    v = ctx("Glabridin is a potent antioxidant.", [glabridin_id], verifiers=[DeterministicSpanVerifier(), judge])
    assert v["status"] == Verdict.NOT_SUPPORTED


@pytest.mark.parametrize("reply", ["not json at all", json.dumps({"verdict": "certain", "explanation": "x"}),
                                   json.dumps({"verdict": "supported", "explanation": "ok",
                                               "quoted_span": "words that are not in the excerpt"})])
def test_llm_verifier_malformed_or_unquoted_output_abstains(ctx, glabridin_id, reply):
    judge = LLMEntailmentVerifier(FakeLLM(reply))
    v = ctx("Glabridin was isolated from Glycyrrhiza glabra.", [glabridin_id],
            verifiers=[DeterministicSpanVerifier(), judge])
    assert v["status"] == Verdict.INSUFFICIENT


def test_llm_verifier_can_downgrade(ctx, glabridin_id):
    judge = LLMEntailmentVerifier(FakeLLM(json.dumps({"verdict": "partially_supported", "explanation": "List only.",
                                                      "quoted_span": "glabridin (6)"})))
    v = ctx("Glabridin was isolated from Glycyrrhiza glabra.", [glabridin_id],
            verifiers=[DeterministicSpanVerifier(), judge])
    assert v["status"] == Verdict.PARTIALLY_SUPPORTED and "llm:fake-judge" in v["verifiers"]


# --- routing ---------------------------------------------------------------------------------

@pytest.mark.parametrize("q,expected", [
    ("What is the molecular formula of glabridin?", QuestionType.CHEMICAL_PROPERTY),
    ("What is the SMILES of quercetin?", QuestionType.CHEMICAL_PROPERTY),
    ("What is the PubChem CID of rutin?", QuestionType.CHEMICAL_PROPERTY),
    ("Which compounds have been reported in licorice?", QuestionType.COMPOSITION),
    ("What research reports glabridin?", QuestionType.PUBLICATION_LOOKUP),
    ("Does licorice treat cancer?", QuestionType.THERAPEUTIC_EFFICACY),
    ("Is glabridin the most abundant compound in licorice?", QuestionType.ABUNDANCE),
    ("What is the mechanism of action of glycyrrhizin?", QuestionType.MECHANISM),
])
def test_routing(q, expected):
    assert classify_question(q) == expected


def test_publication_lookup_lists_real_publications(session):
    out = assistant.answer(session, "What research reports glabridin?", provider=None)
    assert {p["pmid"] for p in out["publications"]} == {"38667552", "42513095"}


# --- API ---------------------------------------------------------------------------------

def test_verify_claim_endpoint(client, glabridin_id):
    r = client.post("/api/verify-claim", json={"claim": "Glabridin is a potent antioxidant.",
                                               "evidence_ids": [glabridin_id]})
    assert r.status_code == 200 and r.json()["status"] == "not_supported"
    r = client.post("/api/verify-claim", json={"claim": "Glabridin is present in licorice and has antioxidant "
                                               "activity.", "evidence_ids": [glabridin_id]})
    assert r.status_code == 422 and len(r.json()["detail"]["atomic_claims"]) == 2
    assert client.post("/api/verify-claim", json={"claim": "x", "evidence_ids": []}).status_code == 422
    assert client.post("/api/verify-claim", json={"claim": "Glabridin occurs.",
                                                  "evidence_ids": list(range(60))}).status_code == 422


def test_extract_and_matrix_endpoints(client):
    r = client.post("/api/extract-claims", json={"claim": "Glabridin is present in licorice, has antioxidant "
                                                 "activity, and is clinically proven to treat inflammation."})
    assert [c["category"] for c in r.json()] == ["chemical_occurrence", "biological_activity", "clinical_efficacy"]
    m = {row["claim_category"]: row for row in client.get("/api/verification/matrix").json()}
    assert m["chemical_abundance"]["excerpt_condition"] and m["clinical_efficacy"]["supported_by"] == {"clinical_study": "supported"}
    assert m["toxicity"]["supported_by"] == {}


def test_assistant_verification_summary(client):
    body = client.post("/api/assistant", json={"question": "Which chemical constituents have been reported in "
                                                           "Glycyrrhiza glabra?"}).json()
    s = body["verification_summary"]
    assert s["supported"] >= 1 and sum(s.values()) == len(body["claim_verifications"])
    for c in body["claims"]:
        assert c["status"] in ("supported", "partially_supported") and c["source_excerpts"] and c["checks"]


def test_review_scope_separates_interpretation_from_expert(client, monkeypatch):
    monkeypatch.setenv("REVIEWER_TOKENS", "Dr Test=0123456789abcdef-token")
    h = {"Authorization": "Bearer 0123456789abcdef-token"}
    r = client.post("/api/evidence/2/reviews", json={"review_status": "expert_confirmed",
                                                     "review_scope": "interpretation_review"}, headers=h)
    assert r.status_code == 201
    p = client.get("/api/evidence/2/provenance").json()
    assert p["interpretation"]["review_status"] == "expert_confirmed"
    assert p["expert_review"]["status"] == "pending_expert_review"  # interpretation review is NOT expert review
    assert p["publication_metadata"]["status"] == "metadata_verified"


def test_source_without_plant_link_is_insufficient(ctx, session):
    """A real-looking finding whose source never names the plant cannot support a plant-specific claim."""
    pub = Publication(title=f"{SYN}: no plant named", authors=[], year=2026, abstract=f"{SYN}.",
                      metadata_source=SYN)
    rec = EvidenceRecord(plant=session.scalar(select(Plant).where(Plant.accepted_name == "Glycyrrhiza glabra")),
                         compound=session.scalar(select(Compound).where(Compound.name == "glabridin")),
                         publication=pub, observation=SYN, evidence_type="direct_experimental_identification",
                         source_location="abstract", original_excerpt="Glabridin was isolated and identified.",
                         verification_status="excerpt_verified")
    session.add(rec)
    session.commit()
    v = ctx("Glabridin was isolated from Glycyrrhiza glabra.", [rec.id])
    assert v["status"] == Verdict.INSUFFICIENT and "plant not named" in v["explanation"]


def test_like_wildcards_are_literal(client):
    r = client.get("/api/search", params={"q": "%%"}).json()
    assert not (r["plants"] or r["compounds"] or r["publications"] or r["evidence"])
    r = client.get("/api/evidence", params={"q": "_"})
    assert r.status_code == 200 and r.headers["X-Total-Count"] == "0"
