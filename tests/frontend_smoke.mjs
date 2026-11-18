// Frontend smoke test: runs every route of app/static/app.js in Node against a running server with a
// minimal DOM stub, and fails if any view renders an error or throws.
//   node tests/frontend_smoke.mjs http://127.0.0.1:8765
//
// RUN IT AGAINST A SERVER WITH LLM_PROVIDER=none. Two assistant checks assert the exact wording of
// the deterministic extractive answer ("does not establish this", vd-not_supported). When a language
// model is configured the model rewrites that wording, and those two checks fail even though the UI
// is correct — the assertions are pinning the retrieval path on purpose, so the variable to control
// is the provider, not the assertion. With generation on, expect 38/40 for that reason alone.
//   LLM_PROVIDER=none ./scripts/start_backend.sh   # then run this
import fs from "node:fs";
import vm from "node:vm";

const BASE = process.argv[2] || "http://127.0.0.1:8765";
// app.js is the router; the view modules it dispatches to are separate files, loaded here in the
// same order as index.html so every route resolves.
const MODULES = ["ui.js", "studio.js", "sim.js", "overview.js", "formulation.js", "board.js", "elements.js", "discovery.js", "drugs.js",
  "screening.js", "workbench.js", "materials.js", "conditions.js",
  "bench.js", "benchai.js", "playground.js", "landing.js", "dosing.js", "anatomy3d.js", "anatomy.js", "app.js"];
const src = MODULES.map((m) =>
  fs.readFileSync(new URL("../app/static/" + m, import.meta.url), "utf8")).join(";\n");

function makeEl(id) {
  return {
    id, innerHTML: "", value: "", textContent: "", classList: { add() {}, remove() {}, toggle() {} },
    style: {}, clientWidth: 800, clientHeight: 500, children: [],
    appendChild(c) { this.children.push(c); return c; }, setAttribute() {}, addEventListener() {},
    set onsubmit(f) { this._submit = f; }, get onsubmit() { return this._submit; },
  };
}
const els = {};
const document = {
  getElementById: (id) => (els[id] ||= makeEl(id)),
  querySelectorAll: () => [],
  createElementNS: () => makeEl("svg-child"),
};
const location = { hash: "" };
const ctx = vm.createContext({
  document, location, window: { addEventListener() {}, scrollTo() {} }, console, URLSearchParams,
  FormData: class {}, fetch: (p, o) => fetch(BASE + p, o), setTimeout,
});
vm.runInContext(src, ctx);
const routes = ["", "plants", "plant/2", "compound/1", "publications", "publication/1", "evidence", "evidence/1",
  "evidence?evidence_type=traditional_use", "graph", "graph?plant_id=2", "search?q=Каперсы", "assistant", "plant/9999",
  "research", "research/gaps?plant_id=5", "research/contradictions", "research/hypotheses", "research/priorities",
  "research/priorities?kind=compound"];
