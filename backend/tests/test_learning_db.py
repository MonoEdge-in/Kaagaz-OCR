"""What Kaagaz learns (PRD 6 F3; db/chat.py ``learned_for``, answering/pins.py ``lifts``) and
the admin's What Kaagaz learned. Another customer's marks: tests/test_isolation_feedback.py."""

import logging
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering import glossary
from kaagaz.answering.hinglish import normalise
from kaagaz.answering.pins import LIFT_LEARNED, Pin, lifts
from kaagaz.db import chat as chat_sql
from kaagaz.db.engine import Database
from tests.test_ai_settings_db import switched_off
from tests.test_api_support import CSRF, Api, Signed, answer_json

pytestmark = pytest.mark.db

ASKED = "what is the total"  # answer_json's normalized_question
WORDS = glossary.content_words(normalise(ASKED).keywords)


def _cited(api: Api, customer_id: str) -> str:
    """Every answer from now on cites page 2 of a new document."""
    document = api.add_document(customer_id, name="march-bill.pdf")
    cited = {"n": 1, "document_id": document, "document_name": "march-bill.pdf", "page_no": 2}
    api.answers.reply = {**answer_json(), "citations": [cited]}
    return document


def _ask(api: Api, signed: Signed) -> tuple[str, list[Pin]]:
    # Changed on purpose (#535): each ask its own question, as one asked again unchanged is answered
    # from its stored answer with no service call; the pins come from the stored answer's question.
    asked = f"the total? ({len(api.answers.asked)})"
    reply = signed.client.post("/api/chat", json={"question": asked}, headers=CSRF)
    assert reply.status_code == 200, reply.text
    return str(reply.json()["answer"]["id"]), api.answers.pins[-1]


def _rate(api: Api, signed: Signed, body: dict[str, Any]) -> None:
    answer, _ = _ask(api, signed)
    assert signed.client.post(f"/api/messages/{answer}/feedback", json=body, headers=CSRF).status_code == 204


def _learned(pins: list[Pin]) -> list[Pin]:
    return [pin for pin in pins if pin.kind != "pick"]


def test_a_useful_answer_lifts_its_pages_for_me_and_for_everyone_once_two_people_marked_it(
    db: Database,
) -> None:
    api = Api(db)
    admin = api.new_admin()
    ravi, asha, kiran = (api.sign_in(admin.customer_id) for _ in range(3))
    document = _cited(api, admin.customer_id)
    _rate(api, ravi, {"rating": "up"})

    _, mine = _ask(api, ravi)
    assert _learned(mine) == [Pin(document, 2, False, ASKED, kind="useful", user_id=ravi.user_id, own=True)]
    assert lifts(mine, WORDS) == {document: {2: LIFT_LEARNED}}
    _, theirs = _ask(api, kiran)
    assert [pin.own for pin in _learned(theirs)] == [False]
    assert lifts(theirs, WORDS) == {}  # one person: for them only
    _rate(api, asha, {"rating": "up"})
    _, theirs = _ask(api, kiran)
    assert lifts(theirs, WORDS) == {document: {2: LIFT_LEARNED}}


def test_wrong_documents_stops_my_lift_and_only_answered_live_recent_marks_count(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document = _cited(api, signed.customer_id)
    _rate(api, signed, {"rating": "up"})
    _rate(api, signed, {"rating": "down", "reason_codes": ["wrong_figure"], "notice_shown": True})
    _, pins = _ask(api, signed)
    assert [pin.kind for pin in _learned(pins)] == ["useful"]  # a wrong figure teaches nothing
    _rate(api, signed, {"rating": "down", "reason_codes": ["wrong_documents"], "notice_shown": True})
    _, pins = _ask(api, signed)
    assert sorted(pin.kind for pin in _learned(pins)) == ["useful", "wrong"]
    assert lifts(pins, WORDS) == {}

    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": document})
    assert _learned(_ask(api, signed)[1]) == []  # in Trash: nothing
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE documents SET deleted_at = NULL WHERE id = :d"), {"d": document})
        conn.execute(text("UPDATE feedback SET created_at = now() - interval '91 days'"))
    assert _learned(_ask(api, signed)[1]) == []  # older than 90 days: nothing


def test_the_admin_sees_what_kaagaz_learned_and_remove_stops_it_audited(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    ravi, asha = api.sign_in(admin.customer_id), api.sign_in(admin.customer_id, "reviewer")
    document = _cited(api, admin.customer_id)
    _rate(api, ravi, {"rating": "up"})
    _rate(api, asha, {"rating": "up"})

    [item] = admin.client.get("/api/admin/learned").json()["items"]
    assert (item["document_id"], item["document_name"], item["people"]) == (document, "march-bill.pdf", 2)
    assert set(item) == {"document_id", "document_name", "people", "last_marked_at"}  # no question, no name
    for signed in (ravi, asha):
        assert signed.client.get("/api/admin/learned").status_code == 403
        assert signed.client.delete(f"/api/admin/learned/{document}", headers=CSRF).status_code == 403

    assert admin.client.delete(f"/api/admin/learned/{document}", headers=CSRF).status_code == 204
    assert admin.client.get("/api/admin/learned").json()["items"] == []
    assert _learned(_ask(api, ravi)[1]) == []
    removed = [row for row in api.audit_rows(admin.customer_id) if row[0] == "learning.remove"]
    assert removed == [("learning.remove", "document", document, {})]
    _rate(api, ravi, {"rating": "up"})  # a Useful after the Remove teaches again
    assert [pin.own for pin in _learned(_ask(api, ravi)[1])] == [True]


def test_a_wrong_documents_stop_never_falls_off_the_cap_and_the_cap_is_logged(
    db: Database, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(chat_sql, "LEARN_LIMIT", 2)
    api = Api(db)
    admin = api.new_admin()
    a, b, c, d, asker = (api.sign_in(admin.customer_id) for _ in range(5))
    x = _cited(api, admin.customer_id)
    cites_x = api.answers.reply
    _rate(api, a, {"rating": "down", "reason_codes": ["wrong_documents"], "notice_shown": True})
    _cited(api, admin.customer_id)
    for _ in range(3):
        _rate(api, d, {"rating": "up"})  # newer marks on another document
    api.answers.reply = cites_x
    _rate(api, b, {"rating": "up"})
    _rate(api, c, {"rating": "up"})

    caplog.set_level(logging.WARNING, logger="kaagaz.db.chat")
    caplog.clear()
    _, pins = _ask(api, asker)
    assert sorted(pin.kind for pin in _learned(pins) if pin.document_id == x) == ["useful", "useful", "wrong"]
    assert x not in lifts(pins, WORDS)  # A's stop still holds for everyone
    assert [r.getMessage() for r in caplog.records if r.name == "kaagaz.db.chat"] == ["learned marks capped"]


def test_the_learning_and_clarify_switches_off_reach_the_answer(db: Database) -> None:
    """PRD 6 integration (0052): ``nlq_learning`` off reads no learned marks; ``nlq_clarify`` off
    asks the answer for no plan; one switch statement either way."""
    api = Api(db)
    admin = api.new_admin()
    ravi = api.sign_in(admin.customer_id)
    _cited(api, admin.customer_id)
    _rate(api, ravi, {"rating": "up"})
    with switched_off(db, "nlq_learning"), switched_off(db, "nlq_clarify"):
        _, pins = _ask(api, ravi)
    assert (_learned(pins), api.answers.clarifies[-1]) == ([], False)
    _, pins = _ask(api, ravi)
    assert (len(_learned(pins)), api.answers.clarifies[-1]) == (1, True)
