// MOLECULAB Research Bench — AI assistant panel.
//
// The assistant runs on the operator's own Claude Code CLI (app/llm.py ClaudeCodeCLIProvider),
// not an external API: no key, every tool disabled, an empty sandbox working directory.
//
// Two routes, deliberately different:
//
//   Structure  — every number the model may cite is computed by RDKit BEFORE the prompt is built,
//                and the same numbers are shown beside the answer so a reader can check it.
//   Literature — goes through the existing verified assistant: claims are extracted, each is
//                checked against the record it cites, unsupported claims are deleted, and the
//                assistant abstains when the indexed evidence cannot answer.
//
// If no model is configured the panel says so. Nothing here ever fabricates an answer.
"use strict";

const AIP = { status: null, busy: false, lastRoute: "structure" };

async function aiStatus() {
  if (AIP.status) return AIP.status;
  try {
    AIP.status = await api("/api/v1/bench/ai/status");
  } catch (err) {
    AIP.status = { available: false, reason: err.message };
  }
  return AIP.status;
}

function aiStatusLine(s) {
  if (!s.available) {
    return '<p class="note caution"><strong>No model is configured.</strong> ' +
      esc(s.reason || "") + " MOLECULAB does not simulate an answer, so the assistant is " +
      "unavailable rather than guessing.</p>";
  }
  return '<p class="sub">' + chip("derived", "AI assisted") + " Running on the local Claude Code " +
    "CLI (<span class=\"mono\">" + esc(s.model || "") + "</span>), tools disabled. The model " +
    "organises facts computed by RDKit and the evidence index; it never supplies a number of its " +
    "own.</p>";
}

// --------------------------------------------------------------- Bench stage

async function stageAssistant(host, header) {
  const s = await aiStatus();
  const sid = BENCH.session ? BENCH.session.session_id : null;

  host.innerHTML = header + aiStatusLine(s) +
    '<div class="seg" id="aiRoute" style="margin-bottom:10px">' +
    '<button data-route="literature" class="on">Ask the evidence</button>' +
    '<button data-route="structure">Ask about a structure</button>' +
    "</div>" +

    '<div id="aiLit">' +
    panel("What does the indexed evidence establish?",
      '<div class="in">' +
      '<div class="field"><textarea id="aiLq" rows="3" placeholder="Which chemical ' +
      'constituents have been reported in Glycyrrhiza glabra?"></textarea></div>' +
      '<button class="btn primary" id="aiLgo"' + (s.available ? "" : " disabled") + ">Ask</button>" +
      '<p class="sub">Every claim is checked against the record it cites. Unsupported claims are ' +
      "removed, and the assistant abstains when the evidence cannot answer.</p></div>") +
    "</div>" +

    '<div id="aiStruct" style="display:none">' +
    panel("Ask about a structure",
      '<div class="in">' +
      '<div class="field"><label for="aiSmi">SMILES</label>' +
      '<input id="aiSmi" placeholder="CC(=O)Oc1ccccc1C(=O)O"></div>' +
      '<div class="field"><textarea id="aiSq" rows="3" placeholder="What functional groups are ' +
      'present, and what would change if I replaced one?"></textarea></div>' +
      '<button class="btn primary" id="aiSgo"' + (s.available ? "" : " disabled") + ">Ask</button>" +
      '<p class="sub">RDKit computes the descriptors first. The model may only cite those ' +
      "values, and they are shown beside the answer.</p></div>") +
    "</div>" +
    '<div id="aiOut"></div>';

  // Carry a structure in from the Playground if one is to hand.
  if (typeof PG === "object" && PG.current) {
    const el = document.getElementById("aiSmi");
    if (el) el.value = PG.current.validation.canonical_smiles || PG.current.smiles;
  }

  document.querySelectorAll("#aiRoute [data-route]").forEach(function (b) {
    b.onclick = function () {
      AIP.lastRoute = b.dataset.route;
      document.querySelectorAll("#aiRoute [data-route]").forEach(function (x) {
        x.className = x.dataset.route === AIP.lastRoute ? "on" : ""; });
      document.getElementById("aiLit").style.display =
        AIP.lastRoute === "literature" ? "" : "none";
      document.getElementById("aiStruct").style.display =
        AIP.lastRoute === "structure" ? "" : "none";
    };
  });
  const lg = document.getElementById("aiLgo");
  if (lg) lg.onclick = function () { askLiterature(sid); };
  const sg = document.getElementById("aiSgo");
  if (sg) sg.onclick = function () { askStructure(sid); };
}

