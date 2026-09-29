"""Helpers for tests that need a real PostgreSQL (marker ``db``).

The database comes from DATABASE_URL (CI sets it; on the laptop, point it at
any empty PostgreSQL 16+ with pgvector and pg_trgm). Migrations run once per
test session. Tests never clean up: each one makes its own customers with
random names, so tests cannot see each other's rows (row level security).
"""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

from kaagaz.db.engine import Database, make_engine

BACKEND = Path(__file__).resolve().parent.parent
APP_ROLE = "kaagaz_app"


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError('Database tests need DATABASE_URL. Run `pytest -m "not db"` to leave them out.')
    return url


def migrate(url: str, revision: str = "head") -> None:
    """Upgrade the database at ``url`` to ``revision`` (for migration tests: "0001")."""
    if revision != "head" and not (len(revision) == 4 and revision.isdigit()):
        raise ValueError("revision must be 'head' or a 4-digit revision id")
    subprocess.run(  # noqa: S603 - fixed command; the revision is checked above
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
    )


def make_database(url: str) -> Database:
    return Database(make_engine(url), APP_ROLE)


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
        "created_at",
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
