// MOLECULAB frontend: hash-routed views over the /api endpoints. No build step, no external libraries.
"use strict";

const view = document.getElementById("view");

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Every request is bounded. Without this a slow or busy server leaves a spinner on screen for
// ever, which is what used to happen on the Simulation Lab: the "Computing..." state was set
// before the fetch and nothing ever cleared it. A timeout turns a hang into an honest error.
const API_TIMEOUT_MS = 90000;          // generous: RDKit work is CPU-bound and can take seconds
const API_SLOW_PATHS = [/\/ai\//, /\/assistant/, /\/screening\/runs/, /\/hypotheses\/generate/,
                        /\/repurposing\//, /protein_ligand/];

async function api(path, opts) {
  const slow = API_SLOW_PATHS.some(function (re) { return re.test(path); });
  const limit = slow ? 420000 : API_TIMEOUT_MS;
  const ctrl = typeof AbortController === "function" ? new AbortController() : null;
  const timer = ctrl ? setTimeout(function () { ctrl.abort(); }, limit) : null;
  let res;
  try {
    res = await fetch(path, ctrl ? Object.assign({}, opts, { signal: ctrl.signal }) : opts);
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw new Error("The server did not respond within " + Math.round(limit / 1000) +
        " seconds. Nothing partial is shown, because a partial result cannot be trusted. " +
        "Try again, or use a smaller input.");
    }
    throw new Error("Could not reach the MOLECULAB server: " + (err && err.message ? err.message : err));
  } finally {
    if (timer) clearTimeout(timer);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body));
  return body;
}

const TYPES = ["direct_experimental_identification", "tentative_identification", "secondary_source_report",
  "traditional_use", "computational_prediction", "unverified_claim"];
const TYPE_LABEL = {
  direct_experimental_identification: "Direct experimental",
  tentative_identification: "Tentative",
  secondary_source_report: "Secondary source",
  traditional_use: "Traditional use",
  computational_prediction: "Computational",
  unverified_claim: "Unverified claim",
};
const TYPE_LIMIT = {
  traditional_use: "Traditional use is not proof of efficacy.",
  computational_prediction: "A computational prediction is not an experimental result; a docking score does not establish binding or efficacy.",
  tentative_identification: "Identification is tentative (e.g. MS-based assignment without isolation).",
  secondary_source_report: "Statement repeats earlier literature; it is not this publication's own finding.",
  direct_experimental_identification: "Occurrence of a compound in a plant is not proof of any therapeutic benefit.",
  unverified_claim: "This claim has not been verified.",
};
const typeBadge = (t) => `<span class="badge t-${esc(t)}" title="${esc(t)}">${esc(TYPE_LABEL[t] || t)}</span>`;
const verifBadge = (v) => `<span class="badge v-${esc(v)}">${v === "excerpt_verified" ? "✓ excerpt matches source" : esc(v)}</span>`;
const reviewBadge = (r) => `<span class="badge ${r === "expert_confirmed" ? "v-excerpt_verified" : "t-tentative_identification"}">${esc(r.replaceAll("_", " "))}</span>`;
const pubLink = (p) => `<a href="#/publication/${p.id}">${esc(p.title)}</a> <span class="sub">(${esc(p.year)})</span>`;
const extLinks = (p) => [
  p.pmid ? `<a href="https://pubmed.ncbi.nlm.nih.gov/${esc(p.pmid)}/" target="_blank" rel="noopener">PMID ${esc(p.pmid)}</a>` : "",
  p.doi ? `<a href="https://doi.org/${esc(p.doi)}" target="_blank" rel="noopener">DOI</a>` : "",
].filter(Boolean).join(" · ");

function typeBar(counts) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
  return `<div class="bar" title="${esc(Object.entries(counts).map(([t, n]) => `${TYPE_LABEL[t]}: ${n}`).join(", "))}">${TYPES
    .filter((t) => counts[t]).map((t) => `<span class="c-${t}" style="width:${(100 * counts[t]) / total}%"></span>`).join("")}</div>`;
}
const legend = () => `<div class="legend">${TYPES.slice(0, 5).map((t) => `<span><i class="c-${t}"></i>${TYPE_LABEL[t]}</span>`).join("")}</div>`;

function evidenceRow(e, { showPlant = true, showCompound = true } = {}) {
  return `<tr>
    <td><a href="#/evidence/${e.id}" class="mono">#${e.id}</a></td>
    ${showPlant ? `<td><a href="#/plant/${e.plant.id}"><i>${esc(e.plant.accepted_name)}</i></a></td>` : ""}
    ${showCompound ? `<td>${e.compound ? `<a href="#/compound/${e.compound.id}">${esc(e.compound.name)}</a>` : '<span class="sub">— (plant-level)</span>'}</td>` : ""}
    <td>${typeBadge(e.evidence_type)}</td>
    <td>${esc(e.observation)}<div class="sub">${esc([e.plant_part, e.geographic_origin].filter(Boolean).join(" · "))}</div></td>
    <td class="hide-sm">${pubLink(e.publication)}</td>
  </tr>`;
}

function evidenceTable(list, opts = {}) {
  if (!list.length) return '<p class="empty">No evidence records.</p>';
  const o = { showPlant: true, showCompound: true, ...opts };
  return `<div class="table-wrap"><table><thead><tr><th>ID</th>${o.showPlant ? "<th>Plant</th>" : ""}
    ${o.showCompound ? "<th>Compound</th>" : ""}<th>Evidence type</th><th>Observation</th><th class="hide-sm">Source</th></tr></thead>
    <tbody>${list.map((e) => evidenceRow(e, o)).join("")}</tbody></table></div>`;
}

// ---------- views ----------
async function dashboard() {
  const d = await api("/api/dashboard");
  const t = d.totals, v = d.verification;
  const n = (obj, k) => obj[k] || 0;
  view.innerHTML = `
    <h1>Research overview</h1>
    <p class="sub">Evidence-linked knowledge base for Central Asian medicinal-plant research. Every number on this page is computed from the database when the page loads.</p>
    <div class="grid" style="margin-top:12px">
      <div class="card"><div class="sub">Indexed plants</div><div class="stat">${t.plants}</div></div>
      <div class="card"><div class="sub">Chemical compounds</div><div class="stat">${t.compounds}</div>
        <div class="sub">${t.compounds_without_identifier} without a PubChem identifier</div></div>
      <div class="card"><div class="sub">Publications</div><div class="stat">${t.publications}</div></div>
      <div class="card"><div class="sub">Evidence records</div><div class="stat">${t.evidence_records}</div>${typeBar(d.evidence_by_type)}</div>
    </div>
    <h2>Evidence verification status</h2>
    <div class="ladder">
      <div class="step ${n(v.source_excerpt, "excerpt_verified") === t.evidence_records ? "ok" : "pending"}"><div class="k">1 · Source excerpt</div>
        <div class="v">${n(v.source_excerpt, "excerpt_verified")} / ${t.evidence_records} match source text</div>
        <div class="sub">Quotation checked against PubMed title/abstract. Not a scientific check.</div></div>
      <div class="step pending"><div class="k">2 · Interpretation</div>
        <div class="v">${n(v.interpretation, "ai_assisted_interpretation")} AI-assisted · ${n(v.interpretation, "curator_interpretation")} by curator</div>
        <div class="sub">Who assigned evidence type, plant part and method.</div></div>
      <div class="step ${n(v.expert_review, "expert_confirmed") ? "ok" : "pending"}"><div class="k">3 · Expert review</div>
        <div class="v">${n(v.expert_review, "expert_confirmed")} confirmed · ${n(v.expert_review, "pending_expert_review")} pending · ${n(v.expert_review, "expert_disputed") + n(v.expert_review, "rejected")} disputed/rejected</div>
        <div class="sub">Only authenticated reviewers can change this.</div></div>
    </div>
    <h2>Research coverage and gaps</h2>${legend()}
    <div class="table-wrap" style="margin-top:8px"><table><thead><tr><th>Plant</th><th>Evidence mix</th><th>Records</th>
      <th class="hide-sm">Compounds</th><th class="hide-sm">Publications</th><th class="hide-sm">Uz/Ru names</th><th>Open gaps</th></tr></thead><tbody>
      ${d.coverage.map((c) => `<tr><td><a href="#/plant/${c.id}"><i>${esc(c.accepted_name)}</i></a><div class="sub">${esc(c.family || "")}</div></td>
        <td style="min-width:110px">${typeBar(c.evidence_by_type)}</td><td class="mono">${c.evidence_count}</td>
        <td class="hide-sm mono">${c.compound_count}</td><td class="hide-sm mono">${c.publication_count}</td>
        <td class="hide-sm">${c.has_regional_names ? "yes (uncited)" : '<span class="err">missing</span>'}</td>
        <td class="mono">${c.gap_count}</td></tr>`).join("")}</tbody></table></div>
    <h2>Recent research activity</h2>
    <ul class="activity">${d.recent_activity.map((a) => `<li><span class="mono sub">${esc(a.at.slice(0, 16).replace("T", " "))}</span>
      ${a.kind === "review" ? "Review" : "Evidence added"} · <a href="#/evidence/${a.evidence_id}">#${a.evidence_id}</a> ${esc(a.label)}</li>`).join("")}</ul>`;
}

async function plantsList() {
  const plants = await api("/api/plants");
  view.innerHTML = `<h1>Plants</h1><div class="table-wrap"><table><thead><tr><th>Accepted name</th><th>Family</th>
    <th class="hide-sm">Names</th><th>Records</th></tr></thead><tbody>${plants.map((p) => `<tr>
    <td><a href="#/plant/${p.id}"><i>${esc(p.accepted_name)}</i></a> <span class="sub">${esc(p.authority || "")}</span></td>
    <td>${esc(p.family || "")}</td><td class="hide-sm">${esc(p.aliases.join(" · "))}</td><td class="mono">${p.evidence_count}</td></tr>`).join("")}
    </tbody></table></div>`;
}

