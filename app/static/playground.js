// MOLECULAB Molecular Playground — build in 2D, manipulate in 3D, verified by RDKit in real time.
//
// Layout: a split stage. Left half is the JSME structure editor (BSD-3-Clause, vendored, offline).
// Right half is a live 3Dmol.js viewer you can rotate, zoom, pan and click into. Edit the 2D
// structure and the 3D rebuilds itself automatically — real conformers from RDKit ETKDGv3 + MMFF94,
// not a cartoon.
//
// THE FRONTEND IS NEVER THE CHEMISTRY AUTHORITY. JSME reports only what was drawn, as SMILES.
// Formula, weight, descriptors, validity, standardization, InChIKey and every 3D coordinate come
// from the backend, where RDKit re-parses that SMILES from scratch. An invalid structure is
// rejected with its reason and nothing is computed.
//
// Nothing here overwrites a scientific record: an imported compound is held as the ORIGINAL and the
// edit is a separate candidate.
"use strict";

const PG = {
  jsme: null,
  ready: false,
  original: null,        // { label, smiles } as imported — never mutated
  current: null,         // last backend-validated state
  sessionId: null,
  debounce: null,
  conf3dDebounce: null,
  history: [],
  pending: false,
  // 3D viewer state
  viewer: null,
  conformers: [],
  confIndex: 0,
  style: "ballstick",
  spinning: false,
  labels: false,
  surface: null,
  autoThree: true,
  lastThreeSmiles: null,
  view: "split",
};

const GROUPS = [
  { label: "Methyl", smiles: "C", hint: "-CH3" },
  { label: "Hydroxyl", smiles: "O", hint: "-OH" },
  { label: "Amino", smiles: "N", hint: "-NH2" },
  { label: "Carboxyl", smiles: "C(=O)O", hint: "-COOH" },
  { label: "Carbonyl", smiles: "C=O", hint: "C=O" },
  { label: "Amide", smiles: "C(=O)N", hint: "-C(=O)NH2" },
  { label: "Methoxy", smiles: "OC", hint: "-OCH3" },
  { label: "Phenyl", smiles: "c1ccccc1", hint: "benzene ring" },
  { label: "Nitro", smiles: "[N+](=O)[O-]", hint: "-NO2" },
  { label: "Fluoro", smiles: "F", hint: "-F" },
  { label: "Chloro", smiles: "Cl", hint: "-Cl" },
  { label: "CF3", smiles: "C(F)(F)F", hint: "-CF3" },
];

// Kekule (explicit alternating bonds) rather than aromatic lowercase: JSME draws a lowercase
// aromatic carbon bearing an exocyclic =O incorrectly, which broke caffeine's ring system.
// RDKit accepts both and standardises them to the same molecule.
const STARTERS = [
  { label: "Benzene", smiles: "C1=CC=CC=C1" },
  { label: "Aspirin", smiles: "CC(=O)OC1=CC=CC=C1C(=O)O" },
  { label: "Paracetamol", smiles: "CC(=O)NC1=CC=C(O)C=C1" },
  { label: "Caffeine", smiles: "CN1C=NC2=C1C(=O)N(C)C(=O)N2C" },
  { label: "Indole", smiles: "C1=CC=C2NC=CC2=C1" },
  { label: "Glucose", smiles: "OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O" },
];

// CPK-ish palette matched to the application's dark surfaces.
const ELEMENT_COLOURS = {
  C: 0x7d8795, H: 0xe3e8ef, O: 0xd9544d, N: 0x6f9fe0, S: 0xd4a95a,
  P: 0xe08a4a, F: 0x4db6a4, Cl: 0x6fc26f, Br: 0xa6603a, I: 0x9b59b6,
};

// --------------------------------------------------------------------- page

