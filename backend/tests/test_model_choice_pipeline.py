"""Model choice and AI spend in the pipeline (council 11 Oct 2026, FINAL 3, 10 and 11; SP1,
MS5): the job's model reads its scans and is the one the checker and the counters look
for, the run's config names it, and the job's model calls are billed to the user who asked
for the reading, scan or upgrade, with card, invoice, map and embed for those stages.
Keyless mode makes no query and keeps no ledger.

The rigs of tests/test_pipeline_gemini.py and tests/test_pipeline_invoice.py; the ledger's
save is kept in memory."""

import functools
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, cast
from unittest import mock

import pytest
from sqlalchemy.exc import OperationalError

from kaagaz import eta
from kaagaz.ai import usage
from kaagaz.ai.client import ModelUnavailable
from kaagaz.chunking.sections import MapBudget
from kaagaz.db import models
from kaagaz.db.engine import Database
from kaagaz.db.usage import UsageRow
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.pipeline import DocumentProcessor, JobReading, build_processor
from kaagaz.reading.ocr import PageRead
from kaagaz.reading.wiring import GEMINI_OCR_MODULE
from kaagaz.scanning.gate import Verdict
from kaagaz.settings import Settings
from tests.fakes import FakeChat, FakeEmbedder, FakeScanner
from tests.pdf_builder import make_pdf
from tests.settings_factory import make_settings
from tests.test_extraction_card import reply as card_reply
from tests.test_pipeline_gemini import (
    CUSTOMER,
    SCANNED,
    TOTAL,
    FakeChecker,
    FakeGeminiModule,
    PictureStorage,
    ScriptedReader,
    page_read,
)
from tests.test_pipeline_gemini import rig as gemini_rig
from tests.test_pipeline_invoice import MISMATCHED, MISMATCHED_REPLY
from tests.test_pipeline_invoice import rig as invoice_rig

CLAUDE = "anthropic/claude-sonnet-5.5"
USER = "00000000-0000-0000-0000-00000000000b"


class _Ledger:
    """A database whose usage saves are kept in memory (``insert_usage`` is swapped)."""

    def __init__(self) -> None:
        self.rows: list[UsageRow] = []

    @contextmanager
    def customer(self, customer_id: str) -> Iterator["_Ledger"]:
        yield self

    def by_kind(self) -> dict[str, UsageRow]:
        assert len({row.kind for row in self.rows}) == len(self.rows)  # one row per scope here
        return {row.kind: row for row in self.rows}


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> _Ledger:
    def insert(conn: _Ledger, rows: list[UsageRow]) -> None:
        conn.rows.extend(rows)

    monkeypatch.setattr(usage, "insert_usage", insert)
    return _Ledger()


def _scopes(ledger: _Ledger) -> Any:
    return functools.partial(usage.usage_scope, cast(Database, ledger))


class _Reading:
    """``job_reading``: the same answer for every job, and the jobs it was asked about."""

    def __init__(
        self, user: str | None, model: str | None = CLAUDE, provider: str | None = "google-vertex"
    ) -> None:
        self.reading = JobReading(user, model, provider)
        self.asked: list[tuple[str, str]] = []

    def __call__(self, customer_id: str, document_id: str) -> JobReading:
        self.asked.append((customer_id, document_id))
        return self.reading


class BillingReader(ScriptedReader):
    """The Gemini reader as the pipeline sees it: each page it reads is one billed call to
    the document's model, as the model client records it."""

    def read_page(self, image: Any, *, page_no: int) -> PageRead:
        assert self.open is not None and self.open.model is not None
        usage.record_call(self.open.model, "Google", 1700, 4000, 300, 0.05)
        return super().read_page(image, page_no=page_no)


class BilledChat(FakeChat):
    """The chat model, each call billed as the model client does."""

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
        model: str | None = None,
        thinking: str | None = None,
        provider: str | None = None,
        deadline: float | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        usage.record_call(self.chat_model, None, 100, 20, 0, 0.001)
        return super().chat_json(system=system, user=user, schema=schema, max_tokens=max_tokens)


