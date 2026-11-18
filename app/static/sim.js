// MOLECULAB Simulation Lab.
//
// Four working simulations and one honest absence. Every control here changes a real computation
// parameter sent to the backend — none of them is decorative, and no view animates a result that
// was not calculated.
//
//   conformers  RDKit ETKDGv3 embedding + MMFF94/UFF optimisation      (computed)
//   properties  RDKit descriptors across the library                   (computed)
//   similarity  Morgan fingerprints + Tanimoto                         (computed)
//   activity    curated ChEMBL IC50 measurements                       (experimental)
//   protein-ligand                                                     (unavailable)
"use strict";

let simViewer = null;
let simState = { tab: "conformers", conformers: null, conformerIndex: 0, library: [], props: null };

function simTabs(active) {
  const tabs = [
    ["conformers", "Conformer exploration", "computed"],
    ["properties", "Property explorer", "computed"],
    ["similarity", "Similarity explorer", "computed"],
    ["activity", "Activity data", "experimental"],
    ["protein_ligand", "Protein-ligand", "experimental"],
  ];
  return '<div class="seg" role="tablist">' + tabs.map(function (t) {
    return '<button data-tab="' + t[0] + '"' + (t[0] === active ? ' class="active"' : "") +
      ">" + esc(t[1]) + "</button>";
  }).join("") + "</div>";
}

async function simulationsPage(params) {
  simViewer = null;
  simState.tab = (params && params.get("tab")) || simState.tab || "conformers";

  // Workstation shell: the page itself does not scroll; each region scrolls on its own.
  const main = document.getElementById("view");
  main.classList.add("workstation");
  main.innerHTML =
    '<div class="wx-head"><div><div class="eyebrow">Simulation Lab</div>' +
    "<h1>Computational experiments</h1></div>" +
    '<div class="wx-sub">Every calculation here runs for real and reports its algorithm, ' +
    "parameters and limits. Capabilities that do not exist are shown as unavailable.</div></div>" +
    '<div class="wx-strip" id="simstrip"></div>' +
    '<div class="wx-body" id="simbody"></div>';

  const strip = document.getElementById("simstrip");
  const tabs = [["conformers", "Conformer exploration"], ["properties", "Property explorer"],
                ["similarity", "Similarity explorer"], ["activity", "Activity data"],
                ["protein_ligand", "Protein-ligand"]];
  strip.innerHTML = tabs.map(function (t2) {
    return '<button class="btn' + (t2[0] === simState.tab ? " on" : "") + '" data-tab="' +
      t2[0] + '">' + esc(t2[1]) + "</button>";
  }).join("");
  strip.querySelectorAll("button").forEach(function (b) {
    b.onclick = function () {
      simState.tab = b.dataset.tab;
      location.hash = "#/simulations?tab=" + b.dataset.tab;
    };
  });

  const body = document.getElementById("simbody");
  // Reset the layout on every tab switch. Tabs set their own grid (some are two-column), and a
  // stale class from the previous tab squashes the next one and can remove its scroll region.
  body.className = "wx-body";
  body.innerHTML = '<div class="wx-main scrolls">' + skeleton(5) + "</div>";
  try {
    if (simState.tab === "conformers") await simConformers(body);
    else if (simState.tab === "properties") await simProperties(body);
    else if (simState.tab === "similarity") await simSimilarity(body);
    else if (simState.tab === "activity") await simActivity(body);
    else await simProteinLigand(body);
  } catch (err) {
    body.innerHTML = '<div class="wx-main scrolls">' + errorState("Simulation failed", err.message) + "</div>";
  }
}

async function simLibrary() {
  if (simState.library.length) return simState.library;
  const data = await api("/api/v1/molecules/?limit=300");
  simState.library = data.items.filter(function (i) { return i.has_structure; });
  return simState.library;
}

function compoundPicker(id, onPick) {
  return simLibrary().then(function (items) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = items.map(function (i) {
      return '<button class="pick" data-source="' + esc(i.source) + '" data-id="' + esc(i.id) + '">' +
        "<strong>" + esc(i.name) + '</strong><span class="meta">' +
        esc(i.molecular_formula || "") + "</span></button>";
    }).join("");
    el.querySelectorAll(".pick").forEach(function (b) {
      b.onclick = async function () {
        el.querySelectorAll(".pick").forEach(function (x) { x.classList.remove("active"); });
        b.classList.add("active");
        const payload = await api("/api/v1/molecules/" + b.dataset.source + "/" + b.dataset.id +
                                  "?want_3d=false");
        if (payload.ok) onPick(payload.standardized_smiles, payload.name);
      };
    });
  });
}

