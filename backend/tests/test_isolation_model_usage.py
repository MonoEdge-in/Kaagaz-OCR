"""Customer separation of the AI spend ledger (model choice, council 11 Oct 2026, SP1, SP2,
SP4; 0023): customer A never reads, counts or writes customer B's spend, nor sees B's
budget, user or document names, whichever customer id A's transaction names (the
statement's filter and row level security, the two locks), and a row of A's can never
point at B's document, user or chat."""

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from kaagaz.db.listing import usage_rows
from kaagaz.db.usage import UsageRow, insert_usage, month_spend, usage_report
from tests.db_support import insert_document, new_customer, violation
from tests.test_migration_0023_db import add_usage, as_owner, new_chat
from tests.test_model_usage import period

pytestmark = pytest.mark.db

TZ = "Asia/Kolkata"
MODEL = "google/gemini-3.1-pro-preview"


def _today() -> date:
    return datetime.now(ZoneInfo(TZ)).date()


def _two_customers(db: Database) -> dict[str, str]:
    """A and B, each with a document, a chat and spend today; B with a budget."""
    ids: dict[str, str] = {}
    for label in ("a", "b"):
        customer, admin = new_customer(db, name=f"Org {label}")
        with db.customer(customer) as conn:
            document = insert_document(conn, customer, display_name=f"{label}-secret.pdf", uploaded_by=admin)
            chat = new_chat(conn, customer, admin)
            row = add_usage(
                conn, customer, user_id=admin, document_id=document, cost_usd=1 if label == "a" else 7
            )
        ids |= {
            label: customer,
            f"{label}_admin": admin,
            f"{label}_document": document,
            f"{label}_chat": chat,
        }
        ids[f"{label}_row"] = str(row)
    as_owner(db, "UPDATE customers SET ai_budget_usd = 50 WHERE id = :c", {"c": ids["b"]})
    return ids


def test_a_customer_sees_only_its_own_ledger_rows(db: Database) -> None:
    ids = _two_customers(db)
    with db.customer(ids["a"]) as conn:
        seen = [str(row[0]) for row in conn.execute(text("SELECT id FROM model_usage"))]
        by_id = conn.execute(text("SELECT count(*) FROM model_usage WHERE id = :i"), {"i": int(ids["b_row"])})
        assert by_id.scalar_one() == 0
    assert seen == [ids["a_row"]]


def test_a_customer_cannot_write_a_row_for_another_or_point_at_anothers_things(db: Database) -> None:
    ids = _two_customers(db)
    theirs = UsageRow(ids["b"], None, None, None, "scan", MODEL, None, False, 1, 1, 1, 0, 0.1)
    with pytest.raises(DBAPIError) as refused, db.customer(ids["a"]) as conn:
        insert_usage(conn, [theirs])
    assert violation(refused.value)[0] == "42501"  # the policy's WITH CHECK
    for column in ("user_id", "document_id", "conversation_id"):
        other = {
            "user_id": ids["b_admin"],
            "document_id": ids["b_document"],
            "conversation_id": ids["b_chat"],
        }
        with pytest.raises(DBAPIError) as refused, db.customer(ids["a"]) as conn:
            add_usage(conn, ids["a"], **{column: other[column]})
        assert violation(refused.value)[0] == "23503", column  # the key names the customer too
    assert as_owner(db, "SELECT count(*) FROM model_usage WHERE customer_id = :c", {"c": ids["b"]}) == [(1,)]


def test_the_report_csv_and_budget_count_only_the_current_customer(db: Database) -> None:
    ids = _two_customers(db)
    start, end = period(_today() - timedelta(days=1), _today() + timedelta(days=1))
    with db.customer(ids["a"]) as conn:
        mine = usage_report(conn, ids["a"], start=start, end=end, timezone=TZ)
        # B's id in A's transaction: row level security shows none of B's rows.
        theirs = usage_report(conn, ids["b"], start=start, end=end, timezone=TZ)
        their_user = usage_report(conn, ids["a"], start=start, end=end, timezone=TZ, user_id=ids["b_admin"])
        csv_theirs = usage_rows(conn, ids["b"], start=start, end=end, timezone=TZ, user_id=None)
        csv_their_user = usage_rows(conn, ids["a"], start=start, end=end, timezone=TZ, user_id=ids["b_admin"])
        spent_theirs = month_spend(conn, ids["b"], start=start, end=end)
        spent_mine = month_spend(conn, ids["a"], start=start, end=end)
        budget_theirs = conn.execute(
            text("SELECT ai_budget_usd FROM customers WHERE id = :b"), {"b": ids["b"]}
        )
        assert budget_theirs.all() == []
    assert (mine["totals"]["calls"], mine["totals"]["cost_usd"]) == (1, pytest.approx(1.0))
    assert [row["name"] for row in mine["top_documents"]] == ["a-secret.pdf"]
    assert "b-secret" not in str(mine)
    for empty in (theirs, their_user):
        assert empty["totals"]["calls"] == 0
        assert [empty[key] for key in ("by_user", "by_model", "by_kind", "by_day", "top_documents")] == [
            []
        ] * 5
    assert (list(csv_theirs), list(csv_their_user)) == ([], [])
    assert (spent_theirs, spent_mine) == (0.0, 1.0)


def test_a_transaction_with_no_customer_sees_no_spend(db: Database) -> None:
    """``Database.system`` sets no customer: the policy shows it no row at all."""
    _two_customers(db)
    start, end = period(_today(), _today())
    with db.system() as conn:
        assert conn.execute(text("SELECT count(*) FROM model_usage")).scalar_one() == 0
        assert (
            usage_report(conn, str(uuid.uuid4()), start=start, end=end, timezone=TZ)["totals"]["calls"] == 0
        )
