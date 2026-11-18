// MOLECULAB whole-body distribution — where a dose actually goes, organ by organ.
//
// The body is a schematic, deliberately. It is a map of the compartments in the model, not a
// picture of an animal: the organs are the ones the PBPK model integrates, and each one is
// shaded by the concentration that model computes for it at the selected time.
//
// WHAT THE COLOUR MEANS: simulated concentration, relative to the highest concentration reached
// anywhere in the body during the run. It does NOT mean harm, damage, pain, or risk. A bright
// liver means the drug is there, not that the liver is in trouble. The page says so beside the
// figure, because this is exactly the visual a reader is most likely to over-read.
"use strict";

const ANAT = { last: null, t: 0, timer: null, species: "human" };

// Two genuinely different body plans. Scaling one human outline down and calling it a rat was
// misleading, so an upright biped and a horizontal quadruped are drawn separately, with organs
// placed to match each plan. Both are SCHEMATIC: they are maps of the compartments the model
// integrates, drawn to be read, not anatomical illustrations.

const BODY_PLANS = {
  biped: {
    label: "Human",
    viewBox: "0 0 200 420",
    outline: "M100 56 q-26 4 -30 30 q-4 24 -2 48 q-22 10 -24 36 q-2 30 2 58 " +
             "q2 10 10 8 q6 -2 6 -14 l2 -40 l4 0 q-4 54 -2 106 q2 60 8 116 l16 0 " +
             "q2 -58 6 -112 l4 0 q4 54 6 112 l16 0 q6 -56 8 -116 q2 -52 -2 -106 l4 0 " +
             "l2 40 q0 12 6 14 q8 2 10 -8 q4 -28 2 -58 q-2 -26 -24 -36 " +
             "q2 -24 -2 -48 q-4 -26 -30 -30 z",
    head: "M100 30 m-22 0 a22 24 0 1 0 44 0 a22 24 0 1 0 -44 0",
    organs: {
      brain:  "M100 28 m-15 0 a15 16 0 1 0 30 0 a15 16 0 1 0 -30 0",
      lung:   "M86 108 q-14 2 -16 24 q-2 22 10 28 q8 2 10 -8 l0 -42 z " +
              "M114 108 q14 2 16 24 q2 22 -10 28 q-8 2 -10 -8 l0 -42 z",
      heart:  "M100 118 q-9 -9 -15 0 q-7 8 2 17 l13 13 l13 -13 q9 -9 2 -17 q-8 -9 -15 0 z",
      liver:  "M74 164 q26 -8 52 0 q4 17 -7 24 q-21 8 -40 1 q-10 -7 -5 -25 z",
      kidney: "M80 198 q-8 1 -8 12 q0 11 8 12 q6 0 6 -12 q0 -11 -6 -12 z " +
              "M120 198 q8 1 8 12 q0 11 -8 12 q-6 0 -6 -12 q0 -11 6 -12 z",
      gut:    "M82 200 q18 -4 36 0 q6 21 -4 32 q-16 7 -30 0 q-10 -11 -2 -32 z",
      fat:    "M72 240 q28 -7 56 0 q4 13 -2 21 q-26 7 -52 0 q-6 -8 -2 -21 z",
      muscle: "M84 276 q-5 40 -2 76 q7 5 14 0 q3 -38 5 -76 z " +
              "M116 276 q5 40 2 76 q-7 5 -14 0 q-3 -38 -5 -76 z",
      skin:   "M100 58 q-24 4 -27 28 q-3 22 -2 44 l6 0 q-1 -20 2 -40 q3 -20 21 -24 " +
              "q18 4 21 24 q3 20 2 40 l6 0 q1 -22 -2 -44 q-3 -24 -27 -28 z",
      rest_of_body: "M93 136 l14 0 l0 22 l-14 0 z",
    },
  },

  quadruped: {
    label: "Quadruped",
    viewBox: "0 0 360 200",
    // Head left, tail right, four limbs below. A different animal, not a shrunken person.
    outline: "M92 92 q-20 -30 -40 -22 q-16 6 -14 24 q-14 4 -14 16 q0 12 16 14 " +
             "q6 14 22 14 l170 0 q26 0 34 -16 q6 -12 -2 -24 q22 -6 34 2 q6 4 4 -4 " +
             "q-2 -8 -14 -12 q-14 -4 -26 2 q-16 -14 -44 -14 l-110 0 q-14 0 -26 20 z",
    head: "M52 70 m-22 10 a24 18 0 1 0 48 0 a24 18 0 1 0 -48 0",
    organs: {
      brain:  "M44 68 m-13 0 a13 11 0 1 0 26 0 a13 11 0 1 0 -26 0",
      lung:   "M108 84 q-10 2 -10 16 q0 14 10 16 q8 0 8 -16 q0 -14 -8 -16 z " +
              "M134 84 q10 2 10 16 q0 14 -10 16 q-8 0 -8 -16 q0 -14 8 -16 z",
      heart:  "M120 92 q-8 -8 -13 0 q-6 7 2 15 l11 11 l11 -11 q8 -8 2 -15 q-7 -8 -13 0 z",
      liver:  "M158 86 q26 -6 48 2 q4 16 -8 22 q-20 6 -36 0 q-9 -7 -4 -24 z",
      kidney: "M224 92 q-9 1 -9 11 q0 11 9 12 q7 0 7 -12 q0 -10 -7 -11 z " +
              "M250 92 q9 1 9 11 q0 11 -9 12 q-7 0 -7 -12 q0 -10 7 -11 z",
      gut:    "M166 116 q34 -6 66 2 q6 18 -6 26 q-28 8 -54 0 q-11 -9 -6 -28 z",
      fat:    "M150 142 q50 -8 96 2 q4 10 -4 15 q-44 8 -88 0 q-7 -6 -4 -17 z",
      muscle: "M104 148 q-6 22 -2 40 q7 4 13 0 q2 -20 4 -40 z " +
              "M256 148 q6 22 2 40 q-7 4 -13 0 q-2 -20 -4 -40 z " +
              "M134 150 q-5 18 -2 34 q6 4 11 0 q2 -16 3 -34 z " +
              "M226 150 q5 18 2 34 q-6 4 -11 0 q-2 -16 -3 -34 z",
      skin:   "M90 94 q-18 -26 -36 -20 q-14 6 -12 22 l6 2 q-2 -12 8 -16 q14 -4 28 18 z " +
              "M98 70 l164 0 q24 0 32 14 l-6 4 q-8 -12 -26 -12 l-164 0 z",
      rest_of_body: "M186 96 l20 0 l0 16 l-20 0 z",
    },
  },
};

