"""v0.2 scientific-reliability, security, graph and API tests."""
import json

import pytest
from sqlalchemy import select

from app import assistant
from app.models import EvidenceRecord, EvidenceType
from app.reasoning import (AnswerStatus, QuestionType, classify_question, find_contradictions, support_level,
                           validate_claim)
from tests.conftest import compound_id, plant_id


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, system, user):
        self.calls.append(user)
        return self.reply(user) if callable(self.reply) else self.reply


def _records(session, **filters):
    stmt = select(EvidenceRecord)
    for k, v in filters.items():
        stmt = stmt.where(getattr(EvidenceRecord, k) == v)
    return list(session.scalars(stmt))


# 1. Question classification ---------------------------------------------------------------

@pytest.mark.parametrize("question,expected", [
    ("Does Glycyrrhiza glabra cure cancer?", QuestionType.THERAPEUTIC_EFFICACY),
    ("Is licorice an effective treatment for ulcers?", QuestionType.THERAPEUTIC_EFFICACY),
    ("Are there clinical trials of Artemisia annua in patients?", QuestionType.CLINICAL),
    ("Is Artemisia annua toxic?", QuestionType.TOXICITY),
    ("What are the side effects of glycyrrhizin?", QuestionType.TOXICITY),
    ("What is the bioavailability of glabridin?", QuestionType.PHARMACOKINETICS),
    ("Does glycyrrhizin have antioxidant activity?", QuestionType.BIOLOGICAL_ACTIVITY),
    ("What is the traditional use of Ziziphora clinopodioides?", QuestionType.TRADITIONAL_USE),
    ("How was glabridin identified in Glycyrrhiza glabra?", QuestionType.METHODOLOGY),
    ("What family does Capparis spinosa belong to?", QuestionType.TAXONOMY),
    ("Which compounds are reported in Capparis spinosa?", QuestionType.COMPOSITION),
    ("Tell me about Ferula tadshikorum", QuestionType.GENERAL),
])
def test_question_classification(question, expected):
    assert classify_question(question) == expected


# 2. Evidence relevance --------------------------------------------------------------------

def test_evidence_relevance_rules(session):
    direct = _records(session, evidence_type=EvidenceType.DIRECT_EXPERIMENTAL)[0]
    trad = _records(session, evidence_type=EvidenceType.TRADITIONAL_USE)[0]
    comp = _records(session, evidence_type=EvidenceType.COMPUTATIONAL)[0]
    assert support_level(QuestionType.COMPOSITION, direct) == "full"
    for q in (QuestionType.THERAPEUTIC_EFFICACY, QuestionType.CLINICAL, QuestionType.TOXICITY,
              QuestionType.PHARMACOKINETICS):
        assert support_level(q, direct) is None
        assert support_level(q, trad) is None
        assert support_level(q, comp) is None
    assert support_level(QuestionType.TRADITIONAL_USE, trad) == "full"
    assert support_level(QuestionType.COMPOSITION, trad) is None
    assert support_level(QuestionType.BIOLOGICAL_ACTIVITY, comp) == "qualified"


# 3/4. Answerability and unsupported efficacy claims ----------------------------------------

def test_regression_glycyrrhiza_cancer_question_abstains(session):
    """Constituent records must never be turned into a claim that the plant cures cancer, even when an
    LLM is configured and eager to say so."""
    def eager(user):
        return json.dumps({"claims": [{"statement": "Glycyrrhiza glabra cures cancer.", "evidence_ids": [11]}]})

    provider = FakeProvider(eager)
    out = assistant.answer(session, "Does Glycyrrhiza glabra cure cancer?", provider=provider)
    ab = out["answerability"]
    assert out["answerable"] is False and ab["status"] == AnswerStatus.NOT_ANSWERABLE
    assert ab["question_type"] == QuestionType.THERAPEUTIC_EFFICACY
    assert out["mode"] == "abstained" and out["claims"] == []
    assert provider.calls == []  # generation never ran
    assert "cancer" in ab["reason"] and "cannot" in out["answer"]
    assert not any(w in out["answer"].lower() for w in ("cures cancer", "is effective", "treats cancer"))
    assert ab["missing_evidence"] and ab["retrieved_evidence_ids"] and ab["evidence_ids"] == []


@pytest.mark.parametrize("question", ["Is Artemisia annua toxic?", "Are there clinical trials of licorice?",
                                      "What is the bioavailability of glabridin in Glycyrrhiza glabra?"])