async function playgroundPage(params) {
  const main = document.getElementById("view");
  main.classList.add("workstation");
  PG.sessionId = (params && params.get("session")) || null;
  const startSmiles = (params && params.get("smiles")) || "";

  main.innerHTML =
    '<div class="wx-head"><div><div class="eyebrow">Molecular Playground</div>' +
    "<h1>Build in 2D, explore in 3D</h1></div>" +
    '<div class="wx-acts">' +
    (PG.sessionId ? '<button class="btn ghost" id="pgBack">Back to the Bench</button>' : "") +
    "</div>" +
    '<div class="wx-sub">Draw a structure and the 3D view rebuilds itself. Every number comes ' +
    "from RDKit on the backend, never from the editor.</div></div>" +

    '<div class="wx-body wx-3">' +

    // ---------------- left rail
    '<div class="wx-col">' +
    '<div class="wx-sec"><h4>Start from</h4><div class="in">' +
    '<div class="field"><div class="row">' +
    '<input id="pgSmi" placeholder="paste SMILES" style="flex:1">' +
    '<button class="btn" id="pgLoad">load</button></div></div>' +
    '<div class="pgrid">' + STARTERS.map(function (s) {
      return '<button class="chipb" data-start="' + esc(s.smiles) + '">' + esc(s.label) +
        "</button>"; }).join("") + "</div>" +
    '<button class="btn ghost" id="pgPick" style="width:100%;margin-top:8px">' +
    "Import from MOLECULAB</button><div id=\"pgPickOut\"></div>" +
    "</div></div>" +

    '<div class="wx-sec"><h4>Functional groups</h4><div class="in">' +
    '<div class="pgrid">' + GROUPS.map(function (g) {
      return '<button class="chipb" data-grp="' + esc(g.smiles) + '" title="' + esc(g.hint) +
        '">' + esc(g.label) + "</button>"; }).join("") + "</div>" +
    '<p class="sub">Added as real atoms and bonds, then validated by RDKit.</p>' +
    "</div></div>" +

    '<div class="wx-sec"><h4>Versions</h4><div class="in"><div id="pgHist">' +
    '<p class="sub">Save a version to compare against it.</p></div></div></div>' +
    "</div>" +

    // ---------------- centre: the split stage
    '<div class="wx-main pg-main">' +
    '<div class="pg-bar">' +
    '<div class="seg" id="pgView">' +
    '<button data-view="build">2D</button>' +
    '<button data-view="split" class="on">Split</button>' +
    '<button data-view="three">3D</button>' +
    "</div>" +
    '<button class="btn" id="pgSave">Save version</button>' +
    '<button class="btn ghost" id="pgClear">Clear</button>' +
    '<span class="pg-status" id="pgStatus">Loading editor...</span>' +
    "</div>" +

    '<div class="pg-split" id="pgSplit">' +
    '<div class="pg-pane" id="pgPane2d">' +
    '<div class="pg-pane-h">2D structure <span class="sub">click an element, then the canvas; ' +
    "drag atom to atom to bond</span></div>" +
    '<div id="pgJsme" class="pg-canvas"></div>' +
    "</div>" +
    '<div class="pg-pane" id="pgPane3d">' +
    '<div class="pg-pane-h">3D conformer ' + chip("computed", "Computed") +
    '<span class="sub">drag to rotate, scroll to zoom, click an atom</span></div>' +
    '<div class="pg-3d-wrap"><div id="pgViewer" class="pg-3d"></div>' +
    '<div class="pg-3d-empty" id="pg3dEmpty">Draw a structure to generate a 3D conformer.</div>' +
    "</div>" +
    '<div class="pg-3d-bar" id="pg3dBar"></div>' +
    "</div></div>" +
    "</div>" +

    // ---------------- right rail
    '<div class="wx-col">' +
    '<div class="wx-sec"><h4>Structure</h4><div class="in" id="pgIdent">' +
    '<p class="sub">Draw something to begin.</p></div></div>' +
    '<div class="wx-sec"><h4>Selection</h4><div class="in" id="pgSel">' +
    '<p class="sub">Click an atom in the 3D view.</p></div></div>' +
    '<div class="wx-sec"><h4>Computed properties</h4><div class="in" id="pgProps">' +
    '<p class="sub">Awaiting a valid structure.</p></div></div>' +
    '<div class="wx-sec"><h4>Original vs modified</h4><div class="in" id="pgDiff">' +
    '<p class="sub">Import or save a version to compare.</p></div></div>' +
    '<div class="wx-sec"><h4>Ask the assistant</h4><div class="in" id="pgAi">' +
    '<p class="sub">Draw a valid structure to ask about it.</p></div></div>' +
    '<div class="wx-sec"><h4>Calculations</h4><div class="in" id="pgCalc">' +
    '<p class="sub">Awaiting a valid structure.</p></div></div>' +
    "</div></div>";

  if (PG.sessionId) {
    document.getElementById("pgBack").onclick = function () {
      location.hash = "#/bench?session=" + PG.sessionId + "&stage=explore";
    };
  }
  wirePlaygroundControls();
  setView("split");
  await loadJsme(startSmiles);
}

function wirePlaygroundControls() {
  document.querySelectorAll("[data-start]").forEach(function (b) {
    b.onclick = function () { pgSetStructure(b.dataset.start, b.textContent.trim()); };
  });
  document.querySelectorAll("[data-grp]").forEach(function (b) {
    b.onclick = function () { pgAppendGroup(b.dataset.grp, b.textContent.trim()); };
  });
  document.querySelectorAll("[data-view]").forEach(function (b) {
    b.onclick = function () { setView(b.dataset.view); };
  });
  document.getElementById("pgLoad").onclick = function () {
    const v = document.getElementById("pgSmi").value.trim();
    if (v) pgSetStructure(v, "typed");
  };
  document.getElementById("pgSmi").onkeydown = function (e) {
    if (e.key === "Enter") document.getElementById("pgLoad").click();
  };
  document.getElementById("pgSave").onclick = pgSaveVersion;
  document.getElementById("pgClear").onclick = pgClear;
  document.getElementById("pgPick").onclick = pgPickCompound;
}

function setView(v) {
  PG.view = v;
  const split = document.getElementById("pgSplit");
  if (split) split.className = "pg-split view-" + v;
  document.querySelectorAll("[data-view]").forEach(function (b) {
    b.className = b.dataset.view === v ? "on" : "";
  });
  // Both viewers hold their own pixel size and must be re-told when the panes change.
  setTimeout(function () {
    if (PG.viewer) {
      try { PG.viewer.resize(); PG.viewer.render(); } catch (e) { /* ignore */ }
    }
    pgResizeJsme();
  }, 80);
}

function pgClear() {
  if (PG.jsme) {
    try { PG.jsme.reset(); } catch (e) { try { PG.jsme.readGenericMolecularInput(""); } catch (e2) {} }
  }
  PG.current = null;
  PG.conformers = [];
  PG.lastThreeSmiles = null;
  if (PG.viewer) { try { PG.viewer.clear(); PG.viewer.render(); } catch (e) {} }
  document.getElementById("pg3dEmpty").style.display = "";
  document.getElementById("pg3dBar").innerHTML = "";
  document.getElementById("pgIdent").innerHTML = '<p class="sub">Draw something to begin.</p>';
  document.getElementById("pgProps").innerHTML = '<p class="sub">Awaiting a valid structure.</p>';
  document.getElementById("pgSel").innerHTML = '<p class="sub">Click an atom in the 3D view.</p>';
  document.getElementById("pgCalc").innerHTML = '<p class="sub">Awaiting a valid structure.</p>';
  pgStatus("Canvas cleared.", "");
}