// ------------------------------------------------- A: conformer exploration

async function simConformers(body) {
  body.className = "wx-body wx-2";
  body.innerHTML =
    '<div class="wx-col">' +
    '<div class="wx-sec"><h4>Compound</h4><div class="in">' +
    '<div id="cfpick" class="wx-list">' + skeleton(3) + "</div>" +
    '<div class="field"><label for="cfsmiles">or a SMILES string</label>' +
    '<input id="cfsmiles" type="text" placeholder="CC(=O)Oc1ccccc1C(=O)O"></div>' +
    "</div></div>" +
    '<div class="wx-sec"><h4>Generation parameters</h4><div class="in">' +
    '<div class="field"><label for="cfn">Conformers to generate: <span id="cfnv">10</span></label>' +
    '<input id="cfn" type="range" min="1" max="50" value="10"></div>' +
    '<div class="field"><label for="cfrms">Prune RMS threshold (A): <span id="cfrmsv">0.5</span></label>' +
    '<input id="cfrms" type="range" min="0" max="2" step="0.1" value="0.5"></div>' +
    '<label class="row"><input type="checkbox" id="cfopt" checked> Optimise with force field</label>' +
    '<button class="btn primary" id="cfrun" style="width:100%">Generate ensemble</button>' +
    '<p class="muted" style="font-size:11.5px">Each control is a real RDKit parameter. Pruning ' +
    "removes near-duplicate embeddings, so fewer conformers may be returned than requested.</p>" +
    "</div></div></div>" +

    '<div class="wx-main">' +
    '<div class="wx-stage" id="cfviewport">' +
    '<div class="state"><span class="g">&#9707;</span><h4>No ensemble yet</h4>' +
    "<p>Pick a compound and generate conformers.</p></div></div>" +
    '<div class="wx-under" id="cfresult"></div></div>';

  document.getElementById("cfn").oninput = function (e) {
    document.getElementById("cfnv").textContent = e.target.value; };
  document.getElementById("cfrms").oninput = function (e) {
    document.getElementById("cfrmsv").textContent = e.target.value; };
  compoundPicker("cfpick", function (smiles) {
    document.getElementById("cfsmiles").value = smiles; });
  document.getElementById("cfrun").onclick = runConformers;
}

async function runConformers() {
  const smiles = document.getElementById("cfsmiles").value.trim();
  const result = document.getElementById("cfresult");
  if (!smiles) { result.innerHTML = emptyState("No structure", "Select a compound or enter a SMILES."); return; }
  result.innerHTML = skeleton(3);
  document.getElementById("cfviewport").innerHTML =
    '<div class="state"><span class="g">⬡</span><h4>Computing…</h4><p>Embedding and optimising.</p></div>';
  try {
    const data = await api("/api/v1/simulations/conformers", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({
        smiles: smiles,
        n_conformers: +document.getElementById("cfn").value,
        prune_rms: +document.getElementById("cfrms").value,
        optimise: document.getElementById("cfopt").checked,
      }),
    });
    simState.conformers = data;
    renderConformers(data);
  } catch (err) {
    result.innerHTML = errorState("Could not generate conformers", err.message);
    const vp = document.getElementById("cfviewport");
    if (vp) vp.innerHTML = errorState("No geometry", err.message);
  } finally {
    // A permanent "Computing..." tells the researcher nothing, so never leave one on screen.
    // This only fires if the success path genuinely produced no viewable geometry.
    const vp = document.getElementById("cfviewport");
    if (vp && vp.innerHTML.indexOf("Embedding and optimising") !== -1) {
      vp.innerHTML = emptyState("No 3D geometry for this structure",
        "RDKit returned no conformer that could be displayed. Single atoms and some ions have " +
        "no meaningful 3D conformation; for anything larger, try fewer conformers or switch " +
        "optimisation off.");
    }
  }
}

