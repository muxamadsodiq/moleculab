"""PubChem PUG REST client.

A PubChem hit identifies a chemical structure. It says nothing about which
organism the compound occurs in; plant-compound links must come from
publications, never from this client.
"""
from urllib.parse import quote

from app.clients.base import SourceClient, SourceResult

PROPERTIES = "MolecularFormula,MolecularWeight,InChIKey,SMILES,ConnectivitySMILES,IUPACName"


def _normalise(props: dict) -> dict:
    return {
        "pubchem_cid": props.get("CID"),
        "molecular_formula": props.get("MolecularFormula"),
        "molecular_weight": float(props["MolecularWeight"]) if props.get("MolecularWeight") else None,
        "inchikey": props.get("InChIKey"),
        # PubChem renamed CanonicalSMILES/IsomericSMILES to ConnectivitySMILES/SMILES in 2025.
        "smiles": props.get("SMILES") or props.get("IsomericSMILES")
        or props.get("ConnectivitySMILES") or props.get("CanonicalSMILES"),
        "iupac_name": props.get("IUPACName"),
    }


class PubChemClient(SourceClient):
    name = "pubchem"
    base_url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

    def search_name(self, name: str) -> SourceResult:
        """Return candidate compounds (all CIDs PubChem maps to this name)."""
        res = self._get(f"/compound/name/{quote(name, safe='')}/property/{PROPERTIES}/JSON")
        if res.ok:
            try:
                res.data = [_normalise(p) for p in res.data["PropertyTable"]["Properties"]]
            except (KeyError, TypeError):
                res.ok, res.data, res.error = False, None, "unexpected PubChem response shape"
        return res

    def get_by_cid(self, cid: int) -> SourceResult:
        res = self._get(f"/compound/cid/{int(cid)}/property/{PROPERTIES}/JSON")
        if res.ok:
            try:
                res.data = _normalise(res.data["PropertyTable"]["Properties"][0])
            except (KeyError, IndexError, TypeError):
                res.ok, res.data, res.error = False, None, "unexpected PubChem response shape"
        return res

    def synonyms(self, cid: int, limit: int = 15) -> SourceResult:
        res = self._get(f"/compound/cid/{int(cid)}/synonyms/JSON")
        if res.ok:
            try:
                res.data = res.data["InformationList"]["Information"][0].get("Synonym", [])[:limit]
            except (KeyError, IndexError, TypeError):
                res.ok, res.data, res.error = False, None, "unexpected PubChem response shape"
        return res
