"""build_processor wires the real parts together (no database is contacted)."""

import unittest

from kaagaz.db.engine import Database, make_engine
from kaagaz.pipeline import DocumentProcessor, build_processor
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


if __name__ == "__main__":
    unittest.main()
