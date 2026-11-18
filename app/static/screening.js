// MOLECULAB — Virtual Screening and Drug Repurposing.
//
// Both engines existed in the backend with no way to reach them: run_screening() was called only by
// its tests, and the repurposing API had no page at all. This file is the missing interface.
//
// Neither view invents anything. Screening ranks compounds that already have real experimental
// measurements against one target and one endpoint; repurposing lists candidates the providers
// actually record. Where the engines refuse (wrong target type, filters that remove everything, a
// provider that is down) the refusal is shown as the result.
"use strict";

let screenState = { options: null, estimate: null, lastRun: null };

// --------------------------------------------------------------- screening

async function screeningPage(params) {
  const main = document.getElementById("view");
  main.classList.add("workstation");
  main.innerHTML =
    '<div class="wx-head"><div><div class="eyebrow">Virtual screening</div>' +
    "<h1>Activity-backed candidate ranking</h1></div>" +
    '<div class="wx-sub">Ranks compounds that already carry real experimental measurements against ' +
    "one target and one endpoint. No docking, no prediction, no combined score.</div></div>" +
    '<div class="wx-body wx-2">' +
    '<div class="wx-col">' +
    '<div class="wx-sec"><h4>Target</h4><div class="in">' +
    '<div class="field"><label for="scTarget">ChEMBL target id</label>' +
    '<input id="scTarget" type="text" value="CHEMBL2835" placeholder="CHEMBL2835"></div>' +
    '<div class="field"><label for="scEndpoint">Endpoint</label>' +
    '<select id="scEndpoint"></select></div>' +
    '<button class="btn" id="scCheck" style="width:100%">Check dataset</button>' +
    '<div id="scEstimate"></div>' +
    "</div></div>" +
    '<div class="wx-sec"><h4>Filters</h4><div class="in">' +
    '<div class="field"><label for="scRank">Rank by</label><select id="scRank"></select></div>' +
    '<div class="field"><label for="scRef">Reference SMILES (optional)</label>' +
    '<input id="scRef" type="text" placeholder="for similarity as a separate dimension"></div>' +
    '<div class="field"><label for="scMinP">Minimum pActivity: <span id="scMinPv">none</span></label>' +
    '<input id="scMinP" type="range" min="0" max="11" step="0.5" value="0"></div>' +
    '<div class="field"><label for="scTop">Candidates to keep: <span id="scTopv">25</span></label>' +
    '<input id="scTop" type="range" min="5" max="200" step="5" value="25"></div>' +
    '<label class="row"><input type="checkbox" id="scPersist" checked> Save this run</label>' +
    '<button class="btn primary" id="scRun" style="width:100%;margin-top:8px">Run screening</button>' +
    '<p class="muted" style="font-size:11.5px">A first run for a target downloads its full activity ' +
    "set from ChEMBL and can take about a minute.</p>" +
    "</div></div>" +
    '<div class="wx-sec"><h4>Saved runs</h4><div class="in"><div id="scRuns">' +
    skeleton(2) + "</div></div></div>" +
    "</div>" +
    '<div class="wx-main scrolls" id="scOut">' +
    emptyState("No screening run yet",
      "Check a target's dataset, then run a screen. Candidates come from real deposited " +
      "measurements; nothing is predicted.") +
    "</div></div>";

  try {
    const opts = await api("/api/v1/screening/options");
    screenState.options = opts;
    document.getElementById("scEndpoint").innerHTML = opts.endpoints.map(function (e) {
      return '<option value="' + esc(e) + '"' + (e === "IC50" ? " selected" : "") + ">" +
        esc(e) + "</option>"; }).join("");
    document.getElementById("scRank").innerHTML = Object.keys(opts.rank_by).map(function (k) {
      return '<option value="' + esc(k) + '">' + esc(k.replace(/_/g, " ")) + "</option>"; }).join("");
  } catch (err) {
    document.getElementById("scOut").innerHTML = errorState("Screening unavailable", err.message);
    return;
  }

  document.getElementById("scMinP").oninput = function (e) {
    document.getElementById("scMinPv").textContent = e.target.value === "0" ? "none" : e.target.value; };
  document.getElementById("scTop").oninput = function (e) {
    document.getElementById("scTopv").textContent = e.target.value; };
  document.getElementById("scCheck").onclick = screeningEstimate;
  document.getElementById("scRun").onclick = screeningRun;
  loadScreeningRuns();

  const preset = params && params.get("run");
  if (preset) openScreeningRun(+preset);
}

