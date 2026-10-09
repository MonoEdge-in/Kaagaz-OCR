"""build_processor wires the real parts together (no database is contacted), and the
reading wiring (kaagaz/reading/wiring.py): the page pool and the Gemini reader's factory."""

import io
import sys
import threading
import unittest
from types import ModuleType
from unittest import mock

from PIL import Image, ImageDraw

from kaagaz.db.engine import Database, make_engine
from kaagaz.extraction.card import CARD_PROMPT_HASH
from kaagaz.extraction.invoice import INVOICE_PROMPT_HASH
from kaagaz.extraction.understanding import model_config
from kaagaz.ingestion import sniff
from kaagaz.pipeline import DocumentProcessor, build_processor
from kaagaz.reading.ocr import MeteredOcrEngine, OcrLine, PageRead
from kaagaz.reading.rapid_pool import RapidOcrPool
from kaagaz.reading.wiring import (
    GEMINI_OCR_MODULE,
    build_gemini_engine,
    build_reader_socket,
    page_pool,
    reader_config,
)
from kaagaz.scanning.gate import Verdict
from kaagaz.settings import INVOICE_REPLY_TOKENS, Settings
from tests.fakes import FakeScanner, MemoryStorage
from tests.settings_factory import make_settings


class PictureStorage(MemoryStorage):
    """MemoryStorage plus page pictures, like kaagaz.adapters.storage.S3Storage."""

    def __init__(self) -> None:
        super().__init__()
        self.pictures: dict[tuple[str, str, int], bytes] = {}

    def put_page_image(self, customer_id: str, document_id: str, page_no: int, png: bytes) -> None:
        self.pictures[(customer_id, document_id, page_no)] = png


class BuildProcessorTest(unittest.TestCase):
    def test_builds_without_a_model_and_without_ocr(self) -> None:
        settings = make_settings(model_enabled=False, ocr_engine="none")
        db = Database(make_engine("postgresql://unused@localhost/unused"), settings.db_app_role)
        processor = build_processor(
            settings=settings, db=db, storage=PictureStorage(), scanner=FakeScanner(Verdict.CLEAN), model=None
        )
        self.assertIsInstance(processor, DocumentProcessor)
        db.engine.dispose()


class RunConfigTest(unittest.TestCase):
    def test_a_run_records_the_route_settings_and_the_fixed_thresholds(self) -> None:
        """ML 10: each run keeps how pages were routed, so a change of threshold shows."""
        config = reader_config(make_settings(ocr_engine="rapidocr", ocr_dpi=300, text_layer_min_chars=41))
        self.assertEqual(
            {
                key: config[key]
                for key in ("ocr_engine", "ocr_dpi", "text_layer_min_chars", "blank_page_stddev")
            },
            {"ocr_engine": "rapidocr", "ocr_dpi": 300, "text_layer_min_chars": 41, "blank_page_stddev": 3.0},
        )
        self.assertEqual((config["hidden_text_max_share"], config["blank_dark_pixels"]), (0.05, 20))

    def test_a_run_records_the_ocr_dpi_rule_and_its_numbers(self) -> None:
        """Council 5 Oct 2026: the accuracy test compares runs by how their DPI was chosen."""
        config = reader_config(make_settings(ocr_dpi_rule="text_height", ocr_dpi=400))
        self.assertEqual(
            {key: config[key] for key in config if key.startswith("ocr_dpi")},
            {
                "ocr_dpi": 400,
                "ocr_dpi_rule": "text_height",
                "ocr_dpi_target_line_px": 48,
                "ocr_dpi_probe_long_side": 1200,
            },
        )

    def test_a_run_records_the_card_prompt_and_the_model_budgets(self) -> None:
        """ML 10: which prompt made the card, and the budgets the calls ran with."""
        settings = make_settings(model_context_tokens=6000, model_card_max_tokens=900, card_max_chars=9000)
        self.assertEqual(
            model_config(settings),
            {
                "card_prompt": CARD_PROMPT_HASH,
                "invoice_prompt": INVOICE_PROMPT_HASH,  # PR 6: which invoice prompt read the windows
                "model_context_tokens": 6000,
                "card_max_chars": 9000,
                "card_max_tokens": 900,
                "extract_window_chars": settings.extract_window_chars,
                "extract_window_overlap": settings.extract_window_overlap,
                "extract_max_windows": settings.extract_max_windows,
                "window_max_tokens": INVOICE_REPLY_TOKENS,
            },
        )


