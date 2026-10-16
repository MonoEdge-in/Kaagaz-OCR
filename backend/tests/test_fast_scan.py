"""Fast scan, Phase 1 (council 16 Oct 2026; Tushar's decisions of the same day): the calls a
page already makes run at the same time (Ensemble, a cut-off page's halves, the box finding),
Careful reads on the page's own thread (early on a short document) at High thinking, the
page pool refills when any page ends and beats the heartbeat while it waits, the document
is ready before its vectors (made in parallel through the one gate), and the time left
counts a model's pages read at once. tests/test_replay_scan.py proves the saved output is
unchanged."""

import io
import itertools
import threading
import time
import unittest
from dataclasses import replace
from typing import Any, cast
from unittest import mock

from PIL import Image
from sqlalchemy.exc import OperationalError

from kaagaz import cli
from kaagaz.ai import openrouter
from kaagaz.ai.replies import ModelOutputError
from kaagaz.ingestion import sniff
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading.ocr import DocumentPages, PageRead, careful_pages, worst_first
from kaagaz.reading.socket import PagePool, PagesInOrder, waiting
from kaagaz.scanning.gate import DocumentStatus
from kaagaz.settings import Settings, SettingsError
from tests import test_dynamic_reading
from tests.fakes import FakeEmbedder, ModelDown, PresetSocket
from tests.pdf_builder import make_pdf
from tests.support import PDF_BYTES
from tests.test_dynamic_reading import CAREFUL, STANDARD, ModelClient, _reading_rig, engine, ledger
from tests.test_ensemble_scan import MAIN, SECOND, THIRD, ensemble_pages
from tests.test_gemini_ocr import (
    CUSTOMER,
    DOCUMENT,
    FakeCache,
    FakeClient,
    FakeFinder,
    line,
    page,
    reply,
    value,
)
from tests.test_openrouter_client import _client
from tests.test_pipeline import CSV, build
from tests.test_pipeline import CUSTOMER as PIPELINE_CUSTOMER
from tests.test_pipeline_gemini import SHARP, FakeChecker
from tests.test_pipeline_runs import rig as runs_rig
from tests.test_pipeline_scans import INVOICE, two_pages
from tests.test_settings import shipped

WAIT = 3.0  # seconds a test waits for something that must happen at once
CLEAN = reply(value(line("Total 1,180.00")))
UNCLEAR = reply(value(line("Total 1,18O.00", unsure=("1,18O.00",)), quality="fair"))


def slow(answer: Any, seconds: float = 0.3) -> Any:
    """An answer that takes ``seconds``, as a model call does."""

    def respond(image: Image.Image) -> Any:
        time.sleep(seconds)
        return answer

    return respond


def inline_pages(page_count: int, cache: FakeCache | None = None, thinking: str = "low") -> DocumentPages:
    return DocumentPages(
        customer_id=CUSTOMER,
        document_id=DOCUMENT,
        cache=cache or FakeCache(),
        model=STANDARD,
        provider="google-vertex",
        thinking=thinking,
        careful=(CAREFUL, "google-vertex"),
        memo={},
        inline_careful=True,
        page_count=page_count,
        escalated={},
    )


