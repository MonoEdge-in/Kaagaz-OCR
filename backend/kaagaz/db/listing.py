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

What the list shows (PRD 1 F5 and F6, plan 9.7): the current version of each
family, out of Trash (``LISTED``), or only Trash (``TRASHED_LISTED``). The
current version is the one search and chat use, so a newer version that is
still being read, or failed, never hides it; that newer one comes along in the
row's ``newer`` column instead. The fragment is written literally in ``_LIST``
and ``_TRASH_LIST``; a guard test checks it. Lookups by id (``_SELECT``,
``_VERSIONS``) have no such filter, so a document in Trash or an older version
still opens.

Every row also carries ``family_uploaders``: who uploaded each version of its
family. Moving a family to Trash or back acts on every version, so only an
admin, or a user who uploaded every version, may do it (``can_delete``).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, RowMapping, text
from sqlalchemy.exc import IntegrityError

from kaagaz.chunking.context import document_part
from kaagaz.db.documents import folder_missing
from kaagaz.db.folders import FolderNotFound

# A DocumentSummary row: the document, its uploader's name, who uploaded each
# version of its family, and the newest version after it that is not in Trash
# (one small JSON object, or NULL). Both subqueries use the index
# documents_family_version on the family's few rows. PRD 5 F4: the pages read
# unreadable or uncertain that are still on the attention list (not kept as is,
# F5), and the values labelled needs review, each counted on the document's own
# rows (the pages key, the index extracted_values_record). PRD 5 U2: the unsure words on
# those pages. PRD 5 Q6: ``batch_ahead``, the files of the same uploader's batch (the
# documents_batch index) uploaded before this one and still being read or waiting; 0
# for a document in no batch. It counts this customer's own rows only, so another
# customer's load never shows in it.
_COLUMNS = (
    "SELECT d.id, d.display_name, d.detected_type, d.size_bytes, d.status, d.failure_code,"
    " d.page_count, d.created_at, d.uploaded_by, d.folder_id, d.deleted_at, d.family_id, d.version_no,"
    " d.attempts, d.max_attempts, d.progress, d.doc_type, d.languages, d.summary,"
    " u.display_name AS uploaded_by_name,"
    " (SELECT count(*) FROM pages pu WHERE pu.customer_id = d.customer_id AND pu.document_id = d.id"
    " AND pu.read_quality = 'unreadable' AND pu.accepted_at IS NULL) AS pages_unreadable,"
    " (SELECT count(*) FROM pages pc WHERE pc.customer_id = d.customer_id AND pc.document_id = d.id"
    " AND pc.read_quality = 'uncertain' AND pc.accepted_at IS NULL) AS pages_uncertain,"
    " (SELECT coalesce(sum(pw.unsure_words), 0) FROM pages pw WHERE pw.customer_id = d.customer_id"
    " AND pw.document_id = d.id AND pw.accepted_at IS NULL) AS words_unsure,"
    " (SELECT count(*) FROM documents b WHERE b.customer_id = d.customer_id AND b.batch_id = d.batch_id"
    " AND b.uploaded_by = d.uploaded_by AND b.deleted_at IS NULL AND b.status IN ('received', 'clean')"
    " AND (b.created_at, b.id) < (d.created_at, d.id)) AS batch_ahead,"
    " (SELECT count(*) FROM extracted_values ev WHERE ev.customer_id = d.customer_id"
    " AND ev.document_id = d.id AND ev.label = 'needs_review') AS values_to_review,"
    " (SELECT array_agg(o.uploaded_by) FROM documents o"
    " WHERE o.customer_id = d.customer_id AND o.family_id = d.family_id) AS family_uploaders,"
    " (SELECT json_build_object('id', n.id, 'version_no', n.version_no, 'status', n.status,"
    " 'failure_code', n.failure_code, 'detected_type', n.detected_type, 'page_count', n.page_count,"
    " 'attempts', n.attempts, 'max_attempts', n.max_attempts, 'progress', n.progress)"
    " FROM documents n WHERE n.customer_id = d.customer_id AND n.family_id = d.family_id"
    " AND n.version_no > d.version_no AND n.deleted_at IS NULL"
    " ORDER BY n.version_no DESC LIMIT 1) AS newer"
)

_COUNT = "SELECT count(*)"

