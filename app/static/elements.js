// MOLECULAB periodic table, atomic structure explorer and SMILES-based molecule constructor.
// Vanilla JS, no build step. Relies on globals from app.js / ui.js: view, esc, api, chip, panel,
// emptyState, errorState, skeleton, tile, measure, limitations.
"use strict";

var ELEMENTS_API = "/api/v1/elements";

var EL_CAT_HUE = {
  "alkali metal": 8,
  "alkaline earth metal": 30,
  "transition metal": 210,
  "post-transition metal": 175,
  "metalloid": 120,
  "nonmetal": 85,
  "halogen": 55,
  "noble gas": 280,
  "lanthanide": 330,
  "actinide": 355
};

var EL_FRAGMENTS = [
  { label: "Benzene", smiles: "c1ccccc1" },
  { label: "Carboxylic acid", smiles: "C(=O)O" },
  { label: "Amine", smiles: "N" },
  { label: "Hydroxyl", smiles: "O" },
  { label: "Methyl", smiles: "C" }
];

var EL_STYLE = '<style>' +
  '.pt-wrap{overflow-x:auto;padding-bottom:6px}' +
  '.pt-grid{display:grid;grid-template-columns:repeat(18,minmax(40px,1fr));gap:3px;min-width:760px}' +
  '.pt-cell{display:flex;flex-direction:column;align-items:center;justify-content:center;' +
  'padding:3px 1px;min-height:46px;border-radius:5px;cursor:pointer;font:inherit;color:inherit;' +
  'border:1px solid transparent;line-height:1.1}' +
  '.pt-cell .z{font-size:9px;opacity:.75}' +
  '.pt-cell .s{font-size:14px;font-weight:700}' +
  '.pt-cell:hover{filter:brightness(1.15);border-color:currentColor}' +
  '.pt-cell.sel{outline:2px solid currentColor;outline-offset:1px}' +
  '.pt-gap{font-size:10px;opacity:.6;display:flex;align-items:center;justify-content:center}' +
  '.pt-legend{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 0}' +
  '.pt-legend span{font-size:11px;padding:2px 8px;border-radius:10px;border:1px solid transparent}' +
  '.el-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-top:16px}' +
  '.el-fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}' +
  '.el-fields input{width:100%;box-sizing:border-box}' +
  '.el-smiles{width:100%;box-sizing:border-box;min-height:84px;font-family:monospace}' +
  '.el-unk{font-style:italic}' +
  '</style>';

function elCatStyle(cat) {
  var h = EL_CAT_HUE[cat];
  if (h === undefined) h = 0;
  return "background:hsla(" + h + ",60%,50%,0.22);border-color:hsla(" + h + ",60%,50%,0.7)";
}

/** Render a possibly-missing field: unknown vs not applicable are shown differently. */
function elField(value, status, unit) {
  if (value !== null && value !== undefined && value !== "" &&
      !(Object.prototype.toString.call(value) === "[object Array]" && value.length === 0)) {
    if (Object.prototype.toString.call(value) === "[object Array]") {
      return esc(value.map(function (v) { return v > 0 ? "+" + v : String(v); }).join(", "));
    }
    return unit ? measure(value, unit) : esc(value);
  }
  if (status === "not_applicable") {
    return '<span class="muted" title="Undefined for this element">⊘ Not applicable</span>';
  }
  return '<span class="el-unk">' + chip("missing", "Unknown / not recorded") + "</span>";
}

function elGridPos(e) {
  if (e.block === "f") {
    var idx = e.atomic_number >= 89 ? e.atomic_number - 89 : e.atomic_number - 57;
    return { col: idx + 3, row: e.atomic_number >= 89 ? 10 : 9 };
  }
  return { col: e.group, row: e.period };
}

function elTableHtml(list, selected) {
  var html = '<div class="pt-wrap"><div class="pt-grid" role="grid" aria-label="Periodic table">';
  list.forEach(function (e) {
    var p = elGridPos(e);
    html += '<button type="button" class="pt-cell' + (e.symbol === selected ? " sel" : "") +
      '" data-sym="' + esc(e.symbol) + '" style="grid-column:' + p.col + ";grid-row:" + p.row + ";" +
      elCatStyle(e.category) + '" title="' + esc(e.name + " (" + e.category + ")") +
      '" aria-label="' + esc(e.name + ", atomic number " + e.atomic_number) + '">' +
      '<span class="z">' + e.atomic_number + '</span><span class="s">' + esc(e.symbol) + "</span></button>";
  });
  html += '<div class="pt-gap" style="grid-column:3;grid-row:6">57–71</div>' +
    '<div class="pt-gap" style="grid-column:3;grid-row:7">89–103</div>';
  html += "</div></div>";
  return html;
}

