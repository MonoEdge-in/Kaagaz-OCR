"""PRD 5 Q5 and Q5(b): a job that must wait is handed back to the queue without counting
an attempt, and the worker takes the next one.

* ``admit`` raises Deferred before ``on_attempt``: the job goes back with its seconds,
  no try is recorded, and its next delivery has the same attempt number.
* FairShare: a customer has MAX_ACTIVE_PER_CUSTOMER slots across every copy of the
  worker; a job of a customer with none free waits 30 s, another customer's runs.
  The slot is held for the whole try and let go after it, however it ends.
* Key problems (``KeyProblemNoAttemptTest``): a reader whose gate is at
  ``key_problem`` hands every job back for 300 s before it runs; a handler's
  BackendUnavailable("gemini_key") hands the job back for 300 s and goes on up, so
  the loop pauses. Neither costs an attempt. Any other outage is as before.

No database: ``MemoryLocks`` takes and lets go of keys as ``first_free_lock`` does
(tests/test_fair_share_db.py checks the real one).
"""

import logging
import unittest
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.fair_share import FairShare
from kaagaz.jobs.ports import Delivery, LeaseLost
from kaagaz.jobs.worker import Admit, Deferred, Heartbeat, Worker
from kaagaz.worker_main import admission
from tests.lease_queue import FakeClock, LeaseQueue

CUSTOMER_A = "aaaaaaaa-0000-4000-8000-00000000000a"
CUSTOMER_B = "bbbbbbbb-0000-4000-8000-00000000000b"
A1 = Job(CUSTOMER_A, "00000000-0000-4000-8000-0000000000a1")
A2 = Job(CUSTOMER_A, "00000000-0000-4000-8000-0000000000a2")
B1 = Job(CUSTOMER_B, "00000000-0000-4000-8000-0000000000b1")
MAX_ATTEMPTS = 3


class MemoryLocks:
    """``first_free_lock`` in memory: the first key not held, held for the block."""

    def __init__(self) -> None:
        self.held: set[str] = set()

    @contextmanager
    def __call__(self, keys: Sequence[str]) -> Iterator[str | None]:
        key = next((key for key in keys if key not in self.held), None)
        if key is not None:
            self.held.add(key)
        try:
            yield key
        finally:
            if key is not None:
                self.held.discard(key)


def fair_share(locks: MemoryLocks, max_active: int = 2) -> FairShare:
    return FairShare(locks, max_active)


def hold_slots(locks: MemoryLocks, customer_id: str, count: int) -> None:
    """Other copies of the worker reading ``count`` of this customer's documents."""
    locks.held.update(f"kaagaz.fair:{customer_id}:{slot}" for slot in range(count))


class Run:
    """A worker over the lease queue, noting the jobs run, the tries recorded and the
    slots held while each job ran."""

    def __init__(
        self,
        *jobs: Job,
        admit: Admit | None = None,
        error: Exception | None = None,
        locks: MemoryLocks | None = None,
    ) -> None:
        self.clock = FakeClock()
        self.queue = LeaseQueue(self.clock, lease_seconds=60.0, max_attempts=MAX_ATTEMPTS + 3)
        for job in jobs:
            self.queue.enqueue(job)
        self.ran: list[Job] = []
        self.tries: list[tuple[Job, int]] = []
        self.held_during: list[set[str]] = []
        self._error = error
        self._locks = locks
        self.worker = Worker(
            self.queue,
            self._handler,
            max_attempts=MAX_ATTEMPTS,
            on_exhausted=lambda job: None,
            on_attempt=lambda job, attempt: self.tries.append((job, attempt)),
            admit=admit,
        )

    def _handler(self, job: Job, beat: Heartbeat) -> None:
        self.ran.append(job)
        if self._locks is not None:
            self.held_during.append(set(self._locks.held))
        if self._error is not None:
            raise self._error

    def next_delivery_after(self, seconds: float) -> Delivery | None:
        self.clock.advance(seconds)
        return self.queue.receive()


def wait_always(seconds: int, code: str) -> Admit:
    @contextmanager
    def admit(job: Job) -> Iterator[None]:
        raise Deferred(seconds, code)
        yield  # a generator, so this is a context manager

    return admit


def working() -> str:
    return "working"


def key_problem() -> str:
    return "key_problem"


class DeferTest(unittest.TestCase):
    def test_a_deferred_job_goes_back_with_its_seconds_and_costs_no_attempt(self) -> None:
        run = Run(A1, admit=wait_always(30, "customer_busy"))
        self.assertTrue(run.worker.run_once())
        self.assertEqual((run.ran, run.tries, run.queue.deferred), ([], [], [(A1, 30)]))

        self.assertIsNone(run.next_delivery_after(29.9))  # still waiting
        delivery = run.next_delivery_after(0.1)
        assert delivery is not None
        self.assertEqual(delivery.attempt, 1)  # the deferred delivery was not counted

    def test_a_job_deferred_many_times_never_runs_out_of_attempts(self) -> None:
        run = Run(A1, admit=wait_always(30, "customer_busy"))
        for _ in range(MAX_ATTEMPTS * 4):
            self.assertTrue(run.worker.run_once())
            run.clock.advance(30)
        self.assertEqual((run.queue.failed, run.queue.pending()), ({}, 1))

    def test_a_deferral_whose_lease_was_lost_is_left_alone(self) -> None:
        class LostQueue(LeaseQueue):
            def defer(self, receipt: str, seconds: int, *, counted: bool = False) -> None:
                raise LeaseLost(receipt)

        queue = LostQueue(FakeClock(), lease_seconds=60.0, max_attempts=6)
        queue.enqueue(A1)
        worker = Worker(
            queue,
            lambda job, beat: None,
            max_attempts=3,
            on_exhausted=lambda job: None,
            admit=wait_always(30, "customer_busy"),
        )
        self.assertTrue(worker.run_once())


