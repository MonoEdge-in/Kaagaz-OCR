"""The tables the pipeline writes: pages, reader outputs, pieces, values, the
document's card columns, its invoice with its lines, and its links.

``save`` keeps everything one run made for a document in one transaction as
that customer: it deletes what an earlier run saved for the document, then
inserts the new rows, one batched statement per table. So a redelivered job
replaces rather than appends, and a crash half way leaves the earlier run's
rows as they were. ``clear`` deletes them (a file rejected on a later scan).
``load_learned`` reads this customer's remembered corrections for some fields.

Every statement names the customer as well (row level security is the second
lock, docs/architecture.md). A row for another customer's document cannot be
written: the (customer_id, document_id) foreign key refuses it.

Lock order (plan 4.3), the same in ``save`` and ``clear``:

1. the family lock (``trash.lock_family``), which every writer of a documents
   row takes first; the card columns, the deletes and the inserts follow under it;
2. the customer's link lock (a transaction advisory lock), so two saves of one
   customer make their links one after the other, the second seeing the first's
   committed values;
3. FOR KEY SHARE, by id in one sorted order, on the link targets and on the
   documents already linked to this one, before any link row is touched: a delete
   forever or purge of an old partner (FOR UPDATE, then a cascade over its link
   rows) is waited for, never deadlocked with; a target deleted meanwhile is left
   out, and a locked one can't be deleted before this commits;
4. one DELETE of this document's links both ways (AR6), then the new links, one
   row each way, sorted, ON CONFLICT DO NOTHING.

The run fence: with ``run_id``, a save goes ahead only if that run is still
open (``runs.is_run_open``, read under the family lock);
otherwise it raises Superseded and writes nothing. A deadlock that PostgreSQL
breaks (40P01) runs the whole transaction again (AR7).

A document links to this customer's live documents of other families whose
card or document values hold one of its identifiers, whatever their state
(AR5, PROVISIONAL), so two documents read at once still link. PRD 3's reader of
the links filters both ends with LIVE_CURRENT. The ``LIVE`` fragment
(kaagaz.db.visibility) is written literally in the two statements that join
``documents d``.

A reader output is stored as a JSON array of spans, each
``{"text", "raw", "region", "confidence"}`` with ``region`` as in
kaagaz.reading.spans.location_to_json, plus ``"kind"``, ``"origin"``,
``"table_id"``, ``"row"``, ``"col"`` and ``"header"`` only when they are set
(PRD 2 P4). Outputs stored before PRD 2 have none of them and still load
(``span_from_json``). A table ``row`` is a top-level key, so it never mixes
with a cell's ``region.row``, which is all ``listing.sheet_cells`` reads.

Cost per save: one family lock, 1 update, 5 deletes, at most 6 batched inserts
and 3 to 7 small link statements; O(rows) data sent. The link lookup reads this
customer's values of the link fields, O(V) for V of them (plan 12, risk 12).
One indexed query for ``load_learned``.
"""

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict
from typing import Any

from sqlalchemy import Connection, text

from kaagaz.db.engine import Database, unavailable
from kaagaz.db.runs import is_run_open
from kaagaz.db.sqlstate import retry_on_deadlock
from kaagaz.db.trash import lock_family
from kaagaz.extraction.learned import LearnedCorrection
from kaagaz.extraction.values import CARD_RECORD, NEEDS_REVIEW, ExtractedValue
from kaagaz.pipeline import DocumentContent, DocumentFacts, InvoiceRow, LinkIdentifier, Superseded
from kaagaz.reading.spans import Span, location_from_json, location_to_json

_UPDATE_FACTS = text(
    "UPDATE documents SET doc_type = :doc_type, languages = CAST(:languages AS text[]),"
    " summary = :summary, document_date = :document_date, card = CAST(:card AS jsonb)"
    " WHERE customer_id = :c AND id = :d"
)
_DELETES = [
    text("DELETE FROM invoices WHERE customer_id = :c AND document_id = :d"),  # its lines go with it
    text("DELETE FROM extracted_values WHERE customer_id = :c AND document_id = :d"),
    text("DELETE FROM pieces WHERE customer_id = :c AND document_id = :d"),
    text("DELETE FROM reader_outputs WHERE customer_id = :c AND document_id = :d"),
    text("DELETE FROM pages WHERE customer_id = :c AND document_id = :d"),
]

