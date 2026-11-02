"""Mistral OCR wired in as the page reader an organisation chose (#473, Tushar, 2 Nov 2026).

What is chosen is what is used: a document whose organisation is on Mistral has every page the
scan reader would send to Gemini read by Mistral instead, and never by Gemini (not Careful, not
Ensemble, not "Read this page again", not a fallback). When Mistral can't read, the document
waits (deferred, uncounted) and the worker carries on with everyone else's jobs. The free paths
stay free: the text layer, a page routed to the local reader (#384), Dynamic's memo and local mode.

No network and never a real key: ``FakeMistral`` is the client with its call scripted, the
Gemini client is ``tests/test_gemini_ocr.FakeClient`` with nothing scripted (any call to it
fails the test), and the ledger is kept in memory.
"""

import dataclasses
import logging
import threading
import urllib.request
from collections.abc import Iterator
from typing import Any, cast
from unittest import mock

import pytest
from PIL import Image

from kaagaz import pipeline
from kaagaz.api import views
from kaagaz.db import models
from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database
from kaagaz.db.page_reads import PageKey
from kaagaz.ingestion import sniff
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs import accuracy
from kaagaz.jobs.accuracy import AccuracyChecks
from kaagaz.jobs.worker import Worker
from kaagaz.reading import gemini_ocr, mistral_reader, wiring
from kaagaz.reading.gemini_ocr import GeminiEngine, pixels_sha256
from kaagaz.reading.mistral_reader import BadReply, MistralClient, MistralUnavailable
from kaagaz.reading.ocr import DocumentPages, PageRead, as_page_engine
from kaagaz.reading.page_quality import READER_REFUSED
from kaagaz.reading.page_route import LOCAL, REGIONS, PageRoute
from kaagaz.reading.rapid_pool import RapidOcrPool
from kaagaz.reading.wiring import build_reader_socket
from tests.lease_queue import FakeClock, LeaseQueue
from tests.pdf_builder import Page, Text, make_pdf
from tests.settings_factory import make_settings
from tests.test_accuracy_check_db import PageImages, Reads, model_page, old_checks, start
from tests.test_api_support import CSRF, Api
from tests.test_dynamic_reading import ledger
from tests.test_gemini_ocr import (
    MODEL,
    FakeCache,
    FakeClient,
    FakeFinder,
    FakeRapidOcr,
    line,
    page,
    reply,
    value,
)
from tests.test_isolation_ai_settings import platform, set_org
from tests.test_model_choice_pipeline import _Reading
from tests.test_operator_models_db import _signed_in
from tests.test_pipeline_gemini import SCANNED, TOTAL, FakeChecker, ScriptedReader, beat, page_read
from tests.test_pipeline_gemini import rig as gemini_rig
from tests.test_scan_cost_settings_db import scan_model

__all__ = ["old_checks", "platform"]  # fixtures, used by name
KEY = "test-mistral-key"  # made up
OCR_4, OCR_3 = "mistral-ocr-4-1", "mistral-ocr-2512"
BY_4, BY_3 = f"mistral/{OCR_4}", f"mistral/{OCR_3}"
CUSTOMER = "11111111-1111-1111-1111-111111111111"
DOCUMENT = "22222222-2222-2222-2222-222222222222"
OTHER_DOCUMENT = "33333333-3333-3333-3333-333333333333"
W, H = 1000, 2000  # test_gemini_ocr.page()'s size; the replies below say the same dimensions
READY = make_settings(ocr_engine="gemini", mistral_api_key=KEY, mistral_zdr_approved=True)


def mistral_reply(*texts: str, average: float = 0.95) -> dict[str, Any]:
    """A Mistral reply page: one single-line text block a line, 100 px apart."""
    blocks = [
        {
            "type": "text",
            "content": text,
            "top_left_x": 100,
            "top_left_y": 100 + 100 * index,
            "bottom_right_x": 500,
            "bottom_right_y": 150 + 100 * index,
        }
        for index, text in enumerate(texts)
    ]
    return {
        "markdown": "\n\n".join(texts),
        "dimensions": {"dpi": 200, "width": W, "height": H},
        "blocks": blocks,
        "confidence_scores": {"average_page_confidence_score": average, "word_confidence_scores": []},
    }


Outcome = tuple[dict[str, Any], int] | Exception


