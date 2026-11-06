"""The land facts re-read (PRD 6 package D3, #245): ``python -m kaagaz.cli land-facts``.

A facts-only pass over stored reader output, started by the operator, never Read again (which
sets a document back to received and keeps only remembered fixes).

* **Eligible** (``eligible``): current, ready, out of Trash, a ``land_record`` (or a contract
  whose card holds a survey or khata number), with no ``document.land_facts`` audit row for
  ``LAND_PROMPT_HASH`` and not read with it on upload (its latest run ready, with no error
  and that ``land_prompt`` in its config).
* ``count``: per customer, the eligible documents and their first reader's characters; no
  model call. ``keys_only``: each document's land values keyed (``value_keys``); no model.
* ``reread``, one document at a time, each in its customer's ``Database.customer``: the stored
  reader output (no OCR) and the latest run's id, read in one short transaction; the land
  windows (``land.read_land``) through the shared model client, so the shared RPM scheduler,
  billed as ``land`` to the customer with no user; the RapidOCR checker on each located box
  of a page a model read, from the stored file (its sha256 checked); ``label_quoted``, the
  reading marks and the remembered corrections, as the pipeline labels them. Then one
  transaction under the family lock: the document must still be ready, current, out of
  Trash, with the same latest run (else ``changed``) and no land value a person corrected
  (else ``corrected``: kept, never overwritten); its ``land:%`` values are replaced (their
  keys go with them), the card owner values the land values repeat are deleted while still
  Needs review, the word changes in force are written again into the new values only,
  the keys written and one ``document.land_facts`` audit row with the prompt hash (and
  ``truncated`` when text past the last window went unread, ``land.read_land``). The
  document stays Ready; a crash leaves the earlier state; a replay finds it done.
* **The cap** (``max_usd``): a document is never started when what was spent plus the
  costliest document so far would pass it; a call whose cost OpenRouter did not report
  stops the run (the cap cannot be kept). A model that is down stops it too: run it again to go
  on. A reply that is unusable (``ModelOutputError``: still cut after its halves, not the
  schema), which asking again would only repeat, gets a ``document.land_facts`` row with the
  prompt hash and ``failed``, its code, under the same fence: nothing else is written, the
  document is no longer eligible, and the run goes on with the next one.

Output: ids, counts and dollars only, never document text.
"""

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TextIO

from sqlalchemy import Connection, text

from kaagaz.ai.usage import usage_scope
from kaagaz.db import audit, value_keys, word_review
from kaagaz.db import customers as customers_sql
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore, _insert_values, span_from_json
from kaagaz.db.trash import lock_family
from kaagaz.extraction.invoice import WindowSettings
from kaagaz.extraction.labels import at_line, label_quoted, label_reading_marks, spot_unsure
from kaagaz.extraction.land import LAND_PROMPT_HASH, read_land, without_owners
from kaagaz.extraction.learned import apply_learned
from kaagaz.extraction.quotes import reader_text
from kaagaz.extraction.values import ExtractedValue
from kaagaz.pipeline import _DEVANAGARI, HANDED_ON, _check_crop, _line_marks, _points_at, _with_parsed
from kaagaz.preparing.pages import page_picture
from kaagaz.reading.ocr import LOCAL_READ_BY, is_word_mark
from kaagaz.reading.office import drawn_from
from kaagaz.reading.spans import BoxLocation, Location, Span, location_from_json

if TYPE_CHECKING:
    from kaagaz.extraction.card import ChatModel
    from kaagaz.ingestion.ports import RenditionStorage
    from kaagaz.pipeline import Checker
    from kaagaz.settings import Settings