async function screeningEstimate() {
  const host = document.getElementById("scEstimate");
  host.innerHTML = '<p class="sub">Checking…</p>';
  try {
    const d = await api("/api/v1/screening/estimate?chembl_target_id=" +
      encodeURIComponent(document.getElementById("scTarget").value.trim()) +
      "&endpoint=" + encodeURIComponent(document.getElementById("scEndpoint").value));
    screenState.estimate = d;
    host.innerHTML =
      '<table class="kv" style="margin-top:8px">' +
      "<tr><th>Name</th><td>" + esc(d.name || "-") + "</td></tr>" +
      "<tr><th>Organism</th><td>" + esc(d.organism || "-") + "</td></tr>" +
      "<tr><th>Type</th><td>" + esc(d.target_type || "-") + "</td></tr>" +
      "<tr><th>Exact measurements</th><td>" + measure(d.exact_measurement_count) + "</td></tr>" +
      "<tr><th>Release</th><td>" + esc(d.source_release || "-") + "</td></tr></table>" +
      "<p>" + (d.screenable ? chip("experimental", "Screenable")
        : chip("missing", "Not screenable")) + "</p>" +
      (d.refusal_reason ? '<p class="limit">' + esc(d.refusal_reason) + "</p>" : "") +
      '<p class="sub">' + esc(d.count_note) + "</p>";
  } catch (err) {
    host.innerHTML = errorState("Could not check target", err.message);
  }
}

async function screeningRun() {
  const out = document.getElementById("scOut");
  out.innerHTML = '<div class="state"><span class="g">&#8987;</span><h4>Screening…</h4>' +
    "<p>Downloading activity records, curating comparable measurements and standardising the " +
    "ranked structures. This can take about a minute on a first run.</p></div>";
  const minP = +document.getElementById("scMinP").value;
  const ref = document.getElementById("scRef").value.trim();
  const body = {
    chembl_target_id: document.getElementById("scTarget").value.trim(),
    endpoint: document.getElementById("scEndpoint").value,
    rank_by: document.getElementById("scRank").value,
    top_n: +document.getElementById("scTop").value,
    persist: document.getElementById("scPersist").checked,
  };
  if (minP > 0) body.min_p_activity = minP;
  if (ref) body.reference_smiles = ref;

  try {
    const d = await api("/api/v1/screening/runs", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body) });
    screenState.lastRun = d;
    renderScreening(d);
    loadScreeningRuns();
  } catch (err) {
    // A refusal is the result, not a crash: show exactly why the engine declined.
    out.innerHTML = panel("Screening refused",
      '<p class="limit">' + esc(err.message) + "</p>" +
      '<p class="sub">MOLECULAB declines rather than returning an empty or fabricated ranking.</p>',
      chip("missing", "Refused"));
  }
}

function renderScreening(d) {
  const snap = d.dataset_snapshot || {};
  const cur = snap.curation_summary || {};
  const hasSim = d.results.length && d.results[0].dimensions.similarity;

  document.getElementById("scOut").innerHTML =
    '<div class="tiles" style="margin-bottom:12px">' +
    tile("Raw rows", (snap.raw_profile || {}).raw_rows, chip("experimental", "ChEMBL")) +
    tile("Comparable measurements", cur.included_measurements, chip("experimental", "Curated")) +
    tile("Compounds ranked", d.results.length, chip("experimental", "Measured")) +
    tile("Censored excluded", cur.censored_measurements_excluded_from_numeric_use,
      chip("missing", "Not numeric")) +
    "</div>" +

    panel("Ranked candidates",
      '<table class="dtable"><thead><tr><th>#</th><th>ChEMBL id</th>' +
      '<th class="num">p' + esc(d.parameters.endpoint) + "</th>" +
      '<th class="num">n</th><th class="num">spread</th>' +
      (hasSim ? '<th class="num">similarity</th>' : "") +
      '<th class="num">MW</th><th></th></tr></thead><tbody>' +
      d.results.map(function (r) {
        const dim = r.dimensions || {};
        return "<tr><td>" + r.rank + "</td><td>" + esc(r.molecule_chembl_id) +
          '</td><td class="num">' + measure(r.measured_p_activity, "", 2) +
          '</td><td class="num">' + esc(r.measurement_count) +
          '</td><td class="num">' + esc((dim.replicate_fold_spread || {}).value || "-") + "x</td>" +
          (hasSim ? '<td class="num">' + measure((dim.similarity || {}).value, "", 3) + "</td>" : "") +
          '<td class="num">' + measure((r.descriptors || {}).molecular_weight, "", 1) + "</td>" +
          '<td><a class="btn ghost" href="#/studio">open</a></td></tr>';
      }).join("") + "</tbody></table>", chip("experimental", "Experimental"), "scroll") +

    panel("How this ranking was produced",
      "<p>" + esc(d.ranking_method) + "</p>" +
      (hasSim ? '<p class="note caution"><strong>Similarity is a separate dimension.</strong> ' +
        esc(d.results[0].dimensions.similarity.caveat) + "</p>" : "") +
      "<h4 style=\"font-size:13px;margin:14px 0 4px\">What was excluded</h4>" +
      '<table class="kv">' + Object.keys(d.exclusion_reasons.curation || {}).map(function (k) {
        return "<tr><th>" + esc(k.replace(/_/g, " ")) + "</th><td>" +
          esc(d.exclusion_reasons.curation[k]) + "</td></tr>"; }).join("") + "</table>" +
      limitations(d.limitations) +
      '<table class="kv" style="margin-top:10px">' +
      "<tr><th>Source release</th><td>" + esc(snap.source_release || "-") + "</td></tr>" +
      "<tr><th>RDKit</th><td>" + esc((d.software_versions || {}).rdkit || "-") + "</td></tr>" +
      "<tr><th>Fingerprint</th><td><span class=\"mono\">" +
      esc((d.content_fingerprint || "").slice(0, 32)) + "</span></td></tr></table>" +
      '<p class="sub">Re-running with these parameters against the same ChEMBL release reproduces ' +
      "this fingerprint.</p>");
}