class FakeMistral(MistralClient):
    """The Mistral client with each call scripted: (page, pages processed) or an exception. Notes
    each call's model and seconds."""

    def __init__(self, *script: Outcome) -> None:
        super().__init__(KEY, url="http://127.0.0.1:9/v1/ocr")
        self.script = list(script)
        self.calls: list[tuple[str, float]] = []
        self._calls_lock = threading.Lock()

    def read(self, png: bytes, *, model: str, seconds: float) -> tuple[dict[str, Any], int]:
        with self._calls_lock:
            self.calls.append((model, seconds))
            outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Rig:
    """The scan reader with a Mistral client (None: Mistral not set up on this server), a Gemini
    client that must never be called unless scripted, a found box finder and a local reader."""

    def __init__(self, mistral: FakeMistral | None, *gemini: Any, **settings: Any) -> None:
        self.mistral = mistral
        self.client = FakeClient(*gemini)
        self.local = FakeRapidOcr()
        self.engine = GeminiEngine(
            make_settings(**{"ocr_engine": "gemini", **settings}),
            client=self.client,
            pool=FakeFinder(),
            local=lambda: as_page_engine(self.local),
            mistral=mistral,
        )

    def read(self, pages: DocumentPages, page_no: int = 1, image: Image.Image | None = None) -> PageRead:
        with self.engine.document(pages):
            return self.engine.read_page(image or page(), page_no=page_no)


def on_mistral(cache: FakeCache, model: str = OCR_4, **more: Any) -> DocumentPages:
    return DocumentPages(
        customer_id=CUSTOMER,
        document_id=DOCUMENT,
        cache=cache,
        scan_reader="mistral",
        mistral_model=model,
        **more,
    )


def on_gemini(cache: FakeCache, **more: Any) -> DocumentPages:
    return DocumentPages(customer_id=CUSTOMER, document_id=OTHER_DOCUMENT, cache=cache, **more)


def texts(read: PageRead) -> list[str]:
    return [item.text for item in read.lines]


def codes(caplog: pytest.LogCaptureFixture) -> list[Any]:
    return [getattr(record, "code", None) for record in caplog.records]


@pytest.fixture
def no_jitter() -> Iterator[None]:
    """The worker's 0-20% jitter on a Mistral wait left out, so the wait is exact."""
    with mock.patch("kaagaz.jobs.worker.random.uniform", return_value=1.0):
        yield


# --- who reads --------------------------------------------------------------------------------


def test_mistral_org_reads_with_mistral() -> None:
    rig = Rig(
        FakeMistral((mistral_reply("INVOICE 1042"), 1)), ocr_timeout_seconds=60, ocr_seconds_per_page=60
    )
    read = rig.read(on_mistral(FakeCache(), page_count=1))
    assert texts(read) == ["INVOICE 1042"]
    assert (read.read_by, read.read_thinking, read.read_resolution) == (BY_4, "none", "page")
    # Changed on purpose (review, 2 Nov 2026): the page's whole time left; the client caps it.
    assert rig.mistral is not None and rig.mistral.calls == [(OCR_4, 60)]
    assert (rig.client.calls, rig.local.pages) == ([], 0)


def test_mistral_is_given_only_the_pages_time_left() -> None:
    rig = Rig(FakeMistral((mistral_reply("A"), 1)), ocr_timeout_seconds=60, ocr_seconds_per_page=60)
    pages = on_mistral(FakeCache(), page_count=1)
    with rig.engine.document(pages):
        rig.client.clock += 50  # 10 s of the document's 60 left
        rig.engine.read_page(page(), page_no=1)
    assert rig.mistral is not None
    [(model, seconds)] = rig.mistral.calls
    assert model == OCR_4 and seconds == pytest.approx(10.0)


def test_gemini_org_never_calls_mistral() -> None:
    rig = Rig(FakeMistral(), reply(value(line("INVOICE 1042"))))
    read = rig.read(on_gemini(FakeCache()))
    assert read.read_by == MODEL and texts(read) == ["INVOICE 1042"]
    assert rig.mistral is not None and rig.mistral.calls == []


def test_reader_follows_each_documents_organisation() -> None:
    mistral = FakeMistral((mistral_reply("A one"), 1), (mistral_reply("A two"), 1))
    rig = Rig(mistral, reply(value(line("B one"))), reply(value(line("B two"))))
    cache = FakeCache()
    reads = [
        rig.read(on_mistral(cache), page_no=1),
        rig.read(on_gemini(cache), page_no=1),
        rig.read(on_mistral(cache), page_no=2, image=page(colour=(250, 250, 250))),
        rig.read(on_gemini(cache), page_no=2, image=page(colour=(250, 250, 250))),
    ]
    assert [(read.read_by, texts(read)) for read in reads] == [
        (BY_4, ["A one"]),
        (MODEL, ["B one"]),
        (BY_4, ["A two"]),
        (MODEL, ["B two"]),
    ]
    assert len(mistral.calls) == 2 and len(rig.client.calls) == 2


