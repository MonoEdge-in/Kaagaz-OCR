"""#246 A1: the PostgreSQL timeouts each process sets on its connections (``make_engine``).

The API's are short, the worker's long (a cut save hands the job back to be read again), the
CLI and the migrations set none. A statement or lock wait cut short reaches the caller as
BackendUnavailable, as a lost database does, never as a crash.
"""

import logging
from typing import Any
from unittest import mock

import pytest
from sqlalchemy import text

from kaagaz import worker_main
from kaagaz.api import app as api_app
from kaagaz.db import engine as engine_module
from kaagaz.db.engine import make_engine, unavailable
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.settings import Settings, SettingsError
from tests.db_support import database_url
from tests.settings_factory import make_settings
from tests.test_api_support import ApiFakeQueue, ApiFakeStorage, FakeAnswers
from tests.test_settings import shipped

UNREACHABLE = "postgresql://unused@localhost:1/unused"
SHOWN = ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")
SETTINGS = (
    "DB_STATEMENT_TIMEOUT_SECONDS",
    "DB_LOCK_TIMEOUT_SECONDS",
    "DB_WORKER_STATEMENT_TIMEOUT_SECONDS",
    "DB_WORKER_LOCK_TIMEOUT_SECONDS",
    "DB_IDLE_IN_TRANSACTION_TIMEOUT_SECONDS",
)


def _connect_args(**timeouts: int) -> dict[str, Any]:
    """What ``make_engine`` hands the driver for each new connection."""
    with mock.patch.object(engine_module, "create_engine") as create:
        make_engine(UNREACHABLE, **timeouts)
    return dict(create.call_args.kwargs["connect_args"])


def test_no_timeout_unless_asked_so_the_cli_and_tests_keep_no_limit() -> None:
    assert _connect_args() == {}


def test_each_timeout_is_set_in_milliseconds_and_a_zero_one_is_left_out() -> None:
    assert _connect_args(
        statement_timeout_seconds=120, lock_timeout_seconds=0, idle_in_transaction_timeout_seconds=900
    ) == {"options": "-c statement_timeout=120000 -c idle_in_transaction_session_timeout=900000"}


def test_the_settings_default_when_unset_so_an_older_env_still_starts() -> None:
    settings = Settings.from_env(shipped(**dict.fromkeys(SETTINGS)))
    assert (
        settings.db_statement_timeout_seconds,
        settings.db_lock_timeout_seconds,
        settings.db_worker_statement_timeout_seconds,
        settings.db_worker_lock_timeout_seconds,
        settings.db_idle_in_transaction_timeout_seconds,
    ) == (120, 60, 900, 300, 900)
    assert Settings.from_env(shipped()) == settings  # .env.example holds the same values


@pytest.mark.parametrize("name", SETTINGS)
def test_a_negative_timeout_stops_start_up_by_name(name: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: "-1"}))


def test_the_api_uses_its_own_timeouts() -> None:
    settings = make_settings(
        database_url=UNREACHABLE,
        db_statement_timeout_seconds=7,
        db_lock_timeout_seconds=3,
        db_idle_in_transaction_timeout_seconds=11,
    )
    with mock.patch.object(api_app, "make_engine", wraps=make_engine) as made:
        api_app.create_app(settings, storage=ApiFakeStorage(), queue=ApiFakeQueue(), answers=FakeAnswers())
    assert made.call_args.kwargs == {
        "statement_timeout_seconds": 7,
        "lock_timeout_seconds": 3,
        "idle_in_transaction_timeout_seconds": 11,
    }


def test_the_worker_uses_its_own_longer_timeouts() -> None:
    settings = make_settings(
        database_url=UNREACHABLE,
        db_worker_statement_timeout_seconds=900,
        db_worker_lock_timeout_seconds=300,
        db_idle_in_transaction_timeout_seconds=900,
    )
    with (
        mock.patch.object(worker_main, "make_engine") as made,
        mock.patch.object(worker_main, "Database", side_effect=RuntimeError("stop here")),
        pytest.raises(RuntimeError, match="stop here"),
    ):
        worker_main._serve(settings)
    assert made.call_args.kwargs == {
        "statement_timeout_seconds": 900,
        "lock_timeout_seconds": 300,
        "idle_in_transaction_timeout_seconds": 900,
    }


@pytest.mark.db
def test_postgresql_takes_the_values_for_the_whole_session() -> None:
    engine = make_engine(
        database_url(),
        statement_timeout_seconds=120,
        lock_timeout_seconds=60,
        idle_in_transaction_timeout_seconds=900,
    )
    try:
        with engine.connect() as conn:
            shown = [conn.execute(text(f"SHOW {name}")).scalar_one() for name in SHOWN]
            # A statement's own SET LOCAL (search, listing, insights) still wins in its transaction.
            conn.execute(text("SELECT set_config('statement_timeout', '3000', true)"))
            local = conn.execute(text("SHOW statement_timeout")).scalar_one()
            conn.rollback()
            after = conn.execute(text("SHOW statement_timeout")).scalar_one()
    finally:
        engine.dispose()
    assert shown == ["2min", "1min", "15min"]
    assert (local, after) == ("3s", "2min")


@pytest.mark.db
def test_a_statement_cut_short_is_backend_unavailable() -> None:
    engine = make_engine(database_url(), statement_timeout_seconds=1)
    try:
        with pytest.raises(BackendUnavailable), unavailable(), engine.connect() as conn:
            conn.execute(text("SELECT pg_sleep(3)"))
    finally:
        engine.dispose()


@pytest.mark.db
def test_the_error_behind_backend_unavailable_is_logged_by_class_and_sqlstate_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#620: production's 503s logged only BackendUnavailable; now the class and SQLSTATE of the
    database error behind it, never the statement or its values."""
    engine = make_engine(database_url(), statement_timeout_seconds=1)
    caplog.set_level(logging.WARNING, logger="kaagaz.db.sqlstate")
    try:
        with pytest.raises(BackendUnavailable), unavailable(), engine.connect() as conn:
            conn.execute(text("SELECT pg_sleep(3), :secret"), {"secret": "ABCDE1234F"})
    finally:
        engine.dispose()
    (record,) = [r for r in caplog.records if r.name == "kaagaz.db.sqlstate"]
    fields = {key: getattr(record, key) for key in ("error_class", "driver_error", "sqlstate")}
    assert fields == {"error_class": "OperationalError", "driver_error": "QueryCanceled", "sqlstate": "57014"}
    assert "pg_sleep" not in caplog.text and "ABCDE1234F" not in caplog.text
