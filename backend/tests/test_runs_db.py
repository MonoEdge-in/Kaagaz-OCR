"""Processing runs, progress and the run fence against a real PostgreSQL (PRD 2 R6, X2, X3).

Every time comes from the test (the injected clock). The concurrency tests force
the order with ``wait_until_blocked``, never with sleeps.
"""

import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import kaagaz
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.db.runs import PgRuns, finish_run, is_run_open, pages_by_route, start_run
from kaagaz.db.trash import lock_family
from kaagaz.ingestion.ports import DocumentRecord
from kaagaz.jobs.worker import JobFailed
from kaagaz.pipeline import FinishedRun, Progress, RunTotals, Superseded
from kaagaz.scanning.gate import REJECTION_CODES, DocumentStatus
from tests.db_support import insert_document, new_customer, violation, wait_until_blocked
from tests.fakes import MemoryDocuments, MemoryProgress, MemoryRuns, MemoryStatuses
from tests.test_api_review import _named_database
from tests.test_ocr_server import VERSION as OCR_VERSION
from tests.test_pipeline_store_db import EMPTY, content, counts, new_document, new_session, rows

pytestmark = pytest.mark.db

T0 = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=5)
T2 = T0 + timedelta(minutes=9)
RUNS = (
    "SELECT id::text, status, error_code, started_at, finished_at FROM processing_runs"
    " WHERE document_id = :d ORDER BY started_at, id"
)


def totals(status: str = "ready", **changes: Any) -> RunTotals:
    """Run totals with a different number in every counter, so a swapped column shows."""
    base = RunTotals(
        status, None, "rapidocr 3.4", "qwen3:4b", None, "bge-m3", 1, 2, 3, 4, 5, 6, 7, 12.5, 3.25
    )
    return replace(base, **changes)


def runs(db: Database, customer_id: str, document_id: str) -> list[tuple[Any, ...]]:
    return [tuple(row.values()) for row in rows(db, customer_id, RUNS, document_id)]