class FairShareTest(unittest.TestCase):
    def test_a_customer_with_every_slot_taken_waits_and_another_customer_runs(self) -> None:
        locks = MemoryLocks()
        hold_slots(locks, CUSTOMER_A, 2)  # two of A's documents are being read elsewhere
        run = Run(A1, B1, admit=admission(working, fair_share(locks).slot), locks=locks)

        self.assertTrue(run.worker.run_once())  # A1: no slot free
        self.assertTrue(run.worker.run_once())  # B1: runs at once
        self.assertEqual((run.ran, run.queue.deferred), ([B1], [(A1, 30)]))
        self.assertEqual(run.tries, [(B1, 1)])
        self.assertIn(f"kaagaz.fair:{CUSTOMER_B}:0", run.held_during[0])

    def test_a_worker_that_is_stopping_hands_a_new_job_straight_back(self) -> None:
        """Council 16 Oct 2026: a job received after SIGTERM is never started (Docker would cut
        it off, and it would wait out its lease); it goes back at once for another worker."""
        locks = MemoryLocks()
        run = Run(A1, admit=admission(working, fair_share(locks).slot, lambda: True), locks=locks)
        self.assertTrue(run.worker.run_once())
        self.assertEqual((run.ran, run.queue.deferred, run.tries), ([], [(A1, 0)], []))

    def test_the_slot_is_held_for_the_try_and_let_go_after_it_however_it_ends(self) -> None:
        for error in (None, BackendUnavailable("storage")):
            with self.subTest(error=error):
                locks = MemoryLocks()
                hold_slots(locks, CUSTOMER_A, 1)
                run = Run(A1, admit=admission(working, fair_share(locks).slot), error=error, locks=locks)
                if error is None:
                    run.worker.run_once()
                else:
                    with self.assertRaises(BackendUnavailable):
                        run.worker.run_once()
                self.assertEqual(
                    run.held_during, [{f"kaagaz.fair:{CUSTOMER_A}:0", f"kaagaz.fair:{CUSTOMER_A}:1"}]
                )
                self.assertEqual(locks.held, {f"kaagaz.fair:{CUSTOMER_A}:0"})

    def test_a_freed_slot_lets_the_waiting_job_in(self) -> None:
        locks = MemoryLocks()
        hold_slots(locks, CUSTOMER_A, 2)
        run = Run(A1, admit=admission(working, fair_share(locks).slot), locks=locks)
        run.worker.run_once()
        locks.held.discard(f"kaagaz.fair:{CUSTOMER_A}:1")  # one of the other reads finished
        run.clock.advance(30)
        run.worker.run_once()
        self.assertEqual((run.ran, run.tries), ([A1], [(A1, 1)]))

    def test_max_active_must_be_at_least_one(self) -> None:
        with self.assertRaises(ValueError):
            FairShare(MemoryLocks(), 0)

    def test_a_hand_back_is_logged_with_the_jobs_ids_and_a_fixed_code(self) -> None:
        locks = MemoryLocks()
        hold_slots(locks, CUSTOMER_A, 2)
        run = Run(A1, admit=admission(working, fair_share(locks).slot))
        with self.assertLogs("kaagaz.worker", logging.DEBUG) as logs:
            run.worker.run_once()
        (record,) = logs.records
        self.assertEqual(record.getMessage(), "job deferred")
        fields = {key: record.__dict__[key] for key in ("customer_id", "document_id", "outcome", "code")}
        self.assertEqual(
            fields,
            {
                "customer_id": A1.customer_id,
                "document_id": A1.document_id,
                "outcome": "deferred",
                "code": "customer_busy",
            },
        )


class KeyProblemNoAttemptTest(unittest.TestCase):
    """test_key_problem_no_attempt: a key problem is a person's fix, never a try."""

    def test_a_gate_with_a_key_problem_hands_every_job_back_for_300_s_before_it_runs(self) -> None:
        locks = MemoryLocks()
        run = Run(A1, B1, admit=admission(key_problem, fair_share(locks).slot), locks=locks)
        run.worker.run_once()
        run.worker.run_once()
        self.assertEqual((run.ran, run.tries), ([], []))
        self.assertEqual(run.queue.deferred, [(A1, 300), (B1, 300)])
        self.assertEqual(locks.held, set())  # no slot was taken
        self.assertIsNone(run.next_delivery_after(299))
        delivery = run.next_delivery_after(1)
        assert delivery is not None
        self.assertEqual(delivery.attempt, 1)

    def test_a_handler_key_problem_hands_the_job_back_and_still_pauses_the_loop(self) -> None:
        run = Run(A1, error=BackendUnavailable("gemini_key"))
        with self.assertRaises(BackendUnavailable):
            run.worker.run_once()
        self.assertEqual((run.tries, run.queue.deferred), ([(A1, 1)], [(A1, 300)]))
        self.assertIsNone(run.next_delivery_after(299))
        delivery = run.next_delivery_after(1)
        assert delivery is not None
        self.assertEqual(delivery.attempt, 1)  # the try it made is not counted

    def test_any_other_outage_is_not_handed_back(self) -> None:
        run = Run(A1, error=BackendUnavailable("gemini"))
        with self.assertRaises(BackendUnavailable):
            run.worker.run_once()
        self.assertEqual(run.queue.deferred, [])
        delivery = run.next_delivery_after(60)  # the lease runs out, as before
        assert delivery is not None
        self.assertEqual(delivery.attempt, 2)


if __name__ == "__main__":
    unittest.main()