function renderConformers(data) {
  const energies = data.conformers.map(function (c) { return c.relative_energy_kcal_per_mol; });
  const maxRel = Math.max.apply(null, energies.filter(function (e) { return e !== null; })) || 1;

  const ladder = data.conformers.map(function (c, i) {
    const rel = c.relative_energy_kcal_per_mol;
    const pct = rel === null ? 0 : (1 - rel / (maxRel || 1)) * 100;
    return '<div class="rung' + (i === 0 ? " active" : "") + '" data-i="' + i + '">' +
      '<span class="n">#' + c.rank + "</span>" +
      '<span class="track"><span class="fill" style="width:' + pct.toFixed(1) + '%"></span></span>' +
      '<span class="e">' + (rel === null ? "—" : "+" + rel.toFixed(2)) + "</span></div>";
  }).join("");

  document.getElementById("cfresult").innerHTML =
    '<div class="ws" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px">' +
    panel("Conformer ensemble",
      '<p class="muted" style="font-size:12.5px">' + data.generated_conformers + " of " +
      data.requested_conformers + " requested conformers kept" +
      (data.pruned > 0 ? " (" + data.pruned + " pruned as near-duplicates)" : "") +
      ". Relative energy in kcal/mol above the lowest found.</p>" +
      '<div class="ladder" id="cfladder">' + ladder + "</div>",
      chip("computed", "Computed")) +
    panel("What this is",
      '<p class="note caution"><strong>Force-field energy is not binding affinity.</strong> ' +
      esc(data.interpretation) + "</p>" +
      methodBlock(data.method) + limitations(data.limitations),
      "") +
    "</div>";

  const pick = function (i) {
    simState.conformerIndex = i;
    document.querySelectorAll("#cfladder .rung").forEach(function (r, j) {
      r.classList.toggle("active", i === j);
    });
    show3D(data.conformers[i]);
  };
  document.querySelectorAll("#cfladder .rung").forEach(function (r) {
    r.onclick = function () { pick(+r.dataset.i); };
  });
  pick(0);
}

function show3D(conformer) {
  // The viewport is rebuilt from scratch each time: a loading or error state replaces its contents,
  // so the overlay and controls cannot be assumed to still exist.
  const host = document.getElementById("cfviewport");
  if (!host) return;
  if (!conformer || !conformer.molblock) {
    host.innerHTML = emptyState("No 3D geometry",
      "This structure produced no displayable conformer.");
    return;
  }
  host.innerHTML =
    '<div class="mol3d" id="cfmol" style="width:100%;height:100%"></div>' +
    '<div class="viewport-overlay" id="cfbadges"></div>' +
    '<div class="viewport-corner"><button class="btn ghost" id="cffull" title="Full screen">&#9974;</button></div>';
  const full = document.getElementById("cffull");
  if (full) {
    full.onclick = function () { if (host.requestFullscreen) host.requestFullscreen(); };
  }
  const badges = document.getElementById("cfbadges");
  if (typeof window.$3Dmol === "undefined") {
    document.getElementById("cfmol").innerHTML =
      errorState("3D viewer unavailable", "3Dmol.js did not load.");
    return;
  }
  simViewer = window.$3Dmol.createViewer(document.getElementById("cfmol"), { backgroundColor: "#080a0d" });
  simViewer.addModel(conformer.molblock, "mol");
  simViewer.setStyle({}, { stick: { radius: 0.13 }, sphere: { scale: 0.21 } });
  simViewer.zoomTo();
  simViewer.render();
  if (badges) {
    badges.innerHTML = chip("computed", "Conformer #" + conformer.rank +
      (conformer.relative_energy_kcal_per_mol !== null
        ? "  +" + conformer.relative_energy_kcal_per_mol.toFixed(2) + " kcal/mol" : "")) +
      chip("missing", "Not docking");
  }
}

// ------------------------------------------------- B: property exploration

