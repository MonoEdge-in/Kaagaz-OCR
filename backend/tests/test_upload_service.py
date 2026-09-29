import dataclasses
import io
import threading
import unittest
from typing import Any, cast

from kaagaz.ingestion import sniff
from kaagaz.ingestion.errors import BackendUnavailable, RejectCode, UploadRejected
from kaagaz.ingestion.messages import over_every_limit_message, too_large_message
from kaagaz.ingestion.policy import KIND_OF, UploadPolicy, limits_by_type
from kaagaz.ingestion.ports import DocumentRecord, Job, UnknownFamily, UnknownFolder
from kaagaz.ingestion.service import UploadResult, UploadService
from tests.fakes import MemoryQueue, MemoryRepository, MemoryStorage
from tests.support import PDF_BYTES, TEST_POLICY, TEXT_BYTES, make_service

CUSTOMER = "cust-a"
MB = 1024 * 1024

# The first bytes that make each type (sniff.py). Made up; no real file.
_FIRST_BYTES = {
    sniff.PDF: b"%PDF-1.7\n",
    sniff.PNG: b"\x89PNG\r\n\x1a\n",
    sniff.JPEG: b"\xff\xd8\xff",
    sniff.TIFF: b"II*\x00",
    sniff.ZIP: b"PK\x03\x04",
    sniff.TEXT: b"invoice,amount\n",
}


def file_of(detected_type: str, size: int) -> bytes:
    """A file of exactly ``size`` bytes that sniffs as ``detected_type``."""
    first = _FIRST_BYTES[detected_type]
    return first + b"1" * (size - len(first))


class AcceptTest(unittest.TestCase):
    def test_new_file_is_stored_recorded_and_queued_once(self) -> None:
        service, repo, storage, queue = make_service()
        result = service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))

        self.assertFalse(result.duplicate)
        self.assertEqual(storage.get(CUSTOMER, result.document_id), PDF_BYTES)
        self.assertEqual(len(repo.records_for(CUSTOMER)), 1)
        self.assertEqual(queue.jobs, [Job(CUSTOMER, result.document_id)])

    def test_record_holds_fingerprint_size_and_detected_type(self) -> None:
        service, repo, _, _ = make_service()
        service.accept(CUSTOMER, io.BytesIO(TEXT_BYTES))
        (record,) = repo.records_for(CUSTOMER)
        self.assertEqual(record.size, len(TEXT_BYTES))
        self.assertEqual(record.detected_type, sniff.TEXT)
        self.assertEqual(len(record.sha256), 64)

    def test_record_has_no_raw_file_name_field(self) -> None:
        # Only the display name the API has cleaned is kept; the raw name never is.
        names = {f.name for f in dataclasses.fields(DocumentRecord)}
        self.assertEqual(
            names,
            {
                "customer_id",
                "document_id",
                "sha256",
                "size",
                "detected_type",
                "display_name",
                "uploaded_by",
                "folder_id",
                "family_id",
                "version_no",
            },
        )

    def test_record_keeps_the_display_name_and_uploader_it_was_given(self) -> None:
        service, repo, _, _ = make_service()
        service.accept(CUSTOMER, io.BytesIO(TEXT_BYTES), display_name="XXXXXX1234F.csv", uploaded_by="u1")
        (record,) = repo.records_for(CUSTOMER)
        self.assertEqual((record.display_name, record.uploaded_by), ("XXXXXX1234F.csv", "u1"))

    def test_record_keeps_its_folder_and_is_the_first_version_of_its_own_family(self) -> None:
        service, repo, _, _ = make_service()
        repo.folders.add((CUSTOMER, "folder-1"))
        service.accept(CUSTOMER, io.BytesIO(PDF_BYTES), folder_id="folder-1")
        service.accept(CUSTOMER, io.BytesIO(TEXT_BYTES))
        by_type = {record.detected_type: record for record in repo.records_for(CUSTOMER)}
        self.assertEqual(
            [(r.folder_id, r.family_id, r.version_no) for r in (by_type[sniff.PDF], by_type[sniff.TEXT])],
            [("folder-1", None, 1), (None, None, 1)],  # family None: the document's own id
        )

    def test_missing_customer_is_refused(self) -> None:
        service, _, storage, _ = make_service()
        for bad in ("", None):
            with self.assertRaises(ValueError):
                service.accept(cast(str, bad), io.BytesIO(PDF_BYTES))
        self.assertEqual(storage.count(), 0)


