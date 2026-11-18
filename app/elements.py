"""Periodic-table reference data and an educational atom bookkeeping model.

Sources and honesty rules
-------------------------
* Atomic weights follow the IUPAC Commission on Isotopic Abundances and Atomic Weights (CIAAW)
  conventional/abridged standard atomic weights. For elements with no standard atomic weight (no
  stable isotope, other than Th, Pa and U) the value given is the **mass number of the most stable
  known isotope** and is flagged with ``atomic_weight_is_mass_number = True``. For the synthetic
  elements (roughly Z >= 99, and in particular Z >= 104) the mass number, electron configuration and
  chemical category are estimated or predicted, not established by measurement; many properties are
  unavailable.
* Electronegativity is the Pauling scale. It is ``None`` where no reliable value is tabulated. The
  ``data_completeness`` map distinguishes ``"unknown"`` (a value may exist but is not recorded here)
  from ``"not_applicable"`` (the quantity is undefined, e.g. Pauling electronegativity of helium) and
  ``"estimated"`` / ``"predicted"``.
* Common oxidation states are a conservative list of textbook-common states, not an exhaustive one.
* Isotope data is deliberately limited to well-known isotopes. Elements not covered return an empty
  list plus a note; nothing is invented.
* f-block layout: La-Lu and Ac-Lr are shown in separate f-block rows and carry ``group = None``
  (the group-3 membership of Lu/Lr versus La/Ac is a convention IUPAC has left open).
"""
from __future__ import annotations

from typing import Any