async function plantPage(id) {
  const p = await api(`/api/plants/${id}`);
  const typeCounts = {}, reviewCounts = {}, pubs = {};
  for (const e of p.evidence) {
    typeCounts[e.evidence_type] = (typeCounts[e.evidence_type] || 0) + 1;
    reviewCounts[e.review_status] = (reviewCounts[e.review_status] || 0) + 1;
    pubs[e.publication.id] = e.publication;
  }
  const aliases = p.aliases.length ? p.aliases.map((a) => `<tr><td>${esc(a.name)}</td><td class="mono">${esc(a.language)}/${esc(a.script)}</td>
      <td>${esc(a.alias_type)}</td><td class="sub">${esc(a.source)}</td></tr>`).join("")
    : '<tr><td colspan="4" class="empty">No names recorded in other languages.</td></tr>';
  view.innerHTML = `
    <div class="section-label">Plant profile ${await addToWs("plant", p.id)}</div>
    <h1><i>${esc(p.accepted_name)}</i> <span class="sub">${esc(p.authority || "")}</span></h1>
    <div class="sub">Family ${esc(p.family || "unknown")}${p.gbif_key ? ` · <a href="https://www.gbif.org/species/${p.gbif_key}" target="_blank" rel="noopener">GBIF ${p.gbif_key}</a> (accepted name)` : ""}
      · <a href="#/graph?plant_id=${p.id}">Open in knowledge graph</a></div>
    ${p.notes ? `<p class="notice" style="margin-top:10px">${esc(p.notes)}</p>` : ""}
    <div class="grid" style="margin-top:12px">
      <div class="card"><div class="sub">Evidence distribution (${p.evidence.length} records)</div>${typeBar(typeCounts)}
        <div class="chips" style="margin-top:8px">${Object.entries(typeCounts).map(([t, n]) => `${typeBadge(t)}<span class="mono sub">${n}</span>`).join(" ")}</div></div>
      <div class="card"><div class="sub">Verification status</div>
        <div class="chips" style="margin-top:6px">${Object.entries(reviewCounts).map(([r, n]) => `${reviewBadge(r)}<span class="mono sub">${n}</span>`).join(" ")}</div>
        <div class="sub" style="margin-top:6px">All excerpts are matched to source text; interpretation was AI-assisted.</div></div>
    </div>
    <h2>Synonyms and names</h2><div class="table-wrap"><table><thead><tr><th>Name</th><th>Lang</th><th>Type</th><th>Source</th></tr></thead><tbody>${aliases}</tbody></table></div>
    <h2>Chemical constituents (${p.compounds.length})</h2>
    <div class="grid">${p.compounds.map((c) => `<div class="card"><h3><a href="#/compound/${c.id}">${esc(c.name)}</a></h3>
      <div class="mono sub">${esc(c.molecular_formula || "no formula")} · ${c.pubchem_cid ? "CID " + c.pubchem_cid : '<span class="err">no PubChem ID</span>'}</div>
      <div class="chips" style="margin-top:6px">${c.evidence_types.map(typeBadge).join("")}</div></div>`).join("")}</div>
    <h2>Publications (${Object.keys(pubs).length})</h2>
    ${Object.values(pubs).map((pb) => `<div class="card" style="margin-bottom:6px">${pubLink(pb)}<div class="sub">${extLinks(pb)}</div></div>`).join("")}
    <h2>Research gaps (${p.gaps.length})</h2>
    <div style="display:grid;gap:8px">${p.gaps.map((g) => `<div class="card gap"><span class="rule">${esc(g.rule)}</span>
      ${g.compound ? ` · <b>${esc(g.compound)}</b>` : ""}<div>${esc(g.detail)}</div>
      ${g.evidence_ids.length ? `<div class="sub">Records: ${g.evidence_ids.map((i) => `<a href="#/evidence/${i}">#${i}</a>`).join(" ")}</div>` : ""}</div>`).join("")}</div>
    <h2>Evidence records (${p.evidence.length})</h2>${evidenceTable(p.evidence, { showPlant: false })}`;
}

async function compoundPage(id) {
  const c = await api(`/api/compounds/${id}`);
  const plants = {};
  for (const e of c.evidence) (plants[e.plant.id] ||= { ...e.plant, n: 0 }).n++;
  view.innerHTML = `
    <div class="section-label">Compound profile ${await addToWs("compound", c.id)}</div>
    <h1>${esc(c.name)}</h1>
    <div class="two">
      <div class="card"><div class="section-label">Chemical identity · from PubChem</div><dl class="kv">
        <dt>PubChem CID</dt><dd>${c.pubchem_url ? `<a href="${esc(c.pubchem_url)}" target="_blank" rel="noopener">${c.pubchem_cid}</a>` : '<span class="err">not resolved</span>'}</dd>
        <dt>Formula</dt><dd class="mono">${esc(c.molecular_formula || "—")}</dd>
        <dt>Mol. weight</dt><dd class="mono">${esc(c.molecular_weight ?? "—")}</dd>
        <dt>SMILES</dt><dd class="mono">${esc(c.smiles || "—")}</dd>
        <dt>InChIKey</dt><dd class="mono">${esc(c.inchikey || "—")}</dd>
        <dt>IUPAC</dt><dd>${esc(c.iupac_name || "—")}</dd>
        <dt>Synonyms</dt><dd>${esc((c.synonyms || []).join(" · ") || "—")}</dd>
        <dt>Retrieved</dt><dd class="sub">${esc(c.identifier_source || "not retrieved")} ${esc(c.identifier_retrieved_at || "")}</dd></dl>
        <p class="sub" style="margin-bottom:0">${esc(c.note)}</p></div>
      <div>${c.structure_image ? `<img class="structure" alt="2D structure of ${esc(c.name)} from PubChem" src="${esc(c.structure_image)}" loading="lazy">
        <div class="sub">Structure image served by PubChem (needs internet).</div>` : ""}</div></div>
    <div class="section-label" style="margin-top:20px">Occurrence evidence · from publications</div>
    <h2 style="margin-top:0">Linked plants</h2>
    <div class="chips">${Object.values(plants).map((p) => `<a class="badge t-secondary_source_report" href="#/plant/${p.id}"><i>${esc(p.accepted_name)}</i> · ${p.n} record${p.n > 1 ? "s" : ""}</a>`).join("") || '<span class="empty">No plant linked.</span>'}</div>
    <h2>Supporting evidence (${c.evidence.length})</h2>${evidenceTable(c.evidence, { showCompound: false })}`;
}

async function publicationsList() {
  const pubs = await api("/api/publications");
  view.innerHTML = `<h1>Publications</h1><div class="table-wrap"><table><thead><tr><th>Year</th><th>Title</th>
    <th class="hide-sm">Journal</th><th>Records</th></tr></thead><tbody>${pubs.map((p) => `<tr><td class="mono">${esc(p.year)}</td>
    <td><a href="#/publication/${p.id}">${esc(p.title)}</a><div class="sub">${extLinks(p)}</div></td>
    <td class="hide-sm sub">${esc(p.journal)}</td><td class="mono">${p.evidence_count}</td></tr>`).join("")}</tbody></table></div>`;
}

async function publicationPage(id) {
  const p = await api(`/api/publications/${id}`);
  view.innerHTML = `<div class="section-label">Publication ${await addToWs("publication", p.id)}</div><h1>${esc(p.title)}</h1>
    <div class="sub">${esc((p.authors || []).slice(0, 6).join(", "))}${(p.authors || []).length > 6 ? " et al." : ""} · ${esc(p.journal)} · ${esc(p.year)} · ${esc(p.language || "")}</div>
    <div style="margin:6px 0">${extLinks(p)} ${p.publication_types ? `<span class="sub">· ${esc(p.publication_types.join(", "))}</span>` : ""}</div>
    <h2>Abstract</h2><div class="card">${esc(p.abstract || "No abstract stored.")}</div>
    <div class="sub" style="margin-top:6px">Metadata: ${esc(p.metadata_source)} · retrieved ${esc(p.metadata_retrieved_at)}</div>
    <h2>Evidence extracted from this publication (${p.evidence.length})</h2>${evidenceTable(p.evidence)}`;
}

async function evidencePage(id) {
  const [e, health] = await Promise.all([api(`/api/evidence/${id}`), api("/api/health")]);
  const pv = e.provenance;
  const cls = (s) => (["excerpt_verified", "expert_confirmed", "curator_interpretation"].includes(s) ? "ok"
    : ["excerpt_mismatch", "expert_disputed", "rejected"].includes(s) ? "bad" : "pending");
  view.innerHTML = `<div class="section-label">Evidence record ${await addToWs("evidence", e.id)}</div><h1>#${e.id} · <i>${esc(e.plant.accepted_name)}</i>${e.compound ? " · " + esc(e.compound.name) : ""}</h1>
    <div class="chips">${typeBadge(e.evidence_type)} ${reviewBadge(e.review_status)}</div>
    <h2>What was reported</h2>
    <div class="card"><p class="answer" style="margin:0 0 8px">${esc(e.observation)}</p><dl class="kv">
      <dt>Plant</dt><dd><a href="#/plant/${e.plant.id}"><i>${esc(e.plant.accepted_name)}</i></a></dd>
      <dt>Compound</dt><dd>${e.compound ? `<a href="#/compound/${e.compound.id}">${esc(e.compound.name)}</a>` : "— (plant-level record)"}</dd>
      <dt>Plant part</dt><dd>${esc(e.plant_part || "not stated")}</dd>
      <dt>Origin</dt><dd>${esc(e.geographic_origin || "not stated in source")}</dd>
      <dt>Method</dt><dd>${esc(e.identification_method || "not stated")}</dd>
      <dt>Classification</dt><dd>${esc(e.evidence_type_label)}</dd></dl></div>
    <h2>Which source reported it</h2>
    <div class="card">${pubLink(e.publication)}<div class="sub">${extLinks(e.publication)} · excerpt taken from the ${esc(e.source_location)}</div>
      <blockquote>${esc(e.original_excerpt)}</blockquote></div>
    <h2>What has been checked</h2>
    <div class="ladder five">
      <div class="step ${pv.publication_metadata.status === "metadata_verified" ? "ok" : "pending"}"><div class="k">1 · Publication metadata</div><div class="v">${esc(pv.publication_metadata.status)}</div><div class="sub">${esc(pv.publication_metadata.meaning)}</div></div>
      <div class="step ${cls(pv.source_excerpt.status)}"><div class="k">2 · Excerpt vs source</div><div class="v">${esc(pv.source_excerpt.status)}</div><div class="sub">${esc(pv.source_excerpt.meaning)}</div></div>
      <div class="step ${cls(pv.interpretation.status)}"><div class="k">3 · Interpretation</div><div class="v">${esc(pv.interpretation.status)}</div><div class="sub">${esc(pv.interpretation.meaning)} Review: ${esc(pv.interpretation.review_status.replaceAll("_", " "))}.</div></div>
      <div class="step ${cls(pv.expert_review.status)}"><div class="k">4 · Expert review</div><div class="v">${esc(pv.expert_review.status)}</div><div class="sub">${esc(pv.expert_review.meaning)}</div></div>
      <div class="step ${pv.disputed_or_rejected ? "bad" : "ok"}"><div class="k">5 · Disputed / rejected</div><div class="v">${pv.disputed_or_rejected ? "yes" : "no"}</div><div class="sub">${esc(pv.basis)}</div></div>
    </div>
    <h2>Known limitations</h2>
    <ul><li class="sub">${esc(TYPE_LIMIT[e.evidence_type] || "")}</li><li class="sub">Curated from the abstract only; the full text was not read.</li>
      ${e.curator_notes ? `<li class="sub">Curator note: ${esc(e.curator_notes)}</li>` : ""}</ul>
    <h2>Review history</h2>
    ${e.reviews.length ? `<div class="table-wrap"><table><thead><tr><th>Status</th><th>Reviewer</th><th>Notes</th><th class="hide-sm">When</th></tr></thead>
      <tbody>${e.reviews.map((r) => `<tr><td>${esc(r.review_status)}</td><td>${esc(r.reviewer)}</td><td>${esc(r.review_notes || "")}</td>
      <td class="hide-sm mono">${esc(r.created_at.slice(0, 16))}</td></tr>`).join("")}</tbody></table></div>` : '<p class="empty">No expert has reviewed this record.</p>'}
    ${health.reviews_enabled ? `<h2>Add expert review (authenticated)</h2>
      <form id="reviewform" class="filters">
        <select name="review_scope"><option value="expert_review">Expert review</option><option value="interpretation_review">Interpretation review</option></select>
        <select name="review_status"><option value="expert_confirmed">Confirm</option><option value="expert_disputed">Dispute</option>
          <option value="rejected">Reject</option><option value="needs_full_text">Needs full text</option></select>
        <input name="token" type="password" placeholder="Reviewer token" required autocomplete="off">
        <input name="review_notes" placeholder="Notes" style="flex:1;min-width:160px">
        <button class="primary" type="submit">Save review</button><span id="reviewmsg" class="sub"></span></form>`
      : '<p class="notice">Review mutations are disabled on this server (no reviewer tokens configured). This is a read-only demonstration; no one can mark evidence as expert-reviewed here.</p>'}`;
  const form = document.getElementById("reviewform");
  if (form) form.onsubmit = async (ev) => {
    ev.preventDefault();
    const f = Object.fromEntries(new FormData(ev.target));
    try {
      await api(`/api/evidence/${id}/reviews`, { method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${f.token}` },
        body: JSON.stringify({ review_status: f.review_status, review_scope: f.review_scope, review_notes: f.review_notes || null }) });
      evidencePage(id);
    } catch (err) { document.getElementById("reviewmsg").innerHTML = `<span class="err">${esc(err.message)}</span>`; }
  };
}

