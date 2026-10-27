"""Model choice and AI spend in the reader (council 11 Oct 2026, FINAL 2, 3 and 9; SP1, MS5):
the strict-portable PAGE_SCHEMA and the local checks that keep its bounds, the prompt
version, the document's model and provider for the call, the cache key and read_by, the
cached page's ledger row, a withdrawn chosen model read locally, an outage clock per model,
and page threads that keep the usage scope.

No network and no database: the fakes of tests/test_gemini_ocr.py, and a ledger whose save
is kept in memory."""

import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import pytest
from PIL import Image

from kaagaz.ai import usage
from kaagaz.ai.openrouter import ProviderNotAllowed
from kaagaz.ai.replies import ModelOutputError, ModelUnavailable
from kaagaz.db.engine import Database
from kaagaz.db.page_reads import PageKey
from kaagaz.db.usage import UsageRow
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading import ocr
from kaagaz.reading.gemini_ocr import (
    CHECK_PROBLEM,
    GEMINI_OCR_PROMPT_VERSION,
    PAGE_SCHEMA,
    checked,
    pixels_sha256,
    repair_problem,
)
from kaagaz.reading.ocr import MAX_TABLE_INDEX, DocumentPages, PageRead
from kaagaz.reading.page_quality import READ_LOCALLY, READER_REFUSED
from kaagaz.reading.socket import PagePool, PagesInOrder
from tests.test_gemini_ocr import (
    CUSTOMER,
    DOCUMENT,
    MODEL,
    FakeCache,
    build,
    line,
    page,
    reply,
    value,
)

CLAUDE = "anthropic/claude-sonnet-5.5"
GPT = "openai/gpt-6-luna"
USER = "00000000-0000-0000-0000-00000000000b"
_BOUNDS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems"}
_BOUNDS |= {"minLength", "maxLength"}


def _objects(schema: Any) -> Iterator[dict[str, Any]]:
    """Every sub-schema of ``schema`` (itself included)."""
    if isinstance(schema, dict):
        yield schema
        for item in schema.values():
            yield from _objects(item)
    elif isinstance(schema, list):
        for item in schema:
            yield from _objects(item)


def _pages(cache: FakeCache, model: str | None = None, provider: str | None = None) -> DocumentPages:
    return DocumentPages(
        customer_id=CUSTOMER, document_id=DOCUMENT, cache=cache, model=model, provider=provider
    )


class _Ledger:
    """A database whose usage saves are kept in memory (``insert_usage`` is swapped)."""

    def __init__(self) -> None:
        self.rows: list[UsageRow] = []

    @contextmanager
    def customer(self, customer_id: str) -> Iterator["_Ledger"]:
        yield self


@contextmanager
def _ledger() -> Iterator[_Ledger]:
    """A scan's usage scope, open, its saved rows on the ledger once it ends."""
    ledger = _Ledger()

    def insert(conn: _Ledger, rows: list[UsageRow]) -> None:
        conn.rows.extend(rows)

    with (
        pytest.MonkeyPatch.context() as patch,
        usage.usage_scope(cast(Database, ledger), customer_id=CUSTOMER, user_id=USER, kind="scan"),
    ):
        patch.setattr(usage, "insert_usage", insert)
        yield ledger


class PortableSchemaTest(unittest.TestCase):
    """FINAL 9: PAGE_SCHEMA is strict-portable, and the local checks keep every bound."""

    def test_the_prompt_version_is_g3_in_the_reader_and_in_ocr(self) -> None:
        # Changed on purpose (#383, #382): g3 adds each line's block, "note" and col_span.
        self.assertEqual(GEMINI_OCR_PROMPT_VERSION, "g3")
        self.assertIs(GEMINI_OCR_PROMPT_VERSION, ocr.READER_PROMPT_VERSION)

    def test_no_numeric_or_length_bound_and_every_object_is_closed(self) -> None:
        objects = 0
        for schema in _objects(PAGE_SCHEMA):
            self.assertEqual(_BOUNDS & schema.keys(), set())
            kinds = schema.get("type")
            if kinds == "object" or (isinstance(kinds, list) and "object" in kinds):
                objects += 1
                self.assertIs(schema.get("additionalProperties"), False)
                self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(objects, 4)  # the reply, page, a line and its table

    def test_the_local_checks_still_refuse_what_the_bounds_refused(self) -> None:
        cell = {"id": "t1", "row": 0, "col": 0, "header": False}
        out_of_range = {
            "a box below 0": value(line("x", (-0.5, 100, 120, 500))),
            "a box past 1000": value(line("x", (100, 100, 120, 1000.5))),
            "a box of three numbers": value({**line("x"), "box_2d": [1, 2, 3]}),
            "a box of five numbers": value({**line("x"), "box_2d": [1, 2, 3, 4, 5]}),
            "a negative order": value(line("x", order=-1)),
            "a negative row": value(line("x", kind="table_cell", table={**cell, "row": -1})),
            "a negative column": value(line("x", kind="table_cell", table={**cell, "col": -1})),
        }
        for name, bad in out_of_range.items():
            with self.subTest(name), self.assertRaises(ModelOutputError):
                checked(bad)
        self.assertEqual(len(checked(value(line("x", (0, 0, 1000, 1000)))).lines), 1)  # the edges pass


