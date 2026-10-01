import unittest

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.ports import LeaseLost
from kaagaz.jobs.worker import Handler, Heartbeat, JobFailed, Worker
from tests.lease_queue import FakeClock, LeaseQueue

LEASE = 30.0
MAX_ATTEMPTS = 3
# The queue's own maximum receives: above MAX_ATTEMPTS + 1, as worker_main requires,
# so a delivery is left to mark the document even when the first marking fails.
QUEUE_MAX_RECEIVES = MAX_ATTEMPTS + 3
GOOD, POISON, OTHER = Job("cust-a", "good"), Job("cust-a", "poison"), Job("cust-a", "other")


def make_queue(max_attempts: int = QUEUE_MAX_RECEIVES) -> tuple[FakeClock, LeaseQueue]:
    clock = FakeClock()
    return clock, LeaseQueue(clock, lease_seconds=LEASE, max_attempts=max_attempts)


class RenewalLog(LeaseQueue):
    """A LeaseQueue that notes the time of each lease renewal."""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__(clock, lease_seconds=LEASE, max_attempts=QUEUE_MAX_RECEIVES)
        self.renewed_at: list[float] = []

    def extend(self, receipt: str) -> None:
        self.renewed_at.append(self._clock.now())
        super().extend(receipt)


def make_worker(queue: LeaseQueue, handler: Handler, marked: list[Job] | None = None) -> Worker:
    """The worker under test, with MAX_ATTEMPTS tries per job. ``marked`` collects
    every job ``on_exhausted`` is called with."""
    sink: list[Job] = [] if marked is None else marked
    return Worker(queue, handler, max_attempts=MAX_ATTEMPTS, on_exhausted=sink.append)


