/* Research Workbench: one investigation, opened once, worked inside across every view.
 * Aggregates real records from /api/v1/workbench/investigations/{id}/overview. Nothing is invented: an empty
 * section is shown as empty. Globals used: esc, chip, panel, tile, skeleton, emptyState, errorState, limitations.
 */
var WB_API = "/api/v1/workbench";
var WB_WS = "/api/workspaces";
var WB_TABS = [["overview", "Overview"], ["evidence", "Evidence"], ["compounds", "Compounds"],
  ["formulations", "Formulations"], ["experiments", "Experiments"], ["hypotheses", "Hypotheses"],
  ["notes", "Notes"], ["board", "Board"], ["reports", "Reports"]];
var WB_NOTE_CATS = ["observation", "question", "methodology", "interpretation", "limitation", "decision"];
var wbState = { id: null, tab: "overview", sel: null, data: null, saved: "", msg: "", err: "" };

function wbToken() {
  try { return (typeof getToken === "function") ? getToken() : ""; } catch (e) { return ""; }
}

async function wbFetch(path, method, body) {
  var headers = {};
  var t = wbToken();
  if (t) headers["Authorization"] = "Bearer " + t;
  if (body) headers["Content-Type"] = "application/json";
  var res = await fetch(path, { method: method || "GET", headers: headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 204) return null;
  var j = await res.json().catch(function () { return {}; });
  if (!res.ok) {
    var d = j.detail;
    var m = typeof d === "string" ? d : (d && d.message ? d.message + (d.accepted_ref_types ? " (accepted: " + d.accepted_ref_types.join(", ") + ")" : "") : JSON.stringify(d || j));
    throw new Error(m);
  }
  return j;
}

function wbMain() { return document.getElementById("view"); }

function wbRemember() {
  try { sessionStorage.setItem("moleculab_workbench", JSON.stringify({ id: wbState.id, tab: wbState.tab })); } catch (e) { /* ignore */ }
}

function wbRestore() {
  try {
    var s = JSON.parse(sessionStorage.getItem("moleculab_workbench") || "null");
    if (s && s.id && !wbState.id) { wbState.id = s.id; wbState.tab = s.tab || "overview"; }
  } catch (e) { /* ignore */ }
}

function wbUrl() {
  return "#/workbench" + (wbState.id ? "?id=" + wbState.id + "&tab=" + wbState.tab : "");
}

function wbSyncUrl() {
  // replaceState does not fire hashchange, so switching tabs never re-routes or reloads the investigation.
  try { history.replaceState(null, "", wbUrl()); } catch (e) { /* ignore */ }
  wbRemember();
}

async function workbenchPage(params) {
  var main = wbMain();
  main.classList.add("workstation");
  wbRestore();
  var qid = params && params.get ? params.get("id") : null;
  var qtab = params && params.get ? params.get("tab") : null;
  if (qid) {
    if (String(wbState.id) !== String(qid)) { wbState.sel = null; wbState.data = null; }
    wbState.id = Number(qid);
  }
  if (qtab) wbState.tab = qtab;
  if (wbState.id) { await wbOpen(wbState.id); } else { await wbList(); }
}

async function wbList() {
  var main = wbMain();
  main.classList.add("workstation");
  wbState.id = null; wbState.data = null; wbState.sel = null;
  wbRemember();
  main.innerHTML = '<div class="wx-head"><div><div class="eyebrow">Research workbench</div><h1>Investigations</h1></div>' +
    '<div class="wx-sub">Open one investigation and work inside it: evidence, compounds, formulations, experiments, hypotheses, notes, board and reports share one context.</div></div>' +
    '<div class="wx-body wx-2"><div class="wx-col" id="wbnew"></div><div class="wx-main scrolls" id="wblist">' + skeleton(5) + "</div></div>";
  document.getElementById("wbnew").innerHTML = wbNewForm();
  try {
    var list = await wbFetch(WB_API + "/investigations");
    document.getElementById("wblist").innerHTML = list.length
      ? '<div style="padding:var(--s-3)">' + panel("Investigations (" + list.length + ")", list.map(function (w) {
          var c = w.counts;
          return '<a class="pick" href="#/workbench?id=' + w.id + '"><strong>' + esc(w.title) + "</strong>" +
            '<span class="meta">' + esc(w.status.replace(/_/g, " ")) + " · " + c.resources + " resources · " + c.hypotheses +
            " hypotheses · " + c.notes + " notes · " + c.formulations + " formulations · " + c.experiments + " experiments · " +
            c.boards + " boards</span><span class=\"meta\">" + esc(w.research_question) + "</span></a>";
        }).join("")) + "</div>"
      : emptyState("No investigations yet", "Create the first one on the left. Your research question frames everything attached to it.");
  } catch (err) {
    document.getElementById("wblist").innerHTML = errorState("Could not load investigations", err.message);
  }
}

function wbNewForm() {
  return '<div class="wx-sec"><h4>New investigation</h4><div class="in">' +
    '<input id="wbt" placeholder="Title" minlength="3" style="width:100%">' +
    '<textarea id="wbq" placeholder="Research question (at least 10 characters)" style="width:100%"></textarea>' +
    '<input id="wbd" placeholder="Domain (optional)" style="width:100%">' +
    '<div><button class="btn primary" onclick="wbCreate()">Create investigation</button></div>' +
    '<div class="note" id="wbcmsg"></div></div></div>';
}

async function wbCreate() {
  var body = { title: document.getElementById("wbt").value.trim(), research_question: document.getElementById("wbq").value.trim() };
  var dom = document.getElementById("wbd").value.trim();
  if (dom) body.research_domain = dom;
  try {
    var w = await wbFetch(WB_WS, "POST", body);
    wbState.id = w.id; wbState.tab = "overview"; wbState.sel = null;
    wbSyncUrl();
    await wbOpen(w.id);
  } catch (err) {
    document.getElementById("wbcmsg").textContent = err.message;
  }
}

async function wbOpen(id) {
  wbState.id = Number(id);
  var main = wbMain();
  main.classList.add("workstation");
  if (!wbState.data) main.innerHTML = '<div class="wx-body"><div class="wx-main scrolls">' + skeleton(6) + "</div></div>";
  try {
    wbState.data = await wbFetch(WB_API + "/investigations/" + wbState.id + "/overview");
  } catch (err) {
    wbRemember();
    main.innerHTML = '<div class="wx-body"><div class="wx-main scrolls" style="padding:var(--s-4)">' +
      errorState("Could not open investigation " + id, err.message,
        '<a class="btn" href="#/workbench" onclick="wbClose()">Back to investigations</a>') + "</div></div>";
    return;
  }
  wbSyncUrl();
  wbRender();
}

function wbClose() {
  wbState.id = null; wbState.data = null; wbState.sel = null; wbRemember();
}

async function wbRefresh(msg) {
  try {
    wbState.data = await wbFetch(WB_API + "/investigations/" + wbState.id + "/overview");
    wbState.saved = msg || "Saved";
    wbState.err = "";
  } catch (err) { wbState.err = err.message; }
  wbRender();
}

async function wbDo(fn, okMsg) {
  try { await fn(); await wbRefresh(okMsg); } catch (err) { wbState.err = err.message; wbRender(); }
}

function wbTab(k) {
  wbState.tab = k; wbState.sel = null; wbState.err = "";
  wbSyncUrl();
  wbRender();
}

function wbSelect(kind, id) {
  wbState.sel = { kind: kind, id: id };
  wbRender();
}

/* ------------------------------------------------------------------ render */

function wbRender() {
  var d = wbState.data;
  if (!d) return;
  var w = d.workspace, c = d.counts;
  var main = wbMain();
  main.classList.add("workstation");
  var head = '<div class="wx-head"><div><div class="eyebrow">Investigation</div><h1>' + esc(w.title) + "</h1></div>" +
    '<div class="wx-sub">' + esc(w.research_question) + "</div>" +
    '<div class="right"><span class="chip">' + esc(w.status.replace(/_/g, " ")) + "</span>" +
    '<span class="muted" id="wbsave">' + esc(wbState.err ? "" : wbState.saved) + "</span>" +
    (wbState.err ? '<span class="note">' + esc(wbState.err) + "</span>" : "") +
    '<button class="btn ghost" onclick="wbRefresh(\'Refreshed\')">Refresh</button>' +
    '<button class="btn" onclick="wbReport()"' + (d.read_only ? " disabled" : "") + ">Generate report</button>" +
    '<a class="btn ghost" href="#/workbench" onclick="wbClose()">All investigations</a></div></div>';
  var strip = '<div class="wx-strip seg">' + WB_TABS.map(function (t) {
    return '<button class="' + (t[0] === wbState.tab ? "active" : "") + '" onclick="wbTab(\'' + t[0] + '\')">' + t[1] + "</button>";
  }).join("") + "</div>";
  main.innerHTML = head + strip + '<div class="wx-body wx-3"><div class="wx-col">' + wbNavigator() +
    '</div><div class="wx-main scrolls" style="padding:var(--s-3)">' + wbView() +
    '</div><div class="wx-col">' + wbInspector() + "</div></div>";
}

function wbNavigator() {
  var c = wbState.data.counts, bt = c.resources_by_type || {};
  var rows = [["overview", "Overview", ""], ["evidence", "Evidence", bt.evidence || 0], ["compounds", "Compounds", bt.compound || 0],
    ["formulations", "Formulations", c.formulations], ["experiments", "Experiments", c.experiments],
    ["hypotheses", "Hypotheses", c.hypotheses], ["notes", "Notes", c.notes], ["board", "Boards", c.boards],
    ["reports", "Reports", c.reports]];
  var h = '<div class="wx-sec"><h4>Investigation</h4><div class="in">' + rows.map(function (r) {
    return '<a class="pick" href="javascript:void(0)" onclick="wbTab(\'' + r[0] + '\')"><strong>' + r[1] + "</strong>" +
      (r[2] !== "" ? '<span class="meta">' + r[2] + (r[2] === 0 ? " (empty)" : "") + "</span>" : "") + "</a>";
  }).join("") + "</div></div>";
  var other = Object.keys(bt).filter(function (k) { return ["evidence", "compound"].indexOf(k) < 0; });
  if (other.length) {
    h += '<div class="wx-sec"><h4>Other attached</h4><div class="in">' + other.map(function (k) {
      return '<div class="kv"><span>' + esc(k) + "</span><b>" + bt[k] + "</b></div>";
    }).join("") + "</div></div>";
  }
  h += '<div class="wx-sec"><h4>Open tasks</h4><div class="in">' + (c.outstanding_tasks ? c.outstanding_tasks + " outstanding of " + c.tasks : '<span class="muted">none</span>') + "</div></div>";
  return h;
}

function wbView() {
  var t = wbState.tab;
  if (t === "evidence") return wbResourceView("evidence", "Evidence records", true);
  if (t === "compounds") return wbResourceView("compound", "Compounds", true);
  if (t === "formulations") return wbFormulations();
  if (t === "experiments") return wbExperiments();
  if (t === "hypotheses") return wbHypotheses();
  if (t === "notes") return wbNotes();
  if (t === "board") return wbBoards();
  if (t === "reports") return wbReports();
  return wbOverview();
}

function wbOverview() {
  var d = wbState.data, c = d.counts, w = d.workspace;
  var tiles = '<div class="tiles">' + tile("Resources", c.resources) + tile("Hypotheses", c.hypotheses) + tile("Notes", c.notes) +
    tile("Formulations", c.formulations) + tile("Experiments", c.experiments) + tile("Boards", c.boards) + tile("Reports", c.reports) + "</div>";
  var scope = panel("Investigation", '<div class="kv"><span>Domain</span><b>' + esc(w.research_domain || "not set") + "</b></div>" +
    '<div class="kv"><span>Scope</span><b>' + esc(w.scope || "not set") + "</b></div>" +
    '<div class="kv"><span>Description</span><b>' + esc(w.description || "not set") + "</b></div>");
  var oq = d.open_questions.length
    ? d.open_questions.map(function (q) {
        return '<div class="kv"><span>' + chip(q.kind === "violated_mandatory_constraint" || q.kind === "contradiction" ? "contradiction" : (q.kind === "researcher_question" ? "hypothesis" : "missing"), q.kind.replace(/_/g, " ")) +
          "</span><b>" + (q.link ? '<a href="' + esc(q.link) + '">' + esc(q.text) + "</a>" : esc(q.text)) + "</b></div>";
      }).join("")
    : '<span class="muted">No open questions derived. This reflects only constraints, attached gaps/contradictions and notes categorised as question.</span>';
  var act = d.activity.length
    ? d.activity.slice(0, 12).map(function (a) {
        return '<div class="kv"><span class="mono">' + esc((a.at || "").slice(0, 16).replace("T", " ")) + "</span><b>" + esc(a.action.replace(/_/g, " ")) + ": " + esc(a.detail) + "</b></div>";
      }).join("")
    : '<span class="muted">No activity yet.</span>';
  return tiles + panel("Open questions (" + d.open_questions.length + ")", oq) + scope + panel("Recent activity", act) +
    limitations(["Hypotheses are unvalidated. Notes are researcher-authored, not verified evidence. Attached records are references, not copies."]);
}

function wbAttachForm(type) {
  return '<div class="wx-sec"><h4>Attach existing ' + esc(type) + '</h4><div class="in">' +
    '<div id="wbpick"><input id="wbref" placeholder="' + (type === "compound" ? "Compound id (or choose below)" : "Record id") + '" style="width:100%"></div>' +
    (type === "compound" ? '<select id="wbcsel" onchange="document.getElementById(\'wbref\').value=this.value" style="width:100%"><option value="">Loading compounds...</option></select>' : "") +
    '<input id="wbrn" placeholder="Researcher note (optional)" style="width:100%">' +
    '<div><button class="btn primary" onclick="wbAttach(\'' + type + '\')">Attach</button></div></div></div>';
}

async function wbLoadCompounds() {
  var sel = document.getElementById("wbcsel");
  if (!sel) return;
  try {
    var list = await wbFetch("/api/compounds");
    sel.innerHTML = '<option value="">Choose a compound</option>' + list.map(function (c) {
      return '<option value="' + c.id + '">' + esc(c.name) + "</option>";
    }).join("");
  } catch (err) { sel.innerHTML = '<option value="">Compounds unavailable</option>'; }
}

async function wbAttach(type) {
  var ref = document.getElementById("wbref").value.trim();
  var note = document.getElementById("wbrn").value.trim();
  if (!ref) { wbState.err = "Enter or choose a record id"; wbRender(); return; }
  var body = { ref_type: type, ref_id: ref };
  if (note) body.note = note;
  await wbDo(function () { return wbFetch(WB_API + "/investigations/" + wbState.id + "/attach", "POST", body); }, "Attached");
}

async function wbDetach(rid) {
  wbState.sel = null;
  await wbDo(function () { return wbFetch(WB_API + "/investigations/" + wbState.id + "/attach/" + rid, "DELETE"); }, "Detached (record itself untouched)");
}

function wbResourceView(type, title) {
  var d = wbState.data;
  var items = d.resources_by_type[type] || [];
  var list = items.length
    ? items.map(function (r) {
        return '<a class="pick" href="javascript:void(0)" onclick="wbSelect(\'resource\',' + r.id + ')"><strong>' + esc(r.label) + "</strong>" +
          '<span class="meta">' + (r.present_in_current_data ? "" : "no longer in current data · ") + esc(r.researcher_note || "") + "</span></a>";
      }).join("")
    : emptyState("Nothing attached", "No " + type + " records are attached to this investigation yet.");
  var html = panel(title + " (" + items.length + ")", list) + wbAttachForm(type);
  if (type === "compound") setTimeout(wbLoadCompounds, 0);
  return html;
}

function wbFormulations() {
  var f = wbState.data.formulations;
  return panel("Formulations (" + f.length + ")", f.length ? f.map(function (x) {
    return '<a class="pick" href="javascript:void(0)" onclick="wbSelect(\'formulation\',' + x.id + ')"><strong>' + esc(x.name) + "</strong>" +
      '<span class="meta">' + esc(x.dosage_form) + " · " + esc(x.status) + " · " + x.constraint_count + " constraints · " +
      x.unresolved_mandatory.length + " unresolved mandatory</span></a>";
  }).join("") : emptyState("No formulations linked", "Create formulations on the Formulations page with this investigation selected.",
    '<a class="btn" href="#/formulations">Open formulations</a>'));
}

function wbExperiments() {
  var e = wbState.data.experiments;
  return panel("Experiment plans (" + e.length + ")", e.length ? e.map(function (x) {
    return '<a class="pick" href="javascript:void(0)" onclick="wbSelect(\'experiment\',' + x.id + ')"><strong>' + esc(x.title) + "</strong>" +
      '<span class="meta">' + esc(x.status) + (x.status === "proposed" ? " · proposed, not performed" : "") + "</span></a>";
  }).join("") : emptyState("No experiment plans linked", "Plans are proposed on the Formulations page; results are never invented here."));
}

function wbHypotheses() {
  var h = wbState.data.hypotheses;
  var list = h.length ? h.map(function (x) {
    return '<a class="pick" href="javascript:void(0)" onclick="wbSelect(\'hypothesis\',' + x.id + ')"><strong>' + esc(x.statement) + "</strong>" +
      '<span class="meta">' + chip("hypothesis", x.origin === "researcher" ? "researcher hypothesis" : "generated candidate") + " " + esc(x.status.replace(/_/g, " ")) + "</span></a>";
  }).join("") : emptyState("No hypotheses", "Record a researcher hypothesis below. It stays unvalidated until evidence supports it.");
  var form = '<div class="wx-sec"><h4>Add researcher hypothesis</h4><div class="in">' +
    '<textarea id="wbhs" placeholder="Statement (at least 10 characters)" style="width:100%"></textarea>' +
    '<div><button class="btn primary" onclick="wbAddHyp()">Record hypothesis</button></div></div></div>';
  return panel("Hypotheses (" + h.length + ")", list) + form;
}

async function wbAddHyp() {
  var st = document.getElementById("wbhs").value.trim();
  await wbDo(function () { return wbFetch(WB_WS + "/" + wbState.id + "/hypotheses", "POST", { origin: "researcher", statement: st }); }, "Hypothesis recorded");
}

function wbNotes() {
  var n = wbState.data.notes;
  var list = n.length ? n.map(function (x) {
    return '<a class="pick" href="javascript:void(0)" onclick="wbSelect(\'note\',' + x.id + ')"><strong>' + esc(x.body.slice(0, 140)) + "</strong>" +
      '<span class="meta">' + esc(x.category) + " · " + esc(x.author) + " · researcher-authored</span></a>";
  }).join("") : emptyState("No notes", "Add a note below.");
  var form = '<div class="wx-sec"><h4>Add note</h4><div class="in"><select id="wbnc" style="width:100%">' +
    WB_NOTE_CATS.map(function (c) { return '<option value="' + c + '">' + c + "</option>"; }).join("") + "</select>" +
    '<textarea id="wbnb" placeholder="Note" style="width:100%"></textarea>' +
    '<div><button class="btn primary" onclick="wbAddNote()">Add note</button></div></div></div>';
  return panel("Notes (" + n.length + ")", list) + form;
}

async function wbAddNote() {
  var body = document.getElementById("wbnb").value.trim();
  var cat = document.getElementById("wbnc").value;
  await wbDo(function () { return wbFetch(WB_WS + "/" + wbState.id + "/notes", "POST", { category: cat, body: body }); }, "Note saved");
}

function wbBoards() {
  var b = wbState.data.boards;
  return panel("Research boards (" + b.length + ")", b.length ? b.map(function (x) {
    return '<a class="pick" href="javascript:void(0)" onclick="wbSelect(\'board\',' + x.id + ')"><strong>' + esc(x.title) + "</strong>" +
      '<span class="meta">' + x.object_count + " objects · revision " + x.revision + "</span></a>";
  }).join("") : emptyState("No boards linked", "Boards are created on the Boards page and linked to an investigation.",
    '<a class="btn" href="#/boards">Open boards</a>'));
}

function wbReports() {
  var r = wbState.data.reports;
  return panel("Reports (" + r.length + ")", r.length ? r.map(function (x) {
    return '<a class="pick" href="javascript:void(0)" onclick="wbSelect(\'report\',' + x.id + ')"><strong>Report #' + x.id + "</strong>" +
      '<span class="meta">' + esc((x.created_at || "").slice(0, 16).replace("T", " ")) + " · " + x.summary.verified_observations +
      " observations · " + x.summary.hypotheses + " hypotheses · " + x.summary.gaps + " gaps</span></a>";
  }).join("") : emptyState("No reports yet", "Generate a report to snapshot this investigation.")) +
    '<div><button class="btn primary" onclick="wbReport()"' + (wbState.data.read_only ? " disabled" : "") + ">Generate report</button></div>";
}

async function wbReport() {
  wbState.tab = "reports"; wbSyncUrl();
  await wbDo(function () { return wbFetch(WB_WS + "/" + wbState.id + "/reports", "POST"); }, "Report generated");
}

/* --------------------------------------------------------------- inspector */

function wbFind(list, id) {
  for (var i = 0; i < list.length; i++) { if (list[i].id === id) return list[i]; }
  return null;
}

function wbKv(k, v) {
  return '<div class="kv"><span>' + esc(k) + "</span><b>" + (v === null || v === undefined || v === "" ? '<span class="muted">not set</span>' : esc(v)) + "</b></div>";
}

function wbInspector() {
  var d = wbState.data, s = wbState.sel;
  if (!s) return '<div class="wx-sec"><h4>Inspector</h4><div class="in"><span class="muted">Select a record to see its fields and open its full page.</span></div></div>';
  var o = null, body = "", link = null;
  if (s.kind === "resource") {
    o = wbFind(d.resources, s.id);
    if (o) {
      link = o.link;
      body = wbKv("Type", o.resource_type) + wbKv("Reference", o.resource_ref) + wbKv("Label", o.label) +
        wbKv("Researcher note", o.researcher_note) + wbKv("In current data", o.present_in_current_data ? "yes" : "no") +
        '<div class="note">Attached by reference. Detaching removes only this link, never the record.</div>' +
        (d.read_only ? "" : '<button class="btn ghost" onclick="wbDetach(' + o.id + ')">Detach from investigation</button>');
    }
  } else if (s.kind === "formulation") {
    o = wbFind(d.formulations, s.id);
    if (o) {
      link = o.link;
      body = wbKv("Name", o.name) + wbKv("Dosage form", o.dosage_form) + wbKv("Status", o.status) + wbKv("Constraints", o.constraint_count) +
        wbKv("Unresolved mandatory", o.unresolved_mandatory.join(", ") || "none") + wbKv("Violated mandatory", o.violated_mandatory.join(", ") || "none") +
        wbKv("Readiness", o.readiness_statement) + wbKv("Question", o.research_question);
    }
  } else if (s.kind === "experiment") {
    o = wbFind(d.experiments, s.id);
    if (o) {
      link = o.link;
      body = wbKv("Title", o.title) + wbKv("Status", o.status) + wbKv("Hypothesis", o.hypothesis) + wbKv("Rationale", o.rationale) +
        wbKv("Acceptance criteria", o.acceptance_criteria) + wbKv("Limitations", o.limitations);
    }
  } else if (s.kind === "hypothesis") {
    o = wbFind(d.hypotheses, s.id);
    if (o) {
      body = wbKv("Statement", o.statement) + wbKv("Origin", o.origin) + wbKv("Status", o.status + " (progress, not proof)") +
        wbKv("Validation", o.validation_state) + wbKv("Causal attribution", o.causal_attribution) +
        wbKv("Supporting evidence", (o.supporting_evidence_ids || []).join(", ") || "none") +
        wbKv("Contradicting evidence", (o.contradicting_evidence_ids || []).join(", ") || "none") +
        wbKv("Unresolved", (o.unresolved_questions || []).join(" "));
    }
  } else if (s.kind === "note") {
    o = wbFind(d.notes, s.id);
    if (o) body = wbKv("Category", o.category) + wbKv("Author", o.author) + wbKv("Origin", o.content_origin) + wbKv("Verification", o.verification_status) + wbKv("Body", o.body);
  } else if (s.kind === "board") {
    o = wbFind(d.boards, s.id);
    if (o) { link = o.link; body = wbKv("Title", o.title) + wbKv("Objects", o.object_count) + wbKv("Revision", o.revision) + wbKv("Description", o.description); }
  } else if (s.kind === "report") {
    o = wbFind(d.reports, s.id);
    if (o) {
      link = "#/workspace/" + d.workspace.id + "/reports";
      body = wbKv("Generated", o.created_at) + wbKv("By", o.generated_by) + wbKv("Observations", o.summary.verified_observations) +
        wbKv("Contradictions", o.summary.contradictions) + wbKv("Gaps", o.summary.gaps) + wbKv("Hypotheses", o.summary.hypotheses) + wbKv("Notes", o.summary.notes);
    }
  }
  if (!o) return '<div class="wx-sec"><h4>Inspector</h4><div class="in"><span class="muted">That record is no longer present.</span></div></div>';
  return '<div class="wx-sec"><h4>' + esc(s.kind) + '</h4><div class="in">' + body +
    (link ? '<a class="btn" href="' + esc(link) + '">Open full page</a>' : "") + "</div></div>";
}