class DocumentModelTest(unittest.TestCase):
    """MS5: the document's model and provider read its pages."""

    def test_the_documents_model_and_provider_make_the_call_the_key_and_read_by(self) -> None:
        built, cache = build(reply(value(line("Total 1,180")))), FakeCache()
        image = page()
        with built.engine.document(_pages(cache, CLAUDE, "google-vertex")):
            read = built.engine.read_page(image, page_no=1)
        (call,) = built.client.calls
        self.assertEqual((call.model, call.provider), (CLAUDE, "google-vertex"))
        self.assertEqual(read.read_by, CLAUDE)
        key = PageKey(CUSTOMER, DOCUMENT, 1, pixels_sha256(image), CLAUDE, GEMINI_OCR_PROMPT_VERSION)
        self.assertEqual(cache.puts, [key])

    def test_the_version_names_the_model_that_read_the_document(self) -> None:
        """R11: processing_runs.ocr_engine and reader_outputs.reader_version come from
        ``version``, asked after the document closes: the chosen model, else OCR_MODEL."""
        built = build(reply(value(line("Total"))), reply(value(line("Total"))))
        self.assertEqual(built.engine.version, f"{MODEL};prompt=g3")  # no document yet
        with built.engine.document(_pages(FakeCache(), CLAUDE, "google-vertex")):
            built.engine.read_page(page(), page_no=1)
            self.assertEqual(built.engine.version, f"{CLAUDE};prompt=g3")
        self.assertEqual(built.engine.version, f"{CLAUDE};prompt=g3")  # asked after it closed
        with built.engine.document(_pages(FakeCache())):  # no model chosen: OCR_MODEL
            built.engine.read_page(page(), page_no=1)
        self.assertEqual(built.engine.version, f"{MODEL};prompt=g3")

    def test_without_a_document_or_a_chosen_model_ocr_model_reads(self) -> None:
        built = build(reply(value(line("Total"))), reply(value(line("Total"))))
        built.engine.read(page())  # cli measure: no document
        with built.engine.document(_pages(FakeCache())):
            read = built.engine.read_page(page(), page_no=1)
        self.assertEqual([(call.model, call.provider) for call in built.client.calls], [(MODEL, None)] * 2)
        self.assertEqual(read.read_by, MODEL)

    def test_the_cache_misses_on_another_model_and_hits_on_the_same_one(self) -> None:
        built, cache = build(reply(value(line("Total"))), reply(value(line("Total")))), FakeCache()
        image = page()
        with built.engine.document(_pages(cache, MODEL)):
            built.engine.read_page(image, page_no=1)
        with built.engine.document(_pages(cache, CLAUDE, "google-vertex")):
            first = built.engine.read_page(image, page_no=1)
        with built.engine.document(_pages(cache, CLAUDE, "google-vertex")):
            again = built.engine.read_page(image, page_no=1)
        self.assertEqual(len(built.client.calls), 2)  # the model change missed; the same model hit
        self.assertEqual((first.cached, again.cached, again.read_by), (False, True, CLAUDE))

    def test_sp1_a_cached_page_is_one_cached_call_at_cost_0(self) -> None:
        built, cache = build(reply(value(line("Total"))), reply(value(line("Total")))), FakeCache()
        image = page()
        with built.engine.document(_pages(cache, CLAUDE, "google-vertex")):
            built.engine.read_page(image, page_no=1)  # read and cached: the client bills it
        with _ledger() as ledger, built.engine.document(_pages(cache, CLAUDE, "google-vertex")):
            built.engine.read_page(image, page_no=1)
            built.engine.read_page(image, page_no=2)  # the same pixels on another page: a miss
        self.assertEqual(len(built.client.calls), 1 + 1)
        (row,) = ledger.rows
        self.assertEqual((row.model, row.cached, row.calls, row.cost_usd), (CLAUDE, True, 1, 0.0))
        self.assertEqual((row.input_tokens, row.output_tokens), (0, 0))


