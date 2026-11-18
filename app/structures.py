"""Protein-ligand structures: real experimental complexes from the Protein Data Bank.

MOLECULAB does not dock. Writing a bad docking engine would produce numbers that look like
science and are not, which is the one thing this product refuses to do.

What it does instead is better evidence, not worse: it retrieves **experimentally determined**
protein-ligand complexes from the RCSB PDB and measures the contacts that are actually in the
crystal structure. Those contacts were observed by X-ray crystallography or cryo-EM. They are
measurements, not predictions, and they answer the real question a researcher has - *how does
this class of molecule actually sit in this target?* - with data rather than a score.

The distinction the API never lets slip:

    A contact in a deposited structure is EXPERIMENTAL.
    A predicted pose or affinity would be PREDICTED, and MOLECULAB has none.

So nothing here estimates binding strength. A 2.9 angstrom hydrogen bond in a crystal structure
tells you the atoms are close; it does not tell you the free energy of binding, and this module
never claims otherwise.
"""

from __future__ import annotations

import math
import re

import numpy as np

from app.clients.base import SourceClient, SourceResult
from app.config import APP_VERSION

STRUCTURES_VERSION = "1.0.0"

RCSB_FILES = "https://files.rcsb.org/download/"
RCSB_DATA = "https://data.rcsb.org/rest/v1/core/entry/"

# Buffers, ions, cryoprotectants and crystallisation additives. These are in the file but are not
# the ligand anyone means, so they are never offered as "the bound ligand".
NOT_LIGANDS = {
    "HOH", "DOD", "WAT", "SO4", "PO4", "GOL", "EDO", "PEG", "PGE", "MPD", "DMS", "ACT", "FMT",
    "CL", "NA", "K", "MG", "CA", "ZN", "MN", "FE", "CU", "NI", "CO", "CD", "BR", "IOD",
    "TRS", "EPE", "MES", "ACY", "NO3", "CIT", "TLA", "IPA", "BME", "CAC", "AZI", "FLC",
}

CONTACT_CUTOFF = 4.0          # angstrom; the usual definition of a close contact
HBOND_MAX = 3.5               # angstrom, heavy-atom to heavy-atom
HBOND_DONORS_ACCEPTORS = {"N", "O", "S"}
MAX_PDB_BYTES = 12_000_000


class PDBClient(SourceClient):
    """Fetches from RCSB through the project's shared caching, rate limiting and provenance."""

    name = "rcsb_pdb"
    base_url = RCSB_FILES

    def fetch_entry(self, pdb_id: str) -> SourceResult:
        """Entry metadata: title, experimental method, resolution."""
        return self._get(RCSB_DATA + pdb_id.upper())

    def fetch_structure(self, pdb_id: str) -> SourceResult:
        """The coordinate file itself. The body is plain text, not JSON."""
        return self._get(RCSB_FILES + pdb_id.upper() + ".pdb")


def _parse_atoms(pdb_text: str) -> tuple[list[dict], list[dict]]:
    """Split ATOM/HETATM records into protein atoms and candidate ligand atoms.

    A deliberately small hand parser: the whole structure is not needed, only coordinates,
    residue identity and element, and the PDB column layout for those is fixed.
    """
    protein, hetero = [], []
    for line in pdb_text.splitlines():
        tag = line[:6]
        if tag not in ("ATOM  ", "HETATM"):
            if line.startswith("ENDMDL"):      # only the first model of an NMR ensemble
                break
            continue
        try:
            rec = {
                "name": line[12:16].strip(),
                "res": line[17:20].strip(),
                "chain": line[21:22].strip(),
                "res_seq": line[22:26].strip(),
                "x": float(line[30:38]), "y": float(line[38:46]), "z": float(line[46:54]),
                "element": (line[76:78].strip() or line[12:16].strip()[:1]).upper(),
                "altloc": line[16:17].strip(),
            }
        except ValueError:
            continue
        if rec["altloc"] not in ("", "A"):      # keep one alternate conformation only
            continue
        (protein if tag == "ATOM  " else hetero).append(rec)
    return protein, hetero


