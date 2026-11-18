// MOLECULAB Research Bench — the product's single front door.
//
// Before this page, a researcher had to choose one of 23 navigation destinations before doing any
// research at all. The Bench replaces that choice with a question: what are you investigating?
//
// Seven stages, in plain words, in order:
//     Research -> Evidence -> Explore -> Calculate -> Compare -> Record -> Report
//
// Each stage calls capabilities that already existed. The Bench adds no science; it makes the
// science reachable in one place. Stage completion is derived from what actually exists in the
// investigation (attached records, notes, reports), never from a decorative progress bar.
"use strict";

const BENCH = {
  session: null,        // the current investigation
  stage: "research",
  options: null,        // modes + stages + examples
  catalogue: null,      // calculation registry
  runs: [],             // calculation runs made this visit
  candidates: [],       // structures gathered for comparison
};

const BENCH_KEY = "moleculab.bench.session";

function benchRemember(id) {
  try { localStorage.setItem(BENCH_KEY, String(id)); } catch (e) { /* private mode */ }
}
function benchRecall() {
  try { return localStorage.getItem(BENCH_KEY); } catch (e) { return null; }
}

// --------------------------------------------------------------------- entry

async function benchPage(params) {
  const main = document.getElementById("view");
  main.classList.add("workstation");
  const wanted = (params && params.get("session")) || benchRecall();

  main.innerHTML = '<div class="wx-head"><div><div class="eyebrow">Research Bench</div>' +
    "<h1>What are you investigating?</h1></div></div>" +
    '<div class="wx-body"><div class="wx-main scrolls" id="bOut">' + skeleton(3) + "</div></div>";

  try {
    BENCH.options = await api("/api/v1/bench/start");
  } catch (err) {
    document.getElementById("bOut").innerHTML = errorState("Research Bench unavailable", err.message);
    return;
  }

  if (wanted) {
    try {
      BENCH.session = await api("/api/v1/bench/sessions/" + encodeURIComponent(wanted));
      BENCH.stage = (params && params.get("stage")) || "research";
      return renderBench();
    } catch (err) { /* the remembered session is gone; fall through to the opening screen */ }
  }
  renderStart();
}

// --------------------------------------------------------------------- opening screen

function renderStart() {
  const o = BENCH.options;
  document.getElementById("bOut").innerHTML =
    '<div class="bench-start">' +
    '<p class="lede">Describe what you want to find out. MOLECULAB will gather the evidence, ' +
    "show you the structures, and run the calculations it genuinely supports.</p>" +

    '<div class="panel"><div class="in">' +
    '<div class="field"><label for="bObj">Your research objective</label>' +
    '<textarea id="bObj" rows="3" placeholder="' + esc(o.examples[0]) + '"></textarea></div>' +
    '<div class="chips" id="bEx">' + o.examples.map(function (e, i) {
      return '<button class="chipb" data-ex="' + i + '">' + esc(e.replace(/^I want to /, "")) +
        "</button>"; }).join("") + "</div>" +
    '<div class="field" style="margin-top:12px"><label>What are you starting from?</label>' +
    '<div class="modes" id="bModes">' + o.modes.map(function (m, i) {
      return '<label class="mode"><input type="radio" name="bmode" value="' + esc(m.id) + '"' +
        (i === o.modes.length - 1 ? " checked" : "") + '><span><strong>' + esc(m.label) +
        '</strong><em>' + esc(m.first_steps.slice(0, 2).join(" - ")) + "</em></span></label>"; }).join("") +
    "</div></div>" +
    '<button class="btn primary big" id="bGo">Start investigating</button>' +
    '<p class="sub">A mode only decides what MOLECULAB asks for first. It does not limit what you ' +
    "can do afterwards.</p>" +
    "</div></div>" +

    (o.sessions.length ? '<div class="panel"><h3>Continue an investigation</h3><div class="in">' +
      o.sessions.map(function (s) {
        return '<button class="pick" data-sess="' + s.session_id + '"><strong>' +
          esc(s.title) + '</strong><span class="meta">' + esc(s.mode || "open") + " - " +
          esc((s.updated_at || "").slice(0, 10)) + "</span></button>"; }).join("") +
      "</div></div>" : "") +

    '<div class="panel"><h3>What MOLECULAB can compute</h3><div class="in">' +
    '<p class="sub">' + esc(String(o.catalogue_summary.calculations_available)) +
    " calculations are implemented and offered. " +
    esc(String(o.catalogue_summary.capabilities_unavailable)) +
    " capabilities are deliberately unavailable and say why on screen, rather than returning a " +
    "number MOLECULAB cannot stand behind.</p></div></div>" +
    "</div>";

  document.querySelectorAll("[data-ex]").forEach(function (b) {
    b.onclick = function () {
      document.getElementById("bObj").value = BENCH.options.examples[+b.dataset.ex];
    };
  });
  document.querySelectorAll("[data-sess]").forEach(function (b) {
    b.onclick = async function () {
      BENCH.session = await api("/api/v1/bench/sessions/" + b.dataset.sess);
      benchRemember(BENCH.session.session_id);
      BENCH.stage = "research";
      renderBench();
    };
  });
  document.getElementById("bGo").onclick = startInvestigation;
}

async function startInvestigation() {
  const objective = document.getElementById("bObj").value.trim();
  if (objective.length < 3) {
    document.getElementById("bObj").focus();
    return;
  }
  const mode = (document.querySelector('input[name="bmode"]:checked') || {}).value || "open";
  const btn = document.getElementById("bGo");
  btn.disabled = true;
  btn.textContent = "Starting...";
  try {
    BENCH.session = await api("/api/v1/bench/sessions", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ objective: objective, mode: mode }),
    });
    benchRemember(BENCH.session.session_id);
    BENCH.stage = "evidence";
    renderBench();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Start investigating";
    document.getElementById("bOut").insertAdjacentHTML("afterbegin",
      errorState("Could not start", err.message));
  }
}

// --------------------------------------------------------------------- the bench itself

function renderBench() {
  const s = BENCH.session;
  const main = document.getElementById("view");
  main.classList.add("workstation");
  main.innerHTML =
    '<div class="wx-head"><div><div class="eyebrow">' + esc(s.mode_label) + "</div>" +
    "<h1>" + esc(s.title) + "</h1></div>" +
    '<div class="wx-acts"><button class="btn ghost" id="bSwitch">Switch investigation</button></div>' +
    '<div class="wx-sub">' + esc(s.objective) + "</div></div>" +
    '<div class="wx-strip" id="bStages">' + s.stages.map(function (st) {
      return '<button class="wx-tab' + (st.id === BENCH.stage ? " on" : "") +
        '" data-stage="' + st.id + '" title="' + esc(st.question) + '">' +
        (st.complete ? '<span class="tick">&#10003;</span>' : '<span class="dot"></span>') +
        esc(st.label) + "</button>"; }).join("") + "</div>" +
    '<div class="wx-body"><div class="wx-main scrolls" id="bStage">' + skeleton(2) + "</div></div>";

  document.querySelectorAll("[data-stage]").forEach(function (b) {
    b.onclick = function () { BENCH.stage = b.dataset.stage; renderBench(); };
  });
  document.getElementById("bSwitch").onclick = function () {
    BENCH.session = null;
    try { localStorage.removeItem(BENCH_KEY); } catch (e) { /* ignore */ }
    benchPage(new URLSearchParams());
  };
  stageView(BENCH.stage);
}