def test_safety_clinical_pk_questions_abstain(session, question):
    out = assistant.answer(session, question, provider=None)
    assert out["answerable"] is False and out["claims"] == []


def test_composition_question_is_answered_with_citations(session):
    out = assistant.answer(session, "Which compounds are reported in Glycyrrhiza glabra?", provider=None)
    assert out["answerability"]["status"] == AnswerStatus.ANSWERABLE
    assert out["claims"] and all(c["evidence_ids"] for c in out["claims"])
    cited = {i for c in out["claims"] for i in c["evidence_ids"]}
    assert cited <= set(out["answerability"]["evidence_ids"])
    assert all(f"#{i}" in out["answer"] for i in cited)


def test_computational_only_is_partial_and_qualified(session):
    out = assistant.answer(session, "Does glycyrrhizin bind lupus targets?", provider=None)
    assert out["answerability"]["status"] == AnswerStatus.PARTIAL
    assert all(c["support"] == "qualified" for c in out["claims"])
    assert any("docking score does not establish" in l for l in out["limitations"])


def test_out_of_corpus_question(session):
    out = assistant.answer(session, "What is the boiling point of water?", provider=None)
    assert out["mode"] == "no_evidence" and out["answerable"] is False


# 5/6/7. Claim-level citation validation ----------------------------------------------------

def _ctx(session, question):
    ab, retrieved, compatible = assistant.evaluate(session, question)
    ids = set(session.scalars(select(EvidenceRecord.id)))
    return {r.id: r for r in compatible}, ids, ab["question_type"]


def test_claim_validation_rules(session):
    by_id, existing, qtype = _ctx(session, "Which compounds are reported in Glycyrrhiza glabra?")
    glab = next(r for r in by_id.values() if r.compound and r.compound.name == "glabridin")
    ok = {"statement": "Glabridin was isolated from Glycyrrhiza glabra.", "evidence_ids": [glab.id]}
    assert validate_claim(ok, by_id, existing, qtype) is None
    cases = {
        "missing": ({"statement": "Glabridin occurs in licorice.", "evidence_ids": []}, "no evidence IDs"),
        "invalid": ({"statement": "Glabridin occurs.", "evidence_ids": [99999]}, "do not exist"),
        "outside": ({"statement": "Pulegone is in Ziziphora clinopodioides.",
                     "evidence_ids": [next(i for i in existing if i not in by_id)]}, "not part of the retrieved"),
        "efficacy": ({"statement": "Glabridin from Glycyrrhiza glabra cures cancer.", "evidence_ids": [glab.id]},
                     "efficacy"),
        "numbers": ({"statement": "Glabridin was isolated from Glycyrrhiza glabra at 99.5% purity.",
                     "evidence_ids": [glab.id]}, "numbers not found"),
        "unrelated": ({"statement": "Quercetin is a flavonol.", "evidence_ids": [glab.id]}, "does not mention"),
        "garbage": ({"statement": "x", "evidence_ids": ["abc"]}, "not integers"),
    }
    for name, (claim, fragment) in cases.items():
        reason = validate_claim(claim, by_id, existing, qtype)
        assert reason and fragment in reason, (name, reason)


def test_computational_claim_must_be_qualified(session):
    by_id, existing, qtype = _ctx(session, "Does glycyrrhizin bind lupus targets?")
    rid = next(iter(by_id))
    bad = {"statement": "Glycyrrhizin binds lupus targets in Glycyrrhiza glabra.", "evidence_ids": [rid]}
    good = {"statement": "Docking predicted that glycyrrhizin complements lupus-associated targets.", "evidence_ids": [rid]}
    assert "prediction" in validate_claim(bad, by_id, existing, qtype)
    assert validate_claim(good, by_id, existing, qtype) is None


def test_llm_overreach_is_rejected_in_pipeline(session):
    def reply(user):
        ids = [r["id"] for r in json.loads(user.split("Evidence records:\n", 1)[1])]
        return json.dumps({"claims": [
            {"statement": "Liquiritin was isolated from Glycyrrhiza glabra.", "evidence_ids": [ids[0]]},
            {"statement": "Glycyrrhiza glabra is proven to prevent disease.", "evidence_ids": [ids[0]]},
            {"statement": "Liquiritin is present.", "evidence_ids": []},
        ]})
    out = assistant.answer(session, "Which compounds are reported in Glycyrrhiza glabra?", provider=FakeProvider(reply))
    assert len(out["claims"]) == 1 and len(out["rejected_claims"]) == 2