class BilledEmbedder(FakeEmbedder):
    def __call__(
        self, customer_id: str, document_id: str, *, on_batch: Callable[[int, int], None] | None = None
    ) -> int:
        usage.record_call("google/gemini-embedding-2", None, 7, 0, 0, 0.0001)
        return super().__call__(customer_id, document_id, on_batch=on_batch)


# MS5, FINAL 10: the job's model.


def test_the_jobs_model_reads_its_scans_and_is_what_the_checker_and_counters_look_for(
    ledger: _Ledger,
) -> None:
    reader = BillingReader({1: page_read(TOTAL, read_by=CLAUDE)})
    checker, job_reading = FakeChecker(), _Reading(USER)
    system = gemini_rig(reader, checker=checker, job_reading=job_reading, usage=_scopes(ledger))
    job = system.run(make_pdf([SCANNED]))

    (opened,) = reader.opened
    assert (opened.model, opened.provider) == (CLAUDE, "google-vertex")
    assert job_reading.asked == [(CUSTOMER, job.document_id)]
    assert checker.crops  # a page CLAUDE read is checked
    totals = system.totals(job)
    assert (totals.pages_gemini, totals.config["read_model"]) == (1, CLAUDE)
    (page,) = system.pages(job)
    assert page.read_by == CLAUDE


def test_with_no_job_reading_ocr_model_is_the_jobs_model_as_before(ledger: _Ledger) -> None:
    reader = ScriptedReader({1: page_read(TOTAL)})
    system = gemini_rig(reader, checker=FakeChecker())
    job = system.run(make_pdf([SCANNED]))
    (opened,) = reader.opened
    assert (opened.model, opened.provider) == ("google/gemini-3.1-pro-preview", None)
    assert system.totals(job).pages_gemini == 1
    assert ledger.rows == []


def test_a_page_another_model_read_is_not_the_jobs_model(ledger: _Ledger) -> None:
    """The checker and the counters compare with the job's model, not OCR_MODEL."""
    reader = BillingReader({1: page_read(TOTAL)})  # read_by OCR_MODEL, but the job is CLAUDE's
    checker = FakeChecker()
    system = gemini_rig(reader, checker=checker, job_reading=_Reading(USER), usage=_scopes(ledger))
    job = system.run(make_pdf([SCANNED]))
    assert checker.crops == []
    assert system.totals(job).pages_gemini == 0


# SP1, FINAL 3 and 11: the ledger.


def test_sp1_a_scan_is_billed_to_the_user_who_asked_for_it(ledger: _Ledger) -> None:
    reader = BillingReader({1: page_read(TOTAL, read_by=CLAUDE), 2: page_read(TOTAL, read_by=CLAUDE)})
    system = gemini_rig(reader, checker=FakeChecker(), job_reading=_Reading(USER), usage=_scopes(ledger))
    job = system.run(make_pdf([SCANNED, SCANNED]))
    (row,) = ledger.rows
    assert (row.customer_id, row.user_id, row.document_id, row.kind) == (
        CUSTOMER,
        USER,
        job.document_id,
        "scan",
    )
    assert (row.model, row.provider_name, row.calls, row.input_tokens) == (CLAUDE, "Google", 2, 3400)


def test_sp1_a_reading_no_user_asked_for_is_an_upgrade_with_no_user(ledger: _Ledger) -> None:
    reader = BillingReader({1: page_read(TOTAL, read_by=CLAUDE)})
    system = gemini_rig(reader, checker=FakeChecker(), job_reading=_Reading(None), usage=_scopes(ledger))
    system.run(make_pdf([SCANNED]))
    (row,) = ledger.rows
    assert (row.kind, row.user_id) == ("upgrade", None)


