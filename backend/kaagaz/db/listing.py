"""What the document screens read: the file list, one document, its pages,
values and spreadsheet cells, and deleting a document.

Why here: the API only shapes JSON; the SQL lives in one reviewable place.
Every function takes a connection from ``Database.customer(customer_id)`` and
filters on ``customer_id`` as well (two locks, docs/architecture.md).

The ``q`` search matches ``display_name`` with ILIKE. The user's text is a bound
parameter, and ``%``, ``_`` and ``\\`` in it are escaped, so it is matched
literally.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Connection, RowMapping, text

_SUMMARY = (
    "SELECT d.id, d.display_name, d.detected_type, d.size_bytes, d.status, d.failure_code,"
    " d.page_count, d.created_at, d.uploaded_by, u.display_name AS uploaded_by_name"
    " FROM documents d"
    " LEFT JOIN users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by"
    " WHERE d.customer_id = :c"
)

_COUNT = "SELECT count(*) FROM documents d WHERE d.customer_id = :c"

_FILTERS = (
    " AND (CAST(:status AS text) IS NULL OR d.status = CAST(:status AS text))"
    " AND (CAST(:pattern AS text) IS NULL OR d.display_name ILIKE CAST(:pattern AS text) ESCAPE '\\')"
)


def _like_pattern(contains: str | None) -> str | None:
    if not contains:
        return None
    escaped = contains.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_documents(
    conn: Connection,
    customer_id: str,
    *,
    limit: int,
    offset: int,
    status: str | None,
    name_contains: str | None,
) -> tuple[Sequence[RowMapping], int]:
    """One page of this customer's documents, newest first, and the total that match.

    Two queries on the (customer_id, created_at) index: O(limit + offset) rows
    read without ``q``; with ``q``, O(documents of this customer).
    """
    params: dict[str, Any] = {
        "c": customer_id,
        "status": status,
        "pattern": _like_pattern(name_contains),
        "limit": limit,
        "offset": offset,
    }
    rows = (
        conn.execute(
            text(_SUMMARY + _FILTERS + " ORDER BY d.created_at DESC, d.id DESC LIMIT :limit OFFSET :offset"),
            params,
        )
        .mappings()
        .all()
    )
    total: int = conn.execute(text(_COUNT + _FILTERS), params).scalar_one()
    return rows, int(total)


def get_document(conn: Connection, customer_id: str, document_id: str) -> RowMapping | None:
    return (
        conn.execute(text(_SUMMARY + " AND d.id = :d"), {"c": customer_id, "d": document_id})
        .mappings()
        .first()
    )


def list_pages(conn: Connection, customer_id: str, document_id: str) -> Sequence[RowMapping]:
    return (
        conn.execute(
            text(
                "SELECT page_no, width, height, unit, has_image FROM pages"
                " WHERE customer_id = :c AND document_id = :d ORDER BY page_no"
            ),
            {"c": customer_id, "d": document_id},
        )
        .mappings()
        .all()
    )


def get_page(conn: Connection, customer_id: str, document_id: str, page_no: int) -> RowMapping | None:
    return (
        conn.execute(
            text(
                "SELECT page_no, width, height, unit, has_image FROM pages"
                " WHERE customer_id = :c AND document_id = :d AND page_no = :n"
            ),
            {"c": customer_id, "d": document_id, "n": page_no},
        )
        .mappings()
        .first()
    )


def list_values(conn: Connection, customer_id: str, document_id: str) -> Sequence[RowMapping]:
    """Every value of one document, by page, record and field. O(v log v)."""
    return (
        conn.execute(
            text(
                "SELECT id, document_id, record_key, field, value_text, value_raw, second_reading,"
                " page_no, region, label, label_reason, confidence"
                " FROM extracted_values WHERE customer_id = :c AND document_id = :d"
                " ORDER BY page_no, record_key, field, id"
            ),
            {"c": customer_id, "d": document_id},
        )
        .mappings()
        .all()
    )


def sheet_cells(
    conn: Connection, customer_id: str, document_id: str, sheet: int, row_from: int, row_to: int
) -> Sequence[RowMapping]:
    """The cell texts of one sheet between two rows, from the first reader's
    stored output, ordered by row and column.

    Each stored span is ``{"text", "raw", "region": {...}, "confidence"}``
    (``kaagaz.db.pipeline_store.span_to_json``); only
    cell locations of this sheet count. O(spans of the document): the output is
    one jsonb array, unpacked in the database.
    """
    return (
        conn.execute(
            text(
                "SELECT (loc ->> 'row')::int AS row_no, (loc ->> 'column')::int AS column_no,"
                " span ->> 'text' AS text"
                " FROM reader_outputs ro"
                " CROSS JOIN LATERAL jsonb_array_elements(ro.output) AS span"
                " CROSS JOIN LATERAL (SELECT span -> 'region' AS loc) AS l"
                " WHERE ro.customer_id = :c AND ro.document_id = :d AND ro.role = 'first'"
                " AND loc ->> 'kind' = 'cell' AND (loc ->> 'page')::int = :sheet"
                " AND (loc ->> 'row')::int BETWEEN :row_from AND :row_to"
                " ORDER BY row_no, column_no"
            ),
            {"c": customer_id, "d": document_id, "sheet": sheet, "row_from": row_from, "row_to": row_to},
        )
        .mappings()
        .all()
    )


def delete_document(conn: Connection, customer_id: str, document_id: str) -> None:
    """Delete the document; pages, pieces, values and the rest go with it (cascade)."""
    conn.execute(
        text("DELETE FROM documents WHERE customer_id = :c AND id = :d"),
        {"c": customer_id, "d": document_id},
    )
