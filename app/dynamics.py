"""Molecular dynamics and implicit solvent, on top of RDKit's MMFF94 force field.

This module exists because MOLECULAB's conformer tool answers "what shapes can this molecule
adopt?" but not "what does it actually DO over time, and does water change that?". Those are
different questions and they need a different calculation.

**What this really is.** A velocity-Verlet integrator driven by analytic MMFF94 gradients that
RDKit computes. At each step the force on every atom is the negative gradient of the real MMFF94
potential (bond stretching, angle bending, torsions, out-of-plane, van der Waals, electrostatics).
Temperature is controlled by a Berendsen thermostat. The trajectory, the kinetic and potential
energies, the temperature and the RMSD from the starting structure are all genuine outputs of that
integration.

**What this is NOT, and the API says so on every response:**

- Not explicit-solvent MD. There are no water molecules. Solvent, when enabled, is an *implicit*
  model: MMFF94's electrostatics are screened with a distance-dependent dielectric, which is a
  crude stand-in for water and nothing more.
- Not a binding simulation. There is no protein, no receptor and no binding site.
- Not free-energy calculation. Nothing here produces a binding affinity, a solubility or a
  partition coefficient.
- Not long enough to be conformationally converged. These runs are picoseconds, on one CPU, in a
  browser request. That is enough to watch a molecule relax and flex; it is not enough to sample
  a conformational landscape, and it must never be reported as if it were.

A classical force field also cannot break or form bonds, so no chemistry happens in these runs.
"""

from __future__ import annotations

import math
import time

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, rdFreeSASA, rdMolAlign

from app.config import APP_VERSION

DYNAMICS_VERSION = "1.0.0"

# Physical constants in the unit system RDKit's MMFF94 uses: kcal/mol, angstrom, amu.
BOLTZMANN_KCAL = 0.0019872041          # kcal/(mol*K)
# 1 kcal/(mol*A*amu) -> A/fs^2 ; 1 kcal/mol = 418.4 amu*A^2/ps^2
FORCE_TO_ACCEL = 418.4 / 1000.0 / 1000.0 * 1000.0   # see _accelerations for the derivation

MAX_ATOMS = 120
MAX_STEPS = 4000
MAX_PS = 20.0

# Dielectric: 1.0 is vacuum. A distance-dependent model with a higher constant is the usual cheap
# stand-in for aqueous screening. It is an approximation, not a solvent.
SOLVENT_MODELS = {
    "vacuum": {"label": "Vacuum (no solvent)", "dielectric_model": 1, "dielectric": 1.0,
               "note": "No solvent at all. Electrostatics are unscreened, which overstates the "
                       "strength of internal hydrogen bonds and salt bridges."},
    "implicit_water": {"label": "Implicit water (distance-dependent dielectric)",
                       "dielectric_model": 2, "dielectric": 80.0,
                       "note": "Water is approximated by screening electrostatics with a "
                               "distance-dependent dielectric of 80. There are NO water "
                               "molecules in the system. This captures the fact that charges "
                               "attract each other less in water; it does not capture hydrogen "
                               "bonding to water, the hydrophobic effect, or any specific "
                               "solvent structure."},
    "implicit_organic": {"label": "Implicit organic solvent (dielectric 4)",
                         "dielectric_model": 2, "dielectric": 4.0,
                         "note": "A low-dielectric environment, loosely representing a nonpolar "
                                 "solvent or a buried protein interior. The same caveats as "
                                 "implicit water apply."},
}


def _prepare(smiles: str, seed: int = 0xC0FFEE) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Could not parse the structure: {smiles[:80]!r}")
    mol = Chem.AddHs(mol)
    if mol.GetNumAtoms() > MAX_ATOMS:
        raise ValueError(
            f"{mol.GetNumAtoms()} atoms with hydrogens; this runs molecules up to {MAX_ATOMS}. "
            "Larger systems need a dedicated MD engine, not a web request.")
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    if AllChem.EmbedMolecule(mol, params) != 0:
        raise ValueError("Could not generate a starting 3D structure for this molecule.")
    AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
    return mol


