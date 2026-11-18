"""Download real human anatomy meshes from BodyParts3D and convert them for the browser.

BodyParts3D is the Life Science Integrated Database Center's anatomical model set: real,
segmented human anatomy, not an artist's impression. This script fetches the organs MOLECULAB's
PBPK model actually has compartments for, decimates them to a size a browser can draw, and writes
a single JSON bundle.

    .venv/Scripts/python scripts/fetch_anatomy.py

LICENCE. BodyParts3D is CC BY-SA 2.1 Japan. That is a SHARE-ALIKE licence: anything derived from
these meshes must carry the same licence, and the credit line below must be displayed wherever
they are shown.

    BodyParts3D, Copyright 2008 Life Science Integrated Database Center
    licensed by CC Display-Inheritance 2.1 Japan

The script writes that credit into the bundle and into a LICENCE file beside it, so the attribution
travels with the data and cannot be lost by someone copying the folder.
"""

from __future__ import annotations

import json
import re
import struct
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "app" / "static" / "vendor" / "anatomy"
RAW = ("https://raw.githubusercontent.com/Kevin-Mattheus-Moerman/BodyParts3D/main/"
       "assets/BodyParts3D_data/")

CREDIT = ("BodyParts3D, Copyright 2008 Life Science Integrated Database Center "
          "licensed by CC Display-Inheritance 2.1 Japan")
LICENCE_URL = "https://creativecommons.org/licenses/by-sa/2.1/jp/deed.en"

# PBPK compartment -> the BodyParts3D meshes that make it up. These FMA ids were checked against
# the mirror's file list: 934 of the 1,513 named parts actually have a mesh, so several obvious
# ids (whole brain FMA50801, whole heart FMA7088, whole lung FMA7195) do not exist and the
# compartment is assembled from the parts that do.
WANTED: dict[str, list[tuple[str, str]]] = {
    "liver":  [("liver", "FMA7197")],
    "kidney": [("left kidney", "FMA7205"), ("right kidney", "FMA7204")],
    # No whole-brain mesh exists in this set. Cerebral white matter plus cerebellum and brainstem
    # gives a recognisable brain; the cortical surface is not included.
    "brain":  [("cerebral white matter", "FMA61822"), ("cerebellum", "FMA67944"),
               ("pons", "FMA67943"), ("medulla oblongata", "FMA62004")],
    # "wall of heart" is the myocardium; there is no whole-heart mesh.
    "heart":  [("wall of heart", "FMA7274")],
    "lung":   [("upper lobe of left lung", "FMA7370"), ("lower lobe of left lung", "FMA7371"),
               ("upper lobe of right lung", "FMA7333"), ("lower lobe of right lung", "FMA7337"),
               ("middle lobe of lung", "FMA7383")],
    "gut":    [("stomach", "FMA7148"), ("duodenum", "FMA7206"), ("jejunum", "FMA7207"),
               ("ileum", "FMA7208")],
    "skin":   [("skin", "FMA7163")],
    # Shown as part of the "rest of body" compartment rather than modelled separately.
    "rest_of_body": [("spleen", "FMA7196"), ("urinary bladder", "FMA15900")],
}

# Compartments the PBPK model has but this mesh set does not: there is no bulk skeletal-muscle
# or adipose-tissue mesh in BodyParts3D. The viewer reports them as present in the model but
# without anatomy, rather than drawing a stand-in.
NO_MESH = {
    "muscle": "BodyParts3D has individual named muscles but no bulk skeletal-muscle volume.",
    "fat": "BodyParts3D contains no adipose-tissue mesh.",
}

MAX_TRIANGLES = 2600          # per compartment, after decimation; keeps the page responsive
TIMEOUT = 120


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MOLECULAB/anatomy-fetch"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    except Exception as exc:                                   # noqa: BLE001
        log(f"      fetch failed: {type(exc).__name__}: {exc}")
        return None