ACTION = "document.land_facts"
_NOT_A_MODEL = frozenset({LOCAL_READ_BY, "text_layer", "cells"})  # pages.read_by of no model
_ELIGIBLE = text(
    "SELECT d.id::text FROM documents d WHERE d.customer_id = :c AND d.deleted_at IS NULL AND d.is_current"
    " AND d.status = 'ready' AND (d.doc_type = 'land_record' OR (d.doc_type = 'contract' AND EXISTS ("
    "SELECT 1 FROM extracted_values v WHERE v.customer_id = :c AND v.document_id = d.id"
    " AND v.record_key = 'card' AND v.field IN ('survey_number', 'khata_number'))))"
    " AND NOT EXISTS (SELECT 1 FROM audit_log a WHERE a.customer_id = :c AND a.action = 'document.land_facts'"
    " AND a.target_id = d.id::text AND a.details ->> 'prompt' = :hash)"
    " AND NOT EXISTS (SELECT 1 FROM processing_runs r WHERE r.customer_id = :c AND r.document_id = d.id"
    " AND r.status = 'ready' AND r.error_code IS NULL AND r.config ->> 'land_prompt' = :hash"
    " AND r.started_at = (SELECT max(x.started_at) FROM processing_runs x"
    " WHERE x.customer_id = :c AND x.document_id = d.id))"
    " ORDER BY d.created_at, d.id"
)
_CHARS = text(
    "SELECT coalesce(sum(length(s ->> 'text')), 0) FROM reader_outputs o"
    " CROSS JOIN LATERAL jsonb_array_elements(o.output) s"
    " WHERE o.customer_id = :c AND o.role = 'first' AND o.document_id = ANY(CAST(:ids AS uuid[]))"
)
_DOCUMENT = text(
    "SELECT d.detected_type, d.sha256, (SELECT r.id::text FROM processing_runs r WHERE r.customer_id = :c"
    " AND r.document_id = d.id ORDER BY r.started_at DESC LIMIT 1) AS run_id FROM documents d"
    " WHERE d.customer_id = :c AND d.id = :d AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
)
_OUTPUTS = text("SELECT role, reader, output FROM reader_outputs WHERE customer_id = :c AND document_id = :d")
_PAGES = text(
    "SELECT page_no, route, read_by, read_quality, read_issues FROM pages"
    " WHERE customer_id = :c AND document_id = :d"
)
_VALUES = text(
    "SELECT id::text, record_key, field, value_text, label, label_reason, region FROM extracted_values"
    " WHERE customer_id = :c AND document_id = :d"
)
_WITH_LAND = text(
    "SELECT DISTINCT document_id::text FROM extracted_values"
    " WHERE customer_id = :c AND record_key LIKE 'land:%'"
)
_DELETE_LAND = text(
    "DELETE FROM extracted_values WHERE customer_id = :c AND document_id = :d AND record_key LIKE 'land:%'"
)
_DELETE_OWNERS = text(
    "DELETE FROM extracted_values WHERE customer_id = :c AND document_id = :d"
    " AND id = ANY(CAST(:ids AS uuid[])) AND label = 'needs_review'"
)
_LAND_IDS = text(
    "SELECT id::text FROM extracted_values"
    " WHERE customer_id = :c AND document_id = :d AND record_key LIKE 'land:%'"
)


class Stop(Exception):
    """The run stops here, with a short reason (ids and codes only)."""


@dataclass(frozen=True, slots=True)
class _Stored:
    """One document's stored reading, as the re-read needs it."""

    run_id: str | None
    detected_type: str
    sha256: str
    reader: str
    first: list[Span]
    second: list[Span] | None
    model_pages: frozenset[int]  # pages a reading model read: the checker's
    local_pages: frozenset[int]  # pages handed to the local reader (HANDED_ON)
    unreadable: frozenset[int]  # pages read unreadable: never sent (as the card is not, 16 Oct 2026)
    others: list[ExtractedValue]  # its values that are not land values, for the shared boxes
    corrected: bool  # a person corrected one of its land values


def eligible(conn: Connection, customer_id: str) -> list[str]:
    """This customer's documents to re-read, oldest first (the module docstring)."""
    return [str(row[0]) for row in conn.execute(_ELIGIBLE, {"c": customer_id, "hash": LAND_PROMPT_HASH})]


