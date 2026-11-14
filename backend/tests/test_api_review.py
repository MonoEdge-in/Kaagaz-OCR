"""The review queue and resolving a value, against a real database."""

import threading
import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from kaagaz.db import review as review_sql
from kaagaz.db.engine import Database
from tests.db_support import database_url, make_database, wait_until_blocked
from tests.test_api_support import CSRF, Api, Signed

PAN = "ABCDE1234F"  # made up
NOT_NEEDS_REVIEW = {
    "error": {"code": "not_needs_review", "message": "This value doesn't need review any more."}
}


def _setup(db: Database) -> tuple[Api, Signed, str, str]:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document_id = api.add_document(reviewer.customer_id, name="bill.pdf")
    api.add_page(reviewer.customer_id, document_id)
    value_id = api.add_value(reviewer.customer_id, document_id, value="1,200", raw="1,200", second="1,250")
    return api, reviewer, document_id, value_id


def uuid_of(signed: Signed) -> uuid.UUID:
    return uuid.UUID(signed.user_id)


def _stored(
    db: Database, customer_id: str, value_id: str
) -> tuple[str, Decimal | None, date | None, str, str]:
    with db.customer(customer_id) as conn:
        row = conn.execute(
            text(
                "SELECT value_text, value_number, value_date, label, label_reason"
                " FROM extracted_values WHERE id = :v"
            ),
            {"v": value_id},
        ).one()
    return row[0], row[1], row[2], row[3], row[4]


@pytest.mark.db
def test_a_queued_value_carries_its_label_spot(db: Database) -> None:
    """PRD 2 D3: the review queue shows a value as the detail does, with its label's spot."""
    _, reviewer, _, first = _setup(db)
    with db.customer(reviewer.customer_id) as conn:
        conn.execute(
            text(
                "UPDATE extracted_values SET label_page_no = 1, label_region = CAST(:r AS jsonb)"
                " WHERE customer_id = :c AND id = :v"
            ),
            {
                "c": reviewer.customer_id,
                "v": first,
                "r": '{"kind": "box", "page": 1, "box": [0.1, 0.1, 0.3, 0.12]}',
            },
        )
    (item,) = reviewer.client.get("/api/review").json()["items"]
    assert (item["value"]["label_page_no"], item["value"]["label_region"]) == (
        1,
        {"kind": "box", "page": 1, "box": [0.1, 0.1, 0.3, 0.12]},
    )


@pytest.mark.db
def test_queue_lists_needs_review_values_oldest_first_with_their_page(db: Database) -> None:
    api, reviewer, document_id, first = _setup(db)
    second = api.add_value(reviewer.customer_id, document_id, field_name="gstin", value="27X")
    api.add_value(
        reviewer.customer_id, document_id, field_name="subtotal", label="verified", reason="readers_agree"
    )

    body = reviewer.client.get("/api/review").json()

    assert body["total"] == 2
    assert [item["value"]["id"] for item in body["items"]] == [first, second]
    item = body["items"][0]
    # Changed on purpose (#600): the page's turn saved for everyone (0: none).
    assert set(item) == {
        "value", "document_id", "document_name", "detected_type", "page", "check_reasons", "page_turn"
    }  # fmt: skip
    assert item["page_turn"] == 0
    assert item["document_id"] == document_id
    assert item["document_name"] == "bill.pdf"
    assert item["detected_type"] == "pdf"
    assert item["page"] == {  # a PageSummary, with PRD 2's route fields
        "page_no": 1,
        "width": 595.0,
        "height": 842.0,
        "unit": "pt",
        "has_image": True,
        "route": None,
        "effective_dpi": None,
        "low_resolution": False,
        "read_by": None,  # PRD 5 RT2, F1 and F5: a page no run has read
        "read_quality": "good",
        "read_issues": [],
        "unsure_words": 0,
        "illegible_words": 0,
        "unpinned_lines": 0,
        "accepted_by": None,
        "accepted_at": None,
        "read_again_requested_at": None,
    }
    assert item["value"]["second_reading"] == "1,250"


@pytest.mark.db
def test_queue_filters_by_document_and_paginates(db: Database) -> None:
    api, reviewer, document_id, first = _setup(db)
    other = api.add_document(reviewer.customer_id, name="other.pdf")
    api.add_page(reviewer.customer_id, other)
    api.add_value(reviewer.customer_id, other)

    only = reviewer.client.get(f"/api/review?document_id={document_id}").json()
    assert [item["value"]["id"] for item in only["items"]] == [first]
    assert only["total"] == 1
    page = reviewer.client.get("/api/review?limit=1&offset=1").json()
    assert page["total"] == 2
    assert len(page["items"]) == 1