# 8. Contradictory evidence -------------------------------------------------------------------

def test_contradictions_are_shown_not_resolved(session):
    out = assistant.answer(session, "How much pulegone is in Ziziphora clinopodioides essential oil?", provider=None)
    [c] = out["answerability"]["contradictions"]
    values = sorted(f["value_percent"] for f in c["findings"] if f["value_percent"] is not None)
    assert values == [7.9, 29.24, 62.17]
    assert len(c["evidence_ids"]) == 3 and "Türkiye" in c["explanation"]
    assert all(f"#{i}" in out["answer"] for i in c["evidence_ids"])


def test_no_contradiction_for_single_source(session):
    assert find_contradictions(_records(session, evidence_type=EvidenceType.TRADITIONAL_USE)) == []


# 9. Pending expert-review limitation ----------------------------------------------------------

def test_pending_review_limitation(session):
    out = assistant.answer(session, "Which compounds are reported in Capparis spinosa?", provider=None)
    assert any("pending expert review" in l for l in out["limitations"])


# 10. Review mutation restrictions -----------------------------------------------------------

def test_reviews_disabled_without_configuration(client, monkeypatch):
    monkeypatch.delenv("REVIEWER_TOKENS", raising=False)
    r = client.post("/api/evidence/1/reviews", json={"review_status": "expert_confirmed"})
    assert r.status_code == 403
    assert client.get("/api/evidence/1").json()["review_status"] == "pending_expert_review"
    assert client.get("/api/health").json()["reviews_enabled"] is False


def test_short_tokens_are_ignored(client, monkeypatch):
    monkeypatch.setenv("REVIEWER_TOKENS", "Dr Weak=short")
    r = client.post("/api/evidence/1/reviews", json={"review_status": "expert_confirmed"},
                    headers={"Authorization": "Bearer short"})
    assert r.status_code == 403


def test_provenance_separates_checks(client):
    p = client.get("/api/evidence/1/provenance").json()
    assert p["source_excerpt"]["status"] == "excerpt_verified"
    assert "quotation only" in p["source_excerpt"]["meaning"]
    assert p["interpretation"]["status"] == "ai_assisted_interpretation"
    assert p["expert_review"]["status"] == "pending_expert_review"
    assert client.get("/api/evidence/9999/provenance").status_code == 404


# 11. Knowledge graph --------------------------------------------------------------------------

def test_graph_relationships(client):
    g = client.get("/api/graph").json()
    ids = [n["id"] for n in g["nodes"]]
    assert len(ids) == len(set(ids))  # no duplicate nodes
    assert g["counts"] == {"plant": 5, "compound": 23, "publication": 20, "evidence": 37}
    node_ids = set(ids)
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in g["edges"])
    rel = {(e["source"], e["relation"]) for e in g["edges"]}
    assert ("evidence:1", "reported_in") in rel and ("evidence:1", "concerns_compound") in rel
    # quercetin is reported by two Capparis publications but appears once
    assert sum(1 for n in g["nodes"] if n["label"] == "quercetin") == 1
    # plant-level traditional-use records have no compound edge
    trad = [n for n in g["nodes"] if n.get("evidence_type") == "traditional_use"]
    assert trad and not any(e["source"] == trad[0]["id"] and e["relation"] == "concerns_compound" for e in g["edges"])


def test_graph_filtered_by_plant(client):
    pid = plant_id(client, "Ferula tadshikorum")
    g = client.get("/api/graph", params={"plant_id": pid}).json()
    assert g["counts"] == {"plant": 1, "compound": 2, "publication": 1, "evidence": 2}
    assert client.get("/api/graph", params={"plant_id": 9999}).status_code == 404
    assert client.get("/api/graph", params={"plant_id": 0}).status_code == 422


# 12. Search across record types -----------------------------------------------------------------

def test_search_categories(client):
    r = client.get("/api/search", params={"q": "glabridin"}).json()
    assert [c["name"] for c in r["compounds"]] == ["glabridin"]
    assert any("glabridin" in e["original_excerpt"].lower() for e in r["evidence"])
    r = client.get("/api/search", params={"q": "LBQIJVLKGVZRIW"}).json()  # InChIKey prefix
    assert [c["name"] for c in r["compounds"]] == ["glabridin"]
    r = client.get("/api/search", params={"q": "Lamiaceae"}).json()
    assert [p["accepted_name"] for p in r["plants"]] == ["Ziziphora clinopodioides"]
    r = client.get("/api/search", params={"q": "oleo-gum"}).json()
    assert r["publications"] and r["evidence"]


