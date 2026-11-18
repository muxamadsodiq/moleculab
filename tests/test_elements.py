import pytest

from app import elements as el

BASE = "/api/v1/elements"


def test_all_118_elements_contiguous():
    items = el.all_elements()
    zs = [e["atomic_number"] for e in items]
    assert len(items) == 118
    assert zs == list(range(1, 119))
    assert len({e["symbol"] for e in items}) == 118


@pytest.mark.parametrize("sym,z,w", [("H", 1, 1.008), ("C", 6, 12.011), ("O", 8, 15.999),
                                      ("Fe", 26, 55.845), ("Au", 79, 196.97)])
def test_known_elements(sym, z, w):
    e = el.get_element(sym)
    assert e["atomic_number"] == z and e["symbol"] == sym
    assert e["atomic_weight"] == pytest.approx(w, abs=0.01)
    assert el.get_element(z)["symbol"] == sym
    assert el.get_element(sym.lower())["symbol"] == sym


def test_unknown_marked_distinct_from_not_applicable():
    he = el.get_element("He")
    assert he["electronegativity_pauling"] is None
    assert he["data_completeness"]["electronegativity_pauling"] == "not_applicable"
    assert el.get_element("Pm")["data_completeness"]["electronegativity_pauling"] == "unknown"
    assert el.get_element("Tc")["atomic_weight_is_mass_number"] is True
    assert el.get_element("Fe")["atomic_weight_is_mass_number"] is False
    assert el.get_element("Zz") is None


def test_search():
    assert [e["symbol"] for e in el.search_elements("gold")] == ["Au"]
    assert el.search_elements("26")[0]["symbol"] == "Fe"
    assert len(el.search_elements("noble gas")) == 7
    assert el.search_elements("qqqq") == []


def test_isotopes_do_not_invent():
    c = el.isotope_info("C")
    assert {i["mass_number"] for i in c["isotopes"]} == {12, 13, 14}
    og = el.isotope_info("Og")
    assert og["isotopes"] == [] and og["note"]


def test_validate_atom_carbon14():
    r = el.validate_atom(6, 8, 6)
    assert r["valid"] and r["element"]["symbol"] == "C"
    assert r["mass_number"] == 14 and r["isotope"] == "carbon-14" and r["charge"] == 0
    assert "not a quantum" in r["model_note"].lower()


def test_validate_atom_sodium_cation():
    r = el.validate_atom(11, 12, 10)
    assert r["element"]["symbol"] == "Na" and r["charge"] == 1 and r["mass_number"] == 23


def test_validate_atom_invalid():
    assert not el.validate_atom(-1, 0, 0)["valid"]
    assert not el.validate_atom(6, -2, 6)["valid"]
    assert not el.validate_atom(0, 0, 0)["valid"]
    assert not el.validate_atom(119, 100, 119)["valid"]
    assert not el.validate_atom(6.5, 6, 6)["valid"]
    r = el.validate_atom(6, 6, 30)
    assert r["valid"] and not r["plausible"] and r["warnings"]


def test_api_list_and_filters(client):
    r = client.get(BASE + "/")
    assert r.status_code == 200
    assert r.json()["count"] == 118
    assert client.get(BASE + "/", params={"q": "iron"}).json()["elements"][0]["symbol"] == "Fe"
    noble = client.get(BASE + "/", params={"category": "noble gas"}).json()
    assert noble["count"] == 7


def test_api_single_element(client):
    r = client.get(BASE + "/Fe")
    assert r.status_code == 200
    body = r.json()
    assert body["element"]["atomic_number"] == 26
    assert body["isotope_info"]["isotopes"]
    assert client.get(BASE + "/Zz").status_code == 404


def test_api_atom_validate(client):
    r = client.post(BASE + "/atom/validate", json={"protons": 11, "neutrons": 12, "electrons": 10})
    assert r.status_code == 200
    assert r.json()["charge"] == 1
    assert client.post(BASE + "/atom/validate",
                       json={"protons": 0, "neutrons": 1, "electrons": 1}).status_code == 422
    assert client.post(BASE + "/atom/validate",
                       json={"protons": 200, "neutrons": 1, "electrons": 1}).status_code == 422


def test_api_molecule_validate(client):
    ok = client.post(BASE + "/molecule/validate", json={"smiles": "c1ccccc1"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["valid"] and body["descriptors"]["molecular_formula"] == "C6H6"
    bad = client.post(BASE + "/molecule/validate", json={"smiles": "C(C"})
    assert bad.status_code == 200
    assert bad.json()["valid"] is False and bad.json()["error"]
    assert client.post(BASE + "/molecule/validate", json={"smiles": ""}).status_code == 422