async function loadScreeningRuns() {
  const host = document.getElementById("scRuns");
  if (!host) return;
  try {
    const d = await api("/api/v1/screening/runs?limit=20");
    host.innerHTML = d.count ? d.items.map(function (r) {
      return '<button class="pick" data-run="' + r.id + '"><strong>' +
        esc(r.target_chembl_id || "run " + r.id) + " · " + esc(r.endpoint || "") +
        '</strong><span class="meta">' + esc(r.result_count) + " candidates · " +
        esc((r.source_release || "")) + "</span></button>"; }).join("")
      : '<p class="sub">No saved runs yet.</p>';
    host.querySelectorAll("[data-run]").forEach(function (b) {
      b.onclick = function () { openScreeningRun(+b.dataset.run); }; });
  } catch (err) { host.innerHTML = '<p class="sub">Could not list runs.</p>'; }
}

async function openScreeningRun(id) {
  const out = document.getElementById("scOut");
  out.innerHTML = skeleton(4);
  try {
    const d = await api("/api/v1/screening/runs/" + id);
    // Stored runs carry structures rather than live descriptors; adapt to the same renderer.
    d.parameters = d.parameters || {};
    d.results = (d.results || []).map(function (r) {
      return { rank: r.rank, molecule_chembl_id: r.chembl_molecule_id || r.inchikey,
               measured_p_activity: r.measured_p_activity, measurement_count: r.measurement_count,
               dimensions: r.dimensions || {}, descriptors: {} }; });
    renderScreening(d);
    out.insertAdjacentHTML("afterbegin",
      '<p class="note">Stored run #' + id + ", reopened from the database. " +
      esc(d.reproducibility_note || "") + "</p>");
  } catch (err) { out.innerHTML = errorState("Could not open run", err.message); }
}

// ------------------------------------------------------------- repurposing

async function repurposingPage() {
  const main = document.getElementById("view");
  main.classList.add("workstation");
  main.innerHTML =
    '<div class="wx-head"><div><div class="eyebrow">Drug repurposing</div>' +
    "<h1>Evidence inventory</h1></div>" +
    '<div class="wx-sub">Lists compounds that providers record against a target, with their ' +
    "development stage. Candidates are not ranked, and a development stage is never reported as " +
    "regulatory approval.</div></div>" +
    '<div class="wx-body wx-2"><div class="wx-col">' +
    '<div class="wx-sec"><h4>Scope</h4><div class="in">' +
    '<div class="field"><label for="rpTarget">Target (gene symbol or Ensembl id)</label>' +
    '<input id="rpTarget" type="text" value="JAK1"></div>' +
    '<div class="field"><label for="rpDisease">Disease</label>' +
    '<input id="rpDisease" type="text" value="rheumatoid arthritis"></div>' +
    '<label class="row"><input type="checkbox" id="rpChembl" checked> Include ChEMBL records</label>' +
    '<button class="btn primary" id="rpRun" style="width:100%;margin-top:8px">Run analysis</button>' +
    '<p class="muted" style="font-size:11.5px">Queries Open Targets and ChEMBL live; takes up to a ' +
    "minute.</p></div></div>" +
    '<div class="wx-sec"><h4>Classification rules</h4><div class="in"><div id="rpRules">' +
    skeleton(2) + "</div></div></div></div>" +
    '<div class="wx-main scrolls" id="rpOut">' +
    emptyState("No analysis yet", "Choose a target and disease, then run the analysis.") +
    "</div></div>";

  api("/api/v1/repurposing/classification-rules").then(function (d) {
    const host = document.getElementById("rpRules");
    if (!host) return;
    const cats = d.categories || d.rules || d;
    host.innerHTML = '<p class="sub">' +
      esc(typeof cats === "object" ? Object.keys(cats).join(" · ") : String(cats).slice(0, 200)) +
      "</p>";
  }).catch(function () {
    const host = document.getElementById("rpRules");
    if (host) host.innerHTML = '<p class="sub">Rules unavailable.</p>'; });

  document.getElementById("rpRun").onclick = async function () {
    const out = document.getElementById("rpOut");
    out.innerHTML = '<div class="state"><span class="g">&#8987;</span><h4>Querying providers…</h4>' +
      "<p>Resolving the disease and target, then collecting candidate records.</p></div>";
    try {
      const d = await api("/api/v1/repurposing/analyses", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          target: document.getElementById("rpTarget").value.trim(),
          disease: document.getElementById("rpDisease").value.trim(),
          include_chembl: document.getElementById("rpChembl").checked,
          max_candidates: 50 }) });
      renderRepurposing(d);
    } catch (err) { out.innerHTML = errorState("Analysis failed", err.message); }
  };
}

