"""PRD 7 PR 12 (#605): the worker and a locked version (p2:16-18). A locked version is scanned,
its kind told, a Word or PowerPoint file converted but its PDF never stored, its page pictures
drawn and stored, and it is ``locked``: no reader is ever called. A lock that comes while a file
is being read stops it before the next step that would send it anywhere, and nothing is saved.
On the pipeline's in-memory fakes (the stores' own SQL: test_isolation_locked_files and
test_locked_two_sessions_db)."""

import io
import unittest
from collections.abc import Callable
from datetime import UTC, datetime

from kaagaz.ingestion import resolve, sniff
from kaagaz.ingestion.ports import Job
from kaagaz.pipeline import CANNOT_DRAW
from kaagaz.reading import office
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.spans import Span
from kaagaz.scanning.gate import DocumentStatus
from tests.fakes import FakePreparer, PresetSocket
from tests.support import PDF_BYTES
from tests.test_pipeline import CUSTOMER, FakeConverter, RecordingSocket, _direct, _pictured, build
from tests.test_pipeline_runs import beat, rig
from tests.xlsx_builder import make_zip

_NOW = datetime(2026, 11, 15, tzinfo=UTC)
_SPANS = [_direct("Grand Total 1,200", page=1, top=0.1)]


class LockedPrepareTest(unittest.TestCase):
    def test_a_locked_version_is_pictured_and_locked_and_never_read(self) -> None:
        socket = RecordingSocket({sniff.PDF: _SPANS})
        system = build(socket=socket, preparer=FakePreparer({sniff.PDF: _pictured(pages=2)}))
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES), display_name="deed.pdf").document_id
        system.repo.locked.add((CUSTOMER, doc))  # a new version of a locked family: locked from the start

        system.worker.run_once()

        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.LOCKED)
        self.assertEqual(socket.reads, [])  # never read, never sent anywhere
        self.assertEqual(system.store.saves, 0)  # no reading saved
        pages = system.repo.locked_pages[(CUSTOMER, doc)]
        self.assertEqual([(page.info.page, page.has_image) for page in pages], [(1, True), (2, True)])
        self.assertEqual(sorted(key[2] for key in system.page_images.images if key[1] == doc), [1, 2])
        self.assertEqual(system.repo.page_counts[(CUSTOMER, doc)], 2)

    def test_a_word_files_pdf_is_drawn_from_and_never_stored(self) -> None:
        converter = FakeConverter(PDF_BYTES)
        socket = RecordingSocket({office.RENDITION: _SPANS})
        system = build(
            socket=socket, preparer=FakePreparer({sniff.PDF: _pictured(pages=1)}), office=converter
        )
        data = make_zip({"word/document.xml": b"<w/>"})
        doc = system.upload.accept(CUSTOMER, io.BytesIO(data), display_name="deed.docx").document_id
        system.repo.locked.add((CUSTOMER, doc))

        system.worker.run_once()

        self.assertEqual(converter.calls, [(resolve.DOCX, data)])
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.LOCKED)
        self.assertEqual(system.storage.renditions, {})  # its PDF holds its words: never kept
        self.assertEqual(socket.reads, [])

    def test_a_kind_with_no_page_pictures_fails_cannot_draw_and_stays_locked(self) -> None:
        socket = RecordingSocket({sniff.PDF: _SPANS})
        system = build(socket=socket, preparer=FakePreparer())  # no pages for any kind
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES), display_name="deed.pdf").document_id
        system.repo.locked.add((CUSTOMER, doc))

        system.worker.run_once()

        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.FAILED)
        self.assertEqual(system.repo.failure_codes[(CUSTOMER, doc)], CANNOT_DRAW)
        self.assertNotIn((CUSTOMER, doc), system.repo.locked_pages)
        self.assertEqual(socket.reads, [])

    def test_a_delivery_for_a_version_locked_and_prepared_is_skipped(self) -> None:
        socket = RecordingSocket({sniff.PDF: _SPANS})
        system = build(socket=socket, preparer=FakePreparer({sniff.PDF: _pictured(pages=1)}))
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES), display_name="deed.pdf").document_id
        system.repo.locked.add((CUSTOMER, doc))
        system.statuses.set_status(CUSTOMER, doc, DocumentStatus.LOCKED)

        system.processor(Job(CUSTOMER, doc), beat)

        self.assertEqual((socket.reads, system.store.saves), ([], 0))
        self.assertNotIn((CUSTOMER, doc), system.repo.locked_pages)


class _LockingSocket(PresetSocket):
    """A lock arrives while the first reader is still on the file (``kaagaz_lock`` ends the run)."""

    def __init__(self, lock: Callable[[], None]) -> None:
        super().__init__({sniff.PDF: _SPANS}, {sniff.PDF: _SPANS})
        self._lock = lock
        self.reads = self.seconds = 0

    def read(
        self,
        detected_type: str,
        data: bytes,
        *,
        on_page: Callable[[RouteDecision | None], None] | None = None,
    ) -> list[Span]:
        self.reads += 1
        self._lock()
        return super().read(detected_type, data, on_page=on_page)

    def read_second(self, detected_type: str, data: bytes) -> list[Span] | None:
        self.seconds += 1
        return super().read_second(detected_type, data)


class LockWhileReadingTest(unittest.TestCase):
    def test_a_lock_during_the_reading_stops_it_before_the_next_step_and_nothing_is_saved(self) -> None:
        holder: dict[str, Job] = {}

        def lock_now() -> None:
            job = holder["job"]
            system.repo.locked.add((job.customer_id, job.document_id))
            system.runs.close_running(
                job.customer_id, job.document_id, status="superseded", error_code="locked", now=_NOW
            )

        socket = _LockingSocket(lock_now)
        system = rig(socket=socket, preparer=FakePreparer({sniff.PDF: _pictured(pages=1)}))
        job = holder["job"] = system.job(PDF_BYTES)

        system.processor(job, beat)

        self.assertEqual((socket.reads, socket.seconds), (1, 0))  # the call in flight only
        self.assertEqual(system.store.saves, 0)
        self.assertEqual(
            [(run.status, run.error_code) for run in system.runs_of(job)], [("superseded", "locked")]
        )


if __name__ == "__main__":
    unittest.main()