async function evidenceExplorer(params) {
  const [plants, types] = await Promise.all([api("/api/plants"), api("/api/meta/evidence-types")]);
  const sel = (name, opts, cur) => `<select name="${name}"><option value="">All ${name.replace("_", " ")}</option>${opts.map(([v, l]) =>
    `<option value="${esc(v)}" ${String(cur) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  const qs = new URLSearchParams([...params].filter(([, v]) => v));
  const list = await api(`/api/evidence?${qs}`);
  view.innerHTML = `<h1>Evidence explorer</h1>
    <p class="sub">Each row is one scientific claim from one publication. Open a record to see its excerpt, provenance and limitations.</p>
    <form id="ef" class="filters">
      ${sel("plant_id", plants.map((p) => [p.id, p.accepted_name]), params.get("plant_id"))}
      ${sel("evidence_type", types.map((t) => [t.value, TYPE_LABEL[t.value]]), params.get("evidence_type"))}
      ${sel("verification_status", [["excerpt_verified", "Excerpt matches source"], ["unverified", "Unverified"], ["excerpt_mismatch", "Excerpt mismatch"]], params.get("verification_status"))}
      <input name="year_from" type="number" placeholder="From year" style="width:110px" value="${esc(params.get("year_from") || "")}">
      <input name="year_to" type="number" placeholder="To year" style="width:110px" value="${esc(params.get("year_to") || "")}">
      <input name="q" placeholder="Text in observation/excerpt" value="${esc(params.get("q") || "")}">
      <button class="primary" type="submit">Filter</button>
    </form><p class="sub">${list.length} records</p>${evidenceTable(list)}`;
  document.getElementById("ef").onsubmit = (ev) => {
    ev.preventDefault();
    location.hash = "#/evidence?" + new URLSearchParams([...new FormData(ev.target)].filter(([, v]) => v));
  };
}

async function searchPage(q) {
  const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
  const total = r.plants.length + r.compounds.length + r.publications.length + r.evidence.length;
  view.innerHTML = `<h1>Search: “${esc(q)}”</h1><p class="sub">${total} results · ${r.plants.length} plants · ${r.compounds.length} compounds · ${r.publications.length} publications · ${r.evidence.length} evidence records</p>
    ${r.plants.length ? `<h2>Plants</h2><div class="grid">${r.plants.map((p) => `<div class="card"><h3><a href="#/plant/${p.id}"><i>${esc(p.accepted_name)}</i></a></h3>
      <div class="sub">${esc(p.family || "")} · ${esc(p.aliases.join(" · "))}</div></div>`).join("")}</div>` : ""}
    ${r.compounds.length ? `<h2>Compounds</h2><div class="grid">${r.compounds.map((c) => `<div class="card"><h3><a href="#/compound/${c.id}">${esc(c.name)}</a></h3>
      <div class="mono sub">${esc(c.molecular_formula || "")} ${c.pubchem_cid ? "· CID " + c.pubchem_cid : ""}</div></div>`).join("")}</div>` : ""}
    ${r.publications.length ? `<h2>Publications</h2>${r.publications.map((p) => `<div class="card" style="margin-bottom:8px">${pubLink(p)}<div class="sub">${extLinks(p)}</div></div>`).join("")}` : ""}
    ${r.evidence.length ? `<h2>Evidence excerpts</h2>${evidenceTable(r.evidence)}` : ""}
    ${total ? "" : '<p class="empty">Nothing in the database matches. A missing record is not proof that no research exists.</p>'}`;
}

// ---------- knowledge graph (SVG, force-directed, no library) ----------
async function graphPage(params) {
  const plants = await api("/api/plants");
  const qs = new URLSearchParams([...params].filter(([, v]) => v));
  const g = await api(`/api/graph?${qs}`);
  view.innerHTML = `<h1>Knowledge graph</h1>
    <p class="sub">Plant → evidence record → compound, and evidence record → publication. Built from stored database relationships. Tap a node to inspect it; drag to pan.</p>
    <form id="gf" class="filters"><select name="plant_id"><option value="">All plants</option>${plants.map((p) =>
      `<option value="${p.id}" ${String(params.get("plant_id")) === String(p.id) ? "selected" : ""}>${esc(p.accepted_name)}</option>`).join("")}</select>
      <button class="primary" type="submit">Show</button>
      <span class="legend"><span><i class="node-plant" style="background:var(--accent)"></i>Plant ${g.counts.plant}</span>
      <span><i style="background:var(--accent-2)"></i>Compound ${g.counts.compound}</span>
      <span><i style="background:#b48ead"></i>Publication ${g.counts.publication}</span>
      <span><i style="background:#4b5563"></i>Evidence ${g.counts.evidence}</span></span></form>
    <div class="graph-wrap"><svg id="gsvg"></svg><div class="graph-side" id="gside"><span class="sub">Tap a node to see its details.</span></div></div>`;
  document.getElementById("gf").onsubmit = (ev) => {
    ev.preventDefault();
    location.hash = "#/graph?" + new URLSearchParams([...new FormData(ev.target)].filter(([, v]) => v));
  };
  drawGraph(g);
}

function drawGraph(g) {
  const svg = document.getElementById("gsvg"), side = document.getElementById("gside");
  const W = svg.clientWidth || 800, H = svg.clientHeight || 500;
  const R = { plant: 11, compound: 7, publication: 6, evidence: 3.5 };
  const nodes = g.nodes.map((n, i) => ({ ...n, x: W / 2 + 200 * Math.cos(i), y: H / 2 + 200 * Math.sin(i * 1.7), vx: 0, vy: 0 }));
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const edges = g.edges.map((e) => ({ ...e, s: byId[e.source], t: byId[e.target] }));
  // deterministic force layout: repulsion, spring edges, gentle gravity
  for (let it = 0; it < 260; it++) {
    const a = 1 - it / 260;
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const p = nodes[i], q = nodes[j];
      let dx = p.x - q.x, dy = p.y - q.y, d2 = dx * dx + dy * dy + 0.01;
      if (d2 > 90000) continue;
      const f = (900 / d2) * a;
      p.vx += dx * f; p.vy += dy * f; q.vx -= dx * f; q.vy -= dy * f;
    }
    for (const e of edges) {
      const dx = e.t.x - e.s.x, dy = e.t.y - e.s.y, d = Math.sqrt(dx * dx + dy * dy) + 0.01;
      const f = ((d - 38) / d) * 0.08 * a;
      e.s.vx += dx * f; e.s.vy += dy * f; e.t.vx -= dx * f; e.t.vy -= dy * f;
    }
    for (const n of nodes) {
      n.vx += (W / 2 - n.x) * 0.004 * a; n.vy += (H / 2 - n.y) * 0.004 * a;
      n.x += Math.max(-20, Math.min(20, n.vx)); n.y += Math.max(-20, Math.min(20, n.vy)); n.vx *= 0.55; n.vy *= 0.55;
    }
  }
  const NS = "http://www.w3.org/2000/svg";
  const el = (tag, attrs) => { const x = document.createElementNS(NS, tag); for (const k in attrs) x.setAttribute(k, attrs[k]); return x; };
  const root = el("g", {});
  svg.appendChild(root);
  const lines = edges.map((e) => { const l = el("line", { x1: e.s.x, y1: e.s.y, x2: e.t.x, y2: e.t.y }); root.appendChild(l); return l; });
  let selected = null;
  for (const n of nodes) {
    const c = el("circle", { cx: n.x, cy: n.y, r: R[n.type] || 4, class: `node-${n.type}` });
    const title = el("title", {}); title.textContent = n.type === "publication" ? n.title : n.label; c.appendChild(title);
    root.appendChild(c);
    if (n.type !== "evidence") {
      const t = el("text", { x: n.x + (R[n.type] || 4) + 2, y: n.y + 3 });
      t.textContent = n.label.length > 22 ? n.label.slice(0, 21) + "…" : n.label; root.appendChild(t);
    }
    c.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (selected) selected.classList.remove("sel");
      selected = c; c.classList.add("sel");
      edges.forEach((e, i) => lines[i].classList.toggle("hl", e.s === n || e.t === n));
      showNode(n, edges, side);
    });
  }
  // pan by drag; fit everything into view
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  let vb = [Math.min(...xs) - 30, Math.min(...ys) - 30, Math.max(...xs) - Math.min(...xs) + 140, Math.max(...ys) - Math.min(...ys) + 60];
  const setVB = () => svg.setAttribute("viewBox", vb.join(" "));
  setVB();
  let drag = null;
  svg.addEventListener("pointerdown", (ev) => { drag = { x: ev.clientX, y: ev.clientY, vb: [...vb] }; });
  svg.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const k = vb[2] / svg.clientWidth;
    vb[0] = drag.vb[0] - (ev.clientX - drag.x) * k; vb[1] = drag.vb[1] - (ev.clientY - drag.y) * k; setVB();
  });
  window.addEventListener("pointerup", () => { drag = null; }, { once: false });
}

function showNode(n, edges, side) {
  const neighbours = edges.filter((e) => e.s === n || e.t === n).map((e) => ({ rel: e.relation, other: e.s === n ? e.t : e.s }));
  const link = { plant: `#/plant/${n.ref_id}`, compound: `#/compound/${n.ref_id}`, publication: `#/publication/${n.ref_id}`, evidence: `#/evidence/${n.ref_id}` }[n.type];
  side.innerHTML = `<div class="section-label">${esc(n.type)}</div>
    <h3 style="margin:0 0 4px">${esc(n.type === "publication" ? n.title : n.label)}</h3>
    ${n.type === "evidence" ? `<div class="chips">${typeBadge(n.evidence_type)} ${reviewBadge(n.review_status)}</div>` : ""}
    ${n.type === "publication" ? `<div class="sub">${esc(n.label)} · ${esc(n.year)}</div>` : ""}
    ${n.disconnected ? '<p class="notice warn">No evidence records link to this node.</p>' : ""}
    <p><a href="${link}">Open full ${esc(n.type)} page →</a></p>
    <div class="sub">${neighbours.length} relationship${neighbours.length === 1 ? "" : "s"}</div>
    <ul style="padding-left:16px;margin:4px 0">${neighbours.slice(0, 25).map((x) => `<li><span class="sub">${esc(x.rel.replaceAll("_", " "))}</span> ${esc(x.other.type === "publication" ? x.other.label : x.other.label)}</li>`).join("")}</ul>`;
}