def count(db: Database, customers: Sequence[str], out: TextIO) -> None:
    """Per customer: its eligible documents and their characters. No model call."""
    for customer in customers:
        with db.customer(customer) as conn:
            ids = eligible(conn, customer)
            chars = int(conn.execute(_CHARS, {"c": customer, "ids": ids}).scalar_one())
        out.write(f"customer {customer}: {len(ids)} eligible documents, {chars} characters\n")


def keys_only(db: Database, customers: Sequence[str], out: TextIO) -> None:
    """Per customer: each document's land values keyed, under its family lock. No model."""
    for customer in customers:
        with db.customer(customer) as conn:
            documents = [str(row[0]) for row in conn.execute(_WITH_LAND, {"c": customer})]
        keyed = 0
        for document in documents:
            with db.customer(customer) as conn:
                lock_family(conn, customer, document)
                keyed += value_keys.key_document(conn, customer, document)
        out.write(f"customer {customer}: {len(documents)} documents, {keyed} keys\n")


@dataclass(slots=True)
class _Spend:
    max_usd: float
    spent: float = 0.0
    worst: float = 0.0  # the costliest document so far

    def check(self) -> None:
        if self.spent + self.worst > self.max_usd:
            raise Stop(f"the next document could pass --max-usd {self.max_usd:g} ({self.spent:.4f} spent)")


def reread(
    db: Database,
    chat: "ChatModel",
    settings: "Settings",
    customers: Sequence[str],
    *,
    max_usd: float,
    out: TextIO,
    storage: "RenditionStorage",
    checker: "Checker | None",
    sample: int | None = None,
) -> None:
    """Re-read each customer's eligible documents, or ``sample`` of them with the estimate
    for all; one line per document. Stop (from ``spend``, a model error or an unknown cost)
    ends the whole run, with its reason."""
    spend = _Spend(max_usd)
    for customer in customers:
        with db.customer(customer) as conn:
            active = customers_sql.is_active(conn, customer)
            documents = eligible(conn, customer) if active else []
            total_chars = int(conn.execute(_CHARS, {"c": customer, "ids": documents}).scalar_one())
        if not active:  # disabled or being deleted (0014): no model calls for it
            out.write(f"customer {customer}: not active, skipped\n")
            continue
        spent_before, chars_read = spend.spent, 0
        try:
            for document in documents[:sample] if sample is not None else documents:
                spend.check()
                chars_read += _one(db, chat, settings, customer, document, spend, out, storage, checker)
        except Stop as stop:
            out.write(f"stopped: {stop}\n")
            return
        if sample is not None and chars_read:
            per_1000 = (spend.spent - spent_before) * 1000 / chars_read
            estimate = per_1000 * total_chars / 1000
            out.write(
                f"customer {customer}: {per_1000:.4f} USD per 1000 characters; all {len(documents)} eligible"
                f" documents ({total_chars} characters): about {estimate:.2f} USD\n"
            )


