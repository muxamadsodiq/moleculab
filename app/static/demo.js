// MOLECULAB guided demo: a stepped tour that drives the REAL application and REAL APIs.
//
// Nothing on these screens is mocked. Every step performs a live request and renders what came back.
// When a request fails (for example a live provider times out) the step shows the failure and a
// retry button; it never substitutes a made-up result. Where MOLECULAB has no capability (docking,
// QSAR) the demo says so on screen.
//
// Defines ONE global: demoPage(params). Uses globals from ui.js / app.js: esc, api, chip, panel,
// emptyState, errorState, unavailableState, skeleton, tile, measure, limitations, methodBlock,
// histogramSVG, fractionBar, and the global `view` element.
"use strict";

var DEMO_JSON = { "Content-Type": "application/json" };
var DEMO_STEPS = [
  { id: "platform", title: "The platform", sub: "What is running, live" },
  { id: "disease", title: "Disease", sub: "Rheumatoid arthritis" },
  { id: "target", title: "Target", sub: "JAK1" },
  { id: "compound", title: "Compound data", sub: "Measured JAK1 potency" },
  { id: "structure", title: "Structure", sub: "2D and 3D" },
  { id: "calc", title: "Calculation", sub: "Conformers and similarity" },
  { id: "evidence", title: "Evidence and AI", sub: "Grounded answers" },
  { id: "formulation", title: "Formulation", sub: "Constraints, not scores" },
  { id: "experiment", title: "Experiment priority", sub: "What to measure next" }
];

var demoState = { step: 0, token: 0, cache: {}, sel: null, viewer: null, aiAnswer: null, aiBusy: false };

function demoStyle() {
  if (document.getElementById("demo-style")) return;
  var css = "" +
    ".demo-shell{display:grid;grid-template-columns:250px minmax(0,1fr);gap:18px;align-items:start}" +
    "@media(max-width:900px){.demo-shell{grid-template-columns:1fr}}" +
    ".demo-rail{position:sticky;top:12px;display:flex;flex-direction:column;gap:4px}" +
    ".demo-rail button{all:unset;cursor:pointer;display:grid;grid-template-columns:26px 1fr;gap:8px;padding:8px 10px;" +
    "border:1px solid var(--line,#2a2f36);border-radius:6px;box-sizing:border-box}" +
    ".demo-rail button:hover{border-color:var(--accent,#7aa2ff)}" +
    ".demo-rail button.on{border-color:var(--accent,#7aa2ff);background:rgba(122,162,255,.10)}" +
    ".demo-rail .n{font-family:ui-monospace,monospace;opacity:.7}" +
    ".demo-rail .t{font-weight:600;font-size:13px}.demo-rail .s{font-size:11px;opacity:.65}" +
    ".demo-rail button.done .n{color:#5cc98a;opacity:1}" +
    ".demo-prog{height:4px;background:rgba(128,128,128,.25);border-radius:2px;margin:6px 0 10px}" +
    ".demo-prog span{display:block;height:100%;background:var(--accent,#7aa2ff);border-radius:2px}" +
    ".demo-why{border-left:3px solid var(--accent,#7aa2ff);padding:8px 14px;margin:0 0 14px;font-size:14px;line-height:1.5}" +
    ".demo-nav{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-top:16px}" +
    ".demo-mol{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}" +
    "@media(max-width:900px){.demo-mol{grid-template-columns:1fr}}" +
    ".demo-svg svg{width:100%;height:auto;max-height:340px;background:#fff;border-radius:6px}" +
    ".demo-3d{position:relative;height:340px;border-radius:6px;overflow:hidden;background:#0e1014}" +
    ".demo-prov{font-size:11px;opacity:.7;margin-top:8px}" +
    ".demo-scroll{overflow-x:auto}";
  var s = document.createElement("style");
  s.id = "demo-style";
  s.textContent = css;
  document.head.appendChild(s);
}

function demoProv(p) {
  if (!p) return "";
  var bits = [];
  if (p.provider) bits.push("source: " + p.provider);
  if (p.source_release) bits.push("release: " + p.source_release);
  if (p.retrieved_at) bits.push("retrieved " + p.retrieved_at);
  if (p.from_cache !== undefined) bits.push(p.from_cache ? "served from local cache" : "fetched live");
  return '<div class="demo-prov mono">' + esc(bits.join(" | ")) + "</div>";
}

function demoPost(path, body) {
  return api(path, { method: "POST", headers: DEMO_JSON, body: JSON.stringify(body) });
}

function demoNum(v, d) {
  return typeof v === "number" ? v.toFixed(d === undefined ? 2 : d) : "-";
}