def test_sp1_a_job_a_backend_stopped_still_saves_what_it_was_billed(ledger: _Ledger) -> None:
    reader = BillingReader({1: page_read(TOTAL, read_by=CLAUDE), 2: BackendUnavailable("gemini")})
    system = gemini_rig(reader, checker=FakeChecker(), job_reading=_Reading(USER), usage=_scopes(ledger))
    with pytest.raises(BackendUnavailable):
        system.run(make_pdf([SCANNED, SCANNED]))
    (row,) = ledger.rows
    assert (row.kind, row.calls) == ("scan", 2)


def test_sp1_card_invoice_map_and_embed_each_have_their_kind(ledger: _Ledger) -> None:
    chat = BilledChat(card_reply(doc_type="invoice"), MISMATCHED_REPLY, ModelUnavailable("server_error"))
    system = invoice_rig(
        MISMATCHED,
        chat,
        job_reading=_Reading(USER, model=None, provider=None),
        usage=_scopes(ledger),
        map_budget=MapBudget.from_settings(make_settings()),
        embed=BilledEmbedder(),
    )
    job, _ = system.read()
    assert len(chat.calls) == 3  # the card, one invoice window, the map
    kinds = ledger.by_kind()
    assert set(kinds) == {"card", "invoice", "map", "embed"}  # nothing billed as the scan itself
    assert all(row.calls == 1 for row in kinds.values())
    assert all((row.user_id, row.document_id) == (USER, job.document_id) for row in kinds.values())
    assert kinds["embed"].model == "google/gemini-embedding-2"
    (run,) = system.runs.for_document(CUSTOMER, job.document_id)
    assert run.totals is not None and "read_model" not in run.totals.config  # no model reads scans


