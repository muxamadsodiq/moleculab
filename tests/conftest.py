import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import get_session, init_db, make_engine
from app.main import app
from scripts.seed import load_seed


@pytest.fixture()
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as s:
        load_seed(s)
    yield factory
    engine.dispose()


@pytest.fixture()
def client(session_factory):
    def _session():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def session(session_factory):
    with session_factory() as s:
        yield s


def plant_id(client, name):
    return next(p["id"] for p in client.get("/api/plants").json() if p["accepted_name"] == name)


def compound_id(client, name):
    return next(c["id"] for c in client.get("/api/compounds").json() if c["name"] == name)