@pytest.mark.db
def test_queue_is_current_only_but_ready_older_versions_can_be_verified_from_the_viewer(db: Database) -> None:
    api, reviewer, older, older_value = _setup(db)
    customer_id = reviewer.customer_id
    with db.customer(customer_id) as conn:
        conn.execute(text("UPDATE documents SET is_current = false WHERE id = :d"), {"d": older})
    current = api.add_document(customer_id, name="bill v2.pdf", family_id=older, version_no=2)
    reading = api.add_document(
        customer_id, name="bill v3.pdf", family_id=older, version_no=3, is_current=False, status="clean"
    )
    values: dict[str, str] = {}
    for document in (current, reading):
        api.add_page(customer_id, document)
        values[document] = api.add_value(customer_id, document)

    body = reviewer.client.get("/api/review").json()
    assert ([item["value"]["id"] for item in body["items"]], body["total"]) == ([values[current]], 1)
    resolved_older = reviewer.client.post(
        f"/api/review/{older_value}", json={"choice": "first", "remember": False}, headers=CSRF
    )
    assert resolved_older.status_code == 200
    assert _stored(db, customer_id, older_value)[3] == "verified"

    reply = reviewer.client.post(
        f"/api/review/{values[reading]}", json={"choice": "first", "remember": False}, headers=CSRF
    )
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    assert _stored(db, customer_id, values[reading])[3] == "needs_review"
    resolved = reviewer.client.post(
        f"/api/review/{values[current]}", json={"choice": "first", "remember": False}, headers=CSRF
    )
    assert resolved.status_code == 200


@pytest.mark.db
def test_use_first_reading(db: Database) -> None:
    _, reviewer, _, value_id = _setup(db)
    reply = reviewer.client.post(
        f"/api/review/{value_id}", json={"choice": "first", "remember": False}, headers=CSRF
    )
    assert reply.status_code == 200
    assert reply.json()["value"]["value"] == "1,200"
    assert reply.json()["value"]["label"] == "verified"
    assert reply.json()["value"]["label_reason"] == "corrected"
    assert _stored(db, reviewer.customer_id, value_id) == (
        "1,200",
        Decimal(1200),
        None,
        "verified",
        "corrected",
    )
    assert reviewer.client.get("/api/review").json()["total"] == 0


@pytest.mark.db
def test_use_second_reading_writes_a_correction_and_remembers_it(db: Database) -> None:
    api, reviewer, _, value_id = _setup(db)
    reply = reviewer.client.post(
        f"/api/review/{value_id}", json={"choice": "second", "remember": True}, headers=CSRF
    )
    assert reply.status_code == 200
    assert _stored(db, reviewer.customer_id, value_id)[:2] == ("1,250", Decimal(1250))
    with db.customer(reviewer.customer_id) as conn:
        correction = conn.execute(
            text("SELECT old_value, new_value, choice, corrected_by FROM corrections WHERE value_id = :v"),
            {"v": value_id},
        ).one()
        learned = conn.execute(
            text("SELECT field, raw_value, corrected_value, created_by FROM learned_corrections")
        ).all()
    assert tuple(correction) == ("1,200", "1,250", "second", uuid_of(reviewer))
    assert [tuple(row) for row in learned] == [("total_amount", "1,200", "1,250", uuid_of(reviewer))]
    assert api.audit_actions(reviewer.customer_id)[-1] == "review.resolve"


@pytest.mark.db
def test_typed_value_is_kept_as_typed_parsed_and_a_second_remember_updates(db: Database) -> None:
    api, reviewer, document_id, value_id = _setup(db)
    reviewer.client.post(
        f"/api/review/{value_id}", json={"choice": "typed", "value": "1,300", "remember": True}, headers=CSRF
    )
    other = api.add_value(reviewer.customer_id, document_id, value="1,200", raw="1,200")
    reply = reviewer.client.post(
        f"/api/review/{other}",
        json={"choice": "typed", "value": f"12/08/2026 {PAN}", "remember": True},
        headers=CSRF,
    )
    assert reply.status_code == 200
    stored = _stored(db, reviewer.customer_id, other)
    assert PAN in stored[0]
    with db.customer(reviewer.customer_id) as conn:
        learned: Sequence[str] = (
            conn.execute(text("SELECT corrected_value FROM learned_corrections")).scalars().all()
        )
    assert len(learned) == 1  # the same (field, raw value): replaced, not added
    assert PAN in learned[0]