function elLegendHtml() {
  return '<div class="pt-legend">' + Object.keys(EL_CAT_HUE).map(function (c) {
    return '<span style="' + elCatStyle(c) + '">' + esc(c) + "</span>";
  }).join("") + "</div>";
}

function elDetailHtml(e, isoInfo) {
  var dc = e.data_completeness || {};
  var rows = [
    ["Name", esc(e.name)],
    ["Symbol", esc(e.symbol)],
    ["Atomic number", esc(e.atomic_number)],
    ["Atomic weight", elField(e.atomic_weight, dc.atomic_weight, "u") +
      (e.atomic_weight_is_mass_number
        ? ' <span class="muted">(mass number of most stable known isotope; no standard atomic weight)</span>' : "") +
      (dc.atomic_weight === "estimated" ? ' <span class="muted">(estimated)</span>' : "")],
    ["Category", esc(e.category)],
    ["Group", elField(e.group, dc.group) +
      (e.group === null ? ' <span class="muted">(f-block row)</span>' : "")],
    ["Period", esc(e.period)],
    ["Block", esc(e.block)],
    ["Electron configuration", '<span class="mono">' + esc(e.electron_configuration) + "</span>" +
      (dc.electron_configuration === "predicted" ? ' <span class="muted">(predicted, not measured)</span>' : "")],
    ["Electronegativity (Pauling)", elField(e.electronegativity_pauling, dc.electronegativity_pauling)],
    ["Common oxidation states", elField(e.common_oxidation_states, dc.common_oxidation_states)],
    ["Standard state (298.15 K, 100 kPa)", e.standard_state === "unknown"
      ? elField(null, "unknown") : esc(e.standard_state)],
    ["Radioactive only", e.radioactive_only ? "yes (no stable isotope)" : "no"]
  ];
  var html = '<table class="dtable"><tbody>' + rows.map(function (r) {
    return "<tr><th>" + esc(r[0]) + "</th><td>" + r[1] + "</td></tr>";
  }).join("") + "</tbody></table>";
  if (e.notes) html += '<p class="note">' + esc(e.notes) + "</p>";

  html += "<h4>Well-known isotopes</h4>";
  if (isoInfo && isoInfo.isotopes && isoInfo.isotopes.length) {
    html += '<table class="dtable"><thead><tr><th>Isotope</th><th>Neutrons</th><th>Natural abundance</th>' +
      "<th>Stability</th></tr></thead><tbody>" + isoInfo.isotopes.map(function (i) {
        return "<tr><td>" + esc(i.label) + "</td><td>" + esc(i.neutrons) + "</td><td>" +
          (i.natural_abundance_percent === null || i.natural_abundance_percent === undefined
            ? '<span class="muted">— (not natural / not recorded)</span>'
            : esc(i.natural_abundance_percent) + " %") + "</td><td>" +
          (i.stable ? "stable" : "radioactive, half-life " + esc(i.half_life)) + "</td></tr>";
      }).join("") + "</tbody></table>";
  } else {
    html += '<p class="muted">' + esc((isoInfo && isoInfo.note) || "No isotope data available.") + "</p>";
  }
  if (isoInfo && isoInfo.isotopes && isoInfo.isotopes.length) {
    html += '<p class="muted">' + esc(isoInfo.note) + "</p>";
  }
  html += '<div class="actions"><button type="button" class="btn" id="el-use-atom">Load into atom explorer</button> ' +
    '<button type="button" class="btn" id="el-add-elem">Add element to SMILES</button></div>';
  return html;
}

function elAtomResultHtml(r) {
  var html = "";
  if (!r.valid) {
    return errorState("Not a valid atom", (r.errors || []).join("; "));
  }
  html += '<div class="tiles">' +
    tile("Element", r.element.name + " (" + r.element.symbol + ")", "Z = " + r.element.atomic_number + " from protons") +
    tile("Isotope", r.isotope, "mass number " + r.mass_number + " = protons + neutrons") +
    tile("Charge", (r.charge > 0 ? "+" : "") + r.charge, esc(r.charge_description)) +
    "</div>";
  if (r.isotope_in_curated_list === true) {
    html += '<p class="muted">This isotope is in the curated well-known list.</p>';
  }
  if (!r.plausible) {
    html += '<p class="note"><strong>Flagged as implausible.</strong> The bookkeeping is arithmetically ' +
      "valid but this combination is unlikely to exist as a real species.</p>";
  }
  if (r.warnings && r.warnings.length) {
    html += '<div class="note"><strong>Warnings</strong><ul>' + r.warnings.map(function (w) {
      return "<li>" + esc(w) + "</li>";
    }).join("") + "</ul></div>";
  }
  html += '<ul class="muted">' + (r.explanation || []).map(function (x) {
    return "<li>" + esc(x) + "</li>";
  }).join("") + "</ul>";
  html += '<p class="limit">' + esc(r.model_note) + "</p>";
  return html;
}

