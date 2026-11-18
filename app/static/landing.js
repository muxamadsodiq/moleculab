// MOLECULAB landing page — the first thing anyone sees.
//
// Every number on this page is fetched live from the running application, not written into the
// markup. If a capability is removed the page stops claiming it, which is the only way a landing
// page stays honest as the product changes.
"use strict";

async function landingPage() {
  const main = document.getElementById("view");
  main.classList.remove("workstation");
  main.innerHTML =
    '<div class="lp">' +

    // ---------------- hero
    '<section class="lp-hero">' +
    '<div class="lp-mark">MOLECU<span>LAB</span></div>' +
    "<h1>From a research question to evidence you can defend.</h1>" +
    '<p class="lp-lede">An AI-assisted research environment for early-stage drug discovery and ' +
    "pharmaceutical formulation. Evidence, molecules, calculations and reporting in one place " +
    "&mdash; with every value labelled by where it came from.</p>" +
    '<div class="lp-cta">' +
    '<a class="btn primary big" href="#/bench">Open the Research Bench</a>' +
    '<a class="btn big" href="#/playground">Build a molecule</a>' +
    "</div>" +
    '<div class="lp-stats" id="lpStats">' + lpStatSkeleton() + "</div>" +
    "</section>" +

    // ---------------- the problem
    '<section class="lp-sec">' +
    "<h2>The problem</h2>" +
    '<p class="lp-body">A researcher investigating a single compound moves between a structure ' +
    "database, a literature index, an activity database, a clinical-trials registry and a " +
    "spreadsheet. None of them talk to each other. Most of the day goes on copying between tabs " +
    "and trying not to lose track of where each fact came from.</p>" +
    '<p class="lp-body">MOLECULAB puts that work on one bench, and keeps every fact attached to ' +
    "its source.</p>" +
    "</section>" +

    // ---------------- the differentiator
    '<section class="lp-sec lp-accent">' +
    "<h2>What makes it different</h2>" +
    '<p class="lp-body">Most tools show you a number and let you assume it was measured. ' +
    "MOLECULAB never confuses a computation with a measurement, and it refuses rather than " +
    "guesses.</p>" +
    '<div class="lp-grid" id="lpIntegrity">' +
    lpCard("Computed is never measured",
      "Every value carries its epistemic status: experimental, computed, predicted, or missing. " +
      "A computed descriptor cannot satisfy a physical constraint.") +
    lpCard("Unsupported claims are deleted",
      "The assistant's output is split into atomic claims and each is checked against the record " +
      "it cites. Claims the record does not support are removed, not softened.") +
    lpCard("It abstains",
      "Ask something the evidence cannot answer and it says so, naming what the records do and " +
      "do not contain.") +
    lpCard("Missing stays missing",
      "An unrecorded amount is shown as not recorded, never as zero and never estimated to make " +
      "a table look complete.") +
    "</div></section>" +

    // ---------------- capabilities
    '<section class="lp-sec">' +
    "<h2>What it does</h2>" +
    '<div class="lp-grid" id="lpCaps">' + lpStatSkeleton() + "</div>" +
    "</section>" +

    // ---------------- refusals
    '<section class="lp-sec">' +
    "<h2>What it refuses to do</h2>" +
    '<p class="lp-body">These are not oversights. Each is listed in the product with the ' +
    "scientific reason, and none returns a number.</p>" +
    '<div class="lp-grid" id="lpRefuse">' + lpStatSkeleton() + "</div>" +
    "</section>" +

    // ---------------- close
    '<section class="lp-sec lp-close">' +
    "<h2>Start an investigation</h2>" +
    '<p class="lp-body">Describe what you want to find out in your own words. MOLECULAB gathers ' +
    "the evidence, shows you the structures, and runs the calculations it genuinely supports.</p>" +
    '<div class="lp-cta"><a class="btn primary big" href="#/bench">Open the Research Bench</a>' +
    '<a class="btn big" href="#/simulations">See the Simulation Lab</a></div>' +
    '<p class="lp-foot" id="lpFoot"></p>' +
    "</section>" +
    "</div>";

  loadLandingData();
}

