// MOLECULAB Dosing Lab — give a dose, to a species, for a period, and watch the concentration.
//
// The curve is produced by integrating real compartmental pharmacokinetics on the backend. What
// this page is careful about is the input side: clearance, volume, absorption and bioavailability
// are MEASURED OR LITERATURE VALUES the researcher supplies. MOLECULAB cannot derive them from a
// structure, so the page never pretends it did, and the reference drugs are labelled as published
// population means rather than as anything MOLECULAB measured.
//
// There is deliberately no "what the animal looks like afterwards". No method exists that would
// make such a picture honest. What the body actually experiences is the concentration curve, and
// that is what is shown.
"use strict";

// tile() escapes what it is given, so numbers must arrive as plain text, not as measure() HTML.
function fmtNum(v, digits) {
  if (v === null || v === undefined) return "—";
  return typeof v === "number" ? v.toFixed(digits) : String(v);
}

const DOSE = {
  species: null,
  drugs: null,
  last: null,
  scenarios: [],
  playTimer: null,
};

async function dosingPage(params) {
  const main = document.getElementById("view");
  main.classList.add("workstation");

  main.innerHTML =
    '<div class="wx-head"><div><div class="eyebrow">Dosing Lab</div>' +
    "<h1>What a dose does over time</h1></div>" +
    '<div class="wx-sub">Real compartmental pharmacokinetics. The parameters are yours; the ' +
    "integration is ours.</div></div>" +

    '<div class="wx-body wx-2">' +

    // ---------------- controls
    '<div class="wx-col">' +
    '<div class="wx-sec"><h4>Compound</h4><div class="in">' +
    '<div class="field"><label for="dsDrug">Start from a known drug</label>' +
    '<select id="dsDrug"><option value="">(enter parameters myself)</option></select>' +
    '<span class="sub" id="dsDrugNote">Published population means, not measured by ' +
    "MOLECULAB.</span></div></div></div>" +

    '<div class="wx-sec"><h4>Dose</h4><div class="in">' +
    '<div class="field"><label for="dsDose">Dose (mg)</label>' +
    '<input id="dsDose" type="number" value="100" min="0.001" step="any"></div>' +
    '<div class="field"><label for="dsFreq">How often</label><select id="dsFreq">' +
    '<option value="0">Once only</option>' +
    '<option value="4">Every 4 hours</option>' +
    '<option value="6">Every 6 hours</option>' +
    '<option value="8" selected>Every 8 hours</option>' +
    '<option value="12">Every 12 hours</option>' +
    '<option value="24">Once a day</option>' +
    '<option value="48">Every 2 days</option>' +
    '<option value="168">Once a week</option>' +
    "</select></div>" +
    '<div class="field"><label for="dsDur">For how long</label><select id="dsDur">' +
    '<option value="24">1 day</option>' +
    '<option value="72">3 days</option>' +
    '<option value="168" selected>1 week</option>' +
    '<option value="720">1 month</option>' +
    '<option value="2160">3 months</option>' +
    '<option value="4320">6 months</option>' +
    "</select></div></div></div>" +

    '<div class="wx-sec"><h4>Subject</h4><div class="in">' +
    '<div class="field"><label for="dsSpecies">Species</label>' +
    '<select id="dsSpecies"></select>' +
    '<span class="sub" id="dsSpeciesNote"></span></div>' +
    '<label class="row"><input type="checkbox" id="dsScale" checked> ' +
    "Scale parameters allometrically from human</label>" +
    '<p class="sub">Clearance scales with weight^0.75 and volume with weight^1.0. This is a ' +
    "standard approximation, not a measurement.</p></div></div>" +

    '<div class="wx-sec"><h4>Pharmacokinetic parameters</h4><div class="in">' +
    '<p class="sub"><strong>These are inputs.</strong> MOLECULAB cannot derive them from a ' +
    "structure. Use measured or literature values.</p>" +
    '<div class="field"><label for="dsCl">Clearance (L/h)</label>' +
    '<input id="dsCl" type="number" value="6.0" step="any" min="0.0001"></div>' +
    '<div class="field"><label for="dsVd">Volume of distribution (L)</label>' +
    '<input id="dsVd" type="number" value="36.0" step="any" min="0.0001"></div>' +
    '<div class="field"><label for="dsKa">Absorption rate (per hour)</label>' +
    '<input id="dsKa" type="number" value="1.5" step="any" min="0.0001"></div>' +
    '<div class="field"><label for="dsF">Bioavailability (0-1)</label>' +
    '<input id="dsF" type="number" value="1.0" step="0.01" min="0.001" max="1"></div>' +
    "</div></div>" +

    '<div class="wx-sec"><h4>Molecule (optional)</h4><div class="in">' +
    '<div class="field"><label for="dsSmi">SMILES</label><div class="row">' +
    '<input id="dsSmi" placeholder="CN1C=NC2=C1C(=O)N(C)C(=O)N2C" style="flex:1">' +
    '<button class="btn" id="dsMolGo">show</button></div>' +
    '<span class="sub">Shows the compound in 3D beside its curve. The structure does not ' +
    "affect the simulation: PK parameters are the inputs.</span></div>" +
    '<div id="dsMol3d"></div></div></div>' +

    '<div class="wx-sec"><div class="in">' +
    '<button class="btn primary" id="dsRun" style="width:100%">Simulate</button>' +
    '<button class="btn" id="dsSurf3d" style="width:100%;margin-top:6px">' +
    "3D dose surface</button>" +
    '<button class="btn" id="dsAdd" style="width:100%;margin-top:6px">Add to comparison</button>' +
    '<button class="btn ghost" id="dsSpecies5" style="width:100%;margin-top:6px">' +
    "Compare all species</button>" +
    "</div></div></div>" +

    // ---------------- stage
    '<div class="wx-main scrolls" id="dsOut">' +
    emptyState("Set a dose and simulate",
      "The concentration curve is integrated from the parameters on the left. Nothing is " +
      "predicted from the molecule itself.") +
    "</div></div>";

  try {
    const t = await api("/api/v1/bench/dosing/options");
    DOSE.species = t.species;
    DOSE.drugs = t.reference_drugs;
    document.getElementById("dsSpecies").innerHTML = t.species.map(function (s) {
      return '<option value="' + esc(s.id) + '"' + (s.id === "human" ? " selected" : "") + ">" +
        esc(s.label) + " - " + esc(String(s.weight_kg)) + " kg</option>"; }).join("");
    document.getElementById("dsDrug").innerHTML +=
      t.reference_drugs.map(function (d) {
        return '<option value="' + esc(d.id) + '">' + esc(d.label) + "</option>"; }).join("");
    updateSpeciesNote();
  } catch (err) {
    document.getElementById("dsOut").innerHTML =
      errorState("Dosing options unavailable", err.message);
    return;
  }

  document.getElementById("dsDrug").onchange = function () {
    const d = (DOSE.drugs || []).find(function (x) { return x.id === this.value; }, this);
    const hit = (DOSE.drugs || []).filter(function (x) { return x.id === document.getElementById("dsDrug").value; })[0];
    if (!hit) return;
    document.getElementById("dsCl").value = hit.cl_l_per_h;
    document.getElementById("dsVd").value = hit.vd_l;
    document.getElementById("dsKa").value = hit.ka_per_h;
    document.getElementById("dsF").value = hit.f;
    document.getElementById("dsDrugNote").textContent =
      hit.label + ": " + hit.source + ". Not measured by MOLECULAB.";
    if (hit.smiles) {
      document.getElementById("dsSmi").value = hit.smiles;
      showDoseMolecule(hit.smiles);
    }
  };
  document.getElementById("dsSpecies").onchange = updateSpeciesNote;
  document.getElementById("dsRun").onclick = runDosing;
  document.getElementById("dsSurf3d").onclick = function () {
    const out = document.getElementById("dsOut");
    out.innerHTML = panel("Dose surface",
      '<p class="sub">Every ridge is a separate integration at that dose level. Nothing ' +
      "between levels is interpolated.</p><div id=\"dsSurfOut\"></div>",
      chip("computed", "Parameter sweep"), "scroll");
    runDoseSurface();
  };
  document.getElementById("dsMolGo").onclick = function () {
    const s = document.getElementById("dsSmi").value.trim();
    if (s) showDoseMolecule(s);
  };
  document.getElementById("dsAdd").onclick = addScenario;
  document.getElementById("dsSpecies5").onclick = compareSpecies;
}

