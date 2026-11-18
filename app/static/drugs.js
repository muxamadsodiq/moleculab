// MOLECULAB drug intelligence page.
//
// Three things are shown in SEPARATE sections and never merged: the active ingredient (a substance), the
// pharmaceutical products (finished forms) and the regulatory status by jurisdiction. Presence in ChEMBL,
// PubChem or DailyMed is NOT regulatory approval, and this page never presents it as such.
"use strict";

const DRUG_STATUS_TEXT = {
  registered: "Registered (authoritative source)",
  not_found_in_indexed_sources: "Not found in indexed sources",
  not_indexed: "Not indexed (no authoritative source connected)",
};

function drugVal(value) {
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) {
    return chip("missing");
  }
  if (Array.isArray(value)) return esc(value.join(", "));
  return esc(value);
}

function drugRows(pairs) {
  return '<table class="dtable"><tbody>' + pairs.map(function (p) {
    return "<tr><th>" + esc(p[0]) + "</th><td>" + p[1] + "</td></tr>";
  }).join("") + "</tbody></table>";
}

function drugProviders(list, unavailable) {
  const down = (unavailable || []).length
    ? '<p class="note">Unavailable providers: ' + esc(unavailable.join(", ")) + ". Fields they would supply are shown as missing.</p>"
    : "";
  const rows = (list || []).map(function (p) {
    return "<tr><td>" + esc(p.provider) + "</td><td>" + esc(p.role || "") + "</td><td>" +
      (p.available ? "available" : chip("missing", "unavailable: " + (p.error || "unknown"))) +
      '</td><td class="mono">' + esc(p.retrieved_at || "") + "</td><td>" + esc(p.source_release || "") + "</td></tr>";
  }).join("");
  return down + '<table class="dtable"><thead><tr><th>Provider</th><th>Role</th><th>Status</th>' +
    "<th>Retrieved</th><th>Release</th></tr></thead><tbody>" + rows + "</tbody></table>";
}

function drugIngredientSection(ing) {
  if (!ing) {
    return emptyState("No active ingredient resolved",
      "No provider returned a substance for this name. This is not evidence that the substance does not exist.");
  }
  const phase = ing.chembl_max_phase === null || ing.chembl_max_phase === undefined
    ? chip("missing")
    : esc(ing.chembl_max_phase) + " " + chip("unverified", "development phase, not approval");
  return drugRows([
    ["Generic / INN name", drugVal(ing.inn_or_generic_name)],
    ["ChEMBL id", drugVal(ing.chembl_id)],
    ["PubChem CID", drugVal(ing.pubchem_cid)],
    ["InChIKey", ing.inchikey ? '<span class="mono">' + esc(ing.inchikey) + "</span>" : chip("missing")],
    ["SMILES", ing.smiles ? '<span class="smiles">' + esc(ing.smiles) + "</span>" : chip("missing")],
    ["Molecular formula", drugVal(ing.molecular_formula)],
    ["Molecular weight", ing.molecular_weight ? measure(ing.molecular_weight, "g/mol", 2) : chip("missing")],
    ["ATC codes", drugVal(ing.atc_codes)],
    ["ChEMBL max phase", phase],
    ["Identity match", drugVal(ing.match_confidence)],
  ]) + '<p class="note">' + esc(ing.chembl_max_phase_note) + "</p>";
}

function drugProductsSection(products) {
  if (!products || !products.length) {
    return emptyState("No product labels found",
      "DailyMed returned no US labels, or was unavailable. This says nothing about products elsewhere.");
  }
  const rows = products.map(function (p) {
    return "<tr><td>" + drugVal(p.brand_name) + "</td><td>" + drugVal(p.manufacturer_or_labeler) + "</td><td>" +
      drugVal(p.dosage_form) + "</td><td>" + drugVal(p.strength) + "</td><td>" + drugVal(p.route) +
      '</td><td class="mono">' + esc(p.source_record_id || "") + "</td></tr>";
  }).join("");
  return '<p class="note">Finished products are US DailyMed labels. They are not the substance and are not a ' +
    "statement about any other country.</p>" +
    '<table class="dtable"><thead><tr><th>Brand / product name</th><th>Manufacturer / labeler</th>' +
    "<th>Dosage form</th><th>Strength</th><th>Route</th><th>DailyMed setid</th></tr></thead><tbody>" +
    rows + "</tbody></table>";
}

