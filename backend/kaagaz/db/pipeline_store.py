"""The tables the pipeline writes: pages, reader outputs, pieces and values.

``save`` keeps everything one run made for a document in one transaction as
that customer: it deletes what an earlier run saved for the document, then
inserts the new rows, one batched statement per table. So a redelivered job
replaces rather than appends, and a crash half way leaves the earlier run's
rows as they were. ``clear`` deletes them (a file rejected on a later scan).
``load_learned`` reads this customer's remembered corrections for some fields.

Every statement names the customer as well (row level security is the second
lock, docs/architecture.md). A row for another customer's document cannot be
written: the (customer_id, document_id) foreign key refuses it.

A reader output is stored as a JSON array of spans, each
``{"text", "raw", "region", "confidence"}`` with ``region`` as in
kaagaz.reading.spans.location_to_json.

Cost: 4 deletes and at most 4 batched inserts per save, O(rows) data sent;
one indexed query for ``load_learned``.
"""

import json
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError

from kaagaz.db.engine import Database
from kaagaz.extraction.learned import LearnedCorrection
from kaagaz.extraction.values import ExtractedValue
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.pipeline import DocumentContent
from kaagaz.reading.spans import Span, location_to_json

_DELETES = [
    text("DELETE FROM extracted_values WHERE customer_id = :c AND document_id = :d"),
    text("DELETE FROM pieces WHERE customer_id = :c AND document_id = :d"),
    text("DELETE FROM reader_outputs WHERE customer_id = :c AND document_id = :d"),
    text("DELETE FROM pages WHERE customer_id = :c AND document_id = :d"),
]

_INSERT_PAGE = text(
    "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, has_image)"
    " VALUES (:c, :d, :page_no, :width, :height, :unit, :has_image)"
)
_INSERT_OUTPUT = text(
    "INSERT INTO reader_outputs (customer_id, document_id, role, reader, reader_version, output)"
    " VALUES (:c, :d, :role, :reader, :version, CAST(:output AS jsonb))"
)
_INSERT_PIECE = text(
    "INSERT INTO pieces (customer_id, document_id, seq, page_from, page_to, text, regions, label)"
    " VALUES (:c, :d, :seq, :page_from, :page_to, :text, CAST(:regions AS jsonb), :label)"
)
_INSERT_VALUE = text(
    "INSERT INTO extracted_values (customer_id, document_id, record_key, field, value_text, value_raw,"
    " second_reading, value_number, value_date, page_no, region, reader, confidence, label, label_reason)"
    " VALUES (:c, :d, :record_key, :field, :value_text, :value_raw, :second_reading, :value_number,"
    " :value_date, :page_no, CAST(:region AS jsonb), :reader, :confidence, :label, :label_reason)"
)
_LEARNED = text(
    "SELECT field, raw_value, corrected_value FROM learned_corrections"
    " WHERE customer_id = :c AND field = ANY(CAST(:fields AS text[]))"
)


def span_to_json(span: Span) -> dict[str, Any]:
    return {
        "text": span.text,
        "raw": span.raw,
        "region": location_to_json(span.location),
        "confidence": span.confidence,
    }


class PgPipelineStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, content: DocumentContent) -> None:
        """Replace everything saved for the document with ``content``, in one transaction."""
        ids = {"c": content.customer_id, "d": content.document_id}
        pages = [
            {
                **ids,
                "page_no": page.info.page,
                "width": page.info.width,
                "height": page.info.height,
                "unit": page.info.unit,
                "has_image": page.has_image,
            }
            for page in content.pages
        ]
        outputs = [
            {
                **ids,
                "role": output.role,
                "reader": output.reader,
                "version": output.version,
                "output": json.dumps([span_to_json(span) for span in output.spans]),
            }
            for output in content.outputs
        ]
        pieces = [
            {
                **ids,
                "seq": seq,
                "page_from": piece.page_from,
                "page_to": piece.page_to,
                "text": piece.text,
                "regions": json.dumps(piece.regions()),
                "label": piece.label,
            }
            for seq, piece in enumerate(content.pieces)
        ]
        values = [_value_row(ids, value, content.reader) for value in content.values]
        with _unavailable(), self._db.customer(content.customer_id) as conn:
            _delete_all(conn, ids)
            for statement, rows in (
                (_INSERT_PAGE, pages),
                (_INSERT_OUTPUT, outputs),
                (_INSERT_PIECE, pieces),
                (_INSERT_VALUE, values),
            ):
                if rows:
                    conn.execute(statement, rows)  # one batched statement per table

    def clear(self, customer_id: str, document_id: str) -> None:
        with _unavailable(), self._db.customer(customer_id) as conn:
            _delete_all(conn, {"c": customer_id, "d": document_id})

    def load_learned(self, customer_id: str, fields: Collection[str]) -> list[LearnedCorrection]:
        if not fields:
            return []
        with _unavailable(), self._db.customer(customer_id) as conn:
            rows = conn.execute(_LEARNED, {"c": customer_id, "fields": sorted(fields)}).all()
        return [LearnedCorrection(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def _delete_all(conn: Connection, ids: dict[str, str]) -> None:
    for statement in _DELETES:
        conn.execute(statement, ids)


def _value_row(ids: dict[str, str], value: ExtractedValue, reader: str) -> dict[str, Any]:
    return {
        **ids,
        "record_key": value.record_key,
        "field": value.field,
        "value_text": value.value_text,
        "value_raw": value.value_raw,
        "second_reading": value.second_reading,
        "value_number": value.value_number,
        "value_date": value.value_date,
        "page_no": value.location.page,
        "region": json.dumps(location_to_json(value.location)),
        "reader": reader,
        "confidence": value.confidence,
        "label": value.label,
        "label_reason": value.label_reason,
    }


@contextmanager
def _unavailable() -> Iterator[None]:
    """Turn "cannot reach the database" into BackendUnavailable (the job is retried).
    Constraint errors stay what they are: they are bugs, not outages."""
    try:
        yield
    except OperationalError:
        raise BackendUnavailable("database") from None
