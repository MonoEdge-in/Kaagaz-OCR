"""PRD 7 PR 12 (#605): a lock against the worker and other writers, two sessions at once (PR 5's
pattern: the second session is named, started in a thread and seen waiting on a lock before the
first commits, so the order is forced, never timed). Whichever comes first, the end state is the
same: nothing of a locked file's content is left, and nothing deadlocks.

* The lock first: a late write is refused (its run is closed, ``locked_at`` is read FOR SHARE, the
  CHECK) or writes nothing (the page cache).
* The write first: the lock waits on the family or row lock, then deletes what was written."""

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import documents as documents_sql
from kaagaz.db import locks as locks_sql
from kaagaz.db import review as review_sql
from kaagaz.db.engine import Database
from kaagaz.db.page_reads import _PUT, PageKey, PgPageReads, _params
from kaagaz.db.trash import lock_family
from kaagaz.jobs.worker import Superseded
from kaagaz.pipeline import FinishedRun, PageRecord, RunTotals
from kaagaz.reading.spans import PageInfo
from tests.db_support import insert_document, name_session, new_customer, wait_until_blocked
from tests.test_api_support import CSRF, Api
from tests.test_isolation_access_views import _app, _walled
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_PIECE = (
    "INSERT INTO pieces (customer_id, document_id, seq, page_from, page_to, text, regions, label)"
    " VALUES (:c, :d, 0, 1, 1, 'Zebra', '[{\"page\": 1}]', 'verified')"
)
_SHA = "c" * 64


def _file(db: Database) -> tuple[str, str, str]:
    """(customer, its Admin, a ready pictured PDF of one page)."""
    c, admin = new_customer(db)
    with db.customer(c) as conn:
        document = insert_document(conn, c, page_count=1)
        conn.execute(
            text(
                "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, has_image)"
                " VALUES (:c, :d, 1, 595, 842, 'pt', true)"
            ),
            {"c": c, "d": document},
        )
    return c, admin, document