# (Z, symbol, name, atomic_weight, group, period, electron_configuration, electronegativity,
#  oxidation_states, standard_state)
_RAW: list[tuple] = [
    (1, "H", "Hydrogen", 1.008, 1, 1, "1s1", 2.20, [-1, 1], "gas"),
    (2, "He", "Helium", 4.0026, 18, 1, "1s2", None, [], "gas"),
    (3, "Li", "Lithium", 6.94, 1, 2, "[He] 2s1", 0.98, [1], "solid"),
    (4, "Be", "Beryllium", 9.0122, 2, 2, "[He] 2s2", 1.57, [2], "solid"),
    (5, "B", "Boron", 10.81, 13, 2, "[He] 2s2 2p1", 2.04, [3], "solid"),
    (6, "C", "Carbon", 12.011, 14, 2, "[He] 2s2 2p2", 2.55, [-4, -2, 2, 4], "solid"),
    (7, "N", "Nitrogen", 14.007, 15, 2, "[He] 2s2 2p3", 3.04, [-3, 3, 5], "gas"),
    (8, "O", "Oxygen", 15.999, 16, 2, "[He] 2s2 2p4", 3.44, [-2], "gas"),
    (9, "F", "Fluorine", 18.998, 17, 2, "[He] 2s2 2p5", 3.98, [-1], "gas"),
    (10, "Ne", "Neon", 20.180, 18, 2, "[He] 2s2 2p6", None, [], "gas"),
    (11, "Na", "Sodium", 22.990, 1, 3, "[Ne] 3s1", 0.93, [1], "solid"),
    (12, "Mg", "Magnesium", 24.305, 2, 3, "[Ne] 3s2", 1.31, [2], "solid"),
    (13, "Al", "Aluminium", 26.982, 13, 3, "[Ne] 3s2 3p1", 1.61, [3], "solid"),
    (14, "Si", "Silicon", 28.085, 14, 3, "[Ne] 3s2 3p2", 1.90, [-4, 4], "solid"),
    (15, "P", "Phosphorus", 30.974, 15, 3, "[Ne] 3s2 3p3", 2.19, [-3, 3, 5], "solid"),
    (16, "S", "Sulfur", 32.06, 16, 3, "[Ne] 3s2 3p4", 2.58, [-2, 2, 4, 6], "solid"),
    (17, "Cl", "Chlorine", 35.45, 17, 3, "[Ne] 3s2 3p5", 3.16, [-1, 1, 3, 5, 7], "gas"),
    (18, "Ar", "Argon", 39.95, 18, 3, "[Ne] 3s2 3p6", None, [], "gas"),
    (19, "K", "Potassium", 39.098, 1, 4, "[Ar] 4s1", 0.82, [1], "solid"),
    (20, "Ca", "Calcium", 40.078, 2, 4, "[Ar] 4s2", 1.00, [2], "solid"),
    (21, "Sc", "Scandium", 44.956, 3, 4, "[Ar] 3d1 4s2", 1.36, [3], "solid"),
    (22, "Ti", "Titanium", 47.867, 4, 4, "[Ar] 3d2 4s2", 1.54, [2, 3, 4], "solid"),
    (23, "V", "Vanadium", 50.942, 5, 4, "[Ar] 3d3 4s2", 1.63, [2, 3, 4, 5], "solid"),
    (24, "Cr", "Chromium", 51.996, 6, 4, "[Ar] 3d5 4s1", 1.66, [2, 3, 6], "solid"),
    (25, "Mn", "Manganese", 54.938, 7, 4, "[Ar] 3d5 4s2", 1.55, [2, 3, 4, 6, 7], "solid"),
    (26, "Fe", "Iron", 55.845, 8, 4, "[Ar] 3d6 4s2", 1.83, [2, 3], "solid"),
    (27, "Co", "Cobalt", 58.933, 9, 4, "[Ar] 3d7 4s2", 1.88, [2, 3], "solid"),
    (28, "Ni", "Nickel", 58.693, 10, 4, "[Ar] 3d8 4s2", 1.91, [2], "solid"),
    (29, "Cu", "Copper", 63.546, 11, 4, "[Ar] 3d10 4s1", 1.90, [1, 2], "solid"),
    (30, "Zn", "Zinc", 65.38, 12, 4, "[Ar] 3d10 4s2", 1.65, [2], "solid"),
    (31, "Ga", "Gallium", 69.723, 13, 4, "[Ar] 3d10 4s2 4p1", 1.81, [3], "solid"),
    (32, "Ge", "Germanium", 72.630, 14, 4, "[Ar] 3d10 4s2 4p2", 2.01, [2, 4], "solid"),
    (33, "As", "Arsenic", 74.922, 15, 4, "[Ar] 3d10 4s2 4p3", 2.18, [-3, 3, 5], "solid"),
    (34, "Se", "Selenium", 78.971, 16, 4, "[Ar] 3d10 4s2 4p4", 2.55, [-2, 4, 6], "solid"),
    (35, "Br", "Bromine", 79.904, 17, 4, "[Ar] 3d10 4s2 4p5", 2.96, [-1, 1, 5], "liquid"),
    (36, "Kr", "Krypton", 83.798, 18, 4, "[Ar] 3d10 4s2 4p6", 3.00, [2], "gas"),
    (37, "Rb", "Rubidium", 85.468, 1, 5, "[Kr] 5s1", 0.82, [1], "solid"),
    (38, "Sr", "Strontium", 87.62, 2, 5, "[Kr] 5s2", 0.95, [2], "solid"),
    (39, "Y", "Yttrium", 88.906, 3, 5, "[Kr] 4d1 5s2", 1.22, [3], "solid"),
    (40, "Zr", "Zirconium", 91.224, 4, 5, "[Kr] 4d2 5s2", 1.33, [4], "solid"),
    (41, "Nb", "Niobium", 92.906, 5, 5, "[Kr] 4d4 5s1", 1.6, [3, 5], "solid"),
    (42, "Mo", "Molybdenum", 95.95, 6, 5, "[Kr] 4d5 5s1", 2.16, [4, 6], "solid"),
    (43, "Tc", "Technetium", 98, 7, 5, "[Kr] 4d5 5s2", 1.9, [4, 7], "solid"),
    (44, "Ru", "Ruthenium", 101.07, 8, 5, "[Kr] 4d7 5s1", 2.2, [3, 4], "solid"),
    (45, "Rh", "Rhodium", 102.91, 9, 5, "[Kr] 4d8 5s1", 2.28, [3], "solid"),
    (46, "Pd", "Palladium", 106.42, 10, 5, "[Kr] 4d10", 2.20, [2, 4], "solid"),
    (47, "Ag", "Silver", 107.87, 11, 5, "[Kr] 4d10 5s1", 1.93, [1], "solid"),
    (48, "Cd", "Cadmium", 112.41, 12, 5, "[Kr] 4d10 5s2", 1.69, [2], "solid"),
    (49, "In", "Indium", 114.82, 13, 5, "[Kr] 4d10 5s2 5p1", 1.78, [3], "solid"),
    (50, "Sn", "Tin", 118.71, 14, 5, "[Kr] 4d10 5s2 5p2", 1.96, [2, 4], "solid"),
    (51, "Sb", "Antimony", 121.76, 15, 5, "[Kr] 4d10 5s2 5p3", 2.05, [-3, 3, 5], "solid"),
    (52, "Te", "Tellurium", 127.60, 16, 5, "[Kr] 4d10 5s2 5p4", 2.1, [-2, 4, 6], "solid"),
    (53, "I", "Iodine", 126.90, 17, 5, "[Kr] 4d10 5s2 5p5", 2.66, [-1, 1, 5, 7], "solid"),
    (54, "Xe", "Xenon", 131.29, 18, 5, "[Kr] 4d10 5s2 5p6", 2.6, [2, 4, 6], "gas"),
    (55, "Cs", "Caesium", 132.91, 1, 6, "[Xe] 6s1", 0.79, [1], "solid"),
    (56, "Ba", "Barium", 137.33, 2, 6, "[Xe] 6s2", 0.89, [2], "solid"),
    (57, "La", "Lanthanum", 138.91, None, 6, "[Xe] 5d1 6s2", 1.10, [3], "solid"),
    (58, "Ce", "Cerium", 140.12, None, 6, "[Xe] 4f1 5d1 6s2", 1.12, [3, 4], "solid"),
    (59, "Pr", "Praseodymium", 140.91, None, 6, "[Xe] 4f3 6s2", 1.13, [3], "solid"),
    (60, "Nd", "Neodymium", 144.24, None, 6, "[Xe] 4f4 6s2", 1.14, [3], "solid"),
    (61, "Pm", "Promethium", 145, None, 6, "[Xe] 4f5 6s2", None, [3], "solid"),
    (62, "Sm", "Samarium", 150.36, None, 6, "[Xe] 4f6 6s2", 1.17, [2, 3], "solid"),
    (63, "Eu", "Europium", 151.96, None, 6, "[Xe] 4f7 6s2", None, [2, 3], "solid"),
    (64, "Gd", "Gadolinium", 157.25, None, 6, "[Xe] 4f7 5d1 6s2", 1.20, [3], "solid"),
    (65, "Tb", "Terbium", 158.93, None, 6, "[Xe] 4f9 6s2", None, [3], "solid"),
    (66, "Dy", "Dysprosium", 162.50, None, 6, "[Xe] 4f10 6s2", 1.22, [3], "solid"),
    (67, "Ho", "Holmium", 164.93, None, 6, "[Xe] 4f11 6s2", 1.23, [3], "solid"),
    (68, "Er", "Erbium", 167.26, None, 6, "[Xe] 4f12 6s2", 1.24, [3], "solid"),
    (69, "Tm", "Thulium", 168.93, None, 6, "[Xe] 4f13 6s2", 1.25, [3], "solid"),
    (70, "Yb", "Ytterbium", 173.05, None, 6, "[Xe] 4f14 6s2", None, [2, 3], "solid"),
    (71, "Lu", "Lutetium", 174.97, None, 6, "[Xe] 4f14 5d1 6s2", 1.27, [3], "solid"),
    (72, "Hf", "Hafnium", 178.49, 4, 6, "[Xe] 4f14 5d2 6s2", 1.3, [4], "solid"),
    (73, "Ta", "Tantalum", 180.95, 5, 6, "[Xe] 4f14 5d3 6s2", 1.5, [5], "solid"),
    (74, "W", "Tungsten", 183.84, 6, 6, "[Xe] 4f14 5d4 6s2", 2.36, [6], "solid"),
    (75, "Re", "Rhenium", 186.21, 7, 6, "[Xe] 4f14 5d5 6s2", 1.9, [4, 7], "solid"),
    (76, "Os", "Osmium", 190.23, 8, 6, "[Xe] 4f14 5d6 6s2", 2.2, [4], "solid"),
    (77, "Ir", "Iridium", 192.22, 9, 6, "[Xe] 4f14 5d7 6s2", 2.20, [3, 4], "solid"),
    (78, "Pt", "Platinum", 195.08, 10, 6, "[Xe] 4f14 5d9 6s1", 2.28, [2, 4], "solid"),
    (79, "Au", "Gold", 196.97, 11, 6, "[Xe] 4f14 5d10 6s1", 2.54, [1, 3], "solid"),
    (80, "Hg", "Mercury", 200.59, 12, 6, "[Xe] 4f14 5d10 6s2", 2.00, [1, 2], "liquid"),
    (81, "Tl", "Thallium", 204.38, 13, 6, "[Xe] 4f14 5d10 6s2 6p1", 1.62, [1, 3], "solid"),
    (82, "Pb", "Lead", 207.2, 14, 6, "[Xe] 4f14 5d10 6s2 6p2", 2.33, [2, 4], "solid"),
    (83, "Bi", "Bismuth", 208.98, 15, 6, "[Xe] 4f14 5d10 6s2 6p3", 2.02, [3], "solid"),
    (84, "Po", "Polonium", 209, 16, 6, "[Xe] 4f14 5d10 6s2 6p4", 2.0, [2, 4], "solid"),
    (85, "At", "Astatine", 210, 17, 6, "[Xe] 4f14 5d10 6s2 6p5", 2.2, [], "unknown"),
    (86, "Rn", "Radon", 222, 18, 6, "[Xe] 4f14 5d10 6s2 6p6", 2.2, [], "gas"),
    (87, "Fr", "Francium", 223, 1, 7, "[Rn] 7s1", None, [1], "unknown"),
    (88, "Ra", "Radium", 226, 2, 7, "[Rn] 7s2", 0.9, [2], "solid"),
    (89, "Ac", "Actinium", 227, None, 7, "[Rn] 6d1 7s2", 1.1, [3], "solid"),
    (90, "Th", "Thorium", 232.04, None, 7, "[Rn] 6d2 7s2", 1.3, [4], "solid"),
    (91, "Pa", "Protactinium", 231.04, None, 7, "[Rn] 5f2 6d1 7s2", 1.5, [4, 5], "solid"),
    (92, "U", "Uranium", 238.03, None, 7, "[Rn] 5f3 6d1 7s2", 1.38, [4, 6], "solid"),
    (93, "Np", "Neptunium", 237, None, 7, "[Rn] 5f4 6d1 7s2", 1.36, [5], "solid"),
    (94, "Pu", "Plutonium", 244, None, 7, "[Rn] 5f6 7s2", 1.28, [4], "solid"),
    (95, "Am", "Americium", 243, None, 7, "[Rn] 5f7 7s2", None, [3], "solid"),
    (96, "Cm", "Curium", 247, None, 7, "[Rn] 5f7 6d1 7s2", None, [3], "solid"),
    (97, "Bk", "Berkelium", 247, None, 7, "[Rn] 5f9 7s2", None, [3], "solid"),
    (98, "Cf", "Californium", 251, None, 7, "[Rn] 5f10 7s2", None, [3], "solid"),
    (99, "Es", "Einsteinium", 252, None, 7, "[Rn] 5f11 7s2", None, [3], "unknown"),
    (100, "Fm", "Fermium", 257, None, 7, "[Rn] 5f12 7s2", None, [3], "unknown"),
    (101, "Md", "Mendelevium", 258, None, 7, "[Rn] 5f13 7s2", None, [3], "unknown"),
    (102, "No", "Nobelium", 259, None, 7, "[Rn] 5f14 7s2", None, [2], "unknown"),
    (103, "Lr", "Lawrencium", 266, None, 7, "[Rn] 5f14 7s2 7p1", None, [3], "unknown"),
    (104, "Rf", "Rutherfordium", 267, 4, 7, "[Rn] 5f14 6d2 7s2", None, [], "unknown"),
    (105, "Db", "Dubnium", 268, 5, 7, "[Rn] 5f14 6d3 7s2", None, [], "unknown"),
    (106, "Sg", "Seaborgium", 269, 6, 7, "[Rn] 5f14 6d4 7s2", None, [], "unknown"),
    (107, "Bh", "Bohrium", 270, 7, 7, "[Rn] 5f14 6d5 7s2", None, [], "unknown"),
    (108, "Hs", "Hassium", 269, 8, 7, "[Rn] 5f14 6d6 7s2", None, [], "unknown"),
    (109, "Mt", "Meitnerium", 278, 9, 7, "[Rn] 5f14 6d7 7s2", None, [], "unknown"),
    (110, "Ds", "Darmstadtium", 281, 10, 7, "[Rn] 5f14 6d8 7s2", None, [], "unknown"),
    (111, "Rg", "Roentgenium", 282, 11, 7, "[Rn] 5f14 6d10 7s1", None, [], "unknown"),
    (112, "Cn", "Copernicium", 285, 12, 7, "[Rn] 5f14 6d10 7s2", None, [], "unknown"),
    (113, "Nh", "Nihonium", 286, 13, 7, "[Rn] 5f14 6d10 7s2 7p1", None, [], "unknown"),
    (114, "Fl", "Flerovium", 289, 14, 7, "[Rn] 5f14 6d10 7s2 7p2", None, [], "unknown"),
    (115, "Mc", "Moscovium", 290, 15, 7, "[Rn] 5f14 6d10 7s2 7p3", None, [], "unknown"),
    (116, "Lv", "Livermorium", 293, 16, 7, "[Rn] 5f14 6d10 7s2 7p4", None, [], "unknown"),
    (117, "Ts", "Tennessine", 294, 17, 7, "[Rn] 5f14 6d10 7s2 7p5", None, [], "unknown"),
    (118, "Og", "Oganesson", 294, 18, 7, "[Rn] 5f14 6d10 7s2 7p6", None, [], "unknown"),
]