// --------------------------------------------------------------------- JSME

function loadJsme(startSmiles) {
  return new Promise(function (resolve) {
    window.jsmeOnLoad = function () {
      try {
        // JSME is GWT/Swing-derived: it lays out to the pixel size it is GIVEN and ignores a
        // percentage, which leaves the toolbar crammed into a corner of an empty canvas. Measure
        // the pane and hand it real numbers, then re-measure on resize.
        const box = document.getElementById("pgJsme");
        const w = Math.max(360, (box && box.clientWidth) || 560);
        const h = Math.max(360, (box && box.clientHeight) || 460);
        PG.jsme = new window.JSApplet.JSME("pgJsme", w + "px", h + "px",
                                           { options: "newlook,star,autoez" });
        pgWatchResize();
        PG.ready = true;
        pgStatus("Ready. Pick an element, click the canvas.", "ok");
        PG.jsme.setCallBack("AfterStructureModified", function () { pgSync(false); });
        if (startSmiles) pgSetStructure(startSmiles, "imported");
      } catch (err) {
        pgEditorUnavailable("The editor loaded but could not start: " + err.message);
      }
      resolve();
    };
    if (window.JSApplet && window.JSApplet.JSME) { window.jsmeOnLoad(); return; }

    const s = document.createElement("script");
    s.src = "/static/vendor/jsme/jsme.nocache.js";
    s.onerror = function () {
      pgEditorUnavailable("Could not load the editor from /static/vendor/jsme/jsme.nocache.js.");
      resolve();
    };
    document.head.appendChild(s);
    setTimeout(function () {
      if (!PG.ready) {
        pgEditorUnavailable("The editor did not finish loading. The SMILES box on the left still " +
          "works, and the 3D view and every calculation remain available.");
        resolve();
      }
    }, 15000);
  });
}

// JSME holds the pixel size it was built with, so it has to be re-told whenever its pane changes
// size — switching 2D/Split/3D, or resizing the window. Debounced, because setSize is not cheap.
let pgResizeTimer = null;

function pgResizeJsme() {
  if (!PG.ready || !PG.jsme) return;
  const box = document.getElementById("pgJsme");
  if (!box || !box.clientWidth || !box.clientHeight) return;
  try {
    PG.jsme.setSize(Math.max(360, box.clientWidth) + "px",
                    Math.max(360, box.clientHeight) + "px");
  } catch (err) { /* older builds expose no setSize; the initial size still stands */ }
}

function pgWatchResize() {
  const schedule = function () {
    clearTimeout(pgResizeTimer);
    pgResizeTimer = setTimeout(pgResizeJsme, 150);
  };
  window.addEventListener("resize", schedule);
  if (typeof ResizeObserver === "function") {
    const box = document.getElementById("pgJsme");
    if (box) {
      try { new ResizeObserver(schedule).observe(box); } catch (e) { /* ignore */ }
    }
  }
  setTimeout(pgResizeJsme, 400);
}

function pgEditorUnavailable(why) {
  const pane = document.getElementById("pgJsme");
  if (pane) pane.innerHTML = '<div class="pg-fallback">' + esc(why) + "</div>";
  pgStatus("2D editor unavailable - backend still authoritative", "warn");
  setView("three");
}

function pgStatus(text, kind) {
  const el = document.getElementById("pgStatus");
  if (el) { el.textContent = text; el.className = "pg-status" + (kind ? " " + kind : ""); }
}

// --------------------------------------------------------------------- structure flow

function pgSetStructure(smiles, label) {
  const box = document.getElementById("pgSmi");
  if (box) box.value = smiles;
  if (!PG.original) PG.original = { label: label || "original", smiles: smiles };

  // Validate the SMILES we were GIVEN, immediately. Do not wait for the editor to echo it back:
  // JSME populates its model asynchronously and smiles() returns "" for a few hundred
  // milliseconds after a load, which previously left the 3D panel empty for ever.
  pgValidateSmiles(smiles, label || "loaded");

  if (!PG.ready) return;
  pgDraw(smiles);
}

// Feed the editor a drawable form. The backend returns a Kekule SMILES, which JSME lays out
// correctly; raw aromatic input can produce a broken drawing even when the molecule is valid.
async function pgDraw(smiles) {
  let drawable = smiles;
  try {
    const v = await api("/api/v1/bench/calculations/structure_validation/run", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ smiles: smiles }) });
    const r = v.result || {};
    if (r.valid && r.editor_smiles) drawable = r.editor_smiles;
  } catch (e) { /* fall back to what the researcher supplied */ }
  try {
    PG.jsme.readGenericMolecularInput(drawable);
    setTimeout(pgResizeJsme, 120);
  } catch (err) {
    pgStatus("The editor could not draw that, but the structure is still valid: " +
             err.message, "warn");
  }
}

function pgAppendGroup(fragment, label) {
  const base = pgReadSmiles();
  if (!base) { pgSetStructure(fragment, label); return; }
  // Joining with "." makes a genuinely separate fragment. Guessing an attachment point would
  // invent chemistry, so the researcher connects it on the canvas instead.
  pgSetStructure(base + "." + fragment, label);
  pgStatus("Added " + label + " as a separate fragment - connect it on the canvas.", "warn");
}

function pgReadSmiles() {
  if (!PG.ready || !PG.jsme) return (document.getElementById("pgSmi") || {}).value || "";
  try { return (PG.jsme.smiles() || "").trim(); } catch (err) { return ""; }
}

