"""Migration 0002 on a fresh database: the backfill, the downgrade, and upgrading again.

The fresh database is DATABASE_URL's database name plus ``_fresh_`` and this run's
process id, dropped and created again for each test (for example ``kaagaz_test_fresh_4242``
for ``kaagaz_test``), so two runs on one server never drop each other's (QA W5). Rows are
written and read through the app role with the customer set, like every other test; only the
migrations run as the owner.
"""

import os
import re
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from kaagaz.db.engine import Database, make_engine
from tests.db_support import BACKEND, database_url, make_database, migrate, new_customer

pytestmark = pytest.mark.db

NEW_TABLES = ("folders", "tags", "document_tags")
NEW_USER_COLUMNS = (
    "failed_logins",
    "locked_until",
    "session_version",
    "must_change_password",
    "totp_secret_enc",
    "totp_enabled_at",
    "totp_last_step",
    "last_login_at",
)
NEW_DOCUMENT_COLUMNS = ("folder_id", "deleted_at", "deleted_by", "family_id", "version_no", "is_current")
NEW_INDEXES = (
    "folders_sibling_name",
    "tags_name",
    "document_tags_tag",
    "documents_current",
    "documents_family_version",
    "documents_folder",
    "documents_trash",
    "corrections_document",
)
NEW_CONSTRAINTS = ("documents_folder_fk", "documents_deleted_by_fk")
TRASH_FUNCTION = "kaagaz_customer_ids_with_trash(timestamptz)"


def _downgrade(url: str, revision: str) -> None:
    """Downgrade the database at ``url`` to ``revision`` (db_support has only upgrade)."""
    # Changed on purpose (#457 with #460, 30 Oct 2026): an optional letter too ("0061b").
    if not re.fullmatch(r"\d{4}[a-z]?", revision):
        raise ValueError("revision must be a 4-digit revision id, perhaps with a letter")
    subprocess.run(  # noqa: S603 - fixed command; the revision is checked above
        [sys.executable, "-m", "alembic", "downgrade", revision],
        cwd=BACKEND,
        # Changed on purpose (#246, 27 Oct 2026): env.py refuses a downgrade without it (G5).
        env={**os.environ, "DATABASE_URL": url, "KAAGAZ_ALLOW_DOWNGRADE": "1"},
        check=True,
        capture_output=True,
    )


def _recreate(maintenance_url: str, name: str, *, create: bool) -> None:
    # CREATE and DROP DATABASE take no parameters, so the name is checked first.
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
        raise ValueError("the fresh database name must be a plain identifier")
    engine = make_engine(maintenance_url)
    try:
        with engine.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
            if create:
                conn.execute(text(f"CREATE DATABASE {name}"))
    finally:
        engine.dispose()


@pytest.fixture
def fresh() -> Iterator[tuple[str, Database]]:
    """An empty database next to DATABASE_URL's, and a Database on it."""
    main = make_url(database_url())
    name = f"{main.database}_fresh_{os.getpid()}"
    url = main.set(database=name).render_as_string(hide_password=False)
    _recreate(database_url(), name, create=True)
    database = make_database(url)
    try:
        yield url, database
    finally:
        database.engine.dispose()
        _recreate(database_url(), name, create=False)


def _document_at_0001(database: Database, customer_id: str, user_id: str) -> str:
    """A document row as 0001 knows it: no folder, trash or version columns yet."""
    document_id = str(uuid.uuid4())
    with database.customer(customer_id) as conn:
        conn.execute(
            text(
                "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes, detected_type,"
                " status, uploaded_by) VALUES (:d, :c, 'bill.pdf', :h, 10, 'pdf', 'ready', :u)"
            ),
            {"d": document_id, "c": customer_id, "h": uuid.uuid4().hex + uuid.uuid4().hex, "u": user_id},
        )
    return document_id


def _version_columns(database: Database, customer_id: str, document_id: str) -> tuple[Any, ...]:
    with database.customer(customer_id) as conn:
        row = conn.execute(
            text(
                "SELECT family_id, version_no, is_current, folder_id, deleted_at, deleted_by"
                " FROM documents WHERE id = :d"
            ),
            {"d": document_id},
        ).one()
    return tuple(row)


def _first_version(document_id: str) -> tuple[Any, ...]:
    """family_id, version_no, is_current, folder_id, deleted_at and deleted_by of a first version."""
    return (uuid.UUID(document_id), 1, True, None, None, None)


