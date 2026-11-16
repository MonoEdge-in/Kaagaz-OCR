"""The PostgreSQL error codes the SQL modules turn into API errors, and the deadlock retry."""

import logging
from collections.abc import Callable

from sqlalchemy.exc import DBAPIError, OperationalError

_log = logging.getLogger(__name__)

UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"
CHECK_VIOLATION = "23514"
DEADLOCK_DETECTED = "40P01"
INSUFFICIENT_PRIVILEGE = "42501"  # PRD 7 (#606): the database rule refused a row
DEADLOCK_RETRIES = 3  # AR7: the whole transaction runs again up to 3 times


def sqlstate(error: DBAPIError) -> str | None:
    """The SQLSTATE of the driver error inside ``error``; None if it has none."""
    state = getattr(error.orig, "sqlstate", None)
    return state if isinstance(state, str) else None


def log_unavailable(error: BaseException, *, path: str | None = None) -> None:
    """#620: log the error behind a BackendUnavailable or a 503, code ``backend_unavailable``: its
    class, the driver's class and its SQLSTATE (57014 a statement timeout, 55P03 a lock timeout,
    53300 too many connections; none for a pool wait), and the route's ``path`` when known; never
    its message, statement or values."""
    orig = getattr(error, "orig", None)
    _log.warning(
        "backend unavailable",
        extra={
            "code": "backend_unavailable",
            "error_class": type(error).__name__,
            "driver_error": None if orig is None else type(orig).__name__,
            "sqlstate": sqlstate(error) if isinstance(error, DBAPIError) else None,
            "path": path,
        },
    )


def retry_on_deadlock[T](transaction: Callable[[], T], *, customer_id: str, document_id: str) -> T:
    """Run ``transaction`` (one whole transaction) again when PostgreSQL broke a
    deadlock by failing it (40P01), up to DEADLOCK_RETRIES more times.

    Call it inside ``engine.unavailable()``, so a deadlock on the last try still
    becomes BackendUnavailable and the job is retried later. Every other error is
    raised at once. Each retry is logged as ``deadlock_retried``, with ids only.
    """
    retries = 0
    while True:
        try:
            return transaction()
        except OperationalError as error:
            if sqlstate(error) != DEADLOCK_DETECTED or retries == DEADLOCK_RETRIES:
                raise
        retries += 1
        _log.warning("deadlock_retried", extra={"customer_id": customer_id, "document_id": document_id})