# --- when Mistral can't read ------------------------------------------------------------------


def test_mistral_outage_never_calls_gemini() -> None:
    outages = [MistralUnavailable("mistral") for _ in range(4)]
    rig = Rig(FakeMistral(*outages, MistralUnavailable("mistral_key")), reader_outage_fallback_minutes=1)
    for minutes in (0, 5, 60, 600):  # long past READER_OUTAGE_FALLBACK_MINUTES: still no fallback
        rig.client.clock += 60 * minutes
        with pytest.raises(BackendUnavailable) as waited:
            rig.read(on_mistral(FakeCache()))
        assert str(waited.value) == "mistral" and waited.value.__cause__ is None
    with pytest.raises(BackendUnavailable) as key:
        rig.read(on_mistral(FakeCache()))
    assert str(key.value) == "mistral_key"
    assert (rig.client.calls, rig.local.pages) == ([], 0)


def _worker(error: Exception) -> tuple[Worker, LeaseQueue, FakeClock, list[Job]]:
    """A worker over the lease queue with two jobs: A's handler raises ``error``, B's reads."""
    clock = FakeClock()
    queue = LeaseQueue(clock, lease_seconds=60.0, max_attempts=6)
    mistral_job, gemini_job = Job(CUSTOMER, DOCUMENT), Job(CUSTOMER, OTHER_DOCUMENT)
    queue.enqueue(mistral_job)
    queue.enqueue(gemini_job)
    ran: list[Job] = []

    def handler(job: Job, _beat: Any) -> None:
        ran.append(job)
        if job == mistral_job:
            raise error

    return Worker(queue, handler, max_attempts=3, on_exhausted=lambda job: None), queue, clock, ran


def test_no_credit_defers_only_mistral_jobs(no_jitter: None) -> None:
    worker, queue, clock, ran = _worker(BackendUnavailable("mistral_key"))
    assert worker.run_once() and worker.run_once()  # neither raises: nothing pauses the loop
    mistral_job, gemini_job = ran
    assert gemini_job != mistral_job
    assert queue.deferred == [(mistral_job, 300)]  # the Gemini organisation's job was read
    clock.advance(299)
    assert queue.receive() is None
    clock.advance(1)
    delivery = queue.receive()
    assert delivery is not None and delivery.job == mistral_job
    assert delivery.attempt == 1  # the wait cost no try


def test_worker_not_paused_by_mistral(no_jitter: None) -> None:
    worker, queue, clock, ran = _worker(BackendUnavailable("mistral"))
    assert worker.run_once() is True  # no BackendUnavailable goes up, so the loop never pauses
    assert worker.run_once() is True
    assert len(ran) == 2 and queue.deferred == [(ran[0], 60)]
    clock.advance(60)
    delivery = queue.receive()
    assert delivery is not None and delivery.attempt == 1
    # Gemini's own waits are as before: an outage goes up (the loop pauses), uncounted.
    gemini, _queue, _clock, _ran = _worker(BackendUnavailable("gemini"))
    with pytest.raises(BackendUnavailable):
        gemini.run_once()


def test_not_set_up_later_waits_never_gemini() -> None:
    rig = Rig(None)  # saved as Mistral, then its key or ZDR went from this server
    with pytest.raises(BackendUnavailable) as waited:
        rig.read(on_mistral(FakeCache()))
    # Changed on purpose (review, 2 Nov 2026): its own name, not the key's.
    assert str(waited.value) == "mistral_setup"
    with pytest.raises(BackendUnavailable, match=r"^mistral_setup$"):
        rig.engine.mistral_ready()  # before the document's pages are drawn, too
    assert (rig.client.calls, rig.local.pages) == ([], 0)
    with (
        mock.patch.object(gemini_ocr, "process_gate"),
        mock.patch.object(gemini_ocr, "OpenRouterClient"),
        mock.patch.object(mistral_reader, "MistralClient") as client,
    ):
        wiring.build_gemini_engine(
            make_settings(ocr_engine="gemini", mistral_api_key=KEY), pool=RapidOcrPool(1)
        )
        client.assert_not_called()  # no ZDR confirmed: no client
        wiring.build_gemini_engine(READY, pool=RapidOcrPool(1))
        client.assert_called_once_with(KEY)


# --- the free paths stay free -----------------------------------------------------------------