// ------------------------------------------------------------------ data loaders (cached)
function demoActivity() {
  if (demoState.cache.activity) return Promise.resolve(demoState.cache.activity);
  return api("/api/v1/simulations/activity?limit=50").then(function (d) {
    demoState.cache.activity = d;
    if (!demoState.sel && d.rows && d.rows.length) {
      var best = d.rows[0];
      d.rows.forEach(function (r) { if ((r.replicate_count || 0) > (best.replicate_count || 0)) best = r; });
      demoState.sel = { kind: "smiles", smiles: best.smiles, name: best.molecule_chembl_id, p: best.p_activity_median,
        reps: best.replicate_count };
    }
    return d;
  });
}

function demoSelected() {
  if (demoState.sel) return Promise.resolve(demoState.sel);
  return demoActivity().then(function () { return demoState.sel; });
}

// ------------------------------------------------------------------ step renderers
// Each step: load() -> Promise<data>; render(data) -> html; after(data) -> optional DOM wiring.
var DEMO_IMPL = {};

DEMO_IMPL.platform = {
  why: "A researcher first needs to know what the system actually holds and whether its live parts are switched on. " +
    "These numbers come straight from the running database.",
  load: function () {
    return Promise.all([
      api("/api/stats"),
      api("/api/v1/copilot/status").catch(function (e) { return { _error: e.message }; }),
      api("/api/workspaces").catch(function (e) { return { _error: e.message }; })
    ]).then(function (r) { return { stats: r[0], copilot: r[1], workspaces: r[2] }; });
  },
  render: function (d) {
    var s = d.stats, c = d.copilot, w = d.workspaces;
    var tiles = '<div class="tiles">' + tile("Plants indexed", s.plants) + tile("Compounds", s.compounds) +
      tile("Publications", s.publications) + tile("Evidence records", s.evidence_records,
        chip("experimental", "excerpt verified: " + ((s.evidence_by_verification || {}).excerpt_verified || 0))) + "</div>";
    var types = Object.keys(s.evidence_by_type || {}).map(function (k) {
      return "<tr><td>" + esc(k.replace(/_/g, " ")) + '</td><td class="mono">' + s.evidence_by_type[k] + "</td></tr>";
    }).join("");
    var ai = c._error ? errorState("Copilot status unavailable", c._error) :
      '<p>Live model generation: <strong>' + (c.model_generation_available ? "ON" : "OFF") + "</strong>" +
      (c.provider ? " (" + esc(c.provider) + ", " + esc(c.model || "") + ")" : "") + "</p><p class=\"muted\">" +
      esc(c.reason || "") + "</p>";
    var ws = Array.isArray(w) ? "<p><strong>" + w.length + "</strong> research investigation(s) stored. First: " +
      (w[0] ? esc(w[0].title) : "none") + "</p>" : errorState("Investigations unavailable", w._error || "");
    return tiles +
      panel("Evidence corpus by type", '<table class="dtable"><tbody>' + types + "</tbody></table>",
        chip("experimental")) +
      panel("AI generation status", ai) + panel("Investigations", ws) +
      '<div class="note caution">The indexed literature corpus is small and is about plant phytochemistry. ' +
      "It is a verified-evidence demonstration corpus, not a comprehensive literature database.</div>";
  }
};

