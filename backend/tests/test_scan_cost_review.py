"""The #463 council review's backend fixes without a database (Tushar, 31 Oct 2026): a page
read is one page on the ledger however many calls it took (C3); Careful, a page read again and
Ensemble's other models each send a level their own model takes (P4, S7, C2); a page read again
gets no low read alongside at the quickest level, and one at its own picture detail (X2); a
page keeps its first read's settings and output tokens and the pass that produced its text
(C9, U6); an accuracy check's read counts only at the settings it asked for (C1); the worker's
check handlers are required (P9); and one rule each for the scan's default path (P5), the
Ensemble settings (P7) and the five scan cost fields (P8). The SQL: test_scan_cost_review_db."""

import contextlib
import dataclasses
import threading
import time
from collections.abc import Iterator
from typing import Any, cast
from unittest import mock

import pytest
from PIL import Image

from kaagaz.ai import usage
from kaagaz.ai.openrouter import PageReply
from kaagaz.ai.replies import ModelOutputError
from kaagaz.api.routers import operator_models as router
from kaagaz.db import models
from kaagaz.ingestion.ports import Job
from kaagaz.jobs import accuracy
from kaagaz.reading.ocr import DocumentPages, OcrLine, PageRead
from kaagaz.settings import NO_THINKING
from kaagaz.worker_main import MarkExhausted, dispatch
from tests.settings_factory import make_settings
from tests.test_ai_resolve import FakeConn, document, row
from tests.test_cost_saving import HARD, WORDS, Client, first_reply, reader
from tests.test_dynamic_reading import CAREFUL, STANDARD, ledger
from tests.test_fast_scan import inline_pages
from tests.test_gemini_ocr import MODEL, Built, FakeCache, FakeClient, page, pages, reply, value

GEMINI = make_settings(ocr_engine="gemini")
LOW_HIGH = frozenset({"low", "high"})


class BillingClient(FakeClient):
    """``FakeClient``, each call billed as the real client bills a page read: 0.001 dollars,
    with what it was sent."""

    def read_page(self, **request: Any) -> PageReply:
        usage.record_call(
            str(request.get("model") or MODEL),
            None,
            10,
            20,
            0,
            0.001,
            thinking=request["thinking_level"],
            resolution=request["media_resolution"],
        )
        return super().read_page(**request)


# --- C3: a page is counted once on the ledger ----------------------------------------------


def test_a_page_read_in_halves_is_one_page_with_the_cost_of_its_three_calls() -> None:
    built = Built(BillingClient(ModelOutputError("output_truncated"), reply(value()), reply(value())))
    with ledger() as kept, built.engine.document(pages(FakeCache())):
        built.engine.read_page(page(), page_no=1)
    [row_] = kept.rows
    assert (row_.calls, row_.pages, row_.limit_hits) == (3, 1, 0)
    assert row_.cost_usd == pytest.approx(0.003)


def test_a_page_is_counted_on_its_own_calls_row_and_never_alone() -> None:
    with ledger() as kept:
        usage.record_page(STANDARD, thinking="low", resolution="high")  # no call yet: nothing
        with usage.billed_as("careful"):
            usage.record_call(STANDARD, None, 1, 1, 0, 0.1, thinking="high", resolution="low")
            usage.record_page(STANDARD, thinking="high", resolution="low")
            usage.record_limit_hit(STANDARD, thinking="high", resolution="low")
    assert [(r.kind, r.calls, r.pages, r.limit_hits) for r in kept.rows] == [("careful", 1, 1, 1)]


# --- P4, S7, C2: each model is sent a level it takes ----------------------------------------


