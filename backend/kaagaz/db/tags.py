"""Tags: a customer's labels, and the set of tags on a document family (PRD 1 F2, WP1.2).

Why here: the tag SQL in one place. Every function takes a connection from
``Database.customer(customer_id)`` and also filters on ``customer_id``.

Tags belong to the family (plan 9.3): ``set_document_tags`` writes the same
set to every version, under the family lock, so two edits of one family run
one after the other and the second one's set wins. Lists are ordered by
``lower(name), id``.

Ids are canonical lowercase UUID strings, as the database returns them; the
router has checked them (a malformed id raises ValueError here). The 50-tag
cap is checked at the boundary (pydantic), not here. The caller checks the
name (1 to 40 characters).

Errors carry the API code in ``code``. After ``TagNameTaken`` a statement has
failed, so the caller's transaction is unusable and must end.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.sqlstate import UNIQUE_VIOLATION, sqlstate
from kaagaz.db.trash import lock_live_family


@dataclass(frozen=True, slots=True)
class TagRow:
    id: str
    name: str
    created_at: datetime


class TagError(Exception):
    """A tag change that is refused. ``code`` is the API error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TagNotFound(TagError):
    """No such tag (or document) for this customer."""

    def __init__(self) -> None:
        super().__init__("not_found")


class TagNameTaken(TagError):
    """This customer already has a tag with this name (any letter case)."""

    def __init__(self) -> None:
        super().__init__("conflict")


_LIST = "SELECT id, name, created_at FROM tags WHERE customer_id = :c ORDER BY lower(name), id"

_INSERT = (
    "INSERT INTO tags (customer_id, name, created_by) VALUES (:c, :name, :u) RETURNING id, name, created_at"
)

_DELETE = "DELETE FROM tags WHERE customer_id = :c AND id = :t RETURNING id"

# KEY SHARE: a tag cannot be deleted between this check and the insert below. A
# tag deleted by a transaction we wait for is simply not returned.
_LOCK_TAGS = (
    "SELECT id, name, created_at FROM tags"
    " WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
    " ORDER BY lower(name), id FOR KEY SHARE"
)

_REMOVE_OTHERS = (
    "DELETE FROM document_tags dt USING documents d"
    " WHERE dt.customer_id = :c AND d.customer_id = :c AND d.family_id = :f"
    " AND dt.document_id = d.id AND dt.tag_id <> ALL(CAST(:ids AS uuid[]))"
)

_ADD = (
    "INSERT INTO document_tags (customer_id, document_id, tag_id)"
    " SELECT d.customer_id, d.id, t.id FROM documents d"
    " JOIN tags t ON t.customer_id = d.customer_id AND t.id = ANY(CAST(:ids AS uuid[]))"
    " WHERE d.customer_id = :c AND d.family_id = :f"
    " ON CONFLICT DO NOTHING"
)

_FOR_DOCUMENTS = (
    "SELECT dt.document_id, t.id, t.name, t.created_at FROM document_tags dt"
    " JOIN tags t ON t.customer_id = dt.customer_id AND t.id = dt.tag_id"
    " WHERE dt.customer_id = :c AND dt.document_id = ANY(CAST(:ids AS uuid[]))"
    " ORDER BY dt.document_id, lower(t.name), t.id"
)


def count_existing(conn: Connection, customer_id: str, tag_ids: Sequence[str]) -> int:
    """How many of these distinct tag ids this customer has (another customer's
    count as missing). O(k log T) for k ids."""
    wanted = list(dict.fromkeys(str(uuid.UUID(tag_id)) for tag_id in tag_ids))
    return int(
        conn.execute(
            text("SELECT count(*) FROM tags WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"),
            {"c": customer_id, "ids": wanted},
        ).scalar_one()
    )


def list_tags(conn: Connection, customer_id: str) -> list[TagRow]:
    """Every tag of this customer, by name. O(T log T) time, O(T) space."""
    return [_tag(row) for row in conn.execute(text(_LIST), {"c": customer_id})]


def create_tag(conn: Connection, customer_id: str, *, name: str, created_by: str) -> TagRow:
    """Add a tag. Raises TagNameTaken. One statement: O(log T) time, O(1) space."""
    try:
        row = conn.execute(text(_INSERT), {"c": customer_id, "name": name, "u": created_by}).one()
    except IntegrityError as error:
        if sqlstate(error) == UNIQUE_VIOLATION:
            raise TagNameTaken() from None
        raise
    return _tag(row)


def delete_tag(conn: Connection, customer_id: str, tag_id: str) -> bool:
    """Delete a tag; it comes off every document (ON DELETE CASCADE). False if
    there is no such tag. O(log T + n log N) time for its n document links, O(1) space."""
    return conn.execute(text(_DELETE), {"c": customer_id, "t": tag_id}).first() is not None


def set_document_tags(
    conn: Connection, customer_id: str, document_id: str, tag_ids: Sequence[str]
) -> list[TagRow]:
    """Make ``tag_ids`` the whole tag set of the document's family (every version),
    and return those tags by name. An empty list clears it; duplicates are ignored.

    A family in Trash raises InTrash (``trash.lock_live_family``): Trash means deleted.
    In order: the family lock, a KEY SHARE lock on the k tags (all k must be this
    customer's, else TagNotFound), then the delete of the others and the insert
    of the missing ones. TagNotFound too when there is no such document; nothing
    is written before either check. O(k log T + v (t + k) log N) time for the
    family's v versions with t tags each, O(k + v) space.
    """
    wanted = list(dict.fromkeys(str(uuid.UUID(tag_id)) for tag_id in tag_ids))
    family_id = lock_live_family(conn, customer_id, document_id)  # InTrash for a family in Trash
    if family_id is None:
        raise TagNotFound()
    params = {"c": customer_id, "f": family_id, "ids": wanted}
    tags = [_tag(row) for row in conn.execute(text(_LOCK_TAGS), params)]
    if len(tags) != len(wanted):
        raise TagNotFound()
    conn.execute(text(_REMOVE_OTHERS), params)
    conn.execute(text(_ADD), params)
    return tags


def tags_for_documents(
    conn: Connection, customer_id: str, document_ids: Sequence[str]
) -> dict[str, list[TagRow]]:
    """Each document's tags, by name, in one query (no N+1). Every id asked for
    is a key, with an empty list when it has none. O(n log N + r log r) time for
    n ids and r links found, O(n + r) space."""
    wanted = list(dict.fromkeys(str(uuid.UUID(document_id)) for document_id in document_ids))
    found: dict[str, list[TagRow]] = {document_id: [] for document_id in wanted}
    if not wanted:
        return found
    for row in conn.execute(text(_FOR_DOCUMENTS), {"c": customer_id, "ids": wanted}):
        found[str(row[0])].append(TagRow(id=str(row[1]), name=str(row[2]), created_at=row[3]))
    return found


def _tag(row: Sequence[Any]) -> TagRow:
    return TagRow(id=str(row[0]), name=str(row[1]), created_at=row[2])