function pgSync(immediate, attempt) {
  const smiles = pgReadSmiles();
  if (!smiles) {
    // JSME can report an empty structure while its model is still settling. Retry briefly
    // before concluding the canvas really is empty.
    const tries = attempt || 0;
    if (tries < 4) {
      clearTimeout(PG.debounce);
      PG.debounce = setTimeout(function () { pgSync(immediate, tries + 1); }, 220);
      return;
    }
    const ident = document.getElementById("pgIdent");
    if (ident) ident.innerHTML = '<p class="sub">The canvas is empty.</p>';
    return;
  }
  clearTimeout(PG.debounce);
  const run = function () { pgValidateSmiles(smiles, "drawn"); };
  if (immediate) run(); else PG.debounce = setTimeout(run, 450);
}

async function pgValidateSmiles(smiles, origin) {
  if (PG.pending) return;
  PG.pending = true;
  pgStatus("Validating with RDKit...", "");
  try {
    const v = await api("/api/v1/bench/calculations/structure_validation/run", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ smiles: smiles }),
    });
    const res = v.result || {};
    if (!res.valid) {
      document.getElementById("pgIdent").innerHTML =
        '<p class="limit"><strong>RDKit rejected this structure.</strong></p>' +
        '<p class="limit">' + esc(res.error || "No reason given.") + "</p>" +
        '<p class="sub">Your drawing is unchanged. Fix the valence or bonding and it ' +
        "re-validates automatically.</p>";
      document.getElementById("pgProps").innerHTML =
        '<p class="sub">No properties are computed for an invalid structure. MOLECULAB does not ' +
        "guess at what you meant.</p>";
      pgStatus("Invalid structure", "bad");
      return;
    }
    const d = await api("/api/v1/bench/calculations/molecular_descriptors/run", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ smiles: smiles }),
    });
    PG.current = {
      smiles: smiles, origin: origin, validation: res,
      descriptors: (d.result || {}).descriptors || {},
      stereo: (d.result || {}).stereochemistry || {},
      fingerprint: d.reproducibility.fingerprint, versions: d.software_versions,
    };
    renderIdentity();
    renderProps();
    renderCalcOptions();
    renderDiff();
    if (typeof pgAiPanel === "function") pgAiPanel();
    pgStatus("Valid - " + (PG.current.descriptors.molecular_formula || ""), "ok");
    // Real-time 3D: rebuild whenever the structure actually changed.
    const canonical = res.canonical_smiles || smiles;
    if (PG.autoThree && canonical !== PG.lastThreeSmiles) {
      clearTimeout(PG.conf3dDebounce);
      PG.conf3dDebounce = setTimeout(function () { build3D(canonical); }, 250);
    }
  } catch (err) {
    document.getElementById("pgIdent").innerHTML =
      errorState("Backend validation failed", err.message);
    pgStatus("Validation failed", "bad");
  } finally {
    PG.pending = false;
  }
}

// --------------------------------------------------------------------- 3D

async function build3D(smiles) {
  const empty = document.getElementById("pg3dEmpty");
  const bar = document.getElementById("pg3dBar");
  if (!empty || !bar) return;
  empty.textContent = "Generating conformers with RDKit...";
  empty.style.display = "";
  try {
    const r = await api("/api/v1/bench/calculations/conformer_ensemble/run", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ smiles: smiles, n_conformers: 6, optimise: true }),
    });
    if (!r.succeeded) {
      empty.textContent = "3D unavailable: " + (r.error || "conformer generation failed");
      bar.innerHTML = "";
      return;
    }
    PG.conformers = (r.result || {}).conformers || [];
    PG.lastThreeSmiles = smiles;
    PG.confIndex = 0;
    if (!PG.conformers.length) {
      empty.textContent = "RDKit produced no conformer for this structure.";
      return;
    }
    empty.style.display = "none";
    render3DBar(r.result);
    show3D(0);
  } catch (err) {
    empty.textContent = "3D unavailable: " + err.message;
  }
}

function render3DBar(result) {
  const c = PG.conformers;
  document.getElementById("pg3dBar").innerHTML =
    '<div class="seg" id="pgStyleSeg">' +
    '<button data-st="ballstick" class="on">Ball &amp; stick</button>' +
    '<button data-st="spheres">Spheres</button>' +
    '<button data-st="wire">Wireframe</button>' +
    "</div>" +
    '<button class="btn ghost sm" id="pgSpin">Spin</button>' +
    '<button class="btn ghost sm" id="pgLbl">Labels</button>' +
    '<button class="btn ghost sm" id="pgSurf">Surface</button>' +
    '<button class="btn ghost sm" id="pgReset">Recentre</button>' +
    '<span class="pg-conf">' +
    '<button class="btn ghost sm" id="pgPrev">&#8249;</button>' +
    '<span id="pgConfLbl">1 / ' + c.length + "</span>" +
    '<button class="btn ghost sm" id="pgNext">&#8250;</button></span>' +
    '<span class="pg-energy" id="pgEnergy"></span>';

  document.querySelectorAll("[data-st]").forEach(function (b) {
    b.onclick = function () {
      PG.style = b.dataset.st;
      document.querySelectorAll("[data-st]").forEach(function (x) {
        x.className = x.dataset.st === PG.style ? "on" : ""; });
      applyStyle();
    };
  });
  document.getElementById("pgSpin").onclick = function () {
    PG.spinning = !PG.spinning;
    this.className = "btn ghost sm" + (PG.spinning ? " on" : "");
    if (PG.viewer) { try { PG.viewer.spin(PG.spinning ? "y" : false); } catch (e) {} }
  };
  document.getElementById("pgLbl").onclick = function () {
    PG.labels = !PG.labels;
    this.className = "btn ghost sm" + (PG.labels ? " on" : "");
    applyStyle();
  };
  document.getElementById("pgSurf").onclick = function () {
    PG.surface = PG.surface ? null : "vdw";
    this.className = "btn ghost sm" + (PG.surface ? " on" : "");
    applyStyle();
  };
  document.getElementById("pgReset").onclick = function () {
    if (PG.viewer) { try { PG.viewer.zoomTo(); PG.viewer.render(); } catch (e) {} }
  };
  document.getElementById("pgPrev").onclick = function () {
    show3D((PG.confIndex - 1 + PG.conformers.length) % PG.conformers.length);
  };
  document.getElementById("pgNext").onclick = function () {
    show3D((PG.confIndex + 1) % PG.conformers.length);
  };
}