@pytest.mark.db
def test_typed_value_is_parsed_as_a_date(db: Database) -> None:
    _, reviewer, _, value_id = _setup(db)
    reviewer.client.post(
        f"/api/review/{value_id}",
        json={"choice": "typed", "value": "12/08/2026", "remember": False},
        headers=CSRF,
    )
    assert _stored(db, reviewer.customer_id, value_id)[2] == date(2026, 8, 12)


@pytest.mark.db
def test_second_without_a_second_reading_is_400_and_changes_nothing(db: Database) -> None:
    api, reviewer, document_id, _ = _setup(db)
    lone = api.add_value(reviewer.customer_id, document_id, second=None, reason="single_reader")
    reply = reviewer.client.post(
        f"/api/review/{lone}", json={"choice": "second", "remember": True}, headers=CSRF
    )
    assert reply.status_code == 400
    assert _stored(db, reviewer.customer_id, lone)[3] == "needs_review"


@pytest.mark.db
@pytest.mark.parametrize(
    "body",
    [
        {"choice": "typed", "remember": False},
        {"choice": "typed", "value": "   ", "remember": False},
        {"choice": "typed", "value": "x" * 501, "remember": False},
        {"choice": "other", "remember": False},
        {"choice": "first"},
    ],
)
def test_bad_resolve_bodies_are_400(db: Database, body: dict[str, object]) -> None:
    _, reviewer, _, value_id = _setup(db)
    reply = reviewer.client.post(f"/api/review/{value_id}", json=body, headers=CSRF)
    assert reply.status_code == 400
    assert _stored(db, reviewer.customer_id, value_id)[3] == "needs_review"


@pytest.mark.db
def test_members_cannot_resolve(db: Database) -> None:
    api, reviewer, _, value_id = _setup(db)
    member = api.sign_in(reviewer.customer_id, "member")
    reply = member.client.post(
        f"/api/review/{value_id}", json={"choice": "first", "remember": False}, headers=CSRF
    )
    assert reply.status_code == 403
    assert _stored(db, reviewer.customer_id, value_id)[3] == "needs_review"


@pytest.mark.db
def test_verify_and_verify_all_from_the_document_view(db: Database) -> None:
    """Issue #224: the document view's Verify and Verify all send each value still Ready to
    verify as typed, unchanged, one POST each. Each keeps its text and gets one
    review.resolve audit row; a verified value and the older version are left alone."""
    api, reviewer, older, older_value = _setup(db)
    customer_id = reviewer.customer_id
    with db.customer(customer_id) as conn:
        conn.execute(text("UPDATE documents SET is_current = false WHERE id = :d"), {"d": older})
    current = api.add_document(customer_id, name="kirana bill v2.pdf", family_id=older, version_no=2)
    api.add_page(customer_id, current)
    pending = [
        api.add_value(customer_id, current, value="1,200", raw="1,200"),
        api.add_value(customer_id, current, field_name="invoice_date", value="12/08/2026", raw="12/O8/2026"),
    ]
    verified = api.add_value(
        customer_id, current, field_name="gstin", label="verified", reason="readers_agree"
    )
    admin = api.sign_in(customer_id, "admin")
    audit_before = len(api.audit_rows(customer_id))

    for signed, value_id, shown in ((reviewer, pending[0], "1,200"), (admin, pending[1], "12/08/2026")):
        reply = signed.client.post(
            f"/api/review/{value_id}",
            json={"choice": "typed", "value": shown, "remember": False},
            headers=CSRF,
        )
        assert reply.status_code == 200
        assert (reply.json()["value"]["value"], reply.json()["value"]["label"]) == (shown, "verified")

    assert _stored(db, customer_id, pending[0]) == ("1,200", Decimal(1200), None, "verified", "corrected")
    assert _stored(db, customer_id, pending[1])[2:] == (date(2026, 8, 12), "verified", "corrected")
    assert _stored(db, customer_id, verified) == ("1,200", None, None, "verified", "readers_agree")
    assert _stored(db, customer_id, older_value) == ("1,200", None, None, "needs_review", "readers_disagree")
    assert _writes(db, customer_id) == (2, 0)  # two correction rows, nothing remembered
    details = {"choice": "typed", "remember": False, "document_id": current}
    assert api.audit_rows(customer_id)[audit_before:] == [
        ("review.resolve", "extracted_value", pending[0], details),
        ("review.resolve", "extracted_value", pending[1], details),
    ]
    # The detail, read again as after a page reload, still shows both as Verified.
    labels = {
        value["id"]: value["label"]
        for value in reviewer.client.get(f"/api/documents/{current}").json()["values"]
    }
    assert labels == {pending[0]: "verified", pending[1]: "verified", verified: "verified"}


