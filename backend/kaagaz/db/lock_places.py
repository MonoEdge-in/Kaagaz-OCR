"""The locked files of a place and of a person, for an Admin's pop-ups (PRD 7 section 6, "Giving a
folder or department that holds locked files", and the Access page's "Locked files"; #605, PR 13).

Names and numbers only, never content, for an Admin's screens (decision 20: true names, whether
the Admin may open the file or not). A place gives what R5 and R4 give: a department's files
outside private folders (and, for its Head, those inside them too), or a private folder's files;
current versions out of Trash, none held for its uploader. ``open``: the person may open that
locked file already (a lock grant). Every statement takes a connection from
``Database.user(customer_id, user_id)`` for an Admin, already checked by the route, and names the
customer. The API alone calls them, through 0094's owner-rights functions (which check the caller is
an Admin and name the customer themselves); ``customer_id`` must be the transaction's, else none.
"""

from collections.abc import Sequence

from sqlalchemy import Connection, RowMapping, text

# 0094 (#605): owner-rights functions, as 0091's per-person rule hides from an Admin the rows of a
# file they can't open; each checks the caller is an Admin and names the customer itself.
_FILES = text(
    "SELECT kaagaz_admin_place_files(CAST(:d AS uuid), CAST(:p AS uuid), :head)"
    " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
_LOCKED = text(
    "SELECT id, display_name, folder_id, open"
    " FROM kaagaz_admin_place_locked(CAST(:d AS uuid), CAST(:p AS uuid), :head, CAST(:u AS uuid))"
    " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
_LOCKED_FOR = text(
    "SELECT id, display_name, folder_id, given_at, given_by FROM kaagaz_admin_locked_for(CAST(:u AS uuid))"
    " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)


def _place(
    customer_id: str, department_id: str | None, private_id: str | None, head: bool
) -> dict[str, object]:
    return {"c": customer_id, "d": department_id, "p": private_id, "head": head}


def files(
    conn: Connection, customer_id: str, department_id: str | None, private_id: str | None, *, head: bool
) -> int:
    """How many files the place gives (``private_id``: a private folder's; else the department's)."""
    return int(conn.execute(_FILES, _place(customer_id, department_id, private_id, head)).scalar() or 0)


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
