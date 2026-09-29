"""What the document screens read and change: the file list and its filters,
one document, its pages, values and spreadsheet cells, rename and move.

Why here: the API only shapes JSON; the SQL lives in one reviewable place.
Every function takes a connection from ``Database.customer(customer_id)`` and
filters on ``customer_id`` as well (two locks, docs/architecture.md).

The ``q`` search matches ``display_name`` with ILIKE. The user's text is a bound
parameter, and ``%``, ``_`` and ``\\`` in it are escaped, so it is matched
literally.

The list's newer filters (PRD 1 F4) are fixed SQL fragments, and Python adds
only the ones that are set, so the statement never carries a catch-all such as
"(:x IS NULL OR ...)" for them. The fragments are constants and every value is
a bound parameter, never text put into the SQL.

A folder and tags belong to the whole family (plan 9.3), so a move files every
version; a name belongs to one version, so a rename changes only that one.

What the list shows (PRD 1 F5 and F6, plan 9.7): the newest version of each
family, out of Trash (``LIVE``), or only Trash (``TRASHED``). The fragment is
written literally in ``_LIST`` and ``_TRASH_LIST``; a guard test checks it.
Lookups by id (``_SELECT``, ``_VERSIONS``) have no such filter, so a document
in Trash or an older version still opens.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, RowMapping, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.documents import folder_missing
from kaagaz.db.folders import FolderNotFound

# A DocumentSummary row: the document and its uploader's name.
_COLUMNS = (
    "SELECT d.id, d.display_name, d.detected_type, d.size_bytes, d.status, d.failure_code,"
    " d.page_count, d.created_at, d.uploaded_by, d.folder_id, d.deleted_at, d.family_id, d.version_no,"
    " u.display_name AS uploaded_by_name"
)

_COUNT = "SELECT count(*)"

# By id: the detail, the file, the pages and the reply to an upload.
_SELECT = (
    _COLUMNS + " FROM documents d"
    " LEFT JOIN users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by"
    " WHERE d.customer_id = :c AND d.id = :d"
)

# The list's rows: the newest version of each family (no newer one exists, found
# on the index documents_family_version), not in Trash. _COLUMNS or _COUNT goes first.
_LIST = (
    " FROM documents d"
    " LEFT JOIN users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by"
    " WHERE d.customer_id = :c AND d.deleted_at IS NULL"
    " AND NOT EXISTS (SELECT 1 FROM documents n WHERE n.customer_id = d.customer_id"
    " AND n.family_id = d.family_id AND n.version_no > d.version_no)"
)

# The same, in Trash (trashed=true).
_TRASH_LIST = (
    " FROM documents d"
    " LEFT JOIN users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by"
    " WHERE d.customer_id = :c AND d.deleted_at IS NOT NULL"
    " AND NOT EXISTS (SELECT 1 FROM documents n WHERE n.customer_id = d.customer_id"
    " AND n.family_id = d.family_id AND n.version_no > d.version_no)"
)

_VERSIONS = (
    "SELECT d.id, d.version_no, d.status, d.is_current, d.created_at FROM documents d"
    " WHERE d.customer_id = :c AND d.family_id = :f ORDER BY d.version_no"
)

_FILTERS = (
    " AND (CAST(:status AS text) IS NULL OR d.status = CAST(:status AS text))"
    " AND (CAST(:pattern AS text) IS NULL OR d.display_name ILIKE CAST(:pattern AS text) ESCAPE '\\')"
)

_TOP_LEVEL = " AND d.folder_id IS NULL"
_IN_FOLDERS = " AND d.folder_id = ANY(CAST(:folders AS uuid[]))"
# Every tag must match: the documents holding all :tag_count distinct tags asked for.
_WITH_TAGS = (
    " AND d.id IN (SELECT dt.document_id FROM document_tags dt"
    " WHERE dt.customer_id = :c AND dt.tag_id = ANY(CAST(:tags AS uuid[]))"
    " GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = :tag_count)"
)
_OF_TYPE = " AND d.detected_type = :detected_type"
_UPLOADED_FROM = " AND d.created_at >= :uploaded_from"
_UPLOADED_TO = " AND d.created_at < :uploaded_to"

_RENAME = "UPDATE documents SET display_name = :name WHERE customer_id = :c AND id = :d"

_MOVE_FAMILY = (
    "UPDATE documents SET folder_id = :f WHERE customer_id = :c"
    " AND family_id = (SELECT family_id FROM documents WHERE customer_id = :c AND id = :d)"
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
    top_level_only: bool = False,
    folder_ids: Sequence[str] | None = None,
    tag_ids: Sequence[str] = (),
    detected_type: str | None = None,
    uploaded_from: datetime | None = None,
    uploaded_to: datetime | None = None,
    trashed: bool = False,
) -> tuple[Sequence[RowMapping], int]:
    """One page of this customer's documents, newest first, and the total that match.

    One row per family: its newest version, out of Trash, or with ``trashed``
    only what is in Trash.

    Filters (each left at its default filters nothing): ``top_level_only`` (in no
    folder), ``folder_ids`` (in one of these folders), ``tag_ids`` (holding every
    one of these tags), ``detected_type``, and ``uploaded_from`` (inclusive) to
    ``uploaded_to`` (exclusive). All of them apply in both queries, before LIMIT.

    Two queries (the page and the count). Cost for this customer's D documents:
    no filter, O(limit + offset) rows on the (customer_id, created_at) index; a
    folder filter, O(n log n) for the n documents in those folders (index
    ``documents_folder``); a tag filter, O(L) for the L links of those tags
    (index ``document_tags_tag``), grouped, then one semi-join; ``q``, the type
    or the dates alone, O(D) rows looked at. The count reads every match.
    Each row looked at costs one more O(log D) probe for a newer version.
    """
    params: dict[str, Any] = {
        "c": customer_id,
        "status": status,
        "pattern": _like_pattern(name_contains),
        "limit": limit,
        "offset": offset,
    }
    chosen: list[str] = []
    if top_level_only:
        chosen.append(_TOP_LEVEL)
    elif folder_ids is not None:
        chosen.append(_IN_FOLDERS)
        params["folders"] = list(folder_ids)
    wanted_tags = list(dict.fromkeys(tag_ids))
    if wanted_tags:
        chosen.append(_WITH_TAGS)
        params["tags"], params["tag_count"] = wanted_tags, len(wanted_tags)
    if detected_type is not None:
        chosen.append(_OF_TYPE)
        params["detected_type"] = detected_type
    if uploaded_from is not None:
        chosen.append(_UPLOADED_FROM)
        params["uploaded_from"] = uploaded_from
    if uploaded_to is not None:
        chosen.append(_UPLOADED_TO)
        params["uploaded_to"] = uploaded_to
    where = (_TRASH_LIST if trashed else _LIST) + _FILTERS + "".join(chosen)
    rows = (
        conn.execute(
            text(_COLUMNS + where + " ORDER BY d.created_at DESC, d.id DESC LIMIT :limit OFFSET :offset"),
            params,
        )
        .mappings()
        .all()
    )
    total: int = conn.execute(text(_COUNT + where), params).scalar_one()
    return rows, int(total)


def get_document(conn: Connection, customer_id: str, document_id: str) -> RowMapping | None:
    return conn.execute(text(_SELECT), {"c": customer_id, "d": document_id}).mappings().first()


def family_versions(conn: Connection, customer_id: str, family_id: str) -> Sequence[RowMapping]:
    """Every version of one family, the first version first, in Trash or not.
    One query on the index documents_family_version: O(log D + v) time and
    O(v) space for the family's v versions."""
    return conn.execute(text(_VERSIONS), {"c": customer_id, "f": family_id}).mappings().all()


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


def rename_document(conn: Connection, customer_id: str, document_id: str, *, name: str) -> None:
    """Give this one version a new display name, already cleaned by the caller. O(log D)."""
    conn.execute(text(_RENAME), {"c": customer_id, "d": document_id, "name": name})


def move_family(conn: Connection, customer_id: str, document_id: str, *, folder_id: str | None) -> None:
    """File every version of the document's family in ``folder_id`` (None: the
    top level). The caller holds the family lock (``trash.lock_family``).

    Raises FolderNotFound when the folder is not this customer's: the foreign
    key refuses it, and the caller's transaction must end. O(v log D) for the
    family's v versions.
    """
    try:
        conn.execute(text(_MOVE_FAMILY), {"c": customer_id, "d": document_id, "f": folder_id})
    except IntegrityError as error:
        if folder_missing(error):
            raise FolderNotFound() from None
        raise