class ThreadNoting:
    """A page engine that notes the thread each page is read on."""

    name = "gemini"
    version = "google/gemini-3.1-pro-preview;prompt=1"

    def __init__(self) -> None:
        self.threads: list[str] = []

    def read(self, image: Image.Image) -> list[OcrLine]:
        return list(self.read_page(image, page_no=1).lines)

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        self.threads.append(threading.current_thread().name)
        line = OcrLine("TOTAL 500", 10, 10, 120, 30, None, "paragraph", box_source="found")
        return PageRead(
            page_no=page_no,
            lines=(line,),
            quality="good",
            handwritten=False,
            scripts=("latin",),
            issues=(),
            read_by="google/gemini-3.1-pro-preview",
        )


def photo() -> bytes:
    picture = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(picture).rectangle((10, 10, 120, 30), fill="black")
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


GEMINI = make_settings(ocr_engine="gemini", ocr_page_concurrency=6)


class PagePoolWiringTest(unittest.TestCase):
    """PRD 5 G8: OCR_PAGE_CONCURRENCY threads for the Gemini reader only."""

    def test_the_gemini_reader_gets_ocr_page_concurrency_threads(self) -> None:
        pool = page_pool(GEMINI, ThreadNoting())
        assert pool is not None
        self.assertEqual(pool.size, 6)

    def test_any_other_case_reads_one_page_at_a_time_as_before(self) -> None:
        cases = {
            "rapidocr": (make_settings(ocr_engine="rapidocr", ocr_page_concurrency=6), ThreadNoting()),
            "no engine": (GEMINI, None),
            "concurrency 1": (make_settings(ocr_engine="gemini", ocr_page_concurrency=1), ThreadNoting()),
        }
        for name, (settings, engine) in cases.items():
            with self.subTest(name):
                self.assertIsNone(page_pool(settings, engine))

    def test_the_socket_reads_gemini_pages_on_the_pool_and_others_on_this_thread(self) -> None:
        for settings, on_pool in ((GEMINI, True), (make_settings(ocr_engine="rapidocr"), False)):
            with self.subTest(settings.ocr_engine):
                engine = ThreadNoting()
                meter = MeteredOcrEngine(engine, timer=lambda: 0.0)
                spans = build_reader_socket(settings, meter).read(sniff.PNG, photo())
                self.assertEqual([span.text for span in spans], ["TOTAL 500"])
                (thread,) = engine.threads
                self.assertEqual(thread != threading.current_thread().name, on_pool)
                self.assertEqual(sorted(meter.take_page_reads()), [1])


class GeminiEngineWiringTest(unittest.TestCase):
    def test_the_gemini_reader_is_imported_by_its_path_and_built_from_the_settings(self) -> None:
        built: list[tuple[Settings, RapidOcrPool]] = []
        engine = ThreadNoting()

        class GeminiEngine:
            @classmethod
            def from_settings(cls, settings: Settings, *, pool: RapidOcrPool) -> ThreadNoting:
                built.append((settings, pool))
                return engine

        module = ModuleType(GEMINI_OCR_MODULE)
        module.__dict__["GeminiEngine"] = GeminiEngine
        pool = RapidOcrPool(2)
        self.assertEqual(GEMINI_OCR_MODULE, "kaagaz.reading.gemini_ocr")
        with mock.patch.dict(sys.modules, {GEMINI_OCR_MODULE: module}):
            self.assertIs(build_gemini_engine(GEMINI, pool=pool), engine)
        self.assertEqual(built, [(GEMINI, pool)])


if __name__ == "__main__":
    unittest.main()
