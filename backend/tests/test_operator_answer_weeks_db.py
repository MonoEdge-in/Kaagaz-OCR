"""The operator's new counts (PRD 6 F3; 0051 ``kaagaz_op_answer_weeks``): per organisation
and fixed ISO week that has ended, replies, answered, not found, clarify, rated and Useful;
no hidden count can be had by subtraction (each part 0 or at least K); a clarify is never answered
or not found and its rating never counts; no sliding window, no text."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import DBAPIError

from kaagaz.api.routers.operator_insights import ANSWER_WEEK_KEYS
from kaagaz.db import operator_insights as insights
from kaagaz.db.engine import Database
from kaagaz.db.sqlstate import sqlstate
from tests.db_support import new_customer
from tests.test_api_support import Api
from tests.test_operator_auth_db import _DISABLED
from tests.test_operator_insights_db import answers, at, feedback, of, operator, read, window_day
from tests.test_operator_orgs_db import _signed_in

pytestmark = pytest.mark.db

K = 10


def test_counts_per_organisation_and_week_and_none_hidden_comes_out_by_subtraction(db: Database) -> None:
    op, day = operator(db), window_day()
    monday = day - timedelta(days=day.weekday())
    (a, a_user), (b, b_user) = new_customer(db), new_customer(db)
    answered = answers(db, a, a_user, at(monday), 2 * K)
    answers(db, a, a_user, at(monday + timedelta(days=6), 20), K, status="not_found")  # Sunday night
    for message in answers(db, a, a_user, at(monday + timedelta(days=3)), K, status="clarify"):
        feedback(db, a, a_user, message, "up")  # a rating on a clarify never counts
    for n, message in enumerate(answered):
        feedback(db, a, a_user, message, "up" if n < K else "down")
    for n, message in enumerate(answers(db, b, b_user, at(monday), 2 * K)):
        feedback(db, b, b_user, message, "up" if n < 15 else "down")
    answers(db, b, b_user, at(monday), K, status="not_found")
    answers(db, b, b_user, at(monday), 3, status="clarify")
    answers(db, a, a_user, at(monday + timedelta(days=7)), 1)  # the next week: its own row

    rows = read(db, op, insights.answer_weeks, monday, 1, "UTC")
    [mine] = of(rows, a)
    assert mine["week"] == monday
    assert [mine[k] for k in ("replies", "answered", "not_found", "clarify", "rated", "useful")] == [
        4 * K, 2 * K, K, K, 2 * K, K,
    ]  # fmt: skip
    [theirs] = of(rows, b)
    # 33 replies: 20 answered, 10 not found, 3 clarify. Showing the 20 and the 10 would give the
    # 3 by subtraction, so all three are hidden; 15 Useful of 20 rated leaves 5, so it is hidden.
    assert [theirs[k] for k in ("replies", "answered", "not_found", "clarify", "rated", "useful")] == [
        33, None, None, None, 2 * K, None,
    ]  # fmt: skip
    two = read(db, op, insights.answer_weeks, monday, 2, "UTC")
    assert [row["week"] for row in of(two, a)] == [monday, monday + timedelta(days=7)]


def test_only_whole_weeks_that_have_ended_from_a_monday_and_a_known_zone(db: Database) -> None:
    op, day = operator(db), window_day()
    monday = day - timedelta(days=day.weekday())
    today = datetime.now(UTC).date()
    this_monday = today - timedelta(days=today.weekday())
    for args in (
        (monday + timedelta(days=1), 1, "UTC"),
        (monday, 0, "UTC"),
        (monday, 54, "UTC"),
        (this_monday, 1, "UTC"),  # this week has not ended: it would be a growing window
        (monday, 1, "Mars/Olympus_Mons"),
    ):
        with pytest.raises(DBAPIError) as error:
            read(db, op, insights.answer_weeks, *args)
        assert sqlstate(error.value) == "23514", args  # check_violation


def test_the_route_sends_counts_only_and_a_disabled_operator_gets_401(db: Database) -> None:
    api = Api(db)
    customer, user = new_customer(db)
    monday = api.clock().date() - timedelta(days=api.clock().date().weekday() + 7)
    answers(db, customer, user, at(monday), 1)
    operator, client = _signed_in(api)
    body = client.get("/api/operator/answer-weeks", params={"weeks": 1}).json()
    assert body["from"] == monday.isoformat() and body["to"] == (monday + timedelta(days=6)).isoformat()
    [row] = [row for row in body["organisations"] if row["organisation_id"] == customer]
    assert set(row) == ANSWER_WEEK_KEYS and row["replies"] == 1 and row["answered"] is None
    assert client.get("/api/operator/answer-weeks", params={"weeks": 13}).status_code == 400
    operator.sql(_DISABLED, d=True)
    assert client.get("/api/operator/answer-weeks").status_code == 401
