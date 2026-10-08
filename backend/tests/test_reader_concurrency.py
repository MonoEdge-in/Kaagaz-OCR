"""Pages in parallel without changing the Reader protocol (PRD 5 G8): the PDF and image
readers hand only the engine's ``read_page`` to a ``PagePool``; parsing, drawing and the
second reading stay on the reader's thread, pages are finished strictly in page order,
and the first failure in page order cancels the rest. The engines are fakes that hold
each page until its turn, so pages finish out of order on purpose.
"""

import io
import threading
import time
import unittest
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any
from unittest import mock

from PIL import Image

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.preparing.pages import render_pdf_page
from kaagaz.reading.image_reader import ImageReader
from kaagaz.reading.ocr import MeteredOcrEngine, OcrLine, PageRead
from kaagaz.reading.ocr_dpi import FIXED, TEXT_HEIGHT
from kaagaz.reading.pdf_reader import PdfReader
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.socket import PagePool
from kaagaz.reading.spans import BoxLocation, Span
from kaagaz.reading.text_quality import TextLayerRules
from tests.pdf_builder import Page, Picture, Text, make_pdf
from tests.picture_support import text_like_picture
from tests.test_ocr_dpi import SCAN_144, page_of

WIDTH, HEIGHT = 612.0, 792.0
RULES = TextLayerRules(min_chars=1, min_good_ratio=0.9, scan_image_coverage=0.5)
SCANNED = Page(picture=Picture(text_like_picture(153, 198), 0, 0, WIDTH, HEIGHT))
TYPED = Page(texts=[Text(72, 700, "typed page")])
WAIT = 5.0  # seconds a fake waits for its turn before the test fails


def closed(image: Image.Image) -> bool:
    try:
        image.load()
    except ValueError:  # "Operation on closed image"
        return True
    return False


class TurnTakingEngine:
    """A ``PageEngine`` that reads page n as the line "page n". With ``order``, each page
    waits until it is the next in ``order`` before it finishes, so pages finish shuffled.
    It records the most pages in flight, each read's thread, and the pictures given."""

    name = "gemini"
    version = "google/gemini-3.1-pro-preview;prompt=1"

    def __init__(
        self,
        order: list[int] | None = None,
        *,
        hold: float = 0.0,
        empty_first: frozenset[int] = frozenset(),
        fail: dict[int, Exception] | None = None,
        blocked: frozenset[int] = frozenset(),
    ) -> None:
        self.order = list(order or [])
        self.hold = hold
        self.empty_first = empty_first
        self.fail = fail or {}
        self.blocked = blocked
        self.go = threading.Event()  # lets the pages in ``blocked`` finish
        self.turn = threading.Condition()
        self.now = 0
        self.most = 0
        self.calls: list[tuple[int, str]] = []
        self.finished: list[int] = []
        self.pictures: list[Image.Image] = []

    def read(self, image: Image.Image) -> list[OcrLine]:
        raise AssertionError("the readers read numbered pages")

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        with self.turn:
            self.now += 1
            self.most = max(self.most, self.now)
            again = any(called == page_no for called, _thread in self.calls)
            self.calls.append((page_no, threading.current_thread().name))
            self.pictures.append(image)
        try:
            time.sleep(self.hold)
            if page_no in self.blocked and not self.go.wait(WAIT):
                raise AssertionError(f"page {page_no} was never let go")
            if page_no in self.fail:
                raise self.fail[page_no]
            with self.turn:
                if page_no in self.order and not self.turn.wait_for(lambda: self.order[0] == page_no, WAIT):
                    raise AssertionError(f"page {page_no} never had its turn")
                if page_no in self.order:
                    self.order.pop(0)
                self.finished.append(page_no)
                self.turn.notify_all()
            empty = page_no in self.empty_first and not again
            width, height = image.size
            line = OcrLine(
                f"page {page_no}", 0.1 * width, 0.1 * height, 0.5 * width, 0.2 * height, None, "paragraph"
            )
            return PageRead(
                page_no=page_no,
                lines=() if empty else (line,),
                quality="good",
                handwritten=False,
                scripts=("latin",),
                issues=(),
                read_by=self.version,
            )
        finally:
            with self.turn:
                self.now -= 1

    def threads_of(self, page_no: int) -> list[str]:
        return [thread for called, thread in self.calls if called == page_no]