class CarefulTest(unittest.TestCase):
    """Careful on the page's thread, at High thinking; early on a short document."""

    def test_the_first_read_is_at_the_documents_level_and_careful_always_at_high(self) -> None:
        client = ModelClient({STANDARD: UNCLEAR, CAREFUL: CLEAN})
        reader = engine(client, careful_early_max_pages=0)
        with reader.document(inline_pages(3, thinking="low")):
            read = reader.read_page(page(), page_no=1)
        self.assertEqual(client.calls, [(STANDARD, "low"), (CAREFUL, "high")])  # OCR_RETRY_THINKING_LEVEL
        self.assertEqual(read.read_by, CAREFUL)

    def test_inline_careful_chooses_careful_pages_pages_with_the_same_notes(self) -> None:
        answers = {
            1: reply(value(line("a"), quality="poor", handwritten=True)),
            2: CLEAN,
            3: reply(value(line("b", unsure=("b",)), quality="fair")),
            4: reply(value(line("c"), handwritten=True)),
        }
        heights = {1000 + n: n for n in answers}  # each page its own height, so its own answer
        client = ModelClient(
            {STANDARD: lambda image: answers[heights[image.height]], CAREFUL: lambda image: CLEAN}
        )
        reader = engine(client, careful_early_max_pages=0)
        pages = inline_pages(4)
        with reader.document(pages):
            for n in (3, 1, 4, 2):  # any thread order
                reader.read_page(page(height=1000 + n), page_no=n)
        assert pages.memo is not None and pages.escalated is not None
        first_reads = {page_no: read for (page_no, _sha), read in pages.memo.items()}
        self.assertEqual(worst_first(pages.escalated), careful_pages(first_reads, lead=STANDARD))
        self.assertEqual([item["page"] for item in worst_first(pages.escalated)[0]], [1, 3, 4])

    def test_the_early_careful_read_starts_with_the_first_read(self) -> None:
        careful_started = threading.Event()
        seen: list[bool] = []

        def first(image: Image.Image) -> Any:
            seen.append(careful_started.wait(WAIT))  # set only if Careful is asked meanwhile
            return UNCLEAR

        def careful(image: Image.Image) -> Any:
            careful_started.set()
            return CLEAN

        reader = engine(ModelClient({STANDARD: first, CAREFUL: careful}))
        with reader.document(inline_pages(1)):
            read = reader.read_page(page(), page_no=1)
        self.assertEqual(seen, [True])
        self.assertEqual(read.read_by, CAREFUL)

    def test_a_dropped_early_read_never_becomes_the_page_or_feeds_a_vote_even_cached(self) -> None:
        careful = reply(value(line("Careful's own words")), 30, 70)
        cache = FakeCache()
        off = engine(ModelClient({STANDARD: CLEAN, CAREFUL: careful}), careful_early_max_pages=0)
        with off.document(inline_pages(1, FakeCache())):
            alone = off.read_page(page(), page_no=1)
        client = ModelClient({STANDARD: CLEAN, CAREFUL: careful})
        reader = engine(client)
        for _ in range(2):  # the second time, both reads come from the page cache
            pages = inline_pages(1, cache)
            with ledger() as kept, reader.document(pages):
                read = reader.read_page(page(), page_no=1)
            self.assertEqual(read.read_by, STANDARD)
            self.assertEqual(read.others, ())
            self.assertEqual(pages.escalated, {})
            if not read.cached:  # the dropped read was billed, as careful, and its tokens kept
                self.assertEqual((read.input_tokens, read.output_tokens), (1700 + 30, 4000 + 70))
                self.assertIn((CAREFUL, "careful"), {(row.model, row.kind) for row in kept.rows})
            # Byte-identical to the read with no early read, but the tokens.
            same = replace(read, input_tokens=alone.input_tokens, output_tokens=alone.output_tokens)
            self.assertEqual(replace(same, cached=False), alone)
        self.assertEqual(sorted(model for model, _level in client.calls), [CAREFUL, STANDARD])

    def test_an_inline_dynamic_document_is_read_through_the_socket_once(self) -> None:
        answers = test_dynamic_reading.PipelineTest.ANSWERS  # its class is not imported: run once
        system, client = _reading_rig(answers, checker=FakeChecker("Total: 1,180.00"))
        socket = system.processor._socket
        with mock.patch.object(socket, "read", wraps=socket.read) as read:
            job = system.run(make_pdf([SHARP]))
        self.assertEqual(read.call_count, 1)
        self.assertEqual(system.totals(job).config["reading"]["escalated"][0]["page"], 1)
        self.assertEqual(sorted(model for model, _level in client.calls), sorted([STANDARD, CAREFUL]))


