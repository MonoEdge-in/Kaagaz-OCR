"""POST /api/documents/{id}/reprocess: "Read again" (PRD 2 X1, security A8, AR15).

A reviewer or an admin sends a ready, failed or unreadable document out of Trash
back to the start: it becomes received with its tries reset, one job is queued,
and one audit row says what it was. Anything else is 409 and nothing changes.
The concurrency tests force the order with events and ``wait_until_blocked``.
"""

import io
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api.views import READER_STOPPED, READER_WAITING
from kaagaz.db.documents import RETRY_STUCK_HOURS, PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.runs import PgRuns, close_running
from kaagaz.db.trash import trash_document
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.jobs.worker import Superseded
from kaagaz.pipeline import Progress
from tests.db_support import wait_until_blocked
from tests.fakes import MemoryQueue
from tests.settings_factory import make_settings
from tests.test_api_review import _named_database
from tests.test_api_support import CSRF, Api, ApiFakeQueue, Signed
from tests.test_pipeline import CSV, PIPELINE_POLICY
from tests.test_pipeline_db import with_runs
from tests.test_pipeline_store_db import new_session

pytestmark = pytest.mark.db

NOT_REPROCESSABLE = {
    "error": {
        "code": "not_reprocessable",
        # Changed on purpose (issue 407): one still waiting 6 hours on may be read again too.
        "message": "This document can't be read again right now. Only a ready, failed or unreadable document,"
        " or one still waiting 6 hours after it was sent, that is not in Trash can be.",
    }
}


def reprocess(signed: Signed, document_id: str) -> Any:
    return signed.client.post(f"/api/documents/{document_id}/reprocess", headers=CSRF)


def stored(api: Api, customer_id: str, document_id: str) -> tuple[object, ...]:
    with api.db.customer(customer_id) as conn:
        return tuple(
            conn.execute(
                text(
                    "SELECT status, failure_code, attempts, max_attempts, progress, is_current"
                    " FROM documents WHERE customer_id = :c AND id = :d"
                ),
                {"c": customer_id, "d": document_id},
            ).one()
        )


def add_correction(api: Api, customer_id: str, document_id: str, user_id: str, value_id: str | None) -> None:
    with api.db.customer(customer_id) as conn:
        conn.execute(
            text(
                "INSERT INTO corrections (customer_id, document_id, field, value_id, old_value, new_value,"
                " choice, corrected_by) VALUES (:c, :d, 'total_amount', :v, '1,200', '1,250', 'second', :u)"
            ),
            {"c": customer_id, "d": document_id, "v": value_id, "u": user_id},
        )


@pytest.mark.parametrize("role", ["reviewer", "admin"])
def test_a_reviewer_or_an_admin_sends_a_document_to_be_read_again(db: Database, role: str) -> None:
    api = Api(db)
    signed = api.new_signed(role)
    c = signed.customer_id
    doc = api.add_document(c, status="failed", failure_code="corrupt_file", attempts=3, max_attempts=5)

    reply = reprocess(signed, doc)

    assert reply.status_code == 200
    body = reply.json()
    assert (body["id"], body["status"], body["attempts"], body["max_attempts"]) == (doc, "received", 0, None)
    assert (body["failure_code"], body["status_message"], body["progress"]) == (
        None,
        "Waiting to start.",  # changed on purpose (council 16 Oct 2026): it waits in the queue
        None,
    )
    assert stored(api, c, doc) == ("received", None, 0, None, None, True)
    assert [(job.customer_id, job.document_id) for job in api.queue.jobs] == [(c, doc)]
    assert api.audit_rows(c)[-1] == ("document.reprocess", "document", doc, {"status": "failed"})


def test_a_member_gets_403_and_nothing_changes(db: Database) -> None:
    api = Api(db)
    member = api.new_signed("member")
    doc = api.add_document(member.customer_id)
    reply = reprocess(member, doc)
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "forbidden")
    assert stored(api, member.customer_id, doc)[0] == "ready"
    assert api.queue.jobs == []


