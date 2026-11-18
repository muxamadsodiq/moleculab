// MOLECULAB Conditions Explorer.
//
// Browses documented processing conditions across formulations. Every row is labelled TARGET
// (an intended setting) or MEASURED. A target is never presented as an observation, and a
// formulation with no documented value for a parameter is shown as a gap, not as zero or "same".
"use strict";

const CX_BASE = "/api/v1/materials-explorer";
const cxState = { parameter: "", step: "", kind: "", selected: null, items: [], params: null, cmp: [] };

function cxKindChip(c) {
  return c.is_target ? chip("hypothesis", "Target (intended)") : chip("experimental", "Measured");
}

function cxValue(c) {
  if (c.value !== null && c.value !== undefined) return measure(c.value, c.unit);
  if (c.value_text) return esc(c.value_text) + (c.unit ? " " + esc(c.unit) : "");
  return chip("missing", "Not recorded");
}

async function conditionsPage(params) {
  const main = document.getElementById("view");
  main.classList.add("workstation");
  if (params) {
    if (params.get("parameter")) cxState.parameter = params.get("parameter");
    if (params.get("process_step")) cxState.step = params.get("process_step");
    if (params.get("is_target")) cxState.kind = params.get("is_target");
  }
  main.innerHTML =
    '<div class="wx-head"><div><div class="eyebrow">Formulation research</div>' +
    "<h1>Conditions explorer</h1></div>" +
    '<div class="wx-sub">Processing conditions documented across formulations. Targets are intended ' +
    "settings; only rows labelled Measured are observations.</div>" +
    '<div class="right"><a class="btn" href="#/materials">Material explorer</a></div></div>' +
    '<div class="wx-strip" id="cxstrip"></div>' +
    '<div class="wx-body wx-3">' +
    '<div class="wx-col" id="cxleft">' + skeleton(4) + "</div>" +
    '<div class="wx-col" id="cxmain">' + skeleton(5) + "</div>" +
    '<div class="wx-col" id="cxright"></div></div>';
  try {
    cxState.params = await api(CX_BASE + "/conditions/parameters");
  } catch (err) {
    document.getElementById("cxleft").innerHTML = errorState("Could not load parameters", err.message);
    document.getElementById("cxmain").innerHTML = "";
    return;
  }
  cxRenderLeft();
  await cxLoad();
}

function cxRenderLeft() {
  const p = cxState.params;
  const left = document.getElementById("cxleft");
  if (!p.total_conditions) {
    left.innerHTML = emptyState("No conditions recorded", "No formulation has a processing condition yet.");
    return;
  }
  const kinds = [["", "All"], ["true", "Target"], ["false", "Measured"]];
  left.innerHTML =
    '<div class="wx-sec"><h4>Value kind</h4><div class="in"><div class="seg" id="cxkind">' +
    kinds.map(function (k) {
      return '<button data-k="' + k[0] + '" class="' + (cxState.kind === k[0] ? "active" : "") + '">' + k[1] + "</button>";
    }).join("") + "</div></div></div>" +
    '<div class="wx-sec"><h4>Parameter</h4><div class="in wx-list">' +
    '<button class="pick' + (cxState.parameter ? "" : " active") + '" data-param="">All parameters</button>' +
    p.parameters.map(function (x) {
      return '<button class="pick' + (cxState.parameter.toLowerCase() === x.parameter.toLowerCase() ? " active" : "") +
        '" data-param="' + esc(x.parameter) + '"><strong>' + esc(x.parameter) + '</strong><span class="meta">' +
        x.count + " rows · " + (x.units.length ? esc(x.units.join(", ")) : "no unit") +
        (x.mixed_units ? " · MIXED UNITS" : "") + " · " + x.target_count + " target / " +
        x.measured_count + " measured</span></button>";
    }).join("") + "</div></div>" +
    '<div class="wx-sec"><h4>Process step</h4><div class="in wx-list">' +
    '<button class="pick' + (cxState.step ? "" : " active") + '" data-step="">All steps</button>' +
    p.process_steps.map(function (s) {
      return '<button class="pick' + (cxState.step.toLowerCase() === s.process_step ? " active" : "") +
        '" data-step="' + esc(s.process_step) + '"><strong>' + esc(s.process_step) + '</strong><span class="meta">' +
        s.count + " rows</span></button>";
    }).join("") + "</div></div>" +
    '<div class="limit">' + limitations(p.limitations) + "</div>";
  left.querySelectorAll("#cxkind button").forEach(function (b) {
    b.onclick = function () { cxState.kind = b.dataset.k; cxRenderLeft(); cxLoad(); };
  });
  left.querySelectorAll("button[data-param]").forEach(function (b) {
    b.onclick = function () { cxState.parameter = b.dataset.param; cxRenderLeft(); cxLoad(); };
  });
  left.querySelectorAll("button[data-step]").forEach(function (b) {
    b.onclick = function () { cxState.step = b.dataset.step; cxRenderLeft(); cxLoad(); };
  });
}

