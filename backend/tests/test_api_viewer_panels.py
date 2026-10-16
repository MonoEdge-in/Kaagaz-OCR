"""The server side of the viewer panels and the failed jobs tab (PRD 3 Y2, Y5, Z1),
against a real database: the detail's ``links``, the review queue's ``reason``
filter and ``check_reasons``, and ``GET /api/admin/failed`` with Retry."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api.views import status_message
from kaagaz.db.engine import Database
from tests.test_api_documents import _statements
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db

_LINK = text(
    "INSERT INTO document_links (customer_id, document_id, linked_document_id, identifier_kind,"
    " identifier_value) VALUES (:c, :a, :b, :k, :v)"
)
_INVOICE = text(
    "INSERT INTO invoices (customer_id, document_id, label, check_status, check_reasons)"
    " VALUES (:c, :d, 'needs_review', :s, CAST(:r AS text[]))"
)
_RUN = text(
    "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status)"
    " VALUES (:c, :d, :at, :at, 'failed')"
)
REASONS = (
    "check_failed",
    "readers_disagree",
    "low_confidence",
    "single_reader",
    "unsure_text",  # PRD 5 U6: the reading model's marks
    "box_unanchored",
    "local_reader",
    "ocr_script",
)
FAILED_FIELDS = {"id", "display_name", "status", "failure_code", "message", "last_run_at"}


def link(
    db: Database, customer_id: str, a: str, b: str, kind: str = "invoice_number", value: str = "inv-1"
) -> None:
    """One stored link row, a to b (the pipeline stores both ways; a test may store one)."""
    with db.customer(customer_id) as conn:
        conn.execute(_LINK, {"c": customer_id, "a": a, "b": b, "k": kind, "v": value})


def links_of(signed: Signed, document_id: str) -> list[dict[str, Any]]:
    reply = signed.client.get(f"/api/documents/{document_id}")
    assert reply.status_code == 200, reply.text
    links: list[dict[str, Any]] = reply.json()["links"]
    return links


def test_links_come_from_both_directions_once_each_with_the_shared_identifier(db: Database) -> None:
    api = Api(db)
    user = api.new_signed()
    c = user.customer_id
    bill, order, challan = (
        api.add_document(c, name=name) for name in ("bill.pdf", "order.pdf", "challan.pdf")
    )
    with db.customer(c) as conn:
        conn.execute(text("UPDATE documents SET doc_type = 'invoice' WHERE id = :d"), {"d": order})
    link(db, c, bill, order)  # stored both ways, as the pipeline does
    link(db, c, order, bill)
    link(db, c, challan, bill, "challan_number", "ch-9")  # only the other way

    assert links_of(user, bill) == [
        {
            "id": challan,
            "display_name": "challan.pdf",
            "doc_type": None,
            "identifier": {"kind": "challan_number", "value": "ch-9"},
        },
        {
            "id": order,
            "display_name": "order.pdf",
            "doc_type": "invoice",
            "identifier": {"kind": "invoice_number", "value": "inv-1"},
        },
    ]
    assert [each["id"] for each in links_of(user, order)] == [bill]
    assert [each["id"] for each in links_of(user, challan)] == [bill]


def test_links_to_trash_older_versions_and_unread_documents_are_hidden(db: Database) -> None:
    api = Api(db)
    user = api.new_signed()
    c = user.customer_id
    bill = api.add_document(c, name="bill.pdf")
    kept = api.add_document(c, name="kept.pdf")
    trashed = api.add_document(c, name="trashed.pdf", deleted_at=api.clock(), deleted_by=user.user_id)
    older = api.add_document(c, name="older.pdf", is_current=False)
    reading = api.add_document(c, name="reading.pdf", status="clean")
    for partner in (kept, trashed, older, reading):
        link(db, c, bill, partner)
        link(db, c, partner, bill)

    assert [each["id"] for each in links_of(user, bill)] == [kept]  # the control: a live one shows


def test_the_detail_sends_the_same_number_of_statements_for_one_link_or_many(db: Database) -> None:
    api = Api(db)
    user = api.new_signed()
    c = user.customer_id
    one, many = api.add_document(c, name="one.pdf"), api.add_document(c, name="many.pdf")
    link(db, c, one, api.add_document(c))
    for n in range(5):
        link(db, c, many, api.add_document(c), value=f"inv-{n}")

    def detail(document_id: str) -> object:
        return user.client.get(f"/api/documents/{document_id}")

    assert len(links_of(user, many)) == 5
    assert _statements(db, lambda: detail(one)) == _statements(db, lambda: detail(many))


# --- the review queue's reason filter (Y5) -------------------------------------------


def _queue(api: Api) -> tuple[Signed, dict[str, str], str]:
    """A reviewer, one needs-review value per reason (oldest first, in REASONS order),
    and the document whose invoice check failed."""
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    checked, plain = api.add_document(c, name="bill.pdf"), api.add_document(c, name="note.pdf")
    for doc in (checked, plain):
        api.add_page(c, doc)
    with api.db.customer(c) as conn:
        conn.execute(_INVOICE, {"c": c, "d": checked, "s": "failed", "r": ["lines_sum_mismatch", "bad_date"]})
    ids = {
        reason: api.add_value(c, checked if reason == "check_failed" else plain, reason=reason)
        for reason in REASONS
    }
    return reviewer, ids, checked


@pytest.mark.parametrize("reason", REASONS)
def test_the_queue_filters_by_each_reason(db: Database, reason: str) -> None:
    reviewer, ids, _ = _queue(Api(db))
    body = reviewer.client.get("/api/review", params={"reason": reason}).json()
    assert [item["value"]["id"] for item in body["items"]] == [ids[reason]]
    assert body["total"] == 1


def test_several_reasons_page_by_offset_inside_the_filter(db: Database) -> None:
    reviewer, ids, _ = _queue(Api(db))
    wanted: dict[str, list[str] | int] = {
        "reason": ["readers_disagree", "single_reader", "low_confidence"],
        "limit": 2,
    }
    first = reviewer.client.get("/api/review", params=wanted).json()
    rest = reviewer.client.get("/api/review", params={**wanted, "offset": 2}).json()
    assert [item["value"]["id"] for item in first["items"] + rest["items"]] == [
        ids["readers_disagree"],
        ids["low_confidence"],
        ids["single_reader"],
    ]
    assert first["total"] == rest["total"] == 3
    assert reviewer.client.get("/api/review").json()["total"] == len(REASONS)  # no filter: every reason
    every = reviewer.client.get("/api/review", params={"reason": list(REASONS)}).json()
    assert every["total"] == len(REASONS)  # all of them at once


@pytest.mark.parametrize("reason", ["corrected", "nope", ""])
def test_an_unknown_reason_is_400(db: Database, reason: str) -> None:
    reviewer, _, _ = _queue(Api(db))
    reply = reviewer.client.get("/api/review", params={"reason": reason})
    assert (reply.status_code, reply.json()["error"]["code"]) == (400, "bad_request")


def test_each_item_carries_its_invoice_check_reasons(db: Database) -> None:
    reviewer, ids, _ = _queue(Api(db))
    items = {item["value"]["id"]: item for item in reviewer.client.get("/api/review").json()["items"]}
    assert items[ids["check_failed"]]["check_reasons"] == ["lines_sum_mismatch", "bad_date"]
    assert items[ids["single_reader"]]["check_reasons"] == []  # no invoice row


def test_a_passed_check_gives_no_reasons(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = api.add_document(c)
    api.add_page(c, doc)
    with db.customer(c) as conn:
        conn.execute(_INVOICE, {"c": c, "d": doc, "s": "passed", "r": []})
    api.add_value(c, doc)
    (item,) = reviewer.client.get("/api/review").json()["items"]
    assert item["check_reasons"] == []


# --- the failed jobs list (Z1) ---------------------------------------------------------


def _failed_setup(api: Api) -> tuple[Signed, dict[str, str]]:
    """An admin session and one document in each state the list must and must not show."""
    admin = api.new_admin()
    c = admin.customer_id
    docs = {
        "failed": api.add_document(c, name="failed.pdf", status="failed", failure_code="retries_exhausted"),
        "needs_reader": api.add_document(c, name="odd.zip", status="needs_reader", detected_type="zip"),
        "ready": api.add_document(c, name="ready.pdf"),
        "received": api.add_document(c, name="new.pdf", status="received"),
        "trashed": api.add_document(
            c,
            name="gone.pdf",
            status="failed",
            failure_code="ocr_failed",
            deleted_at=api.clock(),
            deleted_by=admin.user_id,
        ),
        "older": api.add_document(c, name="old.pdf", status="failed", is_current=False),
    }
    return admin, docs


def test_the_failed_list_shows_current_failed_and_unreadable_documents_out_of_trash(db: Database) -> None:
    api = Api(db)
    admin, docs = _failed_setup(api)
    early = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    with db.customer(admin.customer_id) as conn:
        for at in (early, early + timedelta(hours=2)):
            conn.execute(_RUN, {"c": admin.customer_id, "d": docs["failed"], "at": at})

    body = admin.client.get("/api/admin/failed").json()

    assert body["total"] == 2
    items = {item["id"]: item for item in body["items"]}
    assert set(items) == {docs["failed"], docs["needs_reader"]}
    assert all(set(item) == FAILED_FIELDS for item in body["items"])
    failed, odd = items[docs["failed"]], items[docs["needs_reader"]]
    assert (failed["status"], failed["failure_code"]) == ("failed", "retries_exhausted")
    assert failed["message"] == status_message(
        "failed", "retries_exhausted", detected_type="pdf", max_pages=api.settings.max_pages
    )
    assert datetime.fromisoformat(failed["last_run_at"]) == early + timedelta(hours=2)
    assert odd["message"] == "Stored safely. This kind of file can't be read yet."
    assert odd["last_run_at"] is None  # never run


def test_the_failed_list_pages_by_limit_and_offset(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    for n in range(3):
        api.add_document(admin.customer_id, name=f"{n}.pdf", status="failed")
    first = admin.client.get("/api/admin/failed", params={"limit": 2}).json()
    rest = admin.client.get("/api/admin/failed", params={"limit": 2, "offset": 2}).json()
    assert (len(first["items"]), len(rest["items"]), first["total"]) == (2, 1, 3)
    assert {item["id"] for item in first["items"]}.isdisjoint(item["id"] for item in rest["items"])
    assert admin.client.get("/api/admin/failed", params={"limit": 101}).status_code == 400


@pytest.mark.parametrize("role", ["member", "reviewer"])  # an admin has no app session (0035)
def test_the_failed_list_refuses_every_app_session(db: Database, role: str) -> None:
    signed = Api(db).new_signed(role)
    reply = signed.client.get("/api/admin/failed")
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "admin_session_required")


def test_the_failed_list_needs_a_sign_in(db: Database) -> None:
    reply = Api(db).client().get("/api/admin/failed")
    assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in")


def test_retry_from_the_list_reads_it_again_and_a_stale_retry_is_409(db: Database) -> None:
    api = Api(db)
    admin, docs = _failed_setup(api)
    target = docs["failed"]

    first = admin.client.post(f"/api/documents/{target}/reprocess", headers=CSRF)
    again = admin.client.post(f"/api/documents/{target}/reprocess", headers=CSRF)  # an old list

    assert (first.status_code, first.json()["status"]) == (200, "received")
    assert (again.status_code, again.json()["error"]["code"]) == (409, "not_reprocessable")
    listed = {item["id"] for item in admin.client.get("/api/admin/failed").json()["items"]}
    assert listed == {docs["needs_reader"]}