function updateSpeciesNote() {
  const id = document.getElementById("dsSpecies").value;
  const s = (DOSE.species || []).filter(function (x) { return x.id === id; })[0];
  const note = document.getElementById("dsSpeciesNote");
  if (s && note) note.textContent = s.note;
}

function dosingBody() {
  return {
    dose_mg: +document.getElementById("dsDose").value,
    interval_h: +document.getElementById("dsFreq").value,
    duration_h: +document.getElementById("dsDur").value,
    clearance_l_per_h: +document.getElementById("dsCl").value,
    volume_l: +document.getElementById("dsVd").value,
    ka_per_h: +document.getElementById("dsKa").value,
    bioavailability: +document.getElementById("dsF").value,
    species: document.getElementById("dsSpecies").value,
    scale_from_human: document.getElementById("dsScale").checked,
  };
}

async function runDosing() {
  const out = document.getElementById("dsOut");
  out.innerHTML = '<div class="state"><span class="g">&#8987;</span><h4>Integrating…</h4>' +
    "<p>Solving the compartmental equations over the dosing period.</p></div>";
  try {
    const r = await api("/api/v1/bench/calculations/pharmacokinetics/run", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(dosingBody()),
    });
    if (!r.succeeded) { out.innerHTML = errorState("Could not simulate", r.error); return; }
    DOSE.last = r.result;
    renderDosing(r.result);
  } catch (err) { out.innerHTML = errorState("Could not simulate", err.message); }
}

