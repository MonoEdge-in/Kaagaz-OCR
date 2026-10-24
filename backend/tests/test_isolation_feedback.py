"""Useful and Not useful never cross companies (PRD 6 F1): customer B cannot rate A's answer
with reasons, never sees A's ratings, and a handled mark can never name another company's
user (the key on (customer_id, handled_by))."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db

NOT_USEFUL = {
    "rating": "down",
    "reason": "sharma reason",
    "reason_codes": ["wrong_figure"],
    "notice_shown": True,
}


def _two(db: Database) -> tuple[Signed, Signed, str]:
    """Customers A and B, and an answer in A's conversation."""
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    asked = a.client.post("/api/chat", json={"question": "sharma total"}, headers=CSRF).json()
    return a, b, asked["answer"]["id"]


def test_b_cannot_rate_a_answer_with_reasons_or_see_a_rating(db: Database) -> None:
    a, b, answer = _two(db)
    refused = b.client.post(f"/api/messages/{answer}/feedback", json=NOT_USEFUL, headers=CSRF)
    assert refused.status_code == 404
    with db.customer(a.customer_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM feedback")).scalar_one() == 0

    assert a.client.post(f"/api/messages/{answer}/feedback", json=NOT_USEFUL, headers=CSRF).status_code == 204
    with db.customer(b.customer_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM feedback")).scalar_one() == 0
    with db.customer(a.customer_id) as conn:
        assert conn.execute(text("SELECT reason FROM feedback")).scalar_one() == "sharma reason"


def test_a_handled_mark_cannot_name_another_companys_user(db: Database) -> None:
    a, b, answer = _two(db)
    assert a.client.post(f"/api/messages/{answer}/feedback", json=NOT_USEFUL, headers=CSRF).status_code == 204
    with pytest.raises(IntegrityError), db.customer(a.customer_id) as conn:
        conn.execute(text("UPDATE feedback SET handled_at = now(), handled_by = :u"), {"u": b.user_id})
    with db.customer(a.customer_id) as conn:
        assert conn.execute(text("SELECT handled_by FROM feedback")).scalar_one() is None