DEMO_IMPL.disease = {
  why: "Every programme starts from a disease question. Here a live Open Targets search resolves the name to an ontology ID, " +
    "then pulls the targets associated with it and the type of evidence behind each score.",
  load: function () {
    return api("/api/v1/discovery/diseases/search?q=" + encodeURIComponent("rheumatoid arthritis")).then(function (s) {
      var hit = s.results && s.results[0];
      if (!hit) throw new Error("The disease search returned no results.");
      return api("/api/v1/discovery/diseases/" + encodeURIComponent(hit.id)).then(function (dz) {
        return { search: s, hit: hit, detail: dz };
      });
    });
  },
  render: function (d) {
    var dz = d.detail;
    if (!dz.available) {
      return panel("Disease: " + d.hit.name + " (" + d.hit.id + ")", chip("missing", "provider unavailable") +
        errorState("Live provider did not answer", dz.error || "unknown error",
          '<p class="muted">This is a live external service. Use Re-run to try again; nothing is substituted.</p>'));
    }
    var rows = (dz.associated_targets || []).slice(0, 10).map(function (t) {
      var ds = t.datatype_scores || {};
      var scores = Object.keys(ds).map(function (k) { return esc(k.replace(/_/g, " ")) + " " + demoNum(ds[k]); }).join(" | ");
      return "<tr" + (t.gene_symbol === "JAK1" ? ' style="background:rgba(122,162,255,.12)"' : "") + "><td><strong>" +
        esc(t.gene_symbol) + '</strong><div class="muted">' + esc(t.target_name) + "</div></td><td>" +
        fractionBar(t.overall_score, "") + '</td><td class="muted">' + scores + "</td></tr>";
    }).join("");
    var areas = (dz.disease.therapeutic_areas || []).map(function (a) { return esc(a.name); }).join(", ");
    return panel(dz.disease.name + " (" + dz.disease.id + ")",
      "<p>" + esc(dz.disease.description || "") + "</p>" +
      '<p class="muted">Therapeutic areas: ' + areas + "</p>" +
      '<p><strong>' + esc(dz.associated_target_count) + "</strong> associated targets; top 10 by association score:</p>" +
      '<div class="demo-scroll"><table class="dtable"><thead><tr><th>Target</th><th>Overall score</th><th>Evidence by datatype</th></tr></thead><tbody>' +
      rows + "</tbody></table></div>" + demoProv((dz.provenance || {})),
      chip("experimental", "aggregated source scores")) +
      '<div class="note">Association scores prioritise targets for investigation. They are evidence-aggregation scores, not proof that modulating a target treats the disease.</div>';
  }
};

DEMO_IMPL.target = {
  why: "Having chosen JAK1 from the list, the researcher needs to know what is already known: biology, tractability, " +
    "existing drugs and their safety warnings.",
  load: function () { return api("/api/v1/discovery/targets/ENSG00000162434"); },
  render: function (d) {
    var t = d.target && d.target.data;
    if (!t) return errorState("Target record unavailable", (d.target && d.target.error) || "no data");
    var fn = (t.function_descriptions && t.function_descriptions[0]) || "";
    fn = fn.replace(/\(PubMed:[^)]*\)/g, "").replace(/\{ECO:[^}]*\}/g, "").slice(0, 420);
    var tract = (t.tractability || []).filter(function (x) { return x.value === true; })
      .map(function (x) { return '<span class="chip">' + esc(x.modality + ": " + x.label) + "</span> "; }).join("");
    var drugs = ((d.drug_candidates && d.drug_candidates.data && d.drug_candidates.data.rows) || []);
    var seen = {}, drows = "";
    drugs.forEach(function (r) {
      if (seen[r.drug_name] || Object.keys(seen).length >= 8) return;
      seen[r.drug_name] = 1;
      var moa = (r.mechanisms_of_action || []).map(function (m) { return m.mechanism_of_action; }).join("; ");
      var warn = (r.drug_warnings || []).map(function (x) { return x.warning_type; }).join(", ");
      drows += "<tr><td><strong>" + esc(r.drug_name) + '</strong></td><td class="mono">' + esc(r.maximum_clinical_stage) +
        "</td><td>" + esc(moa) + "</td><td>" + esc(warn || "none listed") + "</td></tr>";
    });
    var liab = (t.safety_liabilities || []).length;
    return panel(t.gene_symbol + ": " + t.target_name,
      "<p>" + esc(fn) + "...</p>" +
      '<div class="kv"><table class="kv"><tbody><tr><th>Ensembl</th><td class="mono">' + esc(t.ensembl_id) + "</td></tr>" +
      '<tr><th>UniProt</th><td class="mono">' + esc((t.uniprot_accessions || [])[0] || "") + "</td></tr>" +
      "<tr><th>Associated diseases</th><td>" + esc((d.associated_diseases && d.associated_diseases.data && d.associated_diseases.data.count) || "-") + "</td></tr>" +
      "<tr><th>Safety liabilities returned</th><td>" + liab + "</td></tr></tbody></table></div>" +
      "<p>Tractability flags that are true: " + (tract || '<span class="muted">none</span>') + "</p>" +
      demoProv(d.target.provenance), chip("experimental", "Open Targets")) +
      panel("Drugs acting on this target (first 8 distinct)",
        '<div class="demo-scroll"><table class="dtable"><thead><tr><th>Drug</th><th>Max stage</th><th>Mechanism</th><th>Warnings</th></tr></thead><tbody>' +
        drows + "</tbody></table></div>" + demoProv(d.drug_candidates && d.drug_candidates.provenance),
        chip("experimental"));
  }
};