def _schema(database: Database) -> dict[str, list[tuple[Any, ...]]]:
    """Everything a migration can change in ``public``, as sorted plain rows."""
    queries = {
        "relations": "SELECT relname, relkind::text, relrowsecurity, relacl::text FROM pg_class"
        " WHERE relnamespace = 'public'::regnamespace",
        "columns": "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,"
        " pg_get_expr(d.adbin, d.adrelid)"
        " FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid"
        " LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum"
        " WHERE c.relnamespace = 'public'::regnamespace AND a.attnum > 0 AND NOT a.attisdropped",
        "constraints": "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)"
        " FROM pg_constraint WHERE connamespace = 'public'::regnamespace",
        "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'",
        "policies": "SELECT tablename, policyname, cmd, qual, with_check FROM pg_policies"
        " WHERE schemaname = 'public'",
        "functions": "SELECT proname, pg_get_function_identity_arguments(oid), prosecdef,"
        " proconfig::text, proacl::text FROM pg_proc"
        " WHERE pronamespace = 'public'::regnamespace AND proname LIKE 'kaagaz%'",
        "triggers": "SELECT tgrelid::regclass::text, tgname FROM pg_trigger WHERE NOT tgisinternal",
    }
    with database.system() as conn:
        return {
            part: sorted(tuple(row) for row in conn.execute(text(query))) for part, query in queries.items()
        }


def _names(schema: dict[str, list[tuple[Any, ...]]], part: str, position: int = 0) -> set[str]:
    return {str(row[position]) for row in schema[part]}


def test_the_fresh_database_is_this_runs_own(fresh: tuple[str, Database]) -> None:
    """QA W5/T3: two test runs on one server never drop each other's fresh database."""
    url, _ = fresh
    assert make_url(url).database == f"{make_url(database_url()).database}_fresh_{os.getpid()}"


def test_upgrade_makes_every_existing_document_its_own_first_version(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0001")
    # Two customers: the backfill runs as the owner, so it must reach both.
    a, a_user = new_customer(database)
    b, b_user = new_customer(database)
    a_document = _document_at_0001(database, a, a_user)
    b_document = _document_at_0001(database, b, b_user)

    migrate(url, "head")

    for customer, document in ((a, a_document), (b, b_document)):
        assert _version_columns(database, customer, document) == _first_version(document)
    with database.customer(a) as conn:
        user = conn.execute(
            text("SELECT failed_logins, session_version, must_change_password FROM users WHERE id = :u"),
            {"u": a_user},
        ).one()
    assert tuple(user) == (0, 1, False)


def test_downgrade_removes_everything_and_upgrading_again_works(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0001")
    at_0001 = _schema(database)
    customer, user = new_customer(database)
    document = _document_at_0001(database, customer, user)

    migrate(url, "head")
    at_head = _schema(database)
    assert set(NEW_TABLES) <= _names(at_head, "relations")
    assert set(NEW_INDEXES) <= _names(at_head, "indexes", 1)
    assert set(NEW_CONSTRAINTS) <= _names(at_head, "constraints", 1)
    assert "kaagaz_customer_ids_with_trash" in _names(at_head, "functions")
    head_columns = {(str(row[0]), str(row[1])) for row in at_head["columns"]}
    assert {("users", column) for column in NEW_USER_COLUMNS} <= head_columns
    assert {("documents", column) for column in NEW_DOCUMENT_COLUMNS} <= head_columns

    # Rows in every new table and column, so the downgrade has something to drop.
    with database.customer(customer) as conn:
        folder = str(
            conn.execute(
                text(
                    "INSERT INTO folders (customer_id, name, created_by) VALUES (:c, 'GST', :u) RETURNING id"
                ),
                {"c": customer, "u": user},
            ).scalar_one()
        )
        tag = str(
            conn.execute(
                text("INSERT INTO tags (customer_id, name, created_by) VALUES (:c, 'Paid', :u) RETURNING id"),
                {"c": customer, "u": user},
            ).scalar_one()
        )
        conn.execute(
            text("INSERT INTO document_tags (customer_id, document_id, tag_id) VALUES (:c, :d, :t)"),
            {"c": customer, "d": document, "t": tag},
        )
        conn.execute(
            text("UPDATE documents SET folder_id = :f, deleted_at = :at, deleted_by = :u WHERE id = :d"),
            {"f": folder, "at": datetime(2026, 9, 29, tzinfo=UTC), "u": user, "d": document},
        )

    _downgrade(url, "0001")

    assert _schema(database) == at_0001
    with database.system() as conn:
        gone = conn.execute(
            text(
                "SELECT to_regclass('public.folders'), to_regclass('public.tags'),"
                " to_regclass('public.document_tags'), to_regprocedure(:f)"
            ),
            {"f": TRASH_FUNCTION},
        ).one()
    assert tuple(gone) == (None, None, None, None)
    with database.customer(customer) as conn:
        kept = str(conn.execute(text("SELECT id FROM documents WHERE id = :d"), {"d": document}).scalar_one())
    assert kept == document

    migrate(url, "head")

    assert _schema(database) == at_head
    assert _version_columns(database, customer, document) == _first_version(document)
