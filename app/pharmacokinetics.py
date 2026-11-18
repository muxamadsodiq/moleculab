"""Pharmacokinetic simulation: what happens to a dose over time, in a given species.

This answers the question "I give this much, this often, for this long - what is the concentration
in the body?" That is a real, standard calculation, and it is solved here by integrating the
compartmental equations rather than by guessing.

**What is genuinely computed**

- One- or two-compartment disposition with first-order absorption, integrated with a fixed-step
  RK4 solver. Multiple doses accumulate through the model itself, not by a superposition shortcut,
  so a changing regimen is handled correctly.
- Allometric scaling between species, using the conventional exponents (0.75 for clearance,
  1.0 for volume). This is a published, widely used interspecies scaling method.
- Standard exposure metrics derived from the simulated curve: Cmax, Tmax, AUC by the trapezoidal
  rule, terminal half-life, accumulation ratio and trough concentrations.

**What this is NOT, and the response says so every time**

- Not a prediction from structure. PK parameters are INPUTS. MOLECULAB cannot derive clearance,
  volume of distribution or bioavailability from a molecular structure, and does not try. The
  researcher supplies measured or literature parameters, and the output is only as good as they
  are.
- Not an efficacy or safety prediction. A concentration is not an effect. Nothing here says
  whether a dose works or whether it is safe.
- Not a physiological simulation. No organ, tissue, disease state or individual patient is
  modelled. There is no "before and after" picture of an animal or a person, because no method
  exists that would make one honest.
- Not a substitute for a pharmacokinetic study. These are textbook equations with assumed
  parameters, and real subjects vary enormously from them.

Allometric scaling in particular is an approximation that fails for compounds cleared by routes
that do not scale with body size, and the response says so.
"""

from __future__ import annotations

import math

from app.config import APP_VERSION

PK_VERSION = "1.0.0"

# Reference body weights, kilograms. Conventional values used in preclinical scaling.
SPECIES = {
    "human": {"label": "Human (adult)", "weight_kg": 70.0,
              "note": "A 70 kg reference adult. Real patients vary widely in weight, organ "
                      "function, genetics and co-medication."},
    "rat": {"label": "Rat", "weight_kg": 0.25,
            "note": "A 250 g laboratory rat."},
    "mouse": {"label": "Mouse", "weight_kg": 0.025,
              "note": "A 25 g laboratory mouse."},
    "rabbit": {"label": "Rabbit", "weight_kg": 2.5,
               "note": "A 2.5 kg laboratory rabbit."},
    "dog": {"label": "Dog (beagle)", "weight_kg": 10.0,
            "note": "A 10 kg beagle, the usual non-rodent species in preclinical work."},
}

# Allometric exponents. Clearance scales with metabolic rate, volume with body size.
EXPONENT_CL = 0.75
EXPONENT_V = 1.00

MAX_DURATION_H = 24 * 180          # six months
MAX_POINTS = 6000
MIN_STEP_H = 0.005

# A small set of well-characterised drugs so the tool can be used immediately. Every value is a
# human population mean from the published literature and is labelled as such; none is measured
# by MOLECULAB.
REFERENCE_DRUGS = {
    "caffeine": {"label": "Caffeine", "cl_l_per_h": 6.0, "vd_l": 36.0, "ka_per_h": 1.5,
                 "f": 1.00, "source": "typical adult population values",
                 "smiles": "CN1C=NC2=C1C(=O)N(C)C(=O)N2C"},
    "paracetamol": {"label": "Paracetamol (acetaminophen)", "cl_l_per_h": 21.0, "vd_l": 67.0,
                    "ka_per_h": 2.0, "f": 0.88, "source": "typical adult population values",
                    "smiles": "CC(=O)NC1=CC=C(O)C=C1"},
    "ibuprofen": {"label": "Ibuprofen", "cl_l_per_h": 3.5, "vd_l": 10.0, "ka_per_h": 2.0,
                  "f": 0.92, "source": "typical adult population values",
                  "smiles": "CC(C)CC1=CC=C(C=C1)C(C)C(=O)O"},
    "baricitinib": {"label": "Baricitinib", "cl_l_per_h": 8.9, "vd_l": 76.0, "ka_per_h": 1.0,
                    "f": 0.79, "source": "typical adult population values",
                    "smiles": "CC1(CC(C1)(CC#N)N1C=C(C=N1)C1=NC=NC2=C1C=CN2)S(=O)(=O)CC"},
}