def test_text_layer_and_routing_still_free() -> None:
    # Local mode: every page by the local reader, no call.
    rig = Rig(FakeMistral())
    read = rig.read(on_mistral(FakeCache(), local=True))
    assert read.read_by == "rapidocr" and rig.local.pages == 1
    # #384: a page the local read will do for stays local; one whose regions the model would
    # read is read whole by Mistral (its regions call is Gemini's), never by Gemini.
    routed: dict[int, tuple[str, str]] = {}
    pages = on_mistral(FakeCache(), route_pages=True, routed=routed)
    with mock.patch.object(gemini_ocr, "route_page", return_value=PageRoute(LOCAL, "clean")):
        assert rig.read(pages).read_by == "rapidocr"
    mistral = FakeMistral((mistral_reply("WHOLE PAGE"), 1))
    rig = Rig(mistral)
    with mock.patch.object(gemini_ocr, "route_page", return_value=PageRoute(REGIONS, "few_unsure", (0,))):
        assert rig.read(pages).read_by == BY_4
    assert routed[1][0] == "model" and len(mistral.calls) == 1 and rig.client.calls == []
    # The text layer: a typed PDF page never reaches the scan reader.
    rig = Rig(FakeMistral())
    socket = build_reader_socket(make_settings(), rig.engine)
    typed = make_pdf(
        [
            Page(
                texts=[
                    Text(72, 700 - 20 * row, f"Invoice 1042 line {row} total 1,180.00") for row in range(20)
                ]
            )
        ]
    )
    with rig.engine.document(on_mistral(FakeCache())):
        spans = socket.read(sniff.PDF, typed)
    assert spans and {span.origin for span in spans} == {"text_layer"}
    assert rig.mistral is not None and rig.mistral.calls == [] and rig.client.calls == []


def test_no_careful_or_ensemble_for_mistral() -> None:
    hard = mistral_reply("Total 1,180.00", average=0.5)  # a poor page: Careful's reason on Gemini
    mistral = FakeMistral((hard, 1), (hard, 1))
    rig = Rig(mistral)
    careful = (MODEL, "google-vertex")
    inline = on_mistral(
        FakeCache(),
        careful=careful,
        memo={},
        inline_careful=True,
        page_count=1,
        escalated={},
        others=(("google/other", "google-vertex"),),
    )
    read = rig.read(inline)
    assert read.read_by == BY_4 and read.others == () and inline.escalated == {}
    again = on_mistral(
        FakeCache(), read_again=frozenset({1}), read_again_regions={1: ((0.0, 0.0, 1.0, 0.5),)}
    )
    assert rig.read(again).read_by == BY_4  # "Read this page again" for regions: Mistral, whole page
    assert len(mistral.calls) == 2 and rig.client.calls == []


def test_memo_stops_second_call() -> None:
    mistral = FakeMistral((mistral_reply("Total 1,180.00", average=0.5), 1))
    rig = Rig(mistral)
    memo: dict[tuple[int, str], PageRead] = {}
    first = rig.read(on_mistral(FakeCache(), careful=(MODEL, "google-vertex"), memo=memo))
    assert memo == {(1, pixels_sha256(page())): first}
    second = on_mistral(FakeCache(), careful=(MODEL, "google-vertex"), memo=memo, escalate=frozenset({1}))
    assert rig.read(second) is first  # Dynamic's second reading: no call, never Careful
    assert len(mistral.calls) == 1 and rig.client.calls == []


# --- the page cache and the ledger ------------------------------------------------------------


def test_cache_keyed_by_reader_and_read_again_asks_again() -> None:
    mistral = FakeMistral(
        (mistral_reply("first"), 1), (mistral_reply("again"), 1), (mistral_reply("ocr 3"), 1)
    )
    rig = Rig(mistral)
    cache = FakeCache()
    sha = pixels_sha256(page())
    gemini_key = PageKey(CUSTOMER, DOCUMENT, 1, sha, MODEL, "g3")
    cache.rows[gemini_key] = (value(line("read by Gemini")), 10, 20)  # never a Mistral read
    mistral_key = PageKey(CUSTOMER, DOCUMENT, 1, sha, BY_4, "mistral-1")
    assert texts(rig.read(on_mistral(cache))) == ["first"]
    assert cache.puts == [mistral_key] and gemini_key not in cache.gets
    hit = rig.read(on_mistral(cache))
    assert (texts(hit), hit.cached, len(mistral.calls)) == (["first"], True, 1)
    again = rig.read(on_mistral(cache, read_again=frozenset({1})))  # skips the cache, overwrites it
    assert texts(again) == ["again"] and len(mistral.calls) == 2
    assert texts(rig.read(on_mistral(cache))) == ["again"]
    other_model = rig.read(on_mistral(cache, model=OCR_3))  # another Mistral model is another read
    assert (texts(other_model), other_model.read_by, len(mistral.calls)) == (["ocr 3"], BY_3, 3)


