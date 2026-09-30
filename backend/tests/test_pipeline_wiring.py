"""build_processor wires the real parts together (no database is contacted)."""

import unittest

from kaagaz.db.engine import Database, make_engine
from kaagaz.pipeline import DocumentProcessor, build_processor
from kaagaz.reading.wiring import reader_config
from kaagaz.scanning.gate import Verdict
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


if __name__ == "__main__":
    unittest.main()
