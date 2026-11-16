"""The only way code talks to PostgreSQL.

Every transaction starts the same way (CLAUDE.md never-do 1):

1. ``SET LOCAL ROLE <db_app_role>``: a role that is not a superuser, does not
   own the tables and has no BYPASSRLS, so row level security always applies,
   even when the connection logged in as the owner (as in CI and on the laptop).
   On the server the API and the workers log in as ``kaagaz_api`` (#479, 0067),
   which holds no right until this switch, so a transaction without it reads nothing.
   The workers and the CLI switch to ``db_worker_role`` instead: ``kaagaz_worker``, with the
   same rights, on their own login ``kaagaz_jobs`` once a server names it (#587, 0083).
2. ``set_config('kaagaz.customer_id', <customer>, true)``: the current customer
   for this transaction only. Every table's policy compares its ``customer_id``
   with it, so a forgotten ``WHERE`` still returns nothing from another customer.

``Database.customer`` is for everything a customer does; ``Database.user`` also sets
``kaagaz.user_id``, without which the one-user tables (my words, 0008) show nothing.
``Database.system`` has
no current customer, so every customer table looks empty inside it; it exists
only to call the few SECURITY DEFINER functions the schema provides (signing
in by e-mail, creating a customer, listing the customers with old trash for
the purge) and for health checks.

Queries are plain SQL through ``sqlalchemy.text`` with bound parameters.
Never build SQL from strings (ruff's S608 checks for it).
"""

import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from kaagaz.db.sqlstate import log_unavailable
from kaagaz.ingestion.errors import BackendUnavailable


