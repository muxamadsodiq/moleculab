// MOLECULAB — Molecular Studio view.
//
// Everything drawn here is COMPUTED, never measured: the 2D depiction is generated for legibility and
// the 3D geometry is an RDKit conformer, not an experimentally determined structure. The origin badges
// are not decoration — they are the scientific claim the screen is making. A molecular viewer is not
// docking: no protein, binding site, pose or affinity is computed anywhere in this view.
//
// Loaded before app.js; uses its helpers (esc, api, view) at call time.
"use strict";

const ORIGIN_LABEL = {
  computed_conformer: "Computed conformer",
  computed_depiction: "Computed depiction",
  imported: "Imported structure",
  experimental: "Experimental structure",
};

function originBadge(origin) {
  const cls = origin === "experimental" ? "v-excerpt_verified" : "t-computational_prediction";
  return '<span class="badge ' + cls + '">' + esc(ORIGIN_LABEL[origin] || origin || "unknown origin") + "</span>";
}

let studioViewer = null;

function render3D(molblock) {
  const host = document.getElementById("mol3d");
  if (!host) return;
  if (typeof window.$3Dmol === "undefined") {
    host.innerHTML = '<p class="err">3D viewer library not loaded (/static/vendor/3Dmol-min.js).</p>';
    return;
  }
  host.innerHTML = "";
  try {
    studioViewer = window.$3Dmol.createViewer(host, { backgroundColor: "#0e1014" });
    studioViewer.addModel(molblock, "mol");
    studioViewer.setStyle({}, { stick: { radius: 0.14 }, sphere: { scale: 0.22 } });
    studioViewer.zoomTo();
    studioViewer.render();
  } catch (err) {
    host.innerHTML = '<p class="err">3D render failed: ' + esc(err.message) + "</p>";
  }
}

function studioStyle(style) {
  if (!studioViewer) return;
  const styles = {
    ballstick: { stick: { radius: 0.14 }, sphere: { scale: 0.22 } },
    stick: { stick: { radius: 0.18 } },
    sphere: { sphere: { scale: 0.95 } },
    line: { line: { linewidth: 2 } },
  };
  studioViewer.setStyle({}, styles[style] || styles.ballstick);
  studioViewer.render();
}

function descriptorTable(d) {
  const rows = [
    ["Molecular formula", d.molecular_formula], ["Molecular weight", d.molecular_weight],
    ["Exact mass", d.exact_mass], ["Heavy atoms", d.heavy_atom_count],
    ["TPSA (A^2)", d.tpsa], ["cLogP (computed)", d.clogp],
    ["H-bond donors", d.h_bond_donors], ["H-bond acceptors", d.h_bond_acceptors],
    ["Rotatable bonds", d.rotatable_bonds], ["Rings", d.ring_count],
    ["Aromatic rings", d.aromatic_ring_count], ["Fraction Csp3", d.fraction_csp3],
    ["Formal charge", d.formal_charge], ["QED", d.qed],
  ];
  return '<table class="kv">' + rows.map(function (r) {
    return "<tr><th>" + esc(r[0]) + "</th><td>" + esc(r[1] === null || r[1] === undefined ? "-" : r[1]) + "</td></tr>";
  }).join("") + "</table>";
}

function transformationLog(payload) {
  if (!payload.standardization_changed) return "";
  const rows = (payload.transformations || []).map(function (t) {
    return "<tr><th>" + esc(t.step) + (t.changed ? " (changed)" : "") + "</th><td><code>" +
      esc(t.smiles_before) + "</code> &rarr; <code>" + esc(t.smiles_after) +
      '</code><br><span class="sub">' + esc(t.description) + "</span></td></tr>";
  }).join("");
  return "<details><summary>Transformation log</summary>" +
    '<table class="kv">' + rows + "</table></details>";
}

function studioSetView(mode) {
  const two = document.getElementById("s2d"), three = document.getElementById("s3d");
  if (!two || !three) return;
  two.style.display = (mode === "3d") ? "none" : "";
  three.style.display = (mode === "2d") ? "none" : "";
  if (mode !== "2d" && studioViewer) { studioViewer.resize(); studioViewer.render(); }
}