class FailsWhileOthersRead:
    """A ``PageEngine`` whose page 1 fails once pages 2 and 3 have started; they take a
    moment longer, then read as "page n" (review FX1)."""

    name = "gemini"
    version = "google/gemini-3.1-pro-preview;prompt=1"

    def __init__(self) -> None:
        self.started = threading.Barrier(3)
        self.finished: list[int] = []

    def read(self, image: Image.Image) -> list[OcrLine]:
        raise AssertionError("the readers read numbered pages")

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        self.started.wait(WAIT)
        if page_no == 1:
            raise BackendUnavailable("gemini")
        time.sleep(0.3)
        self.finished.append(page_no)
        line = OcrLine(f"page {page_no}", 1, 1, 5, 5, None, "paragraph")
        return PageRead(
            page_no=page_no,
            lines=(line,),
            quality="good",
            handwritten=False,
            scripts=("latin",),
            issues=(),
            read_by="google/gemini-3.1-pro-preview",
        )


def pdf_reader(engine: Any, pages: PagePool | None, *, dpi: int = 72, rule: str = FIXED) -> PdfReader:
    return PdfReader(
        engine,
        max_pages=20,
        dpi=dpi,
        max_image_pixels=40_000_000,
        rules=RULES,
        blank_stddev=3.0,
        dpi_rule=rule,
        pages=pages,
    )


def read_pdf(reader: PdfReader, data: bytes) -> tuple[list[Span], list[RouteDecision | None]]:
    routes: list[RouteDecision | None] = []
    return reader.read(data, on_page=routes.append), routes


@contextmanager
def drawn() -> Iterator[list[tuple[str, Image.Image]]]:
    """Every picture the PDF reader draws, with the thread that drew it."""
    pictures: list[tuple[str, Image.Image]] = []

    def draw(*args: Any, **kwargs: Any) -> Image.Image:
        picture = render_pdf_page(*args, **kwargs)
        pictures.append((threading.current_thread().name, picture))
        return picture

    with mock.patch("kaagaz.reading.pdf_reader.render_pdf_page", draw):
        yield pictures


def texts(spans: list[Span]) -> list[tuple[str, int]]:
    return [(span.text, span.location.page) for span in spans if isinstance(span.location, BoxLocation)]


MAIN = threading.current_thread().name