DEMO_IMPL.compound = {
  why: "Targets become chemistry. These are real, curated ChEMBL IC50 measurements against JAK1, with one endpoint, one organism " +
    "and one assay type enforced so values are comparable. Pick any compound to carry it through the next steps.",
  load: function () {
    return Promise.all([demoActivity(), api("/api/v1/molecules/?limit=50")]).then(function (r) {
      return { activity: r[0], library: r[1] };
    });
  },
  render: function (d) {
    var a = d.activity, st = a.statistics || {};
    var sel = demoState.sel;
    var rows = (a.rows || []).slice(0, 12).map(function (r, i) {
      var on = sel && sel.name === r.molecule_chembl_id;
      return "<tr" + (on ? ' style="background:rgba(122,162,255,.12)"' : "") + '><td class="mono">' + esc(r.molecule_chembl_id) +
        "</td><td>" + measure(r.p_activity_median, "pIC50", 2) + "</td><td>" + esc(r.replicate_count) +
        '</td><td class="smiles mono" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(r.smiles) +
        '</td><td><button class="btn ghost" data-pick="' + i + '">' + (on ? "Selected" : "Use this") + "</button></td></tr>";
    }).join("");
    var lib = d.library, withS = (lib.items || []).filter(function (x) { return x.has_structure; }).length;
    return '<div class="tiles">' + tile("Curated JAK1 compounds", a.dataset.compound_count, chip("experimental")) +
      tile("Median pIC50", st.median, chip("derived")) + tile("Range", demoNum(st.min, 1) + " to " + demoNum(st.max, 1)) +
      tile("Single-measurement compounds", st.unreplicated) + "</div>" +
      panel("Top compounds by potency (showing 12 of the first " + a.returned + ")",
        '<div class="demo-scroll"><table class="dtable"><thead><tr><th>ChEMBL ID</th><th>Potency</th><th>Replicates</th><th>SMILES</th><th></th></tr></thead><tbody>' +
        rows + "</tbody></table></div>" +
        '<p class="muted">Selected compound: ' + (sel ? esc(sel.name) : "none") + ". Default is the most-replicated of the first 50.</p>" +
        '<div class="demo-prov">' + esc(a.dataset.source + ", retrieved " + a.dataset.retrieved + ", " + a.dataset.endpoint + " in " + a.dataset.units) + "</div>",
        chip("experimental") + " " + chip("derived", "pIC50 derived")) +
      panel("Local compound library", "<p><strong>" + esc(lib.count) + "</strong> library entries returned, " + withS +
        " with a stored structure. This library is the plant-phytochemistry corpus and imported structures.</p>" +
        '<p class="muted">' + esc(lib.note) + "</p>") +
      limitations(a.dataset.limitations);
  },
  after: function (d) {
    var btns = view.querySelectorAll("[data-pick]");
    Array.prototype.forEach.call(btns, function (b) {
      b.addEventListener("click", function () {
        var r = d.activity.rows[parseInt(b.getAttribute("data-pick"), 10)];
        demoState.sel = { kind: "smiles", smiles: r.smiles, name: r.molecule_chembl_id, p: r.p_activity_median, reps: r.replicate_count };
        delete demoState.cache.structure; delete demoState.cache.calc;
        demoRenderStep();
      });
    });
  }
};

function demoRender3D(hostId, molblock) {
  var host = document.getElementById(hostId);
  if (!host) return;
  if (typeof window.$3Dmol === "undefined") {
    host.innerHTML = errorState("3D viewer unavailable", "3Dmol.js did not load.");
    return;
  }
  try {
    host.innerHTML = "";
    var v = window.$3Dmol.createViewer(host, { backgroundColor: "#0e1014" });
    v.addModel(molblock, "mol");
    v.setStyle({}, { stick: { radius: 0.14 }, sphere: { scale: 0.22 } });
    v.zoomTo();
    v.render();
    demoState.viewer = v;
  } catch (err) {
    host.innerHTML = errorState("3D render failed", err.message);
  }
}