# Elements whose tabulated "atomic weight" is a mass number (no IUPAC standard atomic weight).
MASS_NUMBER_WEIGHT = frozenset([43, 61] + list(range(84, 90)) + list(range(93, 119)))

_NOBLE_GASES = {2, 10, 18, 36, 54, 86, 118}
_ALKALI = {3, 11, 19, 37, 55, 87}
_ALKALINE_EARTH = {4, 12, 20, 38, 56, 88}
_METALLOIDS = {5, 14, 32, 33, 51, 52}
_POST_TRANSITION = {13, 31, 49, 50, 81, 82, 83, 84, 113, 114, 115, 116}
_HALOGENS = {9, 17, 35, 53, 85, 117}
_NONMETALS = {1, 6, 7, 8, 15, 16, 34}


def _category(z: int) -> str:
    if z in _NOBLE_GASES:
        return "noble gas"
    if z in _ALKALI:
        return "alkali metal"
    if z in _ALKALINE_EARTH:
        return "alkaline earth metal"
    if 57 <= z <= 71:
        return "lanthanide"
    if 89 <= z <= 103:
        return "actinide"
    if z in _METALLOIDS:
        return "metalloid"
    if z in _POST_TRANSITION:
        return "post-transition metal"
    if z in _HALOGENS:
        return "halogen"
    if z in _NONMETALS:
        return "nonmetal"
    return "transition metal"


