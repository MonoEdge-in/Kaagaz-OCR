"""The storage limit in the upload service (#516): a new file that would take the organisation
past its limit is refused ``storage_full`` with the bytes used and the limit, before anything is
stored; a file of exactly the bytes left goes in; a duplicate costs nothing, so it is answered
even when the organisation is full. Against PostgreSQL and through the API:
test_api_storage_limit."""

import io
import unittest

from kaagaz.ingestion.errors import RejectCode, UploadRejected
from tests.support import PDF_BYTES, TEXT_BYTES, make_service

CUSTOMER, OTHER = "cust-a", "cust-b"


class StorageLimitTest(unittest.TestCase):
    def test_exactly_the_bytes_left_go_in_and_one_byte_more_is_refused_before_it_is_stored(self) -> None:
        service, repo, storage, queue = make_service()
        repo.storage_limit = len(PDF_BYTES) + len(TEXT_BYTES)
        service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        service.accept(CUSTOMER, io.BytesIO(TEXT_BYTES))  # exactly full
        with self.assertRaises(UploadRejected) as refused:
            service.accept(CUSTOMER, io.BytesIO(TEXT_BYTES + b"1"))
        error = refused.exception
        self.assertEqual(error.code, RejectCode.STORAGE_FULL)
        self.assertEqual((error.used, error.limit), (repo.storage_limit, repo.storage_limit))
        self.assertEqual((storage.count(), len(repo.records_for(CUSTOMER)), len(queue.jobs)), (2, 2, 2))

    def test_one_byte_over_with_room_left_is_refused_too(self) -> None:
        service, repo, storage, _ = make_service()
        repo.storage_limit = len(PDF_BYTES) + len(TEXT_BYTES) - 1
        service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        with self.assertRaises(UploadRejected) as refused:
            service.accept(CUSTOMER, io.BytesIO(TEXT_BYTES))
        self.assertEqual(
            (refused.exception.code, refused.exception.used), (RejectCode.STORAGE_FULL, len(PDF_BYTES))
        )
        self.assertEqual(storage.count(), 1)

    def test_a_duplicate_costs_nothing_even_when_full(self) -> None:
        service, repo, storage, queue = make_service()
        repo.storage_limit = len(PDF_BYTES)
        first = service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        again = service.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        self.assertEqual((again.duplicate, again.document_id), (True, first.document_id))
        self.assertEqual((storage.count(), len(queue.jobs)), (1, 1))

    def test_a_new_version_is_refused_the_same_way(self) -> None:
        service, repo, storage, _ = make_service()
        repo.storage_limit = len(PDF_BYTES)
        first = service.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        with self.assertRaises(UploadRejected) as refused:
            service.accept_version(
                CUSTOMER,
                io.BytesIO(TEXT_BYTES),
                of_document_id=first,
                display_name="v2.csv",
                uploaded_by="u1",
            )
        self.assertEqual(refused.exception.code, RejectCode.STORAGE_FULL)
        self.assertEqual(storage.count(), 1)

    def test_another_organisations_files_never_count(self) -> None:
        service, repo, _, _ = make_service()
        repo.storage_limit = len(PDF_BYTES)
        service.accept(OTHER, io.BytesIO(PDF_BYTES))
        self.assertFalse(service.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).duplicate)
