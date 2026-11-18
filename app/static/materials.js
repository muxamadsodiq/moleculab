// MOLECULAB Material Explorer.
//
// Browses the materials actually recorded in the database. Shows what is known about each one with
// its source, and says explicitly what is NOT recorded. A pair of materials nobody has assessed is
// "not evaluated"; it is never shown as compatible, and a missing value is never shown as zero.
"use strict";

const MX_BASE = "/api/v1/materials-explorer";
const MX_ROLES = ["active_ingredient", "filler_diluent", "binder", "disintegrant", "lubricant",
  "glidant", "coating", "solvent", "buffer", "preservative", "surfactant", "other"];
const MX_VERDICT = {
  demonstrated_compatible: { kind: "experimental", label: "Demonstrated compatible" },
  demonstrated_incompatible: { kind: "contradiction", label: "Demonstrated incompatible" },
  predicted_interaction: { kind: "predicted", label: "Predicted interaction" },
  literature_concern: { kind: "hypothesis", label: "Literature concern" },
  hypothesis: { kind: "hypothesis", label: "Hypothesis" },
  not_evaluated: { kind: "missing", label: "Not evaluated" },
};
const mxState = { q: "", role: "", selected: null, compare: [], list: null };

function mxRole(r) {
  return String(r || "other").replace(/_/g, " ");
}

function mxVerdictChip(v) {
  const d = MX_VERDICT[v] || { kind: "unverified", label: String(v || "unknown") };
  return chip(d.kind, d.label);
}

function mxPropKind(k) {
  const map = { experimental: "experimental", literature: "experimental",
    supplier_specification: "experimental", computed: "computed", predicted: "predicted",
    researcher_estimate: "hypothesis" };
  return map[k] || "unverified";
}

function mxNotRecorded() {
  return chip("missing", "Not recorded");
}

function mxValue(value, unit) {
  if (value === null || value === undefined || value === "") return mxNotRecorded();
  return measure(value, unit);
}

function mxSource(src, url) {
  if (!src && !url) return chip("unverified", "No source recorded");
  let out = esc(src || "");
  if (url) out += ' <a href="' + esc(url) + '" target="_blank" rel="noopener">link</a>';
  return out;
}

// ------------------------------------------------------------------ page

async function materialsPage(params) {
  const main = document.getElementById("view");
  main.classList.add("workstation");
  const pid = params && params.get("id");
  if (pid) mxState.selected = +pid;
  if (params && params.get("q") !== null && params.get("q") !== undefined) mxState.q = params.get("q");
  if (params && params.get("role")) mxState.role = params.get("role");

  main.innerHTML =
    '<div class="wx-head"><div><div class="eyebrow">Formulation research</div>' +
    "<h1>Material explorer</h1></div>" +
    '<div class="wx-sub">Materials recorded in this database, with the source of every value and an ' +
    "explicit list of what is missing. Unassessed material pairs are not evaluated, not compatible.</div>" +
    '<div class="right"><a class="btn" href="#/conditions">Conditions explorer</a></div></div>' +
    '<div class="wx-strip" id="mxstrip"></div>' +
    '<div class="wx-body wx-3" id="mxbody">' +
    '<div class="wx-col" id="mxleft"></div>' +
    '<div class="wx-col" id="mxmain">' + skeleton(5) + "</div>" +
    '<div class="wx-col" id="mxright"></div></div>';

  mxRenderLeft();
  await mxLoadList();
  if (mxState.selected) await mxShowDetail(mxState.selected);
  else document.getElementById("mxmain").innerHTML =
    emptyState("Select a material", "Choose a material on the left to see its recorded identity, " +
      "properties, formulations and compatibility records.");
  mxRenderRight(null);
}

function mxRenderLeft() {
  const left = document.getElementById("mxleft");
  left.innerHTML =
    '<div class="wx-sec"><h4>Find</h4><div class="in">' +
    '<input id="mxq" type="search" placeholder="Name or CAS number" value="' + esc(mxState.q) +
    '" style="width:100%">' +
    '<select id="mxrole" style="width:100%"><option value="">All roles</option>' +
    MX_ROLES.map(function (r) {
      return '<option value="' + r + '"' + (r === mxState.role ? " selected" : "") + ">" +
        esc(mxRole(r)) + "</option>";
    }).join("") + "</select></div></div>" +
    '<div class="wx-sec"><h4>Materials</h4><div class="in"><div id="mxlist" class="wx-list">' +
    skeleton(4) + "</div></div></div>" +
    '<div class="wx-sec"><h4>Compare</h4><div class="in" id="mxcmp"></div></div>';
  let timer = null;
  document.getElementById("mxq").oninput = function (ev) {
    mxState.q = ev.target.value;
    clearTimeout(timer);
    timer = setTimeout(mxLoadList, 250);
  };
  document.getElementById("mxrole").onchange = function (ev) {
    mxState.role = ev.target.value;
    mxLoadList();
  };
  mxRenderCompareBox();
}

