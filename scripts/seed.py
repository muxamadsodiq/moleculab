"""Load the curated seed corpus into the database (offline, idempotent).

    python -m scripts.seed            # create tables and load
    python -m scripts.seed --reset    # drop everything first

Each evidence excerpt is checked against the cached PubMed title/abstract.
Matches are stored as `excerpt_verified`; anything else as `excerpt_mismatch`,
so a bad excerpt is visible instead of silently trusted.
"""
import argparse
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine
from app.migrate import upgrade
from app.models import (Compound, EvidenceRecord, InterpretationStatus, Plant, PlantAlias, Publication,
                        VerificationStatus)
from app.textnorm import excerpt_in_source

SEED = Path(__file__).resolve().parent.parent / "data" / "seed"


def load_seed(session: Session, seed_dir: Path = SEED) -> dict:
    def _load(name: str):
        return json.loads((seed_dir / name).read_text(encoding="utf-8"))

    plants = _load("plants.json")["plants"]
    ev = _load("evidence.json")
    cache = {c["pmid"]: c for c in _load("pubmed_cache.json")}
    enriched = _load("enriched.json") if (seed_dir / "enriched.json").exists() else {
        "plants": {}, "compounds": {}, "publications": {}}

    plant_by_name: dict[str, Plant] = {}
    for p in plants:
        g = enriched["plants"].get(p["accepted_name"], {})
        plant = session.scalar(select(Plant).where(Plant.accepted_name == p["accepted_name"]))
        if plant is None:
            plant = Plant(accepted_name=p["accepted_name"])
            session.add(plant)
        sci = g.get("scientific_name") or ""
        plant.authority = sci[len(p["accepted_name"]):].strip() or None if sci.startswith(p["accepted_name"]) else None
        plant.family = g.get("family")
        plant.gbif_key = g.get("gbif_key")
        plant.taxonomy_source = (g.get("provenance") or {}).get("request_url")
        plant.notes = p.get("notes")
        session.flush()
        existing = {(a.name, a.language) for a in plant.aliases}
        for a in p["aliases"]:
            if (a["name"], a["language"]) not in existing:
                plant.aliases.append(PlantAlias(
                    name=a["name"], language=a["language"], script=a.get("script", "latn"),
                    alias_type=a["alias_type"], source=a.get("source", "curator-supplied; citation needed"),
                    verification_status=VerificationStatus.UNVERIFIED))
        plant_by_name[p["accepted_name"]] = plant

    compound_by_name: dict[str, Compound] = {}
    for name in sorted({r["compound"] for r in ev["records"] if r["compound"]}):
        e = enriched["compounds"].get(name, {})
        comp = session.scalar(select(Compound).where(Compound.name == name)) or Compound(name=name)
        session.add(comp)
        if e.get("ok"):
            comp.pubchem_cid = e.get("pubchem_cid")
            comp.inchikey = e.get("inchikey")
            comp.molecular_formula = e.get("molecular_formula")
            comp.molecular_weight = e.get("molecular_weight")
            comp.smiles = e.get("smiles")
            comp.iupac_name = e.get("iupac_name")
            comp.synonyms = e.get("synonyms") or []
            comp.identifier_source = e["provenance"]["request_url"]
            comp.identifier_retrieved_at = e["provenance"]["retrieved_at"]
        compound_by_name[name] = comp
    session.flush()

    pub_by_pmid: dict[str, Publication] = {}
    for pmid in sorted({r["pmid"] for r in ev["records"]}):
        c = cache[pmid]
        pub = session.scalar(select(Publication).where(Publication.pmid == pmid)) or Publication(pmid=pmid)
        session.add(pub)
        pub.title, pub.authors, pub.year = c["title"], c["authors"], c["year"]
        pub.journal, pub.doi, pub.url = c["journal"], (c.get("doi") or "").lower() or None, c["url"]
        pub.language, pub.publication_types, pub.abstract = c.get("language"), c.get("publication_types"), c["abstract"]
        prov = c.get("_provenance", {})
        cr = enriched["publications"].get(pmid, {})
        pub.metadata_source = prov.get("request_url", "pubmed") + (" | crossref DOI verified" if cr.get("crossref_ok") else "")
        pub.metadata_retrieved_at = prov.get("retrieved_at")
        pub_by_pmid[pmid] = pub
    session.flush()

    stats = {"verified": 0, "mismatch": 0, "created": 0}
    for r in ev["records"]:
        pub = pub_by_pmid[r["pmid"]]
        plant = plant_by_name[r["plant"]]
        comp = compound_by_name.get(r["compound"]) if r["compound"] else None
        dup = session.scalar(select(EvidenceRecord).where(
            EvidenceRecord.publication_id == pub.id, EvidenceRecord.plant_id == plant.id,
            EvidenceRecord.compound_id == (comp.id if comp else None),
            EvidenceRecord.evidence_type == r["evidence_type"],
            EvidenceRecord.observation == r["observation"]))
        if dup:
            continue
        ok = excerpt_in_source(r["original_excerpt"], pub.title, pub.abstract)
        stats["verified" if ok else "mismatch"] += 1
        rec = EvidenceRecord(
            plant=plant, compound=comp, publication=pub, plant_part=r.get("plant_part"),
            geographic_origin=r.get("geographic_origin"), observation=r["observation"],
            identification_method=r.get("identification_method"), evidence_type=r["evidence_type"],
            source_location=r["source_location"], original_excerpt=r["original_excerpt"],
            verification_status=VerificationStatus.EXCERPT_VERIFIED if ok else VerificationStatus.EXCERPT_MISMATCH,
            # Curation is recorded as interpretation, never as a review: reviews come only from
            # authenticated reviewers through the API.
            interpretation_status=InterpretationStatus.AI_INTERPRETED,
            interpreted_by=ev.get("curator", "unknown")[:120],
            curator_notes=r.get("curator_notes"))
        session.add(rec)
        stats["created"] += 1
    session.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    if args.reset:
        from sqlalchemy import text
        from app import models  # noqa: F401
        Base.metadata.drop_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    print("schema revision", upgrade())
    with SessionLocal() as s:
        print(load_seed(s))


if __name__ == "__main__":
    main()