def _in_thread(name: str, db: Database, work: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    """Run ``work`` in a thread; its result, or the error it raised, lands in the list."""
    out: list[Any] = []

    def run() -> None:
        try:
            out.append(work())
        except Exception as error:  # noqa: BLE001 - the test reads what the session ended with
            out.append(error)

    thread = threading.Thread(target=run, name=name)
    thread.start()
    return thread, out


def _lock_as(
    db: Database, c: str, admin: str, document: str, *, name: str | None = None
) -> locks_sql.Outcome:
    with _app(db).user(c, admin) as conn:
        if name is not None:
            name_session(conn, name)
        return locks_sql.lock(conn, [document], [])


def _pieces(db: Database, document: str) -> int:
    return int(_owner(db, "SELECT count(*) FROM pieces WHERE document_id = :d", {"d": document})[0][0])


def test_a_save_holding_the_family_first_is_waited_for_and_then_deleted_by_the_lock(db: Database) -> None:
    c, admin, document = _file(db)
    with db.customer(c) as conn:  # as the worker's save: the family lock, then its rows
        lock_family(conn, c, document)
        conn.execute(text(_PIECE), {"c": c, "d": document})
        thread, out = _in_thread("lock", db, lambda: _lock_as(db, c, admin, document, name="locking"))
        wait_until_blocked(db, application_name="locking")
    thread.join(timeout=10)
    assert not thread.is_alive() and not out[0].refused
    assert _pieces(db, document) == 0


def test_a_write_after_the_lock_waits_for_it_then_is_refused(db: Database) -> None:
    c, admin, document = _file(db)

    def write() -> None:
        with db.customer(c) as conn:
            name_session(conn, "writing")
            conn.execute(text(_PIECE), {"c": c, "d": document})  # no family lock: an unlisted writer

    with _app(db).user(c, admin) as conn:
        assert not locks_sql.lock(conn, [document], []).refused
        thread, out = _in_thread("write", db, write)
        wait_until_blocked(db, application_name="writing")  # the trigger's FOR SHARE waits
    thread.join(timeout=10)
    assert isinstance(out[0], DBAPIError) and getattr(out[0].orig, "sqlstate", None) == "23514"
    assert _pieces(db, document) == 0


def test_the_page_cache_after_the_lock_waits_then_writes_nothing(db: Database) -> None:
    c, admin, document = _file(db)
    key = PageKey(c, document, 1, _SHA, "m", "g1")

    def put() -> None:
        with db.customer(c) as conn:
            name_session(conn, "caching")
            conn.execute(
                _PUT,
                {**_params(key), "result": '{"lines": ["Zebra"]}', "input_tokens": 1, "output_tokens": 1},
            )

    with _app(db).user(c, admin) as conn:
        assert not locks_sql.lock(conn, [document], []).refused
        thread, out = _in_thread("cache", db, put)
        wait_until_blocked(db, application_name="caching")
    thread.join(timeout=10)
    assert out == [None]  # no error: nothing written
    assert PgPageReads(db).get(key) is None


def test_the_page_cache_before_the_lock_is_waited_for_and_then_deleted(db: Database) -> None:
    c, admin, document = _file(db)
    key = PageKey(c, document, 1, _SHA, "m", "g1")
    with db.customer(c) as conn:
        conn.execute(
            _PUT, {**_params(key), "result": '{"lines": ["Zebra"]}', "input_tokens": 1, "output_tokens": 1}
        )
        thread, out = _in_thread("lock", db, lambda: _lock_as(db, c, admin, document, name="locking"))
        wait_until_blocked(db, application_name="locking")  # the lock waits on the FOR SHARE
    thread.join(timeout=10)
    assert not out[0].refused
    assert PgPageReads(db).get(key) is None


def _run(db: Database, c: str, document: str) -> str:
    with db.customer(c) as conn:
        return str(
            conn.execute(
                text(
                    "INSERT INTO processing_runs (customer_id, document_id, started_at, status)"
                    " VALUES (:c, :d, now(), 'running') RETURNING id"
                ),
                {"c": c, "d": document},
            ).scalar_one()
        )


def _finished(run_id: str, status: str) -> FinishedRun:
    from datetime import UTC, datetime

    totals = RunTotals(status, None, None, None, None, None, 0, 0, 0, 0, 0, 0, 0, None, None)
    return FinishedRun(run_id, totals, datetime.now(UTC))


def test_ready_after_the_lock_is_refused_by_the_run_fence(db: Database) -> None:
    c, admin, document = _file(db)
    _owner(db, "UPDATE documents SET status = 'clean' WHERE id = :d", {"d": document})
    run = _run(db, c, document)
    assert not _lock_as(db, c, admin, document).refused
    with pytest.raises(Superseded):
        documents_sql.PgDocuments(db).mark_ready(c, document, 1, run=_finished(run, "ready"))
    assert _owner(db, "SELECT status FROM documents WHERE id = :d", {"d": document})[0][0] == "locked"
    assert not documents_sql.PgDocuments(db).still_open(c, document, run)


def test_the_check_before_a_model_step_waits_for_a_lock_under_way_and_then_sees_it(db: Database) -> None:
    """Review (15 Nov 2026): ``still_open`` reads the version FOR SHARE, so a lock that has taken the
    family but not committed yet is waited for: the worker never sends the file on in that gap."""
    c, admin, document = _file(db)
    _owner(db, "UPDATE documents SET status = 'clean' WHERE id = :d", {"d": document})
    run = _run(db, c, document)
    with _app(db).user(c, admin) as conn:
        assert not locks_sql.lock(conn, [document], []).refused
        thread, out = _in_thread(
            "check", db, lambda: documents_sql.PgDocuments(db).still_open(c, document, run)
        )
        _wait_for_waiting(db, "FOR SHARE OF d")
    thread.join(timeout=10)
    assert out == [False]


def test_two_admins_locking_at_once_one_locks_and_the_other_finds_it_locked(db: Database) -> None:
    c, admin, document = _file(db)
    second, _ = Api(db).add_user(c, "admin")
    with _app(db).user(c, admin) as conn:
        assert not locks_sql.lock(conn, [document], []).refused
        thread, out = _in_thread("again", db, lambda: _lock_as(db, c, str(second), document, name="again"))
        wait_until_blocked(db, application_name="again")  # the tree lock
    thread.join(timeout=10)
    assert out[0].already == [document] and out[0].versions == []


def test_an_unlock_while_the_prepare_job_runs_supersedes_its_save(db: Database) -> None:
    c, admin, document = _file(db)
    # Still being read, no picture stored yet: the lock leaves it to the worker to prepare.
    _owner(db, "UPDATE documents SET status = 'clean' WHERE id = :d", {"d": document})
    _owner(db, "DELETE FROM pages WHERE document_id = :d", {"d": document})
    assert _lock_as(db, c, admin, document).versions[0].queue
    _owner(db, "UPDATE documents SET status = 'clean' WHERE id = :d", {"d": document})  # scanned
    run = _run(db, c, document)
    with _app(db).user(c, admin) as conn:
        assert not locks_sql.unlock(conn, [document]).refused
    page = PageRecord(PageInfo(1, 595.0, 842.0, "pt"), True)
    with pytest.raises(Superseded):
        documents_sql.PgDocuments(db).save_locked_pages(c, document, [page], run=_finished(run, "locked"))
    assert _owner(db, "SELECT status, locked_at FROM documents WHERE id = :d", {"d": document})[0] == (
        "received",
        None,
    )


def test_a_new_version_uploaded_while_the_lock_runs_waits_and_is_locked_from_the_start(db: Database) -> None:
    from kaagaz.ingestion.ports import DocumentRecord

    c, admin, document = _file(db)
    record = DocumentRecord(
        c, str(uuid.uuid4()), uuid.uuid4().hex * 2, 10, "pdf", "v2.pdf", uploaded_by=admin
    )

    def upload() -> Any:
        with db.customer(c) as conn:
            name_session(conn, "uploading")
            return documents_sql.add_version(conn, record, of_document_id=document)

    with _app(db).user(c, admin) as conn:
        assert not locks_sql.lock(conn, [document], []).refused
        thread, out = _in_thread("upload", db, upload)
        wait_until_blocked(db, application_name="uploading")  # the tree lock, first
    thread.join(timeout=10)
    assert out[0][1] is True
    row = _owner(
        db, "SELECT status, locked_at IS NOT NULL FROM documents WHERE id = :d", {"d": record.document_id}
    )
    assert tuple(row[0]) == ("received", True)  # the worker prepares it, never reads it


def test_a_prepared_locked_version_is_current_unless_a_newer_one_is_locked(db: Database) -> None:
    """F2: a locked version is never ready, so it counts as ready for which version is current."""
    c, admin, first = _file(db)
    assert not _lock_as(db, c, admin, first).refused
    with db.customer(c) as conn:
        newer = insert_document(conn, c, family_id=first, version_no=2, is_current=False, status="received")
    page = PageRecord(PageInfo(1, 595.0, 842.0, "pt"), True)
    for document in (newer, first):  # the new version prepared, then the first prepared again
        _owner(db, "UPDATE documents SET status = 'clean' WHERE id = :d", {"d": document})
        run = _run(db, c, document)
        documents_sql.PgDocuments(db).save_locked_pages(c, document, [page], run=_finished(run, "locked"))
    current = _owner(db, "SELECT id::text FROM documents WHERE family_id = :f AND is_current", {"f": first})
    assert [row[0] for row in current] == [newer]
    states = _owner(db, "SELECT DISTINCT status FROM documents WHERE family_id = :f", {"f": first})
    assert [row[0] for row in states] == ["locked"]


def _wait_for_waiting(db: Database, fragment: str, timeout: float = 5.0) -> None:
    """Wait until a session whose statement holds ``fragment`` waits on a lock (a route's own
    connection has no name to wait for)."""
    deadline = time.monotonic() + timeout
    query = text(
        "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND query LIKE :q"
        " AND pid <> pg_backend_pid()"
    )
    while time.monotonic() < deadline:
        with db.engine.connect() as conn:
            if conn.execute(query, {"q": f"%{fragment}%"}).scalar_one():
                return
        time.sleep(0.02)
    raise AssertionError(f"no session running {fragment} waited on a lock")


def test_a_lock_deadlocked_with_a_review_save_is_run_again_and_locks(db: Database) -> None:
    """Review (15 Nov 2026): a review save holds its value, then writes its correction (which reads
    the version FOR SHARE); the lock holds the family, then deletes the value. PostgreSQL fails the
    lock, which waited first; the route runs it again once the save is in, and the file is locked
    with the save's rows gone."""
    api = Api(db)
    org = _walled(api)
    c, admin, file = org.c, org.people["admin"], org.files["loose"]
    api.add_page(c, file)
    value = api.add_value(c, file)
    with _app(db).user(c, admin.user_id) as conn:  # the review save, as POST /review/{id}
        current = review_sql.lock_value(conn, c, value)
        assert current is not None
        api.prove(admin, "locks")
        thread, out = _in_thread(
            "lock", db, lambda: admin.client.post(f"/api/documents/{file}/lock", json={}, headers=CSRF)
        )
        _wait_for_waiting(db, "kaagaz_lock(")  # the lock waits on the value the save holds
        review_sql.resolve_value(
            conn, c, current, new_text="1,250", choice="typed", remember=True, user_id=admin.user_id
        )  # waits on the lock's family: the cycle, which PostgreSQL breaks by failing the lock
    thread.join(timeout=20)
    assert not thread.is_alive() and out[0].status_code == 200, out[0].text
    assert _owner(db, "SELECT locked_at IS NOT NULL FROM documents WHERE id = :d", {"d": file})[0][0] is True
    for table in ("extracted_values", "corrections"):
        assert _owner(db, f"SELECT count(*) FROM {table} WHERE document_id = :d", {"d": file})[0][0] == 0  # noqa: S608
    learned = "SELECT count(*) FROM learned_corrections WHERE customer_id = :c"
    assert _owner(db, learned, {"c": c})[0][0] == 0
