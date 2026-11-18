// MOLECULAB Formulation Research Studio.
//
// A record system, not a predictor. MOLECULAB does not know whether a tablet will be hard enough
// or dissolve fast enough; it holds the researcher's constraints and measurements and reports,
// honestly, what the evidence does and does not establish.
//
// The one rule this screen exists to enforce visually: a requirement with no confirming
// measurement is shown as an OPEN QUESTION, never as met. There is no overall readiness score.
"use strict";

const CONSTRAINT_STATUS = {
  satisfied: { kind: "experimental", label: "Satisfied" },
  violated: { kind: "contradiction", label: "Violated" },
  uncertain: { kind: "hypothesis", label: "Uncertain" },
  conflicting: { kind: "contradiction", label: "Conflicting" },
  not_measured: { kind: "missing", label: "Not measured" },
  not_applicable: { kind: "unverified", label: "Not applicable" },
};

const MEASUREMENT_KIND = {
  experimental: "experimental", literature: "experimental",
  supplier_specification: "experimental", computed: "computed",
  predicted: "predicted", researcher_estimate: "hypothesis",
};

let formState = { id: null, vocab: null, detail: null };

function statusChip(status) {
  const s = CONSTRAINT_STATUS[status] || CONSTRAINT_STATUS.not_applicable;
  return chip(s.kind, s.label);
}

function acceptanceText(a) {
  if (a.qualitative) return esc(a.qualitative);
  const unit = a.unit ? " " + esc(a.unit) : "";
  if (a.min !== null && a.max !== null) return esc(a.min) + "–" + esc(a.max) + unit;
  if (a.min !== null) return "≥ " + esc(a.min) + unit;
  if (a.max !== null) return "≤ " + esc(a.max) + unit;
  return '<span class="muted">no range defined</span>';
}

// ------------------------------------------------------------------ list

async function formulationsPage(params) {
  const fid = params && params.get("id");
  if (fid) return formulationDetail(+fid);

  view.innerHTML =
    '<div class="page-head"><div><div class="eyebrow">Formulation research</div>' +
    "<h1>Formulation studio</h1>" +
    '<p class="lede">Define formulation candidates, record what each requirement actually ' +
    "demands, and track which requirements the evidence supports. MOLECULAB does not predict " +
    "formulation performance; it keeps the question honest.</p></div>" +
    '<div class="actions"><button class="btn primary" id="newform">New candidate</button></div></div>' +
    '<div id="flbody">' + skeleton(4) + "</div>";

  document.getElementById("newform").onclick = async function () {
    const name = prompt("Name for this formulation candidate:");
    if (!name) return;
    const created = await api("/api/v1/formulations", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: name, dosage_form: "tablet" }),
    });
    location.hash = "#/formulations?id=" + created.id;
  };

  const data = await api("/api/v1/formulations");
  document.getElementById("flbody").innerHTML = data.count
    ? panel("Candidates (" + data.count + ")",
        '<table class="dtable"><thead><tr><th>Name</th><th>Dosage form</th>' +
        '<th class="num">Components</th><th class="num">Constraints</th><th></th></tr></thead><tbody>' +
        data.items.map(function (f) {
          return "<tr><td><strong>" + esc(f.name) + "</strong>" +
            (f.research_question ? '<br><span class="sub">' + esc(f.research_question.slice(0, 90)) +
              "</span>" : "") +
            "</td><td>" + esc(f.dosage_form) + '</td><td class="num">' + f.component_count +
            '</td><td class="num">' + f.constraint_count + "</td>" +
            '<td><a class="btn ghost" href="#/formulations?id=' + f.id + '">Open</a></td></tr>';
        }).join("") + "</tbody></table>", "", "scroll")
    : emptyState("No formulation candidates yet",
        "A candidate holds an active ingredient, its excipients, the requirements it must meet, " +
        "and the measurements bearing on them.",
        '<button class="btn primary" onclick="document.getElementById(\'newform\').click()">' +
        "Create the first candidate</button>");
}

// ---------------------------------------------------------------- detail

async function formulationDetail(fid) {
  formState.id = fid;
  view.innerHTML = '<div id="fdbody">' + skeleton(6) + "</div>";
  const [detail, vocab] = await Promise.all([
    api("/api/v1/formulations/" + fid),
    formState.vocab ? Promise.resolve(formState.vocab) : api("/api/v1/formulations/vocabulary"),
  ]);
  formState.vocab = vocab;
  formState.detail = detail;
  renderFormulation(detail, vocab);
}