def _one(
    db: Database,
    chat: "ChatModel",
    settings: "Settings",
    customer: str,
    document: str,
    spend: _Spend,
    out: TextIO,
    storage: "RenditionStorage",
    checker: "Checker | None",
) -> int:
    """Re-read one document; its characters read (0 when not read). Raises Stop."""
    with db.customer(customer) as conn:
        stored = _stored(conn, customer, document)
    if stored is None or stored.corrected:
        out.write(f"document {document}: {'changed' if stored is None else 'corrected'}, skipped\n")
        return 0
    readable = [
        s
        for s in stored.first
        if _points_at(s.location) and not is_word_mark(s) and s.location.page not in stored.unreadable
    ]
    chars = sum(len(span.text) for span in readable)
    cut = WindowSettings.from_settings(settings)
    with usage_scope(db, customer_id=customer, user_id=None, kind="land", document_id=document) as scope:
        reading = read_land(chat, reader_text(readable), cut, settings.model_context_tokens, _beat)
        rows = scope.rows()
    costs = [row.cost_usd for row in rows]
    calls, tokens = sum(r.calls for r in rows), sum(r.input_tokens + r.output_tokens for r in rows)
    cost = None if None in costs else sum(c for c in costs if c is not None)
    if cost is not None:
        spend.spent += cost
        spend.worst = max(spend.worst, cost)
    per_1000 = "unknown" if cost is None or not chars else f"{cost * 1000 / chars:.4f}"
    shown = "unknown" if cost is None else f"{cost:.4f}"
    if reading.error_code is not None and not reading.unusable:
        raise Stop(
            f"document {document}: the model failed ({reading.error_code}); run land-facts again to go on"
        )
    if reading.error_code is not None:
        outcome = _failed(db, customer, document, stored, reading.error_code, calls)
        out.write(f"document {document}: {outcome} ({reading.error_code}), {calls} calls, cost_usd {shown}\n")
        if cost is None:
            raise Stop(f"document {document}: a call's cost is not known, so --max-usd cannot be kept")
        return chars
    values = _labelled(db, customer, document, reading.values, stored, settings, storage, checker)
    outcome = _save(db, customer, document, stored, values, calls, reading.truncated)
    out.write(
        f"document {document}: {outcome}, {len(values)} values, {calls} calls, {tokens} tokens,"
        f" cost_usd {shown}, per 1000 characters {per_1000}\n"
    )
    if cost is None:
        raise Stop(f"document {document}: a call's cost is not known, so --max-usd cannot be kept")
    return chars


def _beat(done: int, total: int) -> None:
    """No heartbeat here: the command runs in the operator's terminal, not on a lease."""


def _stored(conn: Connection, customer: str, document: str) -> _Stored | None:
    ids = {"c": customer, "d": document}
    row = conn.execute(_DOCUMENT, ids).first()
    if row is None:
        return None
    outputs = {
        str(r.role): (str(r.reader), [span_from_json(s) for s in r.output])
        for r in conn.execute(_OUTPUTS, ids)
    }
    pages = conn.execute(_PAGES, ids).all()
    left_out = {int(p.page_no) for p in pages if p.route in ("ocr", "blank")}
    second = outputs.get("second")
    values = conn.execute(_VALUES, ids).all()
    return _Stored(
        run_id=row.run_id,
        detected_type=str(row.detected_type),
        sha256=str(row.sha256),
        reader=outputs.get("first", ("", []))[0],
        first=outputs.get("first", ("", []))[1],
        second=None if second is None else [s for s in second[1] if s.location.page not in left_out],
        model_pages=frozenset(int(p.page_no) for p in pages if p.read_by and p.read_by not in _NOT_A_MODEL),
        unreadable=frozenset(int(p.page_no) for p in pages if p.read_quality == "unreadable"),
        local_pages=frozenset(int(p.page_no) for p in pages if not HANDED_ON.isdisjoint(p.read_issues or ())),
        others=[_value(v) for v in values if not str(v.record_key).startswith("land:")],
        corrected=any(
            str(v.record_key).startswith("land:") and v.label_reason == "corrected" for v in values
        ),
    )


def _value(row: Any) -> ExtractedValue:
    return ExtractedValue(str(row.record_key), str(row.field), str(row.value_text), str(row.value_text),
                          location_from_json(row.region), label=str(row.label))  # fmt: skip