def _force_field(mol: Chem.Mol, solvent: str):
    spec = SOLVENT_MODELS.get(solvent)
    if spec is None:
        raise ValueError(f"Unknown solvent model {solvent!r}. "
                         f"Choose one of {sorted(SOLVENT_MODELS)}.")
    props = AllChem.MMFFGetMoleculeProperties(mol)
    if props is None:
        raise ValueError("MMFF94 has no parameters for this molecule, so it cannot be simulated.")
    props.SetMMFFDielectricModel(spec["dielectric_model"])
    props.SetMMFFDielectricConstant(spec["dielectric"])
    ff = AllChem.MMFFGetMoleculeForceField(mol, props)
    if ff is None:
        raise ValueError("Could not build an MMFF94 force field for this molecule.")
    return ff, spec


def _masses(mol: Chem.Mol) -> np.ndarray:
    return np.array([a.GetMass() for a in mol.GetAtoms()], dtype=float)


def _accelerations(ff, masses: np.ndarray, n: int) -> np.ndarray:
    """a = F/m, with F = -grad(E).

    RDKit returns the gradient in kcal/(mol*angstrom). Converting to angstrom/fs^2:
        1 kcal/mol = 418.4 amu*A^2/ps^2 = 418.4e-6 amu*A^2/fs^2
    so  a [A/fs^2] = grad [kcal/(mol*A)] / m [amu] * 418.4e-6.
    """
    grad = np.array(ff.CalcGrad(), dtype=float).reshape(n, 3)
    return -grad / masses[:, None] * 418.4e-6


def _temperature(vel: np.ndarray, masses: np.ndarray, n_dof: int) -> float:
    # KE = 1/2 sum m v^2, in amu*A^2/fs^2 -> kcal/mol by dividing by 418.4e-6
    ke = 0.5 * float(np.sum(masses[:, None] * vel ** 2)) / 418.4e-6
    if n_dof <= 0:
        return 0.0
    return 2.0 * ke / (n_dof * BOLTZMANN_KCAL)


def _kinetic(vel: np.ndarray, masses: np.ndarray) -> float:
    return 0.5 * float(np.sum(masses[:, None] * vel ** 2)) / 418.4e-6


