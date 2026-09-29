"""The PostgreSQL error codes the SQL modules turn into API errors."""

from sqlalchemy.exc import IntegrityError

UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"


def sqlstate(error: IntegrityError) -> str | None:
    """The SQLSTATE of the driver error inside ``error``; None if it has none."""
    state = getattr(error.orig, "sqlstate", None)
    return state if isinstance(state, str) else None