function elMoleculeResultHtml(r) {
  if (!r.valid) {
    return '<div class="state error"><span class="g" aria-hidden="true">⚠</span>' +
      "<h4>Invalid structure</h4><p>" + esc(r.error || "unknown parser error") + "</p>" +
      '<p class="muted">Reported as-is. MOLECULAB does not auto-repair invalid structures.</p></div>';
  }
  var d = r.descriptors || {};
  var html = '<p>' + chip("computed", "Valid structure") + "</p>" +
    '<table class="dtable"><tbody>' +
    "<tr><th>Canonical SMILES (as submitted)</th><td class=\"mono smiles\">" + esc(r.original_smiles) + "</td></tr>" +
    "<tr><th>Standardized SMILES</th><td class=\"mono smiles\">" + esc(r.standardized_smiles) + "</td></tr>" +
    "<tr><th>Formula</th><td>" + esc(d.molecular_formula) + "</td></tr>" +
    "<tr><th>Molecular weight</th><td>" + measure(d.molecular_weight, "g/mol", 2) + "</td></tr>" +
    "</tbody></table>";
  if (r.standardization_changed) {
    html += '<p class="note">Standardization changed the structure; the submitted canonical form and ' +
      "the standardized form are both shown above.</p>";
  }
  return html;
}