def test_cached_hit_no_record_page() -> None:
    cache = FakeCache()
    Rig(FakeMistral((mistral_reply("first"), 1))).read(on_mistral(cache))
    with ledger() as kept:
        Rig(FakeMistral()).read(on_mistral(cache))
    [row] = kept.rows
    assert (row.model, row.provider_name, row.cached, row.calls, row.pages, row.cost_usd) == (
        BY_4,
        "mistral",
        True,
        1,
        0,
        0.0,
    )
    assert (row.thinking, row.resolution) == ("none", "page")


def test_ledger_fixed_cost_none_page_pair() -> None:
    with ledger() as kept:
        Rig(FakeMistral((mistral_reply("two pages billed"), 2))).read(on_mistral(FakeCache()))
        Rig(FakeMistral((mistral_reply("ocr 3"), 1))).read(on_mistral(FakeCache(), model=OCR_3))
    rows = {row.model: row for row in kept.rows}
    assert set(rows) == {BY_4, BY_3}
    four, three = rows[BY_4], rows[BY_3]
    assert (four.provider_name, four.cached, four.calls, four.pages) == ("mistral", False, 1, 1)
    assert (four.input_tokens, four.output_tokens, four.reasoning_tokens) == (0, 0, 0)
    assert (four.thinking, four.resolution, four.kind) == ("none", "page", "scan")
    assert four.cost_usd == pytest.approx(2 * 0.004) and three.cost_usd == pytest.approx(0.002)


def test_bad_reply_billed_then_reader_refused() -> None:
    cache = FakeCache()
    with ledger() as kept:
        refused = Rig(FakeMistral(BadReply("bad_reply"))).read(on_mistral(cache))  # a 400: no 200
    assert (refused.lines, refused.issues, refused.read_by) == ((), (READER_REFUSED,), BY_4)
    assert kept.rows == [] and cache.puts == []
    with ledger() as kept:
        broken = Rig(FakeMistral(({"markdown": 5}, 1))).read(on_mistral(cache))  # a 200, bad shape
    assert (broken.lines, broken.issues) == ((), (READER_REFUSED,))
    [row] = kept.rows
    assert (row.model, row.calls, row.pages) == (BY_4, 1, 1) and row.cost_usd == pytest.approx(0.004)
    assert cache.puts == []  # never cached: "Read this page again" (or the next try) asks again
    mistral = FakeMistral((mistral_reply("read"), 1))
    assert texts(Rig(mistral).read(on_mistral(cache))) == ["read"]


# --- the pipeline and the document's words ----------------------------------------------------


def test_the_pipeline_passes_the_reader_and_checks_mistrals_pages() -> None:
    lead = models.Agent(MODEL, "google-vertex", "Gemini")
    reading = models.Reading("single", lead, scan_reader="mistral", mistral_model=OCR_3)
    job_reading = _Reading(None, MODEL)
    job_reading.reading = dataclasses.replace(job_reading.reading, reading=reading)
    reader, checker = ScriptedReader({1: page_read(TOTAL, read_by=BY_3)}), FakeChecker()
    system = gemini_rig(reader, checker=checker, job_reading=job_reading)
    system.run(make_pdf([SCANNED]))
    (opened,) = reader.opened
    assert (opened.scan_reader, opened.mistral_model) == ("mistral", OCR_3)
    assert checker.crops  # a page Mistral read is the run's model's: its values are checked
    assert opened.model == BY_3  # review, 2 Nov 2026: the run names the Mistral model
    # Dynamic's Careful is Gemini: a Mistral document never has its memo or Careful.
    dynamic = models.Reading("dynamic", lead, careful=lead, scan_reader="mistral", mistral_model=OCR_3)
    job_reading.reading = dataclasses.replace(job_reading.reading, reading=dynamic)
    reader = ScriptedReader({1: page_read(TOTAL, read_by=BY_3)})
    gemini_rig(reader, checker=FakeChecker(), job_reading=job_reading).run(make_pdf([SCANNED]))
    (opened,) = reader.opened
    assert (opened.model, opened.careful, opened.memo) == (BY_3, None, None)


def test_a_mistral_document_names_its_model_in_the_engine_version() -> None:
    rig = Rig(FakeMistral((mistral_reply("A"), 1)), reply(value(line("B"))))
    rig.read(on_mistral(FakeCache(), model=OCR_3))
    assert rig.engine.version.startswith(f"{BY_3};")
    rig.read(on_gemini(FakeCache()))
    assert rig.engine.version.startswith(f"{MODEL};")