async function refreshSession() {
  try {
    BENCH.session = await api("/api/v1/bench/sessions/" + BENCH.session.session_id);
  } catch (err) { /* keep the stale copy rather than breaking the page */ }
}

function stageView(id) {
  const host = document.getElementById("bStage");
  const s = BENCH.session;
  const stage = s.stages.find(function (x) { return x.id === id; }) || s.stages[0];
  const header = '<div class="stage-head"><h2>' + esc(stage.question) + "</h2>" +
    '<p class="sub">' + esc(stage.evidence_for_completion) + "</p></div>";

  if (id === "research") {
    host.innerHTML = header + stageResearch();
    // The "Go to <next stage>" button is rendered as part of that string, so its handler has to
    // be attached after the HTML is in the DOM.
    const next = document.getElementById("bNext");
    if (next) {
      next.onclick = function () {
        BENCH.stage = s.next_step.stage;
        renderBench();
      };
    }
    return;
  }
  if (id === "evidence") return void stageEvidence(host, header);
  if (id === "assist") return void stageAssistant(host, header);
  if (id === "explore") return void stageExplore(host, header);
  if (id === "calculate") return void stageCalculate(host, header);
  if (id === "compare") return void stageCompare(host, header);
  if (id === "record") return void stageRecord(host, header);
  if (id === "report") return void stageReport(host, header);
}

// --------------------------------------------------------------------- 1. research

function stageResearch() {
  const s = BENCH.session;
  const c = s.counts;
  return '<div class="tiles">' +
    tile("Starting materials", c.starting_materials, chip("experimental", "Attached")) +
    tile("Evidence records", c.evidence, chip("experimental", "Indexed")) +
    tile("Publications", c.publications, chip("experimental", "Indexed")) +
    tile("Open questions", c.gaps, chip("missing", "Unresolved")) +
    "</div>" +
    panel("Your objective", "<p>" + esc(s.objective) + "</p>" +
      '<table class="kv">' +
      "<tr><th>Mode</th><td>" + esc(s.mode_label) + "</td></tr>" +
      "<tr><th>Starting material</th><td>" + esc(s.starting_material) + "</td></tr>" +
      "<tr><th>Started</th><td>" + esc((s.created_at || "").slice(0, 16).replace("T", " ")) + "</td></tr>" +
      "<tr><th>Investigation</th><td>#" + s.session_id + "</td></tr></table>") +
    panel("What MOLECULAB suggests doing first",
      '<ol class="steps">' + s.first_steps.map(function (f) {
        return "<li>" + esc(f) + "</li>"; }).join("") + "</ol>" +
      '<p class="note">Next: <strong>' + esc(s.next_step.label) + "</strong> - " +
      esc(s.next_step.question) + '</p><button class="btn primary" id="bNext">Go to ' +
      esc(s.next_step.label) + "</button>");
}

// --------------------------------------------------------------------- 2. evidence

async function stageEvidence(host, header) {
  const s = BENCH.session;
  host.innerHTML = header +
    panel("Attach what you already have",
      '<p class="sub">Search MOLECULAB\'s curated records and attach them to this investigation. ' +
      "An attached record is a pointer: it stays in the scientific corpus and is never copied or " +
      'altered.</p><div class="row"><input id="bSearch" type="text" placeholder="compound, ' +
      'product, plant, publication..." style="flex:1"><button class="btn" id="bSearchGo">Search' +
      "</button></div>" + '<div id="bSearchOut"></div>') +
    '<div id="bAttached">' + skeleton(2) + "</div>";

  const go = async function () {
    const q = document.getElementById("bSearch").value.trim();
    const out = document.getElementById("bSearchOut");
    if (q.length < 2) { out.innerHTML = '<p class="sub">Enter at least two characters.</p>'; return; }
    out.innerHTML = '<p class="sub">Searching...</p>';
    try {
      const d = await api("/api/v1/bench/search?q=" + encodeURIComponent(q));
      out.innerHTML = d.count
        ? '<div class="hits">' + d.results.map(function (r) {
            return '<div class="hit"><span class="k">' + esc(r.kind) + "</span><strong>" +
              esc(r.label) + '</strong><span class="meta">' + esc(r.detail || "") + "</span>" +
              '<button class="btn ghost" data-att="' + esc(r.kind) + ':' + r.id + '">attach</button>' +
              '<a class="btn ghost" href="' + esc(r.route) + '">open</a></div>'; }).join("") +
            "</div>" + '<p class="sub">' + esc(d.note) + "</p>"
        : emptyState("Nothing found for " + esc(q),
            "These are MOLECULAB's curated records. A product missing here may still be " +
            "searchable against live providers from the Drug intelligence page.");
      out.querySelectorAll("[data-att]").forEach(function (b) {
        b.onclick = function () { attachRecord(b.dataset.att, b); };
      });
    } catch (err) { out.innerHTML = errorState("Search failed", err.message); }
  };
  document.getElementById("bSearchGo").onclick = go;
  document.getElementById("bSearch").onkeydown = function (e) { if (e.key === "Enter") go(); };
  loadAttached();
}

async function attachRecord(ref, btn) {
  const [kind, id] = ref.split(":");
  btn.disabled = true;
  btn.textContent = "...";
  try {
    await api("/api/workspaces/" + BENCH.session.session_id + "/resources", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ resource_type: kind, resource_ref: String(id) }),
    });
    btn.textContent = "attached";
    await refreshSession();
    loadAttached();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "retry";
    btn.title = err.message;
  }
}

async function loadAttached() {
  const host = document.getElementById("bAttached");
  if (!host) return;
  try {
    const d = await api("/api/workspaces/" + BENCH.session.session_id + "/resources");
    const items = Array.isArray(d) ? d : (d.items || []);
    host.innerHTML = panel("In this investigation (" + items.length + ")",
      items.length ? '<table class="dtable"><thead><tr><th>Kind</th><th>Record</th><th></th>' +
        "</tr></thead><tbody>" + items.map(function (r) {
          return "<tr><td>" + chip("experimental", esc(r.resource_type)) + "</td><td>" +
            esc(r.label || r.resource_ref) + '</td><td class="num">' +
            '<button class="btn ghost" data-det="' + r.id + '">remove</button></td></tr>';
        }).join("") + "</tbody></table>"
        : '<p class="sub">Nothing attached yet. Search above.</p>');
    host.querySelectorAll("[data-det]").forEach(function (b) {
      b.onclick = async function () {
        b.disabled = true;
        try {
          await api("/api/workspaces/" + BENCH.session.session_id + "/resources/" + b.dataset.det,
                    { method: "DELETE" });
          await refreshSession();
          loadAttached();
        } catch (err) { b.disabled = false; b.title = err.message; }
      };
    });
  } catch (err) { host.innerHTML = errorState("Could not list attached records", err.message); }
}

// --------------------------------------------------------------------- 3. explore