function renderDosing(d) {
  const m = d.metrics;
  const p = d.parameters_used;
  document.getElementById("dsOut").innerHTML =
    // tile() escapes its value, so pass plain text and keep the unit in the footer chip.
    '<div class="tiles" style="margin-bottom:12px">' +
    tile("Peak concentration", fmtNum(m.cmax_mg_per_l, 3) + " mg/L",
         chip("computed", "Cmax")) +
    tile("Time to peak", fmtNum(m.tmax_h, 2) + " h", chip("computed", "Tmax")) +
    tile("Total exposure", fmtNum(m.auc_mg_h_per_l, 1), chip("computed", "AUC mg·h/L")) +
    tile("Half-life", fmtNum(m.terminal_half_life_h, 2) + " h",
         chip("computed", "Terminal")) +
    "</div>" +

    panel("Concentration over time",
      '<div class="dose-chart" id="dsChart"></div>' +
      '<div class="row gap" style="margin-top:8px">' +
      '<button class="btn" id="dsPlay">Play</button>' +
      '<span class="sub" id="dsPlayLbl"></span></div>' +
      '<p class="sub">' + esc(String(d.doses.count)) + " dose(s) of " +
      esc(String(d.doses.dose_mg)) + " mg" +
      (d.doses.interval_h ? " every " + esc(String(d.doses.interval_h)) + " h" : "") +
      " in " + esc(p.species_label) + " (" + esc(String(p.body_weight_kg)) + " kg), " +
      esc(p.model) + " model.</p>",
      chip("computed", "Computed"), "scroll") +

    (m.accumulation_ratio ? panel("Repeat dosing",
      '<table class="kv">' +
      "<tr><th>Accumulation ratio</th><td>" + measure(m.accumulation_ratio, "x", 2) +
      "</td></tr>" +
      "<tr><th>Time to ~90% steady state</th><td>" +
      measure(m.time_to_90pc_steady_state_h, " h", 1) + "</td></tr>" +
      (m.troughs.length ? "<tr><th>First trough</th><td>" +
        measure(m.troughs[0].trough_mg_per_l, " mg/L", 3) + "</td></tr>" +
        "<tr><th>Last trough</th><td>" +
        measure(m.troughs[m.troughs.length - 1].trough_mg_per_l, " mg/L", 3) + "</td></tr>" : "") +
      "</table>" +
      '<p class="sub">Accumulation is a consequence of dosing faster than the drug is cleared. ' +
      "It follows from the half-life and the interval, both shown above.</p>") : "") +

    (d.allometric_scaling ? panel("Allometric scaling",
      '<table class="kv">' +
      "<tr><th>Scaled from</th><td>human (70 kg)</td></tr>" +
      "<tr><th>Weight ratio</th><td>" + measure(d.allometric_scaling.weight_ratio, "", 4) +
      "</td></tr>" +
      "<tr><th>Clearance used</th><td>" + measure(p.clearance_l_per_h, " L/h", 4) + "</td></tr>" +
      "<tr><th>Volume used</th><td>" + measure(p.volume_l, " L", 3) + "</td></tr></table>" +
      '<p class="note caution">Allometric scaling assumes the same elimination pathway scales ' +
      "with body size. It fails for species-specific metabolism, and it is an approximation, " +
      "not a measurement.</p>", chip("derived", "Scaled")) : "") +

    panel("What this does not tell you",
      '<p class="note caution"><strong>A concentration is not an effect.</strong> This says ' +
      "nothing about whether the dose works or whether it is safe.</p>" +
      "<h5>Not simulated</h5>" + limitations(d.not_simulated) +
      "<h5>Limitations</h5>" + limitations(d.limitations) +
      '<table class="kv" style="margin-top:8px">' +
      "<tr><th>Method</th><td>" + esc(d.method) + "</td></tr>" +
      "<tr><th>Integration</th><td>" + esc(p.integration.method) + ", step " +
      esc(String(p.integration.step_h)) + " h, " + esc(String(p.integration.steps)) +
      " steps</td></tr></table>", chip("missing", "Boundaries"));

  drawDoseChart("dsChart", [{ label: "concentration", series: d.series }], d.doses.times_h);
  wireDosePlayback(d);
}

