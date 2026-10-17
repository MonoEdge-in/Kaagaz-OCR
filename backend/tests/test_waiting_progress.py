"""PRD 5 Q6: a document waiting for the reader says so.

When the reader stops a try (Gemini down, a key problem, or the OCR server gone), the
run's progress becomes ``{"step": "waiting"}`` (``clear_progress(..., waiting="reader")``)
instead of none, so the list can say "Reading is paused for now". Any other
backend (storage, the database) clears it as before. The database side is in
tests/test_waiting_progress_db.py.
"""

import unittest

import pytest
from PIL import Image

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading.ocr import OcrLine
from tests.pdf_builder import make_pdf
from tests.test_pipeline_runs import CUSTOMER, SCANNED, Timer, beat, rig


def stopped_by(backend: str) -> tuple[str | None, bool]:
    """Run a scanned page whose reading raises BackendUnavailable(backend). Returns what
    clear_progress was told to wait for, and whether any progress is left shown."""

    class Down:
        name, version = "fake-ocr", "7"

        def __init__(self, timer: Timer) -> None:
            self.timer = timer

        def read(self, image: Image.Image) -> list[OcrLine]:
            raise BackendUnavailable(backend)

    system = rig(ocr=Down)
    job = system.job(make_pdf([SCANNED]))
    with pytest.raises(BackendUnavailable):
        system.processor(job, beat)
    key = (CUSTOMER, job.document_id)
    return system.progress.waiting[key], key in system.progress.progress


class WaitingForTheReaderTest(unittest.TestCase):
    def test_a_reader_that_stopped_the_try_leaves_the_document_waiting_for_it(self) -> None:
        for backend in ("gemini", "gemini_key", "ocr"):
            with self.subTest(backend=backend):
                self.assertEqual(stopped_by(backend), ("reader", False))

    def test_any_other_backend_clears_the_progress_as_before(self) -> None:
        for backend in ("storage", "database"):
            with self.subTest(backend=backend):
                self.assertEqual(stopped_by(backend), (None, False))


if __name__ == "__main__":
    unittest.main()