const SPECIES_PLAN = {
  human: "biped", rat: "quadruped", mouse: "quadruped",
  rabbit: "quadruped", dog: "quadruped",
};

// All four animals share the quadruped plan, but a mouse is not a beagle. These transforms give
// each a distinct build - a stubby mouse, a long-bodied rat, a deep-chested rabbit, a taller
// dog - so the figure cannot be mistaken for one generic animal. They change the DRAWING only;
// the physiology driving the colours is each species' own.
const SPECIES_BUILD = {
  rat:    { t: "translate(0,6) scale(1.00,0.92)", note: "long body, short legs" },
  mouse:  { t: "translate(26,14) scale(0.80,0.80)", note: "small and stubby" },
  rabbit: { t: "translate(6,0) scale(0.94,1.08)", note: "deep chest, long hind legs" },
  dog:    { t: "translate(0,-4) scale(1.04,1.12)", note: "taller, longer limbs" },
  human:  { t: "", note: "upright" },
};


async function anatomyPage(params) {
  const main = document.getElementById("view");
  main.classList.add("workstation");

  main.innerHTML =
    '<div class="wx-head"><div><div class="eyebrow">Whole-body distribution</div>' +
    "<h1>Where the dose goes</h1></div>" +
    '<div class="wx-sub">Physiologically-based pharmacokinetics on published reference ' +
    "physiology. Colour shows simulated concentration &mdash; not harm.</div></div>" +

    '<div class="wx-body wx-3">' +

    // ---- controls
    '<div class="wx-col">' +
    '<div class="wx-sec"><h4>Dose</h4><div class="in">' +
    '<div class="field"><label for="anDose">Dose (mg)</label>' +
    '<input id="anDose" type="number" value="100" min="0.001" step="any"></div>' +
    '<div class="field"><label for="anFreq">How often</label><select id="anFreq">' +
    '<option value="0" selected>Once only</option><option value="6">Every 6 h</option>' +
    '<option value="8">Every 8 h</option><option value="12">Every 12 h</option>' +
    '<option value="24">Once a day</option></select></div>' +
    '<div class="field"><label for="anDur">Simulate for</label><select id="anDur">' +
    '<option value="6">6 hours</option><option value="24" selected>24 hours</option>' +
    '<option value="72">3 days</option><option value="168">1 week</option></select></div>' +
    "</div></div>" +

    '<div class="wx-sec"><h4>Subject</h4><div class="in">' +
    '<div class="field"><label for="anSpecies">Species</label>' +
    '<select id="anSpecies"></select></div>' +
    '<p class="sub" id="anPhys"></p></div></div>' +

    '<div class="wx-sec"><h4>Elimination and uptake</h4><div class="in">' +
    '<p class="sub"><strong>Inputs, not predictions.</strong> MOLECULAB cannot derive these ' +
    "from a structure.</p>" +
    '<div class="field"><label for="anHep">Hepatic clearance (L/h)</label>' +
    '<input id="anHep" type="number" value="6.0" step="any" min="0"></div>' +
    '<div class="field"><label for="anRen">Renal clearance (L/h)</label>' +
    '<input id="anRen" type="number" value="0" step="any" min="0"></div>' +
    '<div class="field"><label for="anKa">Absorption rate (per hour)</label>' +
    '<input id="anKa" type="number" value="1.5" step="any" min="0.0001"></div>' +
    "</div></div>" +

    '<div class="wx-sec"><h4>Tissue affinity (Kp)</h4><div class="in">' +
    '<p class="sub">How strongly each tissue takes the drug up, relative to plasma. 1 means no ' +
    "preference. These are measured or estimated values you supply.</p>" +
    '<div class="field"><label for="anKpFat">Fat</label>' +
    '<input id="anKpFat" type="number" value="1" step="any" min="0.01"></div>' +
    '<div class="field"><label for="anKpBrain">Brain</label>' +
    '<input id="anKpBrain" type="number" value="1" step="any" min="0.01"></div>' +
    '<div class="field"><label for="anKpLiver">Liver</label>' +
    '<input id="anKpLiver" type="number" value="1" step="any" min="0.01"></div>' +
    '<div class="field"><label for="anKpMuscle">Muscle</label>' +
    '<input id="anKpMuscle" type="number" value="1" step="any" min="0.01"></div>' +
    '<button class="btn primary" id="anRun" style="width:100%;margin-top:8px">Simulate</button>' +
    "</div></div></div>" +

    // ---- the body
    '<div class="wx-main">' +
    '<div class="an-stage">' +
    '<div class="an-figure" id="anFigure"></div>' +
    '<div class="an-side">' +
    '<div id="anLegend"></div>' +
    '<div id="anRank"></div>' +
    "</div></div>" +
    '<div class="an-time" id="anTime"></div>' +
    "</div>" +

    // ---- readout
    '<div class="wx-col"><div class="wx-sec"><h4>Selected organ</h4>' +
    '<div class="in" id="anOrgan"><p class="sub">Run a simulation, then click an organ.</p>' +
    "</div></div>" +
    '<div class="wx-sec"><h4>What colour means</h4><div class="in">' +
    '<p class="note caution"><strong>Colour is concentration, not harm.</strong> A bright organ ' +
    "means the drug is present there. It does not mean damage, pain, risk or toxicity. " +
    "MOLECULAB has no model of what any concentration does to a tissue.</p></div></div>" +
    '<div class="wx-sec"><h4>Mass balance</h4><div class="in" id="anBalance">' +
    '<p class="sub">Published after each run as a check on the integration.</p>' +
    "</div></div></div></div>";

  try {
    const t = await api("/api/v1/bench/pbpk/physiology");
    document.getElementById("anSpecies").innerHTML = t.species.map(function (s) {
      return '<option value="' + esc(s.id) + '"' + (s.id === "human" ? " selected" : "") + ">" +
        esc(s.label) + "</option>"; }).join("");
    ANAT.physiology = t;
    updatePhysNote();
  } catch (err) {
    document.getElementById("anFigure").innerHTML =
      errorState("Physiology unavailable", err.message);
    return;
  }

  document.getElementById("anSpecies").onchange = updatePhysNote;
  document.getElementById("anRun").onclick = runAnatomy;

  // Fetch the anatomy bundle up front so the first draw can already be the real thing.
  if (typeof loadAnatomyBundle === "function") {
    const fig = document.getElementById("anFigure");
    if (fig) fig.innerHTML = '<p class="sub">Loading anatomy\u2026</p>';
    await loadAnatomyBundle();
  }
  drawBody(null);
}