async function stageExplore(host, header) {
  host.innerHTML = header +
    '<div class="row gap" style="margin-bottom:12px">' +
    '<button class="btn primary" id="bPlay">Open the Molecular Playground</button>' +
    '<button class="btn" id="bForm">Inspect a formulation</button>' +
    "</div>" +
    '<div id="bMine">' + skeleton(2) + "</div>" +
    '<div id="bExploreOut"></div>';
  document.getElementById("bPlay").onclick = function () {
    location.hash = "#/playground?session=" + BENCH.session.session_id;
  };
  document.getElementById("bForm").onclick = loadFormulationPicker;
  loadMyMaterials();
}

// What this investigation is actually about: the attached compounds and plants, with their
// structures, ready to open. Previously this stage showed a placeholder even when records were
// attached, which made it look like nothing had happened.
async function loadMyMaterials() {
  const host = document.getElementById("bMine");
  if (!host) return;
  try {
    const d = await api("/api/workspaces/" + BENCH.session.session_id + "/resources");
    const items = (Array.isArray(d) ? d : (d.items || [])).filter(function (r) {
      const t = String(r.resource_type || "");
      return t === "compound" || t === "plant";
    });
    if (!items.length) {
      host.innerHTML = emptyState("Nothing attached yet",
        "Attach a compound or a plant on the Evidence stage, or open the Playground to build a " +
        "structure from scratch.");
      return;
    }
    host.innerHTML = panel("In this investigation (" + items.length + ")",
      '<div class="matgrid" id="bMatGrid">' + items.map(function (r) {
        return '<div class="matcard" data-mat="' + esc(r.resource_type) + ":" +
          esc(r.resource_ref) + '"><div class="matdep" id="dep-' + esc(r.resource_type) + "-" +
          esc(r.resource_ref) + '">' + skeleton(1) + "</div>" +
          "<strong>" + esc(r.label || r.resource_ref) + "</strong>" +
          '<span class="meta">' + esc(r.resource_type) + "</span></div>"; }).join("") +
      "</div>" +
      '<p class="sub">Click one to inspect its structure, or open it in the Playground to ' +
      "modify it. The original record is never changed.</p>");
    items.forEach(function (r) { previewMaterial(r); });
    host.querySelectorAll("[data-mat]").forEach(function (el) {
      el.onclick = function () {
        const [kind, ref] = el.dataset.mat.split(":");
        if (kind === "compound") showCompoundStructure(+ref);
        else location.hash = "#/plant/" + ref;
      };
    });
  } catch (err) {
    host.innerHTML = errorState("Could not list what is attached", err.message);
  }
}

// A small 2D depiction per card, so the stage shows molecules rather than words.
async function previewMaterial(r) {
  if (String(r.resource_type) !== "compound") {
    const box = document.getElementById("dep-" + r.resource_type + "-" + r.resource_ref);
    if (box) box.innerHTML = '<div class="matdep-none">' + esc(r.resource_type) + "</div>";
    return;
  }
  const box = document.getElementById("dep-compound-" + r.resource_ref);
  if (!box) return;
  try {
    const d = await api("/api/v1/bench/structure", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ compound_id: Number(r.resource_ref), include_3d: false }),
    });
    box.innerHTML = depictionSvg(d) || '<div class="matdep-none">no structure</div>';
  } catch (err) {
    box.innerHTML = '<div class="matdep-none">no structure recorded</div>';
  }
}

async function showCompoundStructure(compoundId) {
  const host = document.getElementById("bExploreOut");
  host.innerHTML = skeleton(3);
  try {
    const d = await api("/api/v1/bench/structure", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ compound_id: compoundId, include_3d: false }),
    });
    renderStructurePanel(host, d, d.standardized_smiles || d.input_smiles);
  } catch (err) {
    host.innerHTML = errorState("Could not load that structure", err.message);
  }
}

async function loadFormulationPicker() {
  const out = document.getElementById("bExploreOut");
  out.innerHTML = skeleton(2);
  try {
    const d = await api("/api/v1/bench/search?q=&kinds=formulation&limit=25")
      .catch(function () { return api("/api/v1/bench/search?q=tablet&kinds=formulation"); });
    const items = d.results || [];
    out.innerHTML = panel("Formulations",
      items.length ? items.map(function (r) {
        return '<button class="pick" data-fdet="' + r.id + '"><strong>' + esc(r.label) +
          '</strong><span class="meta">' + esc(r.detail || "") + "</span></button>"; }).join("")
        : '<p class="sub">No formulation records found.</p>');
    out.querySelectorAll("[data-fdet]").forEach(function (b) {
      b.onclick = function () { showFormulation(+b.dataset.fdet); };
    });
  } catch (err) { out.innerHTML = errorState("Could not list formulations", err.message); }
}

async function showFormulation(id) {
  const out = document.getElementById("bExploreOut");
  out.innerHTML = skeleton(3);
  try {
    const d = await api("/api/v1/bench/formulations/" + id);
    const t = d.composition_totals && d.composition_totals.result;
    out.innerHTML =
      panel(esc(d.formulation.name),
        '<p class="note caution">' + esc(d.distinction) + "</p>" +
        '<table class="dtable"><thead><tr><th>Component</th><th>Role</th>' +
        '<th class="num">Amount</th><th class="num">% of total</th><th>Structure</th><th></th>' +
        "</tr></thead><tbody>" +
        d.components.map(function (c, i) {
          const pct = t && t.components[i] ? t.components[i].percent_of_total : null;
          return "<tr><td><strong>" + esc(c.name) + "</strong></td><td>" +
            esc((c.role || "-").replace(/_/g, " ")) + '</td><td class="num">' +
            (c.amount_recorded ? measure(c.amount, " " + esc(c.unit || ""), 2)
                               : chip("missing", "not recorded")) +
            '</td><td class="num">' + (pct === null ? "-" : pct + "%") + "</td><td>" +
            (c.has_structure ? chip("computed", "available") : chip("missing", "none linked")) +
            "</td><td>" + (c.has_structure
              ? '<button class="btn ghost" data-struct="' + esc(c.smiles) + '">view</button>'
              : "") + "</td></tr>"; }).join("") +
        "</tbody></table>" +
        (t ? '<table class="kv" style="margin-top:10px">' +
          "<tr><th>Total</th><td>" + measure(t.total, " " + esc(t.total_unit || ""), 2) + "</td></tr>" +
          "<tr><th>Mass balance checkable</th><td>" +
          (t.mass_balance_checkable ? chip("experimental", "yes") : chip("missing", "no")) +
          "</td></tr></table>" : ""),
        chip("experimental", "Recorded composition"), "scroll") +
      panel("What is missing", limitations(d.limitations)) +
      '<div id="bStructOut"></div>';
    out.querySelectorAll("[data-struct]").forEach(function (b) {
      b.onclick = function () { showStructure(b.dataset.struct); };
    });
  } catch (err) { out.innerHTML = errorState("Could not load formulation", err.message); }
}

async function showStructure(smiles) {
  const host = document.getElementById("bStructOut") ||
               document.getElementById("bExploreOut");
  host.innerHTML = skeleton(2);
  try {
    const d = await api("/api/v1/bench/structure", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ smiles: smiles, include_3d: false }),
    });
    renderStructurePanel(host, d, smiles);
  } catch (err) { host.innerHTML = errorState("Could not render structure", err.message); }
}

