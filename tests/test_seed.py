import json
import shutil
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db import init_db, make_engine
from app.models import EvidenceRecord
from app.textnorm import excerpt_in_source, fold
from scripts.seed import SEED, load_seed


def test_excerpt_matching_ignores_only_whitespace():
    src = "values of 126 mg/100 g FW and more"
    assert excerpt_in_source("values of 126 mg/100 g FW", src)
    assert not excerpt_in_source("values of 127 mg/100 g FW", src)
    assert not excerpt_in_source("", src)


def test_fold_handles_cyrillic_and_uzbek_apostrophes():
    assert fold("Каперсы") == fold("каперсы")
    assert fold("Kiyiko’t") == fold("kiyikoʻt") == "kiyiko't"


def test_seed_is_idempotent(session_factory):
    with session_factory() as s:
        before = s.scalar(select(func.count()).select_from(EvidenceRecord))
        assert load_seed(s)["created"] == 0
        assert s.scalar(select(func.count()).select_from(EvidenceRecord)) == before


def test_bad_excerpt_is_marked_mismatch(tmp_path):
    seed_dir = tmp_path / "seed"
    shutil.copytree(SEED, seed_dir)
    ev = json.loads((seed_dir / "evidence.json").read_text(encoding="utf-8"))
    ev["records"] = ev["records"][:1]
    ev["records"][0]["original_excerpt"] = "This sentence is not in the abstract."
    (seed_dir / "evidence.json").write_text(json.dumps(ev), encoding="utf-8")
    engine = make_engine(f"sqlite:///{tmp_path / 'x.db'}")
    init_db(engine)
    with sessionmaker(bind=engine)() as s:
        assert load_seed(s, seed_dir) == {"verified": 0, "mismatch": 1, "created": 1}
        assert s.scalar(select(EvidenceRecord.verification_status)) == "excerpt_mismatch"


def test_every_seed_relationship_has_a_publication():
    ev = json.loads((Path(SEED) / "evidence.json").read_text(encoding="utf-8"))["records"]
    cache = {c["pmid"] for c in json.loads((Path(SEED) / "pubmed_cache.json").read_text(encoding="utf-8"))}
    assert all(r["pmid"] in cache for r in ev)
