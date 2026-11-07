"""GET /api/health with a real database and fake storage and queue. Changed on purpose (A5,
stability pass, 7 Nov 2026): anyone may ask, so it says ``{status}`` only; which part is down and
the running commit are on the operator's Health page (GET /api/operator/health)."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import OperationalError

from kaagaz.adapters.email import MemoryMailer
from kaagaz.api import app as app_module
from kaagaz.api.app import create_app
from kaagaz.api.routers.health import running_commit
from kaagaz.db.engine import Database, make_engine, normalise_url
from kaagaz.ingestion.errors import BackendUnavailable
from tests.db_support import APP_ROLE, BACKEND, database_url
from tests.settings_factory import make_settings
from tests.test_api_support import Api, ApiFakeQueue, ApiFakeStorage, FakeAnswers, TestClient


@pytest.mark.db
def test_all_ok(db: Database) -> None:
    reply = Api(db).client().get("/api/health")
    assert reply.status_code == 200
    # Changed on purpose (A5, 7 Nov 2026): the status only; the parts and the commit are the operator's.
    assert reply.json() == {"status": "ok"}
    assert reply.headers["Cache-Control"] == "no-store"


@pytest.mark.db
@pytest.mark.parametrize("down", ["storage", "queue"])
def test_one_backend_down_is_503_degraded(db: Database, down: str) -> None:
    api = Api(db)
    getattr(api, down).down = True
    reply = api.client().get("/api/health")
    assert reply.status_code == 503
    assert reply.json() == {"status": "degraded"}


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
    # Changed on purpose (A5, 7 Nov 2026): the status only; the parts and the commit are the operator's.
    assert reply.json() == {"status": "degraded"}


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
    # Changed on purpose (A5, 7 Nov 2026): the status only; the parts and the commit are the operator's.
    assert reply.json() == {"status": "degraded"}


def test_the_running_commit_is_the_image_s_and_only_the_operator_sees_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#246 O1: the deployer builds with KAAGAZ_TAG=<commit>; compose passes it to the image as
    KAAGAZ_COMMIT. Changed on purpose (A5): GET /api/health no longer says it (the operator's
    Health page's Release card does: tests/test_operator_activity.py)."""
    monkeypatch.setenv("KAAGAZ_COMMIT", "c77586d0123456789abcdef0123456789abcdef0")
    assert running_commit() == "c77586d0123456789abcdef0123456789abcdef0"
    unreachable = _DownDatabase(make_engine("postgresql://unused@localhost:1/unused"), "kaagaz_app")
    app = create_app(
        make_settings(), db=unreachable, storage=ApiFakeStorage(), queue=ApiFakeQueue(), answers=FakeAnswers()
    )
    assert TestClient(app).get("/api/health").json() == {"status": "degraded"}
    monkeypatch.setenv("KAAGAZ_COMMIT", " ")
    assert running_commit() == "local"


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


# --- settings unsafe on a server (A6, stability pass, 7 Nov 2026) -------------------------------


@pytest.mark.db
def test_unsafe_settings_are_found_by_their_codes(db: Database) -> None:
    """https with cookies not secure, the test scanner, and a superuser login (the tests' own
    login is the database owner's, a superuser in the Postgres image, as on a laptop)."""
    with db.engine.connect() as conn:
        superuser = bool(
            conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = session_user")).scalar()
        )
    unsafe = make_settings(app_url="https://kaagaz.example.com", cookie_secure=False, scanner="eicar_dev")
    expected = ["cookie_not_secure", "eicar_scanner"] + (["superuser_login"] if superuser else [])
    assert app_module.unsafe_config(unsafe, db) == expected
    safe = make_settings(app_url="https://kaagaz.example.com", cookie_secure=True, scanner="clamav")
    assert app_module.unsafe_config(safe, db) == (["superuser_login"] if superuser else [])
    laptop = make_settings(app_url="http://localhost:3000", cookie_secure=False, scanner="clamav")
    assert "cookie_not_secure" not in app_module.unsafe_config(laptop, db)


def test_each_unsafe_setting_is_logged_and_one_ops_alert_says_them_all(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = make_settings(ops_alert_email="ops@example.com", email_from="no-reply@example.com")
    mailer = MemoryMailer()
    with caplog.at_level(logging.ERROR):
        sent = app_module.report_unsafe_config(settings, ["eicar_scanner", "superuser_login"], mailer=mailer)
        assert sent is not None
        sent.join(10)
    said = [
        record.getMessage() for record in caplog.records if getattr(record, "code", None) == "unsafe_config"
    ]
    assert said == [app_module.UNSAFE_SAID["eicar_scanner"], app_module.UNSAFE_SAID["superuser_login"]]
    [(to, subject, body)] = [(s.to, s.subject, s.text) for s in mailer.sent]
    assert (to, subject) == ("ops@example.com", "Kaagaz server: unsafe settings")
    assert (
        app_module.UNSAFE_SAID["eicar_scanner"] in body and app_module.UNSAFE_SAID["superuser_login"] in body
    )
    assert app_module.report_unsafe_config(settings, [], mailer=mailer) is None and len(mailer.sent) == 1


class _NoLoginDatabase(Database):
    """Its transactions fail as a database refusing the read would."""

    @contextmanager
    def system(self) -> Iterator[Connection]:
        raise OperationalError("SELECT", {}, Exception("refused"))
        yield cast(Connection, None)


def test_a_superuser_check_that_could_not_run_is_said_never_read_as_safe(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A6 review: no answer is not "not a superuser": the operator sees the check did not run."""
    broken = _NoLoginDatabase(make_engine("postgresql://unused@localhost:1/unused"), "kaagaz_app")
    with caplog.at_level(logging.ERROR):
        assert app_module.unsafe_config(make_settings(scanner="clamav"), broken) == ["superuser_unchecked"]
    assert "superuser_unchecked" in app_module.UNSAFE_SAID


def test_an_unsafe_settings_alert_that_was_not_sent_says_so(caplog: pytest.LogCaptureFixture) -> None:
    """A6 review: with OPS_ALERT_EMAIL unset (or SES refusing), the log says the alert never went."""
    settings = make_settings(ops_alert_email=None, email_from=None)
    with caplog.at_level(logging.ERROR):
        sending = app_module.report_unsafe_config(settings, ["eicar_scanner"], mailer=MemoryMailer())
        assert sending is not None
        sending.join(10)
    said = [r.getMessage() for r in caplog.records if getattr(r, "code", None) == "unsafe_config"]
    assert any("OPS_ALERT_EMAIL" in line for line in said)