// RDKit emits its depiction with an <?xml?> prolog. Browsers tolerate it inside innerHTML by
// treating it as a bogus comment, but stripping it is cheaper than relying on that.
function depictionSvg(payload) {
  const dep = (payload && payload.depiction_2d) || {};
  if (!dep.ok && !dep.svg) return "";
  const svg = String(dep.svg || "");
  const at = svg.indexOf("<svg");
  return at >= 0 ? svg.slice(at) : svg;
}

function renderStructurePanel(host, d, smiles) {
  {
    const dep = d.depiction_2d || {};
    const desc = d.descriptors || {};
    // The payload carries the structure under standardized_smiles / input_smiles;
    // `identifiers` is a separate (often empty) block of external ids.
    const smi = smiles || d.standardized_smiles || d.input_smiles || d.original_smiles || "";
    host.innerHTML = panel("Structure",
      '<div class="row gap">' +
      '<div class="svgbox">' + depictionSvg(d) + "</div>" +
      '<table class="kv" style="flex:1">' +
      ["molecular_formula", "molecular_weight", "clogp", "tpsa", "h_bond_donors",
       "h_bond_acceptors", "rotatable_bonds", "ring_count", "heavy_atom_count"].map(function (k) {
        return "<tr><th>" + esc(k.replace(/_/g, " ")) + "</th><td>" +
          (desc[k] === undefined || desc[k] === null ? chip("missing", "not computed")
                                                     : esc(String(desc[k]))) + "</td></tr>";
      }).join("") + "</table></div>" +
      '<p class="mono wrap sub">' + esc(smi) + "</p>" +
      '<p class="note">' + esc(d.epistemic_note || "") + "</p>" +
      '<div class="row gap">' +
      '<button class="btn primary" data-edit="' + esc(smi) + '">Open in the Playground</button>' +
      '<button class="btn" data-calc-with="' + esc(smi) + '">Run a calculation on it</button>' +
      "</div>", chip("computed", "Computed"));

    const e = host.querySelector("[data-edit]");
    if (e) e.onclick = function () {
      location.hash = "#/playground?smiles=" + encodeURIComponent(e.dataset.edit) +
        "&session=" + BENCH.session.session_id;
    };
    const c = host.querySelector("[data-calc-with]");
    if (c) c.onclick = function () {
      BENCH.pendingSmiles = c.dataset.calcWith;
      BENCH.stage = "calculate";
      renderBench();
    };
  }
}

// --------------------------------------------------------------------- 4. calculate

async function stageCalculate(host, header) {
  host.innerHTML = header + '<div id="bCalcOut">' + skeleton(3) + "</div>";
  const out = document.getElementById("bCalcOut");
  try {
    BENCH.catalogue = BENCH.catalogue || await api("/api/v1/bench/calculations");
  } catch (err) {
    out.innerHTML = errorState("Calculation registry unavailable", err.message);
    return;
  }
  const cat = BENCH.catalogue;
  out.innerHTML =
    '<p class="lede">' + esc(cat.contract) + "</p>" +
    Object.keys(cat.categories).map(function (k) {
      return panel(k[0].toUpperCase() + k.slice(1) + " calculations",
        cat.categories[k].map(function (c) {
          return '<button class="pick" data-calc="' + esc(c.id) + '"><strong>' + esc(c.name) +
            '</strong><span class="meta">' + esc(c.purpose) + "</span></button>"; }).join(""),
        chip("computed", "Implemented"));
    }).join("") +
    panel("Deliberately unavailable",
      '<p class="sub">These are not missing by oversight. Each says why, and none returns a ' +
      "number.</p>" +
      cat.unavailable.map(function (u) {
        return '<div class="unavail"><strong>' + esc(u.name) + "</strong> " +
          chip("missing", "unavailable") + "<p>" + esc(u.reason) + "</p>" +
          '<p class="sub"><em>Would require:</em> ' + esc(u.what_would_be_required) + "</p></div>";
      }).join(""), chip("missing", "Not available")) +
    '<div id="bRunOut"></div>' +
    (BENCH.runs.length ? panel("This visit's runs", benchRunHistory()) : "");

  out.querySelectorAll("[data-calc]").forEach(function (b) {
    b.onclick = function () { openCalcForm(b.dataset.calc); };
  });
  loadSessionSmiles();
  // Arriving here from "Run a calculation on it" opens the commonest one, already filled in.
  if (BENCH.pendingSmiles) openCalcForm("molecular_descriptors");
}

function findCalc(id) {
  const cats = (BENCH.catalogue || {}).categories || {};
  for (const k in cats) {
    const hit = cats[k].find(function (c) { return c.id === id; });
    if (hit) return hit;
  }
  return null;
}

function openCalcForm(id) {
  const c = findCalc(id);
  const host = document.getElementById("bRunOut");
  if (!c) { host.innerHTML = errorState("Unknown calculation", id); return; }
  // Bring the form into view: it sits above the catalogue, which is long.
  if (host.scrollIntoView) { try { host.scrollIntoView({ block: "start" }); } catch (e) {} }
  host.innerHTML = panel(esc(c.name),
    '<p class="sub">' + esc(c.purpose) + "</p>" +
    '<table class="kv"><tr><th>Method</th><td>' + esc(c.method) + "</td></tr></table>" +
    '<div class="in" id="bCalcForm">' + c.inputs.map(function (i) {
      const val = i.default === null || i.default === undefined ? "" : String(i.default);
      if (i.kind === "boolean") {
        return '<label class="row"><input type="checkbox" data-in="' + esc(i.name) + '"' +
          (i.default ? " checked" : "") + "> " + esc(i.description) + "</label>";
      }
      if (i.kind === "reference") {
        return '<div class="field"><label>' + esc(i.name.replace(/_/g, " ")) + "</label>" +
          '<textarea data-in="' + esc(i.name) + '" rows="4" placeholder=\'[{"name":"Lactose",' +
          '"amount":120,"unit":"mg","role":"filler"}]\'></textarea>' +
          '<span class="sub">' + esc(i.description) + " JSON list.</span></div>";
      }
      // A SMILES field is prefilled from whatever structure the researcher already has in
      // hand, so the commonest calculation does not begin with retyping a molecule.
      const prefill = (i.kind === "smiles" || /smiles/.test(i.name))
        ? (BENCH.pendingSmiles || "") : val;
      const human = (i.kind === "smiles" || /smiles/.test(i.name))
        ? "Molecule" : i.name.replace(/_/g, " ");
      return '<div class="field"><label>' + esc(human) +
        (i.required ? "" : " (optional)") + "</label>" +
        ((i.kind === "smiles" || /smiles/.test(i.name))
          ? '<p class="sub" style="margin:0 0 4px">Click a molecule below, or paste a SMILES ' +
            "code - the standard text way of writing a chemical structure, e.g. " +
            '<span class="mono">CCO</span> is ethanol.</p>' : "") +
        (i.kind === "smiles" || /smiles/.test(i.name) ? calcSmilesPicker(i.name) : "") +
        '<input type="' + (i.kind === "number" || i.kind === "integer" ? "number" : "text") +
        '" data-in="' + esc(i.name) + '" value="' + esc(prefill) + '"' +
        (i.minimum !== null && i.minimum !== undefined ? ' min="' + i.minimum + '"' : "") +
        (i.maximum !== null && i.maximum !== undefined ? ' max="' + i.maximum + '"' : "") +
        (i.kind === "number" ? ' step="any"' : "") + ">" +
        ((i.kind === "smiles" || /smiles/.test(i.name)) ? ""
          : '<span class="sub">' + esc(i.description) + "</span>") + "</div>";
    }).join("") +
    '<button class="btn primary" id="bRun">Run calculation</button></div>' +
    panel2("Assumptions and limitations",
      "<h5>Assumptions</h5>" + limitations(c.assumptions) +
      "<h5>Limitations</h5>" + limitations(c.limitations)));

  document.getElementById("bRun").onclick = function () { runCalc(c); };
  wireCalcPickers();
}