def species_table() -> dict:
    return {
        "species": [{"id": k, **v} for k, v in SPECIES.items()],
        "reference_drugs": [{"id": k, **v} for k, v in REFERENCE_DRUGS.items()],
        "allometry": {
            "clearance_exponent": EXPONENT_CL,
            "volume_exponent": EXPONENT_V,
            "formula": "parameter_species = parameter_human * (W_species / W_human) ** exponent",
            "warning": ("Allometric scaling is an approximation. It assumes the same elimination "
                        "pathway operates in both species and scales with body size. It fails "
                        "for compounds cleared by species-specific enzymes, by routes that do "
                        "not scale with size, or where protein binding differs materially."),
        },
        "note": ("Reference drug parameters are published human population means, supplied so "
                 "the tool can be used immediately. They are NOT measurements made by MOLECULAB, "
                 "and they are not specific to any individual."),
    }


def scale_to_species(cl_human: float, vd_human: float, species: str) -> dict:
    """Allometric scaling from human parameters to another species."""
    if species not in SPECIES:
        raise ValueError(f"Unknown species {species!r}. Known: {sorted(SPECIES)}")
    w_h = SPECIES["human"]["weight_kg"]
    w_s = SPECIES[species]["weight_kg"]
    ratio = w_s / w_h
    return {
        "species": species,
        "weight_kg": w_s,
        "clearance_l_per_h": cl_human * (ratio ** EXPONENT_CL),
        "volume_l": vd_human * (ratio ** EXPONENT_V),
        "scaled_from": "human",
        "weight_ratio": ratio,
    }


def _derivatives(state: tuple[float, float, float], ka: float, k10: float,
                 k12: float, k21: float) -> tuple[float, float, float]:
    """d/dt for depot, central and peripheral amounts."""
    depot, central, periph = state
    d_depot = -ka * depot
    d_central = ka * depot - k10 * central - k12 * central + k21 * periph
    d_periph = k12 * central - k21 * periph
    return d_depot, d_central, d_periph