// Distributes one structure across the three workstation regions: stage (2D + 3D),
// inspector (identifiers, properties, provenance), under-stage (standardisation audit).
function studioPanel(payload) {
  const stage = document.getElementById("studiostage");
  const side = document.getElementById("studiopanel");
  const under = document.getElementById("studiounder");
  if (!stage || !side) return;

  if (!payload.ok) {
    stage.innerHTML = errorState("Structure unavailable", payload.error || "unknown error");
    side.innerHTML = '<div class="wx-sec"><h4>Validation</h4><div class="in">' +
      '<p class="limit">MOLECULAB does not display a structure it could not validate.</p>' +
      "</div></div>";
    if (under) under.innerHTML = "";
    return;
  }

  const c3 = payload.conformer_3d || {};
  const stereo = payload.stereo || {};
  const inv = payload.atom_bond_inventory || {};
  const ident = payload.identifiers || {};
  const prov = payload.provenance || {};
  const d = payload.descriptors || {};

  // ---- stage: 2D and 3D side by side ----
  stage.innerHTML =
    '<div style="display:grid;grid-template-columns:1fr 1fr;height:100%;min-height:0">' +
    '<div id="s2d" style="min-width:0;display:flex;align-items:center;justify-content:center;' +
    'padding:10px;border-right:1px solid var(--line);overflow:auto">' +
    (payload.depiction_2d && payload.depiction_2d.ok ? payload.depiction_2d.svg
      : '<p class="err">' + esc((payload.depiction_2d || {}).error || "2D unavailable") + "</p>") +
    "</div>" +
    '<div id="s3d" style="min-width:0;position:relative">' +
    (c3.ok ? '<div class="mol3d" id="mol3d" style="width:100%;height:100%"></div>'
      : '<div class="state"><span class="g">&#9888;</span><h4>3D unavailable</h4><p>' +
        esc(c3.error || "not generated") + "</p></div>") +
    "</div></div>" +
    '<div class="viewport-overlay">' + originBadge("computed_depiction") +
    (c3.ok ? originBadge("computed_conformer") : "") + chip("missing", "Not docking") + "</div>";

  if (c3.ok) {
    render3D(c3.molblock);
    const host = document.getElementById("s3d");
    host.insertAdjacentHTML("beforeend",
      '<div class="studio-controls" style="position:absolute;left:8px;bottom:8px;z-index:3">' +
      '<button data-style="ballstick" class="active">Ball &amp; stick</button>' +
      '<button data-style="stick">Stick</button>' +
      '<button data-style="sphere">Space-fill</button></div>');
    host.querySelectorAll(".studio-controls button").forEach(function (b) {
      b.onclick = function () {
        host.querySelectorAll(".studio-controls button").forEach(function (x) {
          x.classList.remove("active"); });
        b.classList.add("active");
        studioStyle(b.dataset.style);
      };
    });
  }

  // ---- inspector ----
  const row = function (k, v) {
    return "<tr><th>" + esc(k) + "</th><td>" + (v === null || v === undefined || v === ""
      ? '<span class="muted">-</span>' : v) + "</td></tr>"; };
  const identRows = Object.keys(ident).filter(function (k) {
    return ident[k] !== null && ident[k] !== undefined;
  }).map(function (k) { return row(k.replace(/_/g, " "), esc(ident[k])); }).join("");

  side.innerHTML =
    '<div class="wx-sec"><h4>' + esc(payload.name || payload.inchikey) + "</h4><div class=\"in\">" +
    "<p>" + originBadge(payload.source_structure_origin) + "</p>" +
    '<table class="kv">' + identRows +
    (ident.inchikey ? "" : row("InChIKey",
      '<span class="mono">' + esc(payload.inchikey || "-") + "</span>")) +
    row("Scaffold", '<code>' + esc(payload.scaffold_smiles || "-") + "</code>") +
    "</table></div></div>" +

    '<div class="wx-sec"><h4>Computed descriptors</h4><div class="in"><table class="kv">' +
    row("Formula", esc(d.molecular_formula)) +
    row("Molecular weight", measure(d.molecular_weight, "Da")) +
    row("Exact mass", measure(d.exact_mass, "Da")) +
    row("Heavy atoms", measure(d.heavy_atom_count)) +
    row("TPSA", measure(d.tpsa, "A^2")) +
    row("cLogP", measure(d.clogp)) +
    row("H-bond donors", measure(d.h_bond_donors)) +
    row("H-bond acceptors", measure(d.h_bond_acceptors)) +
    row("Rotatable bonds", measure(d.rotatable_bonds)) +
    row("Rings / aromatic", measure(d.ring_count) + " / " + measure(d.aromatic_ring_count)) +
    row("Fraction Csp3", measure(d.fraction_csp3)) +
    row("QED", measure(d.qed)) +
    '</table><p class="muted" style="font-size:11.5px">Calculated locally with RDKit. These are ' +
    "estimates, not measured physicochemical values.</p></div></div>" +

    '<div class="wx-sec"><h4>Stereochemistry</h4><div class="in"><table class="kv">' +
    row("Stereocentres", measure(stereo.stereo_centers)) +
    row("Unspecified", measure(stereo.unspecified_stereo_centers)) +
    row("Defined stereo", stereo.has_defined_stereo ? "yes" : "no") +
    "</table>" +
    (stereo.unspecified_stereo_centers > 0
      ? '<p class="limit">Some stereocentres are unspecified in the source record. Stereoisomers ' +
        "can differ in biological activity.</p>" : "") +
    "</div></div>" +

    '<div class="wx-sec"><h4>Atoms and bonds</h4><div class="in"><p class="sub">' +
    Object.keys(inv.elements || {}).map(function (el) {
      return esc(el) + "&times;" + esc(inv.elements[el]); }).join(" &middot; ") +
    (inv.rings !== undefined ? " &middot; rings " + esc(inv.rings) : "") + "</p></div></div>" +

    (Object.keys(prov).filter(function (k) { return prov[k]; }).length
      ? '<div class="wx-sec"><h4>Provenance</h4><div class="in"><table class="kv">' +
        Object.keys(prov).filter(function (k) { return prov[k]; }).map(function (k) {
          return row(k.replace(/_/g, " "), esc(prov[k])); }).join("") + "</table></div></div>"
      : "") +

    (payload.evidence_count
      ? '<div class="wx-sec"><div class="in"><a href="#/evidence?compound=' + esc(ident.compound_id) +
        '">View ' + esc(payload.evidence_count) + " evidence record(s) &rarr;</a></div></div>" : "");

  // ---- under-stage: the standardisation audit ----
  if (under) {
    under.innerHTML =
      '<div class="wx-sec"><h4>Structure as submitted vs standardized</h4><div class="in">' +
      '<table class="kv">' +
      "<tr><th>Submitted</th><td><code>" + esc(payload.original_smiles) + "</code></td></tr>" +
      "<tr><th>Standardized</th><td><code>" + esc(payload.standardized_smiles) + "</code></td></tr>" +
      "<tr><th>Changed</th><td>" + (payload.standardization_changed ? "yes" : "no") +
      "</td></tr></table>" + transformationLog(payload) +
      '<p class="sub">' + esc((payload.conformer_3d || {}).disclaimer || "") + "</p>" +
      "</div></div>";
  }
}

