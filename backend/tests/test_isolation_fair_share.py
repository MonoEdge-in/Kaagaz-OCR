"""PRD 5 Q5, security: fair share leaks nothing between customers.

Customer B's one job gets byte-identical replies whether customer A has 0 or 50 jobs
waiting (and every slot of A's taken): the same handler call, the same queue outcome
and the same log lines (time aside). A's own hand-backs log A's ids and the fixed code
``customer_busy`` only, never a count and never B.
"""

import io
import json
import logging
import unittest

from kaagaz.ingestion.ports import Job
from kaagaz.jobs.fair_share import FairShare
from kaagaz.jobs.worker import Heartbeat
from kaagaz.worker_main import JsonFormatter, admission, job_worker
from tests.lease_queue import FakeClock, LeaseQueue
from tests.test_worker_defer import CUSTOMER_A, CUSTOMER_B, MemoryLocks, hold_slots

B_JOB = Job(CUSTOMER_B, "00000000-0000-4000-8000-0000000000b1")
A_JOBS = [Job(CUSTOMER_A, f"00000000-0000-4000-8000-{n:012d}") for n in range(50)]


def run_until_b_is_done(a_jobs: list[Job]) -> tuple[list[str], list[str], list[Job], int]:
    """Run one worker copy until B's job is done. Returns B's log lines (time left
    out), A's log lines, the jobs the handler ran, and B's jobs left in the queue."""
    locks = MemoryLocks()
    if a_jobs:
        hold_slots(locks, CUSTOMER_A, 2)  # two of A's documents are being read elsewhere
    queue = LeaseQueue(FakeClock(), lease_seconds=60.0, max_attempts=8)
    for job in [*a_jobs, B_JOB]:
        queue.enqueue(job)
    ran: list[Job] = []

    def handler(job: Job, beat: Heartbeat) -> None:
        ran.append(job)

    worker = job_worker(
        queue,
        handler,
        max_attempts=5,
        mark_exhausted=lambda customer, document: None,
        record_attempt=lambda customer, document, attempt, most: None,
        # Changed on purpose (#463 review): the check's handler is required.
        mark_check_exhausted=lambda customer, check: None,
        admit=admission(lambda: "working", FairShare(locks, 2).slot),
    )
    stream = io.StringIO()
    log_handler = logging.StreamHandler(stream)
    log_handler.setFormatter(JsonFormatter())
    worker_log = logging.getLogger("kaagaz.worker")
    worker_log.addHandler(log_handler)
    old_level = worker_log.level
    worker_log.setLevel(logging.DEBUG)
    try:
        while B_JOB not in ran:
            worker.run_once()
    finally:
        worker_log.removeHandler(log_handler)
        worker_log.setLevel(old_level)
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    for line in lines:
        line.pop("time")
    b_lines = [json.dumps(line, sort_keys=True) for line in lines if line.get("customer_id") == CUSTOMER_B]
    a_lines = [json.dumps(line, sort_keys=True) for line in lines if line.get("customer_id") != CUSTOMER_B]
    b_left = queue.pending() - len(a_jobs)  # A's jobs all wait in the queue
    return b_lines, a_lines, [job for job in ran if job.customer_id == CUSTOMER_B], b_left


class FairShareIsolationTest(unittest.TestCase):
    def test_b_gets_the_same_replies_whether_a_has_0_or_50_jobs(self) -> None:
        alone = run_until_b_is_done([])
        crowded = run_until_b_is_done(A_JOBS)
        self.assertEqual((alone[0], alone[2], alone[3]), (crowded[0], crowded[2], crowded[3]))
        self.assertEqual(alone[2], [B_JOB])
        self.assertEqual(alone[3], 0)  # acked

    def test_a_hand_back_says_only_a_ids_and_the_fixed_code(self) -> None:
        _, a_lines, _, _ = run_until_b_is_done(A_JOBS)
        self.assertEqual(len(a_lines), 50)
        for line, job in zip(a_lines, A_JOBS, strict=True):
            self.assertEqual(
                json.loads(line),
                {
                    "level": "DEBUG",
                    "logger": "kaagaz.worker",
                    "message": "job deferred",
                    "customer_id": CUSTOMER_A,
                    "document_id": job.document_id,
                    "outcome": "deferred",
                    "code": "customer_busy",
                },
            )
            self.assertNotIn(CUSTOMER_B, line)


if __name__ == "__main__":
    unittest.main()