# By id: the detail, the file, the pages and the reply to an upload.
_SELECT = (
    _COLUMNS + " FROM documents d"
    " LEFT JOIN users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by"
    " WHERE d.customer_id = :c AND d.id = :d"
)

# The list's rows: the current version of each family (the unique index
# documents_current allows one per family), not in Trash. _COLUMNS or _COUNT goes first.
_LIST = (
    " FROM documents d"
    " LEFT JOIN users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by"
    " WHERE d.customer_id = :c AND d.deleted_at IS NULL AND d.is_current"
)

# The same, in Trash (trashed=true).
_TRASH_LIST = (
    " FROM documents d"
    " LEFT JOIN users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by"
    " WHERE d.customer_id = :c AND d.deleted_at IS NOT NULL AND d.is_current"
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
# PRD 5 U2 "Needs attention": a page unreadable or uncertain still on the attention list
# (not kept as is), or a value to review; the same rows _COLUMNS counts.
_NEEDS_ATTENTION = (
    " AND (EXISTS (SELECT 1 FROM pages pa WHERE pa.customer_id = :c AND pa.document_id = d.id"
    " AND pa.read_quality IN ('unreadable', 'uncertain') AND pa.accepted_at IS NULL)"
    " OR EXISTS (SELECT 1 FROM extracted_values va WHERE va.customer_id = :c"
    " AND va.document_id = d.id AND va.label = 'needs_review'))"
)
_UPLOADED_FROM = " AND d.created_at >= :uploaded_from"
_UPLOADED_TO = " AND d.created_at < :uploaded_to"

_RENAME = "UPDATE documents SET display_name = :name WHERE customer_id = :c AND id = :d"
_NAME_OF = "SELECT display_name FROM documents WHERE customer_id = :c AND id = :d"
# The context header's first part is "Document: {name}" (PRD 2 C4), followed by
# " | " or nothing; a rename swaps it for the new name in each of the document's pieces.
_RENAME_HEADERS = (
    "UPDATE pieces SET context_header = CASE"
    " WHEN context_header = :old THEN :new"
    " WHEN :new = '' THEN substr(context_header, length(:old) + 4)"
    " ELSE :new || substr(context_header, length(:old) + 1) END"
    " WHERE customer_id = :c AND document_id = :d AND :old <> ''"
    " AND (context_header = :old OR left(context_header, length(:old) + 3) = :old || ' | ')"
)

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
    attention: bool = False,
) -> tuple[Sequence[RowMapping], int]:
    """One page of this customer's documents, newest first, and the total that match.

    One row per family: its current version, out of Trash, or with ``trashed``
    only what is in Trash.

    Filters (each left at its default filters nothing): ``top_level_only`` (in no
    folder), ``folder_ids`` (in one of these folders), ``tag_ids`` (holding every
    one of these tags), ``detected_type``, and ``uploaded_from`` (inclusive) to
    ``uploaded_to`` (exclusive), ``attention`` (PRD 5 U2: a page or a value needs
    attention). All of them apply in both queries, before LIMIT.

    Two queries (the page and the count). Cost for this customer's D documents:
    no filter, O(limit + offset) rows on the (customer_id, created_at) index; a
    folder filter, O(n log n) for the n documents in those folders (index
    ``documents_folder``); a tag filter, O(L) for the L links of those tags
    (index ``document_tags_tag``), grouped, then one semi-join; ``q``, the type
    or the dates alone, O(D) rows looked at; ``attention`` adds two key probes a row.
    The count reads every match.
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
    if attention:
        chosen.append(_NEEDS_ATTENTION)
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


# PRD 5 Q1: one drop of files, the uploader's own only, out of Trash, in upload order.
# Any version: a file uploaded as a duplicate is not in it (it stays in its own batch).
MAX_BATCH_ROWS = 100  # MAX_FILES_PER_BATCH's PRD value; the upload refuses more (409)
_BATCH = (
    _COLUMNS + " FROM documents d"
    " LEFT JOIN users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by"
    " WHERE d.customer_id = :c AND d.batch_id = :b AND d.uploaded_by = :u AND d.deleted_at IS NULL"
    " ORDER BY d.created_at, d.id LIMIT :limit"
)
_BATCH_SIZE = (
    "SELECT count(*) FROM documents d"
    " WHERE d.customer_id = :c AND d.batch_id = :b AND d.uploaded_by = :u AND d.deleted_at IS NULL"
)


def batch_rows(conn: Connection, customer_id: str, batch_id: str, *, user_id: str) -> Sequence[RowMapping]:
    """The DocumentSummary rows of ``user_id``'s batch ``batch_id``, at most
    MAX_BATCH_ROWS; empty for another user's or customer's batch, or one that is not
    there. On the index documents_batch: O(log D + b) for the batch's b files, each
    with _COLUMNS' probes."""
    params = {"c": customer_id, "b": batch_id, "u": user_id, "limit": MAX_BATCH_ROWS}
    return conn.execute(text(_BATCH), params).mappings().all()


