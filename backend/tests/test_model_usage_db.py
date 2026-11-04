"""The AI spend ledger against PostgreSQL (model choice, council 11 Oct 2026, SP1, SP2, SP4).

``insert_usage`` keeps OpenRouter's cost, else prices the tokens from the catalogue
(estimated), else leaves it unknown; the ledger is append-only for the app; the admin's
report counts whole days in TIMEZONE with both ends included, groups by user (with names),
model (with catalogue names), kind, day and document (a deleted one named "Deleted
document", chat and the missing-vector pass in no document), and filters by user; the CSV
rows are safe to open in a spreadsheet; a period's spend (for the operator's budget) counts
known costs only, its end left out. Who a scan is billed to: the uploader, who asked to
read it again, or no one for the upgrade sweep (``kaagaz.db.documents``). Customer
separation: test_isolation_model_usage.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.documents import PgDocuments, delete_forever, mark_for_reprocess, mark_for_upgrade
from kaagaz.db.engine import Database
from kaagaz.db.listing import usage_rows
from kaagaz.db.usage import DELETED_DOCUMENT, UsageRow, insert_usage, month_spend, usage_report
from kaagaz.ingestion.ports import DocumentRecord
from tests.db_support import insert_document, new_customer, violation
from tests.fakes import MemoryStorage
from tests.test_migration_0023_db import add_usage, as_owner, catalog_model, new_chat
from tests.test_model_usage import period

pytestmark = pytest.mark.db

TZ = "Asia/Kolkata"  # UTC+05:30, no summer time
UNLISTED = "google/not-in-the-catalogue"
# Around the period 2 to 3 Oct 2026 in TZ.
OCT_1_LATE = datetime(2026, 10, 1, 18, 29, tzinfo=UTC)  # 1 Oct 23:59 in TZ: before
OCT_2_EARLY = datetime(2026, 10, 1, 18, 31, tzinfo=UTC)  # 2 Oct 00:01 in TZ: the first day
OCT_3 = datetime(2026, 10, 3, 6, 0, tzinfo=UTC)
OCT_4_EARLY = datetime(2026, 10, 3, 18, 31, tzinfo=UTC)  # 4 Oct 00:01 in TZ: after
_MEMBER = text(
    "INSERT INTO users (customer_id, email, display_name, role, password_hash)"
    " VALUES (:c, :e, :n, 'member', 'not-a-real-hash') RETURNING id"
)


def _member(db: Database, customer: str, name: str) -> str:
    with db.customer(customer) as conn:
        params = {"c": customer, "e": f"m-{uuid.uuid4().hex}@example.test", "n": name}
        return str(conn.execute(_MEMBER, params).scalar_one())


def _days(first: date, last: date) -> dict[str, Any]:
    """The report's period: whole days in TZ, both included, as the route gives it."""
    start, end = period(first, last)
    return {"start": start, "end": end, "timezone": TZ}


def _approx(report: dict[str, object], key: str) -> list[tuple[object, ...]]:
    """(name, calls, cost) of each group of ``key``, for comparing with pytest.approx."""
    rows = report[key]
    assert isinstance(rows, list)
    name = {"by_user": "user_name", "by_model": "model_name", "by_kind": "kind", "by_day": "day"}.get(
        key, "name"
    )
    return [(row[name], row["calls"], row["cost_usd"]) for row in rows]