// A dependency-free SVG plot. Multiple series overlay for comparison.
function drawDoseChart(hostId, sets, doseTimes, upto) {
  const host = document.getElementById(hostId);
  if (!host) return;
  const W = 820, H = 300, L = 58, R = 16, T = 14, B = 34;
  const all = sets.reduce(function (a, s) { return a.concat(s.series); }, []);
  if (!all.length) { host.innerHTML = '<p class="sub">No data.</p>'; return; }
  const maxT = Math.max.apply(null, all.map(function (p) { return p.time_h; })) || 1;
  const maxC = Math.max.apply(null, all.map(function (p) {
    return p.concentration_mg_per_l; })) || 1;
  const x = function (t) { return L + (t / maxT) * (W - L - R); };
  const y = function (c) { return H - B - (c / maxC) * (H - T - B); };
  const COLOURS = ["#4db6a4", "#6f9fe0", "#d4a95a", "#b98ae0", "#d9544d"];

  let svg = '<svg viewBox="0 0 ' + W + " " + H + '" class="dchart" ' +
    'preserveAspectRatio="xMidYMid meet">';
  // grid
  for (let i = 0; i <= 4; i++) {
    const gy = T + (i / 4) * (H - T - B);
    svg += '<line x1="' + L + '" y1="' + gy + '" x2="' + (W - R) + '" y2="' + gy +
      '" class="dgrid"/>' +
      '<text x="' + (L - 6) + '" y="' + (gy + 4) + '" class="dlab" text-anchor="end">' +
      (maxC * (1 - i / 4)).toFixed(maxC < 1 ? 3 : 2) + "</text>";
  }
  for (let i = 0; i <= 6; i++) {
    const t = (i / 6) * maxT;
    svg += '<text x="' + x(t) + '" y="' + (H - 10) + '" class="dlab" text-anchor="middle">' +
      (maxT > 72 ? (t / 24).toFixed(0) + "d" : t.toFixed(0) + "h") + "</text>";
  }
  // dose markers
  (doseTimes || []).slice(0, 120).forEach(function (t) {
    if (t <= maxT) {
      svg += '<line x1="' + x(t) + '" y1="' + T + '" x2="' + x(t) + '" y2="' + (H - B) +
        '" class="ddose"/>';
    }
  });
  // series
  sets.forEach(function (s, si) {
    const pts = (upto === undefined ? s.series
      : s.series.filter(function (p) { return p.time_h <= upto; }));
    if (pts.length < 2) return;
    const dpath = pts.map(function (p, i) {
      return (i ? "L" : "M") + x(p.time_h).toFixed(1) + " " +
        y(p.concentration_mg_per_l).toFixed(1); }).join(" ");
    svg += '<path d="' + dpath + '" class="dline" stroke="' +
      COLOURS[si % COLOURS.length] + '"/>';
  });
  svg += '<line x1="' + L + '" y1="' + (H - B) + '" x2="' + (W - R) + '" y2="' + (H - B) +
    '" class="daxis"/><line x1="' + L + '" y1="' + T + '" x2="' + L + '" y2="' + (H - B) +
    '" class="daxis"/>';
  svg += '<text x="12" y="' + (H / 2) + '" class="dlab" transform="rotate(-90 12 ' + (H / 2) +
    ')" text-anchor="middle">mg/L</text>';
  svg += "</svg>";

  if (sets.length > 1) {
    svg += '<div class="dlegend">' + sets.map(function (s, i) {
      return '<span><i style="background:' + COLOURS[i % COLOURS.length] + '"></i>' +
        esc(s.label) + "</span>"; }).join("") + "</div>";
  }
  host.innerHTML = svg;
}

