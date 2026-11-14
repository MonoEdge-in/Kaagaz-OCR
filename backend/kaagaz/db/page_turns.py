"""Page turns saved for everyone (PRD 7 section 14 step 2, #600; table ``page_rotations``, 0088).

One row per turned page of one file version: the clockwise turn on top of the file's own, 90, 180
or 270; no row, not turned. The API alone imports this module (the read names
``visible_documents``, which the worker must never reach). The routes check that the person can
see the file and lock its family before they write; a save changes only the pages it names, so the
last save wins per page.
"""

from collections.abc import Mapping

from sqlalchemy import Connection, text

from kaagaz.db.visibility import SEEN_DOCUMENT

_FOR_DOCUMENT = text(
    "SELECT page_no, turn FROM page_rotations"  # noqa: S608 - a fixed fragment, visibility.SEEN_DOCUMENT
    " WHERE customer_id = :c AND document_id = :d" + SEEN_DOCUMENT + " ORDER BY page_no"
)
_CLEAR_PAGES = text(
    "DELETE FROM page_rotations WHERE customer_id = :c AND document_id = :d"
    " AND page_no = ANY(CAST(:pages AS int[]))"
)
_ADD = text(
    "INSERT INTO page_rotations (customer_id, document_id, page_no, turn, set_by)"
    " SELECT :c, :d, p.page_no, p.turn, :by"
    " FROM unnest(CAST(:pages AS int[]), CAST(:turns AS smallint[])) AS p (page_no, turn)"
)
_RESET = text("DELETE FROM page_rotations WHERE customer_id = :c AND document_id = :d")


def for_document(conn: Connection, customer_id: str, document_id: str) -> list[dict[str, int]]:
    """The version's turned pages, by page; [] for a file the person cannot see."""
    rows = conn.execute(_FOR_DOCUMENT, {"c": customer_id, "d": document_id})
    return [{"page_no": int(page_no), "turn": int(turn)} for page_no, turn in rows]


def save(
    conn: Connection, customer_id: str, document_id: str, changes: Mapping[int, int], *, by: str
) -> None:
    """Set each named page's turn (0: not turned); the other pages keep theirs."""
    pages = sorted(changes)
    turned = [page for page in pages if changes[page]]
    conn.execute(_CLEAR_PAGES, {"c": customer_id, "d": document_id, "pages": pages})
    if turned:
        params = {"c": customer_id, "d": document_id, "by": by}
        conn.execute(_ADD, {**params, "pages": turned, "turns": [changes[page] for page in turned]})


def reset(conn: Connection, customer_id: str, document_id: str) -> int:
    """Remove every turn of the version; how many pages were turned."""
    return conn.execute(_RESET, {"c": customer_id, "d": document_id}).rowcount