async function mxLoadList() {
  const box = document.getElementById("mxlist");
  if (!box) return;
  const qs = new URLSearchParams();
  if (mxState.q) qs.set("q", mxState.q);
  if (mxState.role) qs.set("role", mxState.role);
  qs.set("limit", "100");
  try {
    const res = await api(MX_BASE + "/materials?" + qs.toString());
    mxState.list = res;
    if (!res.items.length) {
      box.innerHTML = emptyState("No materials match",
        mxState.q || mxState.role ? "No recorded material matches these filters." :
          "No materials have been recorded yet. Add them from the formulation studio.");
      return;
    }
    box.innerHTML = res.items.map(function (m) {
      const sel = mxState.compare.indexOf(m.id) >= 0;
      return '<div class="row" style="gap:4px;align-items:stretch">' +
        '<button class="pick' + (m.id === mxState.selected ? " active" : "") + '" data-id="' + m.id +
        '" style="flex:1"><strong>' + esc(m.name) + '</strong><span class="meta">' +
        esc(mxRole(m.role)) + (m.cas_number ? " · CAS " + esc(m.cas_number) : " · no CAS") +
        " · " + m.formulation_count + " formulation(s) · " + m.missing_properties.length +
        " properties missing</span></button>" +
        '<button class="btn' + (sel ? " on" : "") + '" data-cmp="' + m.id +
        '" title="Add to comparison">' + (sel ? "&#10003;" : "+") + "</button></div>";
    }).join("") + '<div class="limit">' + res.total + " material(s) in the database.</div>";
    box.querySelectorAll("button[data-id]").forEach(function (b) {
      b.onclick = function () { mxSelect(+b.dataset.id); };
    });
    box.querySelectorAll("button[data-cmp]").forEach(function (b) {
      b.onclick = function () { mxToggleCompare(+b.dataset.cmp); };
    });
  } catch (err) {
    box.innerHTML = errorState("Could not load materials", err.message);
  }
}

function mxSelect(id) {
  mxState.selected = id;
  document.querySelectorAll("#mxlist button[data-id]").forEach(function (b) {
    b.classList.toggle("active", +b.dataset.id === id);
  });
  mxShowDetail(id);
}

function mxToggleCompare(id) {
  const i = mxState.compare.indexOf(id);
  if (i >= 0) mxState.compare.splice(i, 1);
  else if (mxState.compare.length < 4) mxState.compare.push(id);
  mxRenderCompareBox();
  mxLoadList();
}

function mxRenderCompareBox() {
  const box = document.getElementById("mxcmp");
  if (!box) return;
  const n = mxState.compare.length;
  box.innerHTML = '<p class="sub">' + n + " selected (2 to 4 allowed). Use + beside a material.</p>" +
    '<button class="btn primary" id="mxdocmp"' + (n < 2 ? " disabled" : "") + ">Compare selected</button> " +
    '<button class="btn" id="mxclrcmp"' + (n ? "" : " disabled") + ">Clear</button>";
  document.getElementById("mxdocmp").onclick = mxShowCompare;
  document.getElementById("mxclrcmp").onclick = function () {
    mxState.compare = [];
    mxRenderCompareBox();
    mxLoadList();
  };
}

// ------------------------------------------------------------------ detail

async function mxShowDetail(id) {
  const centre = document.getElementById("mxmain");
  centre.innerHTML = skeleton(6);
  try {
    const d = await api(MX_BASE + "/materials/" + id);
    centre.innerHTML = mxDetailHtml(d);
    mxRenderRight(d);
  } catch (err) {
    centre.innerHTML = errorState("Material not available", err.message);
    mxRenderRight(null);
  }
}

