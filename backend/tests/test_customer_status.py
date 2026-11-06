"""A disabled organisation (0014, super admin PR 1) with fakes: the worker fails
its job with ``customer_disabled`` before any scan, read or model call, another
customer's job runs as before, and the file list says why. The database side
(the grant, sign-in, sessions, the real check, the sweeps and the CLI) is in
tests/test_customer_status_db.py."""

import io
import unittest

from kaagaz.api.views import status_message
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.worker import JobFailed
from kaagaz.pipeline import DocumentProcessor
from kaagaz.scanning.gate import DocumentStatus
from tests.fakes import FakeEmbedder, FakePreparer, fake_sheet_pages
from tests.test_pipeline import CSV, CSV_NAME, CUSTOMER, System, build, make_worker, real_socket

OTHER = "cust-b"


def _processor(system: System, asked: list[str], embed: FakeEmbedder) -> DocumentProcessor:
    """The fakes' processor, with a status check that only ``CUSTOMER`` fails."""

    def customer_active(customer_id: str) -> bool:
        asked.append(customer_id)
        return customer_id != CUSTOMER

    return DocumentProcessor(
        gate=system.gate,
        statuses=system.statuses,
        documents=system.repo,
        socket=real_socket(),
        prepare=FakePreparer().prepare,
        sheet_pages=fake_sheet_pages,
        page_images=system.page_images,
        store=system.store,
        embed=embed,
        customer_active=customer_active,
    )


class DisabledCustomerJobTest(unittest.TestCase):
    def test_a_disabled_customers_job_fails_before_anything_is_read(self) -> None:
        system, embed = build(), FakeEmbedder()
        asked: list[str] = []
        processor = _processor(system, asked, embed)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV), display_name=CSV_NAME).document_id

        with self.assertRaises(JobFailed) as failed:
            processor(Job(CUSTOMER, doc), lambda: None)

        self.assertEqual((failed.exception.code, failed.exception.retryable), ("customer_disabled", False))
        self.assertEqual(asked, [CUSTOMER])
        self.assertEqual(system.scanner.scanned, [])  # not even scanned
        self.assertEqual(system.store.saves, 0)
        self.assertEqual(embed.calls, [])  # no model call
        self.assertEqual(system.repo.failure_codes, {(CUSTOMER, doc): "customer_disabled"})
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.FAILED)

    def test_the_worker_buries_it_and_another_customers_job_still_runs(self) -> None:
        system, embed = build(), FakeEmbedder()
        asked: list[str] = []
        worker = make_worker(system.queue, _processor(system, asked, embed), system.repo)
        disabled = system.upload.accept(CUSTOMER, io.BytesIO(CSV), display_name=CSV_NAME).document_id
        other = system.upload.accept(OTHER, io.BytesIO(CSV), display_name=CSV_NAME).document_id

        worker.run_once()
        worker.run_once()

        self.assertEqual([failure.code for failure in system.queue.failed.values()], ["customer_disabled"])
        self.assertEqual(system.repo.failure_codes, {(CUSTOMER, disabled): "customer_disabled"})
        self.assertIs(system.statuses.get_status(OTHER, other), DocumentStatus.READY)
        self.assertEqual(len(system.store.for_document(OTHER, other)), 3)  # one piece a row
        self.assertEqual(system.store.for_document(CUSTOMER, disabled), [])


class DisabledCustomerMessageTest(unittest.TestCase):
    def test_the_file_list_says_the_organisation_was_turned_off(self) -> None:
        message = status_message("failed", "customer_disabled", detected_type="csv", max_pages=100)
        self.assertEqual(
            message, "This file was not read because your organisation was turned off. Read it again."
        )


if __name__ == "__main__":
    unittest.main()