async function simProperties(body) {
  body.innerHTML = skeleton(5);
  const data = await api("/api/v1/simulations/properties");
  simState.props = data;
  const fields = data.fields;
  const opts = function (sel) {
    return fields.map(function (f) {
      return '<option value="' + esc(f.key) + '"' + (f.key === sel ? " selected" : "") + ">" +
        esc(f.label) + "</option>";
    }).join("");
  };
  body.innerHTML =
    '<div class="ws ws-sim"><div class="ws-pane stack">' +
    panel("Axes",
      '<div class="field"><label for="pxs">X axis</label><select id="pxs">' + opts("molecular_weight") + "</select></div>" +
      '<div class="field" style="margin-top:10px"><label for="pys">Y axis</label><select id="pys">' + opts("clogp") + "</select></div>" +
      '<p class="muted" style="font-size:11.5px;margin-top:10px">' + data.count +
      " compounds with a stored structure. All values computed locally by RDKit.</p>",
      chip("computed", "Computed")) +
    panel("Interpretation", '<p class="note caution">' + esc(data.interpretation) + "</p>" +
      methodBlock(data.method)) +
    "</div><div class=\"stack\">" +
    panel("Descriptor space", '<div id="pscatter"></div>', "") +
    panel("Values", '<div style="max-height:380px;overflow:auto"><table class="dtable" id="ptable"></table></div>', "", "scroll") +
    "</div></div>";

  const draw = function () {
    const xk = document.getElementById("pxs").value, yk = document.getElementById("pys").value;
    const xl = fields.find(function (f) { return f.key === xk; }).label;
    const yl = fields.find(function (f) { return f.key === yk; }).label;
    const points = data.rows.filter(function (r) {
      return typeof r[xk] === "number" && typeof r[yk] === "number";
    }).map(function (r) { return { x: r[xk], y: r[yk], label: r.name }; });
    document.getElementById("pscatter").innerHTML = points.length
      ? scatterSVG(points, xl, yl)
      : emptyState("Nothing to plot", "No compound has both descriptors.");
    document.getElementById("ptable").innerHTML =
      "<thead><tr><th>Compound</th><th class=\"num\">" + esc(xl) + '</th><th class="num">' + esc(yl) +
      "</th></tr></thead><tbody>" +
      data.rows.slice(0, 200).map(function (r) {
        return "<tr><td>" + esc(r.name) + '</td><td class="num">' + measure(r[xk]) +
          '</td><td class="num">' + measure(r[yk]) + "</td></tr>";
      }).join("") + "</tbody>";
  };
  document.getElementById("pxs").onchange = draw;
  document.getElementById("pys").onchange = draw;
  draw();
}

// ------------------------------------------------- C: similarity explorer

async function simSimilarity(body) {
  body.innerHTML =
    '<div class="ws ws-sim"><div class="ws-pane stack">' +
    panel("Reference compound", '<div id="simpick" style="max-height:230px;overflow:auto">' + skeleton(3) + "</div>" +
      '<div class="field" style="margin-top:10px"><label for="simsmiles">or a SMILES string</label>' +
      '<input id="simsmiles" type="text" placeholder="CC(=O)Oc1ccccc1C(=O)O"></div>' +
      '<label class="row" style="margin-top:10px"><input type="checkbox" id="simjak1"> ' +
      "Include the 7,777-compound JAK1 set</label>" +
      '<button class="btn primary" id="simrun" style="margin-top:12px;width:100%">Compute similarity</button>') +
    "</div><div id=\"simout\">" +
    emptyState("No reference selected", "Pick a compound to rank the library against it.") +
    "</div></div>";
  compoundPicker("simpick", function (smiles) { document.getElementById("simsmiles").value = smiles; });
  document.getElementById("simrun").onclick = async function () {
    const smiles = document.getElementById("simsmiles").value.trim();
    const out = document.getElementById("simout");
    if (!smiles) { out.innerHTML = emptyState("No structure", "Select a compound or enter a SMILES."); return; }
    out.innerHTML = skeleton(5);
    try {
      const data = await api("/api/v1/simulations/similarity", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ smiles: smiles, top_n: 40,
          include_activity_dataset: document.getElementById("simjak1").checked }),
      });
      out.innerHTML =
        panel("Ranked by structural similarity",
          '<p class="muted" style="font-size:12.5px">' + data.compared + " of " + data.library_size +
          " library structures compared" +
          (data.skipped_unparseable ? " · " + data.skipped_unparseable + " unparseable and skipped" : "") +
          "</p>" +
          '<table class="dtable"><thead><tr><th>Compound</th><th>Tanimoto</th>' +
          "<th>Scaffold</th><th>Source</th></tr></thead><tbody>" +
          data.results.map(function (r) {
            return "<tr><td>" + esc(r.name || r.id) + "</td><td style=\"min-width:150px\">" +
              fractionBar(r.tanimoto) + "</td><td>" +
              (r.shares_scaffold ? chip("computed", "same scaffold") : '<span class="muted">different</span>') +
              "</td><td>" + esc(r.source) +
              (r.source === "compound" ? ' <a href="#/studio">open</a>' : "") + "</td></tr>";
          }).join("") + "</tbody></table>", chip("computed", "Computed")) +
        panel("What similarity does and does not mean",
          '<p class="note caution"><strong>Similarity is not activity.</strong> ' +
          esc(data.interpretation) + "</p>" + methodBlock(data.method) + limitations(data.limitations));
    } catch (err) {
      out.innerHTML = errorState("Similarity search failed", err.message);
    }
  };
}

