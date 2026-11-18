"""Build the v0.5 demonstration investigation on a RUNNING server through the public API.

    python -m scripts.demo_capparis [http://127.0.0.1:8765]

Uses only existing indexed records. Everything it prints comes from the API responses. Re-running
creates a second investigation; delete old ones from the Workspaces page (archive, then delete).
"""
import sys

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765").rstrip("/") + "/api/workspaces"


def main() -> None:
    c = httpx.Client(timeout=60)

    def call(method, path, **kw):
        r = c.request(method, BASE + path, **kw)
        r.raise_for_status()
        return r.json() if r.content else None

    plants = c.get(BASE.replace("/api/workspaces", "/api/plants")).json()
    pid = next(p["id"] for p in plants if p["accepted_name"] == "Capparis spinosa")
    ws = call("POST", "", json={
        "title": "Chemical composition and research gaps in Capparis spinosa",
        "research_question": "Which chemical constituents of Capparis spinosa are documented in the indexed literature, "
                             "and where is the indexed evidence incomplete?",
        "research_domain": "phytochemistry", "scope": "Capparis spinosa; MOLECULAB-indexed PubMed abstracts",
        "status": "active"})
    wid = ws["id"]
    print(f"1-2. investigation #{wid}: {ws['title']}")
    added = call("POST", f"/{wid}/collect-plant", json={"plant_id": pid, "include": ["compounds", "publications", "evidence"]})
    print(f"3-5. collected: {added['added']}")
    gaps = c.get(BASE.replace("/api/workspaces", f"/api/research/plants/{pid}/gaps")).json()["items"]
    tox = next(g for g in gaps if g["category"] == "missing_toxicity_evidence")
    call("POST", f"/{wid}/resources", json={"resource_type": "gap", "resource_ref": tox["id"],
                                            "researcher_note": "Plan a toxicology literature search before any activity work."})
    print(f"6.   gap collected: {tox['explanation']}")
    conflicts = c.get(BASE.replace("/api/workspaces", f"/api/research/plants/{pid}/contradictions")).json()
    print(f"7.   contradictions for this plant: {len(conflicts)}"
          + ("" if conflicts else " (none detected in the indexed records; the report will say so)"))
    runs = call("POST", f"/{wid}/hypotheses/generate", json={})
    cand = runs[0]["hypotheses"]
    if cand:
        h = call("POST", f"/{wid}/hypotheses", json={"origin": "generated", "statement": cand[0]["hypothesis"],
                                                     "source": runs[0]["source"]})
        print(f"8.   hypothesis saved (causal attribution: {h['causal_attribution']}): {h['statement']}")
    else:
        print("8.   no eligible hypothesis: " + "; ".join(runs[0]["abstentions"]))
    call("POST", f"/{wid}/tasks", json={"title": "Search toxicology literature for Capparis spinosa", "priority": "high",
                                        "related_resources": [{"resource_type": "gap", "resource_ref": tox["id"]}]})
    print("9.   task created")
    call("POST", f"/{wid}/notes", json={"category": "question",
                                        "body": "Is the anti-arthritic activity of the fruit fraction attributable to "
                                                "stachydrine, to other constituents, or to their combination?"})
    print("10.  note recorded")
    rep = call("POST", f"/{wid}/reports")
    print(f"11.  report #{rep['id']}: {rep['summary']}  sha256 {rep['dataset_fingerprint']['content_sha256'][:16]}…")
    print(f"     open {BASE.replace('/api/workspaces', '')}/#/workspace/{wid}  ·  export {BASE}/{wid}/reports/{rep['id']}/export")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy code page
    main()