// --------------------------------------------------------------- literature route

async function askLiterature(sessionId) {
  const q = document.getElementById("aiLq").value.trim();
  if (q.length < 3) return;
  const out = document.getElementById("aiOut");
  aiThinking(out, "Retrieving evidence, then verifying every claim against the records it cites.");
  try {
    const d = await api("/api/v1/bench/ai/literature", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ question: q, session_id: sessionId }),
    });
    renderLiterature(out, d);
  } catch (err) { out.innerHTML = aiError(err); }
}

function renderLiterature(out, d) {
  const refs = d.references || d.citations || [];
  const rejected = d.rejected_claims || [];
  const notAnswerable = d.answerable === false || /not answerable/i.test(d.answer || "");

  out.innerHTML =
    panel(notAnswerable ? "The assistant abstained" : "Answer",
      '<div class="ai-answer">' + aiFormat(d.answer || "") + "</div>" +
      (d.verification_summary
        ? '<p class="sub">' + esc(typeof d.verification_summary === "string"
            ? d.verification_summary : JSON.stringify(d.verification_summary)) + "</p>" : "") +
      '<p class="note">' + esc(d.verification_note || "") + "</p>",
      notAnswerable ? chip("missing", "Abstained") : chip("derived", "Verified")) +

    (refs.length ? panel("Citations (" + refs.length + ")",
      refs.map(function (r) {
        return '<div class="rec">' + chip("experimental", "evidence " + esc(String(r.id || r.evidence_id || ""))) +
          (r.excerpt || r.quote ? "<blockquote>" + esc(r.excerpt || r.quote) + "</blockquote>" : "") +
          '<span class="sub">' + esc(r.source || r.publication || r.title || "") + "</span></div>";
      }).join(""), chip("experimental", "Cited")) : "") +

    (rejected.length ? panel("Removed as unsupported (" + rejected.length + ")",
      '<p class="sub">The model wrote these, and the verifier found the cited records do not ' +
      "support them, so they were deleted rather than softened.</p>" +
      rejected.map(function (c) {
        return '<div class="rec">' + chip("unverified", "removed") + "<p>" +
          esc(typeof c === "string" ? c : (c.statement || JSON.stringify(c))) + "</p></div>";
      }).join(""), chip("missing", "Deleted")) : "") +

    (d.limitations ? panel("Limitations", limitations(
      Array.isArray(d.limitations) ? d.limitations : [String(d.limitations)])) : "") +
    aiSaveBar(d.answer || "");
  wireAiSave();
}

// --------------------------------------------------------------- structure route

async function askStructure(sessionId) {
  const smiles = document.getElementById("aiSmi").value.trim();
  const q = document.getElementById("aiSq").value.trim();
  if (!smiles || q.length < 3) return;
  const out = document.getElementById("aiOut");
  aiThinking(out, "Computing descriptors with RDKit, then asking the model to explain them.");
  try {
    const d = await api("/api/v1/bench/ai/structure", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ smiles: smiles, question: q, session_id: sessionId }),
    });
    renderStructureAnswer(out, d);
  } catch (err) { out.innerHTML = aiError(err); }
}