def test_sp1_a_ledger_that_cannot_be_saved_never_fails_the_job(
    ledger: _Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(conn: _Ledger, rows: list[UsageRow]) -> None:
        raise OperationalError("insert", {}, Exception("down"))

    monkeypatch.setattr(usage, "insert_usage", down)
    reader = BillingReader({1: page_read(TOTAL, read_by=CLAUDE)})
    system = gemini_rig(reader, checker=FakeChecker(), job_reading=_Reading(USER), usage=_scopes(ledger))
    job = system.run(make_pdf([SCANNED]))
    assert system.totals(job).status == "ready"


class _Counting(ScriptedReader):
    """Notes the most pages being read at once."""

    def __init__(self, pages: dict[int, PageRead | Exception]) -> None:
        super().__init__(pages)
        self.lock, self.now, self.most = threading.Lock(), 0, 0

    def read_page(self, image: Any, *, page_no: int) -> PageRead:
        with self.lock:
            self.now += 1
            self.most = max(self.most, self.now)
        try:
            time.sleep(0.05)
            return super().read_page(image, page_no=page_no)
        finally:
            with self.lock:
                self.now -= 1


def test_each_job_reads_at_most_the_super_admins_pages_at_once(ledger: _Ledger) -> None:
    """Package S (0053): the value ``job_reading`` gives at the job's start, for that job only."""
    reader = _Counting({page: page_read(TOTAL) for page in range(1, 5)})
    job_reading = _Reading(USER)
    settings = make_settings(page_image_dpi=72, ocr_dpi=72, ocr_engine="gemini", ocr_page_concurrency=4)
    system = gemini_rig(
        reader, checker=FakeChecker(), job_reading=job_reading, usage=_scopes(ledger), settings=settings
    )
    job_reading.reading = replace(job_reading.reading, pages_at_once=1)
    assert system.totals(system.run(make_pdf([SCANNED] * 4))).status == "ready"
    assert reader.most == 1
    job_reading.reading, reader.most = replace(job_reading.reading, pages_at_once=None), 0
    assert system.totals(system.run(make_pdf([SCANNED] * 3))).status == "ready"
    assert reader.most > 1  # the next job: OCR_PAGE_CONCURRENCY again


def test_each_jobs_time_left_counts_its_own_pages_at_once(ledger: _Ledger) -> None:
    """Package S: the estimate assumes the pages the job really reads at once, not
    OCR_PAGE_CONCURRENCY, once the super admin lowered it."""
    job_reading = _Reading(USER)
    settings = make_settings(page_image_dpi=72, ocr_dpi=72, ocr_engine="gemini", ocr_page_concurrency=4)
    system = gemini_rig(
        ScriptedReader({1: page_read(TOTAL), 2: page_read(TOTAL)}),
        checker=FakeChecker(),
        job_reading=job_reading,
        usage=_scopes(ledger),
        settings=settings,
        ocr_page_concurrency=4,
    )
    with mock.patch("kaagaz.pipeline.rates", wraps=eta.rates) as estimated:
        job_reading.reading = replace(job_reading.reading, pages_at_once=1)
        system.run(make_pdf([SCANNED, SCANNED]))
        job_reading.reading = replace(job_reading.reading, pages_at_once=None)
        system.run(make_pdf([SCANNED]))
    assert [call.kwargs["pages_at_once"] for call in estimated.call_args_list] == [1, 4]


# The wiring: keyless makes no query and keeps no ledger.


class _Conn:
    def __init__(self, user: str | None, pages: int | None) -> None:
        self.user, self.pages = user, pages
        self.asked = ""

    def execute(self, statement: object, params: dict[str, str] | None = None) -> "_Conn":
        self.asked = str(statement)
        assert "read_requested_by" in self.asked or "pages_at_once" in self.asked
        return self

    def scalar_one_or_none(self) -> str | None:
        assert "read_requested_by" in self.asked
        return self.user

    def scalar_one(self) -> int | None:
        assert "pages_at_once" in self.asked
        return self.pages


class _Db:
    """A database that answers the job's queries: who asked for the reading and, with the
    Gemini reader, the super admin's pages at once (package S)."""

    def __init__(self, user: str | None = None, pages: int | None = None) -> None:
        self.conn = _Conn(user, pages)
        self.customers: list[str] = []

    @contextmanager
    def customer(self, customer_id: str) -> Iterator[_Conn]:
        self.customers.append(customer_id)
        yield self.conn


def _built(db: _Db, settings: Settings, model: Any = None) -> DocumentProcessor:
    with mock.patch.dict(sys.modules, {GEMINI_OCR_MODULE: FakeGeminiModule().module}):
        return build_processor(
            settings=settings,
            db=cast(Database, db),
            storage=PictureStorage(),
            scanner=FakeScanner(Verdict.CLEAN),
            model=model,
        )


def test_keyless_builds_no_job_reading_and_no_ledger() -> None:
    processor = _built(_Db(), make_settings(ocr_engine="rapidocr", model_enabled=False))
    assert (processor._job_reading, processor._usage) == (None, None)


def test_with_the_gemini_reader_each_job_resolves_its_model_for_prompt_g2() -> None:
    db = _Db(USER, pages=2)
    processor = _built(db, make_settings(ocr_engine="gemini"))
    assert processor._job_reading is not None and processor._usage is not None
    # Changed on purpose (features 25-27, 15 Oct 2026): the job reads the whole Reading
    # (resolve_scan: mode, Careful, Ensemble's others), which knows the reader's prompt itself.
    scan = models.Reading("single", models.Agent(CLAUDE, "google-vertex", "Claude"), thinking="low")
    with mock.patch.object(models, "resolve_scan", return_value=scan) as resolve:
        reading = processor._job_reading(CUSTOMER, "doc-1")
    # Package S (0053): the super admin's pages at once, read at the job's start.
    assert reading == JobReading(USER, CLAUDE, "google-vertex", reading=scan, pages_at_once=2)
    assert db.customers == [CUSTOMER]
    (call,) = resolve.call_args_list
    assert call.args[1:] == (CUSTOMER, "doc-1")


def test_with_a_chat_model_but_rapidocr_each_job_asks_only_who_it_is_billed_to() -> None:
    processor = _built(_Db(), make_settings(ocr_engine="rapidocr", model_enabled=True), model=FakeChat())
    assert processor._job_reading is not None and processor._usage is not None
    with mock.patch.object(models, "resolve") as resolve:
        assert processor._job_reading(CUSTOMER, "doc-1") == JobReading(None)
    resolve.assert_not_called()