function renderFormulation(d, vocab) {
  const ev = d.evaluation;
  const counts = ev.status_counts || {};
  const order = ["satisfied", "violated", "uncertain", "conflicting", "not_measured", "not_applicable"];

  view.innerHTML =
    '<div class="page-head"><div><div class="eyebrow">Formulation candidate</div>' +
    "<h1>" + esc(d.name) + "</h1>" +
    '<p class="lede">' + esc(d.dosage_form) +
    (d.intended_release ? " · " + esc(d.intended_release) : "") +
    (d.research_question ? "<br>" + esc(d.research_question) : "") + "</p></div>" +
    '<div class="actions"><a class="btn" href="#/formulations">All candidates</a></div></div>' +

    // Readiness is a sentence, never a percentage.
    '<p class="' + (ev.violated_mandatory.length ? "limit" : "note") + '"><strong>' +
    (ev.violated_mandatory.length ? "Specification not met. " :
      ev.unresolved_mandatory.length ? "Open questions remain. " : "Evidence recorded. ") +
    "</strong>" + esc(ev.readiness_statement) + "</p>" +

    '<div class="tiles" style="margin-bottom:14px">' +
    order.filter(function (s) { return counts[s]; }).map(function (s) {
      return tile(CONSTRAINT_STATUS[s].label, counts[s], statusChip(s));
    }).join("") + "</div>" +

    '<div class="ws" style="grid-template-columns:minmax(0,1.45fr) minmax(0,1fr)">' +
    '<div class="stack">' + constraintsPanel(d) + componentsPanel(d) + conditionsPanel(d) + "</div>" +
    '<div class="stack">' + tradeOffPanel(d) + experimentPanel(d) + compatPanel(d) + "</div>" +
    "</div>" +
    '<p class="note" style="margin-top:16px">' + esc(ev.no_composite_score) + "</p>";

  wireFormulationForms(d, vocab);
  loadExperimentPriority(d.id);
  loadCompatibility(d.id);
}

function constraintsPanel(d) {
  const rows = d.evaluation.constraints.map(function (c) {
    const values = (c.values || []).map(function (v) {
      return '<div style="padding:3px 0;border-top:1px solid var(--line)">' +
        chip(MEASUREMENT_KIND[v.kind] || "unverified", v.kind.replace(/_/g, " ")) + " " +
        measure(v.value !== null ? v.value : v.value_text, v.unit) +
        (v.uncertainty ? ' <span class="muted">± ' + esc(v.uncertainty) + "</span>" : "") +
        (v.n_replicates ? ' <span class="muted">n=' + esc(v.n_replicates) + "</span>" : "") +
        (v.can_close_constraint ? "" :
          ' <span class="muted">(cannot close this requirement)</span>') +
        (v.source ? '<br><span class="sub">' + esc(v.source) + "</span>" : "") + "</div>";
    }).join("");
    return "<tr><td><strong>" + esc(c.name) + "</strong><br>" +
      '<span class="sub">' + esc(c.category.replace(/_/g, " ")) + " · " + esc(c.priority) +
      "</span>" + (values ? "<div style=\"margin-top:5px\">" + values + "</div>" : "") +
      "</td><td>" + acceptanceText(c.acceptance) + "</td><td>" + statusChip(c.status) +
      '<div class="sub" style="margin-top:4px;max-width:34ch">' + esc(c.reason) + "</div></td>" +
      '<td><button class="btn ghost" data-measure="' + c.constraint_id + '">+ measurement</button></td></tr>';
  }).join("");

  return panel("Requirements",
    (rows ? '<table class="dtable"><thead><tr><th>Constraint</th><th>Acceptance</th>' +
      "<th>Status</th><th></th></tr></thead><tbody>" + rows + "</tbody></table>"
      : emptyState("No requirements defined",
          "Add the properties this formulation must meet, with their acceptance ranges.")) +
    '<form id="cform" class="row" style="margin-top:10px;gap:6px">' +
    '<select id="ccat">' + (formState.vocab ? formState.vocab.constraint_categories.map(function (c) {
      return '<option value="' + esc(c) + '">' + esc(c.replace(/_/g, " ")) + "</option>";
    }).join("") : "") + "</select>" +
    '<input id="cname" placeholder="Requirement name" required style="flex:1;min-width:130px">' +
    '<select id="cprio">' + (formState.vocab ? formState.vocab.constraint_priorities.map(function (p) {
      return '<option value="' + esc(p) + '">' + esc(p) + "</option>";
    }).join("") : "") + "</select>" +
    '<input id="cmin" type="number" step="any" placeholder="min" style="width:76px">' +
    '<input id="cmax" type="number" step="any" placeholder="max" style="width:76px">' +
    '<input id="cunit" placeholder="unit" style="width:70px">' +
    '<button class="btn primary" type="submit">Add</button></form>',
    chip("missing", (d.evaluation.unresolved_mandatory.length || 0) + " open"));
}

