"""Physiologically-based pharmacokinetics: where a dose goes, organ by organ.

The compartmental model in `app.pharmacokinetics` answers "how much is in the blood?". It cannot
answer "how much reaches the liver?", because it has no liver. This module does, by modelling
each organ as its own perfusion-limited compartment connected by real blood flow.

**What is genuinely computed**

- Organ volumes and blood flows are published physiological values for each species, scaled to
  body weight. They are not invented and not fitted.
- Each tissue is a well-stirred, perfusion-limited compartment:
      V_t * dC_t/dt = Q_t * (C_art - C_t / Kp_t)
  Hepatic elimination is applied as intrinsic clearance on the liver compartment. The venous pool
  mixes the outflow from every tissue by flow, and the whole system is integrated with RK4.
- Mass balance is reported. If the solver were wrong, the drug in the system plus the drug
  eliminated would stop matching the dose absorbed, and the response shows that number so the
  reader can check it.

**What this is NOT**

- Not a toxicity, lethality or safety prediction. There is no LD50 here, no dose-response for
  harm, and no model of what a concentration DOES to a tissue. A concentration in the liver is
  not liver damage.
- Not a prediction from structure. Tissue-to-plasma partition coefficients (Kp) are INPUTS.
  MOLECULAB does not estimate them from a molecule, and a Kp of 1 everywhere - the default - is
  a deliberately neutral placeholder, not a property of any compound.
- Not an individual. These are reference physiological values for an average animal of that
  species.
- Not a replacement for in vivo work. A perfusion-limited model assumes instant equilibration
  within a tissue and no transporter effects, which is wrong for many real drugs.
"""

from __future__ import annotations

from app.config import APP_VERSION

PBPK_VERSION = "1.0.0"