function show3D(i) {
  const host = document.getElementById("pgViewer");
  if (!host || !window.$3Dmol || !PG.conformers.length) return;
  PG.confIndex = i;
  const conf = PG.conformers[i];

  if (!PG.viewer) {
    host.innerHTML = "";
    try {
      PG.viewer = window.$3Dmol.createViewer(host, { backgroundColor: "0x0d1015" });
    } catch (err) {
      document.getElementById("pg3dEmpty").textContent = "3D viewer failed: " + err.message;
      document.getElementById("pg3dEmpty").style.display = "";
      return;
    }
  }
  PG.viewer.clear();
  PG.viewer.addModel(conf.molblock, "mol");
  applyStyle(true);

  const lbl = document.getElementById("pgConfLbl");
  if (lbl) lbl.textContent = (i + 1) + " / " + PG.conformers.length;
  const en = document.getElementById("pgEnergy");
  if (en) {
    const e = conf.energy_kcal_per_mol, rel = conf.relative_energy_kcal_per_mol;
    en.innerHTML = (e === null || e === undefined ? "no energy"
      : "MMFF94 " + Number(e).toFixed(2) + " kcal/mol" +
        (rel !== null && rel !== undefined ? " (+" + Number(rel).toFixed(2) + ")" : ""));
  }
}

function applyStyle(zoom) {
  if (!PG.viewer) return;
  const style = {};
  if (PG.style === "ballstick") {
    style.stick = { radius: 0.13, colorscheme: "default" };
    style.sphere = { scale: 0.25 };
  } else if (PG.style === "spheres") {
    style.sphere = { scale: 0.85 };
  } else {
    style.line = { linewidth: 2.4 };
  }
  PG.viewer.setStyle({}, style);

  try { PG.viewer.removeAllSurfaces(); } catch (e) { /* none to remove */ }
  if (PG.surface) {
    try {
      PG.viewer.addSurface(window.$3Dmol.SurfaceType.VDW,
                           { opacity: 0.62, color: "0x4db6a4" });
    } catch (e) { /* surface generation can fail on odd geometries */ }
  }

  try { PG.viewer.removeAllLabels(); } catch (e) { /* none to remove */ }
  if (PG.labels) {
    try {
      PG.viewer.getModel().selectedAtoms({}).forEach(function (a) {
        PG.viewer.addLabel(a.elem, {
          position: { x: a.x, y: a.y, z: a.z }, fontSize: 10,
          backgroundOpacity: 0.55, backgroundColor: "0x12161d", fontColor: "0xe3e8ef",
        });
      });
    } catch (e) { /* labels are optional */ }
  }

  // Clicking an atom reports what RDKit actually placed there.
  try {
  PG.viewer.setClickable({}, true, function (atom) {
    document.getElementById("pgSel").innerHTML =
      '<table class="kv">' +
      "<tr><th>Element</th><td>" + esc(atom.elem || "?") + "</td></tr>" +
      "<tr><th>Atom index</th><td>" + esc(String(atom.serial)) + "</td></tr>" +
      "<tr><th>Bonds</th><td>" + esc(String((atom.bonds || []).length)) + "</td></tr>" +
      "<tr><th>x</th><td>" + Number(atom.x).toFixed(3) + " &#8491;</td></tr>" +
      "<tr><th>y</th><td>" + Number(atom.y).toFixed(3) + " &#8491;</td></tr>" +
      "<tr><th>z</th><td>" + Number(atom.z).toFixed(3) + " &#8491;</td></tr></table>" +
      '<p class="sub">Coordinates are from the RDKit-generated conformer ' +
      (PG.confIndex + 1) + " of " + PG.conformers.length + ". They are computed, not measured.</p>";
  });
  } catch (e) { /* click reporting is optional; the model still renders */ }

  if (zoom) PG.viewer.zoomTo();
  PG.viewer.render();
  if (PG.spinning) { try { PG.viewer.spin("y"); } catch (e) {} }
}

// --------------------------------------------------------------------- right rail

