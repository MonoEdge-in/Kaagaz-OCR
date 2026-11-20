"""POST /api/documents/{id}/stop: Stop (#713).

A reviewer or an admin stops a file still waiting, being checked or being read: it stays in
Documents, failed with ``stopped``, its open run ends failed with the same code, and one
``document.stop`` audit row says what it was. Read again starts it again. Anything else is 409
and nothing changes. What the worker does next is in tests/test_pipeline_db.py; another
customer's file in tests/test_isolation_api_stop.py; a hidden one in
tests/test_isolation_access_views.py (_ROUTES).
"""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db


def stop(signed: Signed, document_id: str) -> Any:
    return signed.client.post(f"/api/documents/{document_id}/stop", headers=CSRF)


def stored(api: Api, customer_id: str, document_id: str) -> tuple[object, ...]:
    with api.db.customer(customer_id) as conn:
        return tuple(
            conn.execute(
                text(
                    "SELECT status, failure_code, deleted_at, reading_held FROM documents"
                    " WHERE customer_id = :c AND id = :d"
                ),
                {"c": customer_id, "d": document_id},
            ).one()
        )


def runs(api: Api, customer_id: str, document_id: str) -> list[tuple[object, ...]]:
    with api.db.customer(customer_id) as conn:
        return [
            tuple(row)
            for row in conn.execute(
                text(
                    "SELECT status, error_code FROM processing_runs"
                    " WHERE customer_id = :c AND document_id = :d ORDER BY started_at, id"
                ),
                {"c": customer_id, "d": document_id},
            ).all()
        ]


def open_run(api: Api, customer_id: str, document_id: str) -> None:
    with api.db.customer(customer_id) as conn:
        conn.execute(
            text(
                "INSERT INTO processing_runs (customer_id, document_id, started_at, status)"
                " VALUES (:c, :d, now(), 'running')"
            ),
            {"c": customer_id, "d": document_id},
        )


@pytest.mark.parametrize("role", ["reviewer", "admin"])
@pytest.mark.parametrize("status", ["received", "clean"])
def test_a_file_being_read_is_stopped_kept_and_said_in_activity(db: Database, role: str, status: str) -> None:
    api = Api(db)
    signed = api.new_signed(role)
    c = signed.customer_id
    doc = api.add_document(c, status=status)
    open_run(api, c, doc)
    with api.db.customer(c) as conn:  # held by the reading pause (#478): the hold ends with the Stop
        conn.execute(text("UPDATE documents SET reading_held = 'daily_limit' WHERE id = :d"), {"d": doc})

    reply = stop(signed, doc)

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["status"], body["failure_code"]) == ("failed", "stopped")
    assert body["status_message"] == "Reading was stopped. Read it again to start it again."
    assert stored(api, c, doc) == ("failed", "stopped", None, None)  # in Documents, no pause hold left
    assert runs(api, c, doc) == [("failed", "stopped")]  # the worker sees its run closed
    assert api.audit_rows(c)[-1] == ("document.stop", "document", doc, {"status": status})
    assert api.queue.jobs == []


def test_a_member_gets_403_and_nothing_changes(db: Database) -> None:
    api = Api(db)
    member = api.new_signed("member")
    doc = api.add_document(member.customer_id, status="clean", uploaded_by=member.user_id)
    reply = stop(member, doc)
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "forbidden")
    assert stored(api, member.customer_id, doc)[:2] == ("clean", None)
    assert "document.stop" not in api.audit_actions(member.customer_id)


@pytest.mark.parametrize(
    ("columns", "code"),
    [
        ({"status": "ready"}, "not_stoppable"),
        ({"status": "failed", "failure_code": "corrupt_file"}, "not_stoppable"),
        ({"status": "failed", "failure_code": "stopped"}, "not_stoppable"),  # stopped already
        ({"status": "needs_reader"}, "not_stoppable"),
        ({"status": "rejected", "failure_code": "virus_found"}, "not_stoppable"),
        ({"status": "clean", "in_trash": True}, "in_trash"),
    ],
)
def test_a_file_not_being_read_is_409_and_nothing_changes(
    db: Database, columns: dict[str, Any], code: str
) -> None:
    """A locked one is 409 ``locked``: tests/test_isolation_locked_files.py (REFUSED)."""
    api = Api(db)
    admin = api.new_signed("admin")
    c = admin.customer_id
    extra = {key: value for key, value in columns.items() if key != "in_trash"}
    if columns.get("in_trash"):
        extra.update(deleted_at=api.clock(), deleted_by=admin.user_id)
    doc = api.add_document(c, **extra)
    open_run(api, c, doc)
    before = stored(api, c, doc)

    reply = stop(admin, doc)

    assert (reply.status_code, reply.json()["error"]["code"]) == (409, code), reply.text
    assert stored(api, c, doc) == before
    assert runs(api, c, doc) == [("running", None)]
    assert "document.stop" not in api.audit_actions(c)