DEMO_IMPL.structure = {
  why: "Chemists reason about shape. The structure is standardised, depicted in 2D, and a 3D conformer is generated. " +
    "The 3D model is computed here; MOLECULAB holds no experimentally determined 3D structures.",
  load: function () {
    return demoSelected().then(function (sel) {
      if (!sel) throw new Error("No compound available to inspect.");
      return demoPost("/api/v1/molecules/smiles", { smiles: sel.smiles, name: sel.name });
    });
  },
  render: function (p) {
    if (!p.ok) return errorState("Structure rejected", p.error || "invalid structure");
    var d = p.descriptors || {};
    var c3 = p.conformer_3d;
    var keys = [["Formula", d.molecular_formula], ["Molecular weight", d.molecular_weight], ["TPSA", d.tpsa],
      ["cLogP", d.clogp], ["H-bond donors", d.h_bond_donors], ["H-bond acceptors", d.h_bond_acceptors],
      ["Rotatable bonds", d.rotatable_bonds], ["QED", d.qed]];
    var tbl = keys.map(function (k) { return "<tr><th>" + esc(k[0]) + '</th><td class="mono">' + esc(k[1]) + "</td></tr>"; }).join("");
    var left = '<div class="demo-svg">' + ((p.depiction_2d && p.depiction_2d.svg) || "") + "</div>" +
      '<div class="muted">' + chip("computed", "2D depiction") + " layout only, no physical meaning</div>";
    var right = c3 && c3.ok ? '<div class="demo-3d" id="demo-mol3d"></div><div class="muted">' +
      chip("computed", "3D conformer") + " " + esc((c3.method || {}).embedding || "") + " + " + esc((c3.method || {}).force_field || "") +
      "</div>" : unavailableState("No 3D conformer", (c3 && c3.error) || "not generated");
    return panel("Structure of " + esc(p.name || ""), '<div class="demo-mol"><div>' + left + "</div><div>" + right + "</div></div>" +
      '<p class="mono" style="word-break:break-all;margin-top:10px">' + esc(p.standardized_smiles) + "</p>" +
      '<p class="muted">InChIKey ' + esc(p.inchikey) + ". Stereocentres: " + esc((p.stereo || {}).stereo_centers) + ".</p>",
      chip("computed")) +
      panel("Descriptors", '<table class="kv"><tbody>' + tbl + "</tbody></table>" +
        '<p class="muted">Calculated by RDKit. These are not measurements.</p>', chip("computed"));
  },
  after: function (p) {
    if (p.ok && p.conformer_3d && p.conformer_3d.ok) demoRender3D("demo-mol3d", p.conformer_3d.molblock);
  }
};

DEMO_IMPL.calc = {
  why: "Two real calculations on the selected compound: a force-field conformer search (shape and strain) and a Tanimoto " +
    "similarity search across the library plus the 7,777 measured JAK1 compounds.",
  load: function () {
    return demoSelected().then(function (sel) {
      return Promise.all([
        demoPost("/api/v1/simulations/conformers", { smiles: sel.smiles, n_conformers: 8 }),
        demoPost("/api/v1/simulations/similarity", { smiles: sel.smiles, top_n: 6, include_activity_dataset: true }),
        api("/api/v1/simulations/protein-ligand").catch(function (e) { return { _error: e.message }; })
      ]).then(function (r) { return { conf: r[0], sim: r[1], pl: r[2], sel: sel }; });
    });
  },
  render: function (d) {
    var cf = d.conf.conformers || [];
    var maxRel = Math.max.apply(null, cf.map(function (c) { return c.relative_energy_kcal_per_mol; })) || 1;
    var crow = cf.map(function (c) {
      return "<tr><td>" + c.rank + '</td><td class="mono">' + demoNum(c.energy_kcal_per_mol, 2) + "</td><td>" +
        fractionBar(c.relative_energy_kcal_per_mol / maxRel, "") + '</td><td class="mono">+' +
        demoNum(c.relative_energy_kcal_per_mol, 2) + '</td><td class="mono">' + demoNum(c.rmsd_to_lowest_angstrom, 2) + "</td></tr>";
    }).join("");
    var srow = (d.sim.results || []).map(function (r) {
      return "<tr><td class=\"mono\">" + esc(r.name) + "</td><td>" + esc(r.source) + "</td><td>" + fractionBar(r.tanimoto, "") +
        "</td><td>" + (r.p_activity_median ? measure(r.p_activity_median, "pIC50", 2) : '<span class="muted">-</span>') +
        "</td><td>" + (r.shares_scaffold ? "yes" : "no") + "</td></tr>";
    }).join("");
    var pl = d.pl._error ? "" : unavailableState("Docking: not available", d.pl.reason || d.pl.message || d.pl.summary ||
      "No docking engine, protein structures or preparation pipeline exist. Nothing is computed or displayed.");
    return panel("Conformer ensemble (" + cf.length + " generated, " + esc(d.conf.method.force_field) + ")",
      '<div class="demo-mol"><div><div class="demo-scroll"><table class="dtable"><thead><tr><th>Rank</th><th>E (kcal/mol)</th><th>Relative</th><th>dE</th><th>RMSD</th></tr></thead><tbody>' +
      crow + '</tbody></table></div></div><div><div class="demo-3d" id="demo-conf3d"></div><div class="muted">Lowest-energy conformer</div></div></div>' +
      '<p class="muted">' + esc(d.conf.interpretation) + "</p>", chip("computed", "MMFF94 energies")) +
      panel("Nearest neighbours by Tanimoto (" + esc(d.sim.library_size) + " structures compared)",
        '<div class="demo-scroll"><table class="dtable"><thead><tr><th>ID</th><th>Source</th><th>Tanimoto</th><th>Measured</th><th>Same scaffold</th></tr></thead><tbody>' +
        srow + "</tbody></table></div><p class=\"muted\">" + esc(d.sim.interpretation) + "</p>", chip("computed")) +
      '<div class="note caution"><strong>What MOLECULAB does not do.</strong> No docking, no QSAR, no activity prediction. ' +
      "A high similarity or a low strain energy is not evidence of potency.</div>" + pl + limitations(d.conf.limitations);
  },
  after: function (d) {
    var cf = d.conf.conformers || [];
    if (cf.length) demoRender3D("demo-conf3d", cf[0].molblock);
  }
};

