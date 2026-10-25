"""POST /api/review/verify-all: the review queue's Verify all, against a real database.

Each value goes through the same resolve as one POST /api/review/{value_id} (typed, as it
reads now, nothing remembered): one correction row and one review.resolve audit row each.
"""

import threading
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from kaagaz.api.routers import review as review_router
from kaagaz.db import review as review_sql
from kaagaz.db.engine import Database
from tests.db_support import wait_until_blocked
from tests.test_api_review import _named_database, _stored, _writes
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db

VERIFY_ALL = "/api/review/verify-all"


def _verify_all(signed: Signed, **body: object) -> dict[str, object]:
    reply = signed.client.post(VERIFY_ALL, json=body, headers=CSRF)
    assert reply.status_code == 200, reply.text
    result: dict[str, object] = reply.json()
    return result


def _queue(api: Api) -> tuple[Signed, str]:
    reviewer = api.new_signed("reviewer")
    document_id = api.add_document(reviewer.customer_id, name="kirana bill.pdf")
    api.add_page(reviewer.customer_id, document_id)
    return reviewer, document_id


def test_verify_all_verifies_every_queued_value_with_one_resolve_each(db: Database) -> None:
    api = Api(db)
    reviewer, document_id = _queue(api)
    customer_id = reviewer.customer_id
    other = api.add_document(customer_id, name="receipt.pdf")
    api.add_page(customer_id, other)
    pending = [
        api.add_value(customer_id, document_id, value="1,200", raw="1,200"),
        api.add_value(
            customer_id, document_id, field_name="invoice_date", value="12/08/2026", raw="12/O8/2026"
        ),
        api.add_value(
            customer_id, other, field_name="gstin", value="27ABCDE1234F1Z5", reason="single_reader"
        ),
    ]
    verified = api.add_value(
        customer_id, document_id, field_name="subtotal", label="verified", reason="readers_agree"
    )
    audit_before = len(api.audit_rows(customer_id))

    assert _verify_all(reviewer) == {"verified": 3, "skipped": [], "left": 0}

    assert _stored(db, customer_id, pending[0]) == ("1,200", Decimal(1200), None, "verified", "corrected")
    assert _stored(db, customer_id, pending[1])[2:] == (date(2026, 8, 12), "verified", "corrected")
    assert _stored(db, customer_id, pending[2])[3:] == ("verified", "corrected")
    assert _stored(db, customer_id, verified) == ("1,200", None, None, "verified", "readers_agree")
    assert _writes(db, customer_id) == (3, 0)  # one correction row each, nothing remembered
    details = {"choice": "typed", "remember": False, "verify_all": True}
    assert api.audit_rows(customer_id)[audit_before:] == [
        ("review.resolve", "extracted_value", pending[0], {**details, "document_id": document_id}),
        ("review.resolve", "extracted_value", pending[1], {**details, "document_id": document_id}),
        ("review.resolve", "extracted_value", pending[2], {**details, "document_id": other}),
    ]
    with db.customer(customer_id) as conn:
        corrections = conn.execute(
            text(
                "SELECT value_id::text, old_value, new_value, choice FROM corrections ORDER BY created_at, id"
            )
        ).all()
    assert sorted(tuple(row) for row in corrections) == sorted(
        [
            (pending[0], "1,200", "1,200", "typed"),
            (pending[1], "12/08/2026", "12/08/2026", "typed"),
            (pending[2], "27ABCDE1234F1Z5", "27ABCDE1234F1Z5", "typed"),
        ]
    )
    assert reviewer.client.get("/api/review").json()["total"] == 0


def test_an_admin_can_verify_all_and_a_second_run_verifies_nothing(db: Database) -> None:
    api = Api(db)
    reviewer, document_id = _queue(api)
    api.add_value(reviewer.customer_id, document_id)
    admin = api.sign_in(reviewer.customer_id, "admin")

    assert _verify_all(admin) == {"verified": 1, "skipped": [], "left": 0}
    audit = api.audit_actions(reviewer.customer_id)
    assert _verify_all(reviewer) == {"verified": 0, "skipped": [], "left": 0}
    assert api.audit_actions(reviewer.customer_id) == audit
    assert _writes(db, reviewer.customer_id) == (1, 0)


def test_values_with_nothing_to_verify_are_skipped_and_stay_in_the_queue(db: Database) -> None:
    api = Api(db)
    reviewer, document_id = _queue(api)
    customer_id = reviewer.customer_id
    empty = api.add_value(customer_id, document_id, value="", raw="")
    blank = api.add_value(customer_id, document_id, value="   ", raw="   ")
    too_long = api.add_value(
        customer_id, document_id, field_name="party_name", value="x" * 501, raw="x" * 501
    )
    good = api.add_value(customer_id, document_id)

    assert _verify_all(reviewer) == {
        "verified": 1,
        "skipped": [{"reason": "no_reading", "count": 2}, {"reason": "too_long", "count": 1}],
        "left": 3,
    }
    for value_id in (empty, blank, too_long):
        assert _stored(db, customer_id, value_id)[3] == "needs_review"
    assert _stored(db, customer_id, good)[3] == "verified"
    assert _writes(db, customer_id) == (1, 0)
    assert api.audit_actions(customer_id).count("review.resolve") == 1
    queue = reviewer.client.get("/api/review").json()
    assert sorted(item["value"]["id"] for item in queue["items"]) == sorted([empty, blank, too_long])


