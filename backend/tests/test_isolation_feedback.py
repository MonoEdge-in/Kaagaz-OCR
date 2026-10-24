"""Useful and Not useful never cross companies (PRD 6 F1, F2): customer B cannot rate A's
answer with reasons, never sees A's ratings, and a handled mark can never name another
company's user (the key on (customer_id, handled_by)). B's admin never lists A's Not useful
answers or counts, and marking A's by id is 404 with A's row unchanged."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, MovingClock, Signed

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


def test_b_admin_never_lists_counts_or_marks_a_not_useful_answers(db: Database) -> None:
    api = Api(db, clock=MovingClock(datetime.now(UTC)))
    a_admin, b_admin = api.new_admin("Sharma Traders"), api.new_admin("Mehta Textiles")
    a_member = api.sign_in(a_admin.customer_id)
    asked = a_member.client.post("/api/chat", json={"question": "sharma total"}, headers=CSRF).json()
    path = f"/api/messages/{asked['answer']['id']}/feedback"
    assert a_member.client.post(path, json=NOT_USEFUL, headers=CSRF).status_code == 204
    [item] = a_admin.client.get("/api/admin/answer-quality").json()["items"]

    seen = b_admin.client.get("/api/admin/answer-quality")
    assert seen.status_code == 200
    assert seen.json()["items"] == [] and seen.json()["total"] == 0
    assert all(week["not_useful"] == 0 and week["useful"] == 0 for week in seen.json()["weeks"])
    assert "sharma" not in seen.text
    for handled in (True, False):
        body = {"handled": handled, "seen": item["given_at"]}  # A's exact rating: still 404, never 409
        reply = b_admin.client.put(f"/api/admin/answer-quality/{item['id']}/handled", json=body, headers=CSRF)
        assert reply.status_code == 404
        assert reply.json()["error"]["code"] == "not_found"
    with db.customer(a_admin.customer_id) as conn:
        assert conn.execute(text("SELECT handled_at FROM feedback")).scalar_one() is None
    assert not [row for row in api.audit_rows(b_admin.customer_id) if row[0].startswith("feedback.")]