// ---------- assistant & claim verification ----------
const VERDICT_LABEL = { supported: "Supported", partially_supported: "Partially supported", not_supported: "Not supported",
  contradicted: "Contradicted", insufficient_information: "Insufficient information" };
const verdictBadge = (v) => `<span class="verdict vd-${esc(v)}">${esc(VERDICT_LABEL[v] || v)}</span>`;
const catLabel = (c) => esc(String(c).replaceAll("_", " "));

function highlight(excerpt, span) {
  const e = esc(excerpt);
  if (!span) return e;
  const s = esc(span.trim());
  const i = e.toLowerCase().indexOf(s.toLowerCase());
  return i < 0 ? e : e.slice(0, i) + "<mark>" + e.slice(i, i + s.length) + "</mark>" + e.slice(i + s.length);
}

function claimCard(v) {
  const sources = (v.source_excerpts || []).map((x) => `<div style="margin-top:6px">
      <a href="#/evidence/${x.evidence_id}" class="mono">#${x.evidence_id}</a> ${typeBadge(x.evidence_type)} ${verdictBadge(x.verdict)}
      <span class="sub">${x.publication.pmid ? "PMID " + esc(x.publication.pmid) : ""} ${esc(x.publication.year || "")}</span>
      <blockquote>${highlight(x.excerpt, x.matched_span)}</blockquote></div>`).join("");
  const structured = (v.structured_sources || []).map((x) => `<div class="sub">Source: ${esc(x.type.replaceAll("_", " "))}
      ${x.pubchem_cid ? "· CID " + esc(x.pubchem_cid) : ""}${x.gbif_key ? "· GBIF " + esc(x.gbif_key) : ""}
      ${x.url ? `· <a href="${esc(x.url)}" target="_blank" rel="noopener">record</a>` : ""} ${x.retrieved_at ? "· retrieved " + esc(x.retrieved_at) : ""}</div>`).join("");
  return `<div class="vcard ${esc(v.status)}">
    <div class="chips">${verdictBadge(v.status)}<span class="badge t-secondary_source_report">${catLabel(v.category)}</span>
      ${v.expert_review_required ? '<span class="badge t-tentative_identification">expert review required</span>' : ""}
      ${(v.evidence_ids || []).map((i) => `<a href="#/evidence/${i}" class="mono">#${i}</a>`).join(" ")}</div>
    <div class="stmt">${esc(v.statement)}</div>
    <div class="sub"><b>Why:</b> ${esc(v.explanation)}</div>
    ${structured}
    <details><summary>Source excerpts, checks and limitations</summary>${sources}
      <ul class="checks">${(v.checks || []).map((c) => `<li class="${c.passed ? "" : "fail"}"><span class="mono">${c.evidence_id ? "#" + c.evidence_id + " " : ""}${esc(c.name)}</span> — ${esc(c.detail)}</li>`).join("")}</ul>
      ${(v.limitations || []).length ? `<ul>${v.limitations.map((l) => `<li class="sub">${esc(l)}</li>`).join("")}</ul>` : ""}
      ${v.draft ? `<div class="sub">Split from drafted sentence: “${esc(v.draft)}”</div>` : ""}
      <div class="sub">Verifiers: ${esc((v.verifiers || []).join(", "))}</div></details></div>`;
}

// Reports, honestly, whether a language model actually answered. MOLECULAB's retrieval, claim
// extraction and claim-level verification are deterministic and run with or without a provider; only
// the free-text generation step needs one. The banner exists so a demo can never imply otherwise.
async function copilotBanner() {
  let s;
  try { s = await api("/api/v1/copilot/status"); } catch (_) { return ""; }
  if (s.model_generation_available) {
    return `<p class="authored"><span class="who">Live model generation: ON</span><br>
      Provider <strong>${esc(s.provider)}</strong>, model <strong>${esc(s.model || "default")}</strong>.
      Generated text is still split into atomic claims and verified against cited records;
      ${esc(s.verifier_note)}</p>`;
  }
  return `<p class="limit"><strong>Live model generation is unavailable.</strong>
    ${esc(s.reason || "")}<br><span class="sub">Nothing on this page is produced by a language model.
    Answers below are assembled from MOLECULAB's own records and verified claim by claim.
    ${esc(s.verifier_note || "")}</span></p>`;
}

async function assistantPage() {
  view.innerHTML = `<h1>AI Research Copilot</h1>
    <div id="copilotstatus"></div>
    <p class="sub">Questions are routed by type. Drafted answers are split into atomic claims, and every claim is verified against the exact source excerpt it cites. Unsupported claims never enter the answer; they are shown below it, struck through, with the reason.</p>
    <form id="af" class="filters"><input id="aq" style="flex:1;min-width:220px" placeholder="e.g. Which chemical constituents have been reported in Glycyrrhiza glabra?" required minlength="3">
      <button class="primary" type="submit">Ask</button></form>
    <div class="chips" id="examples">${["Which chemical constituents have been reported in Glycyrrhiza glabra?", "What is the molecular formula of glabridin?",
      "Is glabridin proven to cure cancer?", "Is glabridin the most abundant compound in licorice?",
      "Is pulegone the dominant constituent of Ziziphora clinopodioides essential oil?", "What research reports glabridin?"].map((q) =>
      `<button type="button" class="badge" style="padding:3px 9px">${esc(q)}</button>`).join("")}</div>
    <div id="ares" style="margin-top:12px"></div>
    <h2>Claim checker</h2>
    <p class="sub">Verify one statement against chosen evidence records (or against PubChem/GBIF records for formulas and families).</p>
    <form id="vf"><textarea id="vclaim" placeholder="e.g. Glabridin is a potent antioxidant."></textarea>
      <div class="filters" style="margin-top:6px"><input id="vids" placeholder="Evidence IDs, e.g. 13, 17" style="flex:1">
      <button class="primary" type="submit">Verify claim</button></div></form><div id="vres"></div>
    <details style="margin-top:14px"><summary>How claims are verified (compatibility matrix)</summary><div id="matrix" class="sub">Loading…</div></details>`;
  copilotBanner().then((h) => { const el = document.getElementById("copilotstatus"); if (el) el.innerHTML = h; });

  const run = async (q) => {
    const out = document.getElementById("ares");
    out.innerHTML = '<p class="empty">Classifying, retrieving and verifying…</p>';
    try {
      const a = await api("/api/assistant", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) });
      const ab = a.answerability;
      const rejected = a.claim_verifications.filter((v) => !["supported", "partially_supported"].includes(v.status) && v !== a.hypothesis && !(a.hypothesis && v.statement === a.hypothesis.statement));
      out.innerHTML = `
        <div class="card"><div class="chips"><span class="status ${esc(ab.status)}">${esc(ab.status.replaceAll("_", " "))}</span>
          <span class="badge t-secondary_source_report">question type: ${catLabel(a.question_type)}</span>
          <span class="mono sub">mode: ${esc(a.mode)}${a.llm_used ? ` · ${esc(a.llm_model)}` : ""}</span></div>
          <p class="sub" style="margin:8px 0 0"><b>Routing and answerability:</b> ${esc(ab.reason)}</p>
          <div class="summary-row">${Object.entries(a.verification_summary).filter(([, n]) => n).map(([k, n]) => `${verdictBadge(k)}<span class="mono sub">${n}</span>`).join(" ")}</div></div>
        <h2>Answer</h2><div class="card answer">${esc(a.answer || "—")}</div>
        <p class="sub">${esc(a.message)}</p>
        ${a.hypothesis ? `<h2>The question as a claim</h2>${claimCard(a.hypothesis)}` : ""}
        ${a.properties.length ? `<h2>Chemical properties (PubChem record)</h2><div class="table-wrap"><table><thead><tr><th>Compound</th><th>Property</th><th>Value</th><th class="hide-sm">Source</th></tr></thead><tbody>
          ${a.properties.map((p) => `<tr><td>${esc(p.compound)}</td><td>${esc(p.property)}</td><td class="mono">${esc(p.value ?? "not available")}</td>
          <td class="hide-sm sub">${esc(p.source.type)} ${esc(p.source.retrieved_at || "")}</td></tr>`).join("")}</tbody></table></div>
          <p class="notice">Chemical identity and properties come from PubChem. They do not show that the compound occurs in any plant.</p>` : ""}
        ${a.publications.length ? `<h2>Publications</h2>${a.publications.map((p) => `<div class="card" style="margin-bottom:6px"><a href="#/publication/${p.id}">${esc(p.title)}</a> <span class="sub">(${esc(p.year)}) PMID ${esc(p.pmid)}</span></div>`).join("")}` : ""}
        ${a.claims.length ? `<h2>Verified claims (${a.claims.length})</h2>${a.claims.map(claimCard).join("")}` : ""}
        ${a.established_findings.length ? `<h2>What the indexed evidence does establish</h2>${a.established_findings.map(claimCard).join("")}` : ""}
        ${a.contradictions.length ? `<h2>Conflicting findings</h2>${a.contradictions.map((c) => `<div class="card" style="margin-bottom:8px">
          <b>${esc(c.compound)}</b> in <i>${esc(c.plant)}</i><div class="contra" style="margin-top:8px">${c.findings.map((f) => `<div class="step">
          <div class="k"><a href="#/evidence/${f.evidence_id}">#${f.evidence_id}</a> · ${esc(f.publication)}</div>
          <div class="v">${f.value_percent != null ? esc(f.value_percent) + " %" : "—"}</div>
          <div class="sub">${esc(f.observation)}<br>origin: ${esc(f.geographic_origin || "not stated")} · part: ${esc(f.plant_part || "not stated")}</div></div>`).join("")}</div>
          <p class="sub">${esc(c.explanation)}</p></div>`).join("")}` : ""}
        ${a.missing_evidence.length ? `<h2>Missing evidence</h2><ul>${a.missing_evidence.map((m) => `<li class="sub">${esc(m)}</li>`).join("")}</ul>` : ""}
        ${rejected.length ? `<h2>Rejected claims (${rejected.length}) — not part of the answer</h2>${rejected.map(claimCard).join("")}` : ""}
        <h2>Limitations</h2><ul>${a.limitations.map((l) => `<li class="sub">${esc(l)}</li>`).join("")}</ul>`;
    } catch (err) { out.innerHTML = `<p class="err">${esc(err.message)}</p>`; }
  };
  document.getElementById("af").onsubmit = (ev) => { ev.preventDefault(); run(document.getElementById("aq").value); };
  document.querySelectorAll("#examples button").forEach((b) => b.onclick = () => { document.getElementById("aq").value = b.textContent; run(b.textContent); });
  document.getElementById("vf").onsubmit = async (ev) => {
    ev.preventDefault();
    const out = document.getElementById("vres");
    const ids = document.getElementById("vids").value.split(/[\s,]+/).filter(Boolean).map(Number);
    try {
      const v = await api("/api/verify-claim", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ claim: document.getElementById("vclaim").value, evidence_ids: ids }) });
      out.innerHTML = claimCard(v);
    } catch (err) {
      let msg = err.message;
      try { const d = JSON.parse(msg); if (d.atomic_claims) msg = "Not atomic. Verify these separately: " + d.atomic_claims.join(" | "); } catch (_) {}
      out.innerHTML = `<p class="err">${esc(msg)}</p>`;
    }
  };
  api("/api/verification/matrix").then((m) => {
    document.getElementById("matrix").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Claim category</th><th>Can be supported by</th><th class="hide-sm">Rule</th></tr></thead><tbody>
      ${m.map((r) => `<tr><td>${catLabel(r.claim_category)}</td><td>${r.answered_from_structured_records ? "PubChem / GBIF record" :
        Object.entries(r.supported_by).map(([e, v]) => `${typeBadge(e)} ${verdictBadge(v)}`).join(" ") || '<span class="err">nothing in MOLECULAB yet</span>'}</td>
        <td class="hide-sm sub">${esc(r.rule)}</td></tr>`).join("")}</tbody></table></div>`;
  }).catch(() => {});
}