def test_the_cost_is_the_billed_one_else_the_catalogue_price_else_not_known(db: Database) -> None:
    customer, admin = new_customer(db)
    model = catalog_model(db, price_in=2, price_out=12)
    rows = [
        UsageRow(
            customer, admin, None, None, "scan", model, "Google", False, 3, 1_000_000, 500_000, 1000, None
        ),
        UsageRow(customer, admin, None, None, "card", model, None, False, 1, 10, 10, 0, 0.5),
        UsageRow(customer, None, None, None, "embed", UNLISTED, None, False, 1, 10, 0, 0, None),
        UsageRow(customer, admin, None, None, "scan", model, None, True, 4, 0, 0, 0, 0.0),
    ]
    with db.customer(customer) as conn:
        insert_usage(conn, rows)
        insert_usage(conn, [])  # nothing to do
        saved = conn.execute(
            text("SELECT kind, cached, calls, cost_usd, cost_estimated FROM model_usage ORDER BY id")
        ).all()
    assert [
        (kind, cached, calls, None if cost is None else float(cost), estimated)
        for kind, cached, calls, cost, estimated in saved
    ] == [
        ("scan", False, 3, 8.0, True),  # 1M in at $2 + 0.5M out at $12, per million
        ("card", False, 1, 0.5, False),
        ("embed", False, 1, None, False),  # neither billed nor in the catalogue
        ("scan", True, 4, 0.0, False),  # cached pages cost nothing
    ]


