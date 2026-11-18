// MOLECULAB shared UI components.
//
// One place for the patterns that must look and READ the same everywhere: what kind of knowledge a
// number is, how a measurement is formatted, how an empty/loading/failed state looks, and the small
// SVG charts drawn from real data.
//
// Epistemic status is the core idea. Every value shown anywhere in MOLECULAB is one of:
//   experimental | derived | computed | predicted | hypothesis | unverified | contradiction | missing
// Each gets a colour, a glyph AND a word, so the distinction survives greyscale, colour-blindness
// and a screenshot.
//
// Loaded before app.js; uses esc() from app.js at call time only.
"use strict";

const EPISTEMIC = {
  experimental: { g: "◆", label: "Experimental",
    tip: "A measurement reported by a source database or publication. Carried through unchanged." },
  derived: { g: "◇", label: "Derived",
    tip: "Arithmetic on an experimental value (e.g. pIC50 from an exact molar IC50). No model involved." },
  computed: { g: "⬡", label: "Computed",
    tip: "Calculated here and now by a named algorithm. Not a measurement." },
  predicted: { g: "◌", label: "Predicted",
    tip: "Output of a statistical model. Not a measurement and not a validated result." },
  hypothesis: { g: "✧", label: "Hypothesis",
    tip: "A research hypothesis. Unvalidated by definition." },
  unverified: { g: "○", label: "Unverified",
    tip: "Recorded but not checked against a source." },
  contradiction: { g: "⚡", label: "Contradiction",
    tip: "Records disagree. MOLECULAB does not decide which is correct." },
  missing: { g: "∅", label: "Not recorded",
    tip: "No record exists in MOLECULAB. Absence of a record is not evidence of absence." },
};

/** Epistemic status chip: colour + glyph + word. Never colour alone. */
function chip(kind, text) {
  const k = EPISTEMIC[kind] ? kind : "unverified";
  const d = EPISTEMIC[k];
  return '<span class="chip chip-' + k + '" title="' + esc(d.tip) + '">' +
    '<span class="g" aria-hidden="true">' + d.g + "</span>" + esc(text || d.label) + "</span>";
}

/** Map a backend `result_kind` / `structure_origin` onto an epistemic status. */
function kindOf(value) {
  const map = {
    computed: "computed", computed_conformer: "computed", computed_depiction: "computed",
    experimental: "experimental", derived: "derived", predicted: "predicted",
    imported: "unverified", unavailable: "missing", hypothesis: "hypothesis",
  };
  return map[value] || "unverified";
}

/** A measurement with its unit kept visually subordinate and numerals tabular. */
function measure(value, unit, digits) {
  if (value === null || value === undefined || value === "") return '<span class="muted">—</span>';
  const n = typeof value === "number" && digits !== undefined ? value.toFixed(digits) : value;
  return '<span class="measure">' + esc(n) +
    (unit ? '<span class="unit">' + esc(unit) + "</span>" : "") + "</span>";
}

function stateBlock(cls, glyph, title, body, actionHtml) {
  return '<div class="state ' + cls + '"><span class="g" aria-hidden="true">' + glyph + "</span>" +
    "<h4>" + esc(title) + "</h4><p>" + esc(body || "") + "</p>" + (actionHtml || "") + "</div>";
}
const emptyState = (title, body, action) => stateBlock("", "∅", title, body, action);
const errorState = (title, body, action) => stateBlock("error", "⚠", title, body, action);
/** Honest unavailability: a capability that does not exist, rather than a failure. */
const unavailableState = (title, body, action) => stateBlock("", "⊘", title, body, action);

function skeleton(rows) {
  let out = "";
  for (let i = 0; i < (rows || 4); i++) {
    out += '<div class="skel skel-row" style="width:' + (96 - i * 11) + '%"></div>';
  }
  return '<div class="panel-body">' + out + "</div>";
}

function tile(label, value, foot, href) {
  const inner = '<div class="label">' + esc(label) + '</div><div class="value">' + esc(value) +
    "</div>" + (foot ? '<div class="foot">' + foot + "</div>" : "");
  return '<div class="tile">' + (href ? '<a href="' + esc(href) + '">' + inner + "</a>" : inner) + "</div>";
}

function panel(title, bodyHtml, rightHtml, extraClass) {
  return '<section class="panel ' + (extraClass || "") + '">' +
    '<div class="panel-head"><span class="panel-title">' + esc(title) + "</span>" +
    (rightHtml ? '<span class="right">' + rightHtml + "</span>" : "") + "</div>" +
    '<div class="panel-body">' + bodyHtml + "</div></section>";
}

/** Limitations list. Shown, never collapsed away by default. */
function limitations(items) {
  if (!items || !items.length) return "";
  return '<ul class="limits">' + items.map(function (l) { return "<li>" + esc(l) + "</li>"; }).join("") + "</ul>";
}

/** Method block: what was calculated, how, with which parameters. */
function methodBlock(method) {
  if (!method) return "";
  const rows = Object.keys(method).map(function (k) {
    let v = method[k];
    if (v && typeof v === "object") v = Object.keys(v).map(function (x) { return x + " " + v[x]; }).join(" · ");
    if (v === null || v === undefined || v === "") return "";
    return "<tr><th>" + esc(k.replace(/_/g, " ")) + "</th><td>" + esc(v) + "</td></tr>";
  }).join("");
  return "<details><summary>Method and parameters</summary><table class=\"kv\">" + rows + "</table></details>";
}