# --- review fixes (2 Nov 2026) ----------------------------------------------------------------


def test_a_lost_200_is_billed_then_waits() -> None:
    with ledger() as kept, pytest.raises(BackendUnavailable) as waited:
        Rig(FakeMistral(MistralUnavailable("mistral", billed=True))).read(on_mistral(FakeCache()))
    assert str(waited.value) == "mistral"
    [row] = kept.rows
    assert (row.model, row.calls, row.pages) == (BY_4, 1, 1) and row.cost_usd == pytest.approx(0.004)
    with ledger() as kept, pytest.raises(BackendUnavailable):
        Rig(FakeMistral(MistralUnavailable("mistral"))).read(on_mistral(FakeCache()))  # no 200
    assert kept.rows == []


def test_refused_pages_are_logged_with_their_ids(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    rig = Rig(FakeMistral(BadReply("refused_request"), ({"markdown": 5}, 1)))
    for _ in range(2):
        assert rig.read(on_mistral(FakeCache())).issues == (READER_REFUSED,)
    refused = [record for record in caplog.records if record.getMessage() == "page refused by the reader"]
    assert [
        tuple(getattr(record, name) for name in ("code", "customer_id", "document_id", "page_no"))
        for record in refused
    ] == [("refused_request", CUSTOMER, DOCUMENT, 1), ("bad_reply", CUSTOMER, DOCUMENT, 1)]


def test_no_time_left_refuses_the_page_and_never_waits(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    mistral = FakeMistral()
    rig = Rig(mistral, ocr_timeout_seconds=60, ocr_seconds_per_page=60)
    cache = FakeCache()
    with ledger() as kept, rig.engine.document(on_mistral(cache, page_count=1)):
        rig.client.clock += 60  # the document's time is spent
        read = rig.engine.read_page(page(), page_no=1)
    assert (read.lines, read.issues, read.read_by) == ((), (READER_REFUSED,), BY_4)
    assert mistral.calls == [] and kept.rows == [] and cache.puts == []  # nothing sent or billed
    assert "mistral_no_time" in codes(caplog)


def test_a_stale_cached_page_is_logged_then_read_again(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    cache = FakeCache()
    cache.rows[PageKey(CUSTOMER, DOCUMENT, 1, pixels_sha256(page()), BY_4, "mistral-1")] = (
        {"markdown": 5},
        0,
        0,
    )
    mistral = FakeMistral((mistral_reply("fresh"), 1))
    assert texts(Rig(mistral).read(on_mistral(cache))) == ["fresh"]
    assert len(mistral.calls) == 1 and codes(caplog).count("cache_stale") == 1


class _Down:
    """An opener for a Mistral that never answers."""

    def open(self, request: urllib.request.Request, timeout: float) -> Any:
        raise TimeoutError


def test_switched_off_mistral_waits_before_any_page_is_drawn(no_jitter: None) -> None:
    """Review B8: while Mistral is switched off, a document on it waits before its pages are
    drawn, for at least the switch-off; Gemini's documents never ask."""
    client = MistralClient(KEY, opener=cast(urllib.request.OpenerDirector, _Down()), now=lambda: 1000.0)
    engine = GeminiEngine(READY, client=FakeClient(), pool=FakeFinder(), mistral=client)
    engine.mistral_ready()  # on: nothing to wait for
    for _ in range(3):
        with pytest.raises(BackendUnavailable), engine.document(on_mistral(FakeCache())):
            engine.read_page(page(), page_no=1)
    with pytest.raises(BackendUnavailable) as off:
        engine.mistral_ready()
    assert (str(off.value), off.value.seconds) == ("mistral", 60.0)

    lead = models.Agent(MODEL, "google-vertex", "Gemini")
    job_reading = _Reading(None, MODEL)
    asked: list[str] = []

    def mistral_ready() -> None:
        asked.append("asked")
        raise off.value

    for reader_name, waits in (("mistral", True), ("openrouter", False)):
        reading = models.Reading("single", lead, scan_reader=reader_name, mistral_model=OCR_3)
        job_reading.reading = dataclasses.replace(job_reading.reading, reading=reading)
        reader = ScriptedReader({1: page_read(TOTAL, read_by=BY_3 if waits else MODEL)})
        system = gemini_rig(
            reader, checker=FakeChecker(), job_reading=job_reading, mistral_ready=mistral_ready
        )
        job = system.job(make_pdf([SCANNED]))
        if waits:
            with pytest.raises(BackendUnavailable) as waited:
                system.processor(job, beat)
            assert waited.value is off.value
            assert reader.opened == [] and system.pages(job) == []  # nothing drawn or read
            assert system.totals(job).error_code == "mistral_unavailable"
        else:
            system.processor(job, beat)
            assert len(reader.opened) == 1
    assert asked == ["asked"]


def test_the_worker_waits_out_the_switch_off_with_jitter() -> None:
    long, short = BackendUnavailable("mistral"), BackendUnavailable("mistral")
    long.seconds = 200.0
    for error, jitter, expected in ((long, 1.0, 200), (long, 1.2, 240), (short, 1.0, 60), (short, 1.2, 72)):
        worker, queue, _clock, ran = _worker(error)
        with mock.patch("kaagaz.jobs.worker.random.uniform", return_value=jitter) as uniform:
            assert worker.run_once()
        uniform.assert_called_once_with(1.0, 1.2)
        assert queue.deferred == [(ran[0], expected)]


def test_not_set_up_waits_as_the_key_does(no_jitter: None) -> None:
    worker, queue, _clock, ran = _worker(BackendUnavailable("mistral_setup"))
    assert worker.run_once() and worker.run_once()  # never paused
    assert queue.deferred == [(ran[0], 300)]


@pytest.mark.parametrize(
    ("code", "words"),
    [
        ("mistral_key", views.READER_STOPPED),
        ("mistral_setup", views.READER_STOPPED),
        ("mistral", views.READER_WAITING),
    ],
)
def test_document_shows_stopped_words(code: str, words: str) -> None:
    system = gemini_rig(ScriptedReader({1: BackendUnavailable(code)}), checker=FakeChecker())
    job = system.job(make_pdf([SCANNED]))
    with pytest.raises(BackendUnavailable):
        system.processor(job, beat)
    run_code = f"{code}_unavailable"
    assert system.totals(job).error_code == run_code
    assert run_code in pipeline._READER_WAITS
    assert system.progress.waiting[(job.customer_id, job.document_id)] == "reader"
    message = views.status_message(
        "received",
        None,
        detected_type="pdf",
        max_pages=100,
        progress={"step": "waiting"},
        waiting_code=run_code,
    )
    assert message == words


# --- the accuracy check and the estimate ------------------------------------------------------


class _Engine:
    def __init__(self, read: PageRead | Exception) -> None:
        self.page = read

    name, version = "gemini", "x"

    def read(self, image: Image.Image) -> list[Any]:
        raise AssertionError("numbered pages only")

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        if isinstance(self.page, Exception):
            raise self.page
        return self.page


def test_accuracy_your_side_uses_mistral() -> None:
    lead = models.Agent(MODEL, "google-vertex", "Gemini")
    reading = models.Reading("single", lead, thinking="low", scan_reader="mistral", mistral_model=OCR_3)
    pages = accuracy.document_pages(reading, CUSTOMER, DOCUMENT)
    assert (pages.scan_reader, pages.mistral_model) == ("mistral", OCR_3)
    read = PageRead(
        page_no=1,
        lines=(),
        quality="good",
        handwritten=False,
        scripts=(),
        issues=(),
        read_by=BY_3,
        read_thinking="none",
        read_resolution="page",
    )
    at = (CUSTOMER, DOCUMENT, 1, Image.new("RGB", (10, 10)))
    assert accuracy._read(_Engine(read), reading, *at, settings=READY) is read
    gemini_read = dataclasses.replace(read, read_by=MODEL, read_thinking="low", read_resolution="high")
    assert accuracy._read(_Engine(gemini_read), reading, *at, settings=READY) is None
    # Review B6: Mistral down or not set up fails the check (its reason goes up), never a page
    # quietly skipped; a Gemini outage still skips the page.
    for code in ("mistral", "mistral_key", "mistral_setup"):
        with pytest.raises(BackendUnavailable, match=rf"^{code}$"):
            accuracy._read(_Engine(BackendUnavailable(code)), reading, *at, settings=READY)
    gemini = models.Reading("single", lead, thinking="low")
    assert accuracy._read(_Engine(BackendUnavailable("gemini")), gemini, *at, settings=READY) is None


@pytest.mark.db
def test_scan_cost_mistral_fixed_price(db: Database, platform: str, old_checks: None) -> None:
    api = Api(db, settings=READY)
    operator, client = _signed_in(api)
    lead = scan_model(db)
    customer = api.customer("Mistral Reads")[0]
    choice = ops.AiChoice([lead], lead, scan_reader="mistral", mistral_model=OCR_3)
    assert set_org(db, platform, customer, choice).code == "done"
    images = PageImages()
    for _ in range(2):
        model_page(db, images, customer, read_by=BY_3)

    # The console sends its whole draft: the organisation's own reader and model.
    draft = {"scan_reader": "mistral", "mistral_model": OCR_3}
    estimate = client.post(
        "/api/operator/scan-cost", json={**draft, "customer_id": str(customer)}, headers=CSRF
    )
    assert estimate.status_code == 200, estimate.text
    body = estimate.json()
    assert (body["fixed_price"], body["fixed_usd_per_page"]) == (True, 0.002)
    assert body["first_read"]["model"] == BY_3
    assert (body["first_read"]["thinking"], body["first_read"]["resolution"]) == ("none", "page")
    assert body["re_read"] is None
    gemini = client.post(
        "/api/operator/scan-cost",
        json={"customer_id": str(customer), "scan_reader": "openrouter"},
        headers=CSRF,
    ).json()
    assert (gemini["fixed_price"], gemini["fixed_usd_per_page"], gemini["first_read"]["model"]) == (
        False,
        None,
        lead,
    )

    # The accuracy check on Mistral's settings: "your settings" read by Mistral, max by Gemini.
    started = start(api, operator, client, customer, pages=20, **draft)
    assert started.status_code == 202, started.text
    reads = Reads()
    mistral = FakeMistral(*[(mistral_reply("Bill to Sharma Traders Total ₹8,000"), 1)] * 2)
    engine = GeminiEngine(
        READY,
        client=reads,
        pool=FakeFinder(),
        local=lambda: as_page_engine(FakeRapidOcr()),
        mistral=mistral,
    )
    AccuracyChecks(db, images, engine, settings=READY)(api.queue.jobs[-1], lambda: None)
    assert [model for model, _seconds in mistral.calls] == [OCR_3, OCR_3]
    assert {call[0] for call in reads.calls} == {lead}  # only the max side, Gemini at max
    done = client.get(f"/api/operator/orgs/{customer}/accuracy-check/latest").json()
    assert done["status"] == "done" and done["settings"]["model"] == BY_3
    assert done["pages_checked"] == 2
    again = client.post(
        "/api/operator/scan-cost", json={**draft, "customer_id": str(customer)}, headers=CSRF
    ).json()
    assert again["accuracy"] == done  # found at the same settings: asked[0] is Mistral's
    # Review B10: stored at Mistral's one setting, so a Gemini level in the draft never hides it.
    assert (done["settings"]["scan_thinking"], done["settings"]["scan_resolution"]) == ("none", "page")
    other = client.post(
        "/api/operator/scan-cost",
        json={**draft, "customer_id": str(customer), "scan_thinking": "high", "scan_resolution": "low"},
        headers=CSRF,
    ).json()
    assert other["accuracy"] == done


@pytest.mark.db
def test_accuracy_check_on_mistral_start_and_fail(
    db: Database, platform: str, old_checks: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Review B10: a Mistral check skips the Gemini level test and is refused while Mistral is
    not set up; review B6: Mistral not reading during the check fails it with its reason."""
    caplog.set_level(logging.INFO)
    api = Api(db, settings=READY)
    operator, client = _signed_in(api)
    lead = scan_model(db, efforts=("low",))  # takes no "high": a Gemini check at high is refused
    customer = api.customer("Mistral Check")[0]
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead, scan_reader="mistral")).code == "done"
    images = PageImages()
    model_page(db, images, customer, read_by=BY_4)
    refused = start(api, operator, client, customer, pages=20, scan_reader="openrouter", scan_thinking="high")
    assert (refused.status_code, refused.json()["error"]["code"]) == (400, "level_not_supported")
    started = start(api, operator, client, customer, pages=20, scan_reader="mistral", scan_thinking="high")
    assert started.status_code == 202, started.text
    not_set_up = GeminiEngine(
        READY, client=Reads(), pool=FakeFinder(), local=lambda: as_page_engine(FakeRapidOcr())
    )
    with pytest.raises(BackendUnavailable, match=r"^mistral_setup$"):
        AccuracyChecks(db, images, not_set_up, settings=READY)(api.queue.jobs[-1], lambda: None)
    failed = client.get(f"/api/operator/orgs/{customer}/accuracy-check/latest").json()
    assert (failed["status"], failed["settings"]["model"]) == ("failed", BY_4)
    assert "mistral_setup_unavailable" in codes(caplog)

    off = Api(db, settings=make_settings(ocr_engine="gemini"))
    operator, client = _signed_in(off)
    refused = start(off, operator, client, customer, pages=20, scan_reader="mistral")
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "mistral_not_set_up")
