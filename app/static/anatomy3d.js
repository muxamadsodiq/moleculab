// Real human anatomy in 3D, coloured by simulated tissue concentration.
//
// The meshes are BodyParts3D (Life Science Integrated Database Center) — segmented from real
// human anatomy, not drawn by hand. They are rendered through 3Dmol.js's custom-geometry path,
// which the application already vendors for molecules, so no new 3D library is needed.
//
// WHAT THE COLOUR MEANS: the concentration the PBPK model computes for that compartment at the
// selected time, relative to the highest concentration reached anywhere during the run. It does
// NOT mean harm, damage or risk. MOLECULAB has no model of what any concentration does to a
// tissue, and the panel beside the figure says so.
//
// Anatomy is available for HUMAN only. There is no openly-licensed organ-level mesh set for rat,
// mouse, rabbit or dog that this could use, so those species fall back to the schematic and the
// page says why rather than showing a scaled-down human and calling it a rat.
"use strict";

const A3D = {
  bundle: null,
  loading: null,
  viewer: null,
  shapes: {},
  failed: false,
};

const ANATOMY_URL = "/static/vendor/anatomy/human_anatomy.json";

// Compartments the model has that the mesh set does not cover.
const A3D_NO_MESH = { muscle: 1, fat: 1 };

async function loadAnatomyBundle() {
  if (A3D.bundle) return A3D.bundle;
  if (A3D.failed) return null;
  if (A3D.loading) return A3D.loading;
  A3D.loading = (async function () {
    try {
      const res = await fetch(ANATOMY_URL);
      if (!res.ok) throw new Error("HTTP " + res.status);
      A3D.bundle = await res.json();
      return A3D.bundle;
    } catch (err) {
      A3D.failed = true;
      return null;
    } finally {
      A3D.loading = null;
    }
  })();
  return A3D.loading;
}

function anatomyAvailable(species) {
  return species === "human" && !!A3D.bundle;
}

// Compute a flat normal per face so the surface shades instead of reading as a silhouette.
function buildGeometry(part) {
  const verts = part.vertices;
  const faces = part.faces;
  const vertexArr = [];
  const normalArr = [];
  const faceArr = [];
  let n = 0;
  for (let i = 0; i < faces.length; i++) {
    const [ia, ib, ic] = faces[i];
    const a = verts[ia], b = verts[ib], c = verts[ic];
    if (!a || !b || !c) continue;
    const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
    const vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
    let nx = uy * vz - uz * vy;
    let ny = uz * vx - ux * vz;
    let nz = ux * vy - uy * vx;
    const len = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
    nx /= len; ny /= len; nz /= len;
    [a, b, c].forEach(function (p) {
      vertexArr.push({ x: p[0], y: p[1], z: p[2] });
      normalArr.push({ x: nx, y: ny, z: nz });
    });
    faceArr.push(n, n + 1, n + 2);
    n += 3;
  }
  return { vertexArr: vertexArr, normalArr: normalArr, faceArr: faceArr };
}

function a3dColour(frac) {
  // Same ramp as the schematic: dark surface -> teal -> amber. Deliberately not a red
  // "danger" scale, because this is concentration, not risk.
  const f = Math.max(0, Math.min(1, frac));
  const r = Math.round(40 + f * 172);
  const g = Math.round(58 + f * 112);
  const b = Math.round(70 + f * -10);
  return (r << 16) | (g << 8) | Math.max(0, b);
}

// Render the body once; later frames only recolour, which is far cheaper than rebuilding.
function renderAnatomy3D(hostId, frame, maxC, onPick) {
  const host = document.getElementById(hostId);
  if (!host || !A3D.bundle || !window.$3Dmol) return false;

  if (!A3D.viewer || A3D.host !== hostId) {
    host.innerHTML = "";
    try {
      A3D.viewer = window.$3Dmol.createViewer(host, { backgroundColor: "0x0d1015" });
    } catch (err) {
      host.innerHTML = '<p class="sub">3D viewer could not start: ' + esc(err.message) + "</p>";
      return false;
    }
    A3D.host = hostId;
    A3D.shapes = {};
  }

  const parts = A3D.bundle.parts || {};
  const names = Object.keys(parts);

  // Rebuild only when the shape set is empty (first draw or after a viewer reset).
  if (!Object.keys(A3D.shapes).length) {
    names.forEach(function (id) {
      const geo = buildGeometry(parts[id]);
      if (!geo.faceArr.length) return;
      const isSkin = id === "skin";
      const shape = A3D.viewer.addCustom({
        vertexArr: geo.vertexArr,
        normalArr: geo.normalArr,
        faceArr: geo.faceArr,
        color: isSkin ? 0x2a3340 : 0x3a4654,
        opacity: isSkin ? 0.12 : 0.95,
        clickable: !isSkin,
        callback: isSkin ? undefined : function () { if (onPick) onPick(id); },
      });
      A3D.shapes[id] = { shape: shape, isSkin: isSkin };
    });
    A3D.viewer.zoomTo();
  }

  // Recolour by concentration.
  names.forEach(function (id) {
    const entry = A3D.shapes[id];
    if (!entry || entry.isSkin) return;
    let colour = 0x3a4654;
    if (frame && frame[id] !== undefined && maxC) colour = a3dColour(frame[id] / maxC);
    try {
      entry.shape.updateStyle({ color: colour, opacity: 0.95 });
    } catch (err) {
      // Older builds expose no updateStyle; fall back to a full rebuild next frame.
      A3D.shapes = {};
    }
  });

  A3D.viewer.render();
  return true;
}

function anatomyCredit() {
  const b = A3D.bundle;
  if (!b) return "";
  const missing = Object.keys(b.compartments_without_anatomy || A3D_NO_MESH);
  return '<p class="sub">' +
    esc(b.credit) + ' &middot; <a href="' + esc(b.licence_url) +
    '" target="_blank" rel="noopener">' + esc(b.licence) + "</a></p>" +
    '<p class="sub">' + esc(b.processing) + "</p>" +
    (missing.length
      ? '<p class="sub">No mesh exists in this set for: ' + esc(missing.join(", ")) +
        ". Those compartments are still simulated; they simply have no anatomy to draw.</p>"
      : "");
}

function anatomyUnavailableNote(speciesLabel) {
  return '<p class="note caution"><strong>No anatomical model for ' + esc(speciesLabel) +
    ".</strong> MOLECULAB uses BodyParts3D, which is human only. There is no openly-licensed, " +
    "organ-level mesh set for rat, mouse, rabbit or dog that could be used here, so the " +
    "schematic is shown instead of scaling down a human figure and calling it another animal. " +
    "The physiology driving the simulation is still that species' own.</p>";
}