class PdfPagesInParallelTest(unittest.TestCase):
    def test_pages_finish_shuffled_but_spans_and_on_page_stay_in_page_order(self) -> None:
        """G8: results are put back in page order, and on_page fires after each page in
        order, so the pipeline's routes (indexed by position) and the heartbeat hold."""
        data = make_pdf([SCANNED, SCANNED, TYPED, SCANNED, SCANNED, SCANNED])
        engine = TurnTakingEngine([4, 2, 1, 6, 5])
        with drawn() as pictures:
            spans, routes = read_pdf(pdf_reader(engine, PagePool.threads(3)), data)
        self.assertEqual(engine.finished, [4, 2, 1, 6, 5])  # really out of order
        expected_spans, expected_routes = read_pdf(pdf_reader(TurnTakingEngine(), None), data)
        self.assertEqual((spans, routes), (expected_spans, expected_routes))
        self.assertEqual(
            texts(spans),
            [("page 1", 1), ("page 2", 2), ("typed page", 3), ("page 4", 4), ("page 5", 5), ("page 6", 6)],
        )
        self.assertEqual(
            [route.route if route else None for route in routes], ["ocr"] * 2 + ["text_layer"] + ["ocr"] * 3
        )
        self.assertEqual({thread for thread, _picture in pictures}, {MAIN})  # drawing stays here
        self.assertNotIn(MAIN, {thread for _page, thread in engine.calls})  # reading goes to the pool
        self.assertTrue(all(closed(picture) for _thread, picture in pictures))

    def test_no_more_pages_are_read_at_once_than_the_pool_size(self) -> None:
        """OCR_PAGE_CONCURRENCY bounds the pages in flight, even on a bigger executor."""
        engine = TurnTakingEngine(hold=0.03)
        pool = PagePool(ThreadPoolExecutor(max_workers=6), size=2)
        spans, _routes = read_pdf(pdf_reader(engine, pool), make_pdf([SCANNED] * 7))
        self.assertEqual(engine.most, 2)
        self.assertEqual([page for _text, page in texts(spans)], list(range(1, 8)))

    def test_concurrency_one_is_the_same_as_no_pool(self) -> None:
        data = make_pdf([SCANNED, TYPED, SCANNED])
        without = pdf_reader(TurnTakingEngine(), None)
        one = pdf_reader(TurnTakingEngine(), PagePool.threads(1))
        self.assertEqual(read_pdf(one, data), read_pdf(without, data))
        self.assertEqual(one.version, without.version)

    def test_with_no_pool_every_page_is_read_on_the_readers_own_thread(self) -> None:
        engine = TurnTakingEngine()
        read_pdf(pdf_reader(engine, None), make_pdf([SCANNED, SCANNED]))
        self.assertEqual({thread for _page, thread in engine.calls}, {MAIN})

    def test_the_first_failure_in_page_order_is_raised_and_the_pages_not_started_are_cancelled(self) -> None:
        """Page 1 fails; page 2 is being read (or cancelled before it started) and page 3
        waits for the one thread: page 3 is never read, no page is finished, and every
        picture is closed. A page already being read is waited for (review FX1)."""
        # Page 1 takes a moment before it fails, so pages 2 and 3 are handed over first.
        engine = TurnTakingEngine(fail={1: BackendUnavailable("gemini")}, blocked=frozenset({2}), hold=0.3)
        executor = ThreadPoolExecutor(max_workers=1)
        routes: list[RouteDecision | None] = []
        let_go = threading.Timer(0.6, engine.go.set)  # page 2, if already being read, ends on its own
        let_go.start()
        with drawn() as pictures, self.assertRaises(BackendUnavailable) as caught:
            pdf_reader(engine, PagePool(executor, size=3)).read(
                make_pdf([SCANNED] * 5), on_page=routes.append
            )
        self.assertEqual(caught.exception.args, ("gemini",))
        let_go.join()
        executor.shutdown(wait=True)
        self.assertIn(sorted(page for page, _thread in engine.calls), ([1], [1, 2]))
        self.assertEqual(routes, [])
        self.assertEqual(len(pictures), 4)  # page 4 was drawn, then never sent
        self.assertTrue(all(closed(picture) for _thread, picture in pictures))

    def test_fx1_pages_being_read_when_a_document_fails_never_reach_the_next_documents_run(self) -> None:
        """Review FX1: page 1 fails while pages 2 and 3 are being read on the shared pool.
        The read waits for them before it raises, so the next job's take (``_start_run``)
        and the meter after it hold none of this document's reads."""
        engine = FailsWhileOthersRead()
        meter = MeteredOcrEngine(engine, timer=time.perf_counter)
        executor = ThreadPoolExecutor(max_workers=3)
        with self.assertRaises(BackendUnavailable):
            pdf_reader(meter, PagePool(executor, size=3)).read(make_pdf([SCANNED] * 3))
        self.assertEqual(sorted(engine.finished), [2, 3])  # waited for before the read raised
        meter.take_page_reads()  # this document's run ends with what it read
        self.assertEqual(meter.take_page_reads(), {})  # the next job's _start_run
        executor.shutdown(wait=True)
        self.assertEqual(meter.take_page_reads(), {})  # and nothing arrives later

    def test_a_page_read_again_at_the_ceiling_is_read_on_the_readers_thread(self) -> None:
        """The DPI rule's second reading (nothing found at a lowered DPI) draws and reads
        the page again on this thread, when the page's turn comes."""
        scan = Page(picture=Picture(SCAN_144, 0, 0, WIDTH, HEIGHT))
        engine = TurnTakingEngine([2, 1], empty_first=frozenset({1, 2}))
        reader = pdf_reader(engine, PagePool.threads(2), dpi=300, rule=TEXT_HEIGHT)
        with drawn() as pictures:
            spans, _routes = read_pdf(reader, make_pdf([scan, scan]))
        self.assertEqual(texts(spans), [("page 1", 1), ("page 2", 2)])
        for page_no in (1, 2):
            first, again = engine.threads_of(page_no)
            self.assertNotEqual(first, MAIN)
            self.assertEqual(again, MAIN)
        self.assertEqual({thread for thread, _picture in pictures}, {MAIN})
        self.assertTrue(reader.version.endswith(";text_dpi=144;ocr_reread=2"), reader.version)

    def test_the_meter_keeps_each_pages_read_from_the_pool(self) -> None:
        engine = TurnTakingEngine([3, 1, 2])
        meter = MeteredOcrEngine(engine, timer=time.perf_counter)
        read_pdf(pdf_reader(meter, PagePool.threads(3)), make_pdf([SCANNED] * 3))
        reads = meter.take_page_reads()
        self.assertEqual(sorted(reads), [1, 2, 3])
        self.assertEqual(
            [read.lines[0].text for _page, read in sorted(reads.items())], ["page 1", "page 2", "page 3"]
        )
        self.assertEqual(meter.take()[0], 3)


