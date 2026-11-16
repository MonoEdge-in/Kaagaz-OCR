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
from typing import Any
from unittest import mock

from kaagaz.ingestion import resolve, sniff
from kaagaz.ingestion.ports import Job
from kaagaz.pipeline import CANNOT_DRAW, OnPicture
from kaagaz.reading import office
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.spans import Span
from kaagaz.scanning.gate import DocumentStatus
from tests.fakes import FakePrepared, FakePreparer, MemoryStorage, PresetSocket
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
        self._on_lock = lock
        self.reads = self.seconds = 0

    def read(
        self,
        detected_type: str,
        data: bytes,
        *,
        on_page: Callable[[RouteDecision | None], None] | None = None,
    ) -> list[Span]:
        self.reads += 1
        self._on_lock()
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


class _LockingPreparer(FakePreparer):
    """A lock under way once the pages are drawn: seen by ``still_open`` (PgDocuments reads the
    version FOR SHARE, so it waits for the lock), before its run fence is closed."""

    def __init__(self, lock: Callable[[], None]) -> None:
        super().__init__({sniff.PDF: _pictured(pages=1)})
        self._on_lock = lock

    def prepare(self, detected_type: str, data: bytes, on_picture: OnPicture) -> FakePrepared:
        prepared = super().prepare(detected_type, data, on_picture)
        self._on_lock()
        return prepared


class _LockingStorage(MemoryStorage):
    """A lock that commits while a Word file's PDF is being stored (``kaagaz_lock`` ends the run)."""

    def __init__(self, lock: Callable[[], None]) -> None:
        super().__init__()
        self._on_lock = lock
        self.puts = 0

    def put_rendition(self, customer_id: str, document_id: str, pdf: bytes) -> None:
        super().put_rendition(customer_id, document_id, pdf)
        self.puts += 1
        self._on_lock()


class _LockingConverter(FakeConverter):
    """A lock that commits while LibreOffice converts the file."""

    def __init__(self, output: bytes, lock: Callable[[], None]) -> None:
        super().__init__(output)
        self._on_lock = lock

    def __call__(self, kind: str, data: bytes) -> bytes:
        self._on_lock()
        return super().__call__(kind, data)


class LockBeforeTheReadingTest(unittest.TestCase):
    """Review (15 Nov 2026): a lock that lands before the first page is sent anywhere."""

    def test_a_lock_between_the_first_read_and_the_run_is_seen_and_the_version_only_prepared(self) -> None:
        socket = RecordingSocket({sniff.PDF: _SPANS})
        system = rig(socket=socket, preparer=FakePreparer({sniff.PDF: _pictured(pages=1)}))
        job = system.job(PDF_BYTES)
        start = system.runs.start

        def lock_then_start(customer_id: str, document_id: str, *, now: datetime) -> str:
            system.repo.locked.add((customer_id, document_id))  # committed after the worker's get
            return start(customer_id, document_id, now=now)

        with mock.patch.object(system.runs, "start", lock_then_start):
            system.processor(job, beat)

        self.assertEqual((socket.reads, system.store.saves), ([], 0))  # never sent anywhere
        self.assertIs(system.statuses.get_status(job.customer_id, job.document_id), DocumentStatus.LOCKED)
        self.assertIn((job.customer_id, job.document_id), system.repo.locked_pages)  # prepared instead
        self.assertEqual([(run.status, run.error_code) for run in system.runs_of(job)], [("locked", None)])

    def test_a_lock_under_way_as_the_reading_starts_stops_it_before_the_first_page(self) -> None:
        holder: dict[str, Job] = {}

        def lock_now() -> None:
            system.repo.locked.add((holder["job"].customer_id, holder["job"].document_id))

        socket = RecordingSocket({sniff.PDF: _SPANS})
        system = rig(socket=socket, preparer=_LockingPreparer(lock_now))
        job = holder["job"] = system.job(PDF_BYTES)

        system.processor(job, beat)

        self.assertEqual((socket.reads, system.store.saves), ([], 0))
        self.assertEqual([run.status for run in system.runs_of(job)], ["superseded"])


class LockWhileConvertingTest(unittest.TestCase):
    """Review (15 Nov 2026): a Word file's PDF holds its words; a lock that comes as it is stored
    deletes it again, and one that came before it is stored keeps it from being stored at all."""

    def _run(
        self, *, lock_on_store: bool
    ) -> tuple[_LockingStorage, RecordingSocket, list[tuple[str, str | None]]]:
        held: dict[str, Any] = {}

        def lock_now() -> None:
            job, system = held["job"], held["system"]
            system.repo.locked.add((job.customer_id, job.document_id))
            system.runs.close_running(
                job.customer_id, job.document_id, status="superseded", error_code="locked", now=_NOW
            )

        storage = _LockingStorage(lock_now if lock_on_store else lambda: None)
        converter = FakeConverter(PDF_BYTES) if lock_on_store else _LockingConverter(PDF_BYTES, lock_now)
        socket = RecordingSocket({office.RENDITION: _SPANS})
        preparer = FakePreparer({sniff.PDF: _pictured(pages=1)})
        system = held["system"] = rig(socket=socket, preparer=preparer, office=converter, renditions=storage)
        job = held["job"] = system.job(make_zip({"word/document.xml": b"<w/>"}))
        system.processor(job, beat)
        return storage, socket, [(run.status, run.error_code) for run in system.runs_of(job)]

    def test_a_lock_while_the_pdf_is_stored_deletes_it_and_nothing_is_read(self) -> None:
        storage, socket, runs = self._run(lock_on_store=True)
        self.assertEqual(storage.puts, 1)
        self.assertEqual(storage.renditions, {})  # stored, then deleted again
        self.assertEqual((socket.reads, runs), ([], [("superseded", "locked")]))

    def test_a_lock_before_the_pdf_is_stored_keeps_it_from_being_stored(self) -> None:
        storage, socket, runs = self._run(lock_on_store=False)
        self.assertEqual((storage.puts, storage.renditions), (0, {}))
        self.assertEqual((socket.reads, runs), ([], [("superseded", "locked")]))


if __name__ == "__main__":
    unittest.main()