class SameTimeTest(unittest.TestCase):
    """The calls a page already makes, at the same time, with the same result."""

    def test_ensemble_models_read_at_once_with_the_same_votes(self) -> None:
        answers: dict[str, Any] = {MAIN: CLEAN, SECOND: CLEAN, THIRD: reply(value(line("Total 1,130.00")))}
        quick = engine(ModelClient(answers))
        with quick.document(ensemble_pages()):
            expected = quick.read_page(page(), page_no=1)
        finder = FakeFinder()
        reader = engine(
            ModelClient({model: slow(answer) for model, answer in answers.items()}), finder=finder
        )
        started = time.perf_counter()
        with reader.document(ensemble_pages()):
            read = reader.read_page(page(), page_no=1)
        self.assertLess(time.perf_counter() - started, 0.6)  # one after another: 0.9 s
        self.assertEqual(read, expected)  # the others in the chosen order: the same vote
        self.assertEqual(len(finder.sizes), 1)  # the page's boxes found once for 3 models

    def test_a_cut_off_pages_halves_are_read_at_once(self) -> None:
        def respond(image: Image.Image) -> Any:
            if image.height == 2000:
                return ModelOutputError("output_truncated", input_tokens=5, output_tokens=50)
            time.sleep(0.3)
            return reply(value(line("half")))

        client = FakeClient(respond=respond)
        reader = engine(cast(Any, client))
        started = time.perf_counter()
        with reader.document(DocumentPages(customer_id=CUSTOMER, document_id=DOCUMENT, cache=FakeCache())):
            read = reader.read_page(page(), page_no=1)
        self.assertLess(time.perf_counter() - started, 0.45)  # one after another: 0.6 s
        self.assertEqual(len(client.calls), 3)
        self.assertEqual((read.input_tokens, read.output_tokens), (5 + 2 * 1700, 50 + 2 * 4000))

    def test_a_pages_boxes_are_found_once_for_its_first_and_careful_reads(self) -> None:
        finder = FakeFinder()
        client = ModelClient({STANDARD: UNCLEAR, CAREFUL: CLEAN})
        reader = engine(client, finder=finder)
        with reader.document(inline_pages(1)):
            reader.read_page(page(), page_no=1)
        self.assertEqual(sorted(model for model, _level in client.calls), [CAREFUL, STANDARD])
        self.assertEqual(len(finder.sizes), 1)


class _Pages:
    """A ``PageEngine`` whose page 1 waits (up to WAIT) for page 7 to start; the rest are quick."""

    name, version = "gemini", "test"

    def __init__(self) -> None:
        self.seventh = threading.Event()
        self.waited: bool | None = None

    def read(self, image: Image.Image) -> Any:
        raise AssertionError("pages are read by number")

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        if page_no == 7:
            self.seventh.set()
        if page_no == 1:
            self.waited = self.seventh.wait(WAIT)
        return PageRead(
            page_no=page_no, lines=(), quality="good", handwritten=False, scripts=(), issues=(), read_by="x"
        )


class PagePoolTest(unittest.TestCase):
    def test_one_slow_page_no_longer_holds_the_others_back(self) -> None:
        engine_, finished = _Pages(), []
        with PagesInOrder(engine_, PagePool.threads(6)) as pages:
            for page_no in range(1, 8):
                pages.read(Image.new("RGB", (8, 8)), page_no, lambda read: finished.append(read.page_no))
            pages.drain()
        self.assertTrue(engine_.waited)  # page 7 started while page 1 was still being read
        self.assertEqual(finished, list(range(1, 8)))  # still finished in page order

    def test_the_heartbeat_beats_while_a_page_is_being_read(self) -> None:
        class Sleepy(_Pages):
            def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
                time.sleep(0.4)
                return super().read_page(image, page_no=2)

        beats: list[int] = []
        with (
            mock.patch("kaagaz.reading.socket.WAIT_BEAT_SECONDS", 0.05),
            waiting(lambda: beats.append(1)),
            PagesInOrder(Sleepy(), PagePool.threads(2)) as pages,
        ):
            pages.read(Image.new("RGB", (8, 8)), 1, lambda read: None)
            pages.drain()
        self.assertGreaterEqual(len(beats), 2)