def _block(z: int, group: int | None) -> str:
    if 57 <= z <= 71 or 89 <= z <= 103:
        return "f"
    if z in (1, 2) or group in (1, 2):
        return "s"
    if group is not None and 3 <= group <= 12:
        return "d"
    return "p"


def _build(row: tuple) -> dict[str, Any]:
    z, sym, name, weight, group, period, config, en, ox, state = row
    mass_number = z in MASS_NUMBER_WEIGHT
    noble_inert = z in (2, 10, 18)
    completeness: dict[str, str] = {
        "atomic_weight": ("estimated" if z >= 104 else
                          "mass_number_of_most_stable_isotope" if mass_number else "known"),
        "electron_configuration": "predicted" if z >= 104 else "known",
        "group": "not_applicable" if group is None else "known",
        "electronegativity_pauling": "known",
        "common_oxidation_states": "known",
        "standard_state": "unknown" if state == "unknown" else "known",
    }
    if en is None:
        completeness["electronegativity_pauling"] = "not_applicable" if noble_inert else "unknown"
    if not ox:
        completeness["common_oxidation_states"] = "not_applicable" if noble_inert else "unknown"
    return {
        "atomic_number": z,
        "symbol": sym,
        "name": name,
        "atomic_weight": weight,
        "atomic_weight_is_mass_number": mass_number,
        "category": _category(z),
        "group": group,
        "period": period,
        "block": _block(z, group),
        "electron_configuration": config,
        "electronegativity_pauling": en,
        "common_oxidation_states": list(ox),
        "standard_state": state,
        "radioactive_only": z in (43, 61) or z >= 84,
        "data_completeness": completeness,
        "notes": ("Synthetic superheavy element: mass number, configuration and category are "
                  "estimated or predicted; most properties are unmeasured." if z >= 104 else None),
    }


