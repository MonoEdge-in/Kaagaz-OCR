"""Helpers for tests that need a real PostgreSQL (marker ``db``).

The database comes from DATABASE_URL (CI sets it; on the laptop, point it at
any empty PostgreSQL 16+ with pgvector and pg_trgm). Migrations run once per
test session. Tests never clean up: each one makes its own customers with
random names, so tests cannot see each other's rows (row level security).
"""

import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database, make_engine

BACKEND = Path(__file__).resolve().parent.parent
APP_ROLE = "kaagaz_app"
# PRD 7 (#595): tests build their data in the worker role, which sees the whole organisation when
# no person is set; the application under test runs in the API role (test_api_support.Api).
WORKER_ROLE = "kaagaz_worker"


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError('Database tests need DATABASE_URL. Run `pytest -m "not db"` to leave them out.')
    return url


def migrate(url: str, revision: str = "head") -> None:
    """Upgrade the database at ``url`` to ``revision`` (for migration tests: "0001")."""
    # Changed on purpose (#457 with #460, 30 Oct 2026): 4 digits and an optional letter ("0061b"),
    # as tests/test_migration_heads.py allows.
    if revision != "head" and not re.fullmatch(r"\d{4}[a-z]?", revision):
        raise ValueError("revision must be 'head' or a 4-digit revision id, perhaps with a letter")
    subprocess.run(  # noqa: S603 - fixed command; the revision is checked above
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
    )


def make_database(url: str, role: str = WORKER_ROLE) -> Database:
    return Database(make_engine(url), role)


def new_customer(db: Database, *, name: str = "Test customer") -> tuple[str, str]:
    """A fresh customer and its first admin user. Returns (customer_id, admin_user_id)."""
    email = f"admin-{uuid.uuid4().hex}@example.test"
    with db.system() as conn:
        customer = str(
            conn.execute(
                text("SELECT kaagaz_create_customer(:name, :email, 'Admin', 'not-a-real-hash')"),
                {"name": name, "email": email},
            ).scalar_one()
        )
    with db.customer(customer) as conn:
        user_id = str(conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email}).scalar_one())
    return customer, user_id


_DOCUMENT_DEFAULTS: dict[str, Any] = {
    "display_name": "document.pdf",
    "size_bytes": 10,
    "detected_type": "pdf",
    "status": "ready",
}
_DOCUMENT_COLUMNS = frozenset(
    {
        "id",
        "display_name",
        "sha256",
        "size_bytes",
        "detected_type",
        "status",
        "failure_code",
        "page_count",
        "uploaded_by",
        "folder_id",
        "deleted_at",
        "deleted_by",
        "family_id",
        "version_no",
        "is_current",
        "attempts",
        "max_attempts",
        "created_at",
        "doc_type",  # the card's type (0004)
        "autofile",  # feature 28 (0025)
        # Changed on purpose (PRD 1, 0041): file management's columns, and the card's date.
        "owner_id",
        "sensitivity",
        "sensitivity_set_by_person",
        "document_date",
        # Changed on purpose (PRD 1, versions): the batch, the note and the fingerprint.
        "batch_id",
        "version_note",
        "name_key",
        "text_minhash",
        "text_bands",
        "updated_at",  # package S: a document last written long ago (the stuck sweep's clock)
    }
)


def insert_document(conn: Connection, customer_id: str, **columns: Any) -> str:
    """Insert one document row the way the pipeline would leave it, and return its id.

    Any column can be given; the rest get test defaults. ``family_id`` defaults to
    the document's own id (a first version). The column names are checked against
    a fixed list, so they are never taken from input. O(1).
    """
    unknown = set(columns) - _DOCUMENT_COLUMNS
    if unknown:
        raise TypeError(f"unknown document columns: {sorted(unknown)}")
    values = {**_DOCUMENT_DEFAULTS, **columns}
    values.setdefault("id", str(uuid.uuid4()))
    values.setdefault("family_id", values["id"])
    values.setdefault("sha256", uuid.uuid4().hex + uuid.uuid4().hex)
    names = sorted(values)
    statement = text(
        "INSERT INTO documents (customer_id, "  # noqa: S608 - names come from the fixed set above
        + ", ".join(names)
        + ") VALUES (:customer_id, "
        + ", ".join(f":{name}" for name in names)
        + ")"
    )
    conn.execute(statement, {"customer_id": customer_id, **values})
    return str(values["id"])


def violation(error: DBAPIError) -> tuple[str | None, str | None]:
    """The SQLSTATE and the constraint name, so a test can't pass on the wrong error."""
    assert isinstance(error.orig, psycopg.Error)
    return error.orig.sqlstate, error.orig.diag.constraint_name


def insert_folder(
    conn: Connection, customer_id: str, user_id: str, name: str, parent_id: str | None = None
) -> str:
    """Insert one folder row straight into the table (no helper rules), and return its id. O(1)."""
    return str(
        conn.execute(
            text(
                "INSERT INTO folders (customer_id, parent_id, name, created_by)"
                " VALUES (:c, :p, :n, :u) RETURNING id"
            ),
            {"c": customer_id, "p": parent_id, "n": name, "u": user_id},
        ).scalar_one()
    )