def run_dynamics(smiles: str, *, temperature_k: float = 300.0, steps: int = 1000,
                 timestep_fs: float = 1.0, solvent: str = "implicit_water",
                 seed: int = 0xC0FFEE, frame_every: int = 25) -> dict:
    """Integrate real Newtonian dynamics on the MMFF94 potential.

    Returns the trajectory, the energy and temperature traces, and an explicit statement of what
    the run does and does not establish.
    """
    steps = max(10, min(int(steps), MAX_STEPS))
    timestep_fs = float(timestep_fs)
    if not 0.1 <= timestep_fs <= 2.0:
        raise ValueError("Timestep must be between 0.1 and 2.0 fs. A classical bond vibration "
                         "needs several steps per period, and above ~2 fs the integration "
                         "becomes unstable unless bonds to hydrogen are constrained.")
    if steps * timestep_fs / 1000.0 > MAX_PS:
        raise ValueError(f"That is longer than the {MAX_PS} ps limit for an interactive run.")
    if not 1.0 <= float(temperature_k) <= 1000.0:
        raise ValueError("Temperature must be between 1 K and 1000 K.")

    started = time.time()
    mol = _prepare(smiles, seed)
    ff, spec = _force_field(mol, solvent)
    n = mol.GetNumAtoms()
    masses = _masses(mol)

    pos = np.array(ff.Positions(), dtype=float).reshape(n, 3)
    start_pos = pos.copy()

    # Maxwell-Boltzmann velocities at the target temperature, then remove net translation so the
    # molecule does not simply drift out of view.
    rng = np.random.default_rng(seed)
    sigma = np.sqrt(BOLTZMANN_KCAL * temperature_k / masses) * math.sqrt(418.4e-6)
    vel = rng.normal(0.0, 1.0, (n, 3)) * sigma[:, None]
    vel -= np.average(vel, axis=0, weights=masses)
    n_dof = max(3 * n - 3, 1)

    def set_positions(p: np.ndarray) -> None:
        flat = p.reshape(-1)
        for i in range(len(flat)):
            ff.Positions()[i] = float(flat[i])

    # RDKit's force field reads coordinates from the conformer, so write there and re-init.
    conf = mol.GetConformer()

    def push(p: np.ndarray) -> None:
        for i in range(n):
            conf.SetAtomPosition(i, (float(p[i, 0]), float(p[i, 1]), float(p[i, 2])))

    acc = _accelerations(ff, masses, n)
    frames, trace = [], []
    thermostat_tau = max(timestep_fs * 10.0, 50.0)          # Berendsen coupling time, fs

    for step in range(steps):
        # velocity-Verlet
        pos = pos + vel * timestep_fs + 0.5 * acc * timestep_fs ** 2
        push(pos)
        ff_new, _ = _force_field(mol, solvent)
        new_acc = _accelerations(ff_new, masses, n)
        vel = vel + 0.5 * (acc + new_acc) * timestep_fs
        acc = new_acc
        ff = ff_new

        # Berendsen thermostat: gently rescale toward the target temperature.
        t_now = _temperature(vel, masses, n_dof)
        if t_now > 1e-9:
            lam = math.sqrt(1.0 + (timestep_fs / thermostat_tau) *
                            (temperature_k / t_now - 1.0))
            lam = min(max(lam, 0.8), 1.25)
            vel *= lam

        if step % max(1, frame_every) == 0 or step == steps - 1:
            pe = float(ff.CalcEnergy())
            ke = _kinetic(vel, masses)
            trace.append({
                "step": step,
                "time_fs": round(step * timestep_fs, 3),
                "potential_energy_kcal_per_mol": round(pe, 4),
                "kinetic_energy_kcal_per_mol": round(ke, 4),
                "total_energy_kcal_per_mol": round(pe + ke, 4),
                "temperature_k": round(_temperature(vel, masses, n_dof), 2),
                "rmsd_from_start_angstrom": round(
                    float(np.sqrt(np.mean(np.sum((pos - start_pos) ** 2, axis=1)))), 4),
            })
            frames.append(Chem.MolToMolBlock(mol, confId=0))

    push(pos)
    final_energy = float(ff.CalcEnergy())
    temps = [t["temperature_k"] for t in trace]
    total = [t["total_energy_kcal_per_mol"] for t in trace]

    return {
        "ok": True,
        "parameters": {
            "smiles": smiles, "temperature_k": temperature_k, "steps": steps,
            "timestep_fs": timestep_fs, "simulated_time_ps": round(steps * timestep_fs / 1000, 4),
            "solvent": solvent, "solvent_label": spec["label"], "seed": seed,
            "atoms_including_hydrogens": n,
        },
        "method": ("Velocity-Verlet integration of Newton's equations on the MMFF94 potential, "
                   "with analytic gradients from RDKit and a Berendsen thermostat."),
        "trajectory_frames": frames,
        "trace": trace,
        "summary": {
            "frames": len(frames),
            "final_potential_energy_kcal_per_mol": round(final_energy, 4),
            "mean_temperature_k": round(sum(temps) / len(temps), 2) if temps else None,
            "target_temperature_k": temperature_k,
            "total_energy_drift_kcal_per_mol": (round(total[-1] - total[0], 4)
                                                if len(total) > 1 else None),
            "final_rmsd_from_start_angstrom": trace[-1]["rmsd_from_start_angstrom"] if trace else None,
        },
        "solvent_note": spec["note"],
        "software_versions": {"moleculab": APP_VERSION, "dynamics": DYNAMICS_VERSION,
                              "rdkit": Chem.rdBase.rdkitVersion, "force_field": "MMFF94"},
        "interpretation": (
            "The molecule was given random velocities drawn from a Maxwell-Boltzmann distribution "
            "at the target temperature and then integrated forward in time. Temperature, kinetic "
            "energy and RMSD are real outputs of that integration. A rising RMSD means the "
            "molecule is flexing away from where it started."
        ),
        "limitations": [
            "This is an in-vacuum or IMPLICIT-solvent run. There are no water molecules. "
            + spec["note"],
            f"The run is {round(steps * timestep_fs / 1000, 3)} ps long. That is long enough to "
            "watch a molecule relax and flex, and FAR too short to sample its conformational "
            "landscape or to draw any thermodynamic conclusion.",
            "MMFF94 is a classical force field: it cannot break or form bonds, so no chemical "
            "reaction can occur, and it has no electronic structure.",
            "A Berendsen thermostat controls the average temperature but does not generate a "
            "correct canonical ensemble, so fluctuation-based quantities are not valid here.",
            "No protein, receptor or binding site is present. Nothing here is a binding "
            "simulation and no affinity is produced.",
            "Energies are comparable only between frames of THIS molecule in THIS run. They are "
            "not comparable between different molecules.",
        ],
    }