@pytest.mark.parametrize("status", ["ready", "failed", "needs_reader"])
def test_ready_failed_and_unreadable_documents_may_be_read_again(db: Database, status: str) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    doc = api.add_document(reviewer.customer_id, status=status)
    assert reprocess(reviewer, doc).status_code == 200
    assert api.audit_rows(reviewer.customer_id)[-1][3] == {"status": status}


@pytest.mark.parametrize("state", ["received", "clean", "rejected", "in_trash"])
def test_one_being_read_rejected_or_in_trash_is_409_and_nothing_is_queued(db: Database, state: str) -> None:
    """PRD 2 edge case: only ready, failed or needs_reader, out of Trash."""
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    if state == "in_trash":
        doc = api.add_document(c, deleted_at=api.clock(), deleted_by=reviewer.user_id)
    else:
        doc = api.add_document(c, status=state, failure_code="virus_found" if state == "rejected" else None)
    before = stored(api, c, doc)

    reply = reprocess(reviewer, doc)

    assert (reply.status_code, reply.json()) == (409, NOT_REPROCESSABLE)
    assert stored(api, c, doc) == before
    assert api.queue.jobs == []
    assert "document.reprocess" not in api.audit_actions(c)


def test_a_queue_that_is_down_is_503_and_changes_nothing(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = api.add_document(c, attempts=2, max_attempts=5)
    before = stored(api, c, doc)
    api.queue.fail_enqueue = True

    reply = reprocess(reviewer, doc)

    assert (reply.status_code, reply.json()["error"]["code"]) == (503, "backend_unavailable")
    assert stored(api, c, doc) == before  # the whole transaction rolled back
    assert "document.reprocess" not in api.audit_actions(c)


def test_the_csrf_header_is_needed(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    doc = api.add_document(reviewer.customer_id)
    reply = reviewer.client.post(f"/api/documents/{doc}/reprocess")
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "csrf")
    assert api.queue.jobs == []


def test_the_detail_counts_the_reviewer_fixes_whose_values_are_still_there(db: Database) -> None:
    """AR15c: a fix whose value a later read replaced (value_id NULL) is not counted."""
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = api.add_document(c)
    api.add_page(c, doc)
    for value_id in (api.add_value(c, doc), api.add_value(c, doc, field_name="gstin"), None):
        add_correction(api, c, doc, reviewer.user_id, value_id)
    other = api.add_document(c)

    body = reviewer.client.get(f"/api/documents/{doc}").json()

    assert (body["reviewer_fixes"], body["summary"]) == (2, None)
    assert reviewer.client.get(f"/api/documents/{other}").json()["reviewer_fixes"] == 0


def test_a_customer_may_send_reprocess_per_hour_documents_in_an_hour(db: Database) -> None:
    """Security A8 (PROVISIONAL): REPROCESS_PER_HOUR, counted from the audit rows."""
    api = Api(db, settings=make_settings(reprocess_per_hour=2))
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    docs = [api.add_document(c) for _ in range(3)]

    assert [reprocess(reviewer, doc).status_code for doc in docs[:2]] == [200, 200]
    reply = reprocess(reviewer, docs[2])

    assert (reply.status_code, reply.json()["error"]["code"]) == (429, "rate_limited")
    assert reply.json()["error"]["message"] == (
        "Too many documents were sent to be read again in the last hour. Please try again later."
    )
    assert stored(api, c, docs[2])[0] == "ready"
    assert len(api.queue.jobs) == 2


def test_one_document_is_read_again_at_most_once_in_10_minutes(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = api.add_document(c)
    assert reprocess(reviewer, doc).status_code == 200
    with db.customer(c) as conn:  # the job read it again
        conn.execute(
            text("UPDATE documents SET status = 'ready' WHERE customer_id = :c AND id = :d"),
            {"c": c, "d": doc},
        )

    reply = reprocess(reviewer, doc)

    assert (reply.status_code, reply.json()["error"]["code"]) == (429, "rate_limited")
    assert reply.json()["error"]["message"] == (
        "This document was sent to be read again less than 10 minutes ago. Please wait and try again."
    )
    assert len(api.queue.jobs) == 1


def test_role_by_owner_matrix(db: Database) -> None:
    """A member is refused anywhere; a reviewer or an admin may only act on their own
    customer's documents, and another customer's id is simply not found."""
    api = Api(db)
    owner, _ = api.customer()
    stranger, _ = api.customer()
    results = {}
    for role in ("member", "reviewer", "admin"):
        for whose in ("own", "other"):
            signed = api.sign_in(owner if whose == "own" else stranger, role)
            doc = api.add_document(owner)
            results[(role, whose)] = reprocess(signed, doc).status_code
    assert results == {
        ("member", "own"): 403,
        ("member", "other"): 403,
        ("reviewer", "own"): 200,
        ("reviewer", "other"): 404,
        ("admin", "own"): 200,
        ("admin", "other"): 404,
    }


def test_reprocessing_an_older_version_never_makes_it_current(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    older = api.add_document(c, is_current=False)
    newer = api.add_document(c, family_id=older, version_no=2)

    assert reprocess(reviewer, older).status_code == 200
    assert (stored(api, c, older)[5], stored(api, c, newer)[5]) == (False, True)
    PgDocuments(db).mark_ready(c, older, 1)  # the job read it again
    assert (stored(api, c, older)[0], stored(api, c, older)[5], stored(api, c, newer)[5]) == (
        "ready",
        False,
        True,
    )


# --- read again against Trash, both orders (test-engineer 4) -------------------------------


class HeldQueue(ApiFakeQueue):
    """A queue whose enqueue says it has started, then waits: the read again is held
    inside its transaction, with the family lock taken."""

    def __init__(self) -> None:
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()

    def enqueue(self, job: Job, *, express: bool = False) -> None:
        self.entered.set()
        assert self.release.wait(timeout=10), "the test never released the queue"
        super().enqueue(job, express=express)


def test_reprocess_waiting_behind_trash_gets_409_and_queues_nothing(db: Database) -> None:
    session = new_session()
    waiting_db = _named_database(session)
    try:
        api = Api(waiting_db)
        reviewer = api.new_signed("reviewer")
        c = reviewer.customer_id
        doc = api.add_document(c)
        replies: dict[str, Any] = {}
        thread = threading.Thread(target=lambda: replies.setdefault("reply", reprocess(reviewer, doc)))
        with db.customer(c) as conn:
            assert trash_document(conn, c, doc, by=reviewer.user_id, now=api.clock())
            thread.start()
            wait_until_blocked(db, application_name=session)
        thread.join(timeout=10)

        assert (replies["reply"].status_code, replies["reply"].json()) == (409, NOT_REPROCESSABLE)
        assert api.queue.jobs == []
        assert "document.reprocess" not in api.audit_actions(c)
    finally:
        waiting_db.engine.dispose()


def test_trash_waiting_behind_reprocess_leaves_it_in_trash_after_the_job(db: Database) -> None:
    session = new_session()
    trash_db = _named_database(session)
    try:
        queue = HeldQueue()
        api, trash_api = Api(db, queue=queue), Api(trash_db)
        reviewer = api.new_signed("reviewer")
        c = reviewer.customer_id
        admin = trash_api.sign_in(c, "admin")
        doc = (
            UploadService(PIPELINE_POLICY, PgDocuments(db), api.storage, MemoryQueue())
            .accept(c, io.BytesIO(CSV))
            .document_id
        )
        with_runs(db, api.storage)(Job(c, doc), lambda: None)  # read: ready
        replies: dict[str, Any] = {}
        reprocessing = threading.Thread(
            target=lambda: replies.setdefault("reprocess", reprocess(reviewer, doc))
        )
        trashing = threading.Thread(
            target=lambda: replies.setdefault(
                "trash", admin.client.delete(f"/api/documents/{doc}", headers=CSRF)
            )
        )
        reprocessing.start()
        assert queue.entered.wait(timeout=10)
        trashing.start()
        wait_until_blocked(db, application_name=session)
        queue.release.set()
        reprocessing.join(timeout=10)
        trashing.join(timeout=10)

        assert (replies["reprocess"].status_code, replies["trash"].status_code) == (200, 204)
        assert [(job.customer_id, job.document_id) for job in queue.jobs] == [(c, doc)]
        with_runs(db, api.storage)(Job(c, doc), lambda: None)  # the queued job runs
        with db.customer(c) as conn:
            row = conn.execute(
                text(
                    "SELECT status, deleted_at IS NOT NULL FROM documents WHERE customer_id = :c AND id = :d"
                ),
                {"c": c, "d": doc},
            ).one()
        assert tuple(row) == ("ready", True)  # read again, and still in Trash
    finally:
        trash_db.engine.dispose()


def test_the_cooldown_is_per_document(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    first, second = api.add_document(c), api.add_document(c)
    assert [reprocess(reviewer, doc).status_code for doc in (first, second)] == [200, 200]


def test_the_hourly_limit_holds_for_two_requests_at_once(db: Database) -> None:
    """Council PR 3 review, medium 4: the count is taken under a per-customer lock, so
    two documents sent at the same moment cannot both pass a limit of 1."""
    session = new_session()
    second_db = _named_database(session)
    try:
        settings = make_settings(reprocess_per_hour=1)
        queue = HeldQueue()
        first_api, second_api = Api(db, settings=settings, queue=queue), Api(second_db, settings=settings)
        reviewer = first_api.new_signed("reviewer")
        c = reviewer.customer_id
        other = second_api.sign_in(c, "reviewer")
        docs = [first_api.add_document(c), first_api.add_document(c)]
        replies: dict[str, Any] = {}
        first = threading.Thread(target=lambda: replies.setdefault("first", reprocess(reviewer, docs[0])))
        second = threading.Thread(target=lambda: replies.setdefault("second", reprocess(other, docs[1])))
        first.start()
        assert queue.entered.wait(timeout=10)  # held inside its transaction, the count taken
        second.start()
        wait_until_blocked(db, application_name=session)
        queue.release.set()
        first.join(timeout=10)
        second.join(timeout=10)

        assert (replies["first"].status_code, replies["second"].status_code) == (200, 429)
        assert len(queue.jobs) + len(second_api.queue.jobs) == 1
        assert first_api.audit_actions(c).count("document.reprocess") == 1
    finally:
        second_db.engine.dispose()


# --- issue 407: Retry for a document still in progress long after it was asked for ---------


def hours_ago(hours: float) -> datetime:
    """Measured on the database's clock (the rule uses SQL now()), not the app's MovingClock."""
    return datetime.now(UTC) - timedelta(hours=hours)


@pytest.mark.parametrize("status", ["received", "clean"])
def test_407_one_still_in_progress_6_hours_after_its_upload_may_be_read_again(
    db: Database, status: str
) -> None:
    """Measured from the upload (``created_at``), never from ``updated_at``: every try, step and
    hand-back moves that (here it is fresh), so a document that keeps waiting would never age."""
    assert RETRY_STUCK_HOURS == 6
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = api.add_document(c, status=status, attempts=1, max_attempts=5, created_at=hours_ago(6.1))
    assert reviewer.client.get(f"/api/documents/{doc}").json()["stuck"] is True

    reply = reprocess(reviewer, doc)

    assert reply.status_code == 200
    assert reply.json()["stuck"] is False  # asked for again just now
    assert stored(api, c, doc) == ("received", None, 0, None, None, True)
    assert [(job.customer_id, job.document_id) for job in api.queue.jobs] == [(c, doc)]
    assert api.audit_rows(c)[-1] == ("document.reprocess", "document", doc, {"status": status})


@pytest.mark.parametrize(("uploaded", "read_again"), [(5.9, None), (7.0, 2.0)])
def test_407_one_asked_for_under_6_hours_ago_is_still_409(
    db: Database, uploaded: float, read_again: float | None
) -> None:
    """Under 6 hours since the upload, or since the last read again when that is later."""
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = api.add_document(c, status="clean", created_at=hours_ago(uploaded))
    if read_again is not None:
        with db.customer(c) as conn:
            conn.execute(
                text(
                    "INSERT INTO audit_log (customer_id, action, target_type, target_id, details, family_id,"
                    " created_at) VALUES (:c, 'document.reprocess', 'document', :t, '{}', :f, :at)"
                ),
                {"c": c, "t": doc, "f": doc, "at": hours_ago(read_again)},
            )
    assert reviewer.client.get(f"/api/documents/{doc}").json()["stuck"] is False
    before = stored(api, c, doc)

    reply = reprocess(reviewer, doc)

    assert (reply.status_code, reply.json()) == (409, NOT_REPROCESSABLE)
    assert stored(api, c, doc) == before
    assert api.queue.jobs == []


def test_407_a_member_still_gets_403_for_a_stuck_document(db: Database) -> None:
    api = Api(db)
    member = api.new_signed("member")
    doc = api.add_document(member.customer_id, status="clean", created_at=hours_ago(7))
    reply = reprocess(member, doc)
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "forbidden")
    assert api.queue.jobs == []


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("gemini_key_unavailable", READER_STOPPED),  # a wrong key, a spent cap, a model ID that names none
        ("gemini_unavailable", READER_WAITING),  # an outage: it goes on by itself
        ("ocr_unavailable", READER_WAITING),
    ],
)
def test_407_a_wait_on_a_key_problem_says_reading_is_stopped_not_that_it_goes_on(
    db: Database, code: str, message: str
) -> None:
    """The pipeline's order: the progress becomes ``{"step": "waiting"}``, then the run ends
    with the code. The list and the detail say which wait it is."""
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = api.add_document(c, status="clean")
    run = PgRuns(db).start(c, doc, now=api.clock())
    PgDocuments(db).clear_progress(c, doc, run_id=run, waiting="reader")
    with db.customer(c) as conn:
        close_running(conn, c, doc, status="failed", error_code=code, now=api.clock())

    detail = reviewer.client.get(f"/api/documents/{doc}").json()
    listed = {row["id"]: row for row in reviewer.client.get("/api/documents").json()["items"]}

    assert (detail["status_message"], detail["progress"]) == (message, None)
    assert listed[doc]["status_message"] == message


def test_407_a_retry_while_its_run_is_still_open_never_makes_two_runs(db: Database) -> None:
    """The run fence: the retry's job starts a run that supersedes the one still open, whose
    writes are then refused (Superseded); the first job's later delivery finds the document
    settled and is skipped, so it is read once and only one run is ready."""
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = UploadService(PIPELINE_POLICY, PgDocuments(db), api.storage, MemoryQueue()).accept(
        c, io.BytesIO(CSV)
    )
    params = {"c": c, "d": doc.document_id}
    with db.customer(c) as conn:
        conn.execute(
            text(
                "UPDATE documents SET status = 'clean', created_at = :at WHERE customer_id = :c AND id = :d"
            ),
            {**params, "at": hours_ago(7)},
        )
    first = PgRuns(db).start(c, doc.document_id, now=api.clock())  # its job is still reading it

    assert reprocess(reviewer, doc.document_id).status_code == 200
    read = with_runs(db, api.storage)
    read(Job(c, doc.document_id), lambda: None)  # the retry's job
    with pytest.raises(Superseded):
        PgDocuments(db).set_progress(c, doc.document_id, Progress("reading", 1, 2), run_id=first)
    read(Job(c, doc.document_id), lambda: None)  # the first job comes back: settled, skipped

    with db.customer(c) as conn:
        runs: dict[str, str] = dict(
            conn.execute(
                text(
                    "SELECT id::text, status FROM processing_runs WHERE customer_id = :c AND document_id = :d"
                ),
                params,
            ).all()
        )
        status = conn.execute(
            text("SELECT status FROM documents WHERE customer_id = :c AND id = :d"), params
        ).scalar_one()
    assert status == "ready"
    assert runs[first] == "superseded"
    assert sorted(runs.values()) == ["ready", "superseded"]
