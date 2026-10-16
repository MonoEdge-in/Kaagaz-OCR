"""The stuck-document sweep (stuck reading, 16 Oct 2026), no database: which documents it
queues again or fails, that a job queued again carries its tries, that a busy customer is
left alone, its limit, timing, lock, key-problem pause and logs. Against PostgreSQL:
tests/test_revive_stuck_db.py."""

import contextlib
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest import mock

import pytest
from sqlalchemy.exc import ProgrammingError

from kaagaz.adapters.queue import SqsQueue
from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs import revive
from kaagaz.jobs.fair_share import FairShare
from kaagaz.jobs.revive import FAILED, REQUEUED, REVIVE_EVERY, ReviveStuck, revive_stuck
from tests.settings_factory import make_settings

A, B = "aaaaaaaa-0000-4000-8000-00000000000a", "bbbbbbbb-0000-4000-8000-00000000000b"
START = datetime(2026, 10, 16, 5, 0, tzinfo=UTC)


class FakeDb:
    """``system()`` and ``customer(id)`` yield the customer's id as the connection."""

    @contextlib.contextmanager
    def system(self) -> Iterator[str]:
        yield "system"

    @contextlib.contextmanager
    def customer(self, customer_id: str) -> Iterator[str]:
        yield customer_id


class Tables:
    """The three statements on rows {customer: {document: tries}}, as the SQL does: only a
    document still stuck changes, and then it is no longer stuck."""

    def __init__(self, rows: dict[str, dict[str, int]]) -> None:
        self.rows = rows

    def stuck(self, conn: str, customer: str, *, stale_seconds: int, limit: int) -> list[tuple[str, int]]:
        return list(self.rows.get(customer, {}).items())[:limit]

    def requeue(
        self, conn: str, customer: str, document: str, *, stale_seconds: int, max_attempts: int
    ) -> int | None:
        tries = self.rows[customer].get(document)
        if tries is None or tries >= max_attempts:
            return None
        del self.rows[customer][document]
        return tries + 1

    def fail(self, conn: str, customer: str, document: str, *, max_attempts: int, **_: Any) -> bool:
        tries = self.rows[customer].get(document)
        if tries is None or tries < max_attempts:
            return False
        del self.rows[customer][document]
        return True


def sweep(
    tables: Tables, *, busy: frozenset[str] = frozenset(), limit: int = 20
) -> tuple[list[tuple[Job, str]], list[tuple[Job, int]]]:
    sent: list[tuple[Job, int]] = []
    with (
        mock.patch.object(revive, "customer_ids", lambda conn: list(tables.rows)),
        mock.patch.object(revive, "stuck_documents", tables.stuck),
        mock.patch.object(revive, "requeue_stuck", tables.requeue),
        mock.patch.object(revive, "fail_stuck", tables.fail),
    ):
        done = revive_stuck(
            cast(Database, FakeDb()),
            lambda job, tries: sent.append((job, tries)),
            lambda customer: customer in busy,
            stale_seconds=6000,
            max_attempts=5,
            limit=limit,
        )
    return done, sent


def test_a_stuck_document_with_a_try_left_is_queued_again_carrying_its_tries() -> None:
    done, sent = sweep(Tables({A: {"never-queued": 0, "tried-once": 1, "tried-four": 4}}))
    assert done == [
        (Job(A, "never-queued"), REQUEUED),
        (Job(A, "tried-once"), REQUEUED),
        (Job(A, "tried-four"), REQUEUED),
    ]
    # Its next delivery is the try just counted (tries + 1), so the count only goes up.
    assert sent == [(Job(A, "never-queued"), 0), (Job(A, "tried-once"), 1), (Job(A, "tried-four"), 4)]


def test_a_stuck_document_with_no_try_left_is_failed_and_never_queued() -> None:
    done, sent = sweep(Tables({A: {"spent": 5, "over": 7}}))
    assert done == [(Job(A, "spent"), FAILED), (Job(A, "over"), FAILED)]
    assert sent == []


def test_queued_again_at_most_max_attempts_times_then_failed() -> None:
    """Never a loop: each pass that finds it stuck again counts one more try."""
    outcomes = []
    tries = 0
    for _ in range(10):
        done, sent = sweep(Tables({A: {"lost": tries}}))
        outcomes.append(done[0][1])
        if done[0][1] == FAILED:
            break
        tries = sent[0][1] + 1  # the delivery recorded the try, then the job was lost again
    assert outcomes == [REQUEUED] * 5 + [FAILED]


def test_a_customer_with_a_slot_held_is_left_alone() -> None:
    """A job of theirs is being read, so their waiting documents are queued, not stuck."""
    done, sent = sweep(Tables({A: {"waiting": 0}, B: {"lost": 0}}), busy=frozenset({A}))
    assert done == [(Job(B, "lost"), REQUEUED)]
    assert sent == [(Job(B, "lost"), 0)]


def test_a_pass_handles_at_most_its_limit() -> None:
    tables = Tables({A: {f"a{n}": 0 for n in range(3)}, B: {"b0": 0}})
    done, _ = sweep(tables, limit=2)
    assert [job.document_id for job, _ in done] == ["a0", "a1"]
    assert list(tables.rows[A]) == ["a2"] and list(tables.rows[B]) == ["b0"]