function renderRepurposing(d) {
  const assoc = d.target_disease_association || {};
  const dts = assoc.datatype_scores || {};
  document.getElementById("rpOut").innerHTML =
    '<div class="tiles" style="margin-bottom:12px">' +
    tile("Candidates", d.candidate_count, chip("experimental", "Recorded")) +
    tile("Association score", assoc.overall_score ? assoc.overall_score.toFixed(3) : "-",
      chip("derived", "Not a success probability")) +
    tile("Target", (d.target || {}).gene_symbol || "-", esc((d.target || {}).chembl_target_id || "")) +
    tile("Disease", (d.disease || {}).ontology_id || "-", esc((d.disease || {}).name || "")) +
    "</div>" +

    panel("Target-disease evidence by datatype",
      '<table class="dtable"><thead><tr><th>Datatype</th><th class="num">Score</th></tr></thead><tbody>' +
      Object.keys(dts).map(function (k) {
        return "<tr><td>" + esc(k.replace(/_/g, " ")) + '</td><td class="num">' +
          measure(dts[k], "", 3) + "</td></tr>"; }).join("") + "</tbody></table>" +
      '<p class="note caution">These are evidence-aggregation scores for prioritisation. They are ' +
      "not probabilities of drug success and not measures of treatment efficacy.</p>",
      chip("derived", "Provider score")) +

    panel("Candidates",
      '<p class="sub">' + esc(d.candidate_ordering || "") + "</p>" +
      '<table class="dtable"><thead><tr><th>Name</th><th>Classification</th>' +
      "<th>Mechanism</th><th>Recorded indications</th></tr></thead><tbody>" +
      (d.candidates || []).map(function (c) {
        const cls = c.classification || {};
        const moa = (c.dimensions && c.dimensions.mechanisms_of_action) ||
                    c.mechanisms_of_action || [];
        const ind = (c.dimensions && c.dimensions.indications) || c.indications || [];
        return "<tr><td><strong>" + esc(c.name || "-") + "</strong></td><td>" +
          chip(cls.category === "approved_medicine" ? "experimental" : "hypothesis",
               String(cls.category || "unclassified").replace(/_/g, " ")) +
          '<div class="sub" style="max-width:40ch">' + esc(cls.regulatory_approval || "") + "</div>" +
          "</td><td>" + esc(Array.isArray(moa) ? moa.slice(0, 2).map(function (m) {
            return typeof m === "string" ? m : (m.mechanism_of_action || ""); }).join("; ") : "") +
          "</td><td>" + esc(Array.isArray(ind) ? ind.slice(0, 3).map(function (i) {
            return typeof i === "string" ? i : (i.disease_name || i.disease_from_source || ""); })
            .join("; ") : "") + "</td></tr>";
      }).join("") + "</tbody></table>", chip("experimental", "Provider records"), "scroll") +

    panel("Limitations and provenance",
      '<p class="limit">' + esc(d.disclaimer || "") + "</p>" + limitations(d.limitations) +
      '<table class="kv">' + Object.keys(d.source_releases || {}).map(function (k) {
        return "<tr><th>" + esc(k) + "</th><td>" + esc(d.source_releases[k]) + "</td></tr>"; }).join("") +
      "<tr><th>Fingerprint</th><td><span class=\"mono\">" +
      esc((d.content_fingerprint || "").slice(0, 32)) + "</span></td></tr></table>");
}