def batch_size(conn: Connection, customer_id: str, batch_id: str, *, user_id: str) -> int:
    """How many files ``user_id``'s batch holds, for the upload's cap (Q1). One count
    on the index documents_batch: O(log D + b)."""
    params = {"c": customer_id, "b": batch_id, "u": user_id}
    return int(conn.execute(text(_BATCH_SIZE), params).scalar_one())


def family_versions(conn: Connection, customer_id: str, family_id: str) -> Sequence[RowMapping]:
    """Every version of one family, the first version first, in Trash or not.
    One query on the index documents_family_version: O(log D + v) time and
    O(v) space for the family's v versions."""
    return conn.execute(text(_VERSIONS), {"c": customer_id, "f": family_id}).mappings().all()


# A PageSummary row: the page, how it was read (PRD 2 R1, R4) and its reading result
# (PRD 5 RT2, F1), "Keep as is" and a read again asked for (F5). PRD 5 U4: the values on
# the page the two readers still read differently, the count readers_disagree's sentence
# names (the index extracted_values_record).
_PAGE = (
    "SELECT page_no, width, height, unit, has_image, route, effective_dpi, low_resolution,"
    " read_by, read_quality, read_issues, unsure_words, illegible_words, unpinned_lines,"
    " accepted_by, accepted_at, read_again_requested_at,"
    " (SELECT count(*) FROM extracted_values ev WHERE ev.customer_id = pages.customer_id"
    " AND ev.document_id = pages.document_id AND ev.page_no = pages.page_no"
    " AND ev.label_reason = 'readers_disagree') AS values_disputed"
    " FROM pages"
)


def list_pages(conn: Connection, customer_id: str, document_id: str) -> Sequence[RowMapping]:
    return (
        conn.execute(
            text(_PAGE + " WHERE customer_id = :c AND document_id = :d ORDER BY page_no"),
            {"c": customer_id, "d": document_id},
        )
        .mappings()
        .all()
    )


def get_page(conn: Connection, customer_id: str, document_id: str, page_no: int) -> RowMapping | None:
    return (
        conn.execute(
            text(_PAGE + " WHERE customer_id = :c AND document_id = :d AND page_no = :n"),
            {"c": customer_id, "d": document_id, "n": page_no},
        )
        .mappings()
        .first()
    )


def reviewer_fix_count(conn: Connection, customer_id: str, document_id: str) -> int:
    """How many of this document's values a reviewer fixed (PRD 2 X1, said before
    "Read again"). A fix whose value a later read replaced has no value any more
    (``value_id`` NULL), and is not counted (AR15c). One query on the index
    corrections_document: O(log C + fixes)."""
    return int(
        conn.execute(
            text(
                "SELECT count(*) FROM corrections"
                " WHERE customer_id = :c AND document_id = :d AND value_id IS NOT NULL"
            ),
            {"c": customer_id, "d": document_id},
        ).scalar_one()
    )


def document_facts(conn: Connection, customer_id: str, document_id: str) -> RowMapping | None:
    """The detail's document card columns (PRD 2 D3): its date and card JSON. Kept out of
    ``_COLUMNS`` so the list stays small. One query by primary key."""
    return (
        conn.execute(
            text("SELECT document_date, card FROM documents WHERE customer_id = :c AND id = :d"),
            {"c": customer_id, "d": document_id},
        )
        .mappings()
        .one_or_none()
    )