# Reference physiology. Volumes are fractions of body weight (L/kg, approximately 1 L = 1 kg of
# tissue), flows are fractions of cardiac output. Values are conventional literature figures used
# in PBPK modelling.
PHYSIOLOGY = {
    "human": {
        "label": "Human (adult)", "weight_kg": 70.0, "cardiac_output_l_per_h": 312.0,
        "tissues": {
            "liver":   {"v_frac": 0.0257, "q_frac": 0.227, "label": "Liver"},
            "kidney":  {"v_frac": 0.0044, "q_frac": 0.175, "label": "Kidneys"},
            "brain":   {"v_frac": 0.0200, "q_frac": 0.114, "label": "Brain"},
            "heart":   {"v_frac": 0.0047, "q_frac": 0.040, "label": "Heart"},
            "lung":    {"v_frac": 0.0076, "q_frac": 0.025, "label": "Lungs"},
            "muscle":  {"v_frac": 0.4000, "q_frac": 0.170, "label": "Muscle"},
            "fat":     {"v_frac": 0.2140, "q_frac": 0.052, "label": "Fat"},
            "gut":     {"v_frac": 0.0171, "q_frac": 0.146, "label": "Gut"},
            "skin":    {"v_frac": 0.0371, "q_frac": 0.051, "label": "Skin"},
        },
    },
    "rat": {
        "label": "Rat", "weight_kg": 0.25, "cardiac_output_l_per_h": 4.8,
        "tissues": {
            "liver":   {"v_frac": 0.0366, "q_frac": 0.183, "label": "Liver"},
            "kidney":  {"v_frac": 0.0073, "q_frac": 0.141, "label": "Kidneys"},
            "brain":   {"v_frac": 0.0057, "q_frac": 0.020, "label": "Brain"},
            "heart":   {"v_frac": 0.0033, "q_frac": 0.051, "label": "Heart"},
            "lung":    {"v_frac": 0.0050, "q_frac": 0.022, "label": "Lungs"},
            "muscle":  {"v_frac": 0.4040, "q_frac": 0.278, "label": "Muscle"},
            "fat":     {"v_frac": 0.0700, "q_frac": 0.070, "label": "Fat"},
            "gut":     {"v_frac": 0.0270, "q_frac": 0.131, "label": "Gut"},
            "skin":    {"v_frac": 0.1900, "q_frac": 0.058, "label": "Skin"},
        },
    },
    "mouse": {
        "label": "Mouse", "weight_kg": 0.025, "cardiac_output_l_per_h": 0.96,
        "tissues": {
            "liver":   {"v_frac": 0.0550, "q_frac": 0.161, "label": "Liver"},
            "kidney":  {"v_frac": 0.0170, "q_frac": 0.091, "label": "Kidneys"},
            "brain":   {"v_frac": 0.0170, "q_frac": 0.033, "label": "Brain"},
            "heart":   {"v_frac": 0.0050, "q_frac": 0.066, "label": "Heart"},
            "lung":    {"v_frac": 0.0070, "q_frac": 0.009, "label": "Lungs"},
            "muscle":  {"v_frac": 0.3840, "q_frac": 0.159, "label": "Muscle"},
            "fat":     {"v_frac": 0.0700, "q_frac": 0.070, "label": "Fat"},
            "gut":     {"v_frac": 0.0422, "q_frac": 0.141, "label": "Gut"},
            "skin":    {"v_frac": 0.1650, "q_frac": 0.058, "label": "Skin"},
        },
    },
    "rabbit": {
        "label": "Rabbit", "weight_kg": 2.5, "cardiac_output_l_per_h": 32.0,
        "tissues": {
            "liver":   {"v_frac": 0.0300, "q_frac": 0.178, "label": "Liver"},
            "kidney":  {"v_frac": 0.0070, "q_frac": 0.154, "label": "Kidneys"},
            "brain":   {"v_frac": 0.0050, "q_frac": 0.030, "label": "Brain"},
            "heart":   {"v_frac": 0.0030, "q_frac": 0.040, "label": "Heart"},
            "lung":    {"v_frac": 0.0060, "q_frac": 0.022, "label": "Lungs"},
            "muscle":  {"v_frac": 0.5400, "q_frac": 0.200, "label": "Muscle"},
            "fat":     {"v_frac": 0.1200, "q_frac": 0.060, "label": "Fat"},
            "gut":     {"v_frac": 0.0450, "q_frac": 0.130, "label": "Gut"},
            "skin":    {"v_frac": 0.1100, "q_frac": 0.060, "label": "Skin"},
        },
    },
    "dog": {
        "label": "Dog (beagle)", "weight_kg": 10.0, "cardiac_output_l_per_h": 108.0,
        "tissues": {
            "liver":   {"v_frac": 0.0323, "q_frac": 0.297, "label": "Liver"},
            "kidney":  {"v_frac": 0.0055, "q_frac": 0.173, "label": "Kidneys"},
            "brain":   {"v_frac": 0.0080, "q_frac": 0.020, "label": "Brain"},
            "heart":   {"v_frac": 0.0080, "q_frac": 0.045, "label": "Heart"},
            "lung":    {"v_frac": 0.0090, "q_frac": 0.024, "label": "Lungs"},
            "muscle":  {"v_frac": 0.5400, "q_frac": 0.217, "label": "Muscle"},
            "fat":     {"v_frac": 0.1500, "q_frac": 0.049, "label": "Fat"},
            "gut":     {"v_frac": 0.0350, "q_frac": 0.130, "label": "Gut"},
            "skin":    {"v_frac": 0.0900, "q_frac": 0.060, "label": "Skin"},
        },
    },
}

BLOOD_V_FRAC = 0.074          # total blood as a fraction of body weight
ARTERIAL_SHARE = 0.30         # of total blood
MAX_STEPS = 200_000
MAX_POINTS = 1200


