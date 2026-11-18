"""Migration 0005 tests (MOLECULAB 1.0, module 3).

Pins the safety properties that were verified by hand on a database copy before 0005 was applied to
any real database: it only creates tables, the round-trip preserves every v0.1-v0.5 row, and no new
foreign key can delete a curated scientific record.
"""
import sqlite3

from alembic import command
from alembic.config import Config

from app.config import ROOT
from app.db import make_engine
from app.migrate import upgrade
from app.models import (ActivityMeasurement, Assay, BiologicalTarget, Disease, MolecularStructure,
                        ScreeningResult, ScreeningRun, TargetDiseaseEvidence)

NEW_TABLES = {"disease", "biological_target", "target_disease_evidence", "molecular_structure",
              "assay", "activity_measurement", "screening_run", "screening_result"}
V05_TABLES = {"plant", "plant_alias", "compound", "publication", "evidence_record", "review_record",
              "research_workspace", "workspace_resource", "workspace_hypothesis", "research_note",
              "research_task", "workspace_activity", "research_report"}


def _cfg():
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def _tables(db):
    con = sqlite3.connect(db)
    try:
        return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    finally:
        con.close()


def test_0005_creates_the_eight_discovery_tables(tmp_path):
    db = tmp_path / "m.db"
    engine = make_engine(f"sqlite:///{db}")
    assert upgrade(engine) == "0007"
    tables = _tables(db)
    assert NEW_TABLES <= tables
    assert V05_TABLES <= tables


def test_0005_round_trip_preserves_every_v05_row(tmp_path):
    """0005 -> 0004 -> 0005 must leave the curated corpus and workspaces byte-identical in count."""
    db = tmp_path / "m.db"
    engine = make_engine(f"sqlite:///{db}")
    assert upgrade(engine) == "0007"

    con = sqlite3.connect(db)
    con.execute("insert into plant (accepted_name, created_at, updated_at) values ('Test plant',0,0)")
    con.execute("insert into compound (name, created_at, updated_at) values ('testol',0,0)")
    con.execute("insert into publication (title, authors, created_at, updated_at) "
                "values ('T','[]',0,0)")
    con.execute("insert into research_workspace (owner,title,research_question,status,"
                "created_at,updated_at) values ('u','t','q','planning',0,0)")
    # A discovery row, to prove the downgrade discards only discovery data.
    con.execute("insert into disease (ontology_id,name,created_at,updated_at) "
                "values ('MONDO_TEST','test disease',0,0)")
    con.commit()

    def counts():
        return {t: con.execute(f"select count(*) from {t}").fetchone()[0]
                for t in ("plant", "compound", "publication", "research_workspace")}

    before = counts()
    con.close()

    with engine.begin() as conn:
        cfg = _cfg(); cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0004")
    assert not (NEW_TABLES & _tables(db))        # discovery tables gone
    assert V05_TABLES <= _tables(db)             # everything else still there

    con = sqlite3.connect(db)
    assert counts() == before, "v0.5 rows changed during downgrade"
    assert con.execute("select version_num from alembic_version").fetchone() == ("0004",)
    con.close()

    assert upgrade(engine) == "0007"
    con = sqlite3.connect(db)
    assert counts() == before, "v0.5 rows changed during re-upgrade"
    # Discovery data is not restored by re-upgrading; it is re-importable from the providers.
    assert con.execute("select count(*) from disease").fetchone()[0] == 0
    con.close()


def test_0005_does_not_alter_existing_tables():
    """The migration script must contain no ALTER/DROP against a v0.1-v0.5 table."""
    source = (ROOT / "migrations" / "versions" / "0005_v1_0_discovery_entities.py").read_text(
        encoding="utf-8")
    for table in V05_TABLES:
        assert f"op.drop_table('{table}')" not in source
        assert f"op.add_column('{table}'" not in source
        assert f"op.drop_column('{table}'" not in source
        assert f"op.alter_column('{table}'" not in source


def test_no_discovery_table_can_delete_a_curated_record():
    """The only FK into the curated corpus is molecular_structure.compound_id, with SET NULL."""
    curated = {"plant", "plant_alias", "compound", "publication", "evidence_record", "review_record"}
    models = (Disease, BiologicalTarget, TargetDiseaseEvidence, MolecularStructure, Assay,
              ActivityMeasurement, ScreeningRun, ScreeningResult)
    found = []
    for model in models:
        for fk in model.__table__.foreign_keys:
            if fk.target_fullname.split(".")[0] in curated:
                found.append((model.__tablename__, fk.parent.name, fk.target_fullname, fk.ondelete))
    assert found == [("molecular_structure", "compound_id", "compound.id", "SET NULL")]


def test_discovery_tables_carry_provenance_columns():
    required = {"provider", "provider_record_id", "source_url", "retrieved_at", "source_release",
                "raw_payload_hash", "transformation_version"}
    for model in (Disease, BiologicalTarget, TargetDiseaseEvidence, MolecularStructure, Assay,
                  ActivityMeasurement):
        assert required <= set(model.__table__.columns.keys()), model.__tablename__


def test_target_disease_evidence_keeps_the_score_caveat_in_the_row():
    column = TargetDiseaseEvidence.__table__.columns["score_meaning"]
    assert "Not a probability of drug success" in column.default.arg


def test_structure_identity_is_unique_on_inchikey():
    constraints = {c.name for c in MolecularStructure.__table__.constraints}
    assert "uq_structure_inchikey" in constraints