_ELEMENTS: list[dict[str, Any]] = [_build(r) for r in _RAW]
_BY_SYMBOL = {e["symbol"].lower(): e for e in _ELEMENTS}
_BY_NAME = {e["name"].lower(): e for e in _ELEMENTS}
_BY_Z = {e["atomic_number"]: e for e in _ELEMENTS}
CATEGORIES = sorted({e["category"] for e in _ELEMENTS})


def _copy(e: dict[str, Any]) -> dict[str, Any]:
    out = dict(e)
    out["common_oxidation_states"] = list(e["common_oxidation_states"])
    out["data_completeness"] = dict(e["data_completeness"])
    return out


def all_elements() -> list[dict[str, Any]]:
    return [_copy(e) for e in _ELEMENTS]


def get_element(symbol_or_z: str | int) -> dict[str, Any] | None:
    """Look up by symbol (case-insensitive), atomic number, or English name."""
    if isinstance(symbol_or_z, bool):
        return None
    if isinstance(symbol_or_z, int):
        e = _BY_Z.get(symbol_or_z)
    else:
        key = str(symbol_or_z).strip().lower()
        if key.isdigit():
            e = _BY_Z.get(int(key))
        else:
            e = _BY_SYMBOL.get(key) or _BY_NAME.get(key)
    return _copy(e) if e else None