function renderIdentity() {
  const c = PG.current, v = c.validation;
  document.getElementById("pgIdent").innerHTML =
    "<p>" + chip("experimental", "RDKit validated") + "</p>" +
    '<table class="kv">' +
    "<tr><th>Formula</th><td><strong>" + esc(c.descriptors.molecular_formula || "-") +
    "</strong></td></tr>" +
    "<tr><th>Heavy atoms</th><td>" + esc(String(v.atom_count)) + "</td></tr>" +
    "<tr><th>Canonical</th><td><span class=\"mono wrap\">" + esc(v.canonical_smiles || "") +
    "</span></td></tr>" +
    "<tr><th>Standardized</th><td><span class=\"mono wrap\">" +
    esc(v.standardized_smiles || "") + "</span></td></tr>" +
    "<tr><th>InChIKey</th><td><span class=\"mono\">" + esc(v.inchikey || "-") + "</span></td></tr>" +
    "</table>" +
    (v.changed_by_standardization
      ? '<p class="note caution"><strong>Standardization changed this structure.</strong> Both ' +
        "forms are above; the original is kept.</p>"
      : '<p class="sub">Standardization did not change this structure.</p>') +
    (c.stereo && c.stereo.assigned_stereocentres !== undefined
      ? '<p class="sub">Stereocentres: ' + esc(String(c.stereo.assigned_stereocentres)) +
        " assigned" + (c.stereo.unassigned_stereocentres
          ? ", " + esc(String(c.stereo.unassigned_stereocentres)) + " unassigned" : "") +
        ". Stereochemistry is preserved, never normalised away.</p>" : "");
}

function renderProps() {
  const d = PG.current.descriptors;
  const KEYS = [["molecular_weight", "Molecular weight"], ["clogp", "cLogP"], ["tpsa", "TPSA"],
                ["h_bond_donors", "H-bond donors"], ["h_bond_acceptors", "H-bond acceptors"],
                ["rotatable_bonds", "Rotatable bonds"], ["ring_count", "Rings"],
                ["aromatic_ring_count", "Aromatic rings"],
                ["heavy_atom_count", "Heavy atoms"], ["fraction_csp3", "Fraction Csp3"],
                ["exact_mass", "Exact mass"], ["qed", "QED"]];
  document.getElementById("pgProps").innerHTML =
    "<p>" + chip("computed", "Computed by RDKit") + "</p>" +
    '<table class="kv">' + KEYS.map(function (k) {
      const v = d[k[0]];
      return "<tr><th>" + esc(k[1]) + "</th><td>" +
        (v === undefined || v === null ? chip("missing", "not computed") : esc(String(v))) +
        "</td></tr>"; }).join("") + "</table>" +
    '<p class="sub">Every value is calculated from the structure. None is a measurement, and ' +
    "none predicts biological activity.</p>" +
    '<p class="sub">RDKit ' + esc(PG.current.versions.rdkit || "-") + " - fp " +
    '<span class="mono">' + esc(PG.current.fingerprint.slice(0, 12)) + "</span></p>";
}

function renderCalcOptions() {
  const smi = PG.current.smiles;
  document.getElementById("pgCalc").innerHTML =
    '<div class="pgrid">' +
    '<button class="chipb" data-run="lipinski">Lipinski</button>' +
    '<button class="chipb" data-run="scaffold">Scaffold</button>' +
    '<button class="chipb" data-run="molecular_dynamics">Dynamics</button>' +
    '<button class="chipb" data-run="solvent_effect">Solvent</button>' +
    "</div><div id=\"pgCalcOut\"></div>" +
    '<p class="sub">Docking and QSAR remain deliberately unavailable; the Bench\'s Calculate ' +
    "stage lists why, and offers experimental protein-ligand structures instead.</p>";
  document.querySelectorAll("[data-run]").forEach(function (b) {
    b.onclick = function () { pgRunCalc(b.dataset.run, smi); };
  });
}

async function pgRunCalc(id, smiles) {
  const out = document.getElementById("pgCalcOut");
  out.innerHTML = '<p class="sub">Running' +
    (id === "molecular_dynamics" ? " dynamics - integrating the trajectory" : "") + "...</p>";
  try {
    const body = (id === "molecular_dynamics")
      ? { smiles: smiles, steps: 800, temperature_k: 310, timestep_fs: 1.0,
          solvent: "implicit_water" }
      : { smiles: smiles };
    const r = await api("/api/v1/bench/calculations/" + id + "/run", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body) });
    if (!r.succeeded) { out.innerHTML = '<p class="limit">' + esc(r.error) + "</p>"; return; }
    if (id === "molecular_dynamics") { renderDynamics(r); return; }
    if (id === "solvent_effect") { renderSolvent(r); return; }
    if (id === "lipinski") {
      out.innerHTML = '<table class="kv">' + r.result.criteria.map(function (c) {
        return "<tr><th>" + esc(c.criterion.replace(/ <=.*/, "")) + "</th><td>" +
          (c.satisfied ? chip("experimental", "met") : chip("missing", "not met")) + " " +
          esc(String(c.value)) + "</td></tr>"; }).join("") + "</table>" +
        '<p class="sub">' + esc(r.result.interpretation) + "</p>";
      return;
    }
    out.innerHTML = '<p><span class="mono wrap">' +
      esc(r.result.murcko_scaffold || "(no ring system)") + "</span></p>" +
      '<p class="sub">' + esc(r.result.interpretation) + "</p>";
  } catch (err) { out.innerHTML = '<p class="limit">' + esc(err.message) + "</p>"; }
}

// --------------------------------------------------------------------- versions / diff

function pgSaveVersion() {
  if (!PG.current) { pgStatus("Nothing valid to save yet.", "warn"); return; }
  const n = PG.history.length + 1;
  PG.history.unshift({
    label: "v" + n,
    smiles: PG.current.validation.canonical_smiles || PG.current.smiles,
    formula: PG.current.descriptors.molecular_formula,
    weight: PG.current.descriptors.molecular_weight,
    at: new Date().toISOString().slice(11, 19),
  });
  if (!PG.original) PG.original = { label: "v1", smiles: PG.history[0].smiles };
  renderHistory();
  renderDiff();
  pgStatus("Saved " + PG.history[0].label + ".", "ok");
}