function renderStructureAnswer(out, d) {
  const c = d.computed_context || {};
  const desc = c.computed_descriptors || {};
  out.innerHTML =
    panel("Answer",
      '<div class="ai-answer">' + aiFormat(d.answer || "") + "</div>" +
      '<p class="sub">' + esc(d.model || "") + " - " + d.duration_ms + " ms</p>",
      chip("derived", "AI assisted")) +

    panel("What the model was given",
      '<p class="sub">These are the only numbers the assistant was allowed to cite. Check the ' +
      "answer against them.</p>" +
      '<table class="kv">' +
      "<tr><th>Canonical SMILES</th><td><span class=\"mono wrap\">" +
      esc(c.canonical_smiles || "") + "</span></td></tr>" +
      "<tr><th>InChIKey</th><td><span class=\"mono\">" + esc(c.inchikey || "-") + "</span></td></tr>" +
      "<tr><th>Scaffold</th><td><span class=\"mono wrap\">" +
      esc(c.murcko_scaffold || "(acyclic)") + "</span></td></tr>" +
      Object.keys(desc).map(function (k) {
        return "<tr><th>" + esc(k.replace(/_/g, " ")) + "</th><td>" + esc(String(desc[k])) +
          "</td></tr>"; }).join("") +
      "<tr><th>Fingerprint</th><td><span class=\"mono\">" +
      esc((c.descriptor_fingerprint || "").slice(0, 24)) + "</span></td></tr></table>",
      chip("computed", "Computed by RDKit")) +

    panel("What does not exist for this molecule",
      limitations(c.what_is_not_available || []), chip("missing", "No data")) +

    panel("Limitations", limitations(d.limitations || [])) +
    aiSaveBar(d.answer || "");
  wireAiSave();
}

// --------------------------------------------------------------- shared

function aiThinking(out, detail) {
  out.innerHTML = '<div class="state"><span class="g">&#8987;</span>' +
    "<h4>Working...</h4><p>" + esc(detail) + "</p>" +
    '<p class="sub">The assistant runs on the local Claude Code CLI, which starts a fresh ' +
    "process for each question. This usually takes 10 to 30 seconds.</p></div>";
}

function aiError(err) {
  const msg = String(err.message || err);
  if (/503/.test(msg) || /no model/i.test(msg)) {
    return panel("The assistant is unavailable",
      '<p class="limit">' + esc(msg) + "</p>" +
      '<p class="sub">MOLECULAB does not produce a simulated answer when no model is available. ' +
      "Every other capability on the Bench is unaffected.</p>", chip("missing", "Unavailable"));
  }
  if (/timed out/i.test(msg)) {
    return panel("The assistant timed out",
      '<p class="limit">' + esc(msg) + "</p>" +
      '<p class="sub">No partial answer is shown, because a truncated answer cannot be ' +
      "verified. Try a shorter question.</p>", chip("missing", "Timed out"));
  }
  return errorState("The assistant failed", msg);
}

// The model is told to use five fixed headings; render them as headings.
const AI_HEADINGS = ["WHAT WE KNOW", "WHAT THE CALCULATIONS INDICATE", "WHAT REMAINS UNCERTAIN",
                     "POSSIBLE RESEARCH DIRECTIONS", "RECOMMENDED NEXT INVESTIGATION"];

function aiFormat(text) {
  let html = esc(text);
  AI_HEADINGS.forEach(function (h) {
    html = html.split(h).join('</p><h4 class="ai-h">' + h.toLowerCase().replace(/^./, function (m) {
      return m.toUpperCase(); }) + "</h4><p>");
  });
  html = html.replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>");
  return "<p>" + html + "</p>";
}

function aiSaveBar(answer) {
  if (!BENCH.session) return "";
  return panel("Keep this",
    '<p class="sub">Saving records this as <strong>your</strong> note, marked as AI-assisted. ' +
    "Nothing is written to the investigation unless you choose to save it.</p>" +
    '<div class="row gap">' +
    '<button class="btn" id="aiSaveNote">Save as a research note</button>' +
    '<button class="btn" id="aiSaveHyp">Save as an unvalidated hypothesis</button></div>' +
    '<div id="aiSaveOut"></div>');
}