function mxDetailHtml(d) {
  const ident =
    '<table class="kv"><tbody>' +
    "<tr><th>Role</th><td>" + esc(mxRole(d.role)) + "</td></tr>" +
    "<tr><th>CAS number</th><td>" + (d.cas_number ? '<span class="mono">' + esc(d.cas_number) + "</span>" : mxNotRecorded()) + "</td></tr>" +
    "<tr><th>Description</th><td>" + (d.description ? esc(d.description) : mxNotRecorded()) + "</td></tr>" +
    "<tr><th>Function notes</th><td>" + (d.function_notes ? esc(d.function_notes) : mxNotRecorded()) + "</td></tr>" +
    "<tr><th>Entered by</th><td>" + esc(d.provider || "unknown") + (d.source_url ? ' · <a href="' + esc(d.source_url) + '" target="_blank" rel="noopener">source</a>' : "") + "</td></tr>" +
    "</tbody></table>";

  let structure;
  if (d.structure) {
    const desc = d.structure.descriptors || {};
    const keys = Object.keys(desc);
    structure = '<table class="kv"><tbody>' +
      "<tr><th>InChIKey</th><td class=\"mono\">" + esc(d.structure.inchikey) + "</td></tr>" +
      "<tr><th>SMILES</th><td class=\"mono\">" + esc(d.structure.standardized_smiles) + "</td></tr>" +
      keys.map(function (k) {
        const v = desc[k];
        return "<tr><th>" + esc(k) + "</th><td>" +
          (typeof v === "object" && v !== null ? esc(JSON.stringify(v)) : measure(v)) + "</td></tr>";
      }).join("") + "</tbody></table>" +
      '<p class="sub">' + chip("computed") + " " + esc(d.structure.descriptors_origin) + "</p>" +
      '<a class="btn" href="#/studio?structure_id=' + esc(d.structure.id) + '">Open in Molecular Studio</a>';
  } else {
    structure = emptyState("No molecular structure linked",
      "Nothing is recorded here. This may be legitimate for a polymer or mixture; it is not evidence about the material.");
  }
  if (d.compound) {
    structure += '<p class="sub">Compound record: ' + esc(d.compound.name) +
      (d.compound.molecular_formula ? " · " + esc(d.compound.molecular_formula) : "") +
      (d.compound.molecular_weight !== null && d.compound.molecular_weight !== undefined ?
        " · MW " + esc(d.compound.molecular_weight) : "") + "</p>";
  }

  let props;
  if (!d.properties.length) {
    props = emptyState("No properties recorded", "No physical property entry exists for this material.");
  } else {
    props = '<table class="dtable"><thead><tr><th>Property</th><th>Value</th><th>Kind</th><th>Source</th></tr></thead><tbody>' +
      d.properties.map(function (p) {
        return "<tr><td>" + esc(p.name) + "</td><td>" + (p.recorded ? mxValue(p.value, p.unit) : mxNotRecorded()) +
          "</td><td>" + (p.kind ? chip(mxPropKind(p.kind), p.kind.replace(/_/g, " ")) : chip("unverified", "kind not stated")) +
          "</td><td>" + mxSource(p.source, p.source_url) + (p.method ? '<div class="sub">' + esc(p.method) + "</div>" : "") +
          "</td></tr>";
      }).join("") + "</tbody></table>";
  }

  let forms;
  if (!d.formulations.length) {
    forms = emptyState("Not used in any formulation", "No formulation component references this material.");
  } else {
    forms = '<table class="dtable"><thead><tr><th>Formulation</th><th>Role</th><th>Amount</th></tr></thead><tbody>' +
      d.formulations.map(function (f) {
        const amt = f.amount === null || f.amount === undefined ? mxNotRecorded() :
          measure(f.amount, (f.amount_unit || "") + (f.amount_basis ? " " + f.amount_basis : ""));
        return '<tr><td><a href="#/formulations?id=' + esc(f.formulation_id) + '">' +
          esc(f.formulation_name || ("Formulation " + f.formulation_id)) + "</a></td><td>" +
          esc(mxRole(f.role)) + "</td><td>" + amt + "</td></tr>";
      }).join("") + "</tbody></table>";
  }

  return '<div class="wx-sec"><h4>' + esc(d.name) + "</h4><div class=\"in\">" + ident + "</div></div>" +
    '<div class="note"><strong>Reading.</strong> ' + esc(d.interpretation) + "</div>" +
    '<div class="wx-sec"><h4>Structure</h4><div class="in">' + structure + "</div></div>" +
    '<div class="wx-sec"><h4>Properties</h4><div class="in">' + props + "</div></div>" +
    '<div class="wx-sec"><h4>Used in formulations</h4><div class="in">' + forms + "</div></div>" +
    '<div class="limit">' + limitations(d.limitations) + "</div>";
}

// ------------------------------------------------------------------ right panel

