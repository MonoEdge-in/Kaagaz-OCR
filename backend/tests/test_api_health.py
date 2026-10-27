"""GET /api/health with a real database and fake storage and queue."""

import pytest
from sqlalchemy import create_engine

from kaagaz.api.app import create_app
from kaagaz.db.engine import Database, make_engine, normalise_url
from kaagaz.ingestion.errors import BackendUnavailable
from tests.db_support import APP_ROLE, BACKEND, database_url
from tests.settings_factory import make_settings
from tests.test_api_support import Api, ApiFakeQueue, ApiFakeStorage, FakeAnswers, TestClient


@pytest.mark.db
def test_all_ok(db: Database) -> None:
    reply = Api(db).client().get("/api/health")
    assert reply.status_code == 200
    # Changed on purpose (#246, 27 Oct 2026): health also reports the running commit (O1).
    assert reply.json() == {
        "status": "ok",
        "database": "ok",
        "storage": "ok",
        "queue": "ok",
        "commit": "local",
    }
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
    # Changed on purpose (#246, 27 Oct 2026): health also reports the running commit (O1).
    assert reply.json() == {
        "status": "degraded",
        "database": "error",
        "storage": "ok",
        "queue": "ok",
        "commit": "local",
    }


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
    # Changed on purpose (#246, 27 Oct 2026): health also reports the running commit (O1).
    assert reply.json() == {
        "status": "degraded",
        "database": "error",
        "storage": "ok",
        "queue": "ok",
        "commit": "local",
    }


def test_health_reports_the_commit_the_image_was_built_from(monkeypatch: pytest.MonkeyPatch) -> None:
    """#246 O1: the deployer builds with KAAGAZ_TAG=<commit>; compose passes it to the image as
    KAAGAZ_COMMIT. A down database still says which release is running."""
    monkeypatch.setenv("KAAGAZ_COMMIT", "c77586d0123456789abcdef0123456789abcdef0")
    unreachable = _DownDatabase(make_engine("postgresql://unused@localhost:1/unused"), "kaagaz_app")
    app = create_app(
        make_settings(), db=unreachable, storage=ApiFakeStorage(), queue=ApiFakeQueue(), answers=FakeAnswers()
    )
    assert TestClient(app).get("/api/health").json()["commit"] == "c77586d0123456789abcdef0123456789abcdef0"
    monkeypatch.setenv("KAAGAZ_COMMIT", " ")
    assert TestClient(app).get("/api/health").json()["commit"] == "local"


def test_the_image_is_given_the_deployers_commit_last_so_no_layer_above_is_rebuilt() -> None:
    """#246 O1: deploy.sh builds with KAAGAZ_TAG=<commit>; compose hands it to the Dockerfile."""
    root = BACKEND.parent
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "    args:\n      # The commit the image is built from" in compose
    assert "      KAAGAZ_COMMIT: ${KAAGAZ_TAG:-local}\n" in compose
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    tail = dockerfile[dockerfile.index("USER app\n") :]
    assert "ARG KAAGAZ_COMMIT=local\nENV KAAGAZ_COMMIT=$KAAGAZ_COMMIT\n" in tail
    assert "RUN " not in tail and "COPY " not in tail
