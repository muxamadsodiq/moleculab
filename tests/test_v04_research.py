"""v0.4 research intelligence: gaps, contradictions, hypotheses, priorities (real seed data unless SYNTHETIC)."""
import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import research
from app.claims import Verdict
from app.db import get_session, init_db, make_engine
from app.main import app
from app.models import Compound, EvidenceRecord, Plant, Publication
from tests.conftest import compound_id, plant_id

SYN = "SYNTHETIC TEST FIXTURE - not real data"
UNIVERSAL = re.compile(r"never (been )?(tested|studied)|has not been (tested|studied)|no studies exist|not been studied", re.I)


def _all_text(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _all_text(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_text(v)


# ---------------- gaps ----------------

def test_plant_with_compounds_but_no_toxicity_evidence(client):
    pid = plant_id(client, "Glycyrrhiza glabra")
    gaps = client.get(f"/api/research/plants/{pid}/gaps").json()["items"]
    [tox] = [g for g in gaps if g["category"] == "missing_toxicity_evidence"]
    assert tox["kind"] == "dataset_coverage" and tox["certainty"] == "dataset_fact"
    assert "No toxicity evidence is currently indexed in MOLECULAB" in tox["explanation"]
    assert len(tox["related_compounds"]) == 7
    assert "not the complete scientific literature" in tox["dataset_limitations"]
    assert not UNIVERSAL.search(tox["explanation"])


def test_plant_with_insufficient_indexed_literature(client):
    pid = plant_id(client, "Ferula tadshikorum")
    [cov] = [g for g in client.get(f"/api/research/plants/{pid}/gaps").json()["items"]
             if g["category"] == "plant_research_coverage"]
    assert "Only 1 publication(s)" in cov["explanation"] and len(cov["evidence_ids"]) == 2


def test_poorly_supported_record_is_flagged_by_verifier(client):
    gaps = client.get("/api/research/gaps", params={"category": "poorly_supported_relationship"}).json()["items"]
    assert [g["evidence_ids"] for g in gaps] == [[20]]  # the record flagged by the v0.2 data audit
    assert gaps[0]["certainty"] == "rule_based_assessment" and "insufficient_information" in gaps[0]["explanation"]


def test_tentative_only_identification_gap(client):
    gaps = client.get("/api/research/gaps", params={"category": "compound_identification"}).json()["items"]
    assert {g["compound"]["name"] for g in gaps} == {"scopoletin", "glycyrrhizin"}


def test_every_gap_is_dataset_scoped_and_references_real_evidence(client, session):
    ids = set(session.scalars(select(EvidenceRecord.id)))
    gaps = client.get("/api/research/gaps", params={"limit": 500}).json()["items"]
    assert gaps
    for g in gaps:
        assert set(g["evidence_ids"]) <= ids
        for text in _all_text(g):
            assert not UNIVERSAL.search(text), text
        assert g["suggested_next_investigation"]


def test_gap_detail_and_compound_filter(client):
    cid = compound_id(client, "pulegone")
    gaps = client.get(f"/api/research/compounds/{cid}/gaps").json()["items"]
    conflict = next(g for g in gaps if g["category"] == "conflicting_findings")
    assert client.get(f"/api/research/gaps/{conflict['id']}").json()["id"] == conflict["id"]
    assert client.get(f"/api/v1/research/gaps/{conflict['id']}").status_code == 200
    assert client.get("/api/research/gaps/not-a-gap").status_code == 404


def test_missing_publication_reference(client, session):
    pub = Publication(title=f"{SYN}: unreferenced study of Capparis spinosa", authors=[], abstract=SYN, metadata_source=SYN)
    session.add(EvidenceRecord(plant=session.scalar(select(Plant).where(Plant.accepted_name == "Capparis spinosa")),
                               compound=session.scalar(select(Compound).where(Compound.name == "rutin")), publication=pub,
                               observation=SYN, evidence_type="direct_experimental_identification",
                               source_location="abstract", original_excerpt="Rutin was isolated.",
                               verification_status="excerpt_verified"))
    session.commit()
    gaps = client.get("/api/research/gaps", params={"category": "missing_publication_reference"}).json()["items"]
    assert len(gaps) == 1 and "without a PMID or DOI" in gaps[0]["explanation"]


# ---------------- contradictions ----------------

def test_conflicting_abundance_measurements(client):
    [c] = client.get("/api/research/contradictions").json()
    assert c["compound"]["name"] == "pulegone" and c["conflict_type"] == "quantity"
    assert sorted(f["value_percent"] for f in c["findings"]) == [7.9, 29.24, 62.17]
    assert all(f["excerpt"] for f in c["findings"])
    # origin is stated for only one sample, so the explorer must not call it a like-for-like conflict
    assert c["assessment"] == "insufficient_context"
    assert any("geographic origin not stated" in m for m in c["missing_context"])
    assert "does not decide which report is correct" in c["interpretation_note"]
    rank = {f["value_percent"]: f["rank_in_source"] for f in c["findings"]}
    assert rank[7.9].startswith("not the highest") and rank[62.17] == "highest value in its source"


def test_contradiction_never_invents_a_cause(client):
    [c] = client.get("/api/research/contradictions").json()
    text = " ".join(_all_text(c)).lower()
    for invented in ("because of", "due to", "caused by", "is wrong", "is incorrect", "chemotype explains"):
        assert invented not in text


def test_contradictory_experimental_conditions_are_listed(client):
    [c] = client.get("/api/research/contradictions").json()
    fields = {x["field"]: x for x in c["context_comparison"]}
    assert fields["geographic origin"]["same"] is None  # unknown: not stated for two records
    assert fields["plant part"]["same"] is False  # stated for all three: leaf essential oil vs essential oil
    assert any("plant part differs" in d for d in c["documented_differences"])


def test_directly_comparable_classification(session):
    """SYNTHETIC: identical documented context, different values -> directly comparable conflict.
    Uses a plant/compound pair with no real records (thymol is only indexed for Ziziphora)."""
    p = session.scalar(select(Plant).where(Plant.accepted_name == "Capparis spinosa"))
    c = session.scalar(select(Compound).where(Compound.name == "thymol"))
    for v, pmid in ((40.0, "SYN1"), (5.0, "SYN2")):
        pub = Publication(title=f"{SYN} {pmid} Capparis spinosa", authors=[], pmid=pmid, abstract=SYN, metadata_source=SYN)
        session.add(EvidenceRecord(plant=p, compound=c, publication=pub, observation=f"Thymol at {v}% of the extract.",
                                   evidence_type="direct_experimental_identification", source_location="abstract",
                                   original_excerpt=f"thymol ({v}%)", plant_part="fruit", geographic_origin="Uzbekistan",
                                   identification_method="HPLC", verification_status="excerpt_verified"))
    session.commit()
    conflicts = [x for x in research.contradictions(session, plant_id=p.id) if x["compound"]["name"] == "thymol"]
    assert len(conflicts) == 1 and conflicts[0]["assessment"] == "directly_comparable_conflict"


# ---------------- hypotheses ----------------

def test_hypothesis_generated_from_real_evidence(client):
    r = client.post("/api/research/hypotheses", json={"plant_id": plant_id(client, "Capparis spinosa")}).json()
    [h] = r["hypotheses"]
    assert h["category"] == "activity_attribution" and h["status"] == "unvalidated_hypothesis"
    assert h["hypothesis"].startswith("Hypothesis (unvalidated): stachydrine might contribute")
    assert "Anti-arthritic" in h["hypothesis"] and "fraction" in h["hypothesis"]
    assert h["supporting_excerpts"][0]["publication"]["pmid"] == "21372539"
    assert h["missing_evidence"] and h["suggested_validation"] and h["limitations"]
    observed_ids = {i for o in r["documented_observations"] for i in o["evidence_ids"]}
    assert set(h["supporting_evidence_ids"]) <= observed_ids  # only verified observations motivate it


def test_compositional_variation_hypothesis_from_conflict(client):
    r = client.post("/api/research/hypotheses", json={"compound_id": compound_id(client, "pulegone")}).json()
    var = [h for h in r["hypotheses"] if h["category"] == "compositional_variation"]
    assert len(var) == 1 and "could reflect" in var[0]["hypothesis"]
    assert sorted(var[0]["supporting_evidence_ids"]) == [27, 32, 33]


def test_hypothesis_with_insufficient_evidence_abstains(client):
    r = client.post("/api/research/hypotheses", json={"plant_id": plant_id(client, "Ferula tadshikorum")}).json()
    assert r["hypotheses"] == [] and r["documented_observations"]
    assert "does not generate hypotheses without a documented motivating observation" in r["abstentions"][-1]


def test_unsupported_biological_activity_question_abstains(client):
    r = client.post("/api/research/hypotheses", json={"question": "Does glabridin have antioxidant activity?"}).json()
    qa = r["question_assessment"]
    assert qa["question_type"] == "biological_activity" and qa["answerable"] is False
    assert any("cannot be answered from the indexed evidence" in a for a in r["abstentions"])
    for o in r["documented_observations"]:
        assert "antioxidant" not in o["statement"].lower()
    for h in r["hypotheses"]:
        assert h["hypothesis"].startswith("Hypothesis (unvalidated):") and re.search(r"\b(may|might|could)\b", h["hypothesis"])


def test_records_failing_verification_never_become_observations(client):
    r = client.post("/api/research/hypotheses", json={"compound_id": compound_id(client, "glycyrrhizin")}).json()
    assert 20 in {i for o in r["unverified_records"] for i in o["evidence_ids"]}
    assert 20 not in {i for o in r["documented_observations"] for i in o["evidence_ids"]}
    assert all(20 not in h["supporting_evidence_ids"] for h in r["hypotheses"])
    assert all(h["category"] != "prediction_testing" for h in r["hypotheses"])


@pytest.mark.parametrize("bad,fragment", [
    ("Hypothesis (unvalidated): stachydrine cures arthritis.", "hedged"),
    ("Hypothesis (unvalidated): stachydrine may be an effective treatment for arthritis.", "therapeutic efficacy"),
    ("Hypothesis (unvalidated): stachydrine may be clinically useful in patients.", "clinical efficacy"),
    ("Hypothesis (unvalidated): stachydrine may be safe for daily use.", "safety"),
    ("Hypothesis (unvalidated): stachydrine may act here, a link that has never been studied.", "beyond the indexed dataset"),
    ("Stachydrine may contribute to the activity.", "not labelled"),
])
def test_hypothesis_guard(bad, fragment):
    reason = research.check_hypothesis(bad)
    assert reason and fragment in reason


def test_injected_hypothesis_cannot_bypass_verification(session, monkeypatch):
    """Even if a generator produced an efficacy claim, it is rejected, not returned."""
    real = research._attribution

    def poisoned(*a, **k):
        out, ab = real(*a, **k)
        out.append({**out[0], "hypothesis": "Hypothesis (unvalidated): stachydrine may cure rheumatoid arthritis in patients."})
        out.append({**out[0], "hypothesis": "Hypothesis (unvalidated): stachydrine might contribute to the activity.",
                    "supporting_evidence_ids": [987654]})
        return out, ab
    monkeypatch.setattr(research, "_attribution", poisoned)
    pid = session.scalar(select(Plant.id).where(Plant.accepted_name == "Capparis spinosa"))
    r = research.hypotheses(session, plant_id=pid)
    assert len(r["hypotheses"]) == 1 and len(r["rejected_hypotheses"]) == 2
    reasons = " ".join(x["reason"] for x in r["rejected_hypotheses"])
    assert "clinical" in reasons or "therapeutic" in reasons
    assert "do not exist" in reasons


def test_universal_claim_guard_on_outputs():
    with pytest.raises(research.ScopeViolation):
        research.assert_dataset_scoped({"a": ["fine", "this compound has never been tested for toxicity"]})
    research.assert_dataset_scoped({"a": "No toxicity evidence is currently indexed in MOLECULAB for X."})


def test_hypotheses_validation_errors(client):
    assert client.post("/api/research/hypotheses", json={}).status_code == 422
    assert client.post("/api/research/hypotheses", json={"plant_id": 0}).status_code == 422
    assert client.post("/api/research/hypotheses", json={"question": "x"}).status_code == 422
    assert client.post("/api/research/hypotheses", json={"plant_id": 9999}).status_code == 404
    assert client.post("/api/research/hypotheses", json={"compound_id": 9999}).status_code == 404
    assert client.post("/api/research/hypotheses", content="not json",
                       headers={"Content-Type": "application/json"}).status_code == 422
    r = client.post("/api/research/hypotheses", json={"question": "What is the boiling point of water?"}).json()
    assert r["hypotheses"] == [] and r["abstentions"]


# ---------------- priorities ----------------

def test_priorities_are_transparent_dimensions(client):
    pr = client.get("/api/research/priorities").json()
    assert "not a score" in pr["ordering"] and "does not estimate the likelihood" in pr["disclaimer"]
    for item in pr["items"]:
        d = item["dimensions"]
        assert set(d) == {"evidence_coverage", "evidence_quality", "independent_publications", "contradictions",
                          "missing_experimental_information", "research_feasibility"}
        assert d["research_feasibility"]["available"] is False and d["research_feasibility"]["value"] is None
        assert all(x["how"] for x in d.values())
        assert "score" not in item
    zizi = next(i for i in pr["items"] if i["name"] == "Ziziphora clinopodioides")
    assert zizi["dimensions"]["contradictions"]["value"] == 1
    assert any("unresolved conflict" in s for s in zizi["investigation_signals"])


def test_compound_with_multiple_publications(client):
    pr = client.get("/api/research/priorities", params={"kind": "compound"}).json()
    glab = next(i for i in pr["items"] if i["name"] == "glabridin")
    assert glab["dimensions"]["independent_publications"]["value"] == 2
    assert "Shared authorship" in glab["dimensions"]["independent_publications"]["how"]
    assert client.get("/api/research/priorities", params={"kind": "x"}).status_code == 422


# ---------------- empty database and failures ----------------

@pytest.fixture()
def empty_client(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    init_db(engine)
    factory = sessionmaker(bind=engine)

    def _s():
        s = factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_session] = _s
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_empty_database(empty_client):
    assert empty_client.get("/api/research/gaps").json()["total"] == 0
    assert empty_client.get("/api/research/contradictions").json() == []
    assert empty_client.get("/api/research/priorities").json()["items"] == []
    assert empty_client.post("/api/research/hypotheses", json={"question": "Does glabridin cure cancer?"}).json()["abstentions"]
    assert empty_client.get("/api/research/plants/1/gaps").status_code == 404


def test_internal_failure_returns_json_500(monkeypatch, client):
    def boom(*a, **k):
        raise RuntimeError("simulated failure")
    monkeypatch.setattr(research, "detect_gaps", boom)
    monkeypatch.setattr(research, "gaps_for", boom)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/research/gaps")
    assert r.status_code == 500 and r.json() == {"detail": "Internal server error; see server log."}


def test_v1_alias_matches(client):
    a = client.get("/api/research/gaps", params={"limit": 500}).json()
    b = client.get("/api/v1/research/gaps", params={"limit": 500}).json()
    assert a == b