# --- only a value that needs review can be resolved -----------------------------------


def _writes(db: Database, customer_id: str) -> tuple[int, int]:
    """How many corrections and remembered corrections the customer has."""
    with db.customer(customer_id) as conn:
        corrections: int = conn.execute(text("SELECT count(*) FROM corrections")).scalar_one()
        learned: int = conn.execute(text("SELECT count(*) FROM learned_corrections")).scalar_one()
    return int(corrections), int(learned)


@pytest.mark.db
def test_a_verified_value_is_409_and_nothing_changes(db: Database) -> None:
    api, reviewer, document_id, _ = _setup(db)
    verified = api.add_value(reviewer.customer_id, document_id, label="verified", reason="readers_agree")
    audit_before = api.audit_actions(reviewer.customer_id)

    reply = reviewer.client.post(
        f"/api/review/{verified}", json={"choice": "typed", "value": "9,999", "remember": True}, headers=CSRF
    )

    assert reply.status_code == 409
    assert reply.json() == NOT_NEEDS_REVIEW
    assert _stored(db, reviewer.customer_id, verified) == ("1,200", None, None, "verified", "readers_agree")
    assert _writes(db, reviewer.customer_id) == (0, 0)
    assert api.audit_actions(reviewer.customer_id) == audit_before


@pytest.mark.db
def test_resolving_twice_is_409_the_second_time(db: Database) -> None:
    api, reviewer, _, value_id = _setup(db)
    first = reviewer.client.post(
        f"/api/review/{value_id}", json={"choice": "second", "remember": False}, headers=CSRF
    )
    again = reviewer.client.post(
        f"/api/review/{value_id}", json={"choice": "typed", "value": "9,999", "remember": True}, headers=CSRF
    )

    assert first.status_code == 200
    assert again.status_code == 409
    assert again.json() == NOT_NEEDS_REVIEW
    assert _stored(db, reviewer.customer_id, value_id)[:2] == ("1,250", Decimal(1250))
    assert _writes(db, reviewer.customer_id) == (1, 0)
    assert api.audit_actions(reviewer.customer_id).count("review.resolve") == 1


def _named_database(name: str) -> Database:
    """The test database, with every session named ``name`` in pg_stat_activity."""
    url = database_url()
    return make_database(f"{url}{'&' if '?' in url else '?'}application_name={name}")


@pytest.mark.db
def test_two_reviewers_at_once_only_one_wins(db: Database) -> None:
    """The order is forced: the test holds the value's lock until both requests wait
    behind it, first then second, so the result never depends on timing."""
    first_db, second_db = _named_database("review-first"), _named_database("review-second")
    try:
        first_api, second_api = Api(first_db), Api(second_db)
        first = first_api.new_signed("reviewer")
        customer_id = first.customer_id
        second = second_api.sign_in(customer_id, "reviewer")
        document_id = first_api.add_document(customer_id, name="bill.pdf")
        first_api.add_page(customer_id, document_id)
        value_id = first_api.add_value(customer_id, document_id)
        statuses: dict[str, int] = {}

        def resolve(name: str, signed: Signed, typed: str) -> None:
            body = {"choice": "typed", "value": typed, "remember": False}
            statuses[name] = signed.client.post(
                f"/api/review/{value_id}", json=body, headers=CSRF
            ).status_code

        threads = {
            "first": threading.Thread(target=resolve, args=("first", first, "1,300")),
            "second": threading.Thread(target=resolve, args=("second", second, "1,400")),
        }
        with db.customer(customer_id) as conn:
            assert review_sql.lock_value(conn, customer_id, value_id) is not None
            for name, thread in threads.items():
                thread.start()
                wait_until_blocked(db, application_name=f"review-{name}")
        for thread in threads.values():
            thread.join(timeout=10)

        assert statuses == {"first": 200, "second": 409}
        assert _stored(db, customer_id, value_id)[:2] == ("1,300", Decimal(1300))
        assert _writes(db, customer_id) == (1, 0)
        assert first_api.audit_actions(customer_id).count("review.resolve") == 1
    finally:
        first_db.engine.dispose()
        second_db.engine.dispose()