def tiff(frames: list[Image.Image]) -> bytes:
    buffer = io.BytesIO()
    frames[0].save(buffer, format="TIFF", save_all=True, append_images=frames[1:])
    return buffer.getvalue()


def image_reader(engine: Any, pages: PagePool | None, *, rule: str = FIXED) -> ImageReader:
    return ImageReader(
        engine,
        max_pages=10,
        max_image_pixels=40_000_000,
        ocr_dpi=300,
        blank_stddev=3.0,
        dpi_rule=rule,
        pages=pages,
    )


def read_image(reader: ImageReader, data: bytes) -> tuple[list[Span], list[RouteDecision | None]]:
    routes: list[RouteDecision | None] = []
    return reader.read(data, on_page=routes.append), routes


class ImagePagesInParallelTest(unittest.TestCase):
    def test_frames_finish_shuffled_but_spans_and_on_page_stay_in_order(self) -> None:
        frames = [text_like_picture(300, 400) for _ in range(3)]
        frames.insert(2, Image.new("RGB", (300, 400), "white"))  # a blank frame: never sent
        data = tiff(frames)
        engine = TurnTakingEngine([4, 1, 2])
        spans, routes = read_image(image_reader(engine, PagePool.threads(3)), data)
        self.assertEqual(engine.finished, [4, 1, 2])
        self.assertEqual(read_image(image_reader(TurnTakingEngine(), None), data), (spans, routes))
        self.assertEqual(texts(spans), [("page 1", 1), ("page 2", 2), ("page 4", 4)])
        self.assertEqual([route.route if route else None for route in routes], ["ocr", "ocr", "blank", "ocr"])
        self.assertNotIn(MAIN, {thread for _page, thread in engine.calls})
        self.assertTrue(all(closed(picture) for picture in engine.pictures))

    def test_a_shrunk_frame_with_nothing_on_it_is_read_again_as_it_was_on_the_readers_thread(self) -> None:
        photo = page_of(96, size=(1600, 2200))
        engine = TurnTakingEngine(empty_first=frozenset({1}))
        reader = image_reader(engine, PagePool.threads(2), rule=TEXT_HEIGHT)
        spans, _routes = read_image(reader, tiff([photo]))
        self.assertEqual(texts(spans), [("page 1", 1)])
        first, again = engine.threads_of(1)
        self.assertEqual((first != MAIN, again), (True, MAIN))
        self.assertEqual([picture.size for picture in engine.pictures], [(800, 1100), (1600, 2200)])
        self.assertEqual(reader.version, "google/gemini-3.1-pro-preview;prompt=1;ocr_scale=0.50;ocr_reread=1")
        self.assertTrue(all(closed(picture) for picture in engine.pictures))

    def test_a_failing_frame_stops_the_read(self) -> None:
        engine = TurnTakingEngine(fail={2: BackendUnavailable("gemini")})
        frames = [text_like_picture(300, 400) for _ in range(4)]
        routes: list[RouteDecision | None] = []
        with self.assertRaises(BackendUnavailable):
            image_reader(engine, PagePool.threads(2)).read(tiff(frames), on_page=routes.append)
        self.assertEqual(routes, [RouteDecision("ocr", "few_chars")])  # page 1 only: page 2 failed


if __name__ == "__main__":
    unittest.main()
