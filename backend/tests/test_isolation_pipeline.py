"""Customer isolation through the whole pipeline (#23). Marked ``isolation``."""

import io
import unittest

from kaagaz.extraction.learned import LearnedCorrection
from kaagaz.ingestion.ports import Job
from kaagaz.scanning.gate import DocumentStatus
from tests.test_pipeline import CSV, build

A, B = "cust-a", "cust-b"


class IsolationPipelineTest(unittest.TestCase):
    def test_job_for_another_customers_document_reads_and_writes_nothing(self) -> None:
        system = build()
        doc = system.upload.accept(A, io.BytesIO(CSV)).document_id
        system.queue.receive()  # take A's real job out of the way

        system.queue.enqueue(Job(B, doc))  # forged: B with A's document id
        system.clock.advance(1)
        system.worker.run_once()

        self.assertEqual([f.code for f in system.queue.failed.values()], ["document_missing"])
        self.assertEqual(system.store.for_document(B, doc), [])
        self.assertIsNone(system.statuses.get_status(B, doc))
        self.assertEqual(system.storage.get(A, doc), CSV)
        self.assertEqual(system.store.saves, 0)
        self.assertEqual(system.repo.failure_codes, {})  # neither A's nor B's marked failed
        self.assertIsNone(system.statuses.get_status(A, doc))

    def test_learned_corrections_of_one_customer_never_reach_another(self) -> None:
        system = build()
        system.store.learned[A] = [LearnedCorrection("invoice", "INV-1", "A-FIX")]
        doc_b = system.upload.accept(B, io.BytesIO(CSV)).document_id
        system.worker.run_once()
        texts = [v.value_text for v in system.store.values_for(B, doc_b)]
        self.assertIn("INV-1", texts)
        self.assertNotIn("A-FIX", texts)

    def test_same_file_from_two_customers_gives_separate_pieces(self) -> None:
        system = build()
        doc_a = system.upload.accept(A, io.BytesIO(CSV)).document_id
        doc_b = system.upload.accept(B, io.BytesIO(CSV)).document_id
        system.worker.run_once()
        system.worker.run_once()

        pieces_a = system.store.for_document(A, doc_a)
        pieces_b = system.store.for_document(B, doc_b)
        self.assertTrue(pieces_a and pieces_b)
        self.assertTrue(all(p.customer_id == A for p in pieces_a))
        self.assertTrue(all(p.customer_id == B for p in pieces_b))
        self.assertEqual(system.store.for_document(B, doc_a), [])
        self.assertEqual(system.store.values_for(B, doc_a), [])
        self.assertEqual(len(system.store.values_for(A, doc_a)), len(system.store.values_for(B, doc_b)))
        self.assertIs(system.statuses.get_status(A, doc_a), DocumentStatus.READY)
        self.assertIs(system.statuses.get_status(B, doc_b), DocumentStatus.READY)


if __name__ == "__main__":
    unittest.main()
