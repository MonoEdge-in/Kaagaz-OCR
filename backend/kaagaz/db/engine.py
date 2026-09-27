"""The only way code talks to PostgreSQL.

Every transaction starts the same way (CLAUDE.md never-do 1):

1. ``SET LOCAL ROLE <db_app_role>``: a role that is not a superuser, does not
   own the tables and has no BYPASSRLS, so row level security always applies,
   even when the connection logged in as the owner (as in CI and on the laptop).
2. ``set_config('kaagaz.customer_id', <customer>, true)``: the current customer
   for this transaction only. Every table's policy compares its ``customer_id``
   with it, so a forgotten ``WHERE`` still returns nothing from another customer.

``Database.customer`` is for everything a customer does. ``Database.system`` has
no current customer, so every customer table looks empty inside it; it exists
only to call the few SECURITY DEFINER functions the schema provides (signing
in by e-mail, creating a customer) and for health checks.

Queries are plain SQL through ``sqlalchemy.text`` with bound parameters.
Never build SQL from strings (ruff's S608 checks for it).
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import OperationalError

from kaagaz.ingestion.errors import BackendUnavailable


def normalise_url(url: str) -> str:
    """Use the psycopg 3 driver for plain ``postgresql://`` URLs (CI gives one)."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(database_url: str) -> Engine:
    return create_engine(normalise_url(database_url), pool_pre_ping=True, future=True)


def _is_role_name(name: str) -> bool:
    return bool(name) and name.replace("_", "").isalnum() and not name[0].isdigit()


class Database:
    def __init__(self, engine: Engine, app_role: str) -> None:
        # The role name is put into SQL (SET ROLE cannot take a parameter), so it
        # is checked to be a plain identifier first.
        if not _is_role_name(app_role):
            raise ValueError("app_role must be a plain identifier")
        self._engine = engine
        self._set_role = text(f"SET LOCAL ROLE {app_role}")

    @property
    def engine(self) -> Engine:
        return self._engine

    @contextmanager
    def customer(self, customer_id: str) -> Iterator[Connection]:
        """One transaction as ``customer_id``. Commits on success, rolls back on error."""
        uuid.UUID(customer_id)  # a malformed id fails here, never inside a policy
        with self._begin() as conn:
            conn.execute(
                text("SELECT set_config('kaagaz.customer_id', :customer_id, true)"),
                {"customer_id": customer_id},
            )
            yield conn

    @contextmanager
    def system(self) -> Iterator[Connection]:
        """One transaction with no current customer (see the module docstring)."""
        with self._begin() as conn:
            yield conn

    def ping(self) -> None:
        """Raise BackendUnavailable unless the database answers."""
        try:
            with self.system() as conn:
                conn.execute(text("SELECT 1"))
        except OperationalError as error:
            raise BackendUnavailable("database") from error

    @contextmanager
    def _begin(self) -> Iterator[Connection]:
        with self._engine.begin() as conn:
            conn.execute(self._set_role)
            yield conn
