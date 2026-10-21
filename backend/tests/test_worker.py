import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import InterfaceError, OperationalError

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.ports import LeaseLost
from kaagaz.jobs.worker import (
    CRASHES_IN_A_ROW,
    RETRY_FIRST_SECONDS,
    RETRY_MOST_SECONDS,
    Deferred,
    Handler,
    Heartbeat,
    JobFailed,
    Worker,
    retry_delay,
)
from kaagaz.worker_main import run_until_stopped
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


def make_long_queue() -> tuple[FakeClock, LeaseQueue]:
    """The production lease (QUEUE_LEASE_SECONDS, 1200 s), so a crashed job that comes back
    sooner was handed back, not let go by a lease running out."""
    clock = FakeClock()
    return clock, LeaseQueue(clock, lease_seconds=1200.0, max_attempts=QUEUE_MAX_RECEIVES)


class RenewalLog(LeaseQueue):
    """A LeaseQueue that notes the time of each lease renewal."""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__(clock, lease_seconds=LEASE, max_attempts=QUEUE_MAX_RECEIVES)
        self.renewed_at: list[float] = []

    def extend(self, receipt: str) -> None:
        self.renewed_at.append(self._clock.now())
        super().extend(receipt)


def pending_after_two_beats(second_beat_after: float) -> int:
    """A 1200 s lease, a beat at 14.99 s (too soon to renew), then one ``second_beat_after``
    later, on the worker's clock. Returns the jobs left in the queue (0 once acked)."""
    clock = FakeClock()
    queue = LeaseQueue(clock, lease_seconds=1200.0, max_attempts=QUEUE_MAX_RECEIVES)
    queue.enqueue(GOOD)

    def two_beats(job: Job, beat: Heartbeat) -> None:
        clock.advance(14.99)
        beat()
        clock.advance(second_beat_after)
        beat()

    Worker(
        queue, two_beats, max_attempts=MAX_ATTEMPTS, on_exhausted=lambda job: None, clock=clock.now
    ).run_once()
    return queue.pending()


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
            clock.advance(RETRY_MOST_SECONDS)  # changed on purpose (council 16 Oct)
        self.assertFalse(worker.run_once())  # buried, nothing left

        self.assertEqual(len(calls), MAX_ATTEMPTS)  # every attempt ran; the next delivery only marks
        self.assertEqual(marked, [(POISON, 0)])  # called once, before the job was buried
        (failed,) = queue.failed.values()
        self.assertEqual(
            (failed.job, failed.attempts, failed.code), (POISON, MAX_ATTEMPTS + 1, "retries_exhausted")
        )

    def test_a_retryable_failure_comes_back_after_a_minute_counted_not_after_the_lease(self) -> None:
        """Council 16 Oct 2026: 1, 2, 4, 8, then 15 minutes, each try counted."""
        clock, queue = make_queue()
        queue.enqueue(POISON)
        calls: list[Job] = []

        def handler(job: Job, beat: Heartbeat) -> None:
            calls.append(job)
            raise JobFailed("scanner_unavailable", retryable=True)

        worker = Worker(queue, handler, max_attempts=MAX_ATTEMPTS, on_exhausted=lambda job: None)
        self.assertTrue(worker.run_once())
        clock.advance(RETRY_FIRST_SECONDS - 0.001)
        self.assertFalse(worker.run_once())  # not yet
        clock.advance(0.001)
        self.assertTrue(worker.run_once())
        self.assertEqual(len(calls), 2)
        self.assertEqual([retry_delay(n) for n in range(1, 7)], [60, 120, 240, 480, 900, 900])

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
            clock.advance(RETRY_MOST_SECONDS)  # changed on purpose (council 16 Oct)
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
        # Changed on purpose (council 17 Oct 2026): a crash no longer ends run_once; each try
        # is handed back counted after a flat minute, then the last delivery marks the job.
        clock, queue = make_long_queue()
        queue.enqueue(POISON)
        marked: list[Job] = []
        calls: list[Job] = []

        def buggy(job: Job, beat: Heartbeat) -> None:
            calls.append(job)
            raise RuntimeError("bug")

        worker = make_worker(queue, buggy, marked)
        for _ in range(MAX_ATTEMPTS):
            self.assertTrue(worker.run_once())  # the same job each time: never the process's fault
            clock.advance(RETRY_FIRST_SECONDS)
        self.assertTrue(worker.run_once())  # past the last attempt: marked and buried, the handler not run

        self.assertEqual(len(calls), MAX_ATTEMPTS)
        self.assertEqual(queue.deferred, [(POISON, RETRY_FIRST_SECONDS)] * MAX_ATTEMPTS)  # flat, not doubling
        self.assertEqual(marked, [POISON])
        self.assertEqual([(f.job, f.code) for f in queue.failed.values()], [(POISON, "retries_exhausted")])

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
        clock.advance(RETRY_FIRST_SECONDS)  # changed on purpose (council 16 Oct): back after a minute
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
            clock.advance(RETRY_MOST_SECONDS)  # changed on purpose (council 16 Oct)
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
        """A heartbeat after every page or batch is hundreds of queue calls on a long
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

    def test_a_worker_clock_that_steps_back_still_renews(self) -> None:
        """The clock should be monotonic. If it steps back anyway (50 s here), the next beat
        renews at once, rather than waiting for the clock to catch up while the lease runs out."""
        clock, worker_clock = FakeClock(), FakeClock(100.0)
        queue = RenewalLog(clock)
        queue.enqueue(GOOD)
        handed_out = []

        def beats_after_a_step_back(job: Job, beat: Heartbeat) -> None:
            clock.advance(20)
            worker_clock.advance(-50)
            beat()
            clock.advance(20)  # 40 s after the receive: past the first lease
            handed_out.append(queue.receive())  # a second worker asks for work

        worker = Worker(
            queue,
            beats_after_a_step_back,
            max_attempts=MAX_ATTEMPTS,
            on_exhausted=lambda job: None,
            clock=worker_clock.now,
        )
        self.assertTrue(worker.run_once())
        self.assertEqual(queue.renewed_at, [20])
        self.assertEqual(handed_out, [None])
        self.assertEqual(queue.pending(), 0)

    def test_a_beat_the_throttle_skips_does_not_move_the_lease(self) -> None:
        """The longest wait the throttle adds: a beat 14.99 s after the receive does not
        renew, so the next beat must still come inside the first lease (1200 s here)."""
        self.assertEqual(pending_after_two_beats(1185.0), 0)  # 1199.99 s renews: acked
        self.assertEqual(pending_after_two_beats(1185.02), 1)  # 1200.01 s: lease gone, left for later

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
        # Changed on purpose (council 17 Oct 2026): the crash is that job's. It no longer
        # propagates (the process used to end and the job wait out its 20-minute lease);
        # it comes back counted after a minute, and run_once says a job was handled. A
        # 1200 s lease, so the old 30 s one cannot pass this by running out.
        clock, queue = make_long_queue()
        queue.enqueue(GOOD)

        def buggy(job: Job, beat: Heartbeat) -> None:
            raise RuntimeError("bug")

        self.assertTrue(make_worker(queue, buggy).run_once())
        self.assertEqual(queue.deferred, [(GOOD, RETRY_FIRST_SECONDS)])
        clock.advance(RETRY_FIRST_SECONDS - 0.001)
        self.assertIsNone(queue.receive())
        clock.advance(0.001)
        again = queue.receive()
        assert again is not None
        self.assertEqual((again.job, again.attempt), (GOOD, 2))


class HandlerCrashTest(unittest.TestCase):
    """Council 17 Oct 2026: a damaged upload crashed every worker that took it, and the job
    came back after each 20-minute lease, up to five times. A handler's crash is now that
    job's; a crash outside the handler, a MemoryError or three different jobs crashing in a
    row still end the process."""

    def test_a_crashing_job_does_not_block_the_one_behind_it(self) -> None:
        _, queue = make_long_queue()
        queue.enqueue(POISON)
        queue.enqueue(GOOD)
        done: list[Job] = []

        def handler(job: Job, beat: Heartbeat) -> None:
            if job == POISON:
                raise KeyError("PDFObjectNotFound")
            done.append(job)

        worker = make_worker(queue, handler)
        self.assertTrue(worker.run_once())
        self.assertTrue(worker.run_once())
        self.assertEqual(done, [GOOD])

    def test_the_loop_runs_on_past_a_crash(self) -> None:
        _, queue = make_long_queue()
        queue.enqueue(POISON)
        queue.enqueue(GOOD)
        stop = threading.Event()
        done: list[Job] = []

        def handler(job: Job, beat: Heartbeat) -> None:
            if job == POISON:
                raise RuntimeError("bug")
            done.append(job)
            stop.set()  # the scripted stop, after the job behind the crash

        run_until_stopped(make_worker(queue, handler), stop, pause_seconds=0)
        self.assertEqual(done, [GOOD])

    def test_three_different_jobs_crashing_in_a_row_end_the_process(self) -> None:
        _, queue = make_long_queue()
        jobs = [Job("cust-a", f"doc-{n}") for n in range(CRASHES_IN_A_ROW)]
        for job in jobs:
            queue.enqueue(job)

        def buggy(job: Job, beat: Heartbeat) -> None:
            raise RuntimeError("bug")

        worker = make_worker(queue, buggy)
        for _ in range(CRASHES_IN_A_ROW - 1):
            self.assertTrue(worker.run_once())
        with self.assertRaises(RuntimeError):
            worker.run_once()
        self.assertEqual(queue.deferred, [(job, RETRY_FIRST_SECONDS) for job in jobs])  # handed back first

    def test_a_job_that_runs_resets_the_count(self) -> None:
        _, queue = make_long_queue()
        names = ["a", "b", "ok", "c", "d", "failed", "e", "f"]
        for name in names:
            queue.enqueue(Job("cust-a", name))

        def handler(job: Job, beat: Heartbeat) -> None:
            if job.document_id == "failed":
                raise JobFailed("broken_file", retryable=False)
            if job.document_id != "ok":
                raise RuntimeError("bug")

        worker = make_worker(queue, handler)
        for _ in names:
            self.assertTrue(worker.run_once())  # never three different crashes in a row

    def test_a_memory_error_is_handed_back_then_raised(self) -> None:
        _, queue = make_long_queue()
        queue.enqueue(GOOD)

        def greedy(job: Job, beat: Heartbeat) -> None:
            raise MemoryError

        with self.assertRaises(MemoryError):
            make_worker(queue, greedy).run_once()
        self.assertEqual(queue.deferred, [(GOOD, RETRY_FIRST_SECONDS)])

    def test_a_database_that_cannot_be_reached_is_an_outage_not_a_crash(self) -> None:
        """Most database calls raise BackendUnavailable("database") already (db/engine.py
        ``unavailable``); a raw OperationalError that slips past is treated the same: no
        try is spent and nothing is handed back, so the loop pauses as for an outage."""
        for error in (
            OperationalError("SELECT 1", {}, Exception("server closed the connection")),
            InterfaceError("SELECT 1", {}, Exception("connection already closed")),
        ):
            with self.subTest(error=type(error).__name__):
                _, queue = make_long_queue()
                queue.enqueue(GOOD)

                def down(job: Job, beat: Heartbeat, error: Exception = error) -> None:
                    raise error

                with self.assertRaises(BackendUnavailable) as raised:
                    make_worker(queue, down).run_once()
                self.assertEqual(str(raised.exception), "database")
                self.assertEqual(queue.deferred, [])

    def test_the_other_outcomes_are_as_before(self) -> None:
        def raising(error: BaseException) -> Handler:
            def handler(job: Job, beat: Heartbeat) -> None:
                raise error

            return handler

        with self.subTest("JobFailed for good is buried"):
            _, queue = make_long_queue()
            queue.enqueue(GOOD)
            self.assertTrue(make_worker(queue, raising(JobFailed("virus_found", retryable=False))).run_once())
            self.assertEqual(([f.code for f in queue.failed.values()], queue.deferred), (["virus_found"], []))
        with self.subTest("a retryable JobFailed is deferred counted"):
            _, queue = make_long_queue()
            queue.enqueue(GOOD)
            self.assertTrue(
                make_worker(queue, raising(JobFailed("scanner_unavailable", retryable=True))).run_once()
            )
            self.assertEqual(queue.deferred, [(GOOD, retry_delay(1))])
        with self.subTest("a Deferred from admit is uncounted"):
            _, queue = make_long_queue()
            queue.enqueue(GOOD)
            worker = Worker(
                queue,
                raising(RuntimeError("never run")),
                max_attempts=MAX_ATTEMPTS,
                on_exhausted=lambda job: None,
                admit=_deferring,
            )
            self.assertTrue(worker.run_once())
            self.assertEqual(queue.deferred, [(GOOD, 30)])
        with self.subTest("BackendUnavailable goes up, nothing deferred"):
            _, queue = make_long_queue()
            queue.enqueue(GOOD)
            with self.assertRaises(BackendUnavailable):
                make_worker(queue, raising(BackendUnavailable("database"))).run_once()
            self.assertEqual(queue.deferred, [])
        with self.subTest("a key problem is deferred 300 s uncounted, then goes up"):
            _, queue = make_long_queue()
            queue.enqueue(GOOD)
            with self.assertRaises(BackendUnavailable):
                make_worker(queue, raising(BackendUnavailable("gemini_key"))).run_once()
            self.assertEqual(queue.deferred, [(GOOD, 300)])
        with self.subTest("LeaseLost from a heartbeat: nothing deferred"):
            _, queue = make_long_queue()
            queue.enqueue(GOOD)
            self.assertTrue(make_worker(queue, raising(LeaseLost("receipt"))).run_once())
            self.assertEqual(queue.deferred, [])
        with self.subTest("KeyboardInterrupt is not caught"):
            _, queue = make_long_queue()
            queue.enqueue(GOOD)
            with self.assertRaises(KeyboardInterrupt):
                make_worker(queue, raising(KeyboardInterrupt())).run_once()
            self.assertEqual(queue.deferred, [])

    def test_a_hand_back_that_fails(self) -> None:
        class Refusing(LeaseQueue):
            def __init__(self, clock: FakeClock, error: Exception) -> None:
                super().__init__(clock, lease_seconds=1200.0, max_attempts=QUEUE_MAX_RECEIVES)
                self.error = error

            def defer(self, receipt: str, seconds: int, *, counted: bool = False) -> None:
                raise self.error

        def buggy(job: Job, beat: Heartbeat) -> None:
            raise RuntimeError("bug")

        lost = Refusing(FakeClock(), LeaseLost("receipt"))
        lost.enqueue(GOOD)
        self.assertTrue(make_worker(lost, buggy).run_once())  # another delivery owns it: quietly left
        down = Refusing(FakeClock(), BackendUnavailable("queue"))
        down.enqueue(GOOD)
        with self.assertRaises(BackendUnavailable):
            make_worker(down, buggy).run_once()

    def test_errors_outside_the_handler_still_end_the_process(self) -> None:
        """on_attempt and on_exhausted are never wrapped: if marking fails the error goes
        on up and the job is not buried, so its next delivery marks it."""
        clock, queue = make_long_queue()
        queue.enqueue(GOOD)

        def broken(*args: object) -> None:
            raise RuntimeError("bug in marking")

        worker = Worker(
            queue, lambda job, beat: None, max_attempts=MAX_ATTEMPTS, on_exhausted=broken, on_attempt=broken
        )
        with self.assertRaises(RuntimeError):
            worker.run_once()  # on_attempt
        self.assertEqual(queue.deferred, [])
        for _ in range(MAX_ATTEMPTS):
            clock.advance(1200)
            queue.receive()  # earlier deliveries that crashed
        clock.advance(1200)
        with self.assertRaises(RuntimeError):
            worker.run_once()  # on_exhausted
        self.assertEqual(queue.failed, {})


@contextmanager
def _deferring(job: Job) -> Iterator[None]:
    raise Deferred(30, "customer_busy")
    yield  # a generator, so this is a context manager


if __name__ == "__main__":
    unittest.main()
