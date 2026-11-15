"""The locked files of a place and of a person, for an Admin's pop-ups (PRD 7 section 6, "Giving a
folder or department that holds locked files", and the Access page's "Locked files"; #605, PR 13).

Names and numbers only, never content, for an Admin's screens (decision 20: true names, whether
the Admin may open the file or not). A place gives what R5 and R4 give: a department's files
outside private folders (and, for its Head, those inside them too), or a private folder's files;
current versions out of Trash, none held for its uploader. ``open``: the person may open that
locked file already (a lock grant). Every statement takes a connection from
``Database.user(customer_id, user_id)`` for an Admin, already checked by the route, and names the
customer. The API alone calls them.
"""

from collections.abc import Sequence

from sqlalchemy import Connection, RowMapping, text

_FILES = text(
    "SELECT count(*) FROM documents d WHERE d.customer_id = :c AND d.deleted_at IS NULL AND d.is_current"
    " AND d.held_for IS NULL AND CASE WHEN CAST(:p AS uuid) IS NOT NULL"
    " THEN d.private_folder_id = CAST(:p AS uuid)"
    " ELSE d.department_id = CAST(:d AS uuid) AND (d.private_folder_id IS NULL OR :head) END"
)
_LOCKED = text(
    "SELECT d.id, d.display_name, d.folder_id, EXISTS (SELECT 1 FROM lock_grants g"
    " WHERE g.customer_id = d.customer_id AND g.document_id = d.id AND g.user_id = CAST(:u AS uuid)) AS open"
    " FROM documents d WHERE d.customer_id = :c AND d.deleted_at IS NULL AND d.is_current"
    " AND d.held_for IS NULL AND d.locked_at IS NOT NULL AND CASE WHEN CAST(:p AS uuid) IS NOT NULL"
    " THEN d.private_folder_id = CAST(:p AS uuid)"
    " ELSE d.department_id = CAST(:d AS uuid) AND (d.private_folder_id IS NULL OR :head) END"
    " ORDER BY lower(d.display_name), d.id"
)
# The Access page: the locked files a person may open, one row a file (its current version).
_LOCKED_FOR = text(
    "SELECT DISTINCT ON (c.family_id) c.id, c.display_name, c.folder_id, g.given_at, g.given_by"
    " FROM lock_grants g"
    " JOIN documents v ON v.customer_id = g.customer_id AND v.id = g.document_id"
    " JOIN documents c ON c.customer_id = v.customer_id AND c.family_id = v.family_id AND c.is_current"
    " WHERE g.customer_id = :c AND g.user_id = :u AND c.deleted_at IS NULL AND c.locked_at IS NOT NULL"
    " ORDER BY c.family_id, g.given_at"
)


def _place(
    customer_id: str, department_id: str | None, private_id: str | None, head: bool
) -> dict[str, object]:
    return {"c": customer_id, "d": department_id, "p": private_id, "head": head}


def files(
    conn: Connection, customer_id: str, department_id: str | None, private_id: str | None, *, head: bool
) -> int:
    """How many files the place gives (``private_id``: a private folder's; else the department's)."""
    return int(conn.execute(_FILES, _place(customer_id, department_id, private_id, head)).scalar_one())


def locked(
    conn: Connection,
    customer_id: str,
    department_id: str | None,
    private_id: str | None,
    *,
    head: bool,
    user_id: str | None,
) -> Sequence[RowMapping]:
    """The place's locked files, by name: id, display_name, folder_id, open (for ``user_id``)."""
    params = {**_place(customer_id, department_id, private_id, head), "u": user_id}
    return conn.execute(_LOCKED, params).mappings().all()


def locked_for(conn: Connection, customer_id: str, user_id: str) -> Sequence[RowMapping]:
    """The locked files a person may open (one row a file, by its current version)."""
    return conn.execute(_LOCKED_FOR, {"c": customer_id, "u": user_id}).mappings().all()