function drugRegulatorySection(p) {
  const rec = p.regulatory_records || {};
  const rows = Object.keys(rec).map(function (j) {
    const r = rec[j];
    const kind = r.status === "registered" ? "experimental" : "missing";
    return "<tr><td>" + esc(j) + "</td><td>" + esc(r.authority) + "</td><td>" +
      chip(kind, DRUG_STATUS_TEXT[r.status] || r.status) + "</td><td>" + esc(r.note || "") +
      '</td><td class="mono">' + esc(r.retrieved_at) + "</td></tr>";
  }).join("");
  return '<table class="dtable"><thead><tr><th>Jurisdiction</th><th>Authority</th><th>Status</th>' +
    "<th>Note</th><th>Retrieved</th></tr></thead><tbody>" + rows + "</tbody></table>" +
    '<p class="note">Database presence is not regulatory approval.</p>';
}

function drugUzSection(uz) {
  if (!uz || uz.available) {
    return '<p class="note">Programmatic access reported available. See the registry endpoint.</p>';
  }
  const checked = (uz.checked || []).map(function (c) {
    return "<li><span class=\"mono\">" + esc(c.url) + "</span>: " + esc(c.outcome) + "</li>";
  }).join("");
  const required = (uz.required || []).map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("");
  return unavailableState("Uzbekistan medicines register: no programmatic access", uz.reason) +
    "<h4>What was checked</h4><ul>" + checked + "</ul>" +
    "<h4>What would be required</h4><ul>" + required + "</ul>" + limitations(uz.limitations);
}

async function drugProfileView(name) {
  const body = document.getElementById("drugbody");
  body.innerHTML = skeleton(6);
  let p;
  try {
    p = await api("/api/v1/drugs/profile/" + encodeURIComponent(name));
  } catch (e) {
    body.innerHTML = errorState("Profile could not be built", e.message);
    return;
  }
  body.innerHTML =
    '<div class="actions"><a class="btn" href="#/drugs?compare=' + encodeURIComponent(name) +
    '">Compare with other drugs</a></div>' +
    '<div class="stack">' +
    panel("Active ingredient (substance)", drugIngredientSection(p.active_ingredient)) +
    panel("Pharmaceutical products (finished forms)", drugProductsSection(p.pharmaceutical_products)) +
    panel("Regulatory status by jurisdiction", drugRegulatorySection(p)) +
    panel("Uzbekistan registry access", drugUzSection(p.uzbekistan_registry)) +
    panel("Provenance", drugProviders(p.provenance, p.unavailable_providers)) +
    panel("Limitations", limitations(p.limitations)) +
    "</div>";
}

async function drugCompareView(names) {
  const body = document.getElementById("drugbody");
  if (names.length < 2) {
    body.innerHTML = emptyState("Add at least two drugs", "Enter two to four names separated by commas.");
    return;
  }
  body.innerHTML = skeleton(8);
  let c;
  try {
    c = await api("/api/v1/drugs/compare?names=" + encodeURIComponent(names.join(",")));
  } catch (e) {
    body.innerHTML = errorState("Comparison failed", e.message);
    return;
  }
  const head = "<th>Field</th>" + c.names.map(function (n) { return "<th>" + esc(n) + "</th>"; }).join("");
  const rows = c.rows.map(function (r) {
    return "<tr><th>" + esc(r.field) + "</th>" + r.values.map(function (v) {
      return "<td>" + (v.status === "missing" ? chip("missing") : drugVal(v.value)) + "</td>";
    }).join("") + "</tr>";
  }).join("");
  body.innerHTML = panel("Side-by-side comparison",
    '<table class="dtable"><thead><tr>' + head + "</tr></thead><tbody>" + rows + "</tbody></table>" +
    drugProviders([], c.unavailable_providers)) + panel("Limitations", limitations(c.limitations));
}