def simulate(*, dose_mg: float, interval_h: float, duration_h: float,
             clearance_l_per_h: float, volume_l: float, ka_per_h: float = 1.0,
             bioavailability: float = 1.0, species: str = "human",
             scale_from_human: bool = False,
             q_l_per_h: float | None = None, v2_l: float | None = None,
             molecular_weight: float | None = None) -> dict:
    """Integrate a dosing regimen and return the concentration-time course.

    `interval_h` of 0 means a single dose. With `scale_from_human`, the clearance and volume are
    taken as human values and scaled allometrically to `species` first.
    """
    if dose_mg <= 0:
        raise ValueError("Dose must be greater than zero.")
    if clearance_l_per_h <= 0 or volume_l <= 0:
        raise ValueError("Clearance and volume of distribution must be greater than zero.")
    if ka_per_h <= 0:
        raise ValueError("Absorption rate constant must be greater than zero.")
    if not 0 < bioavailability <= 1:
        raise ValueError("Bioavailability is a fraction between 0 and 1.")
    if duration_h <= 0 or duration_h > MAX_DURATION_H:
        raise ValueError(f"Duration must be between 0 and {MAX_DURATION_H} hours (six months).")
    if interval_h < 0:
        raise ValueError("Dosing interval cannot be negative.")
    if species not in SPECIES:
        raise ValueError(f"Unknown species {species!r}. Known: {sorted(SPECIES)}")

    scaling = None
    cl, vd = float(clearance_l_per_h), float(volume_l)
    if scale_from_human and species != "human":
        scaling = scale_to_species(cl, vd, species)
        cl, vd = scaling["clearance_l_per_h"], scaling["volume_l"]

    two_compartment = q_l_per_h is not None and v2_l is not None and q_l_per_h > 0 and v2_l > 0
    k10 = cl / vd
    k12 = (q_l_per_h / vd) if two_compartment else 0.0
    k21 = (q_l_per_h / v2_l) if two_compartment else 0.0

    # Step size: small enough to resolve the fastest process, bounded for responsiveness.
    fastest = max(ka_per_h, k10, k12, k21)
    step = max(MIN_STEP_H, min(0.05, 0.05 / max(fastest, 1e-6)))
    n_steps = int(duration_h / step)
    if n_steps > MAX_POINTS * 20:
        step = duration_h / (MAX_POINTS * 20)
        n_steps = MAX_POINTS * 20
    record_every = max(1, n_steps // MAX_POINTS)

    dose_times = ([0.0] if interval_h == 0
                  else [i * interval_h for i in range(int(duration_h // interval_h) + 1)
                        if i * interval_h < duration_h])
    dose_set = {round(t, 6) for t in dose_times}

    depot = central = periph = 0.0
    series, doses_given = [], 0
    next_dose = 0

    for i in range(n_steps + 1):
        t = i * step
        # Administer any dose due at or before this time.
        while next_dose < len(dose_times) and dose_times[next_dose] <= t + 1e-9:
            depot += dose_mg * bioavailability
            doses_given += 1
            next_dose += 1

        if i % record_every == 0 or i == n_steps:
            conc = central / vd                                  # mg/L
            point = {
                "time_h": round(t, 4),
                "concentration_mg_per_l": round(conc, 6),
                "amount_central_mg": round(central, 6),
                "amount_depot_mg": round(depot, 6),
            }
            if molecular_weight and molecular_weight > 0:
                # mg/L -> micromolar
                point["concentration_um"] = round(conc / molecular_weight * 1000.0, 6)
            series.append(point)

        if i == n_steps:
            break

        # RK4
        s = (depot, central, periph)
        k1 = _derivatives(s, ka_per_h, k10, k12, k21)
        s2 = tuple(s[j] + 0.5 * step * k1[j] for j in range(3))
        k2 = _derivatives(s2, ka_per_h, k10, k12, k21)
        s3 = tuple(s[j] + 0.5 * step * k2[j] for j in range(3))
        k3 = _derivatives(s3, ka_per_h, k10, k12, k21)
        s4 = tuple(s[j] + step * k3[j] for j in range(3))
        k4 = _derivatives(s4, ka_per_h, k10, k12, k21)
        depot, central, periph = (
            max(0.0, s[j] + (step / 6.0) * (k1[j] + 2 * k2[j] + 2 * k3[j] + k4[j]))
            for j in range(3)
        )

    concs = [p["concentration_mg_per_l"] for p in series]
    times = [p["time_h"] for p in series]
    cmax = max(concs) if concs else 0.0
    tmax = times[concs.index(cmax)] if concs else 0.0

    # AUC over the whole simulated period, trapezoidal.
    auc = 0.0
    for i in range(1, len(series)):
        auc += 0.5 * (concs[i] + concs[i - 1]) * (times[i] - times[i - 1])

    half_life = math.log(2) / k10 if k10 > 0 else None

    # Trough before each dose after the first.
    troughs = []
    for dt in dose_times[1:]:
        before = [p for p in series if p["time_h"] < dt]
        if before:
            troughs.append({"time_h": dt, "trough_mg_per_l": before[-1]["concentration_mg_per_l"]})

    accumulation = None
    if interval_h > 0 and k10 > 0:
        accumulation = 1.0 / (1.0 - math.exp(-k10 * interval_h))

    time_to_steady_state = (half_life * 5) if half_life else None

    return {
        "ok": True,
        "series": series,
        "doses": {"count": doses_given, "times_h": dose_times[:200],
                  "dose_mg": dose_mg, "interval_h": interval_h},
        "parameters_used": {
            "species": species, "species_label": SPECIES[species]["label"],
            "body_weight_kg": SPECIES[species]["weight_kg"],
            "clearance_l_per_h": round(cl, 5), "volume_l": round(vd, 5),
            "ka_per_h": ka_per_h, "bioavailability": bioavailability,
            "model": "two-compartment" if two_compartment else "one-compartment",
            "k10_per_h": round(k10, 6),
            "integration": {"method": "RK4", "step_h": round(step, 5), "steps": n_steps},
        },
        "allometric_scaling": scaling,
        "metrics": {
            "cmax_mg_per_l": round(cmax, 5),
            "tmax_h": round(tmax, 3),
            "auc_mg_h_per_l": round(auc, 5),
            "terminal_half_life_h": round(half_life, 3) if half_life else None,
            "accumulation_ratio": round(accumulation, 3) if accumulation else None,
            "time_to_90pc_steady_state_h": (round(time_to_steady_state, 2)
                                            if time_to_steady_state else None),
            "final_concentration_mg_per_l": concs[-1] if concs else 0.0,
            "troughs": troughs[:50],
        },
        "software_versions": {"moleculab": APP_VERSION, "pharmacokinetics": PK_VERSION},
        "method": ("Compartmental pharmacokinetics with first-order absorption, integrated with "
                   "a fixed-step fourth-order Runge-Kutta solver. Doses enter a depot "
                   "compartment and accumulate through the model."),
        "epistemic_status": "computed",
        "interpretation": (
            f"This is the concentration time course that the supplied parameters produce for "
            f"{SPECIES[species]['label'].lower()} given {dose_mg:g} mg"
            + (f" every {interval_h:g} h" if interval_h else " as a single dose")
            + f" over {duration_h:g} h. The curve is a consequence of the parameters you "
              "entered, not a property of any molecule MOLECULAB looked up."
        ),
        "limitations": [
            "PK PARAMETERS ARE INPUTS, NOT PREDICTIONS. MOLECULAB cannot derive clearance, "
            "volume of distribution, absorption rate or bioavailability from a structure. This "
            "curve is only as good as the parameters supplied.",
            "A concentration is not an effect. Nothing here predicts whether a dose is "
            "effective, and nothing here predicts whether it is safe.",
            "No organ, tissue, disease state or individual is modelled. This is a compartmental "
            "abstraction, not physiology.",
            "Population means hide very large between-subject variability. Real individuals "
            "differ in weight, organ function, genetics, age and co-medication.",
            ("Parameters were scaled allometrically from human values. That assumes the same "
             "elimination pathway scales with body size, which fails for species-specific "
             "metabolism." if scaling else
             "Parameters were used as supplied, without interspecies scaling."),
            "Linear (first-order) kinetics are assumed throughout. Saturable absorption, "
            "metabolism or transport would change these curves materially.",
        ],
        "not_simulated": [
            "Any physiological or clinical outcome in an animal or a person.",
            "Toxicity, organ damage, or adverse effects.",
            "Efficacy, symptom change or disease progression.",
            "Drug-drug interactions.",
            "Dissolution or release from a dosage form (MOLECULAB has no validated model and no "
            "measured dissolution data).",
        ],
    }


def compare(scenarios: list[dict]) -> dict:
    """Run two to four regimens and line up their exposure metrics."""
    if not 2 <= len(scenarios) <= 4:
        raise ValueError("Compare between two and four scenarios.")
    runs = []
    for i, s in enumerate(scenarios):
        label = s.pop("label", None) or f"Scenario {i + 1}"
        r = simulate(**s)
        runs.append({"label": label, "result": r})

    keys = [("cmax_mg_per_l", "Cmax (mg/L)"), ("tmax_h", "Tmax (h)"),
            ("auc_mg_h_per_l", "AUC (mg*h/L)"),
            ("terminal_half_life_h", "Half-life (h)"),
            ("accumulation_ratio", "Accumulation ratio"),
            ("final_concentration_mg_per_l", "Final concentration (mg/L)")]
    rows = []
    for key, label in keys:
        values = [r["result"]["metrics"].get(key) for r in runs]
        numeric = [v for v in values if isinstance(v, (int, float))]
        rows.append({
            "metric": label, "key": key, "values": values,
            "fold_difference": (round(max(numeric) / min(numeric), 3)
                                if len(numeric) == len(values) and numeric and min(numeric) > 0
                                else None),
        })

    return {
        "ok": True,
        "scenarios": [{"label": r["label"],
                       "parameters": r["result"]["parameters_used"],
                       "doses": r["result"]["doses"],
                       "metrics": r["result"]["metrics"],
                       "series": r["result"]["series"]} for r in runs],
        "comparison": rows,
        "software_versions": {"moleculab": APP_VERSION, "pharmacokinetics": PK_VERSION},
        "interpretation": (
            "Exposure metrics are compared directly. A fold difference is the ratio of the "
            "largest value to the smallest across the scenarios."
        ),
        "limitations": [
            "Comparing scenarios compares the PARAMETERS that were entered. If the parameters "
            "are wrong or mismatched, the comparison is meaningless.",
            "A higher exposure is not a better outcome. Exposure is not efficacy and not safety.",
            "Comparisons between species rest on allometric scaling where it was used, which is "
            "an approximation and not a measurement.",
        ],
    }


def dilution(*, amount_mg: float, volume_ml: float, molecular_weight: float | None = None,
             added_ml_per_event: float = 0.0, events: int = 0) -> dict:
    """Exact dilution arithmetic: concentration after adding a volume of water.

    This is the honest version of "add water". It is arithmetic, and it does not model whether
    the compound actually dissolves, how fast, or whether it stays in solution.
    """
    if amount_mg <= 0 or volume_ml <= 0:
        raise ValueError("Amount and volume must be greater than zero.")
    steps = []
    vol = float(volume_ml)
    for i in range(max(0, int(events)) + 1):
        if i > 0:
            vol += added_ml_per_event
        conc_mg_ml = amount_mg / vol
        step = {
            "step": i,
            "total_volume_ml": round(vol, 4),
            "concentration_mg_per_ml": round(conc_mg_ml, 6),
            "concentration_mg_per_l": round(conc_mg_ml * 1000.0, 6),
        }
        if molecular_weight and molecular_weight > 0:
            step["concentration_mm"] = round(conc_mg_ml / molecular_weight * 1000.0, 6)
            step["concentration_um"] = round(conc_mg_ml / molecular_weight * 1_000_000.0, 4)
        steps.append(step)

    return {
        "ok": True,
        "steps": steps,
        "amount_mg": amount_mg,
        "method": "Exact arithmetic: concentration = amount / total volume.",
        "epistemic_status": "computed",
        "interpretation": ("Concentration if the entire amount dissolves completely in the "
                           "stated volume."),
        "limitations": [
            "This assumes complete dissolution. It does NOT establish that the compound is "
            "soluble at this concentration, and MOLECULAB has no measured solubility for it.",
            "No dissolution rate is modelled. How fast a solid dissolves depends on the dosage "
            "form, particle size, agitation, pH and temperature, none of which is used here.",
            "Nothing here describes stability, precipitation on standing, or behaviour in a "
            "biological fluid.",
        ],
    }


def surface(*, dose_min_mg: float, dose_max_mg: float, dose_levels: int = 12,
            time_points: int = 60, **kwargs) -> dict:
    """Run the model across a range of doses and return a dose x time x concentration grid.

    This is a real parameter sweep, not an interpolation: every row is a full integration at that
    dose. With linear kinetics the surface scales proportionally with dose, which is itself worth
    seeing - and the moment a non-linear model is added, the same sweep would show the departure.
    """
    if dose_min_mg <= 0 or dose_max_mg <= dose_min_mg:
        raise ValueError("Give a dose range with dose_max_mg greater than dose_min_mg.")
    levels = max(3, min(int(dose_levels), 24))
    kwargs.pop("dose_mg", None)

    doses, rows = [], []
    for i in range(levels):
        dose = dose_min_mg + (dose_max_mg - dose_min_mg) * i / (levels - 1)
        run = simulate(dose_mg=dose, **kwargs)
        series = run["series"]
        # Resample onto a fixed time grid so every row has the same x axis.
        step = max(1, len(series) // max(2, int(time_points)))
        sampled = series[::step][: int(time_points)]
        doses.append(round(dose, 4))
        rows.append({
            "dose_mg": round(dose, 4),
            "times_h": [p["time_h"] for p in sampled],
            "concentrations_mg_per_l": [p["concentration_mg_per_l"] for p in sampled],
            "cmax_mg_per_l": run["metrics"]["cmax_mg_per_l"],
            "auc_mg_h_per_l": run["metrics"]["auc_mg_h_per_l"],
        })

    peak = max((max(r["concentrations_mg_per_l"]) for r in rows), default=0.0)
    return {
        "ok": True,
        "doses_mg": doses,
        "rows": rows,
        "max_concentration_mg_per_l": peak,
        "parameters_used": {k: v for k, v in kwargs.items()},
        "method": ("A full RK4 integration at each dose level. No interpolation between "
                   "levels is used to produce the grid."),
        "epistemic_status": "computed",
        "interpretation": (
            "Each ridge is one dose level integrated over the whole period. Because the model is "
            "linear, concentration scales proportionally with dose; the surface makes that "
            "proportionality visible, and would make any departure from it visible too."
        ),
        "limitations": [
            "Linear kinetics are assumed, so this surface is proportional by construction. It "
            "does not discover dose non-linearity, because none is modelled.",
            "Every limitation of the single-dose simulation applies to every row here.",
            "A higher concentration is not a better or worse outcome. This is exposure, not "
            "effect and not toxicity.",
        ],
    }