_INSERT_PAGE = text(
    "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, has_image, route,"
    " effective_dpi, low_resolution) VALUES (:c, :d, :page_no, :width, :height, :unit, :has_image, :route,"
    " :effective_dpi, :low_resolution)"
)
_INSERT_OUTPUT = text(
    "INSERT INTO reader_outputs (customer_id, document_id, role, reader, reader_version, output)"
    " VALUES (:c, :d, :role, :reader, :version, CAST(:output AS jsonb))"
)
_INSERT_PIECE = text(
    "INSERT INTO pieces (customer_id, document_id, seq, page_from, page_to, text, regions, label,"
    " context_header, text_latin) VALUES (:c, :d, :seq, :page_from, :page_to, :text,"
    " CAST(:regions AS jsonb), :label, :context_header, :text_latin)"
)
_INSERT_VALUE = text(
    "INSERT INTO extracted_values (customer_id, document_id, record_key, field, value_text, value_raw,"
    " second_reading, value_number, value_date, page_no, region, reader, confidence, label, label_reason,"
    " label_page_no, label_region)"
    " VALUES (:c, :d, :record_key, :field, :value_text, :value_raw, :second_reading, :value_number,"
    " :value_date, :page_no, CAST(:region AS jsonb), :reader, :confidence, :label, :label_reason,"
    " :label_page_no, CAST(:label_region AS jsonb))"
)
_INSERT_INVOICE = text(
    "INSERT INTO invoices (customer_id, document_id, invoice_number, invoice_date, seller_name,"
    " seller_gstin, buyer_name, buyer_gstin, subtotal, tax_total, total, currency, label, check_status,"
    " check_reasons) VALUES (:c, :d, :invoice_number, :invoice_date, :seller_name, :seller_gstin,"
    " :buyer_name, :buyer_gstin, :subtotal, :tax_total, :total, :currency, :label, :check_status,"
    " CAST(:check_reasons AS text[])) RETURNING id"
)
_INSERT_LINE = text(
    "INSERT INTO invoice_lines (customer_id, invoice_id, document_id, line_no, description, hsn, quantity,"
    " unit, rate, amount, label) VALUES (:c, :invoice_id, :d, :line_no, :description, :hsn, :quantity,"
    " :unit, :rate, :amount, :label)"
)
_LEARNED = text(
    "SELECT field, raw_value, corrected_value FROM learned_corrections"
    " WHERE customer_id = :c AND field = ANY(CAST(:fields AS text[]))"
)

# --- links (D5) ---------------------------------------------------------------

# The key is built from the canonical id, so any spelling of it takes the same lock.
_LINK_LOCK = text(
    "SELECT pg_advisory_xact_lock(hashtextextended('kaagaz.document_links:' || CAST(:c AS uuid)::text, 0))"
)
_LINK_TARGETS = text(
    r"SELECT DISTINCT v.document_id, v.field, lower(regexp_replace(v.value_text, '\s', '', 'g'))"
    " FROM extracted_values v JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id"
    " WHERE v.customer_id = :c AND v.field = ANY(CAST(:kinds AS text[]))"
    " AND v.record_key IN ('card', 'document')"
    r" AND lower(regexp_replace(v.value_text, '\s', '', 'g')) = ANY(CAST(:values AS text[]))"
    " AND d.id <> :d AND d.family_id <> :f AND d.deleted_at IS NULL"
)
_LINK_PARTNERS = text(
    "SELECT linked_document_id FROM document_links WHERE customer_id = :c AND document_id = :d"
)
# Filters nothing on purpose: an old partner in Trash is locked too (see the lock order above).
_LOCK_LINKED = text(
    "SELECT d.id FROM documents d WHERE d.customer_id = :c AND d.id = ANY(CAST(:ids AS uuid[]))"
    " ORDER BY d.id FOR KEY SHARE"
)
_LIVE_TARGETS = text(
    "SELECT d.id FROM documents d WHERE d.customer_id = :c AND d.id = ANY(CAST(:ids AS uuid[]))"
    " AND d.id <> :d AND d.family_id <> :f AND d.deleted_at IS NULL"
)
_DELETE_LINKS = text(
    "DELETE FROM document_links WHERE customer_id = :c AND (document_id = :d OR linked_document_id = :d)"
)
_INSERT_LINK = text(
    "INSERT INTO document_links (customer_id, document_id, linked_document_id, identifier_kind,"
    " identifier_value) VALUES (:c, :from_id, :to_id, :kind, :value)"
    " ON CONFLICT (document_id, linked_document_id, identifier_kind, identifier_value) DO NOTHING"
)


