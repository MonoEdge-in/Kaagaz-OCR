"""The review queue: values labelled needs review, and a reviewer's correction.

Why here: the SQL for Sprint 3 corrections in one place. Every function takes
a connection from ``Database.customer(customer_id)`` and filters on
``customer_id`` too. The API runs one resolve (lock, update, correction row,
remembered correction, audit) inside a single transaction.

Learned corrections belong to one customer (CLAUDE.md "Nothing crosses
customers"): the unique key is (customer_id, field, raw_value).

A value is listed with its page. Values whose page row is missing are not
listed; the pipeline always stores the pages of a document it saves values for.

Only values of each family's current, ready version, out of Trash, are listed
or counted in the review queue. A reviewer may also resolve a value directly
from an older ready version in its document viewer; those values do not reach
search or chat while that version is not current. A version still being read
has nothing final to review.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, RowMapping, text

from kaagaz.db import audit, value_keys
from kaagaz.extraction.land import value_number
from kaagaz.extraction.parsing import parse_date

# One value with its document and page. On its own, by value id (``get_item``),
# it has no Trash filter (plan 9.7): it reads back a value that ``lock_value``
# has just locked, and that lock is filtered.
_ITEM = (
    "SELECT v.id, v.document_id, v.record_key, v.field, v.value_text, v.value_raw,"
    " v.second_reading, v.page_no, v.region, v.label, v.label_reason, v.confidence,"
    " v.label_page_no, v.label_region, v.votes,"
    " d.display_name AS document_name, d.detected_type,"
    " p.width, p.height, p.unit, p.has_image, p.route, p.effective_dpi, p.low_resolution,"
    " p.read_by, p.read_quality, p.read_issues, p.unsure_words, p.illegible_words, p.unpinned_lines,"
    " p.accepted_by, p.accepted_at, p.read_again_requested_at,"
    " coalesce(i.check_reasons, '{}') AS check_reasons"
    " FROM extracted_values v"
    " JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id"
    " JOIN pages p ON p.customer_id = v.customer_id AND p.document_id = v.document_id"
    " AND p.page_no = v.page_no"
    " LEFT JOIN invoices i ON i.customer_id = v.customer_id AND i.document_id = v.document_id"
    " WHERE v.customer_id = :c"
)

# Every label_reason the extracted_values CHECK allows (migrations 0004, 0021 and 0081): the
# reasons bound when the queue is not filtered, so the reason filter is never a catch-all.
EVERY_REASON = (
    "direct_cell",
    "readers_agree",
    "readers_disagree",
    "single_reader",
    "low_confidence",
    "corrected",
    "learned_correction",
    "check_failed",
    "model_chosen",
    "ocr_script",
    "unsure_text",
    "box_unanchored",
    "local_reader",
    "mixed_digits",
    "amount_commas",
    "amount_mark",
    "amount_outlier",
)

# One definition of a value still waiting to be verified (QA #366): labelled needs review,
# shown as Ready to verify; every other value is Verified. The queue lists these (of current,
# ready documents out of Trash), and the file list and the upload tray count them
# (db/listing.py's values_to_review), so a file never says "All values verified" while one
# of its values still waits. Alias ``v``.
WAITING_VALUE = "v.label = 'needs_review'"

# The queue and its total repeat the same filter in full, so each can be checked on its own.
# Two fixed statements each, for the whole queue and for one document, never one with
# "(:doc IS NULL OR ...)": a prepared statement's generic plan could not use the
# document id then. The count keeps the pages join, so it counts what the queue lists.
_QUEUE = _ITEM + (
    f" AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready' AND {WAITING_VALUE}"
    " AND v.label_reason = ANY(CAST(:reasons AS text[]))"
    " ORDER BY v.created_at, v.id LIMIT :limit OFFSET :offset"
)

_QUEUE_FOR_DOCUMENT = _ITEM + (
    f" AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready' AND {WAITING_VALUE}"
    " AND v.document_id = CAST(:doc AS uuid)"
    " AND v.label_reason = ANY(CAST(:reasons AS text[]))"
    " ORDER BY v.created_at, v.id LIMIT :limit OFFSET :offset"
)

_COUNT = (
    "SELECT count(*) FROM extracted_values v"  # noqa: S608 - a fixed fragment, WAITING_VALUE
    " JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id"
    " JOIN pages p ON p.customer_id = v.customer_id AND p.document_id = v.document_id"
    " AND p.page_no = v.page_no"
    " WHERE v.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    f" AND {WAITING_VALUE}"
    " AND v.label_reason = ANY(CAST(:reasons AS text[]))"
)

_COUNT_FOR_DOCUMENT = (
    "SELECT count(*) FROM extracted_values v"  # noqa: S608 - a fixed fragment, WAITING_VALUE
    " JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id"
    " JOIN pages p ON p.customer_id = v.customer_id AND p.document_id = v.document_id"
    " AND p.page_no = v.page_no"
    " WHERE v.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    f" AND {WAITING_VALUE} AND v.document_id = CAST(:doc AS uuid)"
    " AND v.label_reason = ANY(CAST(:reasons AS text[]))"
)

# Verify all: the next chunk of the same queue (the count's filter: current, ready, out of
# Trash, with its page), after the last value seen, by (created_at, id), and no newer than
# when Verify all began. Locked like _LOCK_VALUE (OF v): a value another reviewer is
# resolving is waited for, then read again, and left out once it is no longer needs review.
_NEXT_CHUNK = (
    "SELECT v.id, v.created_at, v.value_text FROM extracted_values v"  # noqa: S608 - a fixed fragment, WAITING_VALUE
    " JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id"
    " JOIN pages p ON p.customer_id = v.customer_id AND p.document_id = v.document_id"
    " AND p.page_no = v.page_no"
    " WHERE v.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    f" AND {WAITING_VALUE}"
    " AND v.label_reason = ANY(CAST(:reasons AS text[]))"
    " AND v.created_at <= :until AND (v.created_at, v.id) > (:after_at, CAST(:after_id AS uuid))"
)
_CHUNK_ORDER = " ORDER BY v.created_at, v.id LIMIT :limit FOR UPDATE OF v"
_NEXT_CHUNK_ALL = _NEXT_CHUNK + _CHUNK_ORDER
_NEXT_CHUNK_FOR_DOCUMENT = _NEXT_CHUNK + " AND v.document_id = CAST(:doc AS uuid)" + _CHUNK_ORDER

# Only the value's row is locked (OF v), never the document's, so a resolve never
# waits on the family lock that trash and the pipeline take.
_LOCK_VALUE = (
    "SELECT v.id, v.document_id, v.field, v.value_text, v.value_raw, v.second_reading, v.label"
    " FROM extracted_values v"
    " JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id"
    " WHERE v.customer_id = :c AND v.id = :v AND d.deleted_at IS NULL AND d.status = 'ready'"
    " FOR UPDATE OF v"
)


def list_queue(
    conn: Connection,
    customer_id: str,
    *,
    limit: int,
    offset: int,
    document_id: str | None,
    reasons: Sequence[str] | None = None,
) -> tuple[Sequence[RowMapping], int]:
    """Needs-review values of documents not in Trash, oldest first, and their total;
    with ``reasons``, only values labelled for one of them (PRD 3 Y5), else every
    reason. Each carries its document's invoice check reasons. Uses the partial index
    ``extracted_values_review``, then each value's document and invoice by key:
    O(limit + offset) rows read for the page, and O(n) for the total over the
    customer's n needs-review values.
    ponytail: OFFSET paging, as the screen's "Show more" asks; keyset on (created_at, id) if queues grow."""
    params = {
        "c": customer_id,
        "doc": document_id,
        "reasons": list(EVERY_REASON if reasons is None else reasons),
        "limit": limit,
        "offset": offset,
    }
    queue = _QUEUE if document_id is None else _QUEUE_FOR_DOCUMENT
    rows = conn.execute(text(queue), params).mappings().all()
    return rows, count_queue(conn, customer_id, document_id=document_id, reasons=reasons)


