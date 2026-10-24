"""The company admin's Answer quality page (PRD 6 F2): what is listed, the weekly counts,
Mark as handled and its Undo, the audit rows, and who may see it."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.api.routers.answer_quality import first_monday
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, MovingClock, Signed, answer_json

PATH = "/api/admin/answer-quality"
NOT_USEFUL = {"rating": "down", "reason_codes": ["wrong_figure", "other"], "notice_shown": True}


def test_the_period_starts_on_a_monday_at_midnight_in_india() -> None:
    sunday_night = datetime(2026, 10, 25, 20, 0, tzinfo=UTC)  # Monday 01:30 in Kolkata
    assert first_monday(sunday_night, "Asia/Kolkata", 1).isoformat() == "2026-10-26T00:00:00+05:30"
    assert first_monday(sunday_night, "Asia/Kolkata", 4).date().isoformat() == "2026-10-05"


def _api(db: Database) -> Api:
    # The rows get the database's now(): the app's clock is set to it, so the weeks match.
    return Api(db, clock=MovingClock(datetime.now(UTC)))


def _rated(signed: Signed, question: str, body: dict[str, object]) -> str:
    answer = signed.client.post("/api/chat", json={"question": question}, headers=CSRF).json()["answer"]["id"]
    assert signed.client.post(f"/api/messages/{answer}/feedback", json=body, headers=CSRF).status_code == 204
    return str(answer)


@pytest.mark.db
def test_the_admin_sees_not_useful_answers_given_after_the_notice_with_who_asked(db: Database) -> None:
    api = _api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id)
    document = api.add_document(admin.customer_id, name="march-bill.pdf")
    cited = {
        **answer_json(),
        "citations": [{"n": 1, "document_id": document, "document_name": "march-bill.pdf", "page_no": 1,
                       "snippet": "Total 1,200", "regions": [{"page": 1}], "label_regions": []}],
    }  # fmt: skip
    api.answers.reply = cited
    shown = _rated(member, "<b>what</b> is the total", {**NOT_USEFUL, "reason": "<script>x</script>"})
    _rated(member, "before the notice", {"rating": "down", "reason": "old screen"})
    _rated(member, "a useful one", {"rating": "up"})

    body = admin.client.get(PATH).json()
    assert body["total"] == 1
    [item] = body["items"]
    assert item["message_id"] == shown
    assert item["asked_by"] == "Test member"
    assert item["question"] == "<b>what</b> is the total"  # plain text, as typed; the screen shows it as text
    assert item["reason"] == "<script>x</script>"
    assert item["reason_codes"] == ["wrong_figure", "other"]
    assert item["handled_at"] is None
    assert item["answer"]["citations"][0]["source_state"] == "live"
    this_week = body["weeks"][0]  # newest first; every rating counts, before the notice too
    assert (this_week["useful"], this_week["not_useful"]) == (1, 2)
    assert len(body["weeks"]) == 4 and body["weeks"][0]["week"] > body["weeks"][-1]["week"]

    with db.customer(admin.customer_id) as conn:
        conn.execute(text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": document})
    gone = admin.client.get(PATH).json()["items"][0]["answer"]["citations"][0]
    assert (gone["source_state"], gone["snippet"], gone["regions"]) == ("in_trash", None, [])


@pytest.mark.db
def test_mark_as_handled_and_undo_move_it_between_the_filters_and_audit_ids_only(db: Database) -> None:
    api = _api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id)
    _rated(member, "what is the total", {**NOT_USEFUL, "reason": "secret words"})
    item = admin.client.get(PATH).json()["items"][0]

    done = admin.client.put(f"{PATH}/{item['id']}/handled", json={"handled": True}, headers=CSRF)
    assert done.status_code == 200 and done.json()["handled_at"]
    assert admin.client.get(PATH).json()["total"] == 0
    assert admin.client.get(PATH, params={"status": "handled"}).json()["items"][0]["id"] == item["id"]
    undone = admin.client.put(f"{PATH}/{item['id']}/handled", json={"handled": False}, headers=CSRF)
    assert undone.json()["handled_at"] is None
    assert admin.client.get(PATH).json()["total"] == 1

    rows = [row for row in api.audit_rows(admin.customer_id) if row[0].startswith("feedback.")]
    assert rows == [
        ("feedback.handled", "feedback", item["id"], {"message_id": item["message_id"]}),
        ("feedback.reopened", "feedback", item["id"], {"message_id": item["message_id"]}),
    ]
    assert "secret words" not in repr(api.audit_rows(admin.customer_id))


@pytest.mark.db
def test_only_not_useful_after_the_notice_may_be_marked(db: Database) -> None:
    api = _api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id)
    _rated(member, "q", {"rating": "down", "reason": "old screen"})
    with db.customer(admin.customer_id) as conn:
        old = str(conn.execute(text("SELECT id FROM feedback")).scalar_one())
    reply = admin.client.put(f"{PATH}/{old}/handled", json={"handled": True}, headers=CSRF)
    assert reply.status_code == 404


@pytest.mark.db
def test_the_list_is_paged_at_most_100_and_the_period_is_whole_weeks(db: Database) -> None:
    api = _api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id)
    for n in range(3):
        _rated(member, f"question {n}", NOT_USEFUL)
    first = admin.client.get(PATH, params={"limit": 2}).json()
    rest = admin.client.get(PATH, params={"limit": 2, "offset": 2}).json()
    assert (len(first["items"]), first["total"], len(rest["items"]), rest["total"]) == (2, 3, 1, 3)
    assert [item["question"] for item in first["items"]] == ["question 2", "question 1"]
    for bad in ({"limit": 101}, {"weeks": 0}, {"weeks": 53}, {"status": "all"}, {"offset": -1}):
        assert admin.client.get(PATH, params=bad).status_code == 400, bad
    assert len(admin.client.get(PATH, params={"weeks": 12}).json()["weeks"]) == 12
    with db.customer(admin.customer_id) as conn:
        conn.execute(text("UPDATE feedback SET created_at = now() - interval '60 days'"))
    assert admin.client.get(PATH).json()["total"] == 0  # older than the 4 weeks shown
    assert admin.client.get(PATH, params={"weeks": 12}).json()["total"] == 3


@pytest.mark.db
def test_a_member_or_reviewer_gets_403(db: Database) -> None:
    api = _api(db)
    admin = api.new_admin()
    for role in ("member", "reviewer"):
        signed = api.sign_in(admin.customer_id, role)
        assert signed.client.get(PATH).status_code == 403, role
        reply = signed.client.put(f"{PATH}/{signed.user_id}/handled", json={"handled": True}, headers=CSRF)
        assert reply.status_code == 403, role