function updatePhysNote() {
  const id = document.getElementById("anSpecies").value;
  const s = (ANAT.physiology.species || []).filter(function (x) { return x.id === id; })[0];
  const el = document.getElementById("anPhys");
  if (s && el) {
    el.textContent = s.weight_kg + " kg, cardiac output " + s.cardiac_output_l_per_h +
      " L/h. Reference physiology for an average animal, not an individual.";
  }
  ANAT.species = id;
  drawBody(null);
}

async function runAnatomy() {
  const fig = document.getElementById("anFigure");
  fig.innerHTML = '<div class="state"><span class="g">&#8987;</span><h4>Integrating…</h4>' +
    "<p>Solving every organ compartment over the dosing period.</p></div>";
  try {
    const r = await api("/api/v1/bench/calculations/pbpk/run", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({
        dose_mg: +document.getElementById("anDose").value,
        species: document.getElementById("anSpecies").value,
        duration_h: +document.getElementById("anDur").value,
        interval_h: +document.getElementById("anFreq").value,
        hepatic_cl_l_per_h: +document.getElementById("anHep").value,
        renal_cl_l_per_h: +document.getElementById("anRen").value,
        ka_per_h: +document.getElementById("anKa").value,
        kp: {
          fat: +document.getElementById("anKpFat").value,
          brain: +document.getElementById("anKpBrain").value,
          liver: +document.getElementById("anKpLiver").value,
          muscle: +document.getElementById("anKpMuscle").value,
        },
      }),
    });
    if (!r.succeeded) { fig.innerHTML = errorState("Could not simulate", r.error); return; }
    ANAT.last = r.result;
    ANAT.t = 0;
    renderAnatomy();
  } catch (err) { fig.innerHTML = errorState("Could not simulate", err.message); }
}

