// MOLECULAB Research Board — the researcher's visual workspace.
//
// Every object on a board is a POINTER to a real record plus its position. Opening an object
// navigates to the underlying record. Edges are the researcher's own reasoning and are labelled
// as such: drawing a line between two objects asserts nothing about the evidence.
"use strict";

let boardState = { id: null, data: null, drag: null, linkFrom: null };

const BOARD_KIND = {
  note: "unverified", hypothesis: "hypothesis", equation: "computed",
  compound: "experimental", molecular_structure: "computed", material: "experimental",
  formulation: "experimental", research_constraint: "missing", experiment_plan: "hypothesis",
  evidence_record: "experimental", publication: "experimental", disease: "experimental",
  biological_target: "experimental", simulation_result: "computed", image: "unverified",
  group: "unverified",
};

async function boardsPage(params) {
  const bid = params && params.get("id");
  if (bid) return boardCanvas(+bid);

  view.innerHTML =
    '<div class="page-head"><div><div class="eyebrow">Research board</div><h1>Boards</h1>' +
    '<p class="lede">A board holds notes, hypotheses, equations and links to real records, ' +
    "positioned however the investigation makes sense. Objects point at records; they are not " +
    "copies.</p></div>" +
    '<div class="actions"><button class="btn primary" id="nb">New board</button></div></div>' +
    '<div id="bl">' + skeleton(3) + "</div>";

  document.getElementById("nb").onclick = async function () {
    const title = prompt("Board title:");
    if (!title) return;
    const made = await api("/api/v1/boards", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ title: title }) });
    location.hash = "#/boards?id=" + made.id;
  };

  const data = await api("/api/v1/boards");
  document.getElementById("bl").innerHTML = data.count
    ? panel("Boards (" + data.count + ")",
        data.items.map(function (b) {
          return '<a class="pick" href="#/boards?id=' + b.id + '"><strong>' + esc(b.title) +
            '</strong><span class="meta">' + b.object_count + " objects · " + b.edge_count +
            " links · revision " + b.revision + "</span></a>";
        }).join(""))
    : emptyState("No boards yet", "Create a board to lay out an investigation visually.",
        '<button class="btn primary" onclick="document.getElementById(\'nb\').click()">New board</button>');
}

async function boardCanvas(bid) {
  boardState.id = bid;
  view.innerHTML = '<div id="bw">' + skeleton(4) + "</div>";
  const data = await api("/api/v1/boards/" + bid);
  boardState.data = data;
  renderBoard(data);
}

function renderBoard(d) {
  view.innerHTML =
    '<div class="page-head"><div><div class="eyebrow">Research board · revision ' + d.revision +
    '</div><h1>' + esc(d.title) + "</h1>" +
    '<p class="lede">' + d.objects.length + " objects · " + d.edges.length + " links. " +
    esc(d.edge_note) + "</p></div>" +
    '<div class="actions">' +
    '<button class="btn" data-add="note">+ Note</button>' +
    '<button class="btn" data-add="hypothesis">+ Hypothesis</button>' +
    '<button class="btn" data-add="equation">+ Equation</button>' +
    '<button class="btn" id="addrec">+ Link a record</button>' +
    '<button class="btn ghost" id="linkmode">Connect</button>' +
    '<a class="btn ghost" href="#/boards">All boards</a></div></div>' +
    '<div class="boardcanvas" id="bc"></div>' +
    '<p class="note" style="margin-top:12px">Objects are references to records. Deleting an object ' +
    "removes it from the board only — the underlying record is untouched.</p>";

  paintBoard();

  view.querySelectorAll("[data-add]").forEach(function (b) {
    b.onclick = function () { addBoardObject(b.dataset.add); };
  });
  document.getElementById("addrec").onclick = addRecordObject;
  document.getElementById("linkmode").onclick = function () {
    boardState.linkFrom = boardState.linkFrom === null ? "arm" : null;
    this.classList.toggle("on", boardState.linkFrom !== null);
    this.textContent = boardState.linkFrom !== null ? "Pick first object…" : "Connect";
  };
}

function paintBoard() {
  const d = boardState.data;
  const host = document.getElementById("bc");
  if (!host) return;

  const edges = d.edges.map(function (e) {
    const a = d.objects.find(function (o) { return o.id === e.from; });
    const b = d.objects.find(function (o) { return o.id === e.to; });
    if (!a || !b) return "";
    const x1 = a.x + a.width / 2, y1 = a.y + a.height / 2;
    const x2 = b.x + b.width / 2, y2 = b.y + b.height / 2;
    return '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 +
      '" stroke="var(--line-strong)" stroke-width="1.5" stroke-dasharray="4 3"/>' +
      '<text x="' + ((x1 + x2) / 2) + '" y="' + ((y1 + y2) / 2 - 4) +
      '" fill="var(--text-faint)" font-size="10">' + esc(e.label || "researcher link") + "</text>";
  }).join("");

  const cards = d.objects.map(function (o) {
    const body = o.object_type === "equation"
      ? '<code>' + esc((o.content || {}).text || "") + "</code>"
      : esc(((o.content || {}).text || "")).slice(0, 300);
    return '<div class="bobj" data-id="' + o.id + '" style="left:' + o.x + "px;top:" + o.y +
      "px;width:" + o.width + "px;min-height:" + o.height + 'px">' +
      '<div class="bobj-head">' + chip(BOARD_KIND[o.object_type] || "unverified",
        o.object_type.replace(/_/g, " ")) +
      '<button class="btn ghost bdel" data-del="' + o.id + '" title="Remove from board">×</button>' +
      "</div>" +
      '<div class="bobj-title">' + esc(o.label) + "</div>" +
      (body ? '<div class="bobj-body">' + body + "</div>" : "") +
      (o.record_link ? '<a class="bobj-link" href="' + esc(o.record_link) + '">open record →</a>'
        : (o.ref_type ? '<span class="sub">' + esc(o.ref_type) + " " + esc(o.ref_id || "") + "</span>" : "")) +
      "</div>";
  }).join("");

  host.innerHTML = '<svg class="bedges">' + edges + "</svg>" + cards;
  wireBoardObjects();
}