def normalise_url(url: str) -> str:
    """Use the psycopg 3 driver for plain ``postgresql://`` URLs (CI gives one)."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


# The pool, per process (PROVISIONAL numbers): 10 connections kept, 5 more when busy
# (15 at most, SQLAlchemy's default total), and a wait of at most 10 seconds for a
# free one. A wait that runs out is a pool TimeoutError: 503 busy, like a lost database.
POOL_SIZE = 10
MAX_OVERFLOW = 5
POOL_TIMEOUT_SECONDS = 10


def make_engine(
    database_url: str,
    *,
    statement_timeout_seconds: int = 0,
    lock_timeout_seconds: int = 0,
    idle_in_transaction_timeout_seconds: int = 0,
) -> Engine:
    """``hide_parameters``: a failing statement's error and the engine's log lines
    never show the bound values, which can be row values or document text.

    The three timeouts (#246 A1) are set on every connection when it opens, so they hold
    for the whole process; 0 is PostgreSQL's own "no limit", the default (the CLI, tests).
    A statement's own ``set_config('statement_timeout', ..., true)`` (search, listing,
    insights) still wins inside its transaction. A statement or lock wait cut short is an
    OperationalError, so ``unavailable()`` makes it BackendUnavailable (503 busy, or the
    worker's retry), never a crash."""
    timeouts = {
        "statement_timeout": statement_timeout_seconds,
        "lock_timeout": lock_timeout_seconds,
        "idle_in_transaction_session_timeout": idle_in_transaction_timeout_seconds,
    }
    options = " ".join(f"-c {name}={seconds * 1000}" for name, seconds in timeouts.items() if seconds > 0)
    return create_engine(
        normalise_url(database_url),
        pool_pre_ping=True,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT_SECONDS,
        future=True,
        hide_parameters=True,
        connect_args={"options": options} if options else {},
    )


@contextmanager
def unavailable() -> Iterator[None]:
    """Turn "cannot reach the database" (OperationalError, or no free pooled connection)
    into BackendUnavailable, the error the upload service undoes and retries on and the
    worker retries the job on. Constraint errors stay what they are: they are bugs, not outages.
    The error's class and SQLSTATE are logged first (#620, ``log_unavailable``)."""
    try:
        yield
    except (OperationalError, PoolTimeout) as error:
        log_unavailable(error)
        raise BackendUnavailable("database") from None


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
    def user(self, customer_id: str, user_id: str, *, repeatable_read: bool = False) -> Iterator[Connection]:
        """One transaction as ``user_id`` of ``customer_id``: the customer, and the one
        user whose own rows (my words) it may see and write. O(1) setup. ``repeatable_read``
        (PRD 7, #601): every statement sees one snapshot, for reads compared with each other."""
        uuid.UUID(customer_id)
        uuid.UUID(user_id)  # malformed ids fail here, never inside a policy
        with self._begin("REPEATABLE READ" if repeatable_read else None) as conn:
            both = "SELECT set_config('kaagaz.customer_id', :c, true), set_config('kaagaz.user_id', :u, true)"
            conn.execute(text(both), {"c": customer_id, "u": user_id})
            yield conn

    @contextmanager
    def system(self) -> Iterator[Connection]:
        """One transaction with no current customer (see the module docstring)."""
        with self._begin() as conn:
            yield conn

    def model_rate_slot(
        self,
        request_id: str,
        customer_id: str | None,
        user_id: str | None,
        wait_budget: float,
        platform_rpm: int = 1200,
    ) -> float:
        """Wait for and reserve a shared OpenRouter slot; return a delay or -1 past deadline.

        The security-definer function checks the supplied customer/user pair and updates the
        one platform scheduler atomically, across API and worker processes. The platform RPM is
        the super admin's (``platform_ai_settings.model_rpm``, 0053), read at every call, never
        above ``platform_rpm`` (MODEL_RPM); ``platform_rpm`` itself while none is set.
        """
        import uuid

        if customer_id is not None:
            uuid.UUID(customer_id)
        if user_id is not None:
            uuid.UUID(user_id)
        uuid.UUID(request_id)
        params = {
            "request": request_id,
            "customer": customer_id,
            "user": user_id,
            "budget": wait_budget,
            "platform_rpm": platform_rpm,
        }
        statement = text(
            "SELECT kaagaz_model_rate_slot(:request, :customer, :user, :budget,"
            " least(CAST(:platform_rpm AS integer), (SELECT p.model_rpm FROM platform_ai_settings p)))"
        )
        if customer_id is not None and user_id is not None:
            with self.user(customer_id, user_id) as conn:
                return float(conn.execute(statement, params).scalar_one())
        if customer_id is not None:
            with self.customer(customer_id) as conn:
                return float(conn.execute(statement, params).scalar_one())
        with self.system() as conn:
            return float(conn.execute(statement, params).scalar_one())

    def ping(self) -> None:
        """Raise BackendUnavailable unless the database answers. No free pooled connection
        counts as no answer."""
        try:
            with self.system() as conn:
                conn.execute(text("SELECT 1"))
        except (OperationalError, PoolTimeout) as error:
            log_unavailable(error)
            raise BackendUnavailable("database") from error

    @contextmanager
    def _begin(self, isolation: str | None = None) -> Iterator[Connection]:
        if isolation is None:
            with self._engine.begin() as conn:
                conn.execute(self._set_role)
                yield conn
            return
        with self._engine.connect() as conn:
            conn.execution_options(isolation_level=isolation)  # the pool puts it back on return
            with conn.begin():
                conn.execute(self._set_role)
                yield conn


def user_or_customer(
    db: Database, customer_id: str, user_id: str | None
) -> AbstractContextManager[Connection]:
    """PRD 7 (#585): ``db.user`` for the person ``user_id`` asked for (an API request's work);
    ``db.customer`` when no person did (None: a worker's, a tool's or a test's work)."""
    return db.customer(customer_id) if user_id is None else db.user(customer_id, user_id)


# The platform operator's role (0015), a fixed name like the migration's.
_SET_OPERATOR_ROLE = text("SET LOCAL ROLE kaagaz_operator")


class OperatorDatabase:
    """The /api/operator routes' only way to PostgreSQL (super admin, PROVISIONAL): every
    transaction takes ``kaagaz_operator``, which can only run the ``kaagaz_op_*``
    functions. Deliberately no ``customer``, ``user`` or ``system``."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def operator(self) -> Iterator[Connection]:
        """One transaction as ``kaagaz_operator``. Commits on success, rolls back on error."""
        with self._engine.begin() as conn:
            conn.execute(_SET_OPERATOR_ROLE)
            yield conn