def test_verify_all_keeps_to_the_screens_filters(db: Database) -> None:
    api = Api(db)
    reviewer, document_id = _queue(api)
    customer_id = reviewer.customer_id
    other = api.add_document(customer_id, name="other.pdf")
    api.add_page(customer_id, other)
    disagree = api.add_value(customer_id, document_id, reason="readers_disagree")
    single = api.add_value(customer_id, document_id, field_name="gstin", reason="single_reader")
    elsewhere = api.add_value(customer_id, other, reason="readers_disagree")

    assert _verify_all(reviewer, document_id=document_id, reason=["single_reader"]) == {
        "verified": 1,
        "skipped": [],
        "left": 0,
    }
    assert [_stored(db, customer_id, v)[3] for v in (disagree, single, elsewhere)] == [
        "needs_review",
        "verified",
        "needs_review",
    ]
    assert _verify_all(reviewer, reason=["readers_disagree", "check_failed"]) == {
        "verified": 2,
        "skipped": [],
        "left": 0,
    }


def test_verify_all_leaves_trash_older_versions_and_unread_documents_alone(db: Database) -> None:
    api = Api(db)
    reviewer, older = _queue(api)
    customer_id = reviewer.customer_id
    older_value = api.add_value(customer_id, older)
    with db.customer(customer_id) as conn:
        conn.execute(text("UPDATE documents SET is_current = false WHERE id = :d"), {"d": older})
    current = api.add_document(customer_id, name="bill v2.pdf", family_id=older, version_no=2)
    reading = api.add_document(
        customer_id, name="bill v3.pdf", family_id=older, version_no=3, is_current=False, status="clean"
    )
    trashed = api.add_document(customer_id, name="old.pdf")
    with db.customer(customer_id) as conn:
        conn.execute(text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": trashed})
    values = {}
    for document in (current, reading, trashed):
        api.add_page(customer_id, document)
        values[document] = api.add_value(customer_id, document)

    assert _verify_all(reviewer) == {"verified": 1, "skipped": [], "left": 0}
    assert _stored(db, customer_id, values[current])[3] == "verified"
    for value_id in (older_value, values[reading], values[trashed]):
        assert _stored(db, customer_id, value_id)[3] == "needs_review"


def test_a_long_queue_is_verified_in_chunks(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_router, "VERIFY_ALL_CHUNK", 2)
    api = Api(db)
    reviewer, document_id = _queue(api)
    customer_id = reviewer.customer_id
    values = [api.add_value(customer_id, document_id, value=f"{n},000", raw=f"{n},000") for n in range(1, 6)]
    api.add_value(customer_id, document_id, value="", raw="")  # in the middle of a chunk, skipped
    values.append(api.add_value(customer_id, document_id))

    assert _verify_all(reviewer) == {
        "verified": 6,
        "skipped": [{"reason": "no_reading", "count": 1}],
        "left": 1,
    }
    assert {_stored(db, customer_id, v)[3] for v in values} == {"verified"}
    assert api.audit_actions(customer_id).count("review.resolve") == 6


def test_a_value_resolved_meanwhile_is_not_verified_or_audited_twice(db: Database) -> None:
    """The order is forced: the test resolves one value under its lock and holds the
    lock until Verify all waits behind it, then commits; Verify all leaves it out."""
    verify_db = _named_database("review-verify-all")
    try:
        api = Api(db)
        reviewer, document_id = _queue(api)
        customer_id = reviewer.customer_id
        busy = api.add_value(customer_id, document_id, value="1,200", raw="1,200")
        rest = [api.add_value(customer_id, document_id, field_name="gstin", value="27X", raw="27X")]
        verifier = Api(verify_db).sign_in(customer_id, "reviewer")
        replies: list[dict[str, object]] = []
        thread = threading.Thread(target=lambda: replies.append(_verify_all(verifier)))

        with db.customer(customer_id) as conn:
            current = review_sql.lock_value(conn, customer_id, busy)
            assert current is not None
            review_sql.resolve_value(
                conn,
                customer_id,
                current,
                new_text="1,300",
                choice="typed",
                remember=False,
                user_id=reviewer.user_id,
            )
            thread.start()
            wait_until_blocked(db, application_name="review-verify-all")
        thread.join(timeout=10)

        assert replies == [{"verified": 1, "skipped": [], "left": 0}]
        assert _stored(db, customer_id, busy)[:2] == ("1,300", Decimal(1300))
        assert _stored(db, customer_id, rest[0])[3] == "verified"
        assert _writes(db, customer_id) == (2, 0)
        resolved = [str(row[2]) for row in api.audit_rows(customer_id) if row[0] == "review.resolve"]
        assert sorted(resolved) == sorted([busy, rest[0]])
    finally:
        verify_db.engine.dispose()


def test_members_cannot_verify_all(db: Database) -> None:
    api = Api(db)
    reviewer, document_id = _queue(api)
    value_id = api.add_value(reviewer.customer_id, document_id)
    member = api.sign_in(reviewer.customer_id, "member")

    reply = member.client.post(VERIFY_ALL, json={}, headers=CSRF)

    assert reply.status_code == 403
    assert _stored(db, reviewer.customer_id, value_id)[3] == "needs_review"
    assert _writes(db, reviewer.customer_id) == (0, 0)


@pytest.mark.parametrize(
    "body",
    [{"reason": ["nope"]}, {"document_id": "not-an-id"}, {"choice": "first"}],
)
def test_bad_verify_all_bodies_are_400(db: Database, body: dict[str, object]) -> None:
    api = Api(db)
    reviewer, document_id = _queue(api)
    value_id = api.add_value(reviewer.customer_id, document_id)
    reply = reviewer.client.post(VERIFY_ALL, json=body, headers=CSRF)
    assert reply.status_code == 400
    assert _stored(db, reviewer.customer_id, value_id)[3] == "needs_review"