_INVOICE = text(
    "SELECT id, invoice_number, invoice_date, seller_name, seller_gstin, buyer_name, buyer_gstin,"
    " subtotal, tax_total, total, currency, label, check_status, check_reasons"
    " FROM invoices WHERE customer_id = :c AND document_id = :d"
)
_INVOICE_LINES = text(
    "SELECT line_no, description, hsn, quantity, unit, rate, amount, label FROM invoice_lines"
    " WHERE customer_id = :c AND invoice_id = :i ORDER BY line_no"
)


def invoice_for(
    conn: Connection, customer_id: str, document_id: str
) -> tuple[RowMapping, Sequence[RowMapping]] | None:
    """The detail's invoice (PRD 2 E5): the document's invoices row and its lines in
    line_no order, or None. Two indexed queries (the unique document_id, then the unique
    (invoice_id, line_no)); O(lines)."""
    params = {"c": customer_id, "d": document_id}
    row = conn.execute(_INVOICE, params).mappings().one_or_none()
    if row is None:
        return None
    lines = conn.execute(_INVOICE_LINES, {**params, "i": row["id"]}).mappings().all()
    return row, lines


# PRD 3 Y2: the documents linked to this one, either way round (the pipeline stores
# both ways, so DISTINCT shows each partner and identifier once), and only a
# current, ready one out of Trash.
_LINKS = text(
    "SELECT DISTINCT d.id, d.display_name, d.doc_type, l.identifier_kind, l.identifier_value"
    " FROM document_links l JOIN documents d ON d.customer_id = l.customer_id"
    " AND d.id = CASE WHEN l.document_id = :d THEN l.linked_document_id ELSE l.document_id END"
    " WHERE l.customer_id = :c AND (l.document_id = :d OR l.linked_document_id = :d)"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " ORDER BY d.display_name, d.id, l.identifier_kind, l.identifier_value LIMIT :limit"
)
# ponytail: a cap, not paging; an identifier shared by more documents than this shows the first ones.
MAX_LINKS = 50


def document_links(conn: Connection, customer_id: str, document_id: str) -> Sequence[RowMapping]:
    """The detail's related documents (PRD 3 Y2), by name. One query on the unique key's
    document_id and the index document_links_linked: O(L log L) for the document's L links."""
    params = {"c": customer_id, "d": document_id, "limit": MAX_LINKS}
    return conn.execute(_LINKS, params).mappings().all()