def span_to_json(span: Span) -> dict[str, Any]:
    data: dict[str, Any] = {
        "text": span.text,
        "raw": span.raw,
        "region": location_to_json(span.location),
        "confidence": span.confidence,
    }
    optional = {
        "kind": span.kind,
        "origin": span.origin,
        "table_id": span.table_id,
        "row": span.row,
        "col": span.col,
        "header": span.header,
    }
    data.update({key: value for key, value in optional.items() if value is not None})
    return data


def span_from_json(data: Mapping[str, Any]) -> Span:
    """A stored span back as a Span; a key that is not there loads as None."""
    return Span(
        text=data["text"],
        raw=data["raw"],
        location=location_from_json(data["region"]),
        confidence=data["confidence"],
        kind=data.get("kind"),
        origin=data.get("origin"),
        table_id=data.get("table_id"),
        row=data.get("row"),
        col=data.get("col"),
        header=data.get("header"),
    )


class PgPipelineStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, content: DocumentContent, *, run_id: str | None = None) -> None:
        """Replace everything saved for the document with ``content``, in one transaction.
        With ``run_id``, raise Superseded and write nothing unless that run is still
        open (the run fence)."""
        customer_id, document_id = content.customer_id, content.document_id
        ids = {"c": customer_id, "d": document_id}
        pages = [
            {
                **ids,
                "page_no": page.info.page,
                "width": page.info.width,
                "height": page.info.height,
                "unit": page.info.unit,
                "has_image": page.has_image,
                "route": page.route,
                "effective_dpi": page.effective_dpi,
                "low_resolution": page.low_resolution,
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
                "context_header": piece.context_header,
                "text_latin": piece.text_latin,
            }
            for seq, piece in enumerate(content.pieces)
        ]
        values = [_value_row(ids, value, content.reader) for value in content.values]

        def transaction() -> None:
            with self._db.customer(customer_id) as conn:
                family_id = lock_family(conn, customer_id, document_id)
                if run_id is not None and not is_run_open(conn, customer_id, document_id, run_id):
                    raise Superseded(run_id)
                _replace_facts_and_delete(conn, ids, content.facts)
                for statement, rows in (
                    (_INSERT_PAGE, pages),
                    (_INSERT_OUTPUT, outputs),
                    (_INSERT_PIECE, pieces),
                    (_INSERT_VALUE, values),
                ):
                    if rows:
                        conn.execute(statement, rows)  # one batched statement per table
                if content.invoice is not None:
                    _insert_invoice(conn, ids, content.invoice)
                _replace_links(conn, ids, family_id, content.links)

        with unavailable():
            retry_on_deadlock(transaction, customer_id=customer_id, document_id=document_id)

    def clear(self, customer_id: str, document_id: str) -> None:
        """Remove everything saved for this document: its rows, card columns, invoice and links."""
        ids = {"c": customer_id, "d": document_id}

        def transaction() -> None:
            with self._db.customer(customer_id) as conn:
                lock_family(conn, customer_id, document_id)
                _replace_facts_and_delete(conn, ids, DocumentFacts())
                _replace_links(conn, ids, None, ())

        with unavailable():
            retry_on_deadlock(transaction, customer_id=customer_id, document_id=document_id)

    def load_learned(self, customer_id: str, fields: Collection[str]) -> list[LearnedCorrection]:
        if not fields:
            return []
        with unavailable(), self._db.customer(customer_id) as conn:
            rows = conn.execute(_LEARNED, {"c": customer_id, "fields": sorted(fields)}).all()
        return [LearnedCorrection(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def _replace_facts_and_delete(conn: Connection, ids: dict[str, str], facts: DocumentFacts) -> None:
    """Write the card columns (always, so no older card stays), then delete the rows
    an earlier run saved. 6 statements."""
    conn.execute(
        _UPDATE_FACTS,
        {
            **ids,
            "doc_type": facts.doc_type,
            "languages": list(facts.languages),
            "summary": facts.summary,
            "document_date": facts.document_date,
            "card": None if facts.card is None else json.dumps(facts.card),
        },
    )
    for statement in _DELETES:
        conn.execute(statement, ids)


def _insert_invoice(conn: Connection, ids: dict[str, str], invoice: InvoiceRow) -> None:
    """The invoice row, then its lines in one batched statement. 2 statements."""
    row = asdict(invoice)
    del row["lines"]
    row["check_reasons"] = list(invoice.check_reasons)
    invoice_id = str(conn.execute(_INSERT_INVOICE, {**ids, **row}).scalar_one())
    lines = [{**ids, **asdict(line), "invoice_id": invoice_id} for line in invoice.lines]
    if lines:
        conn.execute(_INSERT_LINE, lines)


def _replace_links(
    conn: Connection, ids: dict[str, str], family_id: str | None, identifiers: Sequence[LinkIdentifier]
) -> None:
    """Replace this document's links, both ways, under the customer's link lock.
    Every document a link goes to, or went to, is locked first (the lock order
    above). No family (no such document for this customer) finds no targets."""
    conn.execute(_LINK_LOCK, ids)
    targets = _link_targets(conn, ids, family_id, identifiers) if family_id and identifiers else []
    partners: Sequence[object] = conn.execute(_LINK_PARTNERS, ids).scalars().all()
    linked = sorted({target for target, _, _ in targets} | {str(partner) for partner in partners})
    if linked:
        conn.execute(_LOCK_LINKED, {**ids, "ids": linked})
    if targets:  # still live once locked
        params = {**ids, "f": family_id, "ids": sorted({target for target, _, _ in targets})}
        live: Sequence[object] = conn.execute(_LIVE_TARGETS, params).scalars().all()
        targets = [match for match in targets if match[0] in {str(target) for target in live}]
    conn.execute(_DELETE_LINKS, ids)
    document_id = ids["d"]
    rows = sorted(
        {(document_id, target, kind, value) for target, kind, value in targets}
        | {(target, document_id, kind, value) for target, kind, value in targets}
    )
    if rows:
        conn.execute(
            _INSERT_LINK,
            [
                {"c": ids["c"], "from_id": a, "to_id": b, "kind": kind, "value": value}
                for a, b, kind, value in rows
            ],
        )


def _link_targets(
    conn: Connection, ids: dict[str, str], family_id: str, identifiers: Sequence[LinkIdentifier]
) -> list[tuple[str, str, str]]:
    """(target document, kind, value) for each of this customer's live documents of
    another family with a value equal to one of ``identifiers``. O(V) for the V
    values of the link fields."""
    wanted = {(item.kind, item.value) for item in identifiers}
    lookup = {"kinds": sorted({kind for kind, _ in wanted}), "values": sorted({value for _, value in wanted})}
    return [
        (str(target), str(kind), str(value))
        for target, kind, value in conn.execute(_LINK_TARGETS, {**ids, "f": family_id, **lookup})
        if (kind, value) in wanted
    ]


def _value_row(ids: dict[str, str], value: ExtractedValue, reader: str) -> dict[str, Any]:
    if value.record_key == CARD_RECORD and value.label != NEEDS_REVIEW:
        # The last check (council fix 4): the model chose a card value's field, so it is
        # never verified. A bug upstream stops the save before anything is written.
        raise ValueError("a card value must be needs_review")
    label = value.label_location
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
        "label_page_no": None if label is None else label.page,
        "label_region": None if label is None else json.dumps(location_to_json(label)),
    }