class _Embedder(FakeEmbedder):
    """Notes, when called, the document's status and whether it was filed; then fails with
    ``error`` if given."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__(batches=1)
        self.error, self.system = error, cast(Any, None)
        self.seen: list[tuple[DocumentStatus | None, list[tuple[str, str]]]] = []

    def __call__(self, customer_id: str, document_id: str, **options: Any) -> int:
        self.seen.append(
            (self.system.statuses.get_status(customer_id, document_id), list(self.system.repo.filed))
        )
        if self.error is not None:
            raise self.error
        return super().__call__(customer_id, document_id, **options)


def _indexing(error: Exception | None = None) -> tuple[Any, _Embedder]:
    embedder = _Embedder(error)
    embedder.system = runs_rig(embed=embedder)
    return embedder.system, embedder


class ReadyBeforeIndexTest(unittest.TestCase):
    def test_the_document_is_ready_and_filed_before_its_vectors_are_made(self) -> None:
        system, embedder = _indexing()
        job = system.job(CSV)
        system.processor(job, lambda: None)
        filed = [(job.customer_id, job.document_id)]
        self.assertEqual(embedder.seen, [(DocumentStatus.READY, filed)])  # autofile reads no vector
        (run,) = system.runs_of(job)
        assert run.totals is not None
        self.assertEqual((run.status, run.totals.model_embed), ("ready", "embed-tag"))

    def test_a_failure_while_indexing_leaves_the_document_ready(self) -> None:
        failures: list[Exception] = [
            ModelDown(),
            OperationalError("SELECT 1", {}, Exception("gone")),
            BackendUnavailable("database"),
        ]
        for failure in failures:
            with self.subTest(type(failure).__name__):
                system, _embedder = _indexing(failure)
                job = system.job(CSV)
                with self.assertLogs("kaagaz.pipeline", level="WARNING") as logs:
                    system.processor(job, lambda: None)
                self.assertIs(
                    system.statuses.get_status(job.customer_id, job.document_id), DocumentStatus.READY
                )
                (record,) = [
                    r for r in logs.records if r.getMessage() == "pieces saved without meaning vectors"
                ]
                code = getattr(record, "code", "")
                self.assertIn(code, {"model_down", "OperationalError", "BackendUnavailable"})
                self.assertNotIn("gone", code)

    def test_embeddings_go_16_at_once_through_the_one_gate_in_order(self) -> None:
        """EMBED_FAN_OUT 16 (16 Oct 2026): 16 requests in flight, their starts still spaced by
        the limiter (MODEL_RPM), the vectors in the texts' order."""
        client = _client("http://127.0.0.1:9/api/v1", model_rpm=3000)  # one start every 0.02 s
        lock, slots, flying = threading.Lock(), [], [0, 0]  # flying: now, most at once
        take_slot = client.gate.take_slot

        def booked(deadline: float, model: str) -> float:
            start = take_slot(deadline, model)
            slots.append(start)  # the limiter's own start times, under its lock
            return start

        def send(path: str, body: dict[str, Any], timeout: float, **_options: Any) -> Any:
            with lock:
                flying[0] += 1
                flying[1] = max(flying)
            time.sleep(0.4)
            with lock:
                flying[0] -= 1
            number = float(body["input"][0].split()[-1])
            reply = {"data": [{"embedding": [number, 0.5, 0.25, 1.0], "index": 0}]}
            return openrouter._Reply(200, None, reply)

        with mock.patch.object(client, "_send", send), mock.patch.object(client.gate, "take_slot", booked):
            started = time.perf_counter()
            vectors = client.embed([f"piece {n}" for n in range(16)])
        self.assertEqual(openrouter.EMBED_FAN_OUT, 16)
        self.assertLess(time.perf_counter() - started, 0.95)  # 8 at once: two rounds, over 0.8 s
        self.assertEqual(flying[1], 16)
        slots.sort()
        gaps = [later - earlier for earlier, later in itertools.pairwise(slots)]
        self.assertGreaterEqual(min(gaps), client.gate.interval - 1e-9)  # MODEL_RPM still holds
        self.assertEqual([vector[0] for vector in vectors], [float(n) for n in range(16)])


