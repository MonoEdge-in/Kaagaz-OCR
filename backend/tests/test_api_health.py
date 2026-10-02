"""GET /api/health with a real database and fake storage and queue."""

import pytest
from sqlalchemy import create_engine

from kaagaz.api.app import create_app
from kaagaz.db.engine import Database, make_engine, normalise_url
from kaagaz.ingestion.errors import BackendUnavailable
from tests.db_support import APP_ROLE, database_url
from tests.settings_factory import make_settings
from tests.test_api_support import Api, ApiFakeQueue, ApiFakeStorage, FakeAnswers, TestClient


@pytest.mark.db
def test_all_ok(db: Database) -> None:
    reply = Api(db).client().get("/api/health")
    assert reply.status_code == 200
    assert reply.json() == {"status": "ok", "database": "ok", "storage": "ok", "queue": "ok"}
    assert reply.headers["Cache-Control"] == "no-store"


@pytest.mark.db
@pytest.mark.parametrize("down", ["storage", "queue"])
def test_one_backend_down_is_503_degraded(db: Database, down: str) -> None:
    api = Api(db)
    getattr(api, down).down = True
    reply = api.client().get("/api/health")
    assert reply.status_code == 503
    body = reply.json()
    assert body["status"] == "degraded"
    assert body[down] == "error"
    assert body["database"] == "ok"


class _DownDatabase(Database):
    """Answers every ping with BackendUnavailable, as Database.ping does when the
    server cannot be reached (a real unreachable host takes minutes to time out
    on Windows)."""

    def ping(self) -> None:
        raise BackendUnavailable("database")


def test_database_unreachable_is_503() -> None:
    unreachable = _DownDatabase(make_engine("postgresql://unused@localhost:1/unused"), "kaagaz_app")
    app = create_app(
        make_settings(), db=unreachable, storage=ApiFakeStorage(), queue=ApiFakeQueue(), answers=FakeAnswers()
    )
    reply = TestClient(app).get("/api/health")
    assert reply.status_code == 503
    assert reply.json() == {"status": "degraded", "database": "error", "storage": "ok", "queue": "ok"}


@pytest.mark.db
def test_a_database_with_no_free_connection_shows_as_an_error(db: Database) -> None:
    """A7a: a pool wait that runs out is the database "error", as when it is down."""
    engine = create_engine(normalise_url(database_url()), pool_size=1, max_overflow=0, pool_timeout=0.1)
    busy = Database(engine, APP_ROLE)
    app = create_app(
        make_settings(), db=busy, storage=ApiFakeStorage(), queue=ApiFakeQueue(), answers=FakeAnswers()
    )
    try:
        with engine.connect():  # the only connection is in use
            reply = TestClient(app).get("/api/health")
    finally:
        engine.dispose()
    assert reply.status_code == 503
    assert reply.json() == {"status": "degraded", "database": "error", "storage": "ok", "queue": "ok"}