// Offer the structures this investigation already has, plus a few common ones, so a SMILES
// field can be filled by clicking instead of typing.
function calcSmilesPicker(inputName) {
  const common = [["Aspirin", "CC(=O)Oc1ccccc1C(=O)O"], ["Paracetamol", "CC(=O)Nc1ccc(O)cc1"],
                  ["Caffeine", "Cn1cnc2c1c(=O)n(C)c(=O)n2C"], ["Benzene", "c1ccccc1"]];
  const mine = (BENCH.sessionSmiles || []);
  const all = mine.concat(common).slice(0, 8);
  return '<div class="pgrid" style="margin-bottom:5px">' + all.map(function (p) {
    return '<button type="button" class="chipb" data-fill="' + esc(inputName) +
      '" data-smi="' + esc(p[1]) + '">' + esc(p[0]) + "</button>"; }).join("") + "</div>";
}

function wireCalcPickers() {
  document.querySelectorAll("[data-fill]").forEach(function (b) {
    b.onclick = function () {
      const input = document.querySelector('[data-in="' + b.dataset.fill + '"]');
      if (input) input.value = b.dataset.smi;
    };
  });
}

// Load the investigation's own compounds once, so the picker can offer them.
async function loadSessionSmiles() {
  if (BENCH.sessionSmiles || !BENCH.session) return;
  BENCH.sessionSmiles = [];
  try {
    const d = await api("/api/workspaces/" + BENCH.session.session_id + "/resources");
    const comps = (Array.isArray(d) ? d : (d.items || [])).filter(function (r) {
      return String(r.resource_type) === "compound"; });
    for (const r of comps.slice(0, 4)) {
      try {
        const s = await api("/api/v1/bench/structure", {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ compound_id: Number(r.resource_ref), include_3d: false }) });
        const smi = s.standardized_smiles || s.input_smiles || "";
        if (smi) BENCH.sessionSmiles.push([r.label || ("compound " + r.resource_ref), smi]);
      } catch (e) { /* a compound without a structure simply is not offered */ }
    }
  } catch (e) { /* leave the picker with the common structures only */ }
}

function panel2(title, body) {
  return '<details class="det"><summary>' + esc(title) + "</summary><div>" + body + "</div></details>";
}

async function runCalc(c) {
  const btn = document.getElementById("bRun");
  btn.disabled = true;
  btn.textContent = "Running...";
  const params = {};
  document.querySelectorAll("#bCalcForm [data-in]").forEach(function (el) {
    const name = el.dataset.in;
    const spec = c.inputs.find(function (i) { return i.name === name; });
    if (el.type === "checkbox") { params[name] = el.checked; return; }
    const raw = (el.value || "").trim();
    if (!raw) return;
    if (spec && spec.kind === "reference") {
      try { params[name] = JSON.parse(raw); } catch (e) { params[name] = raw; }
    } else if (spec && (spec.kind === "number" || spec.kind === "integer")) {
      params[name] = Number(raw);
    } else { params[name] = raw; }
  });

  try {
    const r = await api("/api/v1/bench/calculations/" + encodeURIComponent(c.id) + "/run", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(params),
    });
    BENCH.runs.unshift(r);
    renderRun(r);
  } catch (err) {
    document.getElementById("bRunOut").insertAdjacentHTML("afterbegin",
      panel("Calculation refused", '<p class="limit">' + esc(err.message) + "</p>" +
        '<p class="sub">MOLECULAB declines rather than returning a number it cannot support.</p>',
        chip("missing", "Refused")));
  } finally {
    btn.disabled = false;
    btn.textContent = "Run calculation";
  }
}

// Human labels for every value a calculation can return, with a plain-language explanation.
const VALUE_LABELS = {
  molecular_formula: ["Molecular formula", "Which atoms, and how many of each"],
  molecular_weight: ["Molecular weight", "Mass of one molecule, in g/mol"],
  exact_mass: ["Exact mass", "Mass using the commonest isotope of each atom"],
  heavy_atom_count: ["Heavy atoms", "Atoms other than hydrogen"],
  tpsa: ["Polar surface area", "How much of the surface is polar, in square angstrom"],
  clogp: ["cLogP", "Estimated fat-vs-water preference. Higher means greasier"],
  h_bond_donors: ["H-bond donors", "Groups that can donate a hydrogen bond"],
  h_bond_acceptors: ["H-bond acceptors", "Groups that can accept a hydrogen bond"],
  rotatable_bonds: ["Rotatable bonds", "Single bonds the molecule can twist around"],
  ring_count: ["Rings", "Closed loops of atoms"],
  aromatic_ring_count: ["Aromatic rings", "Flat, benzene-like rings"],
  fraction_csp3: ["Fraction sp3", "Share of carbons that are fully saturated, 0 to 1"],
  formal_charge: ["Formal charge", "Net electrical charge"],
  num_radical_electrons: ["Radical electrons", "Unpaired electrons"],
  molar_refractivity: ["Molar refractivity", "Relates to molecular volume and polarisability"],
  qed: ["Drug-likeness (QED)", "A 0-to-1 summary of how drug-like the properties look"],
  stereo_centers: ["Stereocentres", "Atoms where the 3D arrangement matters"],
  unspecified_stereo_centers: ["Undefined stereocentres", "Stereocentres not pinned down"],
  has_defined_stereo: ["Stereochemistry defined", "Whether the 3D arrangement is specified"],
  murcko_scaffold: ["Core scaffold", "The ring system with side chains stripped off"],
  tanimoto: ["Similarity", "0 to 1: shared substructure, not shared activity"],
  nanomolar: ["In nanomolar", "The same amount expressed in nM"],
  p_activity_if_concentration: ["p-activity", "-log10 of the molar concentration"],
  compatible: ["Can these be compared?", "Whether the two measurements are comparable at all"],
  total: ["Total", "Sum across components that have a recorded amount"],
};

function labelFor(key) {
  return VALUE_LABELS[key] || [key.replace(/_/g, " "), ""];
}

function fmtVal(v) {
  if (v === null || v === undefined || v === "") return chip("missing", "not recorded");
  if (typeof v === "boolean") return v ? chip("experimental", "yes") : chip("missing", "no");
  if (typeof v === "number") return '<strong class="num">' + esc(String(v)) + "</strong>";
  if (Array.isArray(v)) return esc(v.join(", "));
  return esc(String(v));
}

