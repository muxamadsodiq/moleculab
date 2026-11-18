// MOLECULAB research overview and compound library.
//
// The overview is a place to RESUME work, not a wall of counters: active investigations, outstanding
// evidence gaps, unresolved contradictions, and the tools to continue. Every number comes from the
// database at load time; nothing is cached, estimated or invented.
"use strict";

function toolCard(href, glyph, title, body, kind) {
  return '<a class="tile" href="' + esc(href) + '" style="display:block">' +
    '<div class="label">' + (kind ? chip(kind, title) : esc(title)) + "</div>" +
    '<div class="foot" style="margin-top:6px;font-size:12.5px">' + esc(body) + "</div></a>";
}

async function researchOverview() {
  view.innerHTML =
    '<div class="page-head"><div><div class="eyebrow">Research environment</div>' +
    "<h1>Overview</h1>" +
    '<p class="lede">Evidence-grounded drug discovery workspace. Every figure below is computed ' +
    "from the database when this page loads.</p></div>" +
    '<div class="actions"><a class="btn primary" href="#/workspaces">New investigation</a>' +
    '<a class="btn" href="#/studio">Molecular Studio</a>' +
    '<a class="btn" href="#/simulations">Simulation Lab</a></div></div>' +
    '<div id="ovbody">' + skeleton(5) + "</div>";

  const body = document.getElementById("ovbody");
  try {
    const [stats, workspaces, gaps, copilot] = await Promise.all([
      api("/api/stats"),
      api("/api/workspaces").catch(function () { return []; }),
      api("/api/research/gaps").catch(function () { return { items: [] }; }),
      api("/api/v1/copilot/status").catch(function () { return null; }),
    ]);
    const contradictions = await api("/api/research/contradictions")
      .catch(function () { return { items: [] }; });

    const gapItems = gaps.items || [];
    const conItems = contradictions.items || contradictions || [];
    const conCount = Array.isArray(conItems) ? conItems.length : 0;

    // ---- corpus tiles ----
    let html = '<div class="tiles">' +
      tile("Plants", stats.plants, chip("experimental", "Curated"), "#/plants") +
      tile("Compounds", stats.compounds, chip("experimental", "Curated"), "#/compounds") +
      tile("Publications", stats.publications, "", "#/publications") +
      tile("Evidence records", stats.evidence_records, chip("experimental", "Excerpt-verified"), "#/evidence") +
      tile("Research gaps", gapItems.length, chip("missing", "In this index"), "#/research/gaps") +
      tile("Contradictions", conCount, conCount ? chip("contradiction", "Unresolved")
        : chip("experimental", "None detected"), "#/research/contradictions") +
      "</div>";

    // ---- resume work ----
    const wsList = Array.isArray(workspaces) ? workspaces : [];
    html += '<div class="ws" style="grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);margin-top:16px">';
    html += panel("Active investigations",
      wsList.length
        ? wsList.map(function (w) {
            return '<a class="pick" href="#/workspace/' + esc(w.id) + '">' +
              "<strong>" + esc(w.title) + '</strong><span class="meta">' +
              esc(w.status) + " · " + esc(w.research_domain || "no domain") + "</span></a>";
          }).join("")
        : emptyState("No investigations yet",
            "An investigation keeps a research question, its collected records, hypotheses and reports together.",
            '<a class="btn primary" href="#/workspaces">Start one</a>'),
      '<a class="btn ghost" href="#/workspaces">All →</a>');

    // ---- what needs attention ----
    const attention = gapItems.slice(0, 6).map(function (g) {
      return '<div style="padding:7px 0;border-bottom:1px solid var(--line)">' +
        chip("missing", g.category ? g.category.replace(/_/g, " ") : "gap") +
        '<div style="margin-top:4px;font-size:12.5px;color:var(--text-dim)">' +
        esc((g.explanation || "").slice(0, 170)) + "</div></div>";
    }).join("");
    html += panel("Needs attention",
      attention || emptyState("No gaps detected", "Nothing outstanding in the current index."),
      chip("missing", "Gaps in this index"));
    html += "</div>";

    // ---- tools ----
    html += '<h2>Research tools</h2><div class="tiles">' +
      toolCard("#/studio", "⬢", "Molecular Studio",
        "Inspect 2D and 3D structures, descriptors and provenance.", "computed") +
      toolCard("#/simulations?tab=conformers", "∿", "Conformer exploration",
        "RDKit ETKDGv3 embedding with MMFF94 optimisation.", "computed") +
      toolCard("#/simulations?tab=similarity", "≈", "Similarity explorer",
        "Morgan fingerprints and Tanimoto ranking.", "computed") +
      toolCard("#/simulations?tab=activity", "◆", "Activity data",
        "Curated ChEMBL IC50 measurements for JAK1.", "experimental") +
      toolCard("#/assistant", "✦", "AI Copilot",
        copilot && copilot.model_generation_available
          ? "Grounded answers, verified claim by claim."
          : "Retrieval and verification; model generation off.",
        copilot && copilot.model_generation_available ? "computed" : "missing") +
      toolCard("#/evidence", "▤", "Evidence explorer",
        "Trace every claim to its source excerpt.", "experimental") +
      "</div>";

    html += '<p class="note" style="margin-top:18px">MOLECULAB organises published evidence and ' +
      "computes molecular properties. It does not discover medicines, and no record in this " +
      "corpus has yet been reviewed by a domain expert.</p>";

    body.innerHTML = html;
  } catch (err) {
    body.innerHTML = errorState("Could not load the overview", err.message);
  }
}

// ------------------------------------------------------------ compound library

async function compoundLibraryPage() {
  view.innerHTML =
    '<div class="page-head"><div><div class="eyebrow">Scientific records</div>' +
    "<h1>Compound library</h1>" +
    '<p class="lede">Every compound MOLECULAB holds. Structures are SMILES imported from their ' +
    "source database; compounds without a stored structure are listed, not hidden.</p></div>" +
    '<div class="actions"><input id="clq" type="search" placeholder="Filter…" autocomplete="off"></div></div>' +
    '<div id="clbody">' + skeleton(6) + "</div>";

  const data = await api("/api/v1/molecules/?limit=300");
  const paint = function (filter) {
    const rows = data.items.filter(function (i) {
      return !filter || (i.name || "").toLowerCase().indexOf(filter) !== -1;
    });
    document.getElementById("clbody").innerHTML = rows.length
      ? panel("Compounds (" + rows.length + ")",
          '<table class="dtable"><thead><tr><th>Name</th><th>Formula</th>' +
          '<th class="num">MW</th><th>InChIKey</th><th class="num">Evidence</th>' +
          "<th>Structure</th></tr></thead><tbody>" +
          rows.map(function (i) {
            return "<tr><td><strong>" + esc(i.name) + "</strong></td><td>" +
              esc(i.molecular_formula || "—") + '</td><td class="num">' +
              measure(i.molecular_weight ? Number(i.molecular_weight).toFixed(2) : null) +
              '</td><td class="mono">' + esc(i.inchikey || "—") + '</td><td class="num">' +
              (i.evidence_count || 0) + "</td><td>" +
              (i.has_structure
                ? '<a class="btn ghost" href="#/studio">Open in Studio</a>'
                : chip("missing", "No structure")) +
              "</td></tr>";
          }).join("") + "</tbody></table>", "", "scroll")
      : emptyState("No compound matches", "Try a different filter.");
  };
  paint("");
  document.getElementById("clq").oninput = function (e) {
    paint(e.target.value.trim().toLowerCase());
  };
}