function componentsPanel(d) {
  const rows = (d.components || []).map(function (c) {
    return "<tr><td>" + esc(c.material) + "</td><td>" + esc(c.role.replace(/_/g, " ")) +
      '</td><td class="num">' + measure(c.amount, c.amount_unit) + "</td><td>" +
      esc(c.amount_basis || "—") + "</td></tr>";
  }).join("");
  return panel("Composition",
    (rows ? '<table class="dtable"><thead><tr><th>Material</th><th>Role</th>' +
      '<th class="num">Amount</th><th>Basis</th></tr></thead><tbody>' + rows + "</tbody></table>"
      : emptyState("No materials added", "Add the active ingredient and excipients.")) +
    '<form id="mform" class="row" style="margin-top:10px;gap:6px">' +
    '<select id="mmat" style="flex:1;min-width:150px"></select>' +
    '<select id="mrole">' + (formState.vocab ? formState.vocab.material_roles.map(function (r) {
      return '<option value="' + esc(r) + '">' + esc(r.replace(/_/g, " ")) + "</option>";
    }).join("") : "") + "</select>" +
    '<input id="mamt" type="number" step="any" placeholder="amount" style="width:90px">' +
    '<input id="munit" placeholder="unit" style="width:70px" value="mg">' +
    '<button class="btn" type="submit">Add</button></form>' +
    '<form id="nmform" class="row" style="margin-top:6px;gap:6px">' +
    '<input id="nmname" placeholder="New material name" style="flex:1;min-width:150px">' +
    '<button class="btn ghost" type="submit">Create material</button></form>');
}

function conditionsPanel(d) {
  const rows = (d.conditions || []).map(function (p) {
    return "<tr><td>" + esc(p.process_step || "—") + "</td><td>" + esc(p.parameter) +
      '</td><td class="num">' + measure(p.value !== null ? p.value : p.value_text, p.unit) +
      "</td><td>" + (p.is_target ? chip("hypothesis", "Target") : chip("experimental", "Measured")) +
      "</td></tr>";
  }).join("");
  return panel("Processing conditions",
    (rows ? '<table class="dtable"><thead><tr><th>Step</th><th>Parameter</th>' +
      '<th class="num">Value</th><th>Kind</th></tr></thead><tbody>' + rows + "</tbody></table>"
      : emptyState("No conditions recorded",
          "Record temperature, humidity, pressure or duration, and whether each is a target or a measurement.")) +
    '<form id="pform" class="row" style="margin-top:10px;gap:6px">' +
    '<input id="pstep" placeholder="step (drying)" style="width:120px">' +
    '<input id="pparam" placeholder="parameter" required style="flex:1;min-width:110px">' +
    '<input id="pval" type="number" step="any" placeholder="value" style="width:84px">' +
    '<input id="punit" placeholder="unit" style="width:64px">' +
    '<label class="row" style="gap:4px"><input type="checkbox" id="ptarget" checked> target</label>' +
    '<button class="btn" type="submit">Add</button></form>');
}