// Turn a result object into tables and cards instead of a JSON dump.
function renderResultBody(res) {
  if (res === null || res === undefined) return "";
  if (typeof res !== "object") return "<p>" + fmtVal(res) + "</p>";
  let html = "";

  // A block of plain values becomes a labelled table.
  const flat = {};
  Object.keys(res).forEach(function (k) {
    const v = res[k];
    if (v === null || typeof v !== "object") flat[k] = v;
  });
  if (res.descriptors) Object.assign(flat, res.descriptors);
  if (res.stereochemistry) Object.assign(flat, res.stereochemistry);

  const skip = { note: 1, interpretation: 1, input_smiles: 1, standardized_smiles: 1,
                 canonical_smiles: 1, smiles_before: 1, smiles_after: 1, valid: 1,
                 changed_by_standardization: 1 };
  const rows = Object.keys(flat).filter(function (k) { return !skip[k]; });
  if (rows.length) {
    html += '<table class="dtable vals"><tbody>' + rows.map(function (k) {
      const L = labelFor(k);
      return "<tr><th>" + esc(L[0]) +
        (L[1] ? '<span class="hint">' + esc(L[1]) + "</span>" : "") +
        "</th><td>" + fmtVal(flat[k]) + "</td></tr>"; }).join("") + "</tbody></table>";
  }

  // Lipinski-style criteria.
  if (Array.isArray(res.criteria)) {
    html += '<table class="dtable"><thead><tr><th>Criterion</th><th>Value</th><th>Met?</th>' +
      "</tr></thead><tbody>" + res.criteria.map(function (c) {
        return "<tr><td>" + esc(c.criterion) + "</td><td>" + fmtVal(c.value) + "</td><td>" +
          (c.satisfied ? chip("experimental", "met") : chip("missing", "not met")) +
          "</td></tr>"; }).join("") + "</tbody></table>";
  }

  // Composition components.
  if (Array.isArray(res.components)) {
    html += '<table class="dtable"><thead><tr><th>Component</th><th>Role</th>' +
      '<th class="num">Amount</th><th class="num">% of total</th></tr></thead><tbody>' +
      res.components.map(function (c) {
        return "<tr><td>" + esc(c.name) + "</td><td>" + esc(c.role || "-") +
          '</td><td class="num">' + (c.amount_recorded
            ? fmtVal(c.amount) + " " + esc(c.unit || "") : chip("missing", "not recorded")) +
          '</td><td class="num">' +
          (c.percent_of_total === null ? "-" : esc(String(c.percent_of_total)) + "%") +
          "</td></tr>"; }).join("") + "</tbody></table>";
  }

  // Structures, shown as code but labelled.
  const smiKeys = ["input_smiles", "standardized_smiles", "canonical_smiles", "murcko_scaffold"];
  const smiRows = smiKeys.filter(function (k) { return res[k]; });
  if (smiRows.length) {
    html += '<table class="kv">' + smiRows.map(function (k) {
      return "<tr><th>" + esc(labelFor(k)[0]) + "</th><td><span class=\"mono wrap\">" +
        esc(res[k]) + "</span></td></tr>"; }).join("") + "</table>";
  }

  // Standardization trail, collapsed.
  if (Array.isArray(res.standardization_steps) && res.standardization_steps.length) {
    const changed = res.standardization_steps.filter(function (s) { return s.changed; }).length;
    html += '<details class="det"><summary>Clean-up steps (' +
      (changed ? changed + " changed the structure" : "nothing was changed") + ")</summary>" +
      '<table class="dtable"><thead><tr><th>Step</th><th>What it does</th><th>Changed?</th>' +
      "</tr></thead><tbody>" + res.standardization_steps.map(function (s) {
        return "<tr><td>" + esc(String(s.step).replace(/_/g, " ")) + "</td><td>" +
          esc(s.description || "") + "</td><td>" +
          (s.changed ? chip("derived", "yes") : chip("missing", "no")) + "</td></tr>";
      }).join("") + "</tbody></table></details>";
  }

  if (res.interpretation) html += '<p class="note">' + esc(res.interpretation) + "</p>";
  if (res.note) html += '<p class="sub">' + esc(res.note) + "</p>";
  if (!html) html += '<pre class="res">' + esc(JSON.stringify(res, null, 1)) + "</pre>";
  return html;
}

function renderRun(r) {
  const host = document.getElementById("bRunOut");
  host.insertAdjacentHTML("afterbegin",
    panel(esc(r.calculation_name) + " - result",
      (r.succeeded
        ? renderResultBody(r.result)
        : '<p class="limit">' + esc(r.error) + "</p>") +
      '<table class="kv">' +
      "<tr><th>Method</th><td>" + esc(r.method) + "</td></tr>" +
      "<tr><th>Inputs</th><td><span class=\"mono\">" + esc(JSON.stringify(r.inputs)) + "</span></td></tr>" +
      "<tr><th>Duration</th><td>" + r.duration_ms + " ms</td></tr>" +
      "<tr><th>RDKit</th><td>" + esc(r.software_versions.rdkit || "-") + "</td></tr>" +
      "<tr><th>Fingerprint</th><td><span class=\"mono\">" +
      esc(r.reproducibility.fingerprint.slice(0, 32)) + "</span></td></tr></table>" +
      '<p class="sub">' + esc(r.reproducibility.note) + "</p>" +
      limitations(r.limitations),
      r.succeeded ? chip("computed", "Computed") : chip("missing", "Failed")));
}

function benchRunHistory() {
  return '<table class="dtable"><thead><tr><th>Calculation</th><th class="num">ms</th>' +
    "<th>Fingerprint</th><th></th></tr></thead><tbody>" +
    BENCH.runs.map(function (r) {
      return "<tr><td>" + esc(r.calculation_name) + '</td><td class="num">' + r.duration_ms +
        '</td><td><span class="mono">' + esc(r.reproducibility.fingerprint.slice(0, 14)) +
        "</span></td><td>" + (r.succeeded ? chip("computed", "ok") : chip("missing", "failed")) +
        "</td></tr>"; }).join("") + "</tbody></table>";
}

// --------------------------------------------------------------------- 5. compare