DEMO_IMPL.evidence = {
  why: "Decisions need evidence you can audit. The distribution below is real measured data. The assistant then answers a question " +
    "only from indexed records, and every claim it makes is checked against a cited excerpt.",
  load: function () {
    return Promise.all([demoActivity(), api("/api/v1/copilot/status").catch(function () { return {}; })])
      .then(function (r) { return { activity: r[0], copilot: r[1] }; });
  },
  render: function (d) {
    var a = d.activity, st = a.statistics || {};
    var ai = demoState.aiBusy ? skeleton(4) + '<p class="muted">Asking the assistant. This typically takes about 15 seconds...</p>' :
      demoState.aiAnswer ? demoAiHtml(demoState.aiAnswer) :
        '<p class="muted">Live generation: ' + (d.copilot.model_generation_available ? "on" : "off (a deterministic answer is returned instead)") + "</p>";
    return panel("Distribution of measured JAK1 potency (all " + a.matched + " curated compounds)",
      histogramSVG(a.histogram, a.histogram_axis) +
      '<p class="muted">Median ' + esc(st.median) + ", mean " + esc(st.mean) + ", " + esc(st.unique_scaffolds) + " unique scaffolds.</p>",
      chip("experimental") + " " + chip("derived")) +
      panel("Grounded assistant", '<p>Question: <span class="mono">Which indexed compounds have direct experimental evidence?</span></p>' +
        '<p class="muted">Note: the literature corpus covers plant phytochemistry, so the assistant answers from that corpus. It holds no JAK1 literature.</p>' +
        '<div class="actions"><button class="btn primary" id="demo-ask"' + (demoState.aiBusy ? " disabled" : "") + ">Ask the assistant</button></div>" +
        '<div id="demo-ai">' + ai + "</div>");
  },
  after: function () {
    var b = document.getElementById("demo-ask");
    if (!b) return;
    b.addEventListener("click", function () {
      demoState.aiBusy = true; demoState.aiAnswer = null;
      var tok = demoState.token;
      demoRenderStep(true);
      demoPost("/api/assistant", { question: "Which indexed compounds have direct experimental evidence?" }).then(function (a) {
        demoState.aiAnswer = { ok: true, data: a };
      }).catch(function (e) {
        demoState.aiAnswer = { ok: false, error: e.message };
      }).then(function () {
        demoState.aiBusy = false;
        if (DEMO_STEPS[demoState.step].id === "evidence") demoRenderStep(true);
      });
    });
  }
};

function demoAiHtml(r) {
  if (!r.ok) return errorState("Assistant did not answer", r.error);
  var a = r.data, vs = a.verification_summary || {};
  var refs = (a.references || []).slice(0, 4).map(function (x) {
    var pub = x.publication || {};
    return "<li>" + esc((x.compound || {}).name || "") + " : " + esc(pub.title || "") + " (PMID " + esc(pub.pmid || "n/a") + ")</li>";
  }).join("");
  return '<div class="note">' + chip("unverified", a.llm_used ? "AI-drafted" : "deterministic") + " " + esc(a.message || "") + "</div>" +
    '<p style="white-space:pre-wrap">' + esc(a.answer) + "</p>" +
    "<p>Claim verification: " + chip("experimental", "supported " + (vs.supported || 0)) + " " +
    chip("contradiction", "contradicted " + (vs.contradicted || 0)) + " " + chip("missing", "not supported " + (vs.not_supported || 0)) +
    " (rejected claims: " + (a.rejected_claims || []).length + ")</p>" +
    "<p><strong>Sources</strong></p><ul>" + refs + "</ul>" + limitations(a.limitations);
}

