from tests.conftest import compound_id, plant_id


def test_startup_and_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["database"] == "ok"


def test_database_initialised_with_seed(client):
    s = client.get("/api/stats").json()
    assert s["plants"] == 5
    assert s["evidence_records"] == 37
    assert s["evidence_by_verification"] == {"excerpt_verified": 37}
    assert s["compounds_without_identifier"] == 1


def test_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "MOLECULAB" in r.text
    assert client.get("/static/app.js").status_code == 200


def test_plant_read(client):
    pid = plant_id(client, "Ziziphora clinopodioides")
    p = client.get(f"/api/plants/{pid}").json()
    assert p["family"] == "Lamiaceae"
    assert {"pulegone", "carvacrol", "thymol"} <= {c["name"] for c in p["compounds"]}
    assert all(e["plant"]["id"] == pid for e in p["evidence"])


def test_plant_gaps_flag_variation_and_traditional_use(client):
    p = client.get(f"/api/plants/{plant_id(client, 'Ziziphora clinopodioides')}").json()
    rules = {(g["rule"], g["compound"]) for g in p["gaps"]}
    assert ("possible_variation", "pulegone") in rules
    assert ("traditional_use_unvalidated", None) in rules


def test_unresolved_structure_gap(client):
    p = client.get(f"/api/plants/{plant_id(client, 'Ferula tadshikorum')}").json()
    assert ("unresolved_structure", "8-acetoxy-5-hydroxyumbelliprenin") in {(g["rule"], g["compound"]) for g in p["gaps"]}
    assert ("no_regional_names", None) in {(g["rule"], g["compound"]) for g in p["gaps"]}


def test_missing_records_return_404(client):
    for path in ("/api/plants/9999", "/api/compounds/9999", "/api/publications/9999", "/api/evidence/9999"):
        assert client.get(path).status_code == 404


def test_search_multilingual_and_identifiers(client):
    def plants(q):
        return [p["accepted_name"] for p in client.get("/api/search", params={"q": q}).json()["plants"]]

    assert plants("Каперсы") == ["Capparis spinosa"]
    assert plants("каперсы") == ["Capparis spinosa"]  # case-insensitive Cyrillic
    assert plants("kiyiko’t") == ["Ziziphora clinopodioides"]  # curly apostrophe
    assert plants("licorice") == ["Glycyrrhiza glabra"]
    r = client.get("/api/search", params={"q": "5280343"}).json()
    assert [c["name"] for c in r["compounds"]] == ["quercetin"]
    r = client.get("/api/search", params={"q": "10.3390/jfb15040095"}).json()
    assert len(r["publications"]) == 1
    r = client.get("/api/search", params={"q": "38667552"}).json()
    assert r["publications"][0]["pmid"] == "38667552"


def test_search_nothing_found(client):
    r = client.get("/api/search", params={"q": "zzzz-no-such-thing"}).json()
    assert not (r["plants"] or r["compounds"] or r["publications"] or r["evidence"])


def test_invalid_requests(client, monkeypatch):
    assert client.get("/api/search", params={"q": "a"}).status_code == 422
    assert client.get("/api/search").status_code == 422
    assert client.get("/api/evidence", params={"evidence_type": "bogus"}).status_code == 422
    assert client.get("/api/plants/not-a-number").status_code == 422
    assert client.post("/api/assistant", json={"question": ""}).status_code == 422
    monkeypatch.setenv("REVIEWER_TOKENS", "Dr Test=0123456789abcdef-token")
    assert client.post("/api/evidence/1/reviews", json={"review_status": "maybe"},
                       headers={"Authorization": "Bearer 0123456789abcdef-token"}).status_code == 422


def test_compound_retrieval_and_provenance(client):
    c = client.get(f"/api/compounds/{compound_id(client, 'glabridin')}").json()
    assert c["pubchem_cid"] == 124052
    assert c["inchikey"] == "LBQIJVLKGVZRIW-ZDUSSCGKSA-N"
    assert c["identifier_source"].startswith("https://pubchem.ncbi.nlm.nih.gov/")
    assert {e["publication"]["pmid"] for e in c["evidence"]} == {"38667552", "42513095"}


def test_evidence_relationships_and_types(client):
    rows = client.get("/api/evidence").json()
    assert len(rows) == 37
    for e in rows:
        assert e["publication"]["pmid"] and e["original_excerpt"]
        assert e["verification_status"] == "excerpt_verified"
        # v0.2: AI curation is recorded as interpretation, never as a review; nobody has reviewed yet.
        assert e["reviews"] == [] and e["review_status"] == "pending_expert_review"
        assert e["interpretation_status"] == "ai_assisted_interpretation"
    comp = [e for e in rows if e["evidence_type"] == "computational_prediction"]
    assert len(comp) == 1 and "Docking" in comp[0]["curator_notes"]
    trad = client.get("/api/evidence", params={"evidence_type": "traditional_use"}).json()
    assert len(trad) == 2 and all(e["compound"] is None for e in trad)


def test_evidence_filters(client):
    pid = plant_id(client, "Capparis spinosa")
    rows = client.get("/api/evidence", params={"plant_id": pid, "year_from": 2025}).json()
    assert rows and all(e["plant"]["id"] == pid and e["publication"]["year"] >= 2025 for e in rows)
    assert client.get("/api/evidence", params={"q": "62.17"}).json()[0]["compound"]["name"] == "pulegone"


def test_add_review_requires_configured_reviewer(client, monkeypatch):
    # v0.1 accepted anonymous reviews; v0.2 must refuse them.
    monkeypatch.delenv("REVIEWER_TOKENS", raising=False)
    body = {"review_status": "expert_confirmed", "review_notes": "Checked full text."}
    assert client.post("/api/evidence/1/reviews", json=body).status_code == 403
    monkeypatch.setenv("REVIEWER_TOKENS", "Dr Test=0123456789abcdef-token")
    assert client.post("/api/evidence/1/reviews", json=body).status_code == 401
    assert client.post("/api/evidence/1/reviews", json=body,
                       headers={"Authorization": "Bearer wrong-token-0000000"}).status_code == 401
    r = client.post("/api/evidence/1/reviews", json={**body, "reviewer": "Spoofed Name"},
                    headers={"Authorization": "Bearer 0123456789abcdef-token"})
    assert r.status_code == 201 and r.json()["reviewer"] == "Dr Test"
    e = client.get("/api/evidence/1").json()
    assert e["review_status"] == "expert_confirmed" and e["reviews"][-1]["reviewer"] == "Dr Test"
    assert client.post("/api/evidence/9999/reviews", json=body,
                       headers={"Authorization": "Bearer 0123456789abcdef-token"}).status_code == 404