async function stageCompare(host, header) {
  host.innerHTML = header +
    panel("Compare structures",
      '<p class="sub">Compare an original against modified candidates, or any two structures. ' +
      "Descriptors are shown side by side with pairwise similarity. No composite score is " +
      "produced.</p>" +
      '<div class="in" id="bCmpIn">' +
      '<div class="field"><label>Structure A</label><div class="row">' +
      '<input id="bcA" placeholder="label" style="width:9rem">' +
      '<input id="bcAs" placeholder="SMILES" style="flex:1"></div></div>' +
      '<div class="field"><label>Structure B</label><div class="row">' +
      '<input id="bcB" placeholder="label" style="width:9rem">' +
      '<input id="bcBs" placeholder="SMILES" style="flex:1"></div></div>' +
      '<button class="btn primary" id="bCmpGo">Compare</button></div>') +
    panel("Compare formulations",
      '<p class="sub">Align two compositions component by component.</p>' +
      '<div class="row"><input id="bcF" placeholder="formulation ids, e.g. 1,2" style="flex:1">' +
      '<button class="btn" id="bCmpF">Compare</button></div>') +
    '<div id="bCmpOut"></div>';

  // Carry anything the Playground saved as a candidate.
  if (BENCH.candidates.length >= 1) {
    document.getElementById("bcA").value = BENCH.candidates[0].label || "original";
    document.getElementById("bcAs").value = BENCH.candidates[0].smiles || "";
  }
  if (BENCH.candidates.length >= 2) {
    document.getElementById("bcB").value = BENCH.candidates[1].label || "modified";
    document.getElementById("bcBs").value = BENCH.candidates[1].smiles || "";
  }

  document.getElementById("bCmpGo").onclick = async function () {
    const out = document.getElementById("bCmpOut");
    out.innerHTML = skeleton(2);
    try {
      const d = await api("/api/v1/bench/structures/compare", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ structures: [
          { label: document.getElementById("bcA").value || "A",
            smiles: document.getElementById("bcAs").value },
          { label: document.getElementById("bcB").value || "B",
            smiles: document.getElementById("bcBs").value }] }),
      });
      renderStructureCompare(out, d);
    } catch (err) { out.innerHTML = errorState("Could not compare", err.message); }
  };
  document.getElementById("bCmpF").onclick = async function () {
    const out = document.getElementById("bCmpOut");
    out.innerHTML = skeleton(2);
    try {
      const d = await api("/api/v1/bench/formulations/compare?ids=" +
        encodeURIComponent(document.getElementById("bcF").value.trim()));
      out.innerHTML = panel("Composition comparison",
        '<table class="dtable"><thead><tr><th>Component</th>' +
        d.formulations.map(function (f) { return "<th>" + esc(f.name) + "</th>"; }).join("") +
        "</tr></thead><tbody>" + d.components.map(function (r) {
          return "<tr" + (r.differs ? ' class="diff"' : "") + "><td>" + esc(r.component) +
            "</td>" + r.cells.map(function (c) {
              return "<td>" + (c.present
                ? (c.amount !== null ? measure(c.amount, " " + esc(c.unit || ""), 2)
                                     : chip("missing", "amount not recorded"))
                : chip("missing", "not documented")) + "</td>"; }).join("") + "</tr>";
        }).join("") + "</tbody></table>" +
        '<p class="note">' + esc(d.interpretation) + "</p>" + limitations(d.limitations),
        chip("experimental", "Recorded"), "scroll");
    } catch (err) { out.innerHTML = errorState("Could not compare", err.message); }
  };
}

function renderStructureCompare(out, d) {
  out.innerHTML = panel("Structure comparison",
    '<table class="dtable"><thead><tr><th>Descriptor</th>' +
    d.structures.map(function (s) { return "<th>" + esc(s.label) + "</th>"; }).join("") +
    '<th class="num">delta</th></tr></thead><tbody>' +
    d.descriptor_differences.map(function (r) {
      return "<tr" + (r.differs ? ' class="diff"' : "") + "><td>" +
        esc(r.descriptor.replace(/_/g, " ")) + "</td>" +
        r.values.map(function (v) {
          return "<td>" + (v === null || v === undefined ? chip("missing", "-") : esc(String(v))) +
            "</td>"; }).join("") +
        '<td class="num">' + (r.delta === null ? "-" : esc(String(r.delta))) + "</td></tr>";
    }).join("") + "</tbody></table>" +
    '<table class="kv" style="margin-top:10px">' + d.pairwise_similarity.map(function (p) {
      return "<tr><th>" + esc(p.a) + " vs " + esc(p.b) + "</th><td>Tanimoto " +
        measure(p.tanimoto, "", 3) + (p.identical_structure ? " - identical structure" : "") +
        (p.shares_scaffold ? " - shares scaffold" : " - different scaffold") + "</td></tr>";
    }).join("") + "</table>" +
    '<p class="note">' + esc(d.interpretation) + "</p>" + limitations(d.limitations),
    chip("computed", "Computed"), "scroll");
}

// --------------------------------------------------------------------- 6. record

async function stageRecord(host, header) {
  const id = BENCH.session.session_id;
  host.innerHTML = header +
    panel("Add a research note",
      '<div class="in"><div class="field"><label>Category</label><select id="bnCat">' +
      ["observation", "question", "decision", "method", "interpretation"].map(function (c) {
        return '<option value="' + c + '">' + c + "</option>"; }).join("") + "</select></div>" +
      '<div class="field"><label>Note</label><textarea id="bnBody" rows="3"></textarea></div>' +
      '<button class="btn primary" id="bnGo">Save note</button>' +
      '<p class="sub">A note is attributed to you as the researcher, never to MOLECULAB or to a ' +
      "language model.</p></div>") +
    panel("Record a hypothesis",
      '<div class="in"><div class="field"><label>Hypothesis</label>' +
      '<textarea id="bhBody" rows="3"></textarea></div>' +
      '<button class="btn primary" id="bhGo">Save hypothesis</button>' +
      '<p class="sub">Saved as <strong>unvalidated</strong>. A hypothesis is never presented as a ' +
      "finding.</p></div>") +
    '<div id="bRecOut">' + skeleton(2) + "</div>";

  document.getElementById("bnGo").onclick = async function () {
    const b = document.getElementById("bnGo");
    b.disabled = true;
    try {
      await api("/api/workspaces/" + id + "/notes", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ category: document.getElementById("bnCat").value,
                               body: document.getElementById("bnBody").value }) });
      document.getElementById("bnBody").value = "";
      await refreshSession();
      loadRecorded();
    } catch (err) { document.getElementById("bRecOut").innerHTML = errorState("Could not save", err.message); }
    b.disabled = false;
  };
  document.getElementById("bhGo").onclick = async function () {
    const b = document.getElementById("bhGo");
    b.disabled = true;
    try {
      await api("/api/workspaces/" + id + "/hypotheses", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ origin: "researcher",
                               statement: document.getElementById("bhBody").value }) });
      document.getElementById("bhBody").value = "";
      await refreshSession();
      loadRecorded();
    } catch (err) { document.getElementById("bRecOut").innerHTML = errorState("Could not save", err.message); }
    b.disabled = false;
  };
  loadRecorded();
}

async function loadRecorded() {
  const host = document.getElementById("bRecOut");
  if (!host) return;
  const id = BENCH.session.session_id;
  try {
    const [notes, hyps] = await Promise.all([
      api("/api/workspaces/" + id + "/notes").catch(function () { return []; }),
      api("/api/workspaces/" + id + "/hypotheses").catch(function () { return []; }),
    ]);
    const nl = Array.isArray(notes) ? notes : (notes.items || []);
    const hl = Array.isArray(hyps) ? hyps : (hyps.items || []);
    host.innerHTML =
      panel("Notes (" + nl.length + ")", nl.length ? nl.map(function (n) {
        return '<div class="rec">' + chip("unverified", esc(n.category || "note")) +
          "<p>" + esc(n.body || "") + '</p><span class="sub">' +
          esc((n.created_at || "").slice(0, 16).replace("T", " ")) + "</span></div>"; }).join("")
        : '<p class="sub">No notes yet.</p>') +
      panel("Hypotheses (" + hl.length + ")", hl.length ? hl.map(function (h) {
        return '<div class="rec">' + chip("hypothesis", esc(h.status || "unvalidated")) +
          "<p>" + esc(h.statement || "") + '</p><span class="sub">origin: ' +
          esc(h.origin || "-") + "</span></div>"; }).join("")
        : '<p class="sub">No hypotheses yet.</p>');
  } catch (err) { host.innerHTML = errorState("Could not load records", err.message); }
}