async function cxLoad() {
  const centre = document.getElementById("cxmain");
  centre.innerHTML = skeleton(5);
  const qs = new URLSearchParams();
  if (cxState.parameter) qs.set("parameter", cxState.parameter);
  if (cxState.step) qs.set("process_step", cxState.step);
  if (cxState.kind) qs.set("is_target", cxState.kind);
  qs.set("limit", "500");
  try {
    const res = await api(CX_BASE + "/conditions?" + qs.toString());
    cxState.items = res.items;
    centre.innerHTML = cxCentreHtml(res);
    centre.querySelectorAll("tr[data-cid]").forEach(function (tr) {
      tr.style.cursor = "pointer";
      tr.onclick = function () { cxSelect(+tr.dataset.cid); };
    });
    const cb = document.getElementById("cxdocmp");
    if (cb) cb.onclick = cxCompare;
    cxRenderRight(cxState.items.filter(function (c) { return c.id === cxState.selected; })[0] || null);
  } catch (err) {
    centre.innerHTML = errorState("Could not load conditions", err.message);
  }
}

function cxCentreHtml(res) {
  if (!res.items.length) {
    return emptyState("No conditions match", "No documented condition matches these filters.");
  }
  const groups = {};
  const order = [];
  res.items.forEach(function (c) {
    const k = c.parameter.toLowerCase();
    if (!groups[k]) { groups[k] = []; order.push(k); }
    groups[k].push(c);
  });
  const tables = order.map(function (k) {
    const rows = groups[k];
    const units = {};
    rows.forEach(function (c) { if (c.unit) units[c.unit] = true; });
    const mixed = Object.keys(units).length > 1;
    return '<div class="wx-sec"><h4>' + esc(rows[0].parameter) + " (" + rows.length + ")</h4><div class=\"in\">" +
      (mixed ? '<div class="note caution"><strong>Mixed units.</strong> ' + esc(Object.keys(units).join(", ")) +
        " are shown as entered and not converted.</div>" : "") +
      '<table class="dtable"><thead><tr><th>Formulation</th><th>Step</th><th>Value</th><th>Kind</th></tr></thead><tbody>' +
      rows.map(function (c) {
        return '<tr data-cid="' + c.id + '" class="' + (c.id === cxState.selected ? "sel" : "") + '"><td>' +
          esc(c.formulation_name || ("Formulation " + c.formulation_id)) + "</td><td>" +
          (c.process_step ? esc(c.process_step) : chip("missing", "No step")) + "</td><td>" + cxValue(c) +
          "</td><td>" + cxKindChip(c) + "</td></tr>";
      }).join("") + "</tbody></table></div></div>";
  }).join("");
  return '<div class="tiles">' + tile("Conditions shown", res.total) +
    tile("Targets", res.items.filter(function (c) { return c.is_target; }).length, "intended settings") +
    tile("Measured", res.items.filter(function (c) { return !c.is_target; }).length, "observations") + "</div>" +
    cxChartHtml(res.items) + tables + cxCompareBox() +
    '<div class="note"><strong>Reading.</strong> ' + esc(res.interpretation) + "</div>" +
    '<div class="limit">' + limitations(res.limitations) + "</div>";
}