def solvent_effect(smiles: str, seed: int = 0xC0FFEE) -> dict:
    """Optimise the same molecule under each dielectric model and report what changed.

    This is the honest version of "does solvent matter?": it shows how an implicit dielectric
    changes the optimised geometry and energy, and states plainly that it is not real solvation.
    """
    results = []
    reference = None
    for key, spec in SOLVENT_MODELS.items():
        mol = _prepare(smiles, seed)
        ff, _ = _force_field(mol, key)
        ff.Minimize(maxIts=2000)
        energy = float(ff.CalcEnergy())
        if reference is None:
            reference = mol
            rmsd = 0.0
        else:
            try:
                rmsd = float(rdMolAlign.GetBestRMS(Chem.Mol(mol), Chem.Mol(reference)))
            except Exception:                                  # noqa: BLE001
                rmsd = float("nan")
        radii = rdFreeSASA.classifyAtoms(mol)
        results.append({
            "model": key,
            "label": spec["label"],
            "dielectric_constant": spec["dielectric"],
            "optimised_energy_kcal_per_mol": round(energy, 4),
            "rmsd_vs_vacuum_angstrom": round(rmsd, 4) if rmsd == rmsd else None,
            "solvent_accessible_surface_area_A2": round(
                float(rdFreeSASA.CalcSASA(mol, radii)), 2),
            "note": spec["note"],
            "molblock": Chem.MolToMolBlock(mol),
        })

    return {
        "ok": True,
        "smiles": smiles,
        "models": results,
        "method": ("MMFF94 optimisation repeated under each dielectric model, plus a "
                   "solvent-accessible surface area from RDKit's FreeSASA implementation."),
        "software_versions": {"moleculab": APP_VERSION, "dynamics": DYNAMICS_VERSION,
                              "rdkit": Chem.rdBase.rdkitVersion, "force_field": "MMFF94"},
        "interpretation": (
            "Energies under different dielectric models are NOT comparable to each other as "
            "numbers; each is the minimum of a different potential. What is meaningful is the "
            "geometry change: the RMSD column shows how far the optimised shape moves when "
            "electrostatics are screened as they would be in a polar environment."
        ),
        "limitations": [
            "A dielectric constant is not a solvent. There are no water molecules, no hydrogen "
            "bonds to solvent, and no hydrophobic effect in this model.",
            "Solvent-accessible surface area is a geometric measure of the structure's exposed "
            "surface. It is not a solubility and not a measurement.",
            "Energies from different dielectric models cannot be subtracted to give a solvation "
            "free energy. That calculation is not implemented.",
            "Every value here is computed. None is an experimental measurement.",
        ],
    }