// ---------- research intelligence ----------
const evLinks = (ids) => (ids || []).map((i) => `<a href="#/evidence/${i}" class="mono">#${i}</a>`).join(" ");
const KIND_LABEL = { dataset_coverage: "dataset coverage", evidence_quality: "evidence quality", conflict: "conflict" };

function gapCard(g) {
  const aw = `aw-gap-${String(g.id).replace(/[^a-z0-9]/gi, "_")}`;
  addToWs("gap", g.id);
  return `<div class="card gap" style="margin-bottom:8px"><span class="addws" id="${aw}" style="float:right"></span>
    <div class="chips"><span class="kind k-${esc(g.kind)}">${esc(KIND_LABEL[g.kind] || g.kind)}</span>
      <span class="rule">${esc(g.category.replaceAll("_", " "))}</span>
      ${g.plant ? `<a href="#/plant/${g.plant.id}"><i>${esc(g.plant.accepted_name)}</i></a>` : ""}
      ${g.compound ? `· <a href="#/compound/${g.compound.id}">${esc(g.compound.name)}</a>` : ""}
      <span class="badge t-secondary_source_report">${esc(g.certainty.replaceAll("_", " "))}</span></div>
    <div style="margin-top:6px">${esc(g.explanation)}</div>
    ${g.evidence_ids.length ? `<div class="sub">Evidence: ${evLinks(g.evidence_ids)}</div>` : ""}
    ${g.related_compounds.length ? `<div class="sub">Compounds: ${g.related_compounds.map((c) => `<a href="#/compound/${c.id}">${esc(c.name)}</a>`).join(", ")}</div>` : ""}
    <div class="sub"><b>Missing:</b> ${esc(g.missing_information.join("; "))}</div>
    <div class="sub"><b>Next:</b> ${esc(g.suggested_next_investigation)}</div>
    <div class="sub" style="opacity:.8">${esc(g.dataset_limitations)}</div></div>`;
}

function contradictionCard(c) {
  const aw = `aw-contradiction-${String(c.id).replace(/[^a-z0-9]/gi, "_")}`;
  addToWs("contradiction", c.id);
  return `<div class="card" style="margin-bottom:12px"><span class="addws" id="${aw}" style="float:right"></span>
    <h3 style="margin:0">${esc(c.compound.name)} in <i>${esc(c.plant.accepted_name)}</i></h3>
    <div class="chips" style="margin:6px 0"><span class="kind k-conflict">${esc(c.conflict_type)} conflict</span>
      <span class="badge t-tentative_identification">${esc(c.assessment.replaceAll("_", " "))}</span></div>
    <p class="sub">${esc(c.assessment_explanation)}</p>
    <div class="contra">${c.findings.map((f) => `<div class="step"><div class="k"><a href="#/evidence/${f.evidence_id}">#${f.evidence_id}</a>
      · PMID ${esc(f.publication.pmid)} (${esc(f.publication.year)})</div>
      <div class="v">${f.value_percent != null ? esc(f.value_percent) + " %" : "—"}</div>
      <div class="sub">${esc(f.rank_in_source || "")}</div>
      <blockquote>${esc(f.excerpt)}</blockquote>
      <div class="sub">part: ${esc(f.plant_part || "not stated")} · origin: ${esc(f.geographic_origin || "not stated")} · method: ${esc(f.method || "not stated")}</div></div>`).join("")}</div>
    <h2>Context comparison</h2><div class="table-wrap"><table><thead><tr><th>Field</th><th>Values</th><th>Same?</th></tr></thead><tbody>
      ${c.context_comparison.map((x) => `<tr><td>${esc(x.field)}</td><td class="sub">${Object.entries(x.values).map(([k, v]) => `#${esc(k)}: ${esc(v ?? "not stated")}`).join("<br>")}</td>
      <td>${x.same === null ? '<span class="err">unknown</span>' : x.same ? "yes" : "no"}</td></tr>`).join("")}</tbody></table></div>
    <h2>Missing context</h2><ul>${c.missing_context.map((m) => `<li class="sub">${esc(m)}</li>`).join("")}</ul>
    <p class="notice">${esc(c.interpretation_note)}</p></div>`;
}

function hypothesisCard(h) {
  return `<div class="hyp"><div class="label">Unvalidated research hypothesis · ${esc(h.category.replaceAll("_", " "))}</div>
    <p style="margin:6px 0"><b>Research question:</b> ${esc(h.research_question)}</p>
    <p class="answer" style="margin:6px 0">${esc(h.hypothesis)}</p>
    <div class="sub"><b>Reasoning (from documented evidence):</b><ul>${h.reasoning.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>
    <div class="sub"><b>Supporting evidence:</b> ${evLinks(h.supporting_evidence_ids)}</div>
    <details><summary>Source excerpts</summary>${h.supporting_excerpts.map((x) => `<div style="margin-top:6px"><a href="#/evidence/${x.evidence_id}" class="mono">#${x.evidence_id}</a>
      <span class="sub">PMID ${esc(x.publication.pmid)} · ${esc(x.publication.title)}</span><blockquote>${esc(x.excerpt)}</blockquote></div>`).join("")}</details>
    <div class="sub"><b>Missing evidence:</b> ${esc(h.missing_evidence.join("; "))}</div>
    <div class="sub"><b>Suggested validation:</b><ul>${h.suggested_validation.map((v) => `<li>${esc(v)}</li>`).join("")}</ul></div>
    <div class="sub"><b>Limitations and alternatives:</b><ul>${h.limitations.map((v) => `<li>${esc(v)}</li>`).join("")}</ul></div></div>`;
}

async function researchPage(tab, params) {
  tab = tab || "gaps";
  const tabs = [["gaps", "Research gaps"], ["contradictions", "Contradictions"], ["hypotheses", "Hypothesis explorer"], ["priorities", "Research priorities"]];
  const head = `<h1>Research intelligence</h1>
    <p class="sub">What the indexed evidence establishes, where it is incomplete or conflicting, and which questions deserve investigation. Everything is computed from the database and links to its evidence. "Not indexed" never means "not studied".</p>
    <div class="tabs">${tabs.map(([k, l]) => `<a href="#/research/${k}" class="${k === tab ? "on" : ""}">${l}</a>`).join("")}</div>`;
  const plants = await api("/api/plants");
  if (tab === "gaps") {
    const qs = new URLSearchParams([...params].filter(([, v]) => v));
    const pid = params.get("plant_id");
    const data = await api(pid ? `/api/research/plants/${pid}/gaps?limit=500` : `/api/research/gaps?limit=500&${qs}`);
    view.innerHTML = head + `<form id="gf2" class="filters"><select name="plant_id"><option value="">All plants</option>${plants.map((p) =>
        `<option value="${p.id}" ${String(pid) === String(p.id) ? "selected" : ""}>${esc(p.accepted_name)}</option>`).join("")}</select>
      <select name="kind"><option value="">All kinds</option>${Object.entries(KIND_LABEL).map(([k, l]) => `<option value="${k}" ${params.get("kind") === k ? "selected" : ""}>${l}</option>`).join("")}</select>
      <button class="primary" type="submit">Filter</button></form>
      <p class="sub">${data.total} gaps · ${esc(data.note)}</p>
      ${(pid && params.get("kind") ? data.items.filter((g) => g.kind === params.get("kind")) : data.items).map(gapCard).join("") || '<p class="empty">No gaps for this selection. That reflects the indexed data only.</p>'}`;
    document.getElementById("gf2").onsubmit = (ev) => { ev.preventDefault(); location.hash = "#/research/gaps?" + new URLSearchParams([...new FormData(ev.target)].filter(([, v]) => v)); };
  } else if (tab === "contradictions") {
    const list = await api("/api/research/contradictions");
    view.innerHTML = head + (list.length ? list.map(contradictionCard).join("") : '<p class="empty">No conflicting findings were detected in the indexed records.</p>');
  } else if (tab === "hypotheses") {
    const compounds = await api("/api/compounds");
    view.innerHTML = head + `<form id="hf" class="filters">
      <select name="plant_id"><option value="">Plant…</option>${plants.map((p) => `<option value="${p.id}">${esc(p.accepted_name)}</option>`).join("")}</select>
      <select name="compound_id"><option value="">Compound…</option>${compounds.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select>
      <input name="question" placeholder="or a research question, e.g. Does glabridin have antioxidant activity?" style="flex:1;min-width:220px">
      <button class="primary" type="submit">Explore</button></form><div id="hres"><p class="sub">Select a plant, a compound or ask a question.</p></div>`;
    document.getElementById("hf").onsubmit = async (ev) => {
      ev.preventDefault();
      const f = Object.fromEntries([...new FormData(ev.target)].filter(([, v]) => v));
      if (f.plant_id) f.plant_id = Number(f.plant_id);
      if (f.compound_id) f.compound_id = Number(f.compound_id);
      const out = document.getElementById("hres");
      out.innerHTML = '<p class="empty">Retrieving and verifying evidence…</p>';
      try {
        const r = await api("/api/research/hypotheses", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(f) });
        const qa = r.question_assessment;
        out.innerHTML = `
          ${qa ? `<div class="card"><div class="chips"><span class="status ${esc(qa.status)}">${esc(qa.status.replaceAll("_", " "))}</span>
            <span class="badge t-secondary_source_report">question type: ${esc(qa.question_type.replaceAll("_", " "))}</span></div>
            <p class="sub">${esc(qa.reason)}</p></div>` : ""}
          ${r.abstentions.length ? `<h2>Abstentions</h2>${r.abstentions.map((a) => `<p class="notice warn">${esc(a)}</p>`).join("")}` : ""}
          <h2>Documented observations (${r.documented_observations.length}) — verified against source excerpts</h2>
          ${r.documented_observations.map((o) => `<div class="fact">${esc(o.statement)} ${verdictBadge(o.status)} ${evLinks(o.evidence_ids)}</div>`).join("") || '<p class="empty">None.</p>'}
          ${r.unverified_records.length ? `<h2>Records excluded (failed verification)</h2>${r.unverified_records.map((o) => `<div class="claim err">${esc(o.statement)} ${evLinks(o.evidence_ids)} — ${esc(o.explanation)}</div>`).join("")}` : ""}
          <h2>Hypotheses (${r.hypotheses.length})</h2><p class="sub">${esc(r.status_note)}</p>
          ${r.hypotheses.map(hypothesisCard).join("") || '<p class="empty">No hypothesis generated.</p>'}
          ${r.rejected_hypotheses.length ? `<h2>Rejected hypothesis drafts</h2>${r.rejected_hypotheses.map((h) => `<div class="claim err">${esc(h.hypothesis)} — ${esc(h.reason)}</div>`).join("")}` : ""}
          ${r.gaps.length ? `<h2>Related gaps</h2>${r.gaps.map(gapCard).join("")}` : ""}`;
      } catch (err) { out.innerHTML = `<p class="err">${esc(err.message)}</p>`; }
    };
  } else if (tab === "priorities") {
    const kind = params.get("kind") || "plant";
    const pr = await api(`/api/research/priorities?kind=${kind}`);
    view.innerHTML = head + `<div class="filters"><a class="badge ${kind === "plant" ? "t-direct_experimental_identification" : ""}" href="#/research/priorities?kind=plant">Plants</a>
      <a class="badge ${kind === "compound" ? "t-direct_experimental_identification" : ""}" href="#/research/priorities?kind=compound">Compounds</a></div>
      <p class="notice">${esc(pr.ordering)} ${esc(pr.disclaimer)}</p>
      ${pr.items.map((i) => `<div class="card" style="margin-bottom:10px">
        <h3 style="margin:0"><a href="#/${i.type}/${i.id}">${i.type === "plant" ? `<i>${esc(i.name)}</i>` : esc(i.name)}</a>
          <span class="sub">· ${i.open_gaps} open gaps · <a href="#/research/gaps?plant_id=${i.type === "plant" ? i.id : ""}">view</a></span></h3>
        ${i.investigation_signals.length ? `<div class="sub" style="margin-top:6px"><b>Investigation signals:</b><ul>${i.investigation_signals.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : `<div class="sub">${esc(i.note || "No investigation signal fired.")}</div>`}
        <details><summary>Dimensions and how each is calculated</summary><dl class="dim">${Object.entries(i.dimensions).map(([k, d]) => `<dt>${esc(k.replaceAll("_", " "))}</dt>
          <dd>${d.available ? `<b>${esc(typeof d.value === "object" ? Object.entries(d.value).map(([a, b]) => `${a.replaceAll("_", " ")}: ${b}`).join(", ") : d.value)}</b>` : '<span class="err">unavailable</span>'}
          <div class="sub">${esc(d.how)}</div></dd>`).join("")}</dl>
          <div class="sub">Evidence: ${evLinks(i.evidence_ids)}</div></details></div>`).join("")}`;
  }
}

// ---------- research workspace (v0.5) ----------
const WS = "/api/workspaces";
const getToken = () => { try { return sessionStorage.getItem("moleculab_researcher_token") || ""; } catch (_) { return ""; } };
function wsapi(path, opts = {}) {
  const t = getToken();
  const headers = { ...(opts.headers || {}) };
  if (t) headers.Authorization = `Bearer ${t}`;
  if (opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  return api(path, { ...opts, headers });
}
async function wsSend(path, method, body) {
  const t = getToken();
  const res = await fetch(path, { method, headers: { ...(t ? { Authorization: `Bearer ${t}` } : {}), ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined });
  if (res.status === 204) return null;
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j));
  return j;
}
const STATUSES = ["planning", "active", "under_review", "completed", "archived"];
const HSTATUS = ["proposed", "under_investigation", "supported_by_preliminary_evidence", "contradicted", "requires_experimental_validation", "abandoned"];
const CAUSAL = ["not_applicable", "untested", "supported_by_compound_level_experiment", "not_supported_by_compound_level_experiment"];
const NOTECATS = ["observation", "question", "methodology", "interpretation", "limitation", "decision"];
const RTYPES = ["plant", "compound", "publication", "evidence", "contradiction", "gap"];
const opt = (vals, cur) => vals.map((v) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(String(v).replaceAll("_", " "))}</option>`).join("");
const wsLink = (r) => r.link ? `<a href="${esc(r.link)}">${esc(r.label)}</a>` : esc(r.label);