def test_a_queue_that_is_down_goes_up_inside_the_documents_transaction() -> None:
    entered: list[str] = []

    class Watched(FakeDb):
        @contextlib.contextmanager
        def customer(self, customer_id: str) -> Iterator[str]:
            entered.append("open")
            try:
                yield customer_id
            except BackendUnavailable:
                entered.append("rolled back")
                raise

    def down(job: Job, tries: int) -> None:
        raise BackendUnavailable("queue")

    tables = Tables({A: {"lost": 0}})
    with (
        mock.patch.object(revive, "customer_ids", lambda conn: [A]),
        mock.patch.object(revive, "stuck_documents", tables.stuck),
        mock.patch.object(revive, "requeue_stuck", tables.requeue),
        pytest.raises(BackendUnavailable),
    ):
        revive_stuck(cast(Database, Watched()), down, lambda c: False, stale_seconds=1, max_attempts=5)
    assert entered == ["open", "open", "rolled back"]


def test_the_stuck_statements_look_at_documents_in_progress_out_of_trash_only() -> None:
    from kaagaz.db.documents import _STUCK_FAIL, _STUCK_FIND, _STUCK_REQUEUE

    for statement in (_STUCK_FIND, _STUCK_REQUEUE, _STUCK_FAIL):
        assert "customer_id = :c AND status IN ('received', 'clean') AND deleted_at IS NULL" in statement
        assert "updated_at < now() - make_interval(secs => :stale)" in statement
    assert "attempts < :max" in _STUCK_REQUEUE and "attempts >= :max" in _STUCK_FAIL


# ReviveStuck: when it runs.


class Idle:
    def __init__(self) -> None:
        self.turns = 0

    def run_once(self) -> bool:
        self.turns += 1
        return False


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def locked(mine: bool) -> Any:
    @contextlib.contextmanager
    def lock(name: str) -> Iterator[bool]:
        assert name == "revive_stuck"
        yield mine

    return lock


class Passes:
    """A ``revive`` that notes the clock at each pass and does nothing."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.at: list[datetime] = []

    def __call__(self) -> list[tuple[Job, str]]:
        self.at.append(self.clock.now)
        return []


def test_it_runs_on_the_first_turn_then_every_ten_minutes() -> None:
    clock = Clock()
    passes, worker = Passes(clock), Idle()
    sweeper = ReviveStuck(worker, passes, lambda: "working", clock)
    sweeper.run_once()
    clock.now += REVIVE_EVERY - timedelta(seconds=1)
    sweeper.run_once()
    clock.now += timedelta(seconds=1)
    sweeper.run_once()
    assert passes.at == [START, START + REVIVE_EVERY]
    assert worker.turns == 3


def test_no_pass_with_a_key_problem_or_without_the_lock() -> None:
    clock = Clock()
    passes = Passes(clock)
    ReviveStuck(Idle(), passes, lambda: "key_problem", clock).run_once()
    ReviveStuck(Idle(), passes, lambda: "working", clock, lock=locked(False)).run_once()
    assert passes.at == []


def test_errors_are_logged_by_type_and_the_jobs_go_on(caplog: pytest.LogCaptureFixture) -> None:
    def broken() -> list[tuple[Job, str]]:
        raise ProgrammingError("SELECT", {}, Exception("quoted row"))

    worker = Idle()
    with caplog.at_level(logging.ERROR, logger="kaagaz.worker"):
        ReviveStuck(worker, broken, lambda: "working", Clock()).run_once()
    assert worker.turns == 1
    assert [record.__dict__["code"] for record in caplog.records] == ["ProgrammingError"]
    assert "quoted row" not in caplog.text


def test_each_document_is_logged_with_ids_and_its_code(caplog: pytest.LogCaptureFixture) -> None:
    done = [(Job(A, "d1"), REQUEUED), (Job(B, "d2"), FAILED)]
    with caplog.at_level(logging.WARNING, logger="kaagaz.worker"):
        ReviveStuck(Idle(), lambda: done, lambda: "working", Clock(), lock=locked(True)).run_once()
    assert [
        (record.getMessage(), record.__dict__["document_id"], record.__dict__["code"])
        for record in caplog.records
    ] == [("stuck document queued again", "d1", REQUEUED), ("stuck document failed", "d2", FAILED)]


# What the sweep uses: the fair share's busy check and the queue's tries.


def test_busy_is_true_while_any_slot_of_the_customer_is_held() -> None:
    held = {f"kaagaz.fair:{A}:1"}

    @contextlib.contextmanager
    def locks(keys: Any) -> Iterator[str | None]:
        free = [key for key in keys if key not in held]
        yield free[0] if free else None

    share = FairShare(locks, 2)
    assert share.busy(A) is True
    assert share.busy(B) is False


def test_a_job_queued_again_carries_its_tries_in_the_body() -> None:
    class Client:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []

        def send_message(self, **call: Any) -> None:
            self.sent.append(call)

    client = Client()
    queue = SqsQueue(make_settings(), client=client)
    queue.enqueue(Job(A, "d1"), tries=3)
    queue.enqueue(Job(A, "d2"))
    assert [json.loads(call["MessageBody"]).get("tries") for call in client.sent] == [3, None]
    with pytest.raises(ValueError, match="tries"):
        queue.enqueue(Job(A, "d3"), tries=-1)