def test_read_again_starts_a_stopped_file_and_a_stop_counts_against_no_limit(db: Database) -> None:
    """Read again takes a failed file, so a stopped one too; a Stop writes no read-again row, so it
    is never counted in REPROCESS_PER_HOUR or the cooldown."""
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = api.add_document(c, status="clean")
    assert stop(reviewer, doc).status_code == 200

    reply = reviewer.client.post(f"/api/documents/{doc}/reprocess", headers=CSRF)

    assert reply.status_code == 200, reply.text
    assert stored(api, c, doc)[:2] == ("received", None)
    assert [job.document_id for job in api.queue.jobs] == [doc]
    assert api.audit_actions(c)[-2:] == ["document.stop", "document.reprocess"]
    assert api.audit_rows(c)[-1][3]["status"] == "failed"


def test_each_file_of_a_selection_is_stopped_on_its_own(db: Database) -> None:
    """A selection is stopped one call a file (as Move to Trash and Read again): a file that can't be
    stopped is refused alone and the others are stopped."""
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    reading, ready, waiting = (api.add_document(c, status=s) for s in ("clean", "ready", "received"))

    codes = [stop(reviewer, doc).status_code for doc in (reading, ready, waiting)]

    assert codes == [200, 409, 200]
    assert [stored(api, c, doc)[:2] for doc in (reading, ready, waiting)] == [
        ("failed", "stopped"),
        ("ready", None),
        ("failed", "stopped"),
    ]
    assert api.audit_actions(c).count("document.stop") == 2


def ready_run(api: Api, customer_id: str, document_id: str) -> None:
    """A reading of it that finished: the file was read before."""
    with api.db.customer(customer_id) as conn:
        conn.execute(
            text(
                "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status)"
                " VALUES (:c, :d, now() - interval '1 hour', now() - interval '1 hour', 'ready')"
            ),
            {"c": customer_id, "d": document_id},
        )


def test_a_file_read_before_is_never_stopped_while_it_is_read_again(db: Database) -> None:
    """Tushar, 20 Nov 2026: its reading again goes on, in plain words; nothing changes."""
    api = Api(db)
    admin = api.new_signed("admin")
    c = admin.customer_id
    doc = api.add_document(c, status="clean")
    ready_run(api, c, doc)
    open_run(api, c, doc)
    assert admin.client.get(f"/api/documents/{doc}").json()["read_before"] is True

    reply = stop(admin, doc)

    assert (reply.status_code, reply.json()["error"]) == (
        409,
        {"code": "already_read", "message": "This file was already read. Reading again will finish."},
    )
    assert stored(api, c, doc) == ("clean", None, None, None)
    assert runs(api, c, doc) == [("ready", None), ("running", None)]
    assert "document.stop" not in api.audit_actions(c)


def test_a_first_reading_is_not_read_before(db: Database) -> None:
    api = Api(db)
    admin = api.new_signed("admin")
    doc = api.add_document(admin.customer_id, status="clean")
    assert admin.client.get(f"/api/documents/{doc}").json()["read_before"] is False


def test_a_read_again_too_soon_after_a_stop_says_which_limit(db: Database) -> None:
    """Both Read again limits stay (Tushar, 20 Nov 2026); the 429 says which one, so the screen can
    say when it may be read again."""
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    doc = api.add_document(reviewer.customer_id, status="clean")
    assert stop(reviewer, doc).status_code == 200
    assert reviewer.client.post(f"/api/documents/{doc}/reprocess", headers=CSRF).status_code == 200
    assert stop(reviewer, doc).status_code == 200

    reply = reviewer.client.post(f"/api/documents/{doc}/reprocess", headers=CSRF)

    assert reply.status_code == 429
    assert reply.json()["error"] == {
        "code": "rate_limited",
        "message": (
            "This document was sent to be read again less than 10 minutes ago. Please wait and try again."
        ),
        "details": {"reason": "cooldown"},
    }


def test_cancel_stops_a_file_being_read_and_restored_it_is_stopped(db: Database) -> None:
    """Cancel is Move to Trash (its rights and its Activity row); a file still waiting, checked or
    read for the first time is stopped first, so restored it says Stopped, never Reading."""
    api = Api(db)
    admin = api.new_signed("admin")
    c = admin.customer_id
    doc = api.add_document(c, status="clean")
    open_run(api, c, doc)

    assert admin.client.delete(f"/api/documents/{doc}", headers=CSRF).status_code == 204

    assert stored(api, c, doc)[:2] == ("failed", "stopped")
    assert runs(api, c, doc) == [("failed", "stopped")]
    assert api.audit_actions(c)[-1] == "document.trash"
    restored = admin.client.post(f"/api/documents/{doc}/restore", headers=CSRF).json()
    assert (restored["status"], restored["failure_code"]) == ("failed", "stopped")


def test_cancel_then_the_same_file_uploaded_again_is_a_new_file(db: Database) -> None:
    """#684: a copy in Trash never read (a cancelled one) never blocks: the same bytes uploaded again
    by the same person are stored as a new file and the Trash copy goes."""
    from tests.test_api_folder_upload_files import ResumableStorage
    from tests.test_api_trash_replace import exists, upload

    api = Api(db, storage=ResumableStorage())
    admin = api.new_admin()
    first = upload(admin)
    assert first.status_code == 201, first.text
    old = first.json()["document"]["id"]
    assert admin.client.delete(f"/api/documents/{old}", headers=CSRF).status_code == 204

    again = upload(admin)

    assert again.status_code == 201, again.text
    assert (again.json()["duplicate"], again.json()["in_trash"]) == (False, False)
    assert again.json()["document"]["id"] != old
    assert not exists(api, old)