def test_start_run_supersedes_the_earlier_running_run(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    first = PgRuns(db).start(customer, doc, now=T0)
    second = PgRuns(db).start(customer, doc, now=T1)

    assert runs(db, customer, doc) == [
        (first, "superseded", None, T0, T1),
        (second, "running", None, T1, None),
    ]


def test_start_run_closes_a_stale_run_that_started_after_now(db: Database) -> None:
    """Another worker's clock ran ahead: the stale run never ends before it started."""
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    ahead = PgRuns(db).start(customer, doc, now=T1)
    now = PgRuns(db).start(customer, doc, now=T0)

    assert runs(db, customer, doc) == [
        (now, "running", None, T0, None),
        (ahead, "superseded", None, T1, T1),
    ]


def test_two_starts_at_once_leave_one_open_run(db: Database) -> None:
    """The second start waits for the first on the document's run lock, then
    supersedes it, so exactly one run is left open."""
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    session = new_session()
    named = _named_database(session)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with db.customer(customer) as conn:
                first = start_run(conn, customer, doc, now=T0)
                starting = pool.submit(PgRuns(named).start, customer, doc, now=T1)
                wait_until_blocked(db, application_name=session)
            second = starting.result(timeout=10)
    finally:
        named.engine.dispose()
    assert runs(db, customer, doc) == [
        (first, "superseded", None, T0, T1),
        (second, "running", None, T1, None),
    ]


def test_start_run_for_a_missing_document_fails_for_good(db: Database) -> None:
    customer, _ = new_customer(db)
    missing = str(uuid.uuid4())
    with pytest.raises(JobFailed) as failed:
        PgRuns(db).start(customer, missing, now=T0)
    assert (failed.value.code, failed.value.retryable) == ("document_missing", False)
    assert runs(db, customer, missing) == []


def test_finish_run_writes_every_total_and_never_ends_before_it_started(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    run = PgRuns(db).start(customer, doc, now=T1)
    done = totals(config={"ocr_dpi": 200}, quotes_returned=8, values_verified=9, values_needs_review=10)
    PgRuns(db).finish(customer, FinishedRun(run, done, T0))  # this worker's clock is behind

    [row] = rows(db, customer, "SELECT * FROM processing_runs WHERE document_id = :d", doc)
    assert {key: row[key] for key in ("status", "error_code", "started_at", "finished_at", "config")} == {
        "status": "ready",
        "error_code": None,
        "started_at": T1,
        "finished_at": T1,
        "config": {"ocr_dpi": 200},
    }
    names = ["ocr_engine", "model_chat", "model_vision", "model_embed", "pages_cells", "pages_text_layer"]
    names += ["pages_ocr", "pages_blank", "vision_crops", "model_calls", "dropped_quotes", "seconds_total"]
    names += ["seconds_ocr", "quotes_returned", "values_verified", "values_needs_review"]
    assert [row[name] for name in names] == [
        "rapidocr 3.4",
        "qwen3:4b",
        None,
        "bge-m3",
        *range(1, 8),
        12.5,
        3.25,
        8,
        9,
        10,
    ]


def test_finish_run_keeps_a_long_engine_name_and_cuts_a_longer_one(db: Database) -> None:
    """PR 2's engine version alone is 100 characters. 290 are kept; 400 are cut to the
    300 the column holds, never refused (a refused finish would stop the worker)."""
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    kept = PgRuns(db).start(customer, doc, now=T0)
    PgRuns(db).finish(customer, FinishedRun(kept, totals(ocr_engine="p" * 290), T1))
    cut = PgRuns(db).start(customer, doc, now=T1)
    long = {name: name[0] * 400 for name in ("ocr_engine", "model_chat", "model_vision", "model_embed")}
    PgRuns(db).finish(customer, FinishedRun(cut, totals(**long), T2))

    sql = "SELECT id::text, ocr_engine, model_chat, model_vision, model_embed FROM processing_runs"
    stored = {row["id"]: row for row in rows(db, customer, sql + " WHERE document_id = :d", doc)}
    assert stored[kept]["ocr_engine"] == "p" * 290
    assert {name: stored[cut][name] for name in long} == {name: value[:300] for name, value in long.items()}


def test_finish_run_keeps_the_real_paddleocr_vl_engine_name_untouched(db: Database) -> None:
    """The name a PaddleOCR-VL run records: the engine, a space, and its reply's version."""
    engine = "paddleocr-vl " + OCR_VERSION
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    run = PgRuns(db).start(customer, doc, now=T0)
    PgRuns(db).finish(customer, FinishedRun(run, totals(ocr_engine=engine), T1))

    (stored,) = rows(db, customer, "SELECT ocr_engine FROM processing_runs WHERE document_id = :d", doc)
    assert stored["ocr_engine"] == engine


_CODE = re.compile(
    r'(?:ReadError|JobFailed|ScannerError|ModelUnavailable|ModelOutputError)\("([a-z0-9_]+)"'
    r'|\bcode = "([a-z0-9_]+)"|BackendUnavailable\("([a-z0-9_]+)"\)'
)


def emitted_codes() -> set[str]:
    """Every failure code in the source, as plan 3.9 turns it into a run's error_code:
    reader, job and scanner codes as they are, ``<backend>_unavailable``, and ``model_<code>``."""
    codes = set(REJECTION_CODES) | {"retries_exhausted"}
    for path in Path(kaagaz.__file__).parent.rglob("*.py"):
        for plain, assigned, backend in _CODE.findall(path.read_text(encoding="utf-8")):
            codes |= {plain, assigned} if plain or assigned else {f"{backend}_unavailable"}
    model = {"timeout", "unreachable", "http_error", "bad_response", "response_too_large", "invalid_output"}
    return (codes | {f"model_{code}" for code in model}) - {""}


def test_every_error_code_the_pipeline_emits_is_accepted(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    codes = emitted_codes()
    assert {"corrupt_file", "document_missing", "storage_unavailable", "model_timeout", "not_utf8"} <= codes

    with db.customer(customer) as conn:  # a new run for each code: a run is finished once
        for code in sorted(codes):
            run = start_run(conn, customer, doc, now=T0)
            finish_run(conn, customer, FinishedRun(run, totals("failed", error_code=code), T1))
    stored = rows(db, customer, "SELECT error_code FROM processing_runs WHERE document_id = :d", doc)
    assert sorted(row["error_code"] for row in stored) == sorted(codes)
    with pytest.raises(IntegrityError) as refused, db.customer(customer) as conn:
        run = start_run(conn, customer, doc, now=T0)
        finish_run(conn, customer, FinishedRun(run, totals("failed", error_code="Bad-Code"), T1))
    assert violation(refused.value) == ("23514", "processing_runs_error_code_check")


def test_a_deleted_document_keeps_its_runs_without_the_document(db: Database) -> None:
    """AR12 (PROVISIONAL): the page counts per month outlive a purge."""
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    run = PgRuns(db).start(customer, doc, now=T0)
    with db.customer(customer) as conn:
        conn.execute(
            text("DELETE FROM documents WHERE customer_id = :c AND id = :d"), {"c": customer, "d": doc}
        )
        kept = conn.execute(text("SELECT document_id FROM processing_runs WHERE id = :r"), {"r": run}).all()
    assert [tuple(row) for row in kept] == [(None,)]


PROGRESS = "SELECT progress FROM documents WHERE id = :d"


def test_set_progress_on_a_ready_document_changes_nothing(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        reading, ready = insert_document(conn, customer, status="clean"), insert_document(conn, customer)
    for doc in (reading, ready):
        PgDocuments(db).set_progress(customer, doc, Progress("reading", 2, 5))

    assert rows(db, customer, PROGRESS, reading) == [{"progress": {"step": "reading", "done": 2, "total": 5}}]
    assert rows(db, customer, PROGRESS, ready) == [{"progress": None}]


def test_progress_waits_for_the_family_lock_and_is_then_written(db: Database) -> None:
    """T_save holds the family lock; a progress write queues behind it and never deadlocks."""
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    session = new_session()
    named = _named_database(session)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with db.customer(customer) as conn:
                lock_family(conn, customer, doc)
                writing = pool.submit(
                    PgDocuments(named).set_progress, customer, doc, Progress("indexing", 0, 1)
                )
                wait_until_blocked(db, application_name=session)
            writing.result(timeout=10)
    finally:
        named.engine.dispose()
    assert rows(db, customer, PROGRESS, doc) == [{"progress": {"step": "indexing", "done": 0, "total": 1}}]


# --- the run fence (Lead's resolution 2) ---------------------------------------


def test_a_save_or_ready_from_a_superseded_run_changes_nothing(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    old = PgRuns(db).start(customer, doc, now=T0)
    new = PgRuns(db).start(customer, doc, now=T1)
    store, documents = PgPipelineStore(db), PgDocuments(db)

    with pytest.raises(Superseded):
        store.save(content(customer, doc), run_id=old)
    with pytest.raises(Superseded):
        documents.mark_ready(customer, doc, 2, run=FinishedRun(old, totals(), T2))
    assert counts(db, customer, doc) == {**EMPTY, "processing_runs": 2}
    assert rows(db, customer, "SELECT status FROM documents WHERE id = :d", doc) == [{"status": "received"}]

    store.save(content(customer, doc), run_id=new)
    documents.mark_ready(customer, doc, 2, run=FinishedRun(new, totals(), T2))
    assert rows(db, customer, "SELECT status FROM documents WHERE id = :d", doc) == [{"status": "ready"}]
    assert runs(db, customer, doc) == [(old, "superseded", None, T0, T1), (new, "ready", None, T1, T2)]


def test_finish_never_overwrites_a_superseded_run(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    old = PgRuns(db).start(customer, doc, now=T0)
    new = PgRuns(db).start(customer, doc, now=T1)
    PgRuns(db).finish(customer, FinishedRun(old, totals(), T2))  # the stale delivery ends late
    PgRuns(db).finish(customer, FinishedRun(new, totals(), T2))
    PgRuns(db).finish(customer, FinishedRun(new, totals("failed", error_code="corrupt_file"), T2))

    assert runs(db, customer, doc) == [(old, "superseded", None, T0, T1), (new, "ready", None, T1, T2)]


def test_set_failed_from_a_superseded_run_changes_nothing(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    old = PgRuns(db).start(customer, doc, now=T0)
    new = PgRuns(db).start(customer, doc, now=T1)
    failed = "SELECT status, failure_code FROM documents WHERE id = :d"

    with pytest.raises(Superseded):
        PgDocuments(db).set_failed(customer, doc, "corrupt_file", run_id=old)
    assert rows(db, customer, failed, doc) == [{"status": "received", "failure_code": None}]
    PgDocuments(db).set_failed(customer, doc, "corrupt_file", run_id=new)
    assert rows(db, customer, failed, doc) == [{"status": "failed", "failure_code": "corrupt_file"}]


def test_the_new_run_wins_the_fence_even_when_the_stale_run_started_later(db: Database) -> None:
    """The stale delivery's worker clock ran ahead, so its run "started" after the new
    one. The fence asks whether a run is still open, not which started last."""
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    stale = PgRuns(db).start(customer, doc, now=T1)
    new = PgRuns(db).start(customer, doc, now=T0)
    store, documents = PgPipelineStore(db), PgDocuments(db)

    with pytest.raises(Superseded):
        store.save(content(customer, doc), run_id=stale)
    store.save(content(customer, doc), run_id=new)
    documents.mark_ready(customer, doc, 2, run=FinishedRun(new, totals(), T2))

    assert rows(db, customer, "SELECT status FROM documents WHERE id = :d", doc) == [{"status": "ready"}]
    assert runs(db, customer, doc) == [(new, "ready", None, T0, T2), (stale, "superseded", None, T1, T1)]


def test_a_save_waiting_on_the_family_lock_is_fenced_by_a_run_started_meanwhile(db: Database) -> None:
    """The fence is read under the family lock. Starting a run takes only a key share
    lock on the document, so it does not wait for the family lock."""
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    old = PgRuns(db).start(customer, doc, now=T0)
    session = new_session()
    named = _named_database(session)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            with db.customer(customer) as conn:
                lock_family(conn, customer, doc)
                saving = pool.submit(PgPipelineStore(named).save, content(customer, doc), run_id=old)
                wait_until_blocked(db, application_name=session)
                pool.submit(PgRuns(db).start, customer, doc, now=T1).result(timeout=10)
            with pytest.raises(Superseded):
                saving.result(timeout=10)
    finally:
        named.engine.dispose()
    assert counts(db, customer, doc) == {**EMPTY, "processing_runs": 2}


def test_mark_ready_on_a_rejected_or_deleted_document_still_finishes_its_run(db: Database) -> None:
    """AR11 (PROVISIONAL statuses): the early returns close the run too."""
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        rejected, deleted = (
            insert_document(conn, customer, status="rejected"),
            insert_document(conn, customer),
        )
    ids = {doc: PgRuns(db).start(customer, doc, now=T0) for doc in (rejected, deleted)}
    with db.customer(customer) as conn:
        conn.execute(
            text("DELETE FROM documents WHERE customer_id = :c AND id = :d"), {"c": customer, "d": deleted}
        )
    for doc, run in ids.items():
        PgDocuments(db).mark_ready(customer, doc, 1, run=FinishedRun(run, totals(), T1))

    assert runs(db, customer, rejected) == [(ids[rejected], "rejected", None, T0, T1)]
    with db.customer(customer) as conn:
        closed = conn.execute(
            text("SELECT status, error_code, finished_at FROM processing_runs WHERE id = :r"),
            {"r": ids[deleted]},
        ).one()
    assert tuple(closed) == ("failed", "document_missing", T1)
    assert counts(db, customer, rejected) == {**EMPTY, "processing_runs": 1}


# --- the month's pages by route, the last delivery, and the fence on failures ----------


def test_pages_by_route_counts_only_the_runs_started_in_the_window(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    start, end = T0, T0 + timedelta(days=31)
    for started in (start - timedelta(seconds=1), start, end - timedelta(seconds=1), end):
        run = PgRuns(db).start(customer, doc, now=started)
        PgRuns(db).finish(customer, FinishedRun(run, totals(), started))

    with db.customer(customer) as conn:
        counted = pages_by_route(conn, customer, start=start, end=end)
        nothing = pages_by_route(conn, customer, start=end + timedelta(days=1), end=end + timedelta(days=2))
    # totals() has pages_cells 1, text_layer 2, ocr 3, blank 4; two runs are in [start, end).
    assert counted == {"cells": 2, "text_layer": 4, "ocr": 6, "blank": 8}
    assert nothing == {"cells": 0, "text_layer": 0, "ocr": 0, "blank": 0}


def test_the_last_delivery_ends_the_runs_still_running(db: Database) -> None:
    """AR11: mark_exhausted ends them failed with retries_exhausted, on the injected clock."""
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        reading, ready = insert_document(conn, customer, status="clean"), insert_document(conn, customer)
    for doc in (reading, ready):
        PgRuns(db).start(customer, doc, now=T0)
    for doc in (reading, ready):
        PgDocuments(db).record_attempt(customer, doc, 5, 5)
        PgDocuments(db).mark_exhausted(customer, doc, now=T1, max_attempts=5)

    assert [row[1:] for row in runs(db, customer, reading)] == [("failed", "retries_exhausted", T0, T1)]
    assert rows(db, customer, "SELECT status, failure_code FROM documents WHERE id = :d", reading) == [
        {"status": "failed", "failure_code": "retries_exhausted"}
    ]
    # A ready document keeps its state, and so does its run: its job was not this one.
    assert [row[1:] for row in runs(db, customer, ready)] == [("running", None, T0, None)]


def test_a_superseded_run_cannot_mark_the_document_failed(db: Database) -> None:
    """Lead's resolution 2: the fence covers set_failed, read under the family lock."""
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        doc = insert_document(conn, customer, status="received")
    old = PgRuns(db).start(customer, doc, now=T0)
    new = PgRuns(db).start(customer, doc, now=T1)

    with pytest.raises(Superseded):
        PgDocuments(db).set_failed(customer, doc, "not_scanned", run_id=old)
    assert rows(db, customer, "SELECT status, failure_code FROM documents WHERE id = :d", doc) == [
        {"status": "received", "failure_code": None}
    ]
    PgDocuments(db).set_failed(customer, doc, "corrupt_file", run_id=new)
    assert rows(db, customer, "SELECT status, failure_code FROM documents WHERE id = :d", doc) == [
        {"status": "failed", "failure_code": "corrupt_file"}
    ]


def _scenario(
    *,
    start: Any,
    finish: Any,
    is_open: Any,
    set_progress: Any,
    progress_of: Any,
    customer: str,
    other: str,
    reading: str,
    ready: str,
    missing: str,
) -> list[object]:
    """One scenario of runs and progress, told as what each step gave (test-engineer 13)."""
    seen: list[object] = []
    first = start(customer, reading, T0)
    second = start(customer, reading, T1)
    seen.append((is_open(customer, reading, first), is_open(customer, reading, second)))
    finish(other, FinishedRun(second, totals(), T2))  # another customer's finish changes nothing
    seen.append(is_open(customer, reading, second))
    finish(customer, FinishedRun(second, totals("failed", error_code="corrupt_file"), T2))
    seen.append(is_open(customer, reading, second))
    try:
        start(customer, missing, T0)
    except JobFailed as failed:
        seen.append(failed.code)
    try:
        start(other, reading, T0)  # another customer's document is missing for it
    except JobFailed as failed:
        seen.append(failed.code)
    for who, doc in ((customer, reading), (customer, ready), (other, reading)):
        set_progress(who, doc, Progress("reading", 2, 5))
    seen.append((progress_of(reading), progress_of(ready)))
    return seen


def test_memory_and_postgresql_runs_and_progress_agree(db: Database) -> None:
    """The fakes mirror the SQL WHERE conditions: the same scenario, the same answers."""
    customer, _ = new_customer(db)
    other, _ = new_customer(db)
    with db.customer(customer) as conn:
        reading, ready = insert_document(conn, customer, status="clean"), insert_document(conn, customer)
    missing = str(uuid.uuid4())
    pg_runs, pg_documents = PgRuns(db), PgDocuments(db)

    def pg_open(c: str, d: str, r: str) -> bool:
        with db.customer(c) as conn:
            return is_run_open(conn, c, d, r)

    def pg_progress(d: str) -> object:
        return rows(db, customer, PROGRESS, d)[0]["progress"]

    on_postgresql = _scenario(
        start=lambda c, d, now: pg_runs.start(c, d, now=now),
        finish=pg_runs.finish,
        is_open=pg_open,
        set_progress=pg_documents.set_progress,
        progress_of=pg_progress,
        customer=customer,
        other=other,
        reading=reading,
        ready=ready,
        missing=missing,
    )

    statuses = MemoryStatuses()
    documents = MemoryDocuments(statuses)
    for doc, status in ((reading, DocumentStatus.CLEAN), (ready, DocumentStatus.READY)):
        documents.add_if_absent(DocumentRecord(customer, doc, uuid.uuid4().hex, 10, "pdf"))
        statuses.set_status(customer, doc, status)
    memory_runs, memory_progress = MemoryRuns(documents), MemoryProgress(documents, statuses)

    def memory_progress_of(d: str) -> object:
        found = memory_progress.progress.get((customer, d))
        return None if found is None else found.to_json()

    in_memory = _scenario(
        start=lambda c, d, now: memory_runs.start(c, d, now=now),
        finish=memory_runs.finish,
        is_open=memory_runs.is_open,
        set_progress=memory_progress.set_progress,
        progress_of=memory_progress_of,
        customer=customer,
        other=other,
        reading=reading,
        ready=ready,
        missing=missing,
    )

    assert (
        on_postgresql
        == in_memory
        == [
            (False, True),
            True,
            False,
            "document_missing",
            "document_missing",
            ({"step": "reading", "done": 2, "total": 5}, None),
        ]
    )
    assert [row[1] for row in runs(db, customer, reading)] == [
        row.status for row in memory_runs.for_document(customer, reading)
    ]