async function elementsPage(params) {
  view.innerHTML = '<div class="page-head"><div class="eyebrow">Reference</div><h1>Periodic table</h1></div>' +
    skeleton(6);
  var data;
  try {
    data = await api(ELEMENTS_API + "/");
  } catch (err) {
    view.innerHTML = errorState("Could not load the periodic table", err.message);
    return;
  }
  var list = data.elements || [];
  var state = { selected: null, element: null };
  var wanted = params && (params.symbol || params.s || params.el);
  if (typeof wanted === "string") {
    list.forEach(function (e) { if (e.symbol.toLowerCase() === wanted.toLowerCase()) state.selected = e.symbol; });
  }

  view.innerHTML = EL_STYLE +
    '<div class="page-head"><div class="eyebrow">Reference</div><h1>Periodic table and atomic structure</h1>' +
    '<p class="lede">All 118 elements with IUPAC atomic weights, an atom bookkeeping explorer and a ' +
    "SMILES-based molecule constructor. Unknown values are shown as unknown, never guessed.</p></div>" +
    panel("Periodic table", '<div id="el-table"></div>' + elLegendHtml() +
      '<p class="muted">Lanthanides and actinides are shown in the two rows below the main table. ' +
      "Click any element for its details.</p>") +
    '<div class="el-cols">' +
    '<div id="el-detail-panel">' + panel("Element details",
      '<div id="el-detail">' + emptyState("No element selected", "Choose an element in the table.") + "</div>") + "</div>" +
    "<div>" + panel("Atomic structure explorer",
      '<p class="muted">Enter particle counts. Protons set the element, neutrons select the isotope, ' +
      "electrons set the charge.</p>" +
      '<div class="el-fields">' +
      '<label>Protons<input type="number" id="at-p" class="mono" min="1" max="118" value="6"></label>' +
      '<label>Neutrons<input type="number" id="at-n" class="mono" min="0" max="300" value="6"></label>' +
      '<label>Electrons<input type="number" id="at-e" class="mono" min="0" max="300" value="6"></label>' +
      "</div>" +
      '<div class="actions"><button type="button" class="btn" id="at-go">Validate atom</button></div>' +
      '<div id="at-out" aria-live="polite"></div>') + "</div></div>" +
    panel("Molecule construction (SMILES-based)",
      '<p class="note">This is a SMILES-based constructor, <strong>not</strong> a graphical bond-drawing ' +
      "editor. Fragments are appended as text, so the result is only as meaningful as the string you " +
      "build. Invalid structures are reported with the parser's error and are never auto-repaired.</p>" +
      '<textarea id="mol-smiles" class="el-smiles mono" spellcheck="false" ' +
      'placeholder="Type or build a SMILES string, e.g. CC(=O)O"></textarea>' +
      '<div class="actions" id="mol-frags"></div>' +
      '<div class="actions"><button type="button" class="btn" id="mol-add-el" disabled>Add element</button> ' +
      '<button type="button" class="btn" id="mol-clear">Clear</button> ' +
      '<button type="button" class="btn" id="mol-go">Validate</button></div>' +
      '<div id="mol-out" aria-live="polite"></div>') +
    limitations([
      "Atomic weights are IUPAC conventional values; elements without a standard atomic weight show the mass number of the most stable known isotope.",
      "Synthetic elements (Z >= 104) have predicted configurations and categories; many properties are unmeasured.",
      "The atom explorer is an educational bookkeeping model, not a quantum-mechanical calculation.",
      "Appending an element symbol to SMILES does not guarantee valence-correct chemistry; validation will tell you."
    ]);

  var tableEl = document.getElementById("el-table");
  var detailEl = document.getElementById("el-detail");
  var smilesEl = document.getElementById("mol-smiles");
  var addElBtn = document.getElementById("mol-add-el");

  function drawTable() {
    tableEl.innerHTML = elTableHtml(list, state.selected);
    var cells = tableEl.querySelectorAll(".pt-cell");
    Array.prototype.forEach.call(cells, function (c) {
      c.addEventListener("click", function () { selectElement(c.getAttribute("data-sym")); });
    });
  }

  async function selectElement(sym) {
    state.selected = sym;
    drawTable();
    detailEl.innerHTML = skeleton(5);
    try {
      var res = await api(ELEMENTS_API + "/" + encodeURIComponent(sym));
      state.element = res.element;
      detailEl.innerHTML = elDetailHtml(res.element, res.isotope_info);
      addElBtn.disabled = false;
      addElBtn.textContent = "Add element (" + res.element.symbol + ")";
      document.getElementById("el-use-atom").addEventListener("click", function () {
        var e = state.element;
        var n = Math.max(0, Math.round(e.atomic_weight) - e.atomic_number);
        document.getElementById("at-p").value = e.atomic_number;
        document.getElementById("at-n").value = n;
        document.getElementById("at-e").value = e.atomic_number;
        runAtom();
      });
      document.getElementById("el-add-elem").addEventListener("click", addElement);
    } catch (err) {
      detailEl.innerHTML = errorState("Could not load element", err.message);
    }
  }

  function addElement() {
    if (!state.element) return;
    smilesEl.value += state.element.symbol;
    smilesEl.focus();
  }

  async function runAtom() {
    var out = document.getElementById("at-out");
    var body = {
      protons: parseInt(document.getElementById("at-p").value, 10),
      neutrons: parseInt(document.getElementById("at-n").value, 10),
      electrons: parseInt(document.getElementById("at-e").value, 10)
    };
    if (isNaN(body.protons) || isNaN(body.neutrons) || isNaN(body.electrons)) {
      out.innerHTML = errorState("Enter whole numbers", "Protons, neutrons and electrons must all be integers.");
      return;
    }
    try {
      var r = await api(ELEMENTS_API + "/atom/validate", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
      });
      out.innerHTML = elAtomResultHtml(r);
    } catch (err) {
      out.innerHTML = errorState("Atom rejected", err.message);
    }
  }

  async function runMolecule() {
    var out = document.getElementById("mol-out");
    var s = smilesEl.value.trim();
    if (!s) {
      out.innerHTML = emptyState("Nothing to validate", "Enter or build a SMILES string first.");
      return;
    }
    out.innerHTML = skeleton(3);
    try {
      var r = await api(ELEMENTS_API + "/molecule/validate", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ smiles: s })
      });
      out.innerHTML = elMoleculeResultHtml(r);
    } catch (err) {
      out.innerHTML = errorState("Validation request failed", err.message);
    }
  }

  var fragBox = document.getElementById("mol-frags");
  EL_FRAGMENTS.forEach(function (f) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "btn";
    b.textContent = "+ " + f.label + " (" + f.smiles + ")";
    b.addEventListener("click", function () { smilesEl.value += f.smiles; smilesEl.focus(); });
    fragBox.appendChild(b);
  });

  document.getElementById("at-go").addEventListener("click", runAtom);
  document.getElementById("mol-go").addEventListener("click", runMolecule);
  document.getElementById("mol-clear").addEventListener("click", function () {
    smilesEl.value = "";
    document.getElementById("mol-out").innerHTML = "";
  });
  addElBtn.addEventListener("click", addElement);

  drawTable();
  runAtom();
  if (state.selected) selectElement(state.selected);
}