// "Add to workspace" control used on scientific pages
let _wsCache = null;
async function addToWs(type, ref) {
  const id = `aw-${type}-${String(ref).replace(/[^a-z0-9]/gi, "_")}`;
  setTimeout(async () => {
    const el = document.getElementById(id);
    if (!el) return;
    try {
      _wsCache = _wsCache || (await wsapi(`${WS}?include_archived=false`));
      if (!_wsCache.length) { el.innerHTML = `<a class="sub" href="#/workspaces">Create a workspace to collect this</a>`; return; }
      el.innerHTML = `<select>${_wsCache.map((w) => `<option value="${w.id}">${esc(w.title.slice(0, 40))}</option>`).join("")}</select><button type="button">Add to workspace</button><span class="sub"></span>`;
      el.querySelector("button").onclick = async () => {
        const msg = el.querySelector("span");
        try {
          const r = await wsSend(`${WS}/${el.querySelector("select").value}/resources`, "POST", { resource_type: type, resource_ref: String(ref) });
          msg.textContent = r.created ? " added" : " already collected";
        } catch (err) { msg.textContent = " " + err.message; }
      };
    } catch (err) { el.innerHTML = `<span class="sub">Workspaces unavailable: ${esc(err.message)}</span>`; }
  }, 0);
  return `<span class="addws" id="${id}"></span>`;
}

async function workspacesPage() {
  let access;
  try { access = await wsapi(`${WS}/access`); } catch (err) {
    view.innerHTML = `<h1>Research workspaces</h1><p class="notice warn">${esc(err.message)}</p>
      <form id="tokf" class="filters"><input id="tok" type="password" placeholder="Researcher token" style="flex:1"><button class="primary">Use token</button></form>`;
    document.getElementById("tokf").onsubmit = (ev) => { ev.preventDefault(); try { sessionStorage.setItem("moleculab_researcher_token", document.getElementById("tok").value); } catch (_) {} route(); };
    return;
  }
  const list = await wsapi(WS);
  _wsCache = null;
  view.innerHTML = `<h1>Research workspaces</h1>
    <p class="notice ${access.private ? "" : "warn"}">Access: <b>${esc(access.mode.replaceAll("_", " "))}</b> as ${esc(access.researcher)}. ${esc(access.note)}</p>
    <h2>Investigations (${list.length})</h2>
    <div class="grid">${list.map((w) => `<div class="card"><div class="chips"><span class="ws-status ${esc(w.status)}">${esc(w.status.replaceAll("_", " "))}</span></div>
      <h3 style="margin-top:6px"><a href="#/workspace/${w.id}">${esc(w.title)}</a></h3><div class="sub">${esc(w.research_question)}</div>
      <div class="sub" style="margin-top:6px">${Object.entries(w.resource_counts).map(([k, n]) => `${n} ${esc(k)}`).join(" · ") || "no resources yet"}
        · ${w.hypotheses} hypotheses · ${w.notes} notes · <b>${w.outstanding_tasks}</b> open tasks</div></div>`).join("") || '<p class="empty">No investigations yet.</p>'}</div>
    <h2>New investigation</h2>
    <form id="wsf" class="card" style="display:grid;gap:8px">
      <input name="title" placeholder="Title, e.g. Chemical composition and research gaps in Capparis spinosa" required minlength="3">
      <textarea name="research_question" placeholder="Research question" required minlength="10"></textarea>
      <div class="filters"><input name="research_domain" placeholder="Domain (e.g. phytochemistry)"><input name="scope" placeholder="Scope" style="flex:1">
        <select name="status">${opt(STATUSES.slice(0, 4), "planning")}</select></div>
      <textarea name="description" placeholder="Description (optional)"></textarea>
      <div><button class="primary" type="submit">Create investigation</button> <span id="wsmsg" class="err"></span></div></form>`;
  document.getElementById("wsf").onsubmit = async (ev) => {
    ev.preventDefault();
    const f = Object.fromEntries([...new FormData(ev.target)].filter(([, v]) => v));
    try { const w = await wsSend(WS, "POST", f); location.hash = `#/workspace/${w.id}`; }
    catch (err) { document.getElementById("wsmsg").textContent = err.message; }
  };
}