function wireAiSave() {
  const answer = (document.querySelector(".ai-answer") || {}).textContent || "";
  const n = document.getElementById("aiSaveNote");
  const h = document.getElementById("aiSaveHyp");
  if (n) n.onclick = function () { aiSave("note", answer, n); };
  if (h) h.onclick = function () { aiSave("hypothesis", answer, h); };
}

async function aiSave(kind, answer, btn) {
  if (!BENCH.session) return;
  btn.disabled = true;
  const id = BENCH.session.session_id;
  const body = "AI-assisted (local Claude Code CLI). Reviewed and kept by the researcher.\n\n" +
    answer.trim();
  try {
    if (kind === "note") {
      await api("/api/workspaces/" + id + "/notes", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ category: "interpretation", body: body }) });
    } else {
      await api("/api/workspaces/" + id + "/hypotheses", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ origin: "generated", statement: body.slice(0, 1800) }) });
    }
    document.getElementById("aiSaveOut").innerHTML =
      '<p class="sub">' + chip("experimental", "saved") + " Recorded in investigation #" + id +
      (kind === "hypothesis" ? " as an unvalidated hypothesis." : " as a research note.") + "</p>";
    await refreshSession();
  } catch (err) {
    btn.disabled = false;
    document.getElementById("aiSaveOut").innerHTML = '<p class="limit">' + esc(err.message) + "</p>";
  }
}

// --------------------------------------------------------------- Playground panel

async function pgAiPanel() {
  const host = document.getElementById("pgAi");
  if (!host) return;
  const s = await aiStatus();
  if (!s.available) {
    host.innerHTML = '<p class="sub">No model configured, so the assistant is unavailable. ' +
      "Everything else here still works.</p>";
    return;
  }
  host.innerHTML =
    '<div class="pgrid">' +
    '<button class="chipb" data-aiq="What functional groups are present?">Groups</button>' +
    '<button class="chipb" data-aiq="How would replacing one functional group change the ' +
    'computed descriptors?">Modify</button>' +
    '<button class="chipb" data-aiq="What should I investigate next about this structure?">' +
    "Next</button></div>" +
    '<div class="field" style="margin-top:6px">' +
    '<textarea id="pgAiQ" rows="2" placeholder="ask about this structure"></textarea></div>' +
    '<button class="btn primary" id="pgAiGo" style="width:100%">Ask</button>' +
    '<div id="pgAiOut"></div>';

  const ask = async function (q) {
    if (!PG.current) {
      document.getElementById("pgAiOut").innerHTML =
        '<p class="sub">Draw a valid structure first.</p>';
      return;
    }
    const out = document.getElementById("pgAiOut");
    out.innerHTML = '<p class="sub">Asking... (10-30s)</p>';
    try {
      const d = await api("/api/v1/bench/ai/structure", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          smiles: PG.current.validation.canonical_smiles || PG.current.smiles,
          question: q, session_id: PG.sessionId ? Number(PG.sessionId) : null }),
      });
      out.innerHTML = '<div class="ai-answer sm">' + aiFormat(d.answer || "") + "</div>" +
        '<p class="sub">' + esc(d.model || "") + " - " + d.duration_ms + " ms. The model was " +
        "given only the computed descriptors shown above.</p>";
    } catch (err) {
      out.innerHTML = '<p class="limit">' + esc(String(err.message || err)) + "</p>";
    }
  };
  host.querySelectorAll("[data-aiq]").forEach(function (b) {
    b.onclick = function () { ask(b.dataset.aiq); };
  });
  document.getElementById("pgAiGo").onclick = function () {
    const q = document.getElementById("pgAiQ").value.trim();
    if (q.length >= 3) ask(q);
  };
}
