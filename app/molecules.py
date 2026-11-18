"""Molecular intelligence: structure validation, standardization, descriptors, fingerprints, similarity.

Scientific contract of this module:

* **Invalid structures are rejected, never repaired silently.** `resolve_structure` returns a record with
  `valid=False` and the parser's own message. Nothing downstream receives a guessed molecule.
* **The submitted structure and the standardized structure are kept separate.** `input_value` is stored
  verbatim, `original_smiles` is the canonical form of exactly what was submitted, and
  `standardized_smiles` is the form used for comparison and modelling. Both are always available.
* **Every standardization step is recorded.** `transformations` lists each stage, whether it changed the
  molecule, and the before/after SMILES, so any difference between submitted and standardized structure
  can be audited rather than taken on trust.
* **Stereochemistry is preserved.** The pipeline deliberately excludes any stage that discards stereo
  (no `StereoParent`), and `stereo_centers`/`has_defined_stereo` are reported. A test pins this.
* **Tautomer canonicalization is NOT applied.** It can silently change which tautomer a record claims, and
  for activity data the tautomer reported by the depositor is part of the record. `tautomer_canonical_smiles`
  is computed separately, on request, and never replaces the standardized structure.
* **Similarity is similarity.** `tanimoto` compares fingerprints. It is a structural measure and says
  nothing about biological activity; naming and the screening layer keep that distinction.

Resource limits are enforced on parsing (atom count, input length) because structure input can arrive from
an upload, and an unbounded molblock is a denial-of-service vector.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

from rdkit import Chem, DataStructs, RDLogger
from rdkit.Chem import Descriptors, rdFingerprintGenerator, rdMolDescriptors
from rdkit.Chem.MolStandardize import rdMolStandardize
from rdkit.Chem.Scaffolds import MurckoScaffold

log = logging.getLogger("moleculab")

# RDKit logs parse failures to stderr; we capture and report them in the record instead.
RDLogger.DisableLog("rdApp.*")

#: Bumped when descriptor or standardization logic changes, so stored values stay traceable.
CHEMISTRY_VERSION = "1.0"
FINGERPRINT_SPEC = {"type": "Morgan", "radius": 2, "bits": 2048, "name": "ECFP4-like"}

# --- resource limits (structure input can come from an upload) ---
MAX_INPUT_CHARS = 100_000
MAX_ATOMS = 300

_morgan = rdFingerprintGenerator.GetMorganGenerator(
    radius=FINGERPRINT_SPEC["radius"], fpSize=FINGERPRINT_SPEC["bits"])


@dataclass
class StructureRecord:
    """One resolved structure. Submitted and standardized forms both retained."""
    input_format: str
    input_value: str
    valid: bool
    error: str | None = None
    original_smiles: str | None = None       # canonical SMILES of exactly what was submitted
    standardized_smiles: str | None = None   # used for comparison/modelling
    standardization_changed: bool = False
    transformations: list[dict] = field(default_factory=list)
    inchi: str | None = None
    inchikey: str | None = None
    skeleton_key: str | None = None          # InChIKey first block: connectivity, ignores stereo/salt
    scaffold_smiles: str | None = None
    descriptors: dict = field(default_factory=dict)
    stereo_centers: int = 0
    unspecified_stereo_centers: int = 0
    has_defined_stereo: bool = False
    chemistry_version: str = CHEMISTRY_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- parsing

def _too_long(value: str) -> str | None:
    if value is None or not str(value).strip():
        return "empty structure input"
    if len(value) > MAX_INPUT_CHARS:
        return f"structure input exceeds {MAX_INPUT_CHARS} characters"
    return None


def parse_smiles(smiles: str) -> tuple[Chem.Mol | None, str | None]:
    """Parse SMILES. Returns (mol, error); never raises, never guesses."""
    problem = _too_long(smiles)
    if problem:
        return None, problem
    mol = Chem.MolFromSmiles(smiles.strip())
    if mol is None:
        return None, "invalid SMILES: RDKit could not parse the structure"
    if mol.GetNumAtoms() == 0:
        return None, "structure contains no atoms"
    if mol.GetNumAtoms() > MAX_ATOMS:
        return None, f"structure has {mol.GetNumAtoms()} atoms; limit is {MAX_ATOMS}"
    return mol, None


def parse_molblock(molblock: str) -> tuple[Chem.Mol | None, str | None]:
    """Parse a MOL/SDF block (single record). Returns (mol, error)."""
    problem = _too_long(molblock)
    if problem:
        return None, problem
    try:
        mol = Chem.MolFromMolBlock(molblock, sanitize=True, removeHs=True)
    except Exception as exc:  # malformed input must not propagate
        return None, f"invalid molblock: {type(exc).__name__}"
    if mol is None:
        return None, "invalid molblock: RDKit could not parse the structure"
    if mol.GetNumAtoms() == 0:
        return None, "structure contains no atoms"
    if mol.GetNumAtoms() > MAX_ATOMS:
        return None, f"structure has {mol.GetNumAtoms()} atoms; limit is {MAX_ATOMS}"
    return mol, None


def parse_inchi(inchi: str) -> tuple[Chem.Mol | None, str | None]:
    problem = _too_long(inchi)
    if problem:
        return None, problem
    mol = Chem.MolFromInchi(inchi.strip())
    if mol is None:
        return None, "invalid InChI: RDKit could not parse the structure"
    return mol, None


# ------------------------------------------------------------------- standardization

def standardize(mol: Chem.Mol) -> tuple[Chem.Mol, list[dict]]:
    """Standardize a molecule, recording every stage.

    Stages, in order:

    1. `Cleanup`         - sanitization, normalization of functional groups, metal disconnection.
    2. `FragmentParent`  - keeps the largest organic fragment, dropping salts and solvates.
    3. `Uncharger`       - neutralizes charges where chemically reasonable.

    Deliberately **not** applied: `StereoParent` (would discard stereochemistry) and tautomer
    canonicalization (would change which tautomer the record asserts).

    Returns the standardized molecule and one entry per stage with before/after SMILES.
    """
    transformations: list[dict] = []
    current = mol
    before = Chem.MolToSmiles(current)

    stages = (
        ("cleanup", rdMolStandardize.Cleanup,
         "sanitize, normalize functional groups, disconnect metals"),
        ("fragment_parent", rdMolStandardize.FragmentParent,
         "keep the largest organic fragment (removes salts and solvates)"),
        ("uncharge", rdMolStandardize.Uncharger().uncharge,
         "neutralize charges where chemically reasonable"),
    )

    for name, fn, description in stages:
        try:
            result = fn(current)
        except Exception as exc:
            transformations.append({"step": name, "description": description, "changed": False,
                                    "error": f"{type(exc).__name__}: {exc}",
                                    "smiles_before": before, "smiles_after": before})
            continue
        if result is None:
            transformations.append({"step": name, "description": description, "changed": False,
                                    "error": "stage returned no molecule",
                                    "smiles_before": before, "smiles_after": before})
            continue
        after = Chem.MolToSmiles(result)
        transformations.append({"step": name, "description": description,
                                "changed": after != before,
                                "smiles_before": before, "smiles_after": after})
        current, before = result, after

    return current, transformations


def canonical_tautomer(mol: Chem.Mol) -> str | None:
    """Canonical tautomer, computed on request only. Never replaces the standardized structure:
    for an activity record, the tautomer the depositor reported is part of the record."""
    try:
        return Chem.MolToSmiles(rdMolStandardize.CanonicalTautomer(mol))
    except Exception:
        return None


# ----------------------------------------------------------------------- descriptors

def descriptors(mol: Chem.Mol) -> dict:
    """Physicochemical descriptors computed locally by RDKit.

    These are MOLECULAB's own computed values. Provider-supplied properties (e.g. ChEMBL's `alogp`)
    are stored under their own `chembl_*` names and never merged with these.
    """
    return {
        "molecular_formula": rdMolDescriptors.CalcMolFormula(mol),
        "molecular_weight": round(Descriptors.MolWt(mol), 4),
        "exact_mass": round(Descriptors.ExactMolWt(mol), 5),
        "heavy_atom_count": mol.GetNumHeavyAtoms(),
        "tpsa": round(Descriptors.TPSA(mol), 3),
        "clogp": round(Descriptors.MolLogP(mol), 4),          # Wildman-Crippen estimate, not measured
        "h_bond_donors": rdMolDescriptors.CalcNumHBD(mol),
        "h_bond_acceptors": rdMolDescriptors.CalcNumHBA(mol),
        "rotatable_bonds": rdMolDescriptors.CalcNumRotatableBonds(mol),
        "ring_count": rdMolDescriptors.CalcNumRings(mol),
        "aromatic_ring_count": rdMolDescriptors.CalcNumAromaticRings(mol),
        "fraction_csp3": round(rdMolDescriptors.CalcFractionCSP3(mol), 4),
        "formal_charge": Chem.GetFormalCharge(mol),
        "num_radical_electrons": Descriptors.NumRadicalElectrons(mol),
        "molar_refractivity": round(Descriptors.MolMR(mol), 4),
        "qed": round(Descriptors.qed(mol), 4),
    }


def stereo_summary(mol: Chem.Mol) -> dict:
    """Stereocentre counts, including how many are left unspecified."""
    try:
        centers = Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False)
    except Exception:
        centers = []
    unspecified = sum(1 for _, tag in centers if tag in ("?", None))
    bond_stereo = sum(1 for b in mol.GetBonds()
                      if b.GetStereo() not in (Chem.BondStereo.STEREONONE,))
    return {
        "stereo_centers": len(centers),
        "unspecified_stereo_centers": unspecified,
        "has_defined_stereo": bool((len(centers) - unspecified) > 0 or bond_stereo > 0),
    }


# ---------------------------------------------------------------------- fingerprints

def fingerprint(mol: Chem.Mol):
    """Morgan (ECFP4-like) fingerprint, 2048 bits. One generator, so every comparison is like-for-like."""
    return _morgan.GetFingerprint(mol)


def tanimoto(mol_a: Chem.Mol, mol_b: Chem.Mol) -> float:
    """Tanimoto similarity of Morgan fingerprints.

    A **structural** measure. High similarity is not evidence of shared biological activity, and this
    value must never be presented as, or converted into, predicted activity.
    """
    return round(DataStructs.TanimotoSimilarity(fingerprint(mol_a), fingerprint(mol_b)), 6)


def bulk_tanimoto(query: Chem.Mol, mols: list[Chem.Mol]) -> list[float]:
    fps = [fingerprint(m) for m in mols]
    sims = DataStructs.BulkTanimotoSimilarity(fingerprint(query), fps) if fps else []
    return [round(s, 6) for s in sims]


def murcko_scaffold(mol: Chem.Mol) -> str | None:
    """Bemis-Murcko scaffold SMILES. Used for scaffold-based dataset splitting."""
    try:
        scaffold = MurckoScaffold.GetScaffoldForMol(mol)
        return Chem.MolToSmiles(scaffold) if scaffold is not None else None
    except Exception:
        return None


# ------------------------------------------------------------------------- resolution

PARSERS = {"smiles": parse_smiles, "molblock": parse_molblock, "inchi": parse_inchi}


def resolve_structure(value: str, input_format: str = "smiles") -> StructureRecord:
    """Validate, standardize and describe one structure.

    The returned record always carries both the submitted structure (`original_smiles`) and the
    standardized one (`standardized_smiles`), plus the full transformation log.
    """
    fmt = (input_format or "smiles").lower()
    if fmt not in PARSERS:
        return StructureRecord(input_format=fmt, input_value=str(value)[:200], valid=False,
                               error=f"unsupported input format '{fmt}'; use one of {sorted(PARSERS)}")

    mol, error = PARSERS[fmt](value)
    if mol is None:
        return StructureRecord(input_format=fmt, input_value=str(value)[:MAX_INPUT_CHARS],
                               valid=False, error=error)

    original_smiles = Chem.MolToSmiles(mol)
    std, transformations = standardize(mol)
    standardized_smiles = Chem.MolToSmiles(std)

    try:
        inchi = Chem.MolToInchi(std) or None
        inchikey = Chem.MolToInchiKey(std) or None
    except Exception:
        inchi, inchikey = None, None

    stereo = stereo_summary(std)
    return StructureRecord(
        input_format=fmt,
        input_value=str(value)[:MAX_INPUT_CHARS],
        valid=True,
        original_smiles=original_smiles,
        standardized_smiles=standardized_smiles,
        standardization_changed=standardized_smiles != original_smiles,
        transformations=transformations,
        inchi=inchi,
        inchikey=inchikey,
        skeleton_key=inchikey.split("-")[0] if inchikey else None,
        scaffold_smiles=murcko_scaffold(std),
        descriptors=descriptors(std),
        stereo_centers=stereo["stereo_centers"],
        unspecified_stereo_centers=stereo["unspecified_stereo_centers"],
        has_defined_stereo=stereo["has_defined_stereo"],
    )


# ------------------------------------------------------------------------ duplicates

def duplicate_groups(records: list[StructureRecord], ignore_stereo: bool = False) -> dict[str, list[int]]:
    """Group record indices by identity key.

    Default identity is the full InChIKey, so stereoisomers are **distinct** molecules. With
    `ignore_stereo=True` the connectivity-only skeleton key is used, which deliberately merges
    stereoisomers; callers that do this must say so, because enantiomers can differ in activity.
    """
    groups: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        key = (record.skeleton_key if ignore_stereo else record.inchikey)
        if not key:
            continue
        groups.setdefault(key, []).append(index)
    return {key: idx for key, idx in groups.items() if len(idx) > 1}


def nearest_neighbours(query_smiles: str, candidates: list[tuple[str, str]],
                       top_n: int = 5) -> list[dict]:
    """Most structurally similar candidates to a query.

    `candidates` is [(identifier, smiles)]. Unparseable candidates are skipped and counted by the
    caller rather than silently treated as dissimilar. Results are structural neighbours only.
    """
    query, error = parse_smiles(query_smiles)
    if query is None:
        return []
    parsed, ids = [], []
    for identifier, smiles in candidates:
        mol, _ = parse_smiles(smiles or "")
        if mol is not None:
            parsed.append(mol)
            ids.append(identifier)
    sims = bulk_tanimoto(query, parsed)
    rows = [{"identifier": i, "tanimoto": s,
             "measure": "structural similarity (Morgan/Tanimoto); not evidence of activity"}
            for i, s in zip(ids, sims)]
    rows.sort(key=lambda r: r["tanimoto"], reverse=True)
    return rows[:top_n]