// --------------------------------------------------------------------- 7. report

async function stageReport(host, header) {
  const id = BENCH.session.session_id;
  host.innerHTML = header +
    panel("Generate a report",
      '<p class="sub">A report captures this investigation\'s records and carries a SHA-256 ' +
      "content fingerprint that excludes the timestamp, so the same evidence reproduces the same " +
      'fingerprint.</p><button class="btn primary" id="brGo">Generate report</button>') +
    '<div id="bRepOut">' + skeleton(2) + "</div>";
  document.getElementById("brGo").onclick = async function () {
    const b = document.getElementById("brGo");
    b.disabled = true;
    b.textContent = "Generating...";
    try {
      await api("/api/workspaces/" + id + "/reports", { method: "POST" });
      await refreshSession();
      loadReports();
    } catch (err) {
      document.getElementById("bRepOut").innerHTML = errorState("Could not generate", err.message);
    }
    b.disabled = false;
    b.textContent = "Generate report";
  };
  loadReports();
}

async function loadReports() {
  const host = document.getElementById("bRepOut");
  if (!host) return;
  try {
    const d = await api("/api/workspaces/" + BENCH.session.session_id + "/reports");
    const items = Array.isArray(d) ? d : (d.items || []);
    if (!items.length) {
      host.innerHTML = panel("Reports",
        '<p class="sub">No report yet. Generate one above.</p>');
      return;
    }
    // The API returns dataset_fingerprint (a dict, with content_sha256 inside) and summary (a
    // dict of counts). The full markdown lives on a separate /export route.
    host.innerHTML = items.map(function (r) {
      const fp = r.dataset_fingerprint || {};
      const s = r.summary || {};
      return panel("Report #" + r.id,
        '<table class="kv">' +
        "<tr><th>Generated</th><td>" +
        esc((r.created_at || "").slice(0, 16).replace("T", " ")) + " by " +
        esc(r.generated_by || "-") + "</td></tr>" +
        "<tr><th>Content fingerprint</th><td><span class=\"mono\">" +
        esc(String(fp.content_sha256 || "").slice(0, 40)) + "</span></td></tr>" +
        "<tr><th>Schema</th><td>" + esc(String(fp.schema_revision || "-")) +
        " - MOLECULAB " + esc(String(fp.app_version || "-")) + "</td></tr>" +
        "</table>" +
        '<div class="tiles" style="margin:10px 0">' +
        tile("Verified observations", s.verified_observations, chip("experimental", "Verified")) +
        tile("Open gaps", s.gaps, chip("missing", "Unresolved")) +
        tile("Contradictions", s.contradictions, chip("contradiction", "Conflicting")) +
        tile("Hypotheses", s.hypotheses, chip("hypothesis", "Unvalidated")) +
        "</div>" +
        '<p class="sub">The corpus this was built from: ' +
        esc(String(fp.plants || 0)) + " plants, " + esc(String(fp.compounds || 0)) +
        " compounds, " + esc(String(fp.publications || 0)) + " publications, " +
        esc(String(fp.evidence_records || 0)) + " evidence records. The fingerprint excludes " +
        "the timestamp, so the same evidence reproduces the same value.</p>" +
        '<div class="row gap">' +
        '<button class="btn" data-rep="' + r.id + '">View the full report</button>' +
        '<a class="btn ghost" href="/api/workspaces/' + BENCH.session.session_id +
        "/reports/" + r.id + '/export" target="_blank" rel="noopener">Download .md</a></div>' +
        '<div id="repbody-' + r.id + '"></div>',
        chip("derived", "Reproducible"));
    }).join("");

    host.querySelectorAll("[data-rep]").forEach(function (b) {
      b.onclick = async function () {
        const box = document.getElementById("repbody-" + b.dataset.rep);
        box.innerHTML = '<p class="sub">Loading...</p>';
        try {
          const res = await fetch("/api/workspaces/" + BENCH.session.session_id +
                                  "/reports/" + b.dataset.rep + "/export");
          if (!res.ok) throw new Error("HTTP " + res.status);
          const md = await res.text();
          box.innerHTML = '<div class="mdbody">' + renderMarkdown(md) + "</div>";
        } catch (err) {
          box.innerHTML = '<p class="limit">Could not load the report text: ' +
            esc(err.message) + "</p>";
        }
      };
    });
  } catch (err) { host.innerHTML = errorState("Could not list reports", err.message); }
}


// A small Markdown renderer: enough for the reports MOLECULAB generates (headings, lists,
// tables, bold, code, blockquotes). Everything is escaped first, so report content cannot
// inject markup.
function renderMarkdown(md) {
  const lines = String(md).replace(/\r/g, "").split("\n");
  let out = "";
  let inList = false;
  let inCode = false;
  let tableRows = [];

  const flushList = function () { if (inList) { out += "</ul>"; inList = false; } };
  const flushTable = function () {
    if (!tableRows.length) return;
    const cells = function (row) {
      return row.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|")
        .map(function (c) { return c.trim(); });
    };
    const head = cells(tableRows[0]);
    const body = tableRows.slice(
      tableRows.length > 1 && /^[\s|:-]+$/.test(tableRows[1]) ? 2 : 1);
    out += '<table class="dtable"><thead><tr>' +
      head.map(function (h) { return "<th>" + inline(h) + "</th>"; }).join("") +
      "</tr></thead><tbody>" + body.map(function (r) {
        return "<tr>" + cells(r).map(function (c) {
          return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>"; }).join("") +
      "</tbody></table>";
    tableRows = [];
  };
  const inline = function (s) {
    return esc(s)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g,
               '<a href="$2" target="_blank" rel="noopener">$1</a>');
  };

  for (const raw of lines) {
    const line = raw;
    if (/^```/.test(line.trim())) {
      flushList(); flushTable();
      out += inCode ? "</pre>" : '<pre class="res">';
      inCode = !inCode;
      continue;
    }
    if (inCode) { out += esc(line) + "\n"; continue; }

    if (/^\s*\|.*\|\s*$/.test(line)) { flushList(); tableRows.push(line); continue; }
    flushTable();

    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushList();
      const lvl = Math.min(h[1].length + 1, 6);
      out += "<h" + lvl + ">" + inline(h[2]) + "</h" + lvl + ">";
      continue;
    }
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      if (!inList) { out += "<ul>"; inList = true; }
      out += "<li>" + inline(line.replace(/^\s*([-*+]|\d+\.)\s+/, "")) + "</li>";
      continue;
    }
    flushList();
    if (/^\s*>/.test(line)) { out += "<blockquote>" + inline(line.replace(/^\s*>\s?/, "")) +
      "</blockquote>"; continue; }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { out += "<hr>"; continue; }
    if (!line.trim()) continue;
    out += "<p>" + inline(line) + "</p>";
  }
  flushList(); flushTable();
  if (inCode) out += "</pre>";
  return out;
}