function renderHistory() {
  document.getElementById("pgHist").innerHTML = PG.history.length
    ? PG.history.map(function (h) {
        return '<button class="pick" data-hist="' + esc(h.smiles) + '"><strong>' + esc(h.label) +
          " - " + esc(h.formula || "") + '</strong><span class="meta">MW ' +
          esc(String(h.weight || "-")) + " - " + esc(h.at) + "</span></button>"; }).join("") +
      '<button class="btn ghost" id="pgPersist" style="width:100%">Add to the investigation' +
      "</button>"
    : '<p class="sub">Save a version to compare against it.</p>';
  document.querySelectorAll("[data-hist]").forEach(function (b) {
    b.onclick = function () { pgSetStructure(b.dataset.hist, "version"); };
  });
  const p = document.getElementById("pgPersist");
  if (p) p.onclick = pgPersistToSession;
}

function renderDiff() {
  const host = document.getElementById("pgDiff");
  if (!PG.original || !PG.current) {
    host.innerHTML = '<p class="sub">Import a compound or save a version to compare.</p>';
    return;
  }
  const cur = PG.current.validation.canonical_smiles || PG.current.smiles;
  const same = PG.original.smiles === cur;
  host.innerHTML =
    '<table class="kv"><tr><th>Original</th><td><span class="mono wrap">' +
    esc(PG.original.smiles) + "</span></td></tr>" +
    "<tr><th>Current</th><td><span class=\"mono wrap\">" + esc(cur) + "</span></td></tr></table>" +
    (same ? '<p class="sub">Unchanged from the original.</p>'
          : '<button class="btn primary" id="pgCmp" style="width:100%">Compare</button>') +
    '<div id="pgCmpOut"></div>' +
    '<p class="sub">The original record is never modified.</p>';
  const b = document.getElementById("pgCmp");
  if (b) b.onclick = pgCompare;
}

async function pgCompare() {
  const out = document.getElementById("pgCmpOut");
  out.innerHTML = '<p class="sub">Comparing...</p>';
  const cur = PG.current.validation.canonical_smiles || PG.current.smiles;
  try {
    const d = await api("/api/v1/bench/structures/compare", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ structures: [
        { label: PG.original.label || "original", smiles: PG.original.smiles },
        { label: "modified", smiles: cur }] }),
    });
    const p = d.pairwise_similarity[0] || {};
    out.innerHTML =
      '<table class="kv"><tr><th>Tanimoto</th><td>' + measure(p.tanimoto, "", 3) + "</td></tr>" +
      "<tr><th>Same scaffold</th><td>" + (p.shares_scaffold ? "yes" : "no") + "</td></tr></table>" +
      '<table class="dtable"><thead><tr><th>Descriptor</th><th>orig</th><th>mod</th>' +
      '<th class="num">&#916;</th></tr></thead><tbody>' +
      d.descriptor_differences.filter(function (r) { return r.differs; }).map(function (r) {
        return "<tr><td>" + esc(r.descriptor.replace(/_/g, " ")) + "</td><td>" +
          esc(String(r.values[0])) + "</td><td>" + esc(String(r.values[1])) +
          '</td><td class="num">' + (r.delta === null ? "-" : esc(String(r.delta))) +
          "</td></tr>"; }).join("") + "</tbody></table>" +
      '<p class="sub">' + esc(d.limitations[0]) + "</p>";
    if (typeof BENCH === "object") {
      BENCH.candidates = [
        { label: PG.original.label || "original", smiles: PG.original.smiles },
        { label: "modified", smiles: cur }];
    }
  } catch (err) { out.innerHTML = '<p class="limit">' + esc(err.message) + "</p>"; }
}

async function pgPersistToSession() {
  if (!PG.sessionId) {
    pgStatus("Open the Playground from an investigation to save into it.", "warn");
    return;
  }
  if (!PG.current) { pgStatus("Nothing valid to save.", "warn"); return; }
  const b = document.getElementById("pgPersist");
  b.disabled = true;
  const d = PG.current.descriptors;
  const body =
    "Researcher-constructed structure.\n\n" +
    "SMILES (canonical): " + (PG.current.validation.canonical_smiles || PG.current.smiles) + "\n" +
    "InChIKey: " + (PG.current.validation.inchikey || "-") + "\n" +
    "Formula: " + (d.molecular_formula || "-") + "\n" +
    "Molecular weight: " + (d.molecular_weight || "-") + "\n" +
    "RDKit: " + (PG.current.versions.rdkit || "-") + "\n" +
    "Descriptor fingerprint: " + PG.current.fingerprint + "\n\n" +
    (PG.original ? "Derived from: " + PG.original.smiles + "\n" : "") +
    "Drawn by the researcher in the Molecular Playground. NOT an experimentally characterised " +
    "compound; it carries no measured data.";
  try {
    await api("/api/workspaces/" + PG.sessionId + "/notes", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ category: "method", body: body }) });
    pgStatus("Saved into investigation #" + PG.sessionId + ".", "ok");
    b.textContent = "Added to the investigation";
  } catch (err) {
    b.disabled = false;
    pgStatus("Could not save: " + err.message, "bad");
  }
}

// --------------------------------------------------------------------- import