def parts_index() -> dict[str, str]:
    """name (lowercased) -> FMA id."""
    raw = fetch(RAW + "parts_list_e.txt")
    if raw is None:
        raise SystemExit("Could not download the BodyParts3D parts list.")
    index: dict[str, str] = {}
    for line in raw.decode("utf-8", "replace").splitlines()[1:]:
        cols = line.rstrip("\n").split("\t")
        if len(cols) >= 2 and cols[0].startswith("FMA"):
            index.setdefault(cols[1].strip().lower(), cols[0].strip())
    return index


def parse_stl(data: bytes) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    """Return (vertices, faces). Handles binary and ASCII STL."""
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    lookup: dict[tuple[int, int, int], int] = {}

    def add(x: float, y: float, z: float) -> int:
        # Weld vertices on a 0.01 mm grid so the mesh is indexed rather than a triangle soup.
        key = (round(x * 100), round(y * 100), round(z * 100))
        hit = lookup.get(key)
        if hit is None:
            hit = len(verts)
            lookup[key] = hit
            verts.append((x, y, z))
        return hit

    if data[:5] == b"solid" and b"facet" in data[:2048]:
        nums = re.compile(rb"vertex\s+(-?[\d.eE+-]+)\s+(-?[\d.eE+-]+)\s+(-?[\d.eE+-]+)")
        trio: list[int] = []
        for m in nums.finditer(data):
            trio.append(add(float(m.group(1)), float(m.group(2)), float(m.group(3))))
            if len(trio) == 3:
                faces.append((trio[0], trio[1], trio[2]))
                trio = []
    else:
        count = struct.unpack("<I", data[80:84])[0]
        off = 84
        for _ in range(count):
            if off + 50 > len(data):
                break
            vals = struct.unpack("<12f", data[off:off + 48])
            a = add(vals[3], vals[4], vals[5])
            b = add(vals[6], vals[7], vals[8])
            c = add(vals[9], vals[10], vals[11])
            if a != b and b != c and a != c:
                faces.append((a, b, c))
            off += 50
    return verts, faces