function tradeOffPanel(d) {
  const t = d.trade_offs || { trade_offs: [] };
  const body = t.trade_offs.length
    ? t.trade_offs.map(function (x) {
        return '<div style="padding:9px 0;border-bottom:1px solid var(--line)">' +
          '<div class="row" style="gap:5px">' + chip("hypothesis", "General principle") +
          '<span class="sub">' + esc(x.between.join(" ↔ ")) + "</span></div>" +
          '<p style="margin:5px 0 3px;font-size:12.5px">' + esc(x.principle) + "</p>" +
          '<p class="sub">' + esc(x.direction) + "</p>" +
          '<p class="sub">Source: ' + esc(x.source) + "</p>" +
          '<p class="sub">Applies to this material: <strong>' +
          esc(x.applies_to_this_material) + "</strong></p></div>";
      }).join("")
    : emptyState("No trade-offs surfaced",
        "Trade-offs appear once two related requirement categories are both constrained.");
  return panel("Trade-offs to investigate", body +
    '<p class="note caution" style="margin-top:10px">' + esc(t.interpretation || "") + "</p>" +
    limitations(t.limitations), chip("hypothesis", "Principles"));
}

function experimentPanel(d) {
  return panel("Experiment priority", '<div id="expbody">' + skeleton(3) + "</div>" +
    '<form id="eform" class="row" style="margin-top:10px;gap:6px">' +
    '<input id="etitle" placeholder="Proposed experiment" required style="flex:1;min-width:150px">' +
    '<input id="ecost" type="number" step="any" placeholder="cost est." style="width:96px">' +
    '<select id="econ" multiple size="3" style="min-width:150px"></select>' +
    '<button class="btn" type="submit">Propose</button></form>' +
    '<p class="sub">Cost is your own estimate. MOLECULAB has no price data.</p>');
}

function compatPanel() {
  return panel("Material compatibility", '<div id="compatbody">' + skeleton(2) + "</div>");
}

async function loadExperimentPriority(fid) {
  const host = document.getElementById("expbody");
  if (!host) return;
  try {
    const data = await api("/api/v1/formulations/" + fid + "/experiments/priority");
    host.innerHTML = data.experiments.length
      ? '<table class="dtable"><thead><tr><th>#</th><th>Experiment</th>' +
        '<th class="num">Info value</th><th>Note</th></tr></thead><tbody>' +
        data.experiments.map(function (e) {
          return "<tr><td>" + e.rank + "</td><td>" + esc(e.title) +
            '<div class="sub">addresses ' + e.addresses_constraints + " constraint(s)" +
            (e.estimated_cost ? " · est. " + esc(e.estimated_cost) + " " +
              esc(e.cost_currency || "") : "") + "</div></td>" +
            '<td class="num">' + measure(e.information_value, "", 2) + "</td><td>" +
            (e.likely_redundant ? chip("missing", "Likely redundant") : chip("computed", "Informative")) +
            "</td></tr>";
        }).join("") + "</tbody></table>" +
        "<details style=\"margin-top:8px\"><summary>How this ranking is computed</summary>" +
        '<p class="sub"><code>' + esc(data.method.formula) + "</code></p>" +
        limitations(data.assumptions) + "</details>"
      : emptyState("No experiments proposed",
          "Propose an experiment and link it to the requirements it would measure.");
  } catch (err) {
    host.innerHTML = errorState("Could not rank experiments", err.message);
  }
}

async function loadCompatibility(fid) {
  const host = document.getElementById("compatbody");
  if (!host) return;
  try {
    const data = await api("/api/v1/formulations/compatibility/matrix?formulation_id=" + fid);
    host.innerHTML = data.pair_count
      ? '<p class="sub">' + data.evaluated_pairs + " of " + data.pair_count +
        " material pairs have a recorded assessment.</p>" +
        '<table class="dtable"><thead><tr><th>Pair</th><th>Verdict</th></tr></thead><tbody>' +
        data.pairs.map(function (p) {
          const known = p.verdict !== "not_evaluated";
          return "<tr><td>" + esc(p.material_a.name) + " + " + esc(p.material_b.name) +
            "</td><td>" + chip(known ? (p.verdict === "demonstrated_incompatible"
              ? "contradiction" : "experimental") : "missing",
              p.verdict.replace(/_/g, " ")) + "</td></tr>";
        }).join("") + "</tbody></table>" +
        '<p class="note caution" style="margin-top:8px">' + esc(data.interpretation) + "</p>"
      : emptyState("No material pairs", "Add at least two materials to assess compatibility.");
  } catch (err) {
    host.innerHTML = errorState("Could not load compatibility", err.message);
  }
}