// ------------------------------------------------- D: activity exploration

async function simActivity(body) {
  body.innerHTML = skeleton(5);
  let data;
  try {
    data = await api("/api/v1/simulations/activity?limit=120");
  } catch (err) {
    body.innerHTML = unavailableState("Activity dataset not installed",
      "research_artifacts/jak1_curated.json is not present in this installation.");
    return;
  }
  const ds = data.dataset;
  const render = function (d) {
    const s = d.statistics || {};
    document.getElementById("actout").innerHTML =
      '<div class="tiles" style="margin-bottom:12px">' +
      tile("Compounds matched", d.matched, chip("experimental", "Experimental")) +
      tile("Median pIC50", s.median !== undefined ? s.median : "—", "range " + (s.min || "—") + "–" + (s.max || "—")) +
      tile("Unreplicated", s.unreplicated !== undefined ? s.unreplicated : "—", "single measurement only") +
      tile("Scaffolds", s.unique_scaffolds !== undefined ? s.unique_scaffolds : "—", "Bemis–Murcko") +
      "</div>" +
      panel("pIC50 distribution", histogramSVG(d.histogram, d.histogram_axis),
        chip("experimental", "Experimental")) +
      panel("Measurements",
        '<table class="dtable"><thead><tr><th>ChEMBL id</th><th class="num">pIC50</th>' +
        '<th class="num">replicates</th><th class="num">fold spread</th><th>assays</th></tr></thead><tbody>' +
        d.rows.slice(0, 120).map(function (r) {
          return "<tr><td>" + esc(r.molecule_chembl_id) + '</td><td class="num">' +
            measure(r.p_activity_median, "", 2) + '</td><td class="num">' + esc(r.replicate_count) +
            '</td><td class="num">' + (r.fold_spread > 10
              ? '<span style="color:var(--contradiction)">' + esc(r.fold_spread) + "×</span>"
              : esc(r.fold_spread) + "×") +
            "</td><td>" + esc((r.assay_ids || []).length) + "</td></tr>";
        }).join("") + "</tbody></table>", chip("experimental", "Experimental"));
  };

  body.innerHTML =
    '<div class="ws ws-sim"><div class="ws-pane stack">' +
    panel("Dataset",
      '<table class="kv"><tr><th>Target</th><td>' + esc(ds.target.gene_symbol) + " · " +
      esc(ds.target.chembl_target_id) + "</td></tr>" +
      "<tr><th>Organism</th><td>" + esc(ds.target.organism) + "</td></tr>" +
      "<tr><th>Endpoint</th><td>" + esc(ds.endpoint) + "</td></tr>" +
      "<tr><th>Source</th><td>" + esc(ds.source) + "</td></tr>" +
      "<tr><th>Retrieved</th><td>" + esc(ds.retrieved) + "</td></tr>" +
      "<tr><th>Compounds</th><td>" + esc(ds.compound_count) + "</td></tr></table>" +
      '<p class="note" style="margin-top:10px">' + esc(ds.compatibility_rule) + "</p>",
      chip("experimental", "Experimental")) +
    panel("Filters",
      '<div class="field"><label for="acmin">Minimum pIC50: <span id="acminv">0</span></label>' +
      '<input id="acmin" type="range" min="4" max="11" step="0.5" value="4"></div>' +
      '<div class="field" style="margin-top:10px"><label for="acrep">Minimum replicates: ' +
      '<span id="acrepv">1</span></label><input id="acrep" type="range" min="1" max="5" value="1"></div>' +
      '<button class="btn primary" id="acrun" style="margin-top:12px;width:100%">Apply filters</button>') +
    panel("Curation and limits", "<p class=\"muted\" style=\"font-size:12.5px\">" + esc(ds.curation) + "</p>" +
      limitations(ds.limitations)) +
    '</div><div id="actout"></div></div>';

  document.getElementById("acmin").oninput = function (e) {
    document.getElementById("acminv").textContent = e.target.value;
  };
  document.getElementById("acrep").oninput = function (e) {
    document.getElementById("acrepv").textContent = e.target.value;
  };
  document.getElementById("acrun").onclick = async function () {
    document.getElementById("actout").innerHTML = skeleton(4);
    const d = await api("/api/v1/simulations/activity?limit=120&min_p_activity=" +
      document.getElementById("acmin").value + "&min_replicates=" +
      document.getElementById("acrep").value);
    render(d);
  };
  render(data);
}