function mxRenderRight(d) {
  const right = document.getElementById("mxright");
  if (!right) return;
  if (!d) {
    right.innerHTML = emptyState("No material selected",
      "Compatibility records and missing information appear here.");
    return;
  }
  let comp;
  if (!d.compatibility_assessments.length) {
    comp = '<p>' + chip("missing", "No assessments recorded") + "</p>" +
      '<p class="sub">No compatibility assessment involves this material. That is not evidence of compatibility.</p>';
  } else {
    comp = d.compatibility_assessments.map(function (a) {
      return '<div class="wx-sec"><div class="in"><div><a href="#/materials?id=' + esc(a.other_material.id) +
        '"><strong>' + esc(a.other_material.name) + "</strong></a> " + mxVerdictChip(a.verdict) + "</div>" +
        '<table class="kv"><tbody>' +
        "<tr><th>Conditions</th><td>" + esc(a.conditions || "unspecified") + "</td></tr>" +
        (a.interaction_type ? "<tr><th>Interaction</th><td>" + esc(a.interaction_type) + "</td></tr>" : "") +
        (a.observation ? "<tr><th>Observation</th><td>" + esc(a.observation) + "</td></tr>" : "") +
        (a.method ? "<tr><th>Method</th><td>" + esc(a.method) + "</td></tr>" : "") +
        "<tr><th>Source</th><td>" + mxSource(a.source, a.source_url) + "</td></tr>" +
        "</tbody></table></div></div>";
    }).join("");
  }
  const un = d.unassessed_partners.length ?
    '<div class="wx-sec"><h4>Not evaluated against</h4><div class="in">' +
    d.unassessed_partners.map(function (u) {
      return "<div>" + esc(u.name) + " " + chip("missing", "Not evaluated") + "</div>";
    }).join("") + '<p class="sub">' + esc(d.unassessed_partners[0].note) + "</p></div></div>" : "";
  const miss = '<div class="wx-sec"><h4>Missing information (' + d.missing_information.length +
    ')</h4><div class="in">' + (d.missing_information.length ?
      '<table class="kv"><tbody>' + d.missing_information.map(function (m) {
        return "<tr><th>" + esc(m.field) + "</th><td>" + chip("missing", m.kind) +
          ' <span class="sub">' + esc(m.reason) + "</span></td></tr>";
      }).join("") + "</tbody></table>" : "<p>Nothing on the checklist is missing.</p>") + "</div></div>";
  right.innerHTML = '<div class="wx-sec"><h4>Compatibility</h4><div class="in">' + comp + "</div></div>" +
    un + miss;
}

// ------------------------------------------------------------------ compare

async function mxShowCompare() {
  const centre = document.getElementById("mxmain");
  centre.innerHTML = skeleton(6);
  try {
    const c = await api(MX_BASE + "/materials/compare?ids=" + mxState.compare.join(","));
    const head = "<tr><th>Property</th>" + c.materials.map(function (m) {
      return "<th>" + esc(m.name) + "</th>";
    }).join("") + "</tr>";
    const rows = c.properties.map(function (r) {
      return "<tr><td>" + esc(r.property) + (r.shared ? "" : "") + "</td>" + r.values.map(function (v) {
        if (!v.recorded) return "<td>" + mxNotRecorded() + "</td>";
        return "<td>" + v.entries.map(function (e) {
          return "<div>" + mxValue(e.value, e.unit) + '<div class="sub">' + mxSource(e.source, e.source_url) + "</div></div>";
        }).join("") + "</td>";
      }).join("") + "</tr>";
    }).join("");
    const pairs = c.compatibility_pairs.map(function (p) {
      const v = p.verdict === "not_evaluated" ? mxVerdictChip("not_evaluated") :
        p.assessments.map(function (a) {
          return mxVerdictChip(a.verdict) + ' <span class="sub">' + esc(a.conditions || "") + "</span>";
        }).join("<br>");
      return "<tr><td>" + esc(p.material_a.name) + " + " + esc(p.material_b.name) + "</td><td>" + v +
        '</td><td class="sub">' + esc(p.note) + "</td></tr>";
    }).join("");
    centre.innerHTML =
      '<div class="wx-sec"><h4>Comparison</h4><div class="in"><table class="dtable"><thead>' + head +
      "</thead><tbody>" + rows + "</tbody></table></div></div>" +
      '<div class="wx-sec"><h4>Pairwise compatibility</h4><div class="in"><table class="dtable"><thead>' +
      "<tr><th>Pair</th><th>Verdict</th><th>Note</th></tr></thead><tbody>" + pairs + "</tbody></table></div></div>" +
      '<div class="note"><strong>Reading.</strong> ' + esc(c.interpretation) + "</div>" +
      '<div class="limit">' + limitations(c.limitations) + "</div>";
    mxRenderRight(null);
  } catch (err) {
    centre.innerHTML = errorState("Comparison failed", err.message);
  }
}