async function molecularStudio(params) {
  studioViewer = null;
  // Three-panel workstation: tools | viewport | inspector. The viewport gets the space.
  const main = document.getElementById("view");
  main.classList.add("workstation");
  main.innerHTML =
    '<div class="wx-head"><div><div class="eyebrow">Molecular Studio</div>' +
    "<h1>Structure workstation</h1></div>" +
    '<div class="wx-sub">Structures are SMILES imported from their source database. MOLECULAB ' +
    "holds no experimentally determined 3D structures, and a viewer is not docking: no protein, " +
    "binding site, pose or affinity is computed here.</div>" +
    '<div class="right"><div class="seg" id="studioview">' +
    '<button data-v="split" class="active">Split</button>' +
    '<button data-v="2d">2D</button><button data-v="3d">3D</button></div></div></div>' +
    '<div class="wx-body wx-3" id="studiobody">' +
    '<div class="wx-col">' +
    '<div class="wx-sec"><h4>Compound library</h4><div class="in">' +
    '<input id="studiofilter" type="search" placeholder="Filter compounds..." autocomplete="off">' +
    '<div id="studioitems" class="wx-list">' + skeleton(3) + "</div></div></div>" +
    '<div class="wx-sec"><h4>Structure input</h4><div class="in">' +
    '<form id="smilesform"><input id="smilesin" type="text" ' +
    'placeholder="CC(=O)Oc1ccccc1C(=O)O" autocomplete="off">' +
    '<button class="btn primary" type="submit" style="width:100%;margin-top:6px">Inspect SMILES</button>' +
    "</form>" +
    '<p class="muted" style="font-size:11.5px">SMILES-based constructor. Graphical bond drawing ' +
    "is not implemented; invalid structures are reported, never silently repaired.</p>" +
    "</div></div></div>" +
    '<div class="wx-main"><div class="wx-stage" id="studiostage">' +
    '<div class="state"><span class="g">&#11043;</span><h4>No structure selected</h4>' +
    "<p>Choose a compound or enter a SMILES string.</p></div></div>" +
    '<div class="wx-under" id="studiounder"></div></div>' +
    '<div class="wx-col" id="studiopanel">' +
    '<div class="state"><p>Select a compound to see its properties.</p></div></div>';

  document.querySelectorAll("#studioview button").forEach(function (b) {
    b.onclick = function () {
      document.querySelectorAll("#studioview button").forEach(function (x) {
        x.classList.remove("active"); });
      b.classList.add("active");
      studioSetView(b.dataset.v);
    };
  });

  const data = await api("/api/v1/molecules/?limit=300");
  const items = data.items.filter(function (i) { return i.has_structure; });
  const noStructure = data.items.length - items.length;
  const listEl = document.getElementById("studioitems");

  async function open(source, id) {
    document.getElementById("studiopanel").innerHTML = '<p class="empty">Computing structure...</p>';
    try {
      studioPanel(await api("/api/v1/molecules/" + source + "/" + id));
    } catch (err) {
      document.getElementById("studiopanel").innerHTML =
        '<p class="err">Could not load structure: ' + esc(err.message) + "</p>";
    }
  }

  function paint(filter) {
    const shown = items.filter(function (i) {
      return !filter || (i.name || "").toLowerCase().indexOf(filter) !== -1;
    });
    listEl.innerHTML = shown.length ? shown.map(function (i) {
      return '<button class="studio-item" data-source="' + esc(i.source) + '" data-id="' + esc(i.id) +
        '"><strong>' + esc(i.name) + '</strong><span class="sub">' + esc(i.molecular_formula || "") +
        (i.evidence_count ? " &middot; " + esc(i.evidence_count) + " evidence" : "") + "</span></button>";
    }).join("") : '<p class="empty">No compound matches.</p>';
    listEl.querySelectorAll(".studio-item").forEach(function (b) {
      b.onclick = function () {
        listEl.querySelectorAll(".studio-item").forEach(function (x) { x.classList.remove("active"); });
        b.classList.add("active");
        open(b.dataset.source, b.dataset.id);
      };
    });
  }

  paint("");
  if (noStructure) {
    listEl.insertAdjacentHTML("beforeend", '<p class="sub">' + noStructure +
      " compound(s) have no stored structure and cannot be inspected.</p>");
  }
  document.getElementById("studiofilter").oninput = function (ev) {
    paint(ev.target.value.trim().toLowerCase());
  };
  document.getElementById("smilesform").onsubmit = async function (ev) {
    ev.preventDefault();
    const smiles = document.getElementById("smilesin").value.trim();
    if (!smiles) return;
    document.getElementById("studiopanel").innerHTML = '<p class="empty">Computing structure...</p>';
    try {
      studioPanel(await api("/api/v1/molecules/smiles", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ smiles: smiles, name: "Supplied structure" }),
      }));
    } catch (err) {
      document.getElementById("studiopanel").innerHTML =
        '<p class="err">Could not inspect: ' + esc(err.message) + "</p>";
    }
  };

  const preset = params && params.get("smiles");
  if (preset) {
    document.getElementById("smilesin").value = preset;
    document.getElementById("smilesform").dispatchEvent(new Event("submit"));
  }
}