# 13. API error handling, pagination, new endpoints ----------------------------------------------

def test_pagination_headers(client):
    r = client.get("/api/evidence", params={"limit": 5, "offset": 10})
    assert r.status_code == 200 and len(r.json()) == 5
    assert r.headers["X-Total-Count"] == "37" and r.headers["X-Offset"] == "10"
    assert [e["id"] for e in r.json()] == list(range(11, 16))
    r = client.get("/api/publications", params={"limit": 3})
    assert len(r.json()) == 3 and r.headers["X-Total-Count"] == "20"
    assert client.get("/api/compounds", params={"limit": 0}).status_code == 422
    assert client.get("/api/evidence", params={"offset": -1}).status_code == 422


def test_dashboard_numbers_match_database(client):
    d = client.get("/api/dashboard").json()
    assert d["totals"]["evidence_records"] == 37 == sum(d["evidence_by_type"].values())
    assert d["verification"]["expert_review"] == {"pending_expert_review": 37}
    assert d["verification"]["interpretation"] == {"ai_assisted_interpretation": 37}
    assert sum(c["evidence_count"] for c in d["coverage"]) == 37
    assert d["recent_activity"] and all("at" in a for a in d["recent_activity"])


def test_answerability_endpoint(client):
    r = client.post("/api/assistant/answerability", json={"question": "Does Glycyrrhiza glabra cure cancer?"})
    body = r.json()
    assert r.status_code == 200 and body["answerable"] is False
    assert set(body) >= {"answerable", "reason", "question_type", "evidence_ids", "limitations",
                         "missing_evidence", "contradictions"}
    assert client.post("/api/assistant/answerability", json={"question": "x"}).status_code == 422


def test_assistant_response_schema(client):
    body = client.post("/api/assistant", json={"question": "Which compounds are reported in Capparis spinosa?"}).json()
    assert body["answerability"]["question_type"] == "chemical_composition"
    assert body["answer"] and body["claims"] and body["references"]


def test_unknown_route_and_method(client):
    assert client.get("/api/nope").status_code == 404
    assert client.delete("/api/plants/1").status_code == 405


# Migrations ---------------------------------------------------------------------------------------

def test_migrations_upgrade_v01_database(tmp_path):
    """A v0.1 database (create_all, AI pseudo-reviews) is stamped and upgraded without data loss."""
    import sqlite3
    from app.db import make_engine
    from app.migrate import upgrade
    db = tmp_path / "v01.db"
    engine = make_engine(f"sqlite:///{db}")
    from alembic.config import Config  # build the v0.1 schema from migration 0001 itself
    from alembic import command
    from app.config import ROOT
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001")
        conn.exec_driver_sql("DROP TABLE alembic_version")  # v0.1 had no version table
        conn.exec_driver_sql("INSERT INTO plant (id, accepted_name, created_at, updated_at) VALUES (1,'P',0,0)")
        conn.exec_driver_sql("INSERT INTO publication (id, title, authors, created_at, updated_at) VALUES (1,'T','[]',0,0)")
        conn.exec_driver_sql("INSERT INTO evidence_record (id, plant_id, publication_id, observation, evidence_type, "
                             "source_location, original_excerpt, verification_status, created_at, updated_at) "
                             "VALUES (1,1,1,'o','traditional_use','abstract','e','excerpt_verified',0,0)")
        conn.exec_driver_sql("INSERT INTO review_record (evidence_record_id, review_status, reviewer, created_at, "
                             "updated_at) VALUES (1,'pending_expert_review','ai-assisted-curation (x)',0,0)")
        conn.exec_driver_sql("INSERT INTO review_record (evidence_record_id, review_status, reviewer, created_at, "
                             "updated_at) VALUES (1,'expert_confirmed','Dr Real',0,0)")
    assert upgrade(engine) == "0007"  # head: 0003 in v0.3, 0004 (workspaces) in v0.5, 0005 (1.0 discovery)
    c = sqlite3.connect(db)
    assert c.execute("SELECT review_scope FROM review_record").fetchall() == [("expert_review",)]
    assert c.execute("SELECT reviewer FROM review_record").fetchall() == [("Dr Real",)]
    assert c.execute("SELECT interpretation_status, interpreted_by FROM evidence_record").fetchone() == \
        ("ai_assisted_interpretation", "ai-assisted-curation (x)")
    assert upgrade(engine) == "0007"  # idempotent