function wireDosePlayback(d) {
  const btn = document.getElementById("dsPlay");
  if (!btn) return;
  btn.onclick = function () {
    if (DOSE.playTimer) {
      clearInterval(DOSE.playTimer);
      DOSE.playTimer = null;
      btn.textContent = "Play";
      drawDoseChart("dsChart", [{ label: "concentration", series: d.series }], d.doses.times_h);
      return;
    }
    btn.textContent = "Stop";
    const maxT = d.series[d.series.length - 1].time_h;
    let t = 0;
    DOSE.playTimer = setInterval(function () {
      t += maxT / 90;
      if (t >= maxT) {
        t = maxT;
        clearInterval(DOSE.playTimer);
        DOSE.playTimer = null;
        btn.textContent = "Play";
      }
      drawDoseChart("dsChart", [{ label: "concentration", series: d.series }],
                    d.doses.times_h, t);
      const now = d.series.filter(function (p) { return p.time_h <= t; }).pop();
      const lbl = document.getElementById("dsPlayLbl");
      if (lbl && now) {
        lbl.textContent = (t > 72 ? (t / 24).toFixed(1) + " days" : t.toFixed(1) + " h") +
          " — " + now.concentration_mg_per_l.toFixed(3) + " mg/L";
      }
    }, 40);
  };
}

// --------------------------------------------------------------- comparison

function addScenario() {
  const b = dosingBody();
  const sp = (DOSE.species || []).filter(function (x) { return x.id === b.species; })[0];
  b.label = b.dose_mg + " mg" + (b.interval_h ? "/" + b.interval_h + "h" : " once") +
    " — " + (sp ? sp.label : b.species);
  DOSE.scenarios.push(b);
  if (DOSE.scenarios.length >= 2) runComparison(DOSE.scenarios);
  else {
    document.getElementById("dsOut").insertAdjacentHTML("afterbegin",
      '<p class="note">Added "' + esc(b.label) + '". Add one more to compare.</p>');
  }
}

async function compareSpecies() {
  const base = dosingBody();
  const perKg = base.dose_mg / 70;
  DOSE.scenarios = (DOSE.species || []).map(function (s) {
    return Object.assign({}, base, {
      species: s.id, scale_from_human: true,
      dose_mg: +(perKg * s.weight_kg).toFixed(4),
      label: s.label + " — " + (perKg * s.weight_kg).toFixed(2) + " mg",
    });
  }).slice(0, 4);
  runComparison(DOSE.scenarios);
}

async function runComparison(scenarios) {
  const out = document.getElementById("dsOut");
  out.innerHTML = '<div class="state"><span class="g">&#8987;</span><h4>Comparing…</h4></div>';
  try {
    const d = await api("/api/v1/bench/dosing/compare", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ scenarios: scenarios.slice(0, 4) }),
    });
    out.innerHTML =
      panel("Comparison",
        '<div class="dose-chart" id="dsChart"></div>' +
        '<table class="dtable" style="margin-top:10px"><thead><tr><th>Metric</th>' +
        d.scenarios.map(function (s) { return "<th>" + esc(s.label) + "</th>"; }).join("") +
        '<th class="num">fold</th></tr></thead><tbody>' +
        d.comparison.map(function (row) {
          return "<tr><td>" + esc(row.metric) + "</td>" +
            row.values.map(function (v) {
              return "<td>" + (v === null || v === undefined ? chip("missing", "-")
                : esc(typeof v === "number" ? v.toFixed(3) : String(v))) + "</td>"; }).join("") +
            '<td class="num">' + (row.fold_difference === null ? "-"
              : esc(String(row.fold_difference)) + "x") + "</td></tr>"; }).join("") +
        "</tbody></table>" +
        '<p class="note">' + esc(d.interpretation) + "</p>",
        chip("computed", "Computed"), "scroll") +
      panel("Limitations", limitations(d.limitations)) +
      '<button class="btn ghost" id="dsClear">Clear comparison</button>';

    drawDoseChart("dsChart", d.scenarios.map(function (s) {
      return { label: s.label, series: s.series }; }),
      (d.scenarios[0].doses || {}).times_h);

    document.getElementById("dsClear").onclick = function () {
      DOSE.scenarios = [];
      runDosing();
    };
  } catch (err) { out.innerHTML = errorState("Could not compare", err.message); }
}