function peakAnywhere(d) {
  let m = 0;
  (d.tissues || []).forEach(function (t) {
    m = Math.max(m, t.peak_concentration_mg_per_l); });
  return m || 1;
}

function renderAnatomy() {
  const d = ANAT.last;
  const maxC = peakAnywhere(d);

  document.getElementById("anLegend").innerHTML =
    '<div class="an-legend"><span class="lab">concentration</span>' +
    '<div class="an-ramp"></div>' +
    '<div class="an-ends"><span>0</span><span>' + maxC.toFixed(3) + " mg/L</span></div>" +
    '<p class="sub">Relative to the highest concentration reached anywhere in the body ' +
    "during this run.</p></div>";

  const last = d.series[d.series.length - 1];
  document.getElementById("anTime").innerHTML =
    '<div class="row gap">' +
    '<button class="btn" id="anPlay">Play</button>' +
    '<input type="range" id="anSlider" min="0" max="' + (d.series.length - 1) +
    '" value="0" style="flex:1">' +
    '<span class="sub" id="anClock" style="min-width:11rem;text-align:right"></span></div>';

  document.getElementById("anSlider").oninput = function (e) {
    stopAnatomyPlay();
    ANAT.t = +e.target.value;
    paintFrame();
  };
  document.getElementById("anPlay").onclick = toggleAnatomyPlay;

  const mb = d.mass_balance;
  document.getElementById("anBalance").innerHTML =
    '<table class="kv">' +
    "<tr><th>Administered</th><td>" + mb.administered_mg.toFixed(3) + " mg</td></tr>" +
    "<tr><th>Still in body</th><td>" + mb.in_body_at_end_mg.toFixed(3) + " mg</td></tr>" +
    "<tr><th>Eliminated</th><td>" + mb.eliminated_mg.toFixed(3) + " mg</td></tr>" +
    "<tr><th>Error</th><td>" + chip(mb.error_percent < 0.5 ? "experimental" : "missing",
      mb.error_percent.toFixed(4) + "%") + "</td></tr></table>" +
    '<p class="sub">' + esc(mb.note) + "</p>" +
    '<p class="sub">RK4, ' + esc(String(d.integration.steps)) + " steps of " +
    esc(String(d.integration.step_h)) + " h.</p>";

  paintFrame();
}