def count_queue(
    conn: Connection, customer_id: str, *, document_id: str | None, reasons: Sequence[str] | None
) -> int:
    """How many values ``list_queue`` lists in all with these filters. O(n) over the
    customer's n needs-review values."""
    params = {
        "c": customer_id,
        "doc": document_id,
        "reasons": list(EVERY_REASON if reasons is None else reasons),
    }
    count = _COUNT if document_id is None else _COUNT_FOR_DOCUMENT
    total: int = conn.execute(text(count), params).scalar_one()
    return int(total)


def database_now(conn: Connection) -> datetime:
    """The database's clock, which stamps each value's created_at."""
    now: datetime = conn.execute(text("SELECT now()")).scalar_one()
    return now


def next_chunk(
    conn: Connection,
    customer_id: str,
    *,
    document_id: str | None,
    reasons: Sequence[str] | None,
    until: datetime,
    after: tuple[datetime, str] | None,
    limit: int,
) -> Sequence[RowMapping]:
    """Verify all: up to ``limit`` values of the queue as ``list_queue`` filters it, oldest
    first, created no later than ``until`` and after ``after`` (the last one's created_at
    and id), each locked until the transaction ends. Its id, created_at and value_text."""
    after_at, after_id = after or (datetime.min.replace(tzinfo=UTC), "00000000-0000-0000-0000-000000000000")
    params = {
        "c": customer_id,
        "doc": document_id,
        "reasons": list(EVERY_REASON if reasons is None else reasons),
        "until": until,
        "after_at": after_at,
        "after_id": after_id,
        "limit": limit,
    }
    statement = _NEXT_CHUNK_ALL if document_id is None else _NEXT_CHUNK_FOR_DOCUMENT
    return conn.execute(text(statement), params).mappings().all()


