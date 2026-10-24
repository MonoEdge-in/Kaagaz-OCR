"""Operator insights across organisations (council 11 Oct 2026, F1-F4; 0024), against
PostgreSQL: each organisation's rows hold its own numbers only; nothing planted of a
person, a document, a chat or a value (names, e-mails, file names, question and answer
text, citations, feedback reasons, audit details, an identity-number-shaped value, any
id but the organisation's) ever comes out of a reader; a disabled operator and one
without a confirmed two-step secret get no rows; reading writes no platform_audit row;
and the app's role cannot run the readers at all."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import operator_insights as insights
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.db.sqlstate import sqlstate
from tests.db_support import new_customer
from tests.test_api_support import Api
from tests.test_operator_auth_db import _DISABLED, _Operator, _owner
from tests.test_operator_insights_db import (
    answers,
    at,
    audit,
    document,
    feedback,
    new_model,
    of,
    one,
    page,
    read,
    run,
    usage,
    window_day,
)

pytestmark = pytest.mark.db

SECRETS = (
    "Sentinel Person",
    "secret-ledger.pdf",
    "secret question text",
    "secret answer sentence",
    "secret-snippet",
    "secret-reason",
    "secret-details",
    "ABCDE1234F",  # made up; the 0007 comment test's own sample
)
_PLATFORM_ROWS = "SELECT count(*) FROM platform_audit WHERE operator_id = :o"


def _plant(db: Database, customer: str, user: str, day: date, size: int, model: str) -> list[str]:
    """``size`` of everything in one organisation on ``day``; the ids that must never show."""
    when = at(day)
    document_id = document(db, customer, when, display_name="secret-ledger.pdf", uploaded_by=user)
    page(db, customer, document_id, model, ["readers_disagree"])
    for _ in range(size):
        run(db, customer, document_id, when, config={"read_model": model, "seconds_reading": 1.0}, ai=1)
    usage(db, customer, when, "scan", model, size, "0.25", document_id=document_id)
    secret = {"note": "secret-details", "document_id": document_id}
    for action in ("document.upload", "auth.login", "chat.ask"):
        audit(db, customer, action, when, count=size, actor=user, details=secret)
    audit(
        db, customer, "review.resolve", when, count=size, actor=user, details={**secret, "choice": "second"}
    )
    answer = {
        "status": "answered",
        "label": "verified",
        "route": "search",
        "sentences": [{"text": "secret answer sentence", "citations": [1]}],
        "citations": [{"document_id": document_id, "page": 1, "snippet": "secret-snippet"}],
    }
    asked = answers(db, customer, user, when, size, text_value="secret answer sentence", answer=answer)
    _owner(
        db,
        "INSERT INTO messages (customer_id, conversation_id, role, text, created_at)"
        " SELECT customer_id, conversation_id, 'user', 'secret question text', created_at FROM messages"
        " WHERE id = :m",
        {"m": asked[0]},
    )
    feedback(db, customer, user, asked[0], "down", "secret-reason")
    Api(db).add_value(
        customer, document_id, field_name="pan", value="ABCDE1234F", raw="ABCDE1234F", second=None
    )
    _owner(db, "UPDATE users SET display_name = 'Sentinel Person' WHERE id = :u", {"u": user})
    [(email,)] = _owner(db, "SELECT email FROM users WHERE id = :u", {"u": user})
    conversation = _owner(db, "SELECT conversation_id FROM messages WHERE id = :m", {"m": asked[0]})[0][0]
    return [user, str(email), document_id, str(conversation), *asked]


def _everything(db: Database, operator_id: str, day: date) -> dict[str, list[dict[str, Any]]]:
    return {
        "daily": read(db, operator_id, insights.daily, day, day, "UTC"),
        "health": read(db, operator_id, insights.org_health, day, "UTC"),
        "quality": read(db, operator_id, insights.quality, day, day, "UTC"),
        "board": read(db, operator_id, insights.model_board, day, day, "UTC"),
    }


def test_each_organisation_gets_its_own_numbers_and_nothing_planted_comes_out(db: Database) -> None:
    api = Api(db)
    operator, day = _Operator(api), window_day()
    (a, a_user), (b, b_user) = (
        new_customer(db, name="Org A insights"),
        new_customer(db, name="Org B insights"),
    )
    model = new_model()
    hidden = _plant(db, a, a_user, day, 2, model) + _plant(db, b, b_user, day, 3, model)
    before = _owner(db, _PLATFORM_ROWS, {"o": operator.id})

    rows = _everything(db, operator.id, day)

    for customer, size in ((a, 2), (b, 3)):
        [daily] = of(rows["daily"], customer)
        counts = ("uploads", "sign_ins", "questions", "corrections", "runs", "scan_pages")
        assert [daily[key] for key in counts] == [size] * len(counts), customer
        # Changed on purpose (24 Oct 2026): _plant writes one model_usage row (size calls, 0.25 in
        # all) and cost_usd sums the rows' cost, so it is 0.25, not size * 0.25; both rows is 0.50.
        assert daily["cost_usd"] == Decimal("0.25")
        health = one(rows["health"], "customer_id", customer)
        assert (health["active_users_7d"], health["answers_30d"], health["open_review"]) == (1, size, 1)
        quality = one(rows["quality"], "customer_id", customer)
        assert (quality["runs"], quality["answers"], quality["feedback"], quality["corrections"]) == (
            size,
            size,
            1,
            size,
        )
    [board] = [row for row in rows["board"] if row["model"] == model]
    assert (board["runs"], board["scan_calls"], board["ocr_pages"]) == (5, 5, 2)  # both, no organisation

    every_value = repr(rows)
    for secret in (*SECRETS, *hidden):
        assert secret not in every_value, secret
    assert _owner(db, _PLATFORM_ROWS, {"o": operator.id}) == before  # reads are never audited


def test_a_disabled_or_unconfirmed_operator_gets_no_rows(db: Database) -> None:
    api = Api(db)
    day = window_day()
    customer, user = new_customer(db)
    _plant(db, customer, user, day, 1, new_model())
    enabled, disabled, unconfirmed = _Operator(api), _Operator(api), _Operator(api, confirmed=False)
    disabled.sql(_DISABLED, d=True)

    assert all(_everything(db, enabled.id, day).values())
    for refused in (disabled, unconfirmed):
        assert _everything(db, refused.id, day) == {"daily": [], "health": [], "quality": [], "board": []}


def test_the_app_role_cannot_run_the_readers(db: Database) -> None:
    day = window_day()
    for call in (
        "SELECT * FROM kaagaz_op_daily(gen_random_uuid(), :d, :d, 'UTC')",
        "SELECT * FROM kaagaz_op_org_health(gen_random_uuid(), :d, 'UTC')",
        "SELECT * FROM kaagaz_op_quality(gen_random_uuid(), :d, :d, 'UTC')",
        "SELECT * FROM kaagaz_op_model_board(gen_random_uuid(), :d, :d, 'UTC')",
    ):
        with pytest.raises(DBAPIError) as error, db.system() as conn:
            conn.execute(text(call), {"d": day})
        assert sqlstate(error.value) == "42501", call  # insufficient privilege
    with OperatorDatabase(db.engine).operator() as conn:  # the operator's role may
        assert list(insights.quality(conn, "00000000-0000-0000-0000-000000000000", day, day, "UTC")) == []