def decimate(verts: list, faces: list, budget: int) -> tuple[list, list]:
    """Vertex-cluster decimation: collapse vertices onto a grid, drop degenerate faces.

    Crude but dependency-free and good enough for a 3D overview: it preserves overall shape while
    cutting the triangle count by an order of magnitude.
    """
    if len(faces) <= budget:
        return verts, faces

    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) or 1.0
    # Aim for roughly the cube root of the budget per axis, then refine if we overshoot.
    for divisions in (int((budget * 2) ** (1 / 3)) + 2, 24, 18, 14, 10, 8, 6):
        cell = span / max(divisions, 1)
        cluster: dict[tuple[int, int, int], int] = {}
        remap: list[int] = []
        new_verts: list[tuple[float, float, float]] = []
        for (x, y, z) in verts:
            key = (int(x / cell), int(y / cell), int(z / cell))
            hit = cluster.get(key)
            if hit is None:
                hit = len(new_verts)
                cluster[key] = hit
                new_verts.append((x, y, z))
            remap.append(hit)
        new_faces = []
        seen = set()
        for (a, b, c) in faces:
            ra, rb, rc = remap[a], remap[b], remap[c]
            if ra == rb or rb == rc or ra == rc:
                continue
            key = tuple(sorted((ra, rb, rc)))
            if key in seen:
                continue
            seen.add(key)
            new_faces.append((ra, rb, rc))
        if len(new_faces) <= budget:
            return new_verts, new_faces
    return new_verts, new_faces[:budget]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log("Reading the BodyParts3D parts list...")
    index = parts_index()
    log(f"  {len(index)} anatomical parts listed\n")

    bundle: dict[str, dict] = {}
    all_pts: list[tuple[float, float, float]] = []

    for compartment, members in WANTED.items():
        log(f"{compartment}:")
        verts_all: list[tuple[float, float, float]] = []
        faces_all: list[tuple[int, int, int]] = []
        sources: list[str] = []

        for name, fma in members:
            # Cross-check the id against the official parts list where the name is known, so a
            # silently wrong id shows up here rather than as a misplaced organ on screen.
            listed = index.get(name)
            if listed and listed != fma:
                log(f"   {name:22} id mismatch: using {fma}, parts list says {listed}")
            data = fetch(f"{RAW}stl/{fma}.stl")
            if not data or len(data) < 200:
                log(f"   {name:22} {fma}  no mesh available")
                continue
            v, f = parse_stl(data)
            if not f:
                log(f"   {name:22} {fma}  mesh did not parse")
                continue
            base = len(verts_all)
            verts_all.extend(v)
            faces_all.extend((a + base, b + base, c + base) for (a, b, c) in f)
            sources.append(f"{name} ({fma})")
            log(f"   {name:22} {fma}  {len(f):>7,} triangles  ({len(data) / 1024:.0f} KB)")

        if not faces_all:
            log(f"   -> nothing usable for {compartment}\n")
            continue

        before = len(faces_all)
        verts_all, faces_all = decimate(verts_all, faces_all, MAX_TRIANGLES)
        log(f"   -> {before:,} triangles decimated to {len(faces_all):,}\n")

        all_pts.extend(verts_all)
        bundle[compartment] = {
            "vertices": [[round(c, 2) for c in v] for v in verts_all],
            "faces": [list(f) for f in faces_all],
            "sources": sources,
        }

    if not bundle:
        log("No meshes were retrieved; nothing written.")
        return 1

    # Normalise into a shared coordinate frame: centre on the whole-body bounding box and scale
    # so the body is about 2 units tall, which is what the viewer expects.
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    zs = [p[2] for p in all_pts]
    cx = (max(xs) + min(xs)) / 2
    cy = (max(ys) + min(ys)) / 2
    cz = (max(zs) + min(zs)) / 2
    scale = 2.0 / max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1e-6)
    for part in bundle.values():
        part["vertices"] = [[round((v[0] - cx) * scale, 4),
                             round((v[1] - cy) * scale, 4),
                             round((v[2] - cz) * scale, 4)] for v in part["vertices"]]

    payload = {
        "species": "human",
        "source": "BodyParts3D / Anatomography, Life Science Integrated Database Center",
        "source_url": "https://lifesciencedb.jp/bp3d/",
        "mirror": "https://github.com/Kevin-Mattheus-Moerman/BodyParts3D",
        "licence": "CC BY-SA 2.1 Japan",
        "licence_url": LICENCE_URL,
        "credit": CREDIT,
        "share_alike_warning": (
            "CC BY-SA is a share-alike licence: any work derived from these meshes must be "
            "released under the same licence, and this credit must be displayed wherever they "
            "are shown."
        ),
        "processing": (f"Meshes decimated by vertex clustering to at most {MAX_TRIANGLES} "
                       "triangles per compartment, then centred and scaled to a shared frame. "
                       "Shape is approximate; this is a visual overview, not a measurement."),
        "compartments_without_anatomy": NO_MESH,
        "parts": bundle,
    }

    out = OUT_DIR / "human_anatomy.json"
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    (OUT_DIR / "LICENCE.txt").write_text(
        CREDIT + "\n\nLicence: CC BY-SA 2.1 Japan\n" + LICENCE_URL +
        "\n\nSource: https://lifesciencedb.jp/bp3d/\n"
        "Mirror: https://github.com/Kevin-Mattheus-Moerman/BodyParts3D\n\n"
        "Share-alike: any derivative of these meshes must carry the same licence.\n",
        encoding="utf-8")

    log(f"Wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
    log(f"Compartments: {', '.join(bundle)}")
    log("Licence and credit written to " + str(OUT_DIR / "LICENCE.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