def _ligand_groups(hetero: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for a in hetero:
        if a["res"] in NOT_LIGANDS:
            continue
        key = f"{a['res']}|{a['chain']}|{a['res_seq']}"
        groups.setdefault(key, []).append(a)
    return groups


def list_ligands(pdb_text: str) -> list[dict]:
    """Every bound small molecule in the structure, biggest first, with buffers removed."""
    out = []
    for key, atoms in _ligand_groups(pdb_text if isinstance(pdb_text, list)
                                     else _parse_atoms(pdb_text)[1]).items():
        res, chain, seq = key.split("|")
        heavy = [a for a in atoms if a["element"] != "H"]
        out.append({"ligand_code": res, "chain": chain, "residue_number": seq,
                    "heavy_atom_count": len(heavy), "key": key})
    out.sort(key=lambda d: -d["heavy_atom_count"])
    return out


def contacts(pdb_id: str, ligand_code: str | None = None, *,
             client: PDBClient | None = None) -> dict:
    """Measure the real contacts between a deposited ligand and the protein around it."""
    pdb_id = (pdb_id or "").strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{4}", pdb_id):
        raise ValueError("A PDB id is four characters, for example 1M17 or 6TLC.")

    client = client or PDBClient()
    entry = client.fetch_entry(pdb_id)
    struct = client.fetch_structure(pdb_id)
    if not struct.ok or not struct.data:
        raise ValueError(f"Could not retrieve structure {pdb_id} from the RCSB PDB: "
                         f"{struct.error or 'no data returned'}")
    text = struct.data if isinstance(struct.data, str) else str(struct.data)
    if len(text) > MAX_PDB_BYTES:
        raise ValueError(f"{pdb_id} is larger than this tool handles interactively.")

    protein, hetero = _parse_atoms(text)
    if not protein:
        raise ValueError(f"{pdb_id} contains no protein atoms.")
    groups = _ligand_groups(hetero)
    if not groups:
        raise ValueError(
            f"{pdb_id} has no bound small molecule once water, ions and crystallisation "
            "additives are excluded. It may be an apo (unbound) structure.")

    available = list_ligands(hetero)
    if ligand_code:
        wanted = [k for k in groups if k.split("|")[0] == ligand_code.strip().upper()]
        if not wanted:
            raise ValueError(
                f"{pdb_id} has no ligand {ligand_code!r}. It contains: "
                + ", ".join(sorted({a['ligand_code'] for a in available})))
        key = max(wanted, key=lambda k: len(groups[k]))
    else:
        key = max(groups, key=lambda k: len([a for a in groups[k] if a["element"] != "H"]))

    lig_atoms = groups[key]
    lig_res, lig_chain, lig_seq = key.split("|")

    lig_xyz = np.array([[a["x"], a["y"], a["z"]] for a in lig_atoms])
    pro_xyz = np.array([[a["x"], a["y"], a["z"]] for a in protein])

    # Pairwise distances, ligand atoms against protein atoms.
    d = np.sqrt(((lig_xyz[:, None, :] - pro_xyz[None, :, :]) ** 2).sum(-1))
    near_i, near_j = np.where(d <= CONTACT_CUTOFF)

    residues: dict[str, dict] = {}
    hbonds = []
    for i, j in zip(near_i.tolist(), near_j.tolist()):
        p = protein[j]
        lig = lig_atoms[i]
        dist = float(d[i, j])
        rkey = f"{p['res']} {p['chain']}{p['res_seq']}"
        entry_r = residues.setdefault(rkey, {
            "residue": p["res"], "chain": p["chain"], "residue_number": p["res_seq"],
            "closest_distance_angstrom": dist, "contact_count": 0})
        entry_r["contact_count"] += 1
        entry_r["closest_distance_angstrom"] = min(entry_r["closest_distance_angstrom"], dist)

        if (dist <= HBOND_MAX and p["element"] in HBOND_DONORS_ACCEPTORS
                and lig["element"] in HBOND_DONORS_ACCEPTORS):
            hbonds.append({
                "ligand_atom": lig["name"], "ligand_element": lig["element"],
                "protein_residue": rkey, "protein_atom": p["name"],
                "protein_element": p["element"],
                "distance_angstrom": round(dist, 2),
            })

    ranked = sorted(residues.values(), key=lambda r: r["closest_distance_angstrom"])
    for r in ranked:
        r["closest_distance_angstrom"] = round(r["closest_distance_angstrom"], 2)
    hbonds.sort(key=lambda h: h["distance_angstrom"])

    meta = entry.data if entry.ok and isinstance(entry.data, dict) else {}
    title = ((meta.get("struct") or {}).get("title")) if meta else None
    method = None
    if meta:
        exp = meta.get("exptl") or []
        if exp and isinstance(exp, list):
            method = (exp[0] or {}).get("method")
    resolution = None
    if meta:
        ri = (meta.get("rcsb_entry_info") or {}).get("resolution_combined")
        if isinstance(ri, list) and ri:
            resolution = ri[0]

    # A PDB fragment holding just the ligand and its contact residues, for the 3D viewer.
    keep_res = {(r["chain"], r["residue_number"]) for r in ranked[:24]}
    pocket_lines = []
    for line in text.splitlines():
        if line[:6] not in ("ATOM  ", "HETATM"):
            continue
        ch, seq, res = line[21:22].strip(), line[22:26].strip(), line[17:20].strip()
        if (ch, seq) in keep_res or (res == lig_res and ch == lig_chain and seq == lig_seq):
            pocket_lines.append(line)
    pocket_pdb = "\n".join(pocket_lines) + "\nEND\n"

    lig_only = "\n".join(l for l in pocket_lines
                         if l[17:20].strip() == lig_res and l[21:22].strip() == lig_chain
                         and l[22:26].strip() == lig_seq) + "\nEND\n"

    return {
        "ok": True,
        "pdb_id": pdb_id,
        "title": title,
        "experimental_method": method,
        "resolution_angstrom": resolution,
        "ligand": {
            "code": lig_res, "chain": lig_chain, "residue_number": lig_seq,
            "heavy_atom_count": len([a for a in lig_atoms if a["element"] != "H"]),
        },
        "other_ligands_in_structure": [a for a in available if a["key"] != key][:12],
        "contact_residues": ranked[:40],
        "contact_residue_count": len(ranked),
        "hydrogen_bond_candidates": hbonds[:30],
        "hydrogen_bond_candidate_count": len(hbonds),
        "pocket_pdb": pocket_pdb,
        "ligand_pdb": lig_only,
        "parameters": {"contact_cutoff_angstrom": CONTACT_CUTOFF,
                       "hydrogen_bond_cutoff_angstrom": HBOND_MAX},
        "provenance": {
            "source": "RCSB Protein Data Bank",
            "url": RCSB_FILES + pdb_id + ".pdb",
            "retrieved_at": struct.retrieved_at,
            "from_cache": struct.from_cache,
        },
        "software_versions": {"moleculab": APP_VERSION, "structures": STRUCTURES_VERSION},
        "epistemic_status": "experimental",
        "interpretation": (
            f"These contacts were measured in a deposited {method or 'experimental'} structure of "
            f"{pdb_id}. The ligand {lig_res} is where the experiment found it. Residues are "
            f"ranked by how close they come to the ligand, within {CONTACT_CUTOFF} angstrom."
        ),
        "limitations": [
            "This is an OBSERVED structure, not a prediction. MOLECULAB performed no docking and "
            "produced no pose.",
            "No binding affinity is reported. A short contact distance says two atoms are close; "
            "it does not give a binding free energy, and the two are not interchangeable.",
            "Hydrogen bonds are listed as CANDIDATES on heavy-atom distance and element alone. "
            "Most crystal structures do not resolve hydrogens, so geometry and protonation are "
            "not confirmed here.",
            "A crystal structure is one conformation under crystallisation conditions. It is not "
            "necessarily the physiological binding mode, and crystal packing can affect it.",
            "Alternate conformations beyond the first are ignored, and only the first model of an "
            "NMR ensemble is read.",
        ],
    }