class LeaseQueueTest(unittest.TestCase):
    def test_jobs_come_out_in_order(self) -> None:
        _, queue = make_queue()
        queue.enqueue(GOOD)
        queue.enqueue(OTHER)
        first, second = queue.receive(), queue.receive()
        assert first is not None and second is not None
        self.assertEqual([first.job, second.job], [GOOD, OTHER])

    def test_leased_job_is_hidden_until_the_lease_runs_out(self) -> None:
        clock, queue = make_queue()
        queue.enqueue(GOOD)
        first = queue.receive()
        clock.advance(LEASE - 0.001)
        self.assertIsNone(queue.receive())
        clock.advance(0.001)  # expiry is "now >= deadline"
        again = queue.receive()
        assert first is not None and again is not None
        self.assertEqual((first.attempt, again.attempt), (1, 2))

    def test_stale_receipt_is_refused(self) -> None:
        clock, queue = make_queue()
        queue.enqueue(GOOD)
        slow = queue.receive()
        clock.advance(LEASE)
        fresh = queue.receive()
        assert slow is not None and fresh is not None
        with self.assertRaises(LeaseLost):
            queue.ack(slow.receipt)
        queue.ack(fresh.receipt)  # the current holder can still finish it
        self.assertEqual(queue.pending(), 0)

    def test_settings_are_validated(self) -> None:
        for kwargs in ({"lease_seconds": 0, "max_attempts": 3}, {"lease_seconds": 1, "max_attempts": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                LeaseQueue(FakeClock(), **kwargs)


class WorkerTest(unittest.TestCase):
    def test_successful_job_is_acked_once(self) -> None:
        _, queue = make_queue()
        queue.enqueue(GOOD)
        seen: list[Job] = []
        worker = make_worker(queue, lambda job, beat: seen.append(job))

        self.assertTrue(worker.run_once())
        self.assertFalse(worker.run_once())
        self.assertEqual(seen, [GOOD])
        self.assertEqual(queue.pending(), 0)

    def test_empty_queue_returns_false(self) -> None:
        _, queue = make_queue()
        self.assertFalse(make_worker(queue, lambda job, beat: None).run_once())

    def test_retryable_failure_is_retried_then_the_last_delivery_marks_and_buries(self) -> None:
        clock, queue = make_queue()
        queue.enqueue(POISON)
        calls = []
        marked: list[tuple[Job, int]] = []

        def handler(job: Job, beat: Heartbeat) -> None:
            calls.append(job)
            raise JobFailed("backend_down", retryable=True)

        def on_exhausted(job: Job) -> None:
            marked.append((job, len(queue.failed)))  # how many jobs were buried by then

        worker = Worker(queue, handler, max_attempts=MAX_ATTEMPTS, on_exhausted=on_exhausted)
        for _ in range(MAX_ATTEMPTS + 1):
            self.assertTrue(worker.run_once())
            clock.advance(LEASE)
        self.assertFalse(worker.run_once())  # buried, nothing left

        self.assertEqual(len(calls), MAX_ATTEMPTS)  # every attempt ran; the next delivery only marks
        self.assertEqual(marked, [(POISON, 0)])  # called once, before the job was buried
        (failed,) = queue.failed.values()
        self.assertEqual(
            (failed.job, failed.attempts, failed.code), (POISON, MAX_ATTEMPTS + 1, "retries_exhausted")
        )

    def test_each_attempt_is_recorded_before_the_handler_runs(self) -> None:
        clock, queue = make_queue()
        queue.enqueue(POISON)
        events: list[tuple[str, int]] = []

        def handler(job: Job, beat: Heartbeat) -> None:
            events.append(("ran", 0))
            raise JobFailed("backend_down", retryable=True)

        worker = Worker(
            queue,
            handler,
            max_attempts=MAX_ATTEMPTS,
            on_exhausted=lambda job: events.append(("exhausted", 0)),
            on_attempt=lambda job, attempt: events.append(("attempt", attempt)),
        )
        for _ in range(MAX_ATTEMPTS + 1):
            worker.run_once()
            clock.advance(LEASE)
        self.assertEqual(
            events,
            [
                ("attempt", 1),
                ("ran", 0),
                ("attempt", 2),
                ("ran", 0),
                ("attempt", 3),
                ("ran", 0),
                ("exhausted", 0),
            ],
        )

    def test_marking_that_fails_is_tried_again_on_the_next_delivery(self) -> None:
        """The database is down when the job's tries run out: the job is not buried
        (nothing would ever mark the document), and the next delivery marks it."""
        clock, queue = make_queue()
        queue.enqueue(GOOD)
        for _ in range(MAX_ATTEMPTS):
            queue.receive()  # earlier deliveries that crashed
            clock.advance(LEASE)
        tries: list[Job] = []

        def mark(job: Job) -> None:
            tries.append(job)
            if len(tries) == 1:
                raise BackendUnavailable("database")

        worker = Worker(queue, lambda job, beat: None, max_attempts=MAX_ATTEMPTS, on_exhausted=mark)
        with self.assertRaises(BackendUnavailable):
            worker.run_once()
        self.assertEqual(queue.failed, {})
        clock.advance(LEASE)
        self.assertTrue(worker.run_once())
        self.assertEqual(tries, [GOOD, GOOD])
        self.assertEqual([(f.job, f.code) for f in queue.failed.values()], [(GOOD, "retries_exhausted")])

    def test_an_unexpected_error_on_every_delivery_still_gets_marked(self) -> None:
        clock, queue = make_queue()
        queue.enqueue(GOOD)
        marked: list[Job] = []

        def buggy(job: Job, beat: Heartbeat) -> None:
            raise RuntimeError("bug")

        worker = make_worker(queue, buggy, marked)
        for _ in range(MAX_ATTEMPTS):
            with self.assertRaises(RuntimeError):
                worker.run_once()
            clock.advance(LEASE)
        self.assertTrue(worker.run_once())  # past the last attempt: marked and buried, the handler not run

        self.assertEqual(marked, [GOOD])
        self.assertEqual([(f.job, f.code) for f in queue.failed.values()], [(GOOD, "retries_exhausted")])

    def test_success_on_a_retry_is_not_marked_or_binned(self) -> None:
        clock, queue = make_queue()
        queue.enqueue(GOOD)
        attempts = []
        marked: list[Job] = []

        def handler(job: Job, beat: Heartbeat) -> None:
            attempts.append(job)
            if len(attempts) == 1:
                raise JobFailed("backend_down", retryable=True)

        worker = make_worker(queue, handler, marked)
        worker.run_once()
        clock.advance(LEASE)
        worker.run_once()  # attempt 2 of 3 succeeds
        self.assertEqual(queue.failed, {})
        self.assertEqual(queue.pending(), 0)
        self.assertEqual(marked, [])

    def test_a_lost_lease_on_the_last_delivery_does_not_crash_the_worker(self) -> None:
        clock, queue = make_queue()  # the queue itself would still retry
        queue.enqueue(GOOD)
        for _ in range(MAX_ATTEMPTS):
            queue.receive()  # earlier deliveries that crashed
            clock.advance(LEASE)

        def slow_mark(job: Job) -> None:
            clock.advance(LEASE + 1)  # the lease runs out while marking

        worker = Worker(queue, lambda job, beat: None, max_attempts=MAX_ATTEMPTS, on_exhausted=slow_mark)
        self.assertTrue(worker.run_once())
        self.assertEqual(queue.failed, {})  # LeaseLost from bury: the next delivery decides
        self.assertEqual(queue.pending(), 1)

    def test_max_attempts_must_allow_one_attempt(self) -> None:
        _, queue = make_queue()
        for max_attempts in (0, -1):
            with self.subTest(max_attempts=max_attempts), self.assertRaises(ValueError):
                Worker(
                    queue, lambda job, beat: None, max_attempts=max_attempts, on_exhausted=lambda job: None
                )

    def test_permanent_failure_is_buried_at_once_and_not_retried(self) -> None:
        clock, queue = make_queue()
        queue.enqueue(POISON)
        calls = []
        marked: list[Job] = []

        def handler(job: Job, beat: Heartbeat) -> None:
            calls.append(job)
            raise JobFailed("virus_found", retryable=False)

        worker = make_worker(queue, handler, marked)
        worker.run_once()
        clock.advance(LEASE * 10)
        self.assertFalse(worker.run_once())
        self.assertEqual(len(calls), 1)
        (failed,) = queue.failed.values()
        self.assertEqual((failed.code, failed.attempts), ("virus_found", 1))
        self.assertEqual(marked, [])  # a failure for good keeps its own code

    def test_poison_job_does_not_block_the_jobs_behind_it(self) -> None:
        clock, queue = make_queue()
        queue.enqueue(POISON)
        queue.enqueue(GOOD)
        done: list[Job] = []

        def handler(job: Job, beat: Heartbeat) -> None:
            if job == POISON:
                raise JobFailed("broken_file", retryable=True)
            done.append(job)

        worker = make_worker(queue, handler)
        worker.run_once()  # poison fails
        worker.run_once()  # good runs next, without waiting for the poison
        self.assertEqual(done, [GOOD])

        for _ in range(MAX_ATTEMPTS):
            clock.advance(LEASE)
            worker.run_once()
        self.assertEqual([f.job for f in queue.failed.values()], [POISON])

    def test_long_job_renews_its_lease_and_is_not_handed_out_twice(self) -> None:
        clock, queue = make_queue()
        queue.enqueue(GOOD)
        seen_by_other_worker = []

        def slow_handler(job: Job, beat: Heartbeat) -> None:
            for _ in range(4):  # runs for 4 x 20s, longer than one lease
                clock.advance(20)
                beat()
                other = queue.receive()
                if other is not None:
                    seen_by_other_worker.append(other)

        make_worker(queue, slow_handler).run_once()
        self.assertEqual(seen_by_other_worker, [])
        self.assertEqual(queue.pending(), 0)

    def test_heartbeats_renew_the_lease_at_most_once_every_15_seconds(self) -> None:
        """R2 7: a heartbeat after every page or batch is hundreds of queue calls on a long
        document. On the worker's clock, a beat less than 15 s after the receive or the last
        renewal does not call the queue, and the job is still never handed out twice."""
        clock = FakeClock()
        queue = RenewalLog(clock)
        queue.enqueue(GOOD)
        handed_out = []

        def beats_every_5_seconds(job: Job, beat: Heartbeat) -> None:
            for _ in range(12):  # 60 s: two leases
                clock.advance(5)
                beat()
                handed_out.append(queue.receive())  # a second worker asks for work

        worker = Worker(
            queue,
            beats_every_5_seconds,
            max_attempts=MAX_ATTEMPTS,
            on_exhausted=lambda job: None,
            clock=clock.now,
        )
        self.assertTrue(worker.run_once())
        self.assertEqual(queue.renewed_at, [15, 30, 45, 60])
        self.assertEqual(handed_out, [None] * 12)
        self.assertEqual(queue.pending(), 0)

    def test_heartbeat_after_the_lease_is_lost_does_not_crash_the_worker(self) -> None:
        clock, queue = make_queue()
        queue.enqueue(GOOD)

        def too_slow(job: Job, beat: Heartbeat) -> None:
            clock.advance(LEASE + 1)
            beat()  # the lease is gone: LeaseLost inside the handler

        self.assertTrue(make_worker(queue, too_slow).run_once())
        again = queue.receive()  # the job is still there for the next delivery
        assert again is not None
        self.assertEqual(again.attempt, 2)

    def test_ack_after_the_lease_is_lost_leaves_the_job_for_the_new_holder(self) -> None:
        clock, queue = make_queue()
        queue.enqueue(GOOD)

        def slow_but_no_heartbeat(job: Job, beat: Heartbeat) -> None:
            clock.advance(LEASE + 1)

        self.assertTrue(make_worker(queue, slow_but_no_heartbeat).run_once())
        self.assertEqual(queue.pending(), 1)
        self.assertEqual(queue.failed, {})

    def test_bury_after_the_lease_is_lost_does_not_crash_the_worker(self) -> None:
        clock, queue = make_queue()
        queue.enqueue(POISON)

        def slow_permanent_failure(job: Job, beat: Heartbeat) -> None:
            clock.advance(LEASE + 1)
            raise JobFailed("broken_file", retryable=False)

        self.assertTrue(make_worker(queue, slow_permanent_failure).run_once())
        self.assertEqual(queue.failed, {})  # the next delivery decides
        self.assertEqual(queue.pending(), 1)

    def test_unexpected_error_propagates_and_the_job_comes_back(self) -> None:
        clock, queue = make_queue()
        queue.enqueue(GOOD)

        def buggy(job: Job, beat: Heartbeat) -> None:
            raise RuntimeError("bug")

        with self.assertRaises(RuntimeError):
            make_worker(queue, buggy).run_once()
        clock.advance(LEASE)
        again = queue.receive()
        assert again is not None
        self.assertEqual((again.job, again.attempt), (GOOD, 2))


if __name__ == "__main__":
    unittest.main()