function paintFrame() {
  const d = ANAT.last;
  if (!d) return;
  const frame = d.series[Math.min(ANAT.t, d.series.length - 1)];
  const maxC = peakAnywhere(d);
  drawBody(frame, maxC);

  const clock = document.getElementById("anClock");
  if (clock) {
    clock.textContent = (frame.time_h > 48 ? (frame.time_h / 24).toFixed(2) + " days"
                                           : frame.time_h.toFixed(2) + " h") +
      " — plasma " + frame.plasma_mg_per_l.toFixed(4) + " mg/L";
  }
  const slider = document.getElementById("anSlider");
  if (slider && +slider.value !== ANAT.t) slider.value = ANAT.t;

  // Ranked organs at this instant.
  const now = (d.tissues || []).map(function (t) {
    return { label: t.label, id: t.id, c: frame[t.id] === undefined ? 0 : frame[t.id] }; })
    .sort(function (a, b) { return b.c - a.c; });
  document.getElementById("anRank").innerHTML =
    '<table class="dtable"><thead><tr><th>Organ</th><th class="num">mg/L now</th>' +
    "</tr></thead><tbody>" + now.map(function (t) {
      return '<tr data-organ="' + esc(t.id) + '" class="an-row"><td>' + esc(t.label) +
        '</td><td class="num">' + t.c.toFixed(4) + "</td></tr>"; }).join("") +
    "</tbody></table>";
  document.querySelectorAll("[data-organ]").forEach(function (row) {
    row.onclick = function () { selectOrgan(row.dataset.organ); };
  });
}

function heat(frac) {
  // Dark surface -> teal -> amber as concentration rises. Readable in both directions and not
  // a red "danger" ramp, because this is not a danger scale.
  const f = Math.max(0, Math.min(1, frac));
  const r = Math.round(23 + f * 189);
  const g = Math.round(30 + f * 140);
  const b = Math.round(38 + f * 20);
  return "rgb(" + r + "," + g + "," + b + ")";
}