DEMO_IMPL.formulation = {
  why: "Making a molecule into a product adds a second set of questions. MOLECULAB records formulation components, process conditions " +
    "and constraints, and reports which constraints are satisfied, unresolved or unmeasured. It deliberately computes no overall score.",
  load: function () {
    return api("/api/v1/formulations").then(function (l) {
      if (!l.items || !l.items.length) throw new Error("No formulation records exist.");
      return api("/api/v1/formulations/" + l.items[0].id);
    });
  },
  render: function (f) {
    var ev = f.evaluation || {}, sc = ev.status_counts || {};
    var comps = (f.components || []).map(function (c) {
      return "<tr><td>" + esc(c.material) + "</td><td>" + esc((c.role || "").replace(/_/g, " ")) + '</td><td class="mono">' +
        esc(c.amount) + " " + esc(c.amount_unit) + "</td></tr>";
    }).join("");
    var cons = (ev.constraints || []).map(function (c) {
      var k = c.status === "satisfied" ? "experimental" : c.status === "violated" ? "contradiction" : c.status === "not_measured" ? "missing" : "unverified";
      return "<tr><td>" + esc(c.name) + "</td><td>" + esc(c.priority) + "</td><td>" + chip(k, c.status.replace(/_/g, " ")) +
        '</td><td class="muted">' + esc(c.reason) + "</td></tr>";
    }).join("");
    var to = ((f.trade_offs || {}).trade_offs || []).map(function (t) {
      return "<li>" + esc(t.principle) + " <span class=\"muted\">(" + esc(t.evidence_status) + "; for this material: " +
        esc(t.applies_to_this_material) + ")</span></li>";
    }).join("");
    return panel(f.name, "<p>" + esc(f.research_question || "") + '</p><div class="demo-mol"><div><table class="dtable"><thead><tr><th>Material</th><th>Role</th><th>Amount</th></tr></thead><tbody>' +
      comps + "</tbody></table></div><div><p>Constraint status: " + Object.keys(sc).map(function (k) { return esc(k.replace(/_/g, " ")) + " " + sc[k]; }).join(" | ") +
      '</p><div class="note caution">' + esc(ev.readiness_statement || "") + "</div></div></div>", chip("unverified", "researcher-entered record")) +
      panel("Constraints", '<div class="demo-scroll"><table class="dtable"><thead><tr><th>Constraint</th><th>Priority</th><th>Status</th><th>Why</th></tr></thead><tbody>' +
        cons + "</tbody></table></div><p class=\"muted\">" + esc(ev.no_composite_score || "") + "</p>") +
      panel("Known trade-offs (general principles, cited)", "<ul>" + to + "</ul>" + limitations((f.trade_offs || {}).limitations));
  }
};

DEMO_IMPL.experiment = {
  why: "Instead of guessing, rank the proposed experiments by how much unresolved, high-priority uncertainty each one would reduce. " +
    "The formula is shown so a scientist can disagree with it.",
  load: function () {
    return api("/api/v1/formulations").then(function (l) {
      if (!l.items || !l.items.length) throw new Error("No formulation records exist.");
      return api("/api/v1/formulations/" + l.items[0].id + "/experiments/priority");
    });
  },
  render: function (d) {
    var rows = (d.experiments || []).map(function (e) {
      return "<tr><td><strong>#" + e.rank + "</strong></td><td>" + esc(e.title) + '</td><td class="mono">' + demoNum(e.information_value, 2) +
        '</td><td class="mono">' + (e.estimated_cost !== null && e.estimated_cost !== undefined ? esc(e.estimated_cost + " " + (e.cost_currency || "")) : "-") +
        '</td><td class="muted">' + esc(e.estimate_basis || "") + "</td></tr>";
    }).join("");
    var assume = (d.assumptions || []).map(function (a) { return "<li>" + esc(a) + "</li>"; }).join("");
    return panel("Experiments ranked by information value", '<div class="demo-scroll"><table class="dtable"><thead><tr><th>Rank</th><th>Experiment</th><th>Info value</th><th>Est. cost</th><th>Cost basis</th></tr></thead><tbody>' +
      rows + "</tbody></table></div>" + '<p class="mono muted">' + esc((d.method || {}).formula || "") + "</p>", chip("derived", "rule-based ranking")) +
      panel("Assumptions", "<ul>" + assume + "</ul>" + limitations(d.limitations)) +
      '<div class="note caution"><strong>End of tour.</strong> This ranking suggests what to measure next. It does not replace the laboratory, ' +
      "and nothing shown in this tour constitutes experimental validation of any compound or formulation.</div>";
  }
};