async function drugSearchView(q) {
  const body = document.getElementById("drugbody");
  body.innerHTML = skeleton(5);
  let r;
  try {
    r = await api("/api/v1/drugs/search?q=" + encodeURIComponent(q));
  } catch (e) {
    body.innerHTML = errorState("Search failed", e.message);
    return;
  }
  const ing = (r.ingredient_candidates || []).map(function (c) {
    const label = c.name || c.chembl_id || ("PubChem CID " + c.pubchem_cid);
    return '<tr><td><a href="#/drugs?name=' + encodeURIComponent(c.name || q) + '">' + esc(label) + "</a></td><td>" +
      drugVal(c.chembl_id) + '</td><td class="mono">' + esc(c.inchikey || "") + "</td></tr>";
  }).join("");
  const prod = (r.product_candidates || []).slice(0, 10).map(function (s) {
    return "<tr><td>" + esc(s.title || "") + "</td><td>" + drugVal(s.labeler) + '</td><td class="mono">' +
      esc(s.setid) + "</td></tr>";
  }).join("");
  body.innerHTML =
    panel("Candidate active ingredients",
      ing ? '<table class="dtable"><thead><tr><th>Name</th><th>ChEMBL</th><th>InChIKey</th></tr></thead><tbody>' +
        ing + "</tbody></table>" : emptyState("No ingredient candidates", "No provider returned a candidate.")) +
    panel("Candidate products (US labels)",
      prod ? '<table class="dtable"><thead><tr><th>Label</th><th>Labeler</th><th>setid</th></tr></thead><tbody>' +
        prod + "</tbody></table>" : emptyState("No product candidates", "DailyMed returned none or was unavailable.")) +
    panel("Providers", drugProviders(r.providers, r.unavailable_providers));
}

async function drugsPage(params) {
  const name = params && params.get("name");
  const compare = params && params.get("compare");
  const q = params && params.get("q");
  view.innerHTML =
    '<div class="page-head"><div><div class="eyebrow">Drug intelligence</div><h1>Drugs</h1>' +
    '<p class="lede">Substances, finished products and regulatory status are kept apart. A record in ChEMBL, ' +
    "PubChem or DailyMed is not a regulatory approval, and Uzbekistan registration is not available through " +
    "any public programmatic source.</p></div></div>" +
    '<div class="panel"><div class="panel-body"><form id="drugform" class="actions">' +
    '<input id="drugq" type="search" placeholder="Drug name, or several separated by commas to compare" ' +
    'value="' + esc(name || q || compare || "") + '" style="min-width:320px">' +
    '<button class="btn primary" type="submit">Search</button> ' +
    '<button class="btn" type="button" id="drugcmp">Compare</button></form></div></div>' +
    '<div id="drugbody"></div>';

  document.getElementById("drugform").onsubmit = function (ev) {
    ev.preventDefault();
    const v = document.getElementById("drugq").value.trim();
    if (v) location.hash = "#/drugs?q=" + encodeURIComponent(v);
  };
  document.getElementById("drugcmp").onclick = function () {
    const names = document.getElementById("drugq").value.split(",").map(function (s) { return s.trim(); })
      .filter(Boolean);
    location.hash = "#/drugs?compare=" + encodeURIComponent(names.join(","));
  };

  const body = document.getElementById("drugbody");
  if (name) return drugProfileView(name);
  if (compare) return drugCompareView(compare.split(",").map(function (s) { return s.trim(); }).filter(Boolean));
  if (q) return drugSearchView(q);
  body.innerHTML = emptyState("Search for a drug",
    "Enter a generic name such as aspirin to see the substance, US product labels and regulatory status separately.");
}