// --------------------------------------------------------------- 3D dose surface
//
// A real parameter sweep: every ridge is a separate RK4 integration at that dose level, drawn in
// isometric projection. Nothing is interpolated between levels to make the surface look smooth.

function isoProject(gx, gy, gz, W, H) {
  // Isometric: x runs right-and-down, y runs left-and-down, z is straight up.
  const ox = W * 0.46, oy = H * 0.74;
  const sx = W * 0.0088, sy = H * 0.0062, sz = H * 0.0062;
  return [ox + (gx - gy) * sx * 0.86,
          oy + (gx + gy) * sy * 0.38 - gz * sz];
}

function drawDoseSurface(hostId, data, highlightDose) {
  const host = document.getElementById(hostId);
  if (!host) return;
  const rows = data.rows || [];
  if (!rows.length) { host.innerHTML = '<p class="sub">No surface data.</p>'; return; }

  const W = 820, H = 420;
  const nT = rows[0].times_h.length;
  const maxC = data.max_concentration_mg_per_l || 1;
  const maxT = rows[0].times_h[nT - 1] || 1;

  const gx = function (ti) { return (ti / Math.max(1, nT - 1)) * 100; };
  const gy = function (di) { return (di / Math.max(1, rows.length - 1)) * 100; };
  const gz = function (c) { return (c / maxC) * 56; };

  let svg = '<svg viewBox="0 0 ' + W + " " + H + '" class="dsurf" ' +
    'preserveAspectRatio="xMidYMid meet">';

  for (let d = 0; d < rows.length; d++) {
    const a = isoProject(gx(0), gy(d), 0, W, H);
    const b = isoProject(gx(nT - 1), gy(d), 0, W, H);
    svg += '<line x1="' + a[0].toFixed(1) + '" y1="' + a[1].toFixed(1) + '" x2="' +
      b[0].toFixed(1) + '" y2="' + b[1].toFixed(1) + '" class="sgrid"/>';
  }

  // Far rows first, so nearer ridges draw over them.
  for (let d = rows.length - 1; d >= 0; d--) {
    const row = rows[d];
    const hot = highlightDose !== undefined && Math.abs(row.dose_mg - highlightDose) < 1e-6;
    let path = "", floor = "";
    for (let i = 0; i < nT; i++) {
      const p = isoProject(gx(i), gy(d), gz(row.concentrations_mg_per_l[i]), W, H);
      path += (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1) + " ";
    }
    for (let i = nT - 1; i >= 0; i--) {
      const p = isoProject(gx(i), gy(d), 0, W, H);
      floor += "L" + p[0].toFixed(1) + " " + p[1].toFixed(1) + " ";
    }
    const t = d / Math.max(1, rows.length - 1);
    const fill = "rgba(77,182,164," + (0.06 + t * 0.20).toFixed(3) + ")";
    const stroke = "rgba(" + Math.round(111 + t * 60) + "," + Math.round(159 + t * 23) +
      "," + Math.round(224 - t * 60) + ",0.95)";
    svg += '<path d="' + path + floor + 'Z" class="sface" style="fill:' + fill + '"/>';
    svg += '<path d="' + path + '" class="sridge' + (hot ? " hot" : "") +
      '" style="stroke:' + stroke + '"/>';
  }

  const o = isoProject(0, 0, 0, W, H);
  const xEnd = isoProject(100, 0, 0, W, H);
  const yEnd = isoProject(0, 100, 0, W, H);
  const zEnd = isoProject(0, 0, 60, W, H);
  svg += '<line x1="' + o[0] + '" y1="' + o[1] + '" x2="' + xEnd[0] + '" y2="' + xEnd[1] +
    '" class="saxis"/>' +
    '<line x1="' + o[0] + '" y1="' + o[1] + '" x2="' + yEnd[0] + '" y2="' + yEnd[1] +
    '" class="saxis"/>' +
    '<line x1="' + o[0] + '" y1="' + o[1] + '" x2="' + zEnd[0] + '" y2="' + zEnd[1] +
    '" class="saxis"/>';
  svg += '<text x="' + (xEnd[0] + 6).toFixed(0) + '" y="' + (xEnd[1] + 14).toFixed(0) +
    '" class="dlab">time ' +
    (maxT > 72 ? (maxT / 24).toFixed(0) + "d" : maxT.toFixed(0) + "h") + "</text>";
  svg += '<text x="' + (yEnd[0] - 46).toFixed(0) + '" y="' + (yEnd[1] + 14).toFixed(0) +
    '" class="dlab">dose ' + rows[rows.length - 1].dose_mg.toFixed(0) + " mg</text>";
  svg += '<text x="' + (zEnd[0] - 30).toFixed(0) + '" y="' + (zEnd[1] - 6).toFixed(0) +
    '" class="dlab">' + maxC.toFixed(2) + " mg/L</text>";
  svg += "</svg>";
  host.innerHTML = svg;
}