let failed = 0;
for (const r of routes) {
  location.hash = "#/" + r;
  await vm.runInContext("route()", ctx);
  const html = els.view.innerHTML;
  const expectError = r === "plant/9999";
  const bad = html.includes("Could not load") !== expectError || html.length < 40;
  if (bad) failed++;
  console.log(`${bad ? "FAIL" : "ok  "} #/${r.padEnd(40)} ${html.length} chars${expectError ? " (expected error shown)" : ""}`);
}
// assistant interactions: submit questions through the page's own handler (demo scenarios 1-4)
const scenarios = [
  ["Which chemical constituents have been reported in Glycyrrhiza glabra?", ["Verified claims", "vd-supported", "<blockquote>"]],
  ["What is the molecular formula of glabridin?", ["C20H20O4", "do not establish that it occurs in any plant"]],
  ["Is glabridin proven to cure cancer?", ["not answerable", "cancer", "What the indexed evidence does establish"]],
  ["Is glabridin the most abundant compound in licorice?", ["does not establish this", "vd-not_supported"]],
  ["Does Glycyrrhiza glabra cure cancer?", ["not answerable", "cancer"]],
];
for (const [q, must] of scenarios) {
  location.hash = "#/assistant";
  await vm.runInContext("route()", ctx);
  document.getElementById("aq").value = q;
  await els.af.onsubmit({ preventDefault() {} });
  await new Promise((r) => setTimeout(r, 1200));
  const html = els.ares.innerHTML;
  const miss = must.filter((m) => !html.includes(m));
  console.log(`${miss.length ? "FAIL" : "ok  "} assistant: ${q}${miss.length ? "  missing: " + miss.join(", ") : ""}`);
  if (miss.length) failed++;
}
// claim checker (scenario 5: inspect why a claim is accepted or rejected)
document.getElementById("vclaim").value = "Glabridin is a potent antioxidant.";
document.getElementById("vids").value = "13";
await els.vf.onsubmit({ preventDefault() {} });
await new Promise((r) => setTimeout(r, 800));
const vok = els.vres.innerHTML.includes("vd-not_supported") && els.vres.innerHTML.includes("biological activity");
console.log(`${vok ? "ok  " : "FAIL"} claim checker rejects unsupported antioxidant claim`);
if (!vok) failed++;
// v0.4 research scenarios through the page's own handlers
const research = [
  ["A gaps for Ferula", "research/gaps?plant_id=5", ["Only 1 publication", "No toxicity evidence is currently indexed in MOLECULAB"]],
  ["B contradictions", "research/contradictions", ["pulegone", "insufficient context", "62.17", "7.9", "does not decide which report is correct"]],
  ["E priorities", "research/priorities", ["Investigation signals", "unavailable", "not a score"]],
];
for (const [name, route, must] of research) {
  location.hash = "#/" + route;
  await vm.runInContext("route()", ctx);
  const html = els.view.innerHTML;
  const miss = must.filter((m) => !html.includes(m));
  console.log(`${miss.length ? "FAIL" : "ok  "} scenario ${name}${miss.length ? "  missing: " + miss.join(", ") : ""}`);
  if (miss.length) failed++;
}
for (const [name, form, must] of [
  ["C hypothesis (Capparis)", { plant_id: "1" }, ["Unvalidated research hypothesis", "stachydrine might contribute", "Anti-arthritic"]],
  ["D abstention (glabridin antioxidant)", { question: "Does glabridin have antioxidant activity?" }, ["Abstentions", "cannot be answered from the indexed evidence"]],
]) {
  location.hash = "#/research/hypotheses";
  await vm.runInContext("route()", ctx);
  ctx.FormData = class { constructor() { return Object.entries(form); } };
  await els.hf.onsubmit({ preventDefault() {}, target: {} });
  await new Promise((r) => setTimeout(r, 2500));
  const html = els.hres.innerHTML;
  const miss = must.filter((m) => !html.includes(m));
  console.log(`${miss.length ? "FAIL" : "ok  "} scenario ${name}${miss.length ? "  missing: " + miss.join(", ") : ""}`);
  if (miss.length) failed++;
}
// v0.5 workspace: build a temporary investigation through the API, render every tab, then delete it
const J = (m, b) => ({ method: m, headers: { "Content-Type": "application/json" }, body: b ? JSON.stringify(b) : undefined });
const ws = await (await fetch(BASE + "/api/workspaces", J("POST", { title: "SMOKE TEST investigation (temporary)",
  research_question: "Which constituents of Capparis spinosa are documented in the index?" }))).json();
await fetch(`${BASE}/api/workspaces/${ws.id}/collect-plant`, J("POST", { plant_id: 1, include: ["compounds", "publications", "evidence"] }));
const runs = await (await fetch(`${BASE}/api/workspaces/${ws.id}/hypotheses/generate`, J("POST", {}))).json();
await fetch(`${BASE}/api/workspaces/${ws.id}/hypotheses`, J("POST", { origin: "generated", statement: runs[0].hypotheses[0].hypothesis, source: runs[0].source }));
await fetch(`${BASE}/api/workspaces/${ws.id}/tasks`, J("POST", { title: "Search toxicology literature" }));
await fetch(`${BASE}/api/workspaces/${ws.id}/notes`, J("POST", { category: "question", body: "Is the activity due to stachydrine?" }));
await fetch(`${BASE}/api/workspaces/${ws.id}/reports`, J("POST"));
for (const [tab, must] of [["", ["Research workspaces", "local single user"]], [`workspace/${ws.id}`, ["SMOKE TEST", "Outstanding tasks (1)", "Recent activity"]],
  [`workspace/${ws.id}/collection`, ["stachydrine", "Evidence collection"]], [`workspace/${ws.id}/hypotheses`, ["unvalidated", "Unresolved", "stachydrine might contribute"]],
  [`workspace/${ws.id}/notes`, ["researcher note", "Is the activity due to stachydrine?"]], [`workspace/${ws.id}/tasks`, ["Search toxicology literature"]],
  [`workspace/${ws.id}/timeline`, ["investigation created", "report generated"]], [`workspace/${ws.id}/reports`, ["observations", "Export .md"]]]) {
  location.hash = "#/" + (tab || "workspaces");
  await vm.runInContext("route()", ctx);
  const html = els.view.innerHTML + (els.rlist ? els.rlist.innerHTML : "");  // collection table renders into #rlist
  const miss = must.filter((m) => !html.includes(m));
  console.log(`${miss.length ? "FAIL" : "ok  "} workspace view #/${tab || "workspaces"}${miss.length ? "  missing: " + miss.join(", ") : ""}`);
  if (miss.length) failed++;
}
await fetch(`${BASE}/api/workspaces/${ws.id}/archive`, J("POST"));
const del = await fetch(`${BASE}/api/workspaces/${ws.id}`, { method: "DELETE" });
console.log(`${del.status === 204 ? "ok  " : "FAIL"} temporary workspace deleted`);
if (del.status !== 204) failed++;
process.exit(failed ? 1 : 0);