// ------------------------------------------------------------------ shell and navigation
function demoRail() {
  return DEMO_STEPS.map(function (s, i) {
    var cls = (i === demoState.step ? "on " : "") + (i < demoState.step ? "done" : "");
    return '<button class="' + cls + '" data-go="' + i + '"><span class="n">' + (i + 1) + '</span><span><span class="t">' +
      esc(s.title) + '</span><br><span class="s">' + esc(s.sub) + "</span></span></button>";
  }).join("");
}

function demoNav() {
  var i = demoState.step, last = i === DEMO_STEPS.length - 1;
  return '<div class="demo-nav"><button class="btn ghost" id="demo-back"' + (i === 0 ? " disabled" : "") + ">Back</button>" +
    '<span class="muted">Step ' + (i + 1) + " of " + DEMO_STEPS.length + '</span>' +
    '<span><button class="btn ghost" id="demo-rerun">Re-run live</button> ' +
    '<button class="btn primary" id="demo-next"' + (last ? " disabled" : "") + ">Next</button></span></div>";
}

function demoShell(stageHtml) {
  var pct = Math.round(((demoState.step + 1) / DEMO_STEPS.length) * 100);
  var st = DEMO_STEPS[demoState.step], impl = DEMO_IMPL[st.id];
  return '<div class="page-head"><div class="eyebrow">Guided demo - live application, live APIs</div>' +
    "<h1>MOLECULAB: from disease to experiment</h1>" +
    '<p class="lede">One research story, nine steps. Every result on these screens comes from a real request to this server. ' +
    "Failures are shown, not hidden.</p></div>" +
    '<div class="demo-shell"><aside><div class="demo-prog" aria-label="progress"><span style="width:' + pct + '%"></span></div>' +
    '<nav class="demo-rail">' + demoRail() + '</nav></aside><main>' +
    "<h2>" + (demoState.step + 1) + ". " + esc(st.title) + "</h2>" +
    '<p class="demo-why"><strong>Why it matters:</strong> ' + esc(impl.why) + "</p>" +
    '<div id="demo-stage">' + stageHtml + "</div>" + demoNav() + "</main></div>";
}

function demoWire() {
  Array.prototype.forEach.call(view.querySelectorAll("[data-go]"), function (b) {
    b.addEventListener("click", function () { demoGo(parseInt(b.getAttribute("data-go"), 10)); });
  });
  var back = document.getElementById("demo-back"), next = document.getElementById("demo-next"),
    rerun = document.getElementById("demo-rerun");
  if (back) back.addEventListener("click", function () { demoGo(demoState.step - 1); });
  if (next) next.addEventListener("click", function () { demoGo(demoState.step + 1); });
  if (rerun) rerun.addEventListener("click", function () {
    var id = DEMO_STEPS[demoState.step].id;
    delete demoState.cache[id];
    if (id === "compound" || id === "evidence") delete demoState.cache.activity;
    if (id === "evidence") { demoState.aiAnswer = null; }
    demoRenderStep();
  });
}

function demoGo(i) {
  if (i < 0 || i >= DEMO_STEPS.length) return;
  demoState.step = i;
  demoRenderStep();
}

function demoRenderStep(keepCache) {
  var st = DEMO_STEPS[demoState.step], impl = DEMO_IMPL[st.id];
  var token = ++demoState.token;
  var cached = demoState.cache[st.id];

  function show(data) {
    if (token !== demoState.token) return;
    var html;
    try { html = impl.render(data); } catch (err) { html = errorState("Could not render this result", err.message); }
    view.innerHTML = demoShell(html);
    demoWire();
    try { if (impl.after) impl.after(data); } catch (err2) { /* leave the rendered result in place */ }
  }

  if (cached) { show(cached); return; }
  view.innerHTML = demoShell(skeleton(5) + '<p class="muted">Calling the live API for this step...</p>');
  demoWire();
  impl.load().then(function (data) {
    demoState.cache[st.id] = data;
    show(data);
  }).catch(function (err) {
    if (token !== demoState.token) return;
    view.innerHTML = demoShell(errorState("This step could not complete", err.message,
      '<p class="muted">The request failed, so nothing is shown. Use Re-run live to try again. If a live provider is down, see the demo guide fallback.</p>'));
    demoWire();
  });
}

async function demoPage(params) {
  demoStyle();
  var want = params && typeof params === "object" && params.step !== undefined ? parseInt(params.step, 10) : NaN;
  if (!isNaN(want) && want >= 1 && want <= DEMO_STEPS.length) demoState.step = want - 1;
  demoRenderStep();
}