async function workspacePage(id, tab) {
  tab = tab || "overview";
  const w = await wsapi(`${WS}/${id}`);
  const archived = w.status === "archived";
  const tabs = [["overview", "Overview"], ["collection", "Evidence collection"], ["hypotheses", "Hypothesis board"], ["notes", "Notes"],
    ["tasks", "Tasks"], ["timeline", "Timeline"], ["reports", "Reports"]];
  const head = `<div class="section-label">Research workspace</div>
    <h1>${esc(w.title)} <span class="ws-status ${esc(w.status)}">${esc(w.status.replaceAll("_", " "))}</span></h1>
    <p class="answer" style="margin:4px 0">${esc(w.research_question)}</p>
    ${archived ? '<p class="notice warn">Archived: read-only. Reopen it to make changes.</p>' : ""}
    <div class="tabs">${tabs.map(([k, l]) => `<a href="#/workspace/${id}/${k}" class="${k === tab ? "on" : ""}">${l}</a>`).join("")}</div>`;
  const run = async (fn) => { try { await fn(); workspacePage(id, tab); } catch (err) { alert(err.message); } };
  if (tab === "overview") {
    const [plants, tasks] = await Promise.all([api("/api/plants"), wsapi(`${WS}/${id}/tasks?outstanding=true`)]);
    view.innerHTML = head + `<div class="two"><div>
        <div class="card"><dl class="kv"><dt>Domain</dt><dd>${esc(w.research_domain || "—")}</dd><dt>Scope</dt><dd>${esc(w.scope || "—")}</dd>
          <dt>Description</dt><dd>${esc(w.description || "—")}</dd><dt>Created</dt><dd class="mono">${esc(w.created_at.slice(0, 16))}</dd>
          <dt>Collected</dt><dd>${Object.entries(w.resource_counts).map(([k, n]) => `${n} ${esc(k)}`).join(" · ") || "nothing yet"}</dd>
          <dt>Hypotheses / notes / reports</dt><dd>${w.hypotheses} / ${w.notes} / ${w.reports}</dd></dl></div>
        ${archived ? "" : `<h2>Collect a plant and its related records</h2><form id="cpf" class="inline-form"><select name="plant_id">${plants.map((p) => `<option value="${p.id}">${esc(p.accepted_name)}</option>`).join("")}</select>
          <label class="sub"><input type="checkbox" name="compounds" checked> compounds</label><label class="sub"><input type="checkbox" name="publications" checked> publications</label>
          <label class="sub"><input type="checkbox" name="evidence"> evidence records</label><button class="primary">Collect</button></form>
          <p class="sub">Only existing records linked to the plant are added; nothing is created or copied.</p>`}
        <h2>Outstanding tasks (${tasks.length})</h2>${tasks.map((t) => `<div class="fact">[${esc(t.priority)}] ${esc(t.title)} <span class="sub">${esc(t.status)}${t.due_date ? " · due " + esc(t.due_date) : ""}</span></div>`).join("") || '<p class="empty">None.</p>'}
      </div><div>
        <h2 style="margin-top:0">Status</h2><div class="inline-form">${archived ? `<button id="reopen" class="primary">Reopen</button><button id="del">Delete permanently</button>`
          : `<select id="st">${opt(STATUSES.slice(0, 4), w.status)}</select><button id="stb">Update</button><button id="arch">Archive</button>`}</div>
        <h2>Recent activity</h2><ul class="activity">${w.recent_activity.map((a) => `<li><span class="mono sub">${esc(a.at.slice(0, 16).replace("T", " "))}</span> ${esc(a.action.replaceAll("_", " "))} · ${esc(a.detail.slice(0, 90))}</li>`).join("")}</ul>
      </div></div>`;
    const cpf = document.getElementById("cpf");
    if (cpf) cpf.onsubmit = (ev) => { ev.preventDefault(); const f = new FormData(ev.target);
      run(() => wsSend(`${WS}/${id}/collect-plant`, "POST", { plant_id: Number(f.get("plant_id")), include: ["compounds", "publications", "evidence"].filter((k) => f.get(k)) })); };
    const b = (i, fn) => { const e = document.getElementById(i); if (e) e.onclick = fn; };
    b("stb", () => run(() => wsSend(`${WS}/${id}`, "PATCH", { status: document.getElementById("st").value })));
    b("arch", () => run(() => wsSend(`${WS}/${id}/archive`, "POST")));
    b("reopen", () => run(() => wsSend(`${WS}/${id}/reopen`, "POST")));
    b("del", async () => { if (!confirm("Delete this investigation and its notes, tasks, hypotheses and reports? Shared scientific records are not affected.")) return;
      try { await wsSend(`${WS}/${id}`, "DELETE"); location.hash = "#/workspaces"; } catch (err) { alert(err.message); } });
  } else if (tab === "collection") {
    const res = await wsapi(`${WS}/${id}/resources`);
    view.innerHTML = head + `<p class="sub">Collected items reference existing MOLECULAB records. Notes on them are researcher-authored. Use “Add to workspace” on plant, compound, publication, evidence, gap and contradiction pages to collect more.</p>
      <div class="inline-form"><input id="rq" placeholder="Search within the collection" style="flex:1"><select id="rt"><option value="">All types</option>${opt(RTYPES)}</select></div>
      <div id="rlist"></div>`;
    const draw = () => {
      const q = document.getElementById("rq").value.toLowerCase(), t = document.getElementById("rt").value;
      const rows = res.filter((r) => (!t || r.resource_type === t) && (!q || r.label.toLowerCase().includes(q) || (r.researcher_note || "").toLowerCase().includes(q)));
      document.getElementById("rlist").innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>Type</th><th>Resource</th><th class="hide-sm">Researcher note</th><th></th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td><span class="badge">${esc(r.resource_type)}</span></td><td>${wsLink(r)}${r.present_in_current_data ? "" : ' <span class="err">(no longer in current data; snapshot kept)</span>'}
          ${r.snapshot && r.snapshot.explanation ? `<div class="sub">${esc(r.snapshot.explanation)}</div>` : ""}${r.snapshot && r.snapshot.assessment ? `<div class="sub">assessment: ${esc(r.snapshot.assessment)}</div>` : ""}</td>
          <td class="hide-sm">${r.researcher_note ? `<div class="authored"><div class="who">researcher note</div>${esc(r.researcher_note)}</div>` : ""}
          ${archived ? "" : `<button data-note="${r.id}" class="sub">${r.researcher_note ? "Edit" : "Add"} note</button>`}</td>
          <td>${archived ? "" : `<button data-rm="${r.id}">Remove</button>`}</td></tr>`).join("")}</tbody></table></div>` : '<p class="empty">Nothing collected for this filter.</p>';
      document.querySelectorAll("[data-rm]").forEach((x) => x.onclick = () => run(() => wsSend(`${WS}/${id}/resources/${x.dataset.rm}`, "DELETE")));
      document.querySelectorAll("[data-note]").forEach((x) => x.onclick = () => { const r = res.find((y) => String(y.id) === x.dataset.note);
        const v = prompt("Researcher note (stored as researcher-authored, not verified evidence):", r.researcher_note || ""); if (v !== null) run(() => wsSend(`${WS}/${id}/resources/${r.id}`, "PATCH", { researcher_note: v || null })); });
    };
    document.getElementById("rq").oninput = draw; document.getElementById("rt").onchange = draw; draw();
  } else if (tab === "hypotheses") {
    const hyps = await wsapi(`${WS}/${id}/hypotheses`);
    view.innerHTML = head + `<p class="notice">Statuses record investigation progress, not proof. A hypothesis never becomes an established finding because it has citations. Causal attribution can be marked “supported” only with a linked compound-level experimental record.</p>
      ${archived ? "" : `<div class="inline-form"><button id="gen" class="primary">Generate from collected plants</button><span class="sub">Runs the MOLECULAB hypothesis generator; nothing is saved until you choose.</span></div><div id="cands"></div>
      <h2>Record a researcher hypothesis</h2><form id="rhf" class="card" style="display:grid;gap:6px"><textarea name="statement" placeholder="Hypothesis statement" required minlength="10"></textarea>
        <div class="filters"><input name="supporting" placeholder="Supporting evidence IDs, e.g. 9"><input name="contradicting" placeholder="Contradicting evidence IDs">
        <select name="causal_attribution">${opt(CAUSAL, "not_applicable")}</select></div>
        <input name="alternatives" placeholder="Alternative explanations (separate with ;)"><input name="validation" placeholder="Proposed validation methods (separate with ;)">
        <div><button class="primary">Record hypothesis</button> <span id="rhmsg" class="err"></span></div></form>`}
      <h2>Hypothesis board (${hyps.length})</h2><div class="board">${hyps.map((h) => `<div class="hyp"><div class="label">${esc(h.origin)} · unvalidated · H${h.id}</div>
        <p style="margin:6px 0">${esc(h.statement)}</p>
        ${h.unresolved_questions.map((u) => `<p class="notice warn" style="margin:4px 0">${esc(u)}</p>`).join("")}
        <div class="sub">Supporting: ${evLinks(h.supporting_evidence_ids) || "none"} · Contradicting: ${evLinks(h.contradicting_evidence_ids) || "none"}</div>
        ${h.alternative_explanations.length ? `<div class="sub"><b>Alternatives:</b> ${esc(h.alternative_explanations.join("; "))}</div>` : ""}
        ${h.validation_methods.length ? `<div class="sub"><b>Validation:</b><ul>${h.validation_methods.map((v) => `<li>${esc(v)}</li>`).join("")}</ul></div>` : ""}
        ${archived ? `<div class="sub">status: ${esc(h.status)} · causal: ${esc(h.causal_attribution)}</div>` : `<div class="inline-form">
          <select data-hs="${h.id}">${opt(HSTATUS, h.status)}</select><select data-hc="${h.id}">${opt(CAUSAL, h.causal_attribution)}</select>
          <input data-hsup="${h.id}" placeholder="supporting IDs" value="${esc(h.supporting_evidence_ids.join(", "))}" style="width:110px">
          <input data-hcon="${h.id}" placeholder="contradicting IDs" value="${esc(h.contradicting_evidence_ids.join(", "))}" style="width:110px">
          <button data-hsave="${h.id}">Save</button></div><span class="err" id="hmsg${h.id}"></span>`}</div>`).join("") || '<p class="empty">No hypotheses yet.</p>'}</div>`;
    const ids = (v) => String(v || "").split(/[\s,;]+/).filter(Boolean).map(Number);
    document.querySelectorAll("[data-hsave]").forEach((b) => b.onclick = async () => { const hid = b.dataset.hsave;
      try { await wsSend(`${WS}/${id}/hypotheses/${hid}`, "PATCH", { status: document.querySelector(`[data-hs="${hid}"]`).value,
        causal_attribution: document.querySelector(`[data-hc="${hid}"]`).value, supporting_evidence_ids: ids(document.querySelector(`[data-hsup="${hid}"]`).value),
        contradicting_evidence_ids: ids(document.querySelector(`[data-hcon="${hid}"]`).value) }); workspacePage(id, tab);
      } catch (err) { document.getElementById(`hmsg${hid}`).textContent = err.message; } });
    const gen = document.getElementById("gen");
    if (gen) gen.onclick = async () => { const out = document.getElementById("cands"); out.innerHTML = '<p class="empty">Generating…</p>';
      try { const runs = await wsSend(`${WS}/${id}/hypotheses/generate`, "POST", {});
        out.innerHTML = runs.map((r, ri) => `${r.abstentions.map((a) => `<p class="notice warn">${esc(a)}</p>`).join("")}${r.hypotheses.map((h, hi) => `<div class="hyp"><div class="label">candidate · ${esc(h.category.replaceAll("_", " "))}</div>
          <p>${esc(h.hypothesis)}</p><div class="sub">Evidence: ${evLinks(h.supporting_evidence_ids)}</div><button data-save="${ri}:${hi}" class="primary">Save to board</button></div>`).join("")}`).join("") || '<p class="empty">No candidates.</p>';
        out.querySelectorAll("[data-save]").forEach((b) => b.onclick = () => { const [ri, hi] = b.dataset.save.split(":").map(Number);
          run(() => wsSend(`${WS}/${id}/hypotheses`, "POST", { origin: "generated", statement: runs[ri].hypotheses[hi].hypothesis, source: runs[ri].source })); });
      } catch (err) { out.innerHTML = `<p class="err">${esc(err.message)}</p>`; } };
    const rhf = document.getElementById("rhf");
    if (rhf) rhf.onsubmit = async (ev) => { ev.preventDefault(); const f = Object.fromEntries(new FormData(ev.target));
      const split = (v) => String(v || "").split(";").map((x) => x.trim()).filter(Boolean);
      try { await wsSend(`${WS}/${id}/hypotheses`, "POST", { origin: "researcher", statement: f.statement, supporting_evidence_ids: ids(f.supporting),
        contradicting_evidence_ids: ids(f.contradicting), causal_attribution: f.causal_attribution, alternative_explanations: split(f.alternatives), validation_methods: split(f.validation) });
        workspacePage(id, tab); } catch (err) { document.getElementById("rhmsg").textContent = err.message; } };
  } else if (tab === "notes") {
    const notes = await wsapi(`${WS}/${id}/notes`);
    view.innerHTML = head + `<p class="notice">Notes are researcher-authored. They are never treated or reported as verified evidence.</p>
      ${archived ? "" : `<form id="nf" class="card" style="display:grid;gap:6px"><div class="filters"><select name="category">${opt(NOTECATS, "observation")}</select>
        <select name="resource_type"><option value="">No linked resource</option>${opt(RTYPES)}</select><input name="resource_ref" placeholder="Resource id (e.g. evidence 9)"></div>
        <textarea name="body" placeholder="Note" required></textarea><div><button class="primary">Save note</button> <span id="nmsg" class="err"></span></div></form>`}
      <h2>Notes (${notes.length})</h2>${notes.map((n) => `<div class="authored"><div class="who">researcher note · ${esc(n.category)} · ${esc(n.author)} · ${esc(n.updated_at.slice(0, 16).replace("T", " "))}</div>
        <div>${esc(n.body)}</div>${n.resource_type ? `<div class="sub">re: ${esc(n.resource_type)} ${esc(n.resource_ref)}</div>` : ""}
        ${archived ? "" : `<div class="inline-form"><button data-ne="${n.id}">Edit</button><button data-nd="${n.id}">Delete</button></div>`}</div>`).join("") || '<p class="empty">No notes yet.</p>'}`;
    const nf = document.getElementById("nf");
    if (nf) nf.onsubmit = async (ev) => { ev.preventDefault(); const f = Object.fromEntries([...new FormData(ev.target)].filter(([, v]) => v));
      try { await wsSend(`${WS}/${id}/notes`, "POST", f); workspacePage(id, tab); } catch (err) { document.getElementById("nmsg").textContent = err.message; } };
    document.querySelectorAll("[data-nd]").forEach((b) => b.onclick = () => run(() => wsSend(`${WS}/${id}/notes/${b.dataset.nd}`, "DELETE")));
    document.querySelectorAll("[data-ne]").forEach((b) => b.onclick = () => { const n = notes.find((x) => String(x.id) === b.dataset.ne);
      const v = prompt("Edit note:", n.body); if (v) run(() => wsSend(`${WS}/${id}/notes/${n.id}`, "PATCH", { body: v })); });
  } else if (tab === "tasks") {
    const tasks = await wsapi(`${WS}/${id}/tasks`);
    const ord = { open: 0, in_progress: 1, blocked: 2, done: 3 };
    tasks.sort((a, b) => ord[a.status] - ord[b.status]);
    view.innerHTML = head + `${archived ? "" : `<form id="tf" class="card" style="display:grid;gap:6px"><input name="title" placeholder="Task, e.g. Search toxicology literature for Capparis spinosa" required minlength="3">
        <div class="filters"><select name="priority">${opt(["low", "medium", "high"], "medium")}</select><input type="date" name="due_date">
        <select name="rtype"><option value="">No related resource</option>${opt(RTYPES)}</select><input name="rref" placeholder="Resource id"></div>
        <textarea name="description" placeholder="Description (optional)"></textarea><div><button class="primary">Add task</button> <span id="tmsg" class="err"></span></div></form>`}
      <h2>Tasks (${tasks.filter((t) => t.status !== "done").length} outstanding)</h2>
      <div class="table-wrap"><table><thead><tr><th>Task</th><th>Priority</th><th class="hide-sm">Due</th><th>Status</th></tr></thead><tbody>
      ${tasks.map((t) => `<tr><td>${t.status === "done" ? `<s>${esc(t.title)}</s>` : esc(t.title)}${t.description ? `<div class="sub">${esc(t.description)}</div>` : ""}
        ${t.related_resources.map((r) => `<div class="sub">→ ${esc(r.resource_type)} ${esc(r.resource_ref)}</div>`).join("")}</td>
        <td>${esc(t.priority)}</td><td class="hide-sm mono">${esc(t.due_date || "")}</td>
        <td>${archived ? esc(t.status) : `<select data-ts="${t.id}">${opt(["open", "in_progress", "blocked", "done"], t.status)}</select>`}</td></tr>`).join("") || '<tr><td colspan="4" class="empty">No tasks.</td></tr>'}</tbody></table></div>`;
    const tf = document.getElementById("tf");
    if (tf) tf.onsubmit = async (ev) => { ev.preventDefault(); const f = Object.fromEntries([...new FormData(ev.target)].filter(([, v]) => v));
      const body = { title: f.title, priority: f.priority, description: f.description, due_date: f.due_date,
        related_resources: f.rtype && f.rref ? [{ resource_type: f.rtype, resource_ref: f.rref }] : [] };
      try { await wsSend(`${WS}/${id}/tasks`, "POST", body); workspacePage(id, tab); } catch (err) { document.getElementById("tmsg").textContent = err.message; } };
    document.querySelectorAll("[data-ts]").forEach((x) => x.onchange = () => run(() => wsSend(`${WS}/${id}/tasks/${x.dataset.ts}`, "PATCH", { status: x.value })));
  } else if (tab === "timeline") {
    const tl = await wsapi(`${WS}/${id}/timeline`);
    view.innerHTML = head + `<p class="sub">Every entry was recorded when the action happened; nothing is reconstructed.</p>
      <div class="table-wrap"><table><thead><tr><th>When (UTC)</th><th>Action</th><th>Detail</th></tr></thead><tbody>
      ${tl.map((a) => `<tr><td class="mono">${esc(a.at.slice(0, 19).replace("T", " "))}</td><td>${esc(a.action.replaceAll("_", " "))}</td><td class="sub">${esc(a.detail)}</td></tr>`).join("")}</tbody></table></div>`;
  } else if (tab === "reports") {
    const reps = await wsapi(`${WS}/${id}/reports`);
    view.innerHTML = head + `<p class="sub">Reports compile the collected evidence (re-verified), every contradiction and gap in scope, hypotheses (labelled unvalidated), researcher notes (labelled researcher-authored), tasks and limitations. Each stored report is immutable and carries a dataset fingerprint.</p>
      <div class="inline-form"><button id="genr" class="primary">Generate report</button></div>
      <div class="table-wrap"><table><thead><tr><th>Report</th><th>Generated</th><th>Contents</th><th></th></tr></thead><tbody>
      ${reps.map((r) => `<tr><td>#${r.id}</td><td class="mono">${esc(r.created_at.slice(0, 16).replace("T", " "))}</td>
        <td class="sub">${r.summary.verified_observations} observations · ${r.summary.contradictions} contradictions · ${r.summary.gaps} gaps · ${r.summary.hypotheses} hypotheses · ${r.summary.notes} notes</td>
        <td><button data-prev="${r.id}">Preview</button> <button data-exp="${r.id}">Export .md</button></td></tr>`).join("") || '<tr><td colspan="4" class="empty">No reports yet.</td></tr>'}</tbody></table></div>
      <div id="rprev"></div>`;
    document.getElementById("genr").onclick = () => run(() => wsSend(`${WS}/${id}/reports`, "POST"));
    document.querySelectorAll("[data-prev]").forEach((b) => b.onclick = async () => { const r = await wsapi(`${WS}/${id}/reports/${b.dataset.prev}`);
      document.getElementById("rprev").innerHTML = `<h2>Report #${r.id} (Markdown source)</h2><div class="sub">SHA-256 ${esc(r.dataset_fingerprint.content_sha256)}</div><pre class="report">${esc(r.markdown)}</pre>`; });
    document.querySelectorAll("[data-exp]").forEach((b) => b.onclick = async () => {
      const t = getToken(); const res = await fetch(`${WS}/${id}/reports/${b.dataset.exp}/export`, { headers: t ? { Authorization: `Bearer ${t}` } : {} });
      if (!res.ok) { alert("Export failed"); return; }
      const a = document.createElement("a"); a.href = URL.createObjectURL(await res.blob());
      a.download = (res.headers.get("content-disposition") || "").split('filename="')[1]?.replace('"', "") || "report.md"; a.click(); });
  }
}


