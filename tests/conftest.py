"""Fixtures: BD SQLite temporaria y cliente autenticado."""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="pashtapp-test-"))
os.environ.update(
    DATABASE_URL=f"sqlite:///{_TMP / 'test.db'}",
    BACKUP_DIR=str(_TMP / "backups"),
    SECRET_KEY="test-secret-key",
    ADMIN_USERNAME="admin",
    ADMIN_PASSWORD="secreto",
    APP_ENV="test",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app) as c:
        r = c.post("/login", data={"username": "admin", "password": "secreto"}, follow_redirects=False)
        assert r.status_code == 303
        yield c


@pytest.fixture
def anon():
    with TestClient(app) as c:
        yield c