// ------------------------------------------- E: protein-ligand
//
// MOLECULAB still does not dock, and still says so. What it does instead is stronger evidence,
// not weaker: it measures how a ligand ACTUALLY sits in its target in a deposited experimental
// structure. Those contacts were observed by crystallography, so they are measurements.

const PL_EXAMPLES = [
  ["1M17", "EGFR kinase + erlotinib"],
  ["1STP", "Streptavidin + biotin"],
  ["3ERT", "Oestrogen receptor + tamoxifen"],
  ["4HBV", "JAK1 kinase inhibitor complex"],
  ["1HSG", "HIV-1 protease + indinavir"],
];

async function simProteinLigand(body) {
  // Reset the layout: a previous tab may have left a two-column grid on the body, and the body
  // itself does not scroll. Content must live inside a .wx-main.scrolls region.
  body.className = "wx-body";
  body.innerHTML = '<div class="wx-main scrolls" id="plWrap"></div>';
  document.getElementById("plWrap").innerHTML =
    panel("Protein-ligand contacts, from experimental structures",
      '<p class="lede">MOLECULAB does not dock and predicts no pose. Instead it retrieves a ' +
      "<strong>deposited experimental structure</strong> from the RCSB Protein Data Bank and " +
      "measures the contacts that are actually present in it. Those contacts were observed, " +
      "not predicted.</p>" +
      '<div class="row"><input id="plId" placeholder="PDB id, e.g. 1M17" style="width:12rem">' +
      '<input id="plLig" placeholder="ligand code (optional)" style="width:14rem">' +
      '<button class="btn primary" id="plGo">Analyse</button></div>' +
      '<div class="pgrid" style="margin-top:8px">' + PL_EXAMPLES.map(function (e) {
        return '<button class="chipb" data-pl="' + e[0] + '" title="' + esc(e[1]) + '">' +
          e[0] + "</button>"; }).join("") + "</div>" +
      '<p class="sub">' + PL_EXAMPLES.map(function (e) {
        return esc(e[0]) + " = " + esc(e[1]); }).join(" &middot; ") + "</p>",
      chip("experimental", "Experimental")) +
    panel("Docking is still unavailable",
      '<p class="note unavailable">No docking engine is integrated, and MOLECULAB will not ' +
      "show a predicted pose or an affinity. A docking score is the easiest plausible-looking " +
      "number to fabricate and one of the hardest for a reader to challenge. What is below " +
      "instead is observed structure, which is stronger evidence than a prediction.</p>",
      chip("missing", "No docking")) +
    '<div id="plOut"></div>';

  const run = async function (pdbId, ligand) {
    const out = document.getElementById("plOut");
    out.innerHTML = '<div class="state"><span class="g">&#8987;</span><h4>Retrieving structure' +
      "</h4><p>Downloading " + esc(pdbId) + " from the RCSB PDB and measuring contacts.</p></div>";
    try {
      const r = await api("/api/v1/bench/calculations/protein_ligand_contacts/run", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ pdb_id: pdbId, ligand_code: ligand || null }),
      });
      if (!r.succeeded) {
        out.innerHTML = errorState("Could not analyse that structure", r.error);
        return;
      }
      renderProteinLigand(out, r.result);
    } catch (err) {
      out.innerHTML = errorState("Could not retrieve the structure", err.message);
    }
  };

  document.getElementById("plGo").onclick = function () {
    const id = document.getElementById("plId").value.trim();
    if (id) run(id, document.getElementById("plLig").value.trim());
  };
  document.getElementById("plWrap").querySelectorAll("[data-pl]").forEach(function (b) {
    b.onclick = function () {
      document.getElementById("plId").value = b.dataset.pl;
      run(b.dataset.pl, "");
    };
  });
}