// A view module may not be present yet (workstreams land independently). Report that honestly
// rather than throwing an opaque error.
async function viewOrUnavailable(fnName, label, params) {
  const fn = typeof window !== "undefined" ? window[fnName] : undefined;
  const resolved = (typeof fn === "function") ? fn
    : (typeof globalThis !== "undefined" && typeof globalThis[fnName] === "function"
       ? globalThis[fnName] : null);
  if (!resolved) {
    view.innerHTML = (typeof unavailableState === "function")
      ? unavailableState(label + " is not installed",
          "This module's script (" + fnName + ") is not present in this build. The rest of "
          + "MOLECULAB is unaffected.")
      : '<p class="err">' + esc(label) + " module not installed.</p>";
    return;
  }
  await resolved(params);
}

// ---------- router ----------
async function route() {
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const [section, id] = path.split("/");
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === section));
  // Workstation pages opt in by adding .workstation to #view; reset it on every navigation so a
  // full-height, non-scrolling layout cannot leak into an ordinary document-flow page.
  view.className = "";
  window.scrollTo(0, 0);
  try {
    // The Research Bench is the product's front door. The former landing page remains
    // reachable at #/overview for anyone who wants the corpus-wide summary.
    if (!section) await viewOrUnavailable("landingPage", "Welcome", new URLSearchParams(query));
    else if (section === "overview") await researchOverview();
    else if (section === "legacy-dashboard") await dashboard();
    else if (section === "plants") await plantsList();
    else if (section === "plant") await plantPage(id);
    else if (section === "compound") await compoundPage(id);
    else if (section === "publications") await publicationsList();
    else if (section === "publication") await publicationPage(id);
    else if (section === "evidence" && id) await evidencePage(id);
    else if (section === "evidence") await evidenceExplorer(new URLSearchParams(query));
    else if (section === "graph") await graphPage(new URLSearchParams(query));
    else if (section === "research") await researchPage(id, new URLSearchParams(query));
    else if (section === "workspaces") await workspacesPage();
    else if (section === "workspace") await workspacePage(id, path.split("/")[2]);
    else if (section === "search") await searchPage(new URLSearchParams(query).get("q") || "");
    else if (section === "assistant") await assistantPage();
    else if (section === "studio") await molecularStudio(new URLSearchParams(query));
    else if (section === "simulations") await simulationsPage(new URLSearchParams(query));
    else if (section === "formulations") await formulationsPage(new URLSearchParams(query));
    else if (section === "bench") await viewOrUnavailable("benchPage", "Research Bench", new URLSearchParams(query));
    else if (section === "playground") await viewOrUnavailable("playgroundPage", "Molecular Playground", new URLSearchParams(query));
    else if (section === "dosing") await viewOrUnavailable("dosingPage", "Dosing Lab", new URLSearchParams(query));
    else if (section === "anatomy") await viewOrUnavailable("anatomyPage", "Body Distribution", new URLSearchParams(query));
    else if (section === "screening") await viewOrUnavailable("screeningPage", "Virtual Screening", new URLSearchParams(query));
    else if (section === "repurposing") await viewOrUnavailable("repurposingPage", "Drug Repurposing", new URLSearchParams(query));
    else if (section === "workbench") await viewOrUnavailable("workbenchPage", "Research Workbench", new URLSearchParams(query));
    else if (section === "materials") await viewOrUnavailable("materialsPage", "Material Explorer", new URLSearchParams(query));
    else if (section === "conditions") await viewOrUnavailable("conditionsPage", "Conditions Explorer", new URLSearchParams(query));
    else if (section === "demo") await viewOrUnavailable("demoPage", "Guided Demo", new URLSearchParams(query));
    else if (section === "boards") await boardsPage(new URLSearchParams(query));
    else if (section === "elements") await viewOrUnavailable("elementsPage", "Elements explorer", new URLSearchParams(query));
    else if (section === "diseases") await viewOrUnavailable("diseaseExplorerPage", "Disease Explorer", new URLSearchParams(query));
    else if (section === "targets") await viewOrUnavailable("targetExplorerPage", "Target Explorer", new URLSearchParams(query));
    else if (section === "graph-explorer") await viewOrUnavailable("knowledgeGraphPage", "Knowledge Graph", new URLSearchParams(query));
    else if (section === "drugs") await viewOrUnavailable("drugsPage", "Drug Intelligence", new URLSearchParams(query));
    else if (section === "compounds") await compoundLibraryPage(new URLSearchParams(query));
    else view.innerHTML = '<p class="empty">Page not found.</p>';
  } catch (err) {
    view.innerHTML = `<p class="err">Could not load: ${esc(err.message)}</p>`;
  }
}

document.getElementById("searchform").onsubmit = (ev) => {
  ev.preventDefault();
  const q = document.getElementById("q").value.trim();
  if (q.length >= 2) location.hash = "#/search?q=" + encodeURIComponent(q);
};
window.addEventListener("hashchange", route);
route();