async function runDoseSurface() {
  const out = document.getElementById("dsSurfOut");
  if (!out) return;
  const b = dosingBody();
  const dose = b.dose_mg;
  out.innerHTML = '<div class="state"><span class="g">&#8987;</span><h4>Sweeping doses...</h4>' +
    "<p>Running a full integration at each dose level.</p></div>";
  try {
    const d = await api("/api/v1/bench/dosing/surface", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({
        dose_min_mg: Math.max(0.001, dose * 0.25), dose_max_mg: dose * 2,
        // Fewer ridges and many more points per ridge. Twelve rows of 60 points across a
        // repeat-dosing week undersampled every peak, which rendered as a spiky mess rather
        // than a surface.
        dose_levels: 8, time_points: 200,
        interval_h: b.interval_h,
        duration_h: Math.min(b.duration_h, b.interval_h > 0 ? b.interval_h * 6 : 72),
        clearance_l_per_h: b.clearance_l_per_h, volume_l: b.volume_l,
        ka_per_h: b.ka_per_h, bioavailability: b.bioavailability,
        species: b.species, scale_from_human: b.scale_from_human,
      }),
    });
    out.innerHTML =
      '<div class="dose-chart" id="dsSurf"></div>' +
      '<table class="dtable" style="margin-top:8px"><thead><tr><th class="num">Dose (mg)</th>' +
      '<th class="num">Cmax (mg/L)</th><th class="num">AUC</th></tr></thead><tbody>' +
      d.rows.map(function (r) {
        return '<tr><td class="num">' + r.dose_mg.toFixed(1) + '</td><td class="num">' +
          r.cmax_mg_per_l.toFixed(3) + '</td><td class="num">' +
          r.auc_mg_h_per_l.toFixed(1) + "</td></tr>"; }).join("") + "</tbody></table>" +
      '<p class="note">' + esc(d.interpretation) + "</p>" + limitations(d.limitations);
    drawDoseSurface("dsSurf", d, dose);
  } catch (err) { out.innerHTML = errorState("Could not sweep doses", err.message); }
}

// The molecule being dosed, in 3D, beside its curve.
async function showDoseMolecule(smiles) {
  const host = document.getElementById("dsMol3d");
  if (!host) return;
  host.innerHTML = '<p class="sub">Generating a conformer...</p>';
  try {
    const r = await api("/api/v1/bench/calculations/conformer_ensemble/run", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ smiles: smiles, n_conformers: 3, optimise: true }),
    });
    const confs = (r.result || {}).conformers || [];
    if (!confs.length || !window.$3Dmol) {
      host.innerHTML = '<p class="sub">No 3D structure available for this input.</p>';
      return;
    }
    host.innerHTML = '<div id="dsMolBox" class="viewer3d" style="width:100%;height:250px"></div>';
    const v = window.$3Dmol.createViewer(document.getElementById("dsMolBox"),
                                         { backgroundColor: "0x0d1015" });
    v.addModel(confs[0].molblock, "mol");
    v.setStyle({}, { stick: { radius: 0.14 }, sphere: { scale: 0.26 } });
    v.zoomTo();
    v.render();
    v.spin("y");
  } catch (err) {
    host.innerHTML = '<p class="sub">Could not build a 3D view: ' + esc(err.message) + "</p>";
  }
}