async function pgPickCompound() {
  const out = document.getElementById("pgPickOut");
  out.innerHTML = '<div class="row" style="margin-top:6px">' +
    '<input id="pgQ" placeholder="compound name" style="flex:1">' +
    '<button class="btn" id="pgQGo">find</button></div><div id="pgQOut"></div>';
  const go = async function () {
    const q = document.getElementById("pgQ").value.trim();
    const o = document.getElementById("pgQOut");
    if (q.length < 2) { o.innerHTML = '<p class="sub">Two characters minimum.</p>'; return; }
    o.innerHTML = '<p class="sub">Searching...</p>';
    try {
      const d = await api("/api/v1/bench/search?q=" + encodeURIComponent(q) + "&kinds=compound");
      const hits = (d.results || []).filter(function (r) { return r.has_structure; });
      o.innerHTML = hits.length
        ? hits.map(function (r) {
            return '<button class="pick" data-imp="' + esc(r.smiles) + '" data-lab="' +
              esc(r.label) + '"><strong>' + esc(r.label) + '</strong><span class="meta">' +
              esc(r.detail || "") + "</span></button>"; }).join("")
        : '<p class="sub">No curated compound with a recorded structure matched.</p>';
      o.querySelectorAll("[data-imp]").forEach(function (b) {
        b.onclick = function () {
          PG.original = { label: b.dataset.lab, smiles: b.dataset.imp };
          pgSetStructure(b.dataset.imp, b.dataset.lab);
          pgStatus("Imported " + b.dataset.lab + " - the original record is unchanged.", "ok");
        };
      });
    } catch (err) { o.innerHTML = '<p class="limit">' + esc(err.message) + "</p>"; }
  };
  document.getElementById("pgQGo").onclick = go;
  document.getElementById("pgQ").onkeydown = function (e) { if (e.key === "Enter") go(); };
}

// --------------------------------------------------------------------- dynamics and solvent

// Real molecular dynamics. The trajectory frames are pushed into the same 3D panel the
// conformers use, so the molecule visibly moves rather than sitting still.
function renderDynamics(r) {
  const res = r.result || {};
  const s = res.summary || {};
  const p = res.parameters || {};
  const trace = res.trace || [];

  document.getElementById("pgCalcOut").innerHTML =
    '<table class="kv">' +
    "<tr><th>Simulated time</th><td>" + esc(String(p.simulated_time_ps)) + " ps</td></tr>" +
    "<tr><th>Mean temperature</th><td>" + measure(s.mean_temperature_k, " K", 1) +
    " (target " + esc(String(s.target_temperature_k)) + " K)</td></tr>" +
    "<tr><th>Environment</th><td>" + esc(p.solvent_label || "") + "</td></tr>" +
    "<tr><th>Moved from start</th><td>" +
    measure(s.final_rmsd_from_start_angstrom, " Å", 2) + "</td></tr>" +
    "<tr><th>Frames</th><td>" + esc(String(s.frames)) + "</td></tr></table>" +
    '<div class="row gap" style="margin:6px 0">' +
    '<button class="btn" id="pgPlay">Play trajectory</button>' +
    '<span class="sub" id="pgFrameLbl"></span></div>' +
    '<p class="note caution"><strong>No water molecules are present.</strong> ' +
    esc(res.solvent_note || "") + "</p>" +
    '<p class="sub">' + esc((res.limitations || [])[1] || "") + "</p>";

  PG.conformers = (res.trajectory_frames || []).map(function (mb, i) {
    const t = trace[i] || {};
    return { molblock: mb,
             energy_kcal_per_mol: t.potential_energy_kcal_per_mol,
             relative_energy_kcal_per_mol: t.temperature_k,
             rmsd_to_lowest_angstrom: t.rmsd_from_start_angstrom };
  });
  PG.lastThreeSmiles = null;   // the viewer holds a trajectory now, not the conformer set

  if (PG.conformers.length) {
    const empty = document.getElementById("pg3dEmpty");
    if (empty) empty.style.display = "none";
    render3DBar(res);
    show3D(0);
  }

  const btn = document.getElementById("pgPlay");
  if (btn) {
    btn.onclick = function () {
      if (PG.playing) {
        clearInterval(PG.playing);
        PG.playing = null;
        btn.textContent = "Play trajectory";
        return;
      }
      btn.textContent = "Stop";
      let i = 0;
      PG.playing = setInterval(function () {
        if (!PG.conformers.length) {
          clearInterval(PG.playing);
          PG.playing = null;
          return;
        }
        i = (i + 1) % PG.conformers.length;
        show3D(i);
        const lbl = document.getElementById("pgFrameLbl");
        const t = trace[i] || {};
        if (lbl) {
          lbl.textContent = "t = " + (t.time_fs || 0) + " fs, " +
            Math.round(t.temperature_k || 0) + " K";
        }
      }, 160);
    };
  }
}

function renderSolvent(r) {
  const models = (r.result || {}).models || [];
  document.getElementById("pgCalcOut").innerHTML =
    '<table class="dtable"><thead><tr><th>Environment</th><th class="num">Energy</th>' +
    '<th class="num">Shape change</th><th class="num">Surface</th></tr></thead><tbody>' +
    models.map(function (m) {
      return "<tr><td>" + esc(String(m.label).replace(/ \(.*/, "")) + '</td><td class="num">' +
        measure(m.optimised_energy_kcal_per_mol, "", 1) + '</td><td class="num">' +
        (m.rmsd_vs_vacuum_angstrom === null || m.rmsd_vs_vacuum_angstrom === undefined
          ? "-" : measure(m.rmsd_vs_vacuum_angstrom, " Å", 2)) +
        '</td><td class="num">' +
        measure(m.solvent_accessible_surface_area_A2, " Å²", 0) + "</td></tr>";
    }).join("") + "</tbody></table>" +
    '<p class="note caution">A dielectric constant is not a solvent. There are no water ' +
    "molecules here, and energies from different models are minima of different potentials, so " +
    "they cannot be compared or subtracted. What is meaningful is the shape change.</p>";
}
