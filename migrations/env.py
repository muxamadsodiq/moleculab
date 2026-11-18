from alembic import context

from app.db import Base, make_engine
from app import models  # noqa: F401  (register tables)

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:
        _run(connectable)
        return
    engine = make_engine(config.get_main_option("sqlalchemy.url") or None)
    with engine.connect() as conn:
        _run(conn)


def _run(conn) -> None:
    context.configure(connection=conn, target_metadata=target_metadata,
                      render_as_batch=conn.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