function cxChartHtml(items) {
  const num = items.filter(function (c) { return c.value !== null && c.value !== undefined; });
  if (!cxState.parameter) {
    return '<div class="note">Select one parameter on the left to chart its numeric values.</div>';
  }
  if (!num.length) {
    return emptyState("Nothing to chart", "No numeric value is recorded for this parameter.");
  }
  const units = {};
  num.forEach(function (c) { units[c.unit || ""] = true; });
  if (Object.keys(units).length > 1) {
    return '<div class="note caution"><strong>Not charted.</strong> Values use different units and are not converted.</div>';
  }
  const unit = num[0].unit || "no unit";
  const pts = num.map(function (c, i) {
    return { x: i + 1, y: c.value, label: (c.formulation_name || "") + (c.is_target ? " (target)" : " (measured)") };
  });
  const svg = pts.length > 1 ? scatterSVG(pts, "record #", num[0].parameter + " [" + unit + "]") :
    '<p class="sub">Only one numeric value: ' + esc(num[0].value) + " " + esc(unit) + ".</p>";
  return '<div class="wx-sec"><h4>' + esc(num[0].parameter) + " values</h4><div class=\"in\">" + svg +
    '<p class="sub">' + chip("hypothesis", "Target") + " and " + chip("experimental", "Measured") +
    " rows are plotted together here; hover a point for its label. A target is not an observation. " +
    "The shared chart caption says compounds; each point here is one condition record.</p></div></div>";
}

function cxCompareBox() {
  return '<div class="wx-sec"><h4>Compare formulations</h4><div class="in">' +
    '<input id="cxcmpids" placeholder="Formulation ids, e.g. 1,2" style="width:60%"> ' +
    '<button class="btn" id="cxdocmp">Compare</button><div id="cxcmpout"></div></div></div>';
}

async function cxCompare() {
  const out = document.getElementById("cxcmpout");
  const ids = document.getElementById("cxcmpids").value.replace(/\s+/g, "");
  out.innerHTML = skeleton(3);
  try {
    const c = await api(CX_BASE + "/conditions/compare?formulation_ids=" + encodeURIComponent(ids));
    out.innerHTML = '<table class="dtable"><thead><tr><th>Parameter</th><th>Step</th>' +
      c.formulations.map(function (f) { return "<th>" + esc(f.name) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      c.rows.map(function (r) {
        return "<tr><td>" + esc(r.parameter) + "</td><td>" + esc(r.process_step || "") + "</td>" +
          r.values.map(function (v) {
            if (!v.recorded) return "<td>" + chip("missing", "Not documented") + "</td>";
            return "<td>" + v.entries.map(function (e) {
              return "<div>" + cxValue(e) + " " + cxKindChip(e) + "</div>";
            }).join("") + "</td>";
          }).join("") + "</tr>";
      }).join("") + "</tbody></table>" +
      '<p class="sub">' + c.gap_count + " parameter(s) documented for only some of the formulations. " +
      esc(c.interpretation) + "</p>" + limitations(c.limitations);
  } catch (err) {
    out.innerHTML = errorState("Comparison failed", err.message);
  }
}

function cxSelect(id) {
  cxState.selected = id;
  document.querySelectorAll("#cxmain tr[data-cid]").forEach(function (tr) {
    tr.classList.toggle("sel", +tr.dataset.cid === id);
  });
  cxRenderRight(cxState.items.filter(function (c) { return c.id === id; })[0] || null);
}

function cxRenderRight(c) {
  const right = document.getElementById("cxright");
  if (!right) return;
  if (!c) {
    right.innerHTML = emptyState("No condition selected", "Select a row to see its method, rationale and source.");
    return;
  }
  function field(label, v) {
    return "<tr><th>" + label + "</th><td>" + (v ? esc(v) : chip("missing", "Not recorded")) + "</td></tr>";
  }
  right.innerHTML =
    '<div class="wx-sec"><h4>' + esc(c.parameter) + '</h4><div class="in">' +
    "<p>" + cxKindChip(c) + " " + cxValue(c) + "</p>" +
    '<p class="sub">' + esc(c.value_kind_note) + "</p>" +
    '<table class="kv"><tbody>' +
    "<tr><th>Formulation</th><td><a href=\"" + esc(c.formulation_link) + "\">" +
    esc(c.formulation_name || ("Formulation " + c.formulation_id)) + "</a></td></tr>" +
    field("Process step", c.process_step) + field("Method", c.method) + field("Rationale", c.rationale) +
    field("Source", c.source) + field("Physical state", c.physical_state) +
    "<tr><th>Uncertainty</th><td>" + (c.uncertainty !== null && c.uncertainty !== undefined ?
      measure(c.uncertainty, c.unit) : chip("missing", "Not recorded")) + "</td></tr>" +
    "</tbody></table></div></div>";
}
