"""A locked file on the routes (PRD 7 section 6, "How view only is enforced", PR 12, #605).

Every route for a file finds it through the access view first, so a person who may not open a
locked file gets the same 404 as for an id that never was. Only then is the lock checked: a
person who may open it gets 409 ``locked``, "This file is locked. It can only be viewed.", on
every route that would serve its bytes, its words, its readings or its questions, or read it
again. The routes that keep working on a locked file (its name, folder, tags, versions, Trash,
the person's own story) say so where they look it up (``locked_ok``). PR 13 adds the one route
that shows its page pictures.

The test ``test_locked_routes`` walks every ``{document_id}`` route of the app against a locked
file, so a route added later is refused until it is classified.
"""

from sqlalchemy import Connection, RowMapping, text

from kaagaz.api.errors import ApiError

LOCKED = "This file is locked. It can only be viewed."
UNLOCK_FIRST = "This file is locked. Unlock it first."
# Read after the route's own lookup through the view, so only a file the person sees reaches it.
_LOCKED = text("SELECT locked_at IS NOT NULL FROM visible_documents WHERE customer_id = :c AND id = :d")


def refuse_locked(row: RowMapping) -> None:
    """409 ``locked`` for a row of ``listing.get_document`` (the view: found, so seen)."""
    if row["locked"]:
        raise ApiError(409, "locked", LOCKED)


def refuse_locked_id(conn: Connection, customer_id: str, document_id: str) -> None:
    """409 ``locked`` for a file the route already found through the view by another lookup."""
    if conn.execute(_LOCKED, {"c": customer_id, "d": document_id}).scalar():
        raise ApiError(409, "locked", LOCKED)