def physiology_table() -> dict:
    return {
        "species": [
            {"id": k, "label": v["label"], "weight_kg": v["weight_kg"],
             "cardiac_output_l_per_h": v["cardiac_output_l_per_h"],
             "tissues": [{"id": tk, **tv} for tk, tv in v["tissues"].items()]}
            for k, v in PHYSIOLOGY.items()
        ],
        "blood_volume_fraction": BLOOD_V_FRAC,
        "note": ("Organ volumes and blood flows are published reference physiology for an "
                 "average animal of each species. They are not measurements made by MOLECULAB "
                 "and they are not specific to any individual."),
        "kp_note": ("Tissue-to-plasma partition coefficients (Kp) are INPUTS. MOLECULAB does not "
                    "estimate them from a structure. The default of 1.0 for every tissue is a "
                    "neutral placeholder, not a property of any compound: with Kp = 1 everywhere "
                    "the drug distributes evenly by blood flow, which no real drug does."),
    }


def simulate(*, dose_mg: float, species: str = "human", duration_h: float = 24.0,
             interval_h: float = 0.0, hepatic_cl_l_per_h: float = 6.0,
             renal_cl_l_per_h: float = 0.0, ka_per_h: float = 1.5,
             bioavailability: float = 1.0, kp: dict | None = None,
             weight_kg: float | None = None) -> dict:
    """Integrate a perfusion-limited PBPK model and report concentration in every tissue."""
    if species not in PHYSIOLOGY:
        raise ValueError(f"Unknown species {species!r}. Known: {sorted(PHYSIOLOGY)}")
    if dose_mg <= 0:
        raise ValueError("Dose must be greater than zero.")
    if duration_h <= 0 or duration_h > 720:
        raise ValueError("Duration must be between 0 and 720 hours.")
    if not 0 < bioavailability <= 1:
        raise ValueError("Bioavailability is a fraction between 0 and 1.")

    phys = PHYSIOLOGY[species]
    bw = float(weight_kg or phys["weight_kg"])
    if bw <= 0:
        raise ValueError("Body weight must be greater than zero.")
    scale = bw / phys["weight_kg"]
    co = phys["cardiac_output_l_per_h"] * (scale ** 0.75)

    # Cardiac output must be fully distributed: whatever leaves the arterial pool has to arrive
    # somewhere. The published per-organ fractions do not sum to 1 for every species (they cover
    # the major organs only), so the remainder becomes an explicit "rest of body" compartment -
    # bone, spleen, marrow and the rest. Without it the model creates or destroys drug, which
    # showed up as a mass-balance error of up to 40%.
    tissue_spec = {k: dict(v) for k, v in phys["tissues"].items()}
    q_sum = sum(v["q_frac"] for v in tissue_spec.values())
    v_sum = sum(v["v_frac"] for v in tissue_spec.values())
    if q_sum > 1.0:
        # Rounding in the published values put the total slightly over; scale back to exactly 1.
        for v in tissue_spec.values():
            v["q_frac"] /= q_sum
        q_sum = 1.0
    if q_sum < 1.0 - 1e-9:
        rest_v = max(0.02, (1.0 - BLOOD_V_FRAC) * 0.92 - v_sum)
        tissue_spec["rest_of_body"] = {
            "v_frac": rest_v, "q_frac": 1.0 - q_sum, "label": "Rest of body",
        }

    names = list(tissue_spec)
    kp = {k: float((kp or {}).get(k, 1.0)) for k in names}
    for k, v in kp.items():
        if v <= 0:
            raise ValueError(f"Partition coefficient for {k} must be greater than zero.")

    vt = {k: tissue_spec[k]["v_frac"] * bw for k in names}
    qt = {k: tissue_spec[k]["q_frac"] * co for k in names}
    v_blood = BLOOD_V_FRAC * bw
    v_art = v_blood * ARTERIAL_SHARE
    v_ven = v_blood * (1 - ARTERIAL_SHARE)

    # State: depot, arterial, venous, then one amount per tissue.
    idx = {k: 3 + i for i, k in enumerate(names)}
    n = 3 + len(names)

    def deriv(y, eliminated):
        depot, a_art, a_ven = y[0], y[1], y[2]
        c_art = a_art / v_art
        c_ven = a_ven / v_ven
        d = [0.0] * n
        d[0] = -ka_per_h * depot

        venous_in = 0.0
        elim_rate = 0.0
        for k in names:
            i = idx[k]
            c_t_free = (y[i] / vt[k]) / kp[k]          # concentration leaving the tissue
            flow_in = qt[k] * c_art
            flow_out = qt[k] * c_t_free
            dt_ = flow_in - flow_out
            if k == "liver":
                # Absorption from the gut enters the liver (first pass), and hepatic clearance
                # removes drug from the liver's free concentration.
                dt_ += ka_per_h * depot
                hepatic = hepatic_cl_l_per_h * (scale ** 0.75) * c_t_free
                dt_ -= hepatic
                elim_rate += hepatic
            if k == "kidney" and renal_cl_l_per_h > 0:
                renal = renal_cl_l_per_h * (scale ** 0.75) * c_t_free
                dt_ -= renal
                elim_rate += renal
            d[i] = dt_
            venous_in += flow_out

        d[2] = venous_in - co * c_ven                   # venous pool
        d[1] = co * c_ven - co * c_art                  # lungs pass straight through
        return d, elim_rate

    # The step must resolve the FASTEST process in the system, or RK4 diverges. In a small
    # animal the arterial blood pool turns over hundreds of times an hour (rat: ~865/h), which
    # is far stiffer than any tissue. A fixed 0.01 h step produced inf/nan for rat and mouse.
    rates = [ka_per_h, co / v_art, co / v_ven]
    rates += [qt[k] / (vt[k] * kp[k]) for k in names]
    rates.append(hepatic_cl_l_per_h * (scale ** 0.75) / (vt["liver"] * kp["liver"]))
    if renal_cl_l_per_h > 0:
        rates.append(renal_cl_l_per_h * (scale ** 0.75) / (vt["kidney"] * kp["kidney"]))
    fastest = max(r for r in rates if r > 0)
    # RK4 is stable for h*lambda below about 2.78; take a quarter of that for accuracy.
    step = min(0.01, 0.7 / fastest, duration_h / 500.0)
    n_steps = int(duration_h / step) + 1
    if n_steps > MAX_STEPS:
        n_steps = MAX_STEPS
    step = duration_h / n_steps
    stability = step * fastest
    record_every = max(1, n_steps // MAX_POINTS)

    dose_times = ([0.0] if interval_h <= 0
                  else [i * interval_h for i in range(int(duration_h // interval_h) + 1)
                        if i * interval_h < duration_h])

    y = [0.0] * n
    eliminated = 0.0
    next_dose = 0
    series = []
    peaks = {k: 0.0 for k in names}
    peaks["plasma"] = 0.0

    for s in range(n_steps + 1):
        t = s * step
        while next_dose < len(dose_times) and dose_times[next_dose] <= t + 1e-9:
            y[0] += dose_mg * bioavailability
            next_dose += 1

        if s % record_every == 0 or s == n_steps:
            point = {"time_h": round(t, 4),
                     "plasma_mg_per_l": round(y[2] / v_ven, 6)}
            for k in names:
                c = y[idx[k]] / vt[k]
                point[k] = round(c, 6)
                peaks[k] = max(peaks[k], c)
            peaks["plasma"] = max(peaks["plasma"], y[2] / v_ven)
            series.append(point)

        if s == n_steps:
            break

        # RK4
        k1, e1 = deriv(y, eliminated)
        y2 = [y[i] + 0.5 * step * k1[i] for i in range(n)]
        k2, e2 = deriv(y2, eliminated)
        y3 = [y[i] + 0.5 * step * k2[i] for i in range(n)]
        k3, e3 = deriv(y3, eliminated)
        y4 = [y[i] + step * k3[i] for i in range(n)]
        k4, e4 = deriv(y4, eliminated)
        y = [max(0.0, y[i] + (step / 6.0) * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i]))
             for i in range(n)]
        eliminated += (step / 6.0) * (e1 + 2 * e2 + 2 * e3 + e4)

    in_system = sum(y)
    administered = dose_mg * bioavailability * len(dose_times)
    balance_error = (abs(administered - in_system - eliminated) / administered * 100.0
                     if administered > 0 else 0.0)

    tissues = []
    for k in names:
        tissues.append({
            "id": k, "label": tissue_spec[k]["label"],
            "volume_l": round(vt[k], 5), "blood_flow_l_per_h": round(qt[k], 4),
            "kp": kp[k],
            "peak_concentration_mg_per_l": round(peaks[k], 5),
            "final_concentration_mg_per_l": round(y[idx[k]] / vt[k], 5),
            "final_amount_mg": round(y[idx[k]], 5),
            "percent_of_dose_at_end": (round(y[idx[k]] / administered * 100, 3)
                                       if administered else 0.0),
        })
    tissues.sort(key=lambda d: -d["peak_concentration_mg_per_l"])

    return {
        "ok": True,
        "series": series,
        "tissues": tissues,
        "species": species,
        "species_label": phys["label"],
        "body_weight_kg": bw,
        "cardiac_output_l_per_h": round(co, 3),
        "doses": {"count": len(dose_times), "dose_mg": dose_mg, "interval_h": interval_h,
                  "times_h": dose_times[:200]},
        "peak_plasma_mg_per_l": round(peaks["plasma"], 5),
        "mass_balance": {
            "administered_mg": round(administered, 5),
            "in_body_at_end_mg": round(in_system, 5),
            "eliminated_mg": round(eliminated, 5),
            "error_percent": round(balance_error, 4),
            "note": ("Administered should equal what remains in the body plus what has been "
                     "eliminated. A large error here would mean the integration is wrong, so "
                     "the number is published rather than hidden."),
        },
        "integration": {
            "method": "RK4", "step_h": round(step, 7), "steps": n_steps,
            "fastest_rate_per_h": round(fastest, 2),
            "stability_product": round(stability, 4),
            "stability_note": ("step x fastest rate must stay below about 2.78 for RK4 to be "
                               "stable; the step is chosen from the system's own fastest rate, "
                               "which in small species is the arterial blood pool, not a tissue."),
        },
        "method": ("Perfusion-limited physiologically-based pharmacokinetics. Each tissue is a "
                   "well-stirred compartment fed by its own blood flow; hepatic and renal "
                   "clearance remove drug from the liver and kidney compartments; the venous "
                   "pool mixes tissue outflow by flow. Integrated with RK4."),
        "software_versions": {"moleculab": APP_VERSION, "pbpk": PBPK_VERSION},
        "epistemic_status": "computed",
        "interpretation": (
            f"Concentration in each tissue of {phys['label'].lower()} over time, given the "
            "physiology of that species and the partition coefficients supplied. Tissues with "
            "high blood flow fill first; where Kp is above 1 the drug concentrates and leaves "
            "more slowly."
        ),
        "limitations": [
            "PARTITION COEFFICIENTS ARE INPUTS. MOLECULAB does not estimate Kp from a structure. "
            "With the default Kp of 1 everywhere, this shows distribution by blood flow alone, "
            "which no real drug follows exactly.",
            "THIS DOES NOT PREDICT TOXICITY, HARM OR DEATH. A concentration in a tissue is not "
            "damage to that tissue. There is no LD50 here, no dose-response for harm, and no "
            "model of what any concentration does to an organ.",
            "Perfusion-limited means instant equilibration within a tissue. Drugs limited by "
            "membrane permeability or moved by transporters do not behave this way.",
            "Reference physiology describes an average animal. Real individuals differ "
            "substantially, and disease changes organ blood flow and volume.",
            "Protein binding, enterohepatic recycling, metabolite formation and active transport "
            "are not modelled.",
            "Clearance is applied as a simple first-order process. Saturable metabolism would "
            "change these curves materially.",
        ],
        "not_simulated": [
            "Whether the animal or person is harmed, becomes ill, or dies.",
            "Any symptom, sensation or clinical sign.",
            "Organ damage, histology or biomarker change.",
            "Efficacy against any disease.",
        ],
    }