function wireFormulationForms(d, vocab) {
  const reload = function () { formulationDetail(d.id); };

  const cform = document.getElementById("cform");
  if (cform) cform.onsubmit = async function (ev) {
    ev.preventDefault();
    const body = {
      category: document.getElementById("ccat").value,
      name: document.getElementById("cname").value.trim(),
      priority: document.getElementById("cprio").value,
      unit: document.getElementById("cunit").value.trim() || null,
      min_value: document.getElementById("cmin").value === "" ? null : +document.getElementById("cmin").value,
      max_value: document.getElementById("cmax").value === "" ? null : +document.getElementById("cmax").value,
    };
    try { await api("/api/v1/formulations/" + d.id + "/constraints", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      reload();
    } catch (err) { alert("Could not add requirement: " + err.message); }
  };

  view.querySelectorAll("[data-measure]").forEach(function (b) {
    b.onclick = async function () {
      const kind = prompt("Measurement kind (" + vocab.measurement_kinds.join(", ") + "):",
                          "experimental");
      if (!kind) return;
      const raw = prompt("Value (number), optionally with ± uncertainty, e.g. 78 or 78±3:");
      if (!raw) return;
      const parts = String(raw).split(/[±+]-?/);
      const body = { kind: kind, value: parseFloat(parts[0]),
                     uncertainty: parts[1] ? Math.abs(parseFloat(parts[1])) : null,
                     source: prompt("Source (publication, run id, supplier sheet):") || null };
      try { await api("/api/v1/formulations/constraints/" + b.dataset.measure + "/measurements", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
        reload();
      } catch (err) { alert("Could not add measurement: " + err.message); }
    };
  });

  // material pickers
  api("/api/v1/formulations/materials").then(function (data) {
    const sel = document.getElementById("mmat");
    if (sel) sel.innerHTML = data.items.map(function (m) {
      return '<option value="' + m.id + '">' + esc(m.name) + "</option>";
    }).join("") || '<option value="">(create a material first)</option>';
  });

  const mform = document.getElementById("mform");
  if (mform) mform.onsubmit = async function (ev) {
    ev.preventDefault();
    const mid = document.getElementById("mmat").value;
    if (!mid) return alert("Create a material first.");
    try { await api("/api/v1/formulations/" + d.id + "/components", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ material_id: +mid, role: document.getElementById("mrole").value,
        amount: document.getElementById("mamt").value === "" ? null : +document.getElementById("mamt").value,
        amount_unit: document.getElementById("munit").value.trim() || null }) });
      reload();
    } catch (err) { alert("Could not add component: " + err.message); }
  };

  const nmform = document.getElementById("nmform");
  if (nmform) nmform.onsubmit = async function (ev) {
    ev.preventDefault();
    const name = document.getElementById("nmname").value.trim();
    if (!name) return;
    try { await api("/api/v1/formulations/materials", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: name }) });
      reload();
    } catch (err) { alert("Could not create material: " + err.message); }
  };

  const pform = document.getElementById("pform");
  if (pform) pform.onsubmit = async function (ev) {
    ev.preventDefault();
    try { await api("/api/v1/formulations/" + d.id + "/conditions", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({
        process_step: document.getElementById("pstep").value.trim() || null,
        parameter: document.getElementById("pparam").value.trim(),
        value: document.getElementById("pval").value === "" ? null : +document.getElementById("pval").value,
        unit: document.getElementById("punit").value.trim() || null,
        is_target: document.getElementById("ptarget").checked }) });
      reload();
    } catch (err) { alert("Could not add condition: " + err.message); }
  };

  const econ = document.getElementById("econ");
  if (econ) econ.innerHTML = d.evaluation.constraints.map(function (c) {
    return '<option value="' + c.constraint_id + '">' + esc(c.name) + " (" + esc(c.status) + ")</option>";
  }).join("");

  const eform = document.getElementById("eform");
  if (eform) eform.onsubmit = async function (ev) {
    ev.preventDefault();
    const picked = [...document.getElementById("econ").selectedOptions].map(function (o) { return +o.value; });
    const cost = document.getElementById("ecost").value;
    try { await api("/api/v1/formulations/experiments", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ title: document.getElementById("etitle").value.trim(),
        formulation_id: d.id, addresses_constraint_ids: picked,
        estimated_cost: cost === "" ? null : +cost,
        cost_currency: cost === "" ? null : "USD",
        estimate_basis: cost === "" ? null : "researcher estimate" }) });
      reload();
    } catch (err) { alert("Could not add experiment: " + err.message); }
  };
}
