"""The review queue and resolving a value, against a real database."""

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed

PAN = "ABCDE1234F"  # made up


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
    assert set(item) == {"value", "document_id", "document_name", "detected_type", "page"}
    assert item["document_id"] == document_id
    assert item["document_name"] == "bill.pdf"
    assert item["detected_type"] == "pdf"
    assert item["page"] == {"page_no": 1, "width": 595.0, "height": 842.0, "unit": "pt", "has_image": True}
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