class WithdrawnModelTest(unittest.TestCase):
    """A 404 for a chosen model reads the page locally; it never pauses everyone."""

    def test_a_chosen_model_that_is_gone_reads_the_page_locally_as_reader_refused(self) -> None:
        built = build(ModelUnavailable("model_not_found"))
        with (
            self.assertLogs("kaagaz.reading.gemini_ocr", "ERROR") as logs,
            built.engine.document(_pages(FakeCache(), CLAUDE, "google-vertex")),
        ):
            read = built.engine.read_page(page(), page_no=1)
        self.assertEqual((read.read_by, read.issues[0]), ("rapidocr", READER_REFUSED))
        self.assertIn("model_not_found", [getattr(record, "code", None) for record in logs.records])

    def test_ocr_model_that_is_gone_still_waits_as_a_key_problem(self) -> None:
        built = build(ModelUnavailable("model_not_found"))
        with (
            built.engine.document(_pages(FakeCache(), MODEL, "google-vertex")),
            self.assertRaises(BackendUnavailable) as raised,
        ):
            built.engine.read_page(page(), page_no=1)
        self.assertEqual(str(raised.exception), "gemini_key")


class RepairTest(unittest.TestCase):
    """R12: what ``checked`` refuses (a bad box, a table index out of range) is the
    client's one repair turn, not a page read locally."""

    def test_the_reader_asks_the_client_to_repair_what_checked_refuses(self) -> None:
        built = build(reply(value(line("Total"))))
        with built.engine.document(_pages(FakeCache(), CLAUDE, "google-vertex")):
            built.engine.read_page(page(), page_no=1)
        (call,) = built.client.calls
        self.assertIs(call.validate, repair_problem)
        cell = {"id": "t1", "row": MAX_TABLE_INDEX, "col": 0, "header": False}
        self.assertEqual(repair_problem(value(line("x", (100, 100, 120, 1000.5)))), CHECK_PROBLEM)
        self.assertEqual(repair_problem(value(line("x", kind="table_cell", table=cell))), CHECK_PROBLEM)
        self.assertIsNone(repair_problem(value(line("Total"))))
        self.assertIn(str(MAX_TABLE_INDEX), CHECK_PROBLEM)


class ProviderOutsideTheCeilingTest(unittest.TestCase):
    """R5: a provider refused by the client is a page read locally, never a failed job."""

    def test_a_refused_provider_reads_the_page_locally_as_reader_refused(self) -> None:
        built = build(ProviderNotAllowed("provider_not_allowed"))
        with (
            self.assertLogs("kaagaz.reading.gemini_ocr", "WARNING") as logs,
            built.engine.document(_pages(FakeCache(), GPT, "azure")),
        ):
            read = built.engine.read_page(page(), page_no=1)
        self.assertEqual((read.read_by, read.issues[0]), ("rapidocr", READER_REFUSED))
        self.assertIn("provider_not_allowed", [getattr(record, "code", None) for record in logs.records])


class OutagePerModelTest(unittest.TestCase):
    """FINAL 2: one model's outage clock is its own."""

    def test_another_models_good_page_does_not_restart_an_outage(self) -> None:
        built = build(
            ModelUnavailable("server_error"),
            reply(value(line("Total"))),
            ModelUnavailable("server_error"),
            reader_outage_fallback_minutes=10,
            queue_lease_seconds=1200,
            ocr_timeout_seconds=120.0,
        )
        engine, client = built.engine, built.client
        with (
            engine.document(_pages(FakeCache(), CLAUDE, "google-vertex")),
            self.assertRaises(BackendUnavailable),
        ):
            engine.read_page(page(), page_no=1)  # Claude's outage starts
        client.clock += 60.0
        with engine.document(_pages(FakeCache(), MODEL, "google-vertex")):
            self.assertEqual(engine.read_page(page(), page_no=1).read_by, MODEL)  # Gemini reads
        client.clock += 10 * 60.0
        with engine.document(_pages(FakeCache(), CLAUDE, "google-vertex")):
            read = engine.read_page(page(), page_no=1)
        self.assertEqual(read.issues[0], READ_LOCALLY)  # Claude has been down 11 minutes, one outage


class _BillingEngine:
    """A page engine whose read bills one call, as the model client does, from a page thread."""

    name = "gemini"
    version = "test"

    def __init__(self) -> None:
        self.threads: set[str] = set()

    def read(self, image: Image.Image) -> list[ocr.OcrLine]:
        return []

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        self.threads.add(threading.current_thread().name)
        usage.record_call(CLAUDE, "Google", 10, 2, 1, 0.01)
        return PageRead(
            page_no=page_no,
            lines=(),
            quality="good",
            handwritten=False,
            scripts=(),
            issues=(),
            read_by=CLAUDE,
        )


def test_sp1_page_threads_bill_the_scope_the_reader_is_in() -> None:
    engine, pool = _BillingEngine(), PagePool.threads(3)
    try:
        with _ledger() as ledger, PagesInOrder(engine, pool) as pages:
            for page_no in range(1, 7):
                pages.read(Image.new("RGB", (4, 4)), page_no, lambda read: None)
            pages.drain()
    finally:
        cast(Any, pool.executor).shutdown()
    assert all(name.startswith("kaagaz-page") for name in engine.threads)
    (row,) = ledger.rows
    assert (row.kind, row.user_id, row.calls, row.input_tokens) == ("scan", USER, 6, 60)