class DuplicateTest(unittest.TestCase):
    def test_same_file_twice_is_stored_once_and_says_duplicate(self) -> None:
        service, repo, storage, queue = make_service()
        first = service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        second = service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))

        self.assertTrue(second.duplicate)
        self.assertEqual(second.document_id, first.document_id)
        self.assertEqual(storage.count(), 1)
        self.assertEqual(len(repo.records_for(CUSTOMER)), 1)
        self.assertEqual(len(queue.jobs), 1)

    def test_files_one_byte_apart_are_not_duplicates(self) -> None:
        # The name is never an input, so "similar names" (#25) can only mean
        # nearly identical bytes. Different bytes are different documents.
        service, _, storage, _ = make_service()
        a = service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        b = service.accept(CUSTOMER, io.BytesIO(PDF_BYTES + b"1"))
        self.assertFalse(b.duplicate)
        self.assertNotEqual(a.document_id, b.document_id)
        self.assertEqual(storage.count(), 2)

    def test_lost_race_deletes_own_file_and_returns_the_winner(self) -> None:
        winner = DocumentRecord(CUSTOMER, "winner", "sha", 1, sniff.PDF)

        class RacingRepository(MemoryRepository):
            # Simulates another upload inserting between our check and insert.
            def find_by_fingerprint(self, customer_id: str, sha256: str) -> None:
                return None

            def add_if_absent(self, record: DocumentRecord) -> tuple[DocumentRecord, bool]:
                return winner, False

        storage, queue = MemoryStorage(), MemoryQueue()
        service = UploadService(TEST_POLICY, RacingRepository(), storage, queue)
        result = service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))

        self.assertEqual(result.document_id, "winner")
        self.assertTrue(result.duplicate)
        self.assertEqual(storage.count(), 0)
        self.assertEqual(queue.jobs, [])

    def test_concurrent_identical_uploads_give_one_document(self) -> None:
        service, repo, storage, queue = make_service()
        threads_count = 8
        barrier = threading.Barrier(threads_count)
        results = []

        def upload() -> None:
            barrier.wait()
            results.append(service.accept(CUSTOMER, io.BytesIO(PDF_BYTES)))

        threads = [threading.Thread(target=upload) for _ in range(threads_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertFalse(any(t.is_alive() for t in threads))

        self.assertEqual(len(repo.records_for(CUSTOMER)), 1)
        self.assertEqual(storage.count(), 1)
        self.assertEqual(len(queue.jobs), 1)
        self.assertEqual(sum(not r.duplicate for r in results), 1)
        self.assertEqual({r.document_id for r in results}, {queue.jobs[0].document_id})


class RejectTest(unittest.TestCase):
    def assert_rejected(self, data: bytes, code: RejectCode, policy: UploadPolicy = TEST_POLICY) -> None:
        """The upload is refused with ``code`` and nothing is stored, recorded or queued."""
        service, repo, storage, queue = make_service(policy)
        with self.assertRaises(UploadRejected) as ctx:
            service.accept(CUSTOMER, io.BytesIO(data))
        self.assertIs(ctx.exception.code, code)
        self.assertEqual(repo.records_for(CUSTOMER), [])
        self.assertEqual(storage.count(), 0)
        self.assertEqual(queue.jobs, [])

    def test_empty_file(self) -> None:
        self.assert_rejected(b"", RejectCode.EMPTY_FILE)

    def test_oversized_file(self) -> None:
        self.assert_rejected(b"%PDF-" + b"0" * TEST_POLICY.max_bytes, RejectCode.TOO_LARGE)

    def test_unrecognised_type(self) -> None:
        self.assert_rejected(b"\x7fELF\x02\x01\x01\x00rest", RejectCode.UNSUPPORTED_TYPE)

    def test_recognised_type_that_is_not_allowed(self) -> None:
        text_only = UploadPolicy(
            max_bytes=1024, allowed_types=frozenset({sniff.TEXT}), type_limits=TEST_POLICY.type_limits
        )
        self.assert_rejected(PDF_BYTES, RejectCode.UNSUPPORTED_TYPE, text_only)

    def test_rejection_message_is_only_the_code(self) -> None:
        service, _, _, _ = make_service()
        with self.assertRaises(UploadRejected) as ctx:
            service.accept(CUSTOMER, io.BytesIO(b"\x7fELF secret-content"))
        self.assertEqual(str(ctx.exception), "unsupported_type")

    def test_a_too_large_refusal_with_details_is_still_only_the_code(self) -> None:
        rejected = UploadRejected(RejectCode.TOO_LARGE, kind="image", size=34 * MB, limit=20 * MB)
        self.assertEqual(str(rejected), "too_large")

    def test_a_file_over_every_limit_is_refused_while_reading_before_its_type_is_known(self) -> None:
        service, _, _, _ = make_service()
        with self.assertRaises(UploadRejected) as ctx:
            service.accept(CUSTOMER, io.BytesIO(file_of(sniff.PDF, TEST_POLICY.largest_limit + 1)))
        self.assertIs(ctx.exception.code, RejectCode.TOO_LARGE)
        self.assertEqual((ctx.exception.kind, ctx.exception.size, ctx.exception.limit), (None, None, None))

    def test_an_exe_renamed_to_pdf_is_refused_by_its_first_bytes(self) -> None:
        self.assert_rejected(b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200, RejectCode.UNSUPPORTED_TYPE)


class TypeLimitTest(unittest.TestCase):
    """Each type has its own size limit, checked after the type is sniffed (PRD U2)."""

    def test_each_type_passes_at_its_limit_and_is_refused_one_byte_over(self) -> None:
        for detected, kind in KIND_OF.items():
            # Only this type's limit is low, so no other limit can be what refuses it.
            limits = dict.fromkeys(KIND_OF, 3000) | {detected: 1000}
            policy = UploadPolicy(max_bytes=5000, allowed_types=frozenset(KIND_OF), type_limits=limits)
            with self.subTest(detected=detected):
                service, repo, storage, queue = make_service(policy)
                accepted = service.accept(CUSTOMER, io.BytesIO(file_of(detected, 1000)))
                self.assertFalse(accepted.duplicate)
                with self.assertRaises(UploadRejected) as ctx:
                    service.accept(CUSTOMER, io.BytesIO(file_of(detected, 1001)))
                refused = ctx.exception
                self.assertEqual(
                    (refused.code, refused.kind, refused.size, refused.limit),
                    (RejectCode.TOO_LARGE, kind, 1001, 1000),
                )
                # Only the file at the limit was stored, recorded and queued.
                self.assertEqual(storage.count(), 1)
                self.assertEqual(len(repo.records_for(CUSTOMER)), 1)
                self.assertEqual(queue.jobs, [Job(CUSTOMER, accepted.document_id)])

    def test_the_customers_limit_lowers_a_types_limit(self) -> None:
        limits = limits_by_type(pdf=5000, image=2000, sheet=1000)
        policy = UploadPolicy(max_bytes=1500, allowed_types=frozenset(KIND_OF), type_limits=limits)
        service, _, _, _ = make_service(policy)
        self.assertFalse(service.accept(CUSTOMER, io.BytesIO(file_of(sniff.PNG, 1500))).duplicate)
        # 1500 is now the largest limit of all, so reading stops there, before the type is known.
        with self.assertRaises(UploadRejected) as ctx:
            service.accept(CUSTOMER, io.BytesIO(file_of(sniff.PNG, 1501)))
        self.assertIs(ctx.exception.code, RejectCode.TOO_LARGE)
        self.assertEqual(policy.limit_for(sniff.PNG), 1500)

    def test_the_customers_limit_never_raises_a_types_limit(self) -> None:
        limits = limits_by_type(pdf=5000, image=2000, sheet=1000)
        policy = UploadPolicy(max_bytes=10_000, allowed_types=frozenset(KIND_OF), type_limits=limits)
        service, _, _, _ = make_service(policy)
        with self.assertRaises(UploadRejected) as ctx:
            service.accept(CUSTOMER, io.BytesIO(file_of(sniff.TEXT, 1001)))
        self.assertEqual((ctx.exception.kind, ctx.exception.size, ctx.exception.limit), ("sheet", 1001, 1000))


class MessageTest(unittest.TestCase):
    """The words match the screen's (frontend/components/documents/logic.ts, tooLargeMessage)."""

    def test_each_kind_has_its_own_words(self) -> None:
        self.assertEqual(
            too_large_message("image", 34 * MB, 20 * MB),
            "Photos and scans can be up to 20 MB. This one is 34 MB."
            " Save it at a lower resolution and try again.",
        )
        self.assertEqual(
            too_large_message("pdf", 62 * MB, 50 * MB),
            "PDFs can be up to 50 MB. This one is 62 MB. Split it or save it smaller and try again.",
        )
        self.assertEqual(
            too_large_message("sheet", 14 * MB, 10 * MB),
            "Spreadsheets and CSV files can be up to 10 MB. This one is 14 MB."
            " Split it into smaller files and try again.",
        )

    def test_the_size_is_rounded_up_and_the_limit_down(self) -> None:
        self.assertIn("up to 20 MB. This one is 21 MB.", too_large_message("image", 20 * MB + 1, 20 * MB))
        self.assertIn("up to 19 MB. This one is 20 MB.", too_large_message("image", 20 * MB, 20 * MB - 1))

    def test_a_file_over_every_limit_is_told_the_largest_one(self) -> None:
        self.assertEqual(
            over_every_limit_message(50 * MB),
            "Files can be up to 50 MB. This one is larger. Split it or save it smaller and try again.",
        )


class PartialFailureTest(unittest.TestCase):
    def test_storage_failure_leaves_no_record_and_no_job(self) -> None:
        service, repo, storage, queue = make_service()
        storage.fail_put = True
        with self.assertRaises(BackendUnavailable):
            service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        self.assertEqual(repo.records_for(CUSTOMER), [])
        self.assertEqual(queue.jobs, [])

    def test_database_failure_deletes_the_stored_file(self) -> None:
        service, repo, storage, queue = make_service()
        repo.fail_add = True
        with self.assertRaises(BackendUnavailable):
            service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        self.assertEqual(storage.count(), 0)
        self.assertEqual(queue.jobs, [])

    def test_a_folder_gone_by_the_insert_deletes_the_stored_file_and_queues_nothing(self) -> None:
        service, repo, storage, queue = make_service()
        with self.assertRaises(UnknownFolder):
            service.accept(CUSTOMER, io.BytesIO(PDF_BYTES), folder_id="gone")
        self.assertEqual(repo.records_for(CUSTOMER), [])
        self.assertEqual(storage.count(), 0)
        self.assertEqual(queue.jobs, [])

    def test_queue_failure_undoes_everything_and_a_retry_works(self) -> None:
        service, repo, storage, queue = make_service()
        queue.fail_enqueue = True
        with self.assertRaises(BackendUnavailable):
            service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        self.assertEqual(repo.records_for(CUSTOMER), [])
        self.assertEqual(storage.count(), 0)

        queue.fail_enqueue = False
        retry = service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        self.assertFalse(retry.duplicate)
        self.assertEqual(len(queue.jobs), 1)


class FailedUndoTest(unittest.TestCase):
    def test_file_is_kept_while_its_record_could_not_be_removed(self) -> None:
        service, repo, storage, queue = make_service()
        queue.fail_enqueue = True
        repo.fail_remove = True
        with self.assertRaises(BackendUnavailable) as ctx:
            service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        self.assertEqual(str(ctx.exception), "queue down")  # the original error
        self.assertEqual(ctx.exception.__notes__, ["undo step failed: remove_record"])
        # Documented leftover: record and file kept together, no job. Never a
        # record pointing at a deleted file.
        (record,) = repo.records_for(CUSTOMER)
        self.assertEqual(storage.get(CUSTOMER, record.document_id), PDF_BYTES)
        self.assertEqual(queue.jobs, [])

    def test_database_failure_keeps_its_own_error_when_deleting_the_file_fails(self) -> None:
        service, repo, storage, queue = make_service()
        repo.fail_add = True
        storage.fail_delete = True
        with self.assertRaises(BackendUnavailable) as ctx:
            service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        self.assertEqual(str(ctx.exception), "database down")
        self.assertIn("undo step failed: delete_file", ctx.exception.__notes__)
        self.assertEqual(queue.jobs, [])

    def test_a_folder_gone_keeps_its_own_error_when_deleting_the_file_fails(self) -> None:
        service, _, storage, queue = make_service()
        storage.fail_delete = True
        with self.assertRaises(UnknownFolder) as ctx:
            service.accept(CUSTOMER, io.BytesIO(PDF_BYTES), folder_id="gone")
        self.assertEqual(ctx.exception.__notes__, ["undo step failed: delete_file"])
        self.assertEqual(queue.jobs, [])

    def test_spooled_copy_is_closed_when_storing_fails(self) -> None:
        service, _, storage, _ = make_service()
        storage.fail_put = True
        with self.assertRaises(BackendUnavailable):
            service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        assert storage.last_put_stream is not None
        self.assertTrue(storage.last_put_stream.closed)


class PolicyTest(unittest.TestCase):
    def test_policy_refuses_bad_values(self) -> None:
        pdf_only = {sniff.PDF: 10}
        cases: tuple[dict[str, Any], ...] = (
            {"max_bytes": 0, "allowed_types": frozenset({sniff.PDF}), "type_limits": pdf_only},
            {"max_bytes": 10, "allowed_types": frozenset(), "type_limits": pdf_only},
            {"max_bytes": 10, "allowed_types": frozenset({"exe"}), "type_limits": pdf_only},
            {"max_bytes": 10, "allowed_types": frozenset({sniff.PDF}), "type_limits": {sniff.PDF: 0}},
            {"max_bytes": 10, "allowed_types": frozenset({sniff.PDF}), "type_limits": {**pdf_only, "exe": 5}},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                UploadPolicy(**kwargs)

    def test_every_allowed_type_needs_a_limit(self) -> None:
        with self.assertRaises(ValueError):
            UploadPolicy(
                max_bytes=10, allowed_types=frozenset({sniff.PDF, sniff.PNG}), type_limits={sniff.PDF: 10}
            )
        # Old .xls and .doc files (ole2) have no size group in PRD U2, so they cannot be allowed.
        with self.assertRaises(ValueError):
            UploadPolicy(max_bytes=10, allowed_types=frozenset({sniff.OLE2}), type_limits={sniff.OLE2: 10})

    def test_a_type_with_no_limit_is_refused(self) -> None:
        policy = UploadPolicy(max_bytes=10, allowed_types=frozenset({sniff.PDF}), type_limits={sniff.PDF: 10})
        with self.assertRaises(ValueError):
            policy.limit_for(sniff.PNG)
        service, _, storage, _ = make_service(policy)
        with self.assertRaises(UploadRejected) as ctx:
            service.accept(CUSTOMER, io.BytesIO(file_of(sniff.PNG, 5)))
        self.assertIs(ctx.exception.code, RejectCode.UNSUPPORTED_TYPE)
        self.assertEqual(storage.count(), 0)

    def test_a_types_limit_is_its_own_lowered_but_never_raised_by_the_customers(self) -> None:
        limits = limits_by_type(pdf=5000, image=2000, sheet=1000)
        every_type = frozenset(KIND_OF)
        roomy = UploadPolicy(max_bytes=10_000, allowed_types=every_type, type_limits=limits)
        tight = UploadPolicy(max_bytes=1500, allowed_types=every_type, type_limits=limits)
        self.assertEqual({t: roomy.limit_for(t) for t in every_type}, limits)
        self.assertEqual(
            {t: tight.limit_for(t) for t in every_type},
            {"pdf": 1500, "png": 1500, "jpeg": 1500, "tiff": 1500, "text": 1000, "zip": 1000},
        )
        self.assertEqual((roomy.largest_limit, tight.largest_limit), (5000, 1500))

    def test_the_largest_limit_counts_only_allowed_types(self) -> None:
        limits = limits_by_type(pdf=5000, image=2000, sheet=1000)
        sheets = UploadPolicy(max_bytes=10_000, allowed_types=frozenset({sniff.TEXT}), type_limits=limits)
        self.assertEqual(sheets.largest_limit, 1000)

    def test_limits_by_type_gives_every_type_of_each_kind_its_kinds_limit(self) -> None:
        self.assertEqual(
            limits_by_type(pdf=3, image=2, sheet=1),
            {"pdf": 3, "png": 2, "jpeg": 2, "tiff": 2, "text": 1, "zip": 1},
        )
        self.assertEqual(
            dict(KIND_OF),
            {"pdf": "pdf", "png": "image", "jpeg": "image", "tiff": "image", "text": "sheet", "zip": "sheet"},
        )


class VersionTest(unittest.TestCase):
    """``accept_version`` (PRD 1 F6): the checks, duplicate rule and undo steps of
    ``accept``. The repository puts the record in the family: the next number and
    the family's folder (the fake's rule, like PostgreSQL's)."""

    def _family(self) -> tuple[UploadService, MemoryRepository, MemoryStorage, MemoryQueue, str]:
        service, repo, storage, queue = make_service()
        repo.folders.add((CUSTOMER, "folder-1"))
        first = service.accept(CUSTOMER, io.BytesIO(PDF_BYTES), folder_id="folder-1").document_id
        return service, repo, storage, queue, first

    def _version(self, service: UploadService, target: str, data: bytes = TEXT_BYTES) -> UploadResult:
        return service.accept_version(
            CUSTOMER, io.BytesIO(data), of_document_id=target, display_name="v2.csv", uploaded_by="u1"
        )

    def test_a_new_version_is_stored_recorded_in_the_family_and_queued(self) -> None:
        service, repo, storage, queue, first = self._family()
        result = self._version(service, first)
        again = self._version(service, result.document_id, TEXT_BYTES + b"3")
        self.assertFalse(result.duplicate or again.duplicate)
        stored = [repo.get(CUSTOMER, r.document_id) for r in (result, again)]
        self.assertEqual(
            [(r.family_id, r.version_no, r.folder_id, r.display_name, r.uploaded_by) for r in stored if r],
            [(first, 2, "folder-1", "v2.csv", "u1"), (first, 3, "folder-1", "v2.csv", "u1")],
        )
        self.assertEqual(storage.get(CUSTOMER, result.document_id), TEXT_BYTES)
        self.assertEqual(
            queue.jobs[1:], [Job(CUSTOMER, result.document_id), Job(CUSTOMER, again.document_id)]
        )

    def test_the_same_bytes_as_any_document_are_a_duplicate_and_nothing_is_stored(self) -> None:
        service, _, storage, queue, first = self._family()
        result = self._version(service, first, PDF_BYTES)
        self.assertEqual((result.document_id, result.duplicate), (first, True))
        self.assertEqual((storage.count(), len(queue.jobs)), (1, 1))

    def test_a_family_in_trash_or_no_document_keeps_nothing(self) -> None:
        service, repo, storage, queue, first = self._family()
        repo.trashed_families.add((CUSTOMER, first))
        for target in (first, "no-such-document"):
            with self.subTest(target=target), self.assertRaises(UnknownFamily):
                self._version(service, target)
        self.assertEqual((storage.count(), len(queue.jobs), len(repo.records_for(CUSTOMER))), (1, 1, 1))

    def test_a_queue_failure_undoes_the_version(self) -> None:
        service, repo, storage, queue, first = self._family()
        queue.fail_enqueue = True
        with self.assertRaises(BackendUnavailable):
            self._version(service, first)
        self.assertEqual((storage.count(), len(repo.records_for(CUSTOMER))), (1, 1))


if __name__ == "__main__":
    unittest.main()
