import json

from app import assistant
from app.llm import LLMError


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete(self, system, user):
        self.calls.append(user)
        if self.error:
            raise self.error
        return self.reply(user) if callable(self.reply) else self.reply


def test_no_evidence_means_no_answer(session):
    out = assistant.answer(session, "neutron star equations of state", provider=FakeProvider("{}"))
    assert out["mode"] == "no_evidence" and out["claims"] == [] and out["answerable"] is False


def test_retrieval_only_without_provider(session):
    out = assistant.answer(session, "Which compounds are reported in Capparis spinosa?", provider=None)
    assert out["mode"] == "retrieval_only" and out["llm_used"] is False
    assert out["claims"] and all(len(c["evidence_ids"]) == 1 for c in out["claims"])
    assert set(out["retrieved_evidence_ids"]) >= {c["evidence_ids"][0] for c in out["claims"]}
    assert all(r["plant"]["accepted_name"] == "Capparis spinosa" for r in out["references"])


def test_traditional_use_is_labelled_in_extractive_answer(session):
    out = assistant.answer(session, "traditional use of Ziziphora clinopodioides", provider=None)
    assert out["claims"] and all(c["statement"].startswith("Traditional use reported") for c in out["claims"])


def test_llm_answer_with_fabricated_citation_is_filtered(session):
    def reply(user):
        ids = [r["id"] for r in json.loads(user.split("Evidence records:\n", 1)[1])]
        return json.dumps({"claims": [
            {"statement": "Glabridin was isolated from Glycyrrhiza glabra.", "evidence_ids": ids[:1]},
            {"statement": "Glabridin occurs in Glycyrrhiza glabra.", "evidence_ids": [987654]},
        ]})

    out = assistant.answer(session, "Is glabridin found in Glycyrrhiza glabra?", provider=FakeProvider(reply))
    assert out["mode"] == "llm_grounded" and out["llm_used"]
    assert [c["statement"] for c in out["claims"]] == ["Glabridin was isolated from Glycyrrhiza glabra."]
    assert "do not exist" in out["rejected_claims"][0]["reason"]
    assert {r["id"] for r in out["references"]} == set(out["claims"][0]["evidence_ids"])


def test_llm_failure_falls_back_to_retrieval(session):
    out = assistant.answer(session, "artemisinin", provider=FakeProvider(error=LLMError("boom")))
    assert out["mode"] == "retrieval_only" and "boom" in out["message"] and out["claims"]


def test_llm_non_json_output_falls_back(session):
    out = assistant.answer(session, "artemisinin", provider=FakeProvider("I think it's great!"))
    assert out["mode"] == "retrieval_only" and out["llm_used"] is False


def test_assistant_endpoint_reports_no_llm(client, monkeypatch):
    """With no provider configured, the endpoint must say so and still answer from records.

    The provider is forced off here rather than inherited from the machine's .env: a developer who
    has configured a provider locally must not silently skip this path.
    """
    monkeypatch.setattr("app.main.get_provider", lambda: None)
    r = client.post("/api/assistant", json={"question": "pulegone content in Ziziphora clinopodioides"})
    body = r.json()
    assert r.status_code == 200 and body["llm_used"] is False and body["claims"]
    assert body["mode"] == "retrieval_only"


def test_assistant_endpoint_uses_a_configured_provider(client, monkeypatch):
    """Mirror case: when a provider IS configured, the endpoint reports the generation honestly
    and the drafted claims are still verified against cited records."""
    monkeypatch.setattr("app.main.get_provider",
                        lambda: FakeProvider('{"claims": [{"statement": "Pulegone has been reported '
                                             'in Ziziphora clinopodioides.", "evidence_ids": [30]}]}'))
    body = client.post("/api/assistant",
                       json={"question": "pulegone content in Ziziphora clinopodioides"}).json()
    assert body["llm_used"] is True and body["llm_provider"] == "fake"
    assert body["mode"] == "llm_grounded"
    # Verification still ran: every surviving claim carries the evidence it was checked against.
    assert all(c["evidence_ids"] for c in body["claims"])