// ----------------------------------------------------------------- charts
// Small SVG charts drawn from real arrays. No chart library, no fabricated series.

/** Histogram from [{from,to,count}] bins. */
function histogramSVG(bins, axis, width, height) {
  if (!bins || !bins.length) return "";
  const W = width || 640, H = height || 170, padL = 34, padB = 22, padT = 8;
  const max = Math.max.apply(null, bins.map(function (b) { return b.count; })) || 1;
  const bw = (W - padL - 6) / bins.length;
  let bars = "";
  bins.forEach(function (b, i) {
    const h = Math.round((b.count / max) * (H - padB - padT));
    const x = padL + i * bw;
    bars += '<rect class="cbar" x="' + (x + 0.6).toFixed(1) + '" y="' + (H - padB - h) +
      '" width="' + Math.max(1, bw - 1.4).toFixed(1) + '" height="' + h + '">' +
      "<title>" + esc(b.from + " – " + b.to + ": " + b.count + " compounds") + "</title></rect>";
  });
  const ticks = [0, Math.round(max / 2), max].map(function (t) {
    const y = H - padB - (t / max) * (H - padB - padT);
    return '<text class="tick" x="2" y="' + (y + 3).toFixed(1) + '">' + t + "</text>";
  }).join("");
  const xlab = [bins[0].from, bins[bins.length - 1].to].map(function (v, i) {
    return '<text class="tick" x="' + (i ? W - 26 : padL) + '" y="' + (H - 6) + '">' + v + "</text>";
  }).join("");
  return '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' +
    esc((axis && axis.y) || "count") + '">' + bars + ticks + xlab +
    '<line class="axis" x1="' + padL + '" y1="' + (H - padB) + '" x2="' + W + '" y2="' + (H - padB) + '"/>' +
    "</svg>" + (axis ? '<div class="chart-cap">x: ' + esc(axis.x) + " · y: " + esc(axis.y) + "</div>" : "");
}

/** Scatter from [{x,y,label}]. Used for descriptor-vs-descriptor exploration. */
function scatterSVG(points, xLabel, yLabel, highlightIndex, width, height) {
  if (!points || !points.length) return "";
  const W = width || 640, H = height || 300, pad = 40;
  const xs = points.map(function (p) { return p.x; }), ys = points.map(function (p) { return p.y; });
  const x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs) || 1;
  const y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys) || 1;
  const sx = function (v) { return pad + ((v - x0) / ((x1 - x0) || 1)) * (W - pad - 12); };
  const sy = function (v) { return H - pad - ((v - y0) / ((y1 - y0) || 1)) * (H - pad - 12); };
  const dots = points.map(function (p, i) {
    return '<circle class="dot' + (i === highlightIndex ? " hi" : "") + '" cx="' + sx(p.x).toFixed(1) +
      '" cy="' + sy(p.y).toFixed(1) + '" r="' + (i === highlightIndex ? 5 : 3.1) + '">' +
      "<title>" + esc((p.label || "") + "  " + xLabel + "=" + p.x + "  " + yLabel + "=" + p.y) +
      "</title></circle>";
  }).join("");
  const axes = '<line class="axis" x1="' + pad + '" y1="' + (H - pad) + '" x2="' + (W - 6) + '" y2="' + (H - pad) + '"/>' +
    '<line class="axis" x1="' + pad + '" y1="8" x2="' + pad + '" y2="' + (H - pad) + '"/>';
  const labels =
    '<text class="tick" x="' + pad + '" y="' + (H - pad + 14) + '">' + esc(x0.toFixed ? x0.toFixed(1) : x0) + "</text>" +
    '<text class="tick" x="' + (W - 40) + '" y="' + (H - pad + 14) + '">' + esc(x1.toFixed ? x1.toFixed(1) : x1) + "</text>" +
    '<text class="tick" x="4" y="' + (H - pad) + '">' + esc(y0.toFixed ? y0.toFixed(1) : y0) + "</text>" +
    '<text class="tick" x="4" y="16">' + esc(y1.toFixed ? y1.toFixed(1) : y1) + "</text>";
  return '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" role="img">' + axes + dots + labels + "</svg>" +
    '<div class="chart-cap">x: ' + esc(xLabel) + " · y: " + esc(yLabel) +
    " · " + points.length + " compounds (computed descriptors)</div>";
}

/** Horizontal similarity/intensity bar, 0..1. */
function fractionBar(value, label) {
  const pct = Math.max(0, Math.min(1, value || 0)) * 100;
  return '<span class="row" style="gap:6px"><span class="ladder" style="flex:1;min-width:60px">' +
    '<span class="rung" style="padding:0;cursor:default;grid-template-columns:1fr">' +
    '<span class="track"><span class="fill" style="width:' + pct.toFixed(1) + '%"></span></span>' +
    "</span></span>" + measure((value || 0).toFixed(3), label || "") + "</span>";
}