def _labelled(
    db: Database,
    customer: str,
    document: str,
    found: list[ExtractedValue],
    stored: _Stored,
    settings: "Settings",
    storage: "RenditionStorage",
    checker: "Checker | None",
) -> list[ExtractedValue]:
    """The land values labelled as the pipeline labels them: the checker on each box of a
    page a model read, ``label_quoted``, the reading marks, the remembered corrections."""
    marks, line_texts = _line_marks(stored.first)
    wanted = sorted(
        {v.location for v in found if _checked(v, stored) and checker is not None},
        key=lambda box: box.page,
    )[: settings.max_check_crops]
    readings: dict[Location, str] = {}
    if wanted:
        data = storage.get(customer, document)
        if hashlib.sha256(data).hexdigest() != stored.sha256:
            raise Stop(f"document {document}: its stored file does not match, nothing written")
        # #522 PR 2: a Word file's pages from its PDF rendition, which they were read from.
        drawn, data = drawn_from(storage, customer, document, stored.detected_type, data)
        for page_no in sorted({box.page for box in wanted}):
            with page_picture(drawn, data, page_no, dpi=settings.page_image_dpi,
                              max_image_pixels=settings.max_image_pixels) as page:  # fmt: skip
                for box in (b for b in wanted if b.page == page_no and isinstance(b, BoxLocation)):
                    devanagari = _DEVANAGARI.search(at_line(box, line_texts) or "") is not None
                    with _check_crop(page, box) as crop:
                        reading = checker.read_crop(crop, devanagari=devanagari).strip() if checker else ""
                    if reading:
                        readings[box] = reading
    values = label_quoted(found, stored.second, readings, also_located=stored.others, checked=set(wanted))
    values = label_reading_marks(values, marks, stored.local_pages)
    learned = PgPipelineStore(db).load_learned(customer, {value.field for value in values})
    return [_with_parsed(value) for value in apply_learned(values, learned)]


def _checked(value: ExtractedValue, stored: _Stored) -> bool:
    """A box the checker reads (PRD 5 V1, security A8): scanned, on a page a model read, and
    not an unsure spot."""
    box = value.location
    return (
        isinstance(box, BoxLocation)
        and value.origin == "ocr"
        and box.page in stored.model_pages
        and (not spot_unsure(value))
    )


def _save(
    db: Database,
    customer: str,
    document: str,
    stored: _Stored,
    values: list[ExtractedValue],
    calls: int,
    truncated: bool,
) -> str:
    """The fenced transaction (the module docstring); its outcome: done, changed or corrected."""
    ids = {"c": customer, "d": document}
    with db.customer(customer) as conn:
        lock_family(conn, customer, document)
        now = conn.execute(_DOCUMENT, ids).first()
        if now is None or now.run_id != stored.run_id:
            return "changed"
        rows = conn.execute(_VALUES, ids).all()
        if any(str(row.record_key).startswith("land:") and row.label_reason == "corrected" for row in rows):
            return "corrected"
        cards = [(str(row.id), _value(row)) for row in rows if row.record_key == "card"]
        kept = {id(value) for value in without_owners([value for _, value in cards], values)}
        repeated = [value_id for value_id, value in cards if id(value) not in kept]
        conn.execute(_DELETE_LAND, ids)
        if repeated:
            conn.execute(_DELETE_OWNERS, {**ids, "ids": repeated})
        _insert_values(conn, ids, values, stored.reader)
        new = [str(row[0]) for row in conn.execute(_LAND_IDS, ids)]
        word_review.reapply(conn, customer, document, only=new)
        value_keys.key_document(conn, customer, document)
        details: dict[str, audit.DetailValue] = {
            "prompt": LAND_PROMPT_HASH,
            "values": len(values),
            "calls": calls,
            "truncated": truncated,
        }
        audit.record(conn, customer, actor_user_id=None, action=ACTION, target_type="document",
                     target_id=document, details=details)  # fmt: skip
    return "done"


def _failed(db: Database, customer: str, document: str, stored: _Stored, code: str, calls: int) -> str:
    """A reply that will not be usable: its audit row, fenced as ``_save``; failed or changed."""
    with db.customer(customer) as conn:
        lock_family(conn, customer, document)
        now = conn.execute(_DOCUMENT, {"c": customer, "d": document}).first()
        if now is None or now.run_id != stored.run_id:
            return "changed"
        details: dict[str, audit.DetailValue] = {"prompt": LAND_PROMPT_HASH, "failed": code, "calls": calls}
        audit.record(conn, customer, actor_user_id=None, action=ACTION, target_type="document",
                     target_id=document, details=details)  # fmt: skip
    return "failed"


def customers_of(
    db: Database, customer_id: str | None, all_ids: Callable[[Connection], Sequence[str]]
) -> list[str]:
    """One customer, or every one (``all_ids`` on a system connection)."""
    if customer_id is not None:
        return [customer_id]
    with db.system() as conn:
        return list(all_ids(conn))