def test_careful_and_a_page_read_again_send_a_level_their_model_takes() -> None:
    # Careful's model does not take the saved minimal: it reads at OCR_RETRY_THINKING_LEVEL.
    client = Client({STANDARD: first_reply(HARD)}, crops=WORDS)
    careful = dataclasses.replace(
        inline_pages(3), careful_thinking="minimal", levels={CAREFUL: LOW_HIGH, STANDARD: LOW_HIGH}
    )
    one = reader(client)
    with one.document(careful):
        one.read_page(page(), page_no=1)
    assert [(r["model"], r["thinking_level"]) for r in client.requests] == [
        (STANDARD, "low"),
        (CAREFUL, GEMINI.ocr_retry_thinking_level),
    ]
    # A model that takes it is sent it, as before.
    takes = Client({STANDARD: first_reply(HARD)}, crops=WORDS)
    two = reader(takes)
    taken = {CAREFUL: frozenset({"minimal", "high"})}
    with two.document(dataclasses.replace(inline_pages(3), careful_thinking="minimal", levels=taken)):
        two.read_page(page(), page_no=1)
    assert takes.requests[-1]["thinking_level"] == "minimal"

    # "Read this page again": the lead does not take minimal either.
    again = Client({STANDARD: reply(value())})
    harder = DocumentPages(
        customer_id="c",
        document_id="d",
        cache=FakeCache(),
        model=STANDARD,
        read_again=frozenset({1}),
        careful_thinking="minimal",
        careful_resolution="low",
        levels={STANDARD: LOW_HIGH},
    )
    three = reader(again)
    with three.document(harder):
        three.read_page(page(), page_no=1)
    assert [(r["thinking_level"], r["media_resolution"]) for r in again.requests] == [
        (GEMINI.ocr_retry_thinking_level, "low")
    ]


def test_a_level_fits_its_model_as_the_scan_level_does() -> None:
    takes = models.Agent("google/a", "google-vertex", "A", efforts=("minimal", "low"))
    lacks = models.Agent("google/b", "google-vertex", "B", efforts=("low", "high"))
    plain = models.Agent("google/c", "google-vertex", "C", thinks=False)
    unknown = models.Agent("google/d", "google-vertex", "D")  # no catalogue row: as asked
    assert [models.fit_level(agent, "minimal", "low") for agent in (takes, lacks, plain, unknown)] == [
        "minimal",
        "low",
        NO_THINKING,
        "minimal",
    ]
    reading = models.Reading("dynamic", takes, careful=plain)
    assert reading.accepted() == {"google/a": frozenset({"minimal", "low"})}
    assert models.Reading("single", unknown).accepted() == {}
    # The catalogue's levels ride on the agent, never in its identity.
    assert models._agent(cast(Any, row("google/e", efforts=("low",))), GEMINI).efforts == ("low",)
    assert lacks == models.Agent("google/b", "google-vertex", "B")


# --- X2: a page read again and its low read alongside ---------------------------------------


def _again(careful_thinking: str, careful_resolution: str) -> list[tuple[str, str]]:
    """The calls of a page read again whose harder read is slow (0.3 s, past the share of its
    0.4 s after which a low read starts alongside)."""
    built = Built(FakeClient(), ocr_timeout_seconds=0.4)
    calls: list[tuple[str, str]] = []
    lock = threading.Lock()

    def read_page(**kwargs: Any) -> PageReply:
        with lock:
            calls.append((kwargs["thinking_level"], kwargs["media_resolution"]))
        if kwargs["thinking_level"] != "low":
            time.sleep(0.3)
        return reply(value())

    document = dataclasses.replace(
        pages(FakeCache(), read_again=frozenset({1})),
        careful_thinking=careful_thinking,
        careful_resolution=careful_resolution,
    )
    with built.engine.document(document), mock.patch.object(built.client, "read_page", read_page):
        built.engine.read_page(page(), page_no=1)
    return sorted(calls)


def test_a_minimal_re_read_pair_sends_no_low_read_and_never_high_detail() -> None:
    assert _again("minimal", "low") == [("minimal", "low")]
    # A harder pair still gets its low read alongside, at the pair's own picture detail.
    assert _again("high", "low") == [("high", "low"), ("low", "low")]


# --- C9, U6: the page's first read, its output tokens and the pass that produced it ----------