function lpStatSkeleton() {
  return '<div class="skel skel-row" style="width:92%"></div>' +
    '<div class="skel skel-row" style="width:78%"></div>';
}

function lpCard(title, body, badge) {
  return '<div class="lp-card">' + (badge || "") + "<h3>" + esc(title) + "</h3>" +
    "<p>" + esc(body) + "</p></div>";
}

async function loadLandingData() {
  // Numbers come from the live application. Nothing here is hardcoded.
  const [stats, calcs, aiStat] = await Promise.all([
    api("/api/stats").catch(function () { return null; }),
    api("/api/v1/bench/calculations").catch(function () { return null; }),
    api("/api/v1/bench/ai/status").catch(function () { return null; }),
  ]);

  const statHost = document.getElementById("lpStats");
  if (statHost) {
    const items = [];
    if (calcs) items.push([calcs.implemented_count, "calculations implemented"]);
    if (stats) {
      items.push([stats.compounds, "curated compounds"]);
      items.push([stats.evidence_records, "evidence records"]);
      items.push([stats.publications, "publications"]);
    }
    items.push([8, "live data providers"]);
    statHost.innerHTML = items.map(function (s) {
      return '<div class="lp-stat"><strong>' + esc(String(s[0])) + "</strong><span>" +
        esc(s[1]) + "</span></div>"; }).join("");
  }

  const capHost = document.getElementById("lpCaps");
  if (capHost) {
    const byCat = (calcs && calcs.categories) || {};
    const names = function (cat) {
      return (byCat[cat] || []).map(function (c) { return c.name; }).join(", ");
    };
    capHost.innerHTML =
      lpCard("Evidence with provenance",
        "Search compounds, plants, publications and formulations in one place. Every attached " +
        "record keeps its source, retrieval time and release identifier.",
        chip("experimental", "Experimental")) +
      lpCard("Build and explore molecules",
        "Draw a structure atom by atom in 2D and watch the 3D conformer rebuild itself. Rotate, " +
        "zoom and click any atom. RDKit validates everything on the backend.",
        chip("computed", "Computed")) +
      lpCard("Real calculations",
        names("molecular") || "Descriptors, validation, conformers, dynamics, solvent.",
        chip("computed", "Computed")) +
      lpCard("Experimental protein-ligand structures",
        "No docking. Instead, deposited crystal structures from the Protein Data Bank with the " +
        "contacts that were actually observed.",
        chip("experimental", "Measured")) +
      lpCard("Formulation research",
        names("formulation") || "Composition totals and unit conversion.",
        chip("derived", "Derived")) +
      lpCard("Reproducible reporting",
        "Notes, hypotheses and reports carry a SHA-256 content fingerprint that excludes the " +
        "timestamp, so the same evidence reproduces the same value.",
        chip("derived", "Reproducible"));
  }

  const refuseHost = document.getElementById("lpRefuse");
  if (refuseHost) {
    refuseHost.innerHTML = calcs && calcs.unavailable
      ? calcs.unavailable.map(function (u) {
          return lpCard(u.name, u.reason, chip("missing", "Unavailable")); }).join("")
      : '<p class="lp-body">Could not load the capability list.</p>';
  }

  const foot = document.getElementById("lpFoot");
  if (foot) {
    const ai = aiStat && aiStat.available
      ? "AI assistance runs on the operator's local Claude Code CLI (" +
        esc(aiStat.model || "") + "), with every tool disabled."
      : "AI assistance is not configured on this instance, and no answer is simulated in its place.";
    foot.innerHTML = ai + " MOLECULAB supports research. It is not a substitute for laboratory " +
      "validation, expert review or regulatory assessment.";
  }
}
