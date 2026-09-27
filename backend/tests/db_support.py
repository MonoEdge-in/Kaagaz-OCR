"""Helpers for tests that need a real PostgreSQL (marker ``db``).

The database comes from DATABASE_URL (CI sets it; on the laptop, point it at
any empty PostgreSQL 16+ with pgvector and pg_trgm). Migrations run once per
test session. Tests never clean up: each one makes its own customers with
random names, so tests cannot see each other's rows (row level security).
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import text

from kaagaz.db.engine import Database, make_engine

BACKEND = Path(__file__).resolve().parent.parent
APP_ROLE = "kaagaz_app"


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError('Database tests need DATABASE_URL. Run `pytest -m "not db"` to leave them out.')
    return url


def migrate(url: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
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