function renderProteinLigand(out, d) {
  out.innerHTML =
    '<div class="tiles" style="margin-bottom:12px">' +
    tile("Contact residues", d.contact_residue_count, chip("experimental", "Measured")) +
    tile("H-bond candidates", d.hydrogen_bond_candidate_count, chip("derived", "Geometric")) +
    tile("Resolution", d.resolution_angstrom ? d.resolution_angstrom + " A" : "-",
         chip("experimental", d.experimental_method || "")) +
    tile("Ligand", d.ligand.code, chip("experimental", d.ligand.heavy_atom_count + " atoms")) +
    "</div>" +

    panel(esc(d.pdb_id) + " - " + esc(d.title || "structure"),
      '<div class="row gap">' +
      '<div id="plViewer" class="viewer3d" style="width:360px;height:300px"></div>' +
      '<div style="flex:1;min-width:0">' +
      '<table class="dtable"><thead><tr><th>Residue</th><th class="num">Closest</th>' +
      '<th class="num">Contacts</th></tr></thead><tbody>' +
      d.contact_residues.slice(0, 14).map(function (c) {
        return "<tr><td>" + esc(c.residue) + " " + esc(c.chain) + esc(c.residue_number) +
          '</td><td class="num">' + measure(c.closest_distance_angstrom, " Å", 2) +
          '</td><td class="num">' + esc(String(c.contact_count)) + "</td></tr>"; }).join("") +
      "</tbody></table></div></div>" +
      '<p class="note">' + esc(d.interpretation) + "</p>",
      chip("experimental", "Observed structure"), "scroll") +

    (d.hydrogen_bond_candidates.length
      ? panel("Hydrogen bond candidates",
          '<table class="dtable"><thead><tr><th>Protein residue</th><th>Protein atom</th>' +
          '<th>Ligand atom</th><th class="num">Distance</th></tr></thead><tbody>' +
          d.hydrogen_bond_candidates.map(function (h) {
            return "<tr><td>" + esc(h.protein_residue) + "</td><td>" + esc(h.protein_atom) +
              "</td><td>" + esc(h.ligand_atom) + '</td><td class="num">' +
              measure(h.distance_angstrom, " Å", 2) + "</td></tr>"; }).join("") +
          "</tbody></table>" +
          '<p class="sub">Candidates by heavy-atom distance and element only. Most crystal ' +
          "structures do not resolve hydrogens, so protonation and geometry are not confirmed.</p>",
          chip("derived", "Geometric inference"))
      : "") +

    panel("What this does and does not establish", limitations(d.limitations) +
      '<table class="kv" style="margin-top:10px">' +
      "<tr><th>Source</th><td>" + esc(d.provenance.source) + "</td></tr>" +
      "<tr><th>Retrieved</th><td>" + esc(String(d.provenance.retrieved_at || "")) +
      (d.provenance.from_cache ? " (from cache)" : "") + "</td></tr>" +
      "<tr><th>Contact cutoff</th><td>" +
      esc(String(d.parameters.contact_cutoff_angstrom)) + " &#8491;</td></tr></table>");

  // The pocket in 3D: protein contacts as lines, the ligand as sticks.
  const host = document.getElementById("plViewer");
  if (host && window.$3Dmol && d.pocket_pdb) {
    try {
      const v = window.$3Dmol.createViewer(host, { backgroundColor: "0x0d1015" });
      v.addModel(d.pocket_pdb, "pdb");
      v.setStyle({}, { line: { linewidth: 1.4, colorscheme: "greyCarbon" } });
      v.setStyle({ resn: d.ligand.code },
                 { stick: { radius: 0.18, colorscheme: "greenCarbon" } });
      v.zoomTo({ resn: d.ligand.code });
      v.render();
    } catch (err) {
      host.innerHTML = '<p class="sub">3D view unavailable: ' + esc(err.message) + "</p>";
    }
  }
}
