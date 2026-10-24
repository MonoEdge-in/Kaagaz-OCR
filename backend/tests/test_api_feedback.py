"""Useful and Not useful (PRD 6 F1): the reasons, the notice, the per-person limit, and the
typed reason kept away from the model, the audit log and the logs.

The body's rules are tested first, without a database.
"""

import importlib.util
import logging
import typing
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from kaagaz.api.routers import chat as chat_router
from kaagaz.api.routers.chat import FEEDBACK_PER_MINUTE, TOO_MANY_RATINGS, FeedbackBody
from kaagaz.db import chat as chat_sql
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed

MARKER = "zebra-crossing-7731"  # a reason no answer, question or log line could hold by chance


def _migration() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0050_feedback_reasons.py"
    spec = importlib.util.spec_from_file_location("migration_0050", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_the_reasons_are_one_list_in_the_route_the_sql_and_the_migration() -> None:
    migration = _migration()
    assert typing.get_args(chat_router.ReasonCode) == chat_sql.REASONS == migration.REASONS
    assert chat_sql.REASONS == (
        "wrong_documents",
        "document_missing",
        "wrong_figure",
        "did_not_understand",
        "other",
    )


def test_a_reason_goes_with_not_useful_only_and_only_a_known_one() -> None:
    assert FeedbackBody(rating="down", reason_codes=["wrong_figure"], notice_shown=True).notice_shown
    assert FeedbackBody(rating="up").reason_codes == []
    for bad in (
        {"rating": "up", "reason": "nice"},
        {"rating": "up", "reason_codes": ["other"]},
        {"rating": "down", "reason_codes": ["made_up"]},
        {"rating": "down", "reason_codes": ["other"] * 6},
        {"rating": "down", "reason": "x" * 501},
        {"rating": "down", "handled": True},
    ):
        with pytest.raises(ValidationError):
            FeedbackBody.model_validate(bad)


def _asked(signed: Signed) -> tuple[str, str]:
    body = signed.client.post("/api/chat", json={"question": "what is the total"}, headers=CSRF).json()
    return body["conversation_id"], body["answer"]["id"]


def _row(db: Database, customer_id: str) -> tuple[object, ...]:
    with db.customer(customer_id) as conn:
        return tuple(
            conn.execute(
                text(
                    "SELECT rating, reason, reason_codes, notice_shown, handled_at, handled_by FROM feedback"
                )
            ).one()
        )


@pytest.mark.db
def test_not_useful_keeps_its_reasons_in_order_and_the_notice(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _, answer_id = _asked(signed)
    reply = signed.client.post(
        f"/api/messages/{answer_id}/feedback",
        json={
            "rating": "down",
            "reason_codes": ["other", "wrong_documents", "other"],
            "reason": "  the March bill  ",
            "notice_shown": True,
        },
        headers=CSRF,
    )
    assert reply.status_code == 204, reply.text
    assert _row(db, signed.customer_id) == (
        "down",
        "the March bill",
        ["wrong_documents", "other"],
        True,
        None,
        None,
    )
    # Useful again replaces it all: no reason, no codes, no notice.
    up = signed.client.post(f"/api/messages/{answer_id}/feedback", json={"rating": "up"}, headers=CSRF)
    assert up.status_code == 204
    assert _row(db, signed.customer_id) == ("up", None, [], False, None, None)
    refused = signed.client.post(
        f"/api/messages/{answer_id}/feedback", json={"rating": "up", "reason": "great"}, headers=CSRF
    )
    assert refused.status_code == 400


@pytest.mark.db
def test_one_answer_flips_from_useful_to_not_useful_and_back_on_one_row(db: Database) -> None:
    signed = Api(db).new_signed()
    conversation_id, answer_id = _asked(signed)
    path = f"/api/messages/{answer_id}/feedback"
    not_useful = {
        "rating": "down",
        "reason_codes": ["document_missing"],
        "reason": "May",
        "notice_shown": True,
    }
    for body, row in (
        ({"rating": "up"}, ("up", None, [], False, None, None)),
        (not_useful, ("down", "May", ["document_missing"], True, None, None)),
        ({"rating": "up"}, ("up", None, [], False, None, None)),
    ):
        assert signed.client.post(path, json=body, headers=CSRF).status_code == 204
        assert _row(db, signed.customer_id) == row  # _row reads exactly one row
        shown = signed.client.get(f"/api/conversations/{conversation_id}").json()["messages"]
        assert shown[1]["feedback"] == row[0]


@pytest.mark.db
def test_an_older_screen_without_the_notice_stores_it_as_not_shown(db: Database) -> None:
    signed = Api(db).new_signed()
    _, answer_id = _asked(signed)
    reply = signed.client.post(
        f"/api/messages/{answer_id}/feedback", json={"rating": "down", "reason": "old form"}, headers=CSRF
    )
    assert reply.status_code == 204
    assert _row(db, signed.customer_id)[2:4] == ([], False)


@pytest.mark.db
def test_a_new_rating_opens_a_handled_one_again(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _, answer_id = _asked(signed)
    body = {"rating": "down", "reason_codes": ["wrong_figure"], "notice_shown": True}
    signed.client.post(f"/api/messages/{answer_id}/feedback", json=body, headers=CSRF)
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE feedback SET handled_at = now(), handled_by = :u"), {"u": signed.user_id})
    signed.client.post(f"/api/messages/{answer_id}/feedback", json=body, headers=CSRF)
    assert _row(db, signed.customer_id)[4:] == (None, None)


@pytest.mark.db
def test_one_person_may_rate_at_most_the_limit_a_minute(db: Database) -> None:
    api = Api(db)
    signed, other = api.new_signed(), api.new_signed()
    _, answer_id = _asked(signed)
    path = f"/api/messages/{answer_id}/feedback"
    for _ in range(FEEDBACK_PER_MINUTE):
        assert signed.client.post(path, json={"rating": "up"}, headers=CSRF).status_code == 204
    refused = signed.client.post(path, json={"rating": "up"}, headers=CSRF)
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"
    assert refused.json()["error"]["message"] == TOO_MANY_RATINGS
    # Someone else is not held back by it.
    _, theirs = _asked(other)
    assert (
        other.client.post(f"/api/messages/{theirs}/feedback", json={"rating": "up"}, headers=CSRF).status_code
        == 204
    )


@pytest.mark.db
def test_the_typed_reason_never_reaches_the_model_the_audit_log_or_a_log_line(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    api = Api(db)
    signed = api.new_signed()
    conversation_id, answer_id = _asked(signed)
    caplog.set_level(logging.DEBUG)
    body = {"rating": "down", "reason": MARKER, "reason_codes": ["other"], "notice_shown": True}
    assert (
        signed.client.post(f"/api/messages/{answer_id}/feedback", json=body, headers=CSRF).status_code == 204
    )
    follow_up = {"question": "and the next one", "conversation_id": conversation_id}
    assert signed.client.post("/api/chat", json=follow_up, headers=CSRF).status_code == 200

    sent_to_model = repr(
        (api.answers.asked, api.answers.histories, api.answers.memories, api.answers.summarised)
    )
    assert MARKER not in sent_to_model
    assert len(api.answers.histories) == 2 and api.answers.histories[1]  # the follow-up had its history
    assert all(MARKER not in repr(row) for row in api.audit_rows(signed.customer_id))
    assert all(
        MARKER not in record.getMessage() and MARKER not in repr(record.__dict__) for record in caplog.records
    )