def test_a_page_keeps_its_first_reads_settings_and_tokens_and_the_pass_that_produced_it() -> None:
    # Careful's whole page is kept (a page-wide problem): its text, the first read's settings.
    poor = Client({STANDARD: first_reply(HARD, quality="poor"), CAREFUL: reply(value(), 30, 70)})
    one = reader(poor)
    with one.document(dataclasses.replace(inline_pages(3), careful_thinking="high")):
        kept = one.read_page(page(), page_no=1)
    assert kept.read_by == CAREFUL
    assert (kept.read_thinking, kept.read_resolution, kept.read_pass) == ("low", "high", "again")
    assert kept.read_output_tokens == first_reply(HARD).output_tokens != 70

    clean = Client({STANDARD: dataclasses.replace(first_reply({}), output_tokens=40)})
    two = reader(clean)
    with two.document(inline_pages(3)):
        first = two.read_page(page(), page_no=1)
    assert (first.read_by, first.read_thinking, first.read_pass, first.read_output_tokens) == (
        STANDARD,
        "low",
        "first",
        40,
    )

    # Read again: the first read's settings stay the document's; no first read's tokens.
    again = Client({STANDARD: reply(value())})
    harder = DocumentPages(
        customer_id="c", document_id="d", cache=FakeCache(), model=STANDARD, read_again=frozenset({1})
    )
    three = reader(again)
    with three.document(harder):
        reread = three.read_page(page(), page_no=1)
    assert (reread.read_thinking, reread.read_resolution, reread.read_pass) == ("low", "high", "again")
    assert reread.read_output_tokens is None


# --- C1: an accuracy check's read counts only at the settings it asked for ------------------


class _Engine:
    """A scan reader whose page comes back as ``read``."""

    name = "gemini"
    version = "fake"

    def __init__(self, read: PageRead) -> None:
        self.page = read

    @contextlib.contextmanager
    def document(self, pages: DocumentPages) -> Iterator[None]:
        yield

    def read(self, image: Image.Image) -> list[OcrLine]:
        raise AssertionError("numbered pages only")

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        return self.page


def test_a_checks_read_at_other_settings_than_asked_counts_as_failed() -> None:
    best = models.Reading("single", models.Agent(STANDARD, "google-vertex", "S"), thinking="high")
    read = PageRead(
        page_no=1,
        lines=(),
        quality="good",
        handwritten=False,
        scripts=(),
        issues=(),
        read_by=STANDARD,
        read_thinking="high",
        read_resolution="high",
    )
    image = Image.new("RGB", (10, 10))
    at = ("c", "d", 1, image)
    assert accuracy._read(_Engine(read), best, *at, settings=GEMINI) is read
    low = dataclasses.replace(read, read_thinking="low")  # the low read alongside answered
    assert accuracy._read(_Engine(low), best, *at, settings=GEMINI) is None
    other_detail = dataclasses.replace(read, read_resolution="low")
    assert accuracy._read(_Engine(other_detail), best, *at, settings=GEMINI) is None


# --- P9: the worker's check handlers are required -------------------------------------------


def test_the_worker_cannot_be_built_without_its_check_handlers() -> None:
    def ok(job: Job, beat: Any) -> None:
        return None

    with pytest.raises(TypeError):
        cast(Any, dispatch)(ok, ok)
    with pytest.raises(TypeError):
        cast(Any, MarkExhausted)(lambda c, d: None, lambda c, r: None)


# --- P5, P7, P8: one rule each --------------------------------------------------------------


def test_the_scans_default_path_is_the_estimates_rule() -> None:
    rows = [row(STANDARD, default=True, efforts=("minimal", "low", "high"))]
    conn = FakeConn(scan=rows, settings={"dynamic": True}, document=document(mode="dynamic"))
    sentinel = models.Reading("dynamic", models.Agent("x/y", "p", "n"))
    with mock.patch.object(models, "scope_reading", return_value=sentinel) as rule:
        assert models.resolve_scan(conn.conn(), "c", "d", settings=GEMINI) is sentinel
    assert rule.call_count == 1


def test_ensemble_reads_the_settings_once() -> None:
    rows = [row(STANDARD, default=True), row(CAREFUL)]
    conn = FakeConn(scan=rows, settings={"ensemble": True}, document=document(STANDARD))
    models.resolve_ensemble(conn.conn(), "c", "d", settings=GEMINI)
    assert [statement for statement, _params in conn.sent].count(models._SETTINGS) == 1


def test_the_five_scan_cost_fields_are_declared_once() -> None:
    assert issubclass(router.ScanCostBody, router.ScanSettingsBody)
    assert issubclass(router.AccuracyCheckBody, router.ScanSettingsBody)
    # Changed on purpose (#473): the draft's page reader and Mistral model too; (#488) and Ultra low
    # cost's ceiling.
    assert set(router.ScanSettingsBody.model_fields) == {
        *models.SCAN_COST_FIELDS,
        "dynamic",
        "scan_reader",
        "mistral_model",
        "ulc_ceiling_paise",
    }