def get_item(conn: Connection, customer_id: str, value_id: str) -> RowMapping | None:
    return conn.execute(text(_ITEM + " AND v.id = :v"), {"c": customer_id, "v": value_id}).mappings().first()


def lock_value(conn: Connection, customer_id: str, value_id: str) -> RowMapping | None:
    """The value, locked until the transaction ends, so two reviewers cannot
    resolve it at the same moment. The label is read under the lock: the second
    reviewer sees the first one's ``verified`` and is refused. None if there is no
    such value, or its document is in Trash. O(1), by primary key."""
    return conn.execute(text(_LOCK_VALUE), {"c": customer_id, "v": value_id}).mappings().first()


def apply_correction(
    conn: Connection,
    customer_id: str,
    value_id: str,
    *,
    value_text: str,
    value_number: Decimal | None,
    value_date: date | None,
) -> None:
    conn.execute(
        text(
            "UPDATE extracted_values SET value_text = :t, value_number = :n, value_date = :dt,"
            " label = 'verified', label_reason = 'corrected'"
            " WHERE customer_id = :c AND id = :v"
        ),
        {"c": customer_id, "v": value_id, "t": value_text, "n": value_number, "dt": value_date},
    )


def add_correction(
    conn: Connection,
    customer_id: str,
    value_id: str,
    *,
    old_value: str,
    new_value: str,
    choice: str,
    user_id: str,
) -> None:
    conn.execute(
        text(
            "INSERT INTO corrections"
            " (customer_id, document_id, field, value_id, old_value, new_value, choice, corrected_by)"
            " SELECT customer_id, document_id, field, id, :old, :new, :choice, :u"
            " FROM extracted_values WHERE customer_id = :c AND id = :v"
        ),
        {"c": customer_id, "v": value_id, "old": old_value, "new": new_value, "choice": choice, "u": user_id},
    )


def remember_correction(
    conn: Connection, customer_id: str, *, field: str, raw_value: str, corrected_value: str, user_id: str
) -> None:
    """Upsert: the latest correction for (field, raw value) wins, for this customer only."""
    conn.execute(
        text(
            "INSERT INTO learned_corrections (customer_id, field, raw_value, corrected_value, created_by)"
            " VALUES (:c, :f, :raw, :new, :u)"
            " ON CONFLICT (customer_id, field, raw_value)"
            " DO UPDATE SET corrected_value = EXCLUDED.corrected_value, created_by = EXCLUDED.created_by"
        ),
        {"c": customer_id, "f": field, "raw": raw_value, "new": corrected_value, "u": user_id},
    )


def resolve_value(
    conn: Connection,
    customer_id: str,
    current: Mapping[Any, Any],
    *,
    new_text: str,
    choice: str,
    remember: bool,
    user_id: str,
    details: Mapping[str, audit.DetailValue] | None = None,
) -> None:
    """Verify one needs-review value, already locked by the caller, as ``new_text``: the
    value changed (its number and date parsed again by the pipeline's rules), its
    correction row, the remembered correction when asked, its company vocabulary key written
    again (a land value's, ``value_keys``), and one ``review.resolve`` audit row, all in the
    caller's transaction. ``current`` has the value's id, document_id,
    field, value_text and value_raw as they were before. The correction screen and word
    review (kaagaz/db/word_review.py) both verify a value only through here."""
    value_id = str(current["id"])
    apply_correction(
        conn,
        customer_id,
        value_id,
        value_text=new_text,
        value_number=value_number(str(current["field"]), new_text),
        value_date=parse_date(new_text),
    )
    value_keys.key_values(conn, customer_id, [value_id])
    add_correction(
        conn,
        customer_id,
        value_id,
        old_value=str(current["value_text"]),
        new_value=new_text,
        choice=choice,
        user_id=user_id,
    )
    if remember:
        remember_correction(
            conn,
            customer_id,
            field=str(current["field"]),
            raw_value=str(current["value_raw"]),
            corrected_value=new_text,
            user_id=user_id,
        )
    audit.record(
        conn,
        customer_id,
        actor_user_id=user_id,
        action="review.resolve",
        target_type="extracted_value",
        target_id=value_id,
        details={
            "choice": choice,
            "remember": remember,
            "document_id": str(current["document_id"]),
            **(details or {}),
        },
    )