def list_values(conn: Connection, customer_id: str, document_id: str) -> Sequence[RowMapping]:
    """Every value of one document, by page, record and field. O(v log v)."""
    return (
        conn.execute(
            text(
                "SELECT id, document_id, record_key, field, value_text, value_raw, second_reading,"
                " page_no, region, label, label_reason, confidence, label_page_no, label_region"
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


# Scanned text (2 Oct 2026): every line each reader read on one page, for the viewer
# only. This text goes to the screen and never to the model, so nothing under
# answering/ or search/ may import page_readings (tests/test_api_readings.py checks).
MAX_READING_LINES = 2000  # per reader and page
MAX_READING_TEXT = 100_000  # characters per reader and page
MAX_READING_CHARS = MAX_READING_TEXT  # one line, cut in SQL: never more than the whole page
READINGS_TIMEOUT_MS = 3000
_SET_READINGS_TIMEOUT = text("SELECT set_config('statement_timeout', :ms, true)")
# One row for a reader with no line on the page (the LEFT JOIN), else one per line in
# stored order (ORDINALITY). A reader's lines stop once the characters before a line
# pass the budget, so at most one line over it leaves the database; Python drops that
# one and says ``truncated`` (views.page_readings).
_PAGE_READINGS = text(
    "SELECT ro.role, ro.reader, ro.reader_version, l.ord, l.text, l.cut, l.confidence, l.origin,"
    " l.kind, l.region"
    " FROM reader_outputs ro"
    " LEFT JOIN LATERAL (SELECT * FROM (SELECT e.ord, left(e.span ->> 'raw', :max_chars) AS text,"
    " length(e.span ->> 'raw') > :max_chars AS cut,"
    " CAST(e.span ->> 'confidence' AS double precision) AS confidence,"
    " e.span ->> 'origin' AS origin, e.span ->> 'kind' AS kind, e.span -> 'region' AS region,"
    " sum(length(left(e.span ->> 'raw', :max_chars))) OVER (ORDER BY e.ord) AS upto"
    " FROM jsonb_array_elements(ro.output) WITH ORDINALITY AS e(span, ord)"
    " WHERE (e.span -> 'region' ->> 'page')::int = :page_no"
    " AND NOT (coalesce(e.span ->> 'origin', '') = 'ocr' AND e.span ->> 'kind' IS NULL"
    " AND coalesce(e.span ->> 'quality', '') IN ('unsure', 'illegible'))"
    " ORDER BY e.ord LIMIT :max_lines + 1) AS s"
    " WHERE s.upto - length(s.text) <= :max_text) AS l ON true"
    " WHERE ro.customer_id = :c AND ro.document_id = :d"
    " ORDER BY ro.role, l.ord"
)


def page_readings(conn: Connection, customer_id: str, document_id: str, page_no: int) -> Sequence[RowMapping]:
    """Every line the first and second reader read on one page, as read (``raw``),
    the first reader first, each in stored order; capped in SQL, under a local timeout
    (set_config(..., true) lasts until this transaction ends, as in search/hybrid.py).

    O(spans of the document), as ``sheet_cells``: the output is one jsonb array,
    unpacked in the database, and only this page's lines leave it.
    ponytail: a per-page index of the spans is the upgrade if big documents get slow.
    """
    conn.execute(_SET_READINGS_TIMEOUT, {"ms": str(READINGS_TIMEOUT_MS)})
    params = {
        "c": customer_id,
        "d": document_id,
        "page_no": page_no,
        "max_lines": MAX_READING_LINES,
        "max_chars": MAX_READING_CHARS,
        "max_text": MAX_READING_TEXT,
    }
    return conn.execute(_PAGE_READINGS, params).mappings().all()


# PRD 5 U3: the reading problems drawn on one page, from the stored output: each word
# mark (a span read by OCR with no kind and quality unsure or illegible, F3) and each
# line whose spot no found box confirmed (box_source "model", G5: "unpinned"), in stored
# order, at most MAX_READING_LINES, each text cut to MAX_READING_CHARS. For the viewer
# only, like page_readings.
_PAGE_MARKS = text(
    "SELECT CASE WHEN e.span ->> 'kind' IS NULL THEN e.span ->> 'quality' ELSE 'unpinned' END AS quality,"
    " e.span -> 'region' AS region, left(e.span ->> 'text', :max_chars) AS text"
    " FROM reader_outputs ro"
    " CROSS JOIN LATERAL jsonb_array_elements(ro.output) WITH ORDINALITY AS e(span, ord)"
    " WHERE ro.customer_id = :c AND ro.document_id = :d"
    " AND (e.span -> 'region' ->> 'page')::int = :page_no AND e.span ->> 'origin' = 'ocr'"
    " AND ((e.span ->> 'kind' IS NULL AND e.span ->> 'quality' IN ('unsure', 'illegible'))"
    " OR (e.span ->> 'kind' IS NOT NULL AND e.span ->> 'box_source' = 'model'))"
    " ORDER BY ro.role, e.ord LIMIT :max_marks"
)


def page_marks(conn: Connection, customer_id: str, document_id: str, page_no: int) -> Sequence[RowMapping]:
    """The page's reading marks (quality, region, text): unsure, illegible or unpinned.
    Call it after ``page_readings`` in the same transaction, so its timeout holds.
    O(spans of the document), as ``page_readings``."""
    params = {
        "c": customer_id,
        "d": document_id,
        "page_no": page_no,
        "max_chars": MAX_READING_CHARS,
        "max_marks": MAX_READING_LINES,
    }
    return conn.execute(_PAGE_MARKS, params).mappings().all()


def rename_document(conn: Connection, customer_id: str, document_id: str, *, name: str) -> None:
    """Give this one version a new display name, already cleaned by the caller, and
    put it in its pieces' context headers too, so search never shows the old name.
    O(log D + P) for the document's P pieces."""
    params = {"c": customer_id, "d": document_id}
    old = conn.execute(text(_NAME_OF), params).scalar_one_or_none()
    conn.execute(text(_RENAME), {**params, "name": name})
    if old is not None:
        headers = {"old": document_part(old), "new": document_part(name)}
        conn.execute(text(_RENAME_HEADERS), {**params, **headers})


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