def search_elements(query: str) -> list[dict[str, Any]]:
    """Case-insensitive match on symbol, name, atomic number or category."""
    q = (query or "").strip().lower()
    if not q:
        return all_elements()
    return [_copy(e) for e in _ELEMENTS
            if q == e["symbol"].lower() or q in e["name"].lower()
            or q == str(e["atomic_number"]) or q in e["category"].lower()]


# ---------------------------------------------------------------------------- isotopes
def _iso(a, ab=None, half_life=None):
    return {"mass_number": a, "natural_abundance_percent": ab, "stable": half_life is None,
            "half_life": half_life}


# Only well-known isotopes. Abundances are natural percent (None where not a natural/known value).
_ISOTOPES: dict[str, list[dict]] = {
    "H": [_iso(1, 99.9885), _iso(2, 0.0115), _iso(3, None, "12.32 years")],
    "He": [_iso(3), _iso(4)],
    "Li": [_iso(6, 7.59), _iso(7, 92.41)],
    "B": [_iso(10, 19.9), _iso(11, 80.1)],
    "C": [_iso(12, 98.93), _iso(13, 1.07), _iso(14, None, "5730 years")],
    "N": [_iso(14, 99.636), _iso(15, 0.364)],
    "O": [_iso(16, 99.757), _iso(17, 0.038), _iso(18, 0.205)],
    "F": [_iso(19, 100.0)],
    "Ne": [_iso(20, 90.48), _iso(21, 0.27), _iso(22, 9.25)],
    "Na": [_iso(23, 100.0)],
    "Mg": [_iso(24, 78.99), _iso(25, 10.00), _iso(26, 11.01)],
    "Al": [_iso(27, 100.0)],
    "Si": [_iso(28, 92.223), _iso(29, 4.685), _iso(30, 3.092)],
    "P": [_iso(31, 100.0)],
    "S": [_iso(32, 94.99), _iso(33, 0.75), _iso(34, 4.25), _iso(36, 0.01)],
    "Cl": [_iso(35, 75.76), _iso(37, 24.24)],
    "Ar": [_iso(36, 0.3336), _iso(38, 0.0629), _iso(40, 99.6035)],
    "K": [_iso(39, 93.2581), _iso(41, 6.7302), _iso(40, 0.0117, "1.25 billion years")],
    "Fe": [_iso(54, 5.845), _iso(56, 91.754), _iso(57, 2.119), _iso(58, 0.282)],
    "Co": [_iso(59, 100.0), _iso(60, None, "5.27 years")],
    "Cu": [_iso(63, 69.15), _iso(65, 30.85)],
    "Br": [_iso(79, 50.69), _iso(81, 49.31)],
    "I": [_iso(127, 100.0), _iso(131, None, "8.02 days")],
    "Au": [_iso(197, 100.0)],
    "U": [_iso(235, 0.7204, "704 million years"), _iso(238, 99.2742, "4.47 billion years")],
}