function drawBody(frame, maxC) {
  const host = document.getElementById("anFigure");
  if (!host) return;

  // Real anatomy where it exists. BodyParts3D covers human only, so every other species keeps
  // the schematic rather than being shown a shrunken human.
  if (typeof anatomyAvailable === "function" && anatomyAvailable(ANAT.species)) {
    if (host.dataset.mode !== "mesh") {
      host.dataset.mode = "mesh";
      host.innerHTML = '<div id="an3d" class="an3d"></div><div id="anCredit"></div>';
    }
    const ok = renderAnatomy3D("an3d", frame, maxC, selectOrgan);
    const credit = document.getElementById("anCredit");
    if (credit && !credit.innerHTML) credit.innerHTML = anatomyCredit();
    if (ok) return;
    host.dataset.mode = "";          // the viewer failed; fall through to the schematic
  }

  const plan = BODY_PLANS[SPECIES_PLAN[ANAT.species] || "biped"];
  const label = (ANAT.physiology && (ANAT.physiology.species || [])
    .filter(function (s) { return s.id === ANAT.species; })[0]) || {};

  const build = (SPECIES_BUILD[ANAT.species] || {}).t || "";
  let svg = '<svg viewBox="' + plan.viewBox + '" class="anbody" ' +
    'preserveAspectRatio="xMidYMid meet">';
  svg += build ? '<g transform="' + build + '">' : "<g>";
  svg += '<path d="' + plan.outline + '" class="anoutline"/>';
  svg += '<path d="' + plan.head + '" class="anoutline"/>';

  Object.keys(plan.organs).forEach(function (id) {
    let fill = "var(--surface-2)";
    if (frame && frame[id] !== undefined && maxC) fill = heat(frame[id] / maxC);
    const name = ORGAN_LABELS[id] || id;
    svg += '<path d="' + plan.organs[id] + '" class="anorgan" data-o="' + id +
      '" fill="' + fill + '"><title>' + esc(name) +
      (frame && frame[id] !== undefined
        ? " \u2014 " + frame[id].toFixed(4) + " mg/L" : "") + "</title></path>";
  });
  svg += "</g></svg>";

  host.dataset.mode = "schematic";
  host.innerHTML = svg +
    (typeof anatomyUnavailableNote === "function" && ANAT.species !== "human"
      ? anatomyUnavailableNote(label.label || ANAT.species) : "") +
    '<p class="an-caption">Schematic of ' + esc(label.label || ANAT.species) +
    ". Shapes are a map of the model's compartments, drawn to be read \u2014 not an " +
    "anatomical illustration. Organ volumes and blood flows are this species' own reference " +
    "physiology.</p>";

  host.querySelectorAll("[data-o]").forEach(function (el) {
    el.onclick = function () { selectOrgan(el.dataset.o); };
  });
}

const ORGAN_LABELS = {
  brain: "Brain", lung: "Lungs", heart: "Heart", liver: "Liver", kidney: "Kidneys",
  gut: "Gut", fat: "Fat", muscle: "Muscle", skin: "Skin", rest_of_body: "Rest of body",
};

function selectOrgan(id) {
  const d = ANAT.last;
  const host = document.getElementById("anOrgan");
  if (!d) { host.innerHTML = '<p class="sub">Run a simulation first.</p>'; return; }
  const t = (d.tissues || []).filter(function (x) { return x.id === id; })[0];
  if (!t) { host.innerHTML = '<p class="sub">That organ is not in this model.</p>'; return; }
  const frame = d.series[Math.min(ANAT.t, d.series.length - 1)];
  host.innerHTML =
    "<h5>" + esc(t.label) + "</h5>" +
    '<table class="kv">' +
    "<tr><th>Now</th><td>" + (frame[id] === undefined ? "-" : frame[id].toFixed(4)) +
    " mg/L</td></tr>" +
    "<tr><th>Peak this run</th><td>" + t.peak_concentration_mg_per_l.toFixed(4) +
    " mg/L</td></tr>" +
    "<tr><th>Volume</th><td>" + t.volume_l.toFixed(4) + " L</td></tr>" +
    "<tr><th>Blood flow</th><td>" + t.blood_flow_l_per_h.toFixed(3) + " L/h</td></tr>" +
    "<tr><th>Kp used</th><td>" + t.kp + "</td></tr>" +
    "<tr><th>Share of dose now</th><td>" + t.percent_of_dose_at_end.toFixed(2) +
    "% at end</td></tr></table>" +
    '<p class="sub">Volume and blood flow are reference physiology. Kp is a value you supplied, ' +
    "not something MOLECULAB derived from a molecule.</p>";
}

function toggleAnatomyPlay() {
  const btn = document.getElementById("anPlay");
  if (ANAT.timer) { stopAnatomyPlay(); return; }
  btn.textContent = "Stop";
  ANAT.timer = setInterval(function () {
    ANAT.t += 1;
    if (ANAT.t >= ANAT.last.series.length) { ANAT.t = 0; }
    paintFrame();
  }, 45);
}

function stopAnatomyPlay() {
  if (ANAT.timer) { clearInterval(ANAT.timer); ANAT.timer = null; }
  const btn = document.getElementById("anPlay");
  if (btn) btn.textContent = "Play";
}
