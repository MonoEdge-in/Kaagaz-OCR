"""A zip in the pipeline (#522 PR 3), with fakes: after its own virus check, a zip that is a
folder of files goes to the unpacker and is never read; one the unpacker keeps as a file stays
unread (needs a reader, as before); an Office file sniffed as a zip is held to its own kind's
limit once its kind is told. The unpacking itself runs on PostgreSQL (test_unpack_db.py)."""

import io
import unittest
from dataclasses import replace

from kaagaz.ingestion import resolve
from kaagaz.ingestion.policy import ZIP_MAX_BYTES
from kaagaz.ingestion.ports import DocumentRecord, Job
from kaagaz.jobs.worker import Heartbeat, JobFailed
from kaagaz.pipeline import DocumentProcessor
from kaagaz.scanning.gate import DocumentStatus, Verdict
from tests.fakes import FakePreparer, FakeScanner, fake_sheet_pages
from tests.test_pipeline import CUSTOMER, System, build, real_socket
from tests.xlsx_builder import make_xlsx, sheet_xml
from tests.zip_builder import make_zip

FOLDER_ZIP = make_zip([("Top/a.csv", b"a,b\n1,2\n"), ("Top/Mid/b.csv", b"c\n3\n")])


class Unpacker:
    """Records each call; says it unpacked when ``unpacks``."""

    def __init__(self, *, unpacks: bool = True) -> None:
        self.unpacks = unpacks
        self.calls: list[tuple[Job, DocumentRecord, bytes]] = []

    def __call__(self, job: Job, record: DocumentRecord, data: bytes, heartbeat: Heartbeat) -> bool:
        heartbeat()
        self.calls.append((job, record, data))
        return self.unpacks


def processor(system: System, unpack: Unpacker | None, kind_limit: int | None = None) -> DocumentProcessor:
    return DocumentProcessor(
        gate=system.gate,
        statuses=system.statuses,
        documents=system.repo,
        socket=real_socket(),
        prepare=FakePreparer().prepare,
        sheet_pages=fake_sheet_pages,
        page_images=system.page_images,
        store=system.store,
        unpack=unpack,
        kind_limit=None if kind_limit is None else (lambda _customer, _kind: kind_limit),
    )


def run(
    system: System, unpack: Unpacker | None, data: bytes, name: str, kind_limit: int | None = None
) -> str:
    doc = system.upload.accept(CUSTOMER, io.BytesIO(data), display_name=name).document_id
    processor(system, unpack, kind_limit)(Job(customer_id=CUSTOMER, document_id=doc), lambda: None)
    return doc


class ZipInThePipelineTest(unittest.TestCase):
    def test_a_folder_zip_is_scanned_then_unpacked_and_ready_never_read(self) -> None:
        system, unpack = build(), Unpacker()
        doc = run(system, unpack, FOLDER_ZIP, "Archive.zip")
        ((job, record, data),) = unpack.calls
        self.assertEqual((job.document_id, record.detected_type, data), (doc, resolve.ZIP, FOLDER_ZIP))
        self.assertEqual(system.scanner.scanned, [FOLDER_ZIP])  # its own scan came first
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.READY)
        self.assertEqual(system.repo.page_counts[(CUSTOMER, doc)], 0)
        self.assertEqual(system.store.for_document(CUSTOMER, doc), [])  # nothing read from it

    def test_a_zip_the_unpacker_keeps_stays_an_unread_file(self) -> None:
        for unpack in (Unpacker(unpacks=False), None):
            with self.subTest(unpack=unpack):
                system = build()
                doc = run(system, unpack, FOLDER_ZIP, "inner.zip")
                self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.NEEDS_READER)
                self.assertEqual(system.store.for_document(CUSTOMER, doc), [])

    def test_a_rejected_zip_is_never_unpacked(self) -> None:
        system, unpack = build(scanner=FakeScanner(Verdict.INFECTED)), Unpacker()
        with self.assertRaises(JobFailed):
            run(system, unpack, FOLDER_ZIP, "Archive.zip")
        self.assertEqual(unpack.calls, [])

    def test_an_office_file_sniffed_as_a_zip_is_held_to_its_own_kind_s_limit(self) -> None:
        workbook = make_xlsx([("S", sheet_xml('<row r="1"><c r="A1"><v>1</v></c></row>'))])
        system, unpack = build(), Unpacker()
        with self.assertRaises(JobFailed) as failed:
            run(system, unpack, workbook, "book.xlsx", kind_limit=len(workbook) - 1)
        self.assertEqual(failed.exception.code, "too_large")
        self.assertEqual(unpack.calls, [])
        system = build()
        doc = run(system, unpack, workbook, "book.xlsx", kind_limit=len(workbook))
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.READY)

    def test_a_zip_over_500_mb_is_refused_before_the_scan_loads_it(self) -> None:
        """A fake size: the record says 500 MB and a byte; the worker never reads the file."""
        system, unpack = build(), Unpacker()
        doc = system.upload.accept(CUSTOMER, io.BytesIO(FOLDER_ZIP), display_name="Huge.zip").document_id
        record = system.repo.get(CUSTOMER, doc)
        assert record is not None
        system.repo._by_id[(CUSTOMER, doc)] = replace(record, size=ZIP_MAX_BYTES + 1)
        with self.assertRaises(JobFailed) as failed:
            processor(system, unpack)(Job(customer_id=CUSTOMER, document_id=doc), lambda: None)
        self.assertEqual((failed.exception.code, failed.exception.retryable), ("zip_over_limit", False))
        self.assertEqual((system.scanner.scanned, unpack.calls), ([], []))
        self.assertEqual(ZIP_MAX_BYTES, 500 * 1024**2)  # #522 security review: was 1 GiB

    def test_a_zip_of_exactly_1_gb_goes_on(self) -> None:
        system, unpack = build(), Unpacker()
        doc = system.upload.accept(CUSTOMER, io.BytesIO(FOLDER_ZIP), display_name="Big.zip").document_id
        record = system.repo.get(CUSTOMER, doc)
        assert record is not None
        system.repo._by_id[(CUSTOMER, doc)] = replace(record, size=ZIP_MAX_BYTES)
        processor(system, unpack)(Job(customer_id=CUSTOMER, document_id=doc), lambda: None)
        self.assertEqual(len(unpack.calls), 1)