class PicturesAtOnceTest(unittest.TestCase):
    def test_page_pictures_are_stored_at_the_same_time_and_all_before_reading(self) -> None:
        system = build(socket=PresetSocket({sniff.PDF: INVOICE}, {sniff.PDF: INVOICE}), preparer=two_pages())
        both = threading.Barrier(2, timeout=WAIT)  # one by one, the first never gets past it
        put = system.page_images.put_page_image

        def put_together(customer_id: str, document_id: str, page_no: int, data: bytes) -> None:
            both.wait()
            put(customer_id, document_id, page_no, data)

        system.page_images.put_page_image = put_together  # type: ignore[method-assign]
        doc = system.upload.accept(PIPELINE_CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()
        self.assertIs(system.statuses.get_status(PIPELINE_CUSTOMER, doc), DocumentStatus.READY)
        self.assertEqual(sorted(page for _, _, page in system.page_images.images), [1, 2])
        pages = system.store.pages_for(PIPELINE_CUSTOMER, doc)
        self.assertEqual([p.has_image for p in pages], [True, True])


class SettingsAndCliTest(unittest.TestCase):
    def test_careful_early_max_pages_is_2_unless_set_and_never_below_0(self) -> None:
        self.assertEqual(Settings.from_env(shipped(CAREFUL_EARLY_MAX_PAGES=None)).careful_early_max_pages, 2)
        self.assertEqual(Settings.from_env(shipped(CAREFUL_EARLY_MAX_PAGES="0")).careful_early_max_pages, 0)
        with self.assertRaisesRegex(SettingsError, "CAREFUL_EARLY_MAX_PAGES"):
            Settings.from_env(shipped(CAREFUL_EARLY_MAX_PAGES="-1"))

    def test_the_servers_12_pages_at_once_is_a_valid_setting(self) -> None:
        """Ready first (16 Oct 2026): OCR_PAGE_CONCURRENCY=12 reads 10 pages in one round."""
        self.assertEqual(Settings.from_env(shipped(OCR_PAGE_CONCURRENCY="12")).ocr_page_concurrency, 12)

    def test_scan_times_prints_each_step_in_seconds_and_ids_only(self) -> None:
        from datetime import UTC, datetime, timedelta

        t0 = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
        config = {
            "seconds_preparing": 1.2,
            "seconds_reading": 5.0,
            "seconds_card": 1.5,
            "reading": {"escalated": [{"page": 2, "reasons": ["handwritten"]}]},
        }
        row = {
            "document_id": "d-1",
            "pages_ocr": 7,
            "uploaded_at": t0,
            "started_at": t0 + timedelta(seconds=1),
            "finished_at": t0 + timedelta(seconds=9.5),
            "indexed_at": t0 + timedelta(seconds=14),
            "config": config,
        }
        self.assertEqual(
            cli.scan_times_line(row),
            "document d-1 ocr_pages 7 upload_to_ready 9.5 queued 1.0 run_to_ready 8.5 preparing 1.2"
            " reading 5.0 careful inline(1 pages) card 1.5 map - checker - understood_after_ready -"
            " index_after_ready 4.5",
        )
        # Ready first: ready 4 s into the run, understood 4.5 s more, the run's end after that.
        ready_first = {**row, "config": {**config, "seconds_to_ready": 4.0, "seconds_after_ready": 4.5}}
        self.assertEqual(
            cli.scan_times_line(ready_first),
            "document d-1 ocr_pages 7 upload_to_ready 5.0 queued 1.0 run_to_ready 4.0 preparing 1.2"
            " reading 5.0 careful inline(1 pages) card 1.5 map - checker - understood_after_ready 4.5"
            " index_after_ready 9.0",
        )
