"""Apply database migrations. `python -m app.migrate` (also run by scripts.seed and at app startup).

A database created by v0.1 (create_all, no alembic_version table) is stamped at
revision 0001 first, then upgraded.
"""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.config import ROOT
from app.db import engine as default_engine


def upgrade(engine=None) -> str:
    engine = engine or default_engine
    cfg = Config(str(Path(ROOT) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(ROOT) / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "plant" in tables and "alembic_version" not in tables:
            command.stamp(cfg, "0001")
        command.upgrade(cfg, "head")
        return conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()


if __name__ == "__main__":
    print("database at revision", upgrade())