def insert_tag(conn: Connection, customer_id: str, user_id: str, name: str) -> str:
    """Insert one tag row straight into the table, and return its id. O(1)."""
    return str(
        conn.execute(
            text("INSERT INTO tags (customer_id, name, created_by) VALUES (:c, :n, :u) RETURNING id"),
            {"c": customer_id, "n": name, "u": user_id},
        ).scalar_one()
    )


def tag_document(conn: Connection, customer_id: str, document_id: str, tag_id: str) -> None:
    """Put one tag on one document. O(1)."""
    conn.execute(
        text("INSERT INTO document_tags (customer_id, document_id, tag_id) VALUES (:c, :d, :t)"),
        {"c": customer_id, "d": document_id, "t": tag_id},
    )


def new_library(db: Database) -> dict[str, str]:
    """A new customer with one folder, one tag and one tagged document in that folder."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "GST")
        tag = insert_tag(conn, customer, user, "Paid")
        document = insert_document(conn, customer, folder_id=folder, uploaded_by=user)
        tag_document(conn, customer, document, tag)
    return {"customer": customer, "user": user, "folder": folder, "tag": tag, "document": document}


def library_rows_seen(db: Database, customer_id: str, library: dict[str, str]) -> tuple[int, int, int]:
    """How many of ``library``'s folder, tag and document_tags rows ``customer_id`` can
    see, by id. ``library`` is what ``new_library`` returns."""
    with db.customer(customer_id) as conn:
        return (
            count_rows(conn, "SELECT count(*) FROM folders WHERE id = :f", {"f": library["folder"]}),
            count_rows(conn, "SELECT count(*) FROM tags WHERE id = :t", {"t": library["tag"]}),
            count_rows(
                conn,
                "SELECT count(*) FROM document_tags WHERE document_id = :d AND tag_id = :t",
                {"d": library["document"], "t": library["tag"]},
            ),
        )


_LAND_FIELDS = {"person": "land_owner_name", "place": "land_village", "property": "land_survey_number"}


_KEY = text(
    "WITH v AS (INSERT INTO extracted_values (customer_id, document_id, record_key, field, value_text,"
    " value_raw, page_no, region, reader, label, label_reason) VALUES (:c, :d, 'land:1', :f, :v, :v, 1,"
    " CAST(:box AS jsonb), 'gemini', 'needs_review', 'single_reader') RETURNING id)"
    " INSERT INTO value_keys (customer_id, value_id, document_id, kind, place_level, latin, sound)"
    " SELECT :c, v.id, :d, :k, :level, :latin, :sound FROM v"
)


def insert_key(
    conn: Connection,
    customer_id: str,
    document_id: str,
    kind: str,
    printed: str,
    place_level: str = "village",
) -> None:
    """One land value printed as ``printed`` and its ``value_keys`` row, as #245 keys it (a
    place at ``place_level``), all of one land record. O(1)."""
    from kaagaz.answering.clarify import latin_key
    from kaagaz.chunking.script import sound_keys
    from kaagaz.extraction.links import normalise_identifier

    # #245: a property's key is links.normalise_identifier ("survey45/2"), never a Latin spelling.
    latin = normalise_identifier(printed) if kind == "property" else latin_key(printed)
    level = place_level if kind == "place" else None
    sound = "" if kind == "property" else " ".join(key for _, key in sound_keys(latin))
    box = '{"kind": "box", "page": 1, "box": [0.1, 0.2, 0.3, 0.25]}'
    named = {"k": kind, "f": _LAND_FIELDS[kind], "v": printed, "level": level, "latin": latin, "sound": sound}
    conn.execute(_KEY, {"c": customer_id, "d": document_id, "box": box, **named})


def new_trashed_document(db: Database, customer_id: str, *, deleted_at: datetime) -> str:
    """One document already in Trash since ``deleted_at``, in its own transaction. O(1)."""
    with db.customer(customer_id) as conn:
        return insert_document(conn, customer_id, deleted_at=deleted_at)


def count_rows(conn: Connection, statement: str, params: dict[str, str] | None = None) -> int:
    """The single number a ``SELECT count(*)`` statement returns."""
    return int(conn.execute(text(statement), params or {}).scalar_one())


def name_session(conn: Connection, application_name: str) -> None:
    """Name this transaction's session, so ``wait_until_blocked`` can find it."""
    conn.execute(text("SELECT set_config('application_name', :name, true)"), {"name": application_name})


def wait_until_blocked(db: Database, *, application_name: str, timeout: float = 5.0) -> None:
    """Wait until the session named ``application_name`` is waiting on a lock.

    Concurrency tests open a second connection with that application_name, start
    it in a thread, and call this before letting the first transaction commit, so
    the order is forced and the test never depends on timing. Polls every 20 ms.
    """
    deadline = time.monotonic() + timeout
    query = text(
        "SELECT count(*) FROM pg_stat_activity WHERE application_name = :name AND wait_event_type = 'Lock'"
    )
    while time.monotonic() < deadline:
        with db.engine.connect() as conn:
            if conn.execute(query, {"name": application_name}).scalar_one():
                return
        time.sleep(0.02)
    raise AssertionError(f"{application_name} never waited on a lock")