def isotope_info(symbol: str) -> dict[str, Any]:
    """Well-known isotopes for an element, or an empty list with a note when not covered."""
    e = get_element(symbol)
    if e is None:
        return {"symbol": symbol, "isotopes": [], "note": "unknown element"}
    isos = _ISOTOPES.get(e["symbol"])
    if not isos:
        return {"symbol": e["symbol"], "isotopes": [],
                "note": "Isotope data not curated for this element; none is shown rather than guessed."}
    out = []
    for i in isos:
        d = dict(i)
        d["label"] = "%s-%d" % (e["name"].lower(), i["mass_number"])
        d["neutrons"] = i["mass_number"] - e["atomic_number"]
        out.append(d)
    return {"symbol": e["symbol"], "isotopes": sorted(out, key=lambda x: x["mass_number"]),
            "note": ("Curated list of well-known isotopes only; absence of an isotope here does not "
                     "mean it does not exist.")}


# ---------------------------------------------------------------------------- atom model
MODEL_NOTE = ("Educational bookkeeping model: identity from proton count, isotope from neutron count, "
              "charge from electron count. It is NOT a quantum-mechanical calculation and says nothing "
              "about orbitals, energies, bonding or nuclear stability.")


def validate_atom(protons: Any, neutrons: Any, electrons: Any) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    for k, v in (("protons", protons), ("neutrons", neutrons), ("electrons", electrons)):
        if isinstance(v, bool) or not isinstance(v, int):
            errors.append("%s must be an integer" % k)
        elif v < 0:
            errors.append("%s cannot be negative (got %d)" % (k, v))
    result: dict[str, Any] = {
        "protons": protons, "neutrons": neutrons, "electrons": electrons,
        "valid": False, "plausible": False, "element": None, "mass_number": None, "isotope": None,
        "isotope_in_curated_list": None, "charge": None, "charge_description": None,
        "explanation": [
            "Element identity is set by the number of protons (atomic number).",
            "Neutron count selects the isotope: mass number = protons + neutrons.",
            "Electron count sets charge: charge = protons - electrons.",
        ],
        "errors": errors, "warnings": warnings, "model_note": MODEL_NOTE,
    }
    if errors:
        return result
    if protons == 0:
        errors.append("protons = 0 is not an element (identity requires at least one proton)")
    elif protons > 118:
        errors.append("protons = %d exceeds 118; no element with that atomic number is known" % protons)
    if errors:
        return result

    el = _BY_Z[protons]
    mass = protons + neutrons
    charge = protons - electrons
    result.update(valid=True, mass_number=mass, charge=charge,
                  element={"symbol": el["symbol"], "name": el["name"], "atomic_number": protons},
                  isotope="%s-%d" % (el["name"].lower(), mass))
    if charge == 0:
        result["charge_description"] = "neutral atom"
    elif charge > 0:
        result["charge_description"] = "cation (%d electron%s removed)" % (charge, "" if charge == 1 else "s")
    else:
        result["charge_description"] = "anion (%d extra electron%s)" % (-charge, "" if charge == -1 else "s")

    known = [i["mass_number"] for i in _ISOTOPES.get(el["symbol"], [])]
    if known:
        result["isotope_in_curated_list"] = mass in known
        if mass not in known:
            warnings.append("Mass number %d is not in the curated list of well-known %s isotopes; this "
                            "does not prove it does not exist." % (mass, el["name"].lower()))
    plausible = True
    if electrons > protons + 10:
        plausible = False
        warnings.append("Implausible: %d electrons on %d protons (charge %d). Anions this highly "
                        "charged do not exist as isolated species." % (electrons, protons, charge))
    elif electrons > protons + 2:
        warnings.append("Highly charged anion (charge %d); such species are generally unstable in "
                        "isolation." % charge)
    if electrons == 0:
        warnings.append("No electrons: a bare nucleus, seen only under extreme conditions.")
    if protons == 1:
        if neutrons > 6:
            plausible = False
            warnings.append("Hydrogen with %d neutrons is far beyond any observed hydrogen isotope." % neutrons)
    else:
        ratio = neutrons / protons
        if ratio < 0.7 or ratio > 2.3:
            plausible = False
            warnings.append("Neutron:proton ratio %.2f is far outside the range of known nuclei; if it "
                            "exists at all it would be extremely short-lived (rough heuristic)." % ratio)
    if protons in (43, 61) or protons >= 84:
        warnings.append("%s has no stable isotope; every isotope is radioactive." % el["name"])
    result["plausible"] = plausible
    return result