def test_the_ledger_is_append_only_for_the_app(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        row = add_usage(conn, customer)
    for statement, params in (
        ("UPDATE model_usage SET cost_usd = 0 WHERE id = :i", {"i": row}),
        ("DELETE FROM model_usage WHERE id = :i", {"i": row}),
        ("TRUNCATE model_usage", {}),
    ):
        with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
            conn.execute(text(statement), params)
        assert violation(refused.value)[0] == "42501", statement  # insufficient_privilege
    assert as_owner(db, "SELECT count(*) FROM model_usage WHERE id = :i", {"i": row}) == [(1,)]


def _spend(db: Database) -> dict[str, str]:
    """An organisation with spend around 2 to 3 Oct 2026 (TZ). Returns its ids by name."""
    customer, admin = new_customer(db)
    member = _member(db, customer, "Asha")
    model = catalog_model(db, name="Gemini test")
    with db.customer(customer) as conn:
        invoice = insert_document(conn, customer, display_name="invoice.pdf", uploaded_by=member)
        trashed = insert_document(
            conn, customer, display_name="trashed.pdf", deleted_at=OCT_3, deleted_by=admin, uploaded_by=admin
        )
        gone = insert_document(conn, customer, display_name="gone.pdf")
        chat = new_chat(conn, customer, admin)
        scan = {"user_id": member, "document_id": invoice, "model": model}
        add_usage(
            conn,
            customer,
            **scan,
            calls=2,
            input_tokens=1000,
            output_tokens=500,
            reasoning_tokens=100,
            cost_usd=0.30,
            created_at=OCT_2_EARLY,
        )
        add_usage(
            conn,
            customer,
            **scan,
            cached=True,
            calls=3,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0,
            created_at=OCT_3,
        )
        add_usage(
            conn,
            customer,
            user_id=admin,
            document_id=trashed,
            model=model,
            kind="card",
            cost_usd=0.10,
            created_at=OCT_3,
        )
        add_usage(
            conn, customer, document_id=gone, model=model, kind="upgrade", cost_usd=0.20, created_at=OCT_3
        )
        add_usage(
            conn,
            customer,
            user_id=admin,
            conversation_id=chat,
            model=UNLISTED,
            kind="chat",
            cost_usd=None,
            created_at=OCT_3,
        )
        add_usage(
            conn, customer, model=UNLISTED, kind="embed", cost_usd=0.05, created_at=OCT_3
        )  # no document
        for outside in (OCT_1_LATE, OCT_4_EARLY):
            add_usage(conn, customer, **scan, cost_usd=9.99, created_at=outside)
    with db.customer(customer) as conn:
        delete_forever(conn, MemoryStorage(), customer, gone)
    return {"customer": customer, "admin": admin, "member": member, "model": model, "invoice": invoice}


def test_the_report_counts_whole_days_in_timezone_and_groups_six_ways(db: Database) -> None:
    ids = _spend(db)
    with db.customer(ids["customer"]) as conn:
        report = usage_report(conn, ids["customer"], **_days(date(2026, 10, 2), date(2026, 10, 3)))

    assert set(report) == {"totals", "by_user", "by_model", "by_kind", "by_day", "top_documents"}
    assert report["totals"] == {
        "calls": 9,
        "cached_calls": 3,
        "input_tokens": 1000 + 4 * 100,
        "output_tokens": 500 + 4 * 50,
        "reasoning_tokens": 100,
        "cost_usd": pytest.approx(0.65),  # the chat's unknown cost is not counted
        "cost_estimated": False,
        "cost_part_unknown": True,  # and the total says so (R9)
    }
    assert _approx(report, "by_user") == [
        ("Asha", 5, pytest.approx(0.30)),
        (None, 2, pytest.approx(0.25)),  # the upgrade sweep and the missing-vector pass
        ("Admin", 2, pytest.approx(0.10)),
    ]
    assert [row["user_id"] for row in report["by_user"]] == [ids["member"], None, ids["admin"]]
    assert _approx(report, "by_model") == [
        ("Gemini test", 7, pytest.approx(0.60)),
        (UNLISTED, 2, pytest.approx(0.05)),  # not in the catalogue: its id
    ]
    assert _approx(report, "by_kind") == [
        ("scan", 5, pytest.approx(0.30)),
        ("upgrade", 1, pytest.approx(0.20)),
        ("card", 1, pytest.approx(0.10)),
        ("embed", 1, pytest.approx(0.05)),
        ("chat", 1, None),  # unknown cost, last
    ]
    assert _approx(report, "by_day") == [
        ("2026-10-02", 2, pytest.approx(0.30)),
        ("2026-10-03", 7, pytest.approx(0.35)),
    ]
    assert _approx(report, "top_documents") == [
        ("invoice.pdf", 5, pytest.approx(0.30)),
        (DELETED_DOCUMENT, 1, pytest.approx(0.20)),
        ("trashed.pdf", 1, pytest.approx(0.10)),  # in Trash: still named, the money was spent
    ]
    assert report["top_documents"][1]["document_id"] is None


def test_the_report_of_one_user_and_of_a_quiet_period(db: Database) -> None:
    ids = _spend(db)
    customer = ids["customer"]
    with db.customer(customer) as conn:
        mine = usage_report(
            conn, customer, **_days(date(2026, 10, 1), date(2026, 10, 4)), user_id=ids["member"]
        )
        quiet = usage_report(conn, customer, **_days(date(2026, 9, 1), date(2026, 9, 30)))
    assert _approx(mine, "by_user") == [("Asha", 7, pytest.approx(0.30 + 2 * 9.99))]
    assert _approx(mine, "top_documents") == [("invoice.pdf", 7, pytest.approx(0.30 + 2 * 9.99))]
    assert quiet["totals"] == {
        "calls": 0,
        "cached_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": 0.0,
        "cost_estimated": False,
        "cost_part_unknown": False,
    }
    assert [quiet[key] for key in ("by_user", "by_model", "by_kind", "by_day", "top_documents")] == [[]] * 5


def test_the_csv_rows_are_per_day_user_model_and_kind(db: Database) -> None:
    """The query the CSV route runs (``listing.usage_rows``); its cells are made safe by
    the route's ``csv_cell`` (test_model_usage, and the route's own test)."""
    customer, _ = new_customer(db)
    member = _member(db, customer, "=HYPERLINK(1)")
    model = catalog_model(db, name="+Gemini")
    with db.customer(customer) as conn:
        add_usage(conn, customer, user_id=member, model=model, cost_usd=0.25, created_at=OCT_3)
        add_usage(conn, customer, model=UNLISTED, kind="embed", cost_usd=None, created_at=OCT_3)
        add_usage(conn, customer, model=UNLISTED, kind="embed", cost_usd=None, created_at=OCT_4_EARLY)
        rows = usage_rows(conn, customer, **_days(date(2026, 10, 3), date(2026, 10, 3)), user_id=None)
    assert [
        (row["day"].isoformat(), row["user_name"], row["model"], row["kind"], row["calls"], row["tokens"],
         None if row["cost_usd"] is None else float(row["cost_usd"]), row["cost_estimated"])
        for row in rows
    ] == [
        ("2026-10-03", None, UNLISTED, "embed", 1, 150, None, False),
        ("2026-10-03", "=HYPERLINK(1)", model, "scan", 1, 150, 0.25, False),
    ]  # fmt: skip


def test_the_spend_of_a_period_counts_known_costs_only(db: Database) -> None:
    customer, _ = new_customer(db)
    now = datetime.now(UTC)
    with db.customer(customer) as conn:
        nothing = month_spend(conn, customer, start=now - timedelta(days=1), end=now + timedelta(days=1))
        add_usage(conn, customer, cost_usd=2.5)
        add_usage(conn, customer, cost_usd=None)
        add_usage(conn, customer, cost_usd=100, created_at=now - timedelta(days=40))
        spent = month_spend(conn, customer, start=now - timedelta(days=1), end=now + timedelta(days=1))
        ending = month_spend(conn, customer, start=now - timedelta(days=50), end=now - timedelta(days=40))
    assert (nothing, spent) == (0.0, 2.5)
    assert ending == 0.0  # the end is not included


# --- who a scan is billed to (kaagaz.db.documents) ---------------------------------------

_BILLED = text("SELECT CAST(read_requested_by AS text), read_model FROM documents WHERE id = :d")
_READY = text("UPDATE documents SET status = 'ready' WHERE id = :d")


def _record(customer: str, user: str) -> DocumentRecord:
    return DocumentRecord(
        customer_id=customer,
        document_id=str(uuid.uuid4()),
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        size=10,
        detected_type="pdf",
        uploaded_by=user,
    )


def test_an_upload_and_a_new_version_are_billed_to_their_uploader(db: Database) -> None:
    customer, admin = new_customer(db)
    member = _member(db, customer, "Asha")
    documents = PgDocuments(db)
    first, added = documents.add_if_absent(_record(customer, admin))
    assert added
    version = documents.add_version_if_absent(_record(customer, member), of_document_id=first.document_id)
    assert version is not None
    with db.customer(customer) as conn:
        billed = [
            tuple(conn.execute(_BILLED, {"d": d}).one()) for d in (first.document_id, version[0].document_id)
        ]
    assert billed == [(admin, None), (member, None)]


def test_read_again_bills_who_asked_and_keeps_the_model_unless_given_one_the_sweep_bills_no_one(
    db: Database,
) -> None:
    customer, admin = new_customer(db)
    member = _member(db, customer, "Asha")
    model = catalog_model(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=admin)
    seen = []
    for ask in (
        lambda conn: mark_for_reprocess(
            conn, customer, document, per_hour=100, requested_by=member, read_model=model
        ),
        lambda conn: mark_for_reprocess(conn, customer, document, per_hour=100, requested_by=admin),
        lambda conn: mark_for_upgrade(conn, customer, document),
    ):
        with db.customer(customer) as conn:
            conn.execute(_READY, {"d": document})
            assert ask(conn)
            seen.append(tuple(conn.execute(_BILLED, {"d": document}).one()))
    assert seen == [(member, model), (admin, model), (None, model)]


def test_an_ultra_low_cost_read_of_a_document_deleted_for_good_counts_as_the_deleted_document(
    db: Database,
) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        add_usage(conn, customer, kind="ulc_scan", calls=3, cost_usd=0.009, created_at=OCT_3)
        report = usage_report(conn, customer, **_days(date(2026, 10, 2), date(2026, 10, 3)))
    assert _approx(report, "by_kind") == [("ulc_scan", 3, pytest.approx(0.009))]
    assert _approx(report, "top_documents") == [(DELETED_DOCUMENT, 3, pytest.approx(0.009))]