function wireBoardObjects() {
  const host = document.getElementById("bc");
  host.querySelectorAll(".bobj").forEach(function (el) {
    const id = +el.dataset.id;

    el.onmousedown = function (ev) {
      if (ev.target.closest(".bdel") || ev.target.closest("a")) return;
      if (boardState.linkFrom !== null) {
        if (boardState.linkFrom === "arm") {
          boardState.linkFrom = id;
          el.classList.add("picked");
        } else if (boardState.linkFrom !== id) {
          connectObjects(boardState.linkFrom, id);
        }
        return;
      }
      const rect = el.getBoundingClientRect();
      boardState.drag = { id: id, el: el, dx: ev.clientX - rect.left, dy: ev.clientY - rect.top };
      el.classList.add("dragging");
      ev.preventDefault();
    };

    el.ondblclick = function () {
      const obj = boardState.data.objects.find(function (o) { return o.id === id; });
      const text = prompt("Text:", (obj.content || {}).text || "");
      if (text === null) return;
      api("/api/v1/boards/objects/" + id, {
        method: "PATCH", headers: { "content-type": "application/json" },
        body: JSON.stringify({ content: { text: text } }),
      }).then(function () { boardCanvas(boardState.id); });
    };
  });

  host.querySelectorAll(".bdel").forEach(function (b) {
    b.onclick = async function (ev) {
      ev.stopPropagation();
      await fetch("/api/v1/boards/objects/" + b.dataset.del, { method: "DELETE" });
      boardCanvas(boardState.id);
    };
  });

  document.onmousemove = function (ev) {
    const d = boardState.drag;
    if (!d) return;
    const host2 = document.getElementById("bc");
    if (!host2) return;
    const box = host2.getBoundingClientRect();
    d.el.style.left = Math.max(0, ev.clientX - box.left - d.dx) + "px";
    d.el.style.top = Math.max(0, ev.clientY - box.top - d.dy) + "px";
  };

  document.onmouseup = async function () {
    const d = boardState.drag;
    if (!d) return;
    boardState.drag = null;
    d.el.classList.remove("dragging");
    const x = parseFloat(d.el.style.left), y = parseFloat(d.el.style.top);
    await api("/api/v1/boards/objects/" + d.id, {
      method: "PATCH", headers: { "content-type": "application/json" },
      body: JSON.stringify({ x: x, y: y }) });
    const obj = boardState.data.objects.find(function (o) { return o.id === d.id; });
    if (obj) { obj.x = x; obj.y = y; }
    paintBoard();
  };
}

async function connectObjects(from, to) {
  try {
    await api("/api/v1/boards/" + boardState.id + "/edges", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ from_object_id: from, to_object_id: to }) });
  } catch (err) { alert("Could not connect: " + err.message); }
  boardState.linkFrom = null;
  boardCanvas(boardState.id);
}

async function addBoardObject(type) {
  const label = prompt((type === "equation" ? "Equation" : type === "hypothesis"
    ? "Hypothesis" : "Note") + " title:");
  if (!label) return;
  const text = prompt("Content (optional):") || "";
  const n = boardState.data.objects.length;
  await api("/api/v1/boards/" + boardState.id + "/objects", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ object_type: type, label: label, content: { text: text },
      x: 30 + (n % 4) * 260, y: 30 + Math.floor(n / 4) * 170 }) });
  boardCanvas(boardState.id);
}

async function addRecordObject() {
  const types = await api("/api/v1/boards/object-types");
  const refType = prompt("Record type (" + types.linkable_ref_types.join(", ") + "):", "compound");
  if (!refType) return;
  const refId = prompt("Record id:");
  if (!refId) return;
  const label = prompt("Label for the board:", refType + " " + refId);
  if (!label) return;
  const n = boardState.data.objects.length;
  try {
    await api("/api/v1/boards/" + boardState.id + "/objects", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ object_type: refType === "compound" ? "compound" : "note",
        label: label, ref_type: refType, ref_id: refId,
        x: 30 + (n % 4) * 260, y: 30 + Math.floor(n / 4) * 170 }) });
    boardCanvas(boardState.id);
  } catch (err) { alert("Could not add: " + err.message); }
}
