"""Every number on the admin's members table (#386, ``/admin/members``) and the operator's
organisation numbers (#387, ``/operator/org-numbers``) recounted from the raw rows.

``plant`` builds an organisation the way the product leaves it: several members, files read by
every route (the PDF's own text, an AI model, the local reader, spreadsheet cells, a blank
page, a page not read), a file in Trash, a newer version, a failed and a rejected file,
questions with a clarifying question back, verified values, sign-ins, activity and AI use,
spread over this month, the 30 days before it and long ago. A second organisation is planted
beside it. The expected numbers are then counted again here, in Python, from each table's
rows read as that organisation (customer filter and row level security), never from the
statements under test. Customer separation itself: ``test_isolation_member_numbers.py`` and
``test_isolation_org_numbers.py``."""

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.db_support import insert_document
from tests.test_api_support import NOW, Api, Signed
from tests.test_operator_orgs_db import _signed_in

pytestmark = pytest.mark.db

ZONE = ZoneInfo("Asia/Kolkata")  # settings_factory's TIMEZONE
MODEL = "google/gemini-3-flash-preview"
THIS_MONTH = NOW - timedelta(days=7)  # 20 Sep 2026: this month and the last 30 days
LAST_30_DAYS = NOW - timedelta(days=28)  # 30 Aug 2026: the last 30 days, not this month
LONG_AGO = NOW - timedelta(days=120)  # all time only

_PAGE = (
    "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, has_image, route, read_by)"
    " VALUES (:c, :d, :n, 595, 842, 'pt', true, :route, :by)"
)
_CONVERSATION = "INSERT INTO conversations (customer_id, user_id, title) VALUES (:c, :u, 'Chat') RETURNING id"
_MESSAGE = (
    "INSERT INTO messages (customer_id, conversation_id, role, text, answer, created_at)"
    " VALUES (:c, :v, :role, 'words', CAST(:answer AS jsonb), :at)"
)
_CORRECTION = (
    "INSERT INTO corrections (customer_id, document_id, field, old_value, new_value, choice, corrected_by,"
    " created_at) VALUES (:c, :d, 'total_amount', '1,200', '1,250', 'typed', :u, :at)"
)
_AUDIT = (
    "INSERT INTO audit_log (customer_id, actor_user_id, action, target_type, target_id, created_at)"
    " VALUES (:c, :u, :a, 'document', NULL, :at)"
)
_USAGE = (
    "INSERT INTO model_usage (customer_id, user_id, kind, model, cached, calls, input_tokens, output_tokens,"
    " reasoning_tokens, cost_usd, cost_estimated, created_at)"
    " VALUES (:c, :u, :kind, :m, :cached, :calls, :input, :output, 0, :cost, :estimated, :at)"
)
_SEEN = "UPDATE users SET last_seen_at = :seen, last_login_at = :login WHERE customer_id = :c AND id = :u"


@dataclass
class Org:
    """One planted organisation: its id and its people by name."""

    customer_id: str
    people: dict[str, str] = field(default_factory=dict)


def _sql(db: Database, customer_id: str, statement: str, **params: Any) -> list[Any]:
    """As this organisation (its customer filter and row level security)."""
    with db.customer(customer_id) as conn:
        result = conn.execute(text(statement), {"c": customer_id, **params})
        return list(result) if result.returns_rows else []


def _document(
    db: Database, org: Org, by: str, at: datetime, pages: list[tuple[str | None, str | None]], **columns: Any
) -> str:
    """A file ``by`` uploaded at ``at``, with one page row per (route, read_by)."""
    with db.customer(org.customer_id) as conn:
        document_id = insert_document(
            conn, org.customer_id, uploaded_by=org.people[by], created_at=at, page_count=len(pages), **columns
        )
        for number, (route, read_by) in enumerate(pages, start=1):
            conn.execute(
                text(_PAGE),
                {"c": org.customer_id, "d": document_id, "n": number, "route": route, "by": read_by},
            )
    return document_id


def _replies(db: Database, org: Org, by: str, at: datetime, statuses: list[str]) -> None:
    """A chat of ``by``'s: for each status, their question and the reply."""
    [(conversation,)] = _sql(db, org.customer_id, _CONVERSATION, u=org.people[by])
    for status in statuses:
        _sql(db, org.customer_id, _MESSAGE, v=conversation, role="user", answer=None, at=at)
        answer = json.dumps({"status": status, "label": "verified", "citations": []})
        _sql(db, org.customer_id, _MESSAGE, v=conversation, role="assistant", answer=answer, at=at)


def plant(api: Api, admin: Signed) -> Org:
    """An organisation used the way the product leaves it (the module docstring). ``admin`` is
    its signed-in admin (``admin``); the others are added here."""
    db, c = api.db, admin.customer_id
    org = Org(c, {"admin": admin.user_id})
    for person, role in (("m1", "member"), ("m2", "member"), ("r1", "reviewer"), ("idle", "member")):
        org.people[person] = api.add_user(c, role)[0]
    org.people["first"] = str(
        _sql(db, c, "SELECT id FROM users WHERE customer_id = :c AND display_name = 'Admin'")[0][0]
    )
    text_page, ai_page, local_page = ("text_layer", "text_layer"), ("ocr", MODEL), ("ocr", "rapidocr")
    # m1: a PDF with its own text (3 read, 1 not read yet), a scan the AI read, a spreadsheet in Trash.
    first = _document(db, org, "m1", THIS_MONTH, [text_page, text_page, text_page, (None, None)])
    _document(db, org, "m1", THIS_MONTH, [ai_page, ai_page])
    _document(
        db, org, "m1", THIS_MONTH, [("cells", "cells")], deleted_at=THIS_MONTH, deleted_by=org.people["m1"]
    )
    _document(db, org, "m1", LAST_30_DAYS, [ai_page, ai_page])
    # m2: a scan the local reader read (and a blank page), a failed and a rejected file, an old one.
    _document(db, org, "m2", THIS_MONTH, [local_page, local_page, ("blank", None)])
    _document(db, org, "m2", THIS_MONTH, [], status="failed", failure_code="too_many_pages")
    _document(db, org, "m2", LAST_30_DAYS, [], status="rejected", failure_code="infected")
    _document(db, org, "m2", LONG_AGO, [text_page] * 5)
    # r1: a newer version of m1's PDF (not current yet), and a scan whose reader was not recorded.
    _document(db, org, "r1", THIS_MONTH, [text_page], family_id=first, version_no=2, is_current=False)
    _document(db, org, "r1", LONG_AGO, [("ocr", None)], status="needs_reader")
    # Questions: a clarifying question back is not a question asked.
    _replies(db, org, "m1", THIS_MONTH, ["answered", "not_found", "clarify"])
    _replies(db, org, "m2", LAST_30_DAYS, ["answered"])
    _replies(db, org, "r1", LONG_AGO, ["unchecked"])
    # Values verified.
    for person, at, count in (("r1", THIS_MONTH, 2), ("admin", LAST_30_DAYS, 1), ("m1", LONG_AGO, 1)):
        for _ in range(count):
            _sql(db, c, _CORRECTION, d=first, u=org.people[person], at=at)
    # Last active, and what the activity log holds.
    _sql(db, c, _SEEN, u=org.people["m1"], seen=THIS_MONTH, login=LAST_30_DAYS)
    _sql(db, c, _SEEN, u=org.people["r1"], seen=None, login=LONG_AGO)
    for person, action, at in (
        ("m1", "document.upload", THIS_MONTH),
        ("m2", "chat.ask", LAST_30_DAYS),
        ("r1", "review.resolve", LONG_AGO),
    ):
        _sql(db, c, _AUDIT, u=org.people[person], a=action, at=at)
    _sql(db, c, _AUDIT, u=None, a="document.upload", at=THIS_MONTH)  # the system: nobody
    # AI use: billed scan calls, cached ones, a chat call whose cost is not known, an estimate.
    for kind, cached, calls, cost, estimated, at in (
        ("scan", False, 4, "0.040000", False, THIS_MONTH),
        ("scan", True, 2, "0", False, THIS_MONTH),
        ("chat", False, 1, None, False, LAST_30_DAYS),
        ("card", False, 1, "0.002000", True, LAST_30_DAYS),
        ("scan", False, 9, "9.000000", False, LONG_AGO),
    ):
        _sql(
            db, c, _USAGE, u=org.people["m1"], kind=kind, m=MODEL, cached=cached, calls=calls,
            input=1000 * calls, output=100 * calls, cost=cost, estimated=estimated, at=at,
        )  # fmt: skip
    return org


# --- the independent recount ----------------------------------------------------------------


Window = tuple[datetime | None, datetime | None]


def _within(at: datetime, window: Window) -> bool:
    lo, hi = window
    return (lo is None or at >= lo) and (hi is None or at < hi)


def _day(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=ZONE)


def _today() -> date:
    return NOW.astimezone(ZONE).date()


def windows() -> dict[str, Window]:
    """The admin's three periods, in TIMEZONE, as the screen promises them."""
    today = _today()
    month = today.replace(day=1)
    following = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return {
        "month": (_day(month), _day(following)),
        "30d": (_day(today - timedelta(days=29)), _day(today + timedelta(days=1))),
        "all": (None, None),
    }


@dataclass
class Raw:
    """One organisation's raw rows, read as that organisation."""

    users: list[Any]
    documents: list[Any]
    pages: list[Any]
    replies: list[Any]
    corrections: list[Any]
    audit: list[Any]
    usage: list[Any]


def raw(db: Database, customer_id: str) -> Raw:
    def rows(statement: str) -> list[Any]:
        return list(_sql(db, customer_id, statement))

    return Raw(
        users=rows("SELECT id, last_seen_at, last_login_at FROM users WHERE customer_id = :c"),
        documents=rows(
            "SELECT id, uploaded_by, created_at, deleted_at, status, failure_code FROM documents"
            " WHERE customer_id = :c"
        ),
        pages=rows("SELECT document_id, route, read_by FROM pages WHERE customer_id = :c"),
        replies=rows(
            "SELECT conversation_id, created_at, answer ->> 'status' FROM messages"
            " WHERE customer_id = :c AND role = 'assistant'"
        ),
        corrections=rows("SELECT corrected_by, created_at FROM corrections WHERE customer_id = :c"),
        audit=rows("SELECT actor_user_id, created_at FROM audit_log WHERE customer_id = :c"),
        usage=rows(
            "SELECT cached, calls, input_tokens, output_tokens, cost_usd, cost_estimated, created_at"
            " FROM model_usage WHERE customer_id = :c"
        ),
    )


def _owners(db: Database, customer_id: str) -> dict[Any, Any]:
    """Each conversation's owner."""
    return dict(_sql(db, customer_id, "SELECT id, user_id FROM conversations WHERE customer_id = :c"))


def _read_pages(rows: Raw, documents: set[Any]) -> list[Any]:
    return [page for page in rows.pages if page.document_id in documents and page.route is not None]


def expected_members(db: Database, customer_id: str, window: Window) -> dict[str, dict[str, Any]]:
    rows, owners = raw(db, customer_id), _owners(db, customer_id)
    members: dict[str, dict[str, Any]] = {}
    for user in rows.users:
        mine = {d.id for d in rows.documents if d.uploaded_by == user.id and _within(d.created_at, window)}
        seen = [at for at in (user.last_seen_at, user.last_login_at) if at is not None]
        members[str(user.id)] = {
            "documents_uploaded": len(mine),
            "pages_read": len(_read_pages(rows, mine)),
            "questions_asked": sum(
                1
                for conversation, at, status in rows.replies
                if owners[conversation] == user.id and status != "clarify" and _within(at, window)
            ),
            "values_verified": sum(
                1 for k in rows.corrections if k.corrected_by == user.id and _within(k.created_at, window)
            ),
            "last_active_at": max(seen).isoformat() if seen else None,
        }
    return members


def way(route: str, read_by: str | None) -> str:
    """How a page was read, as the operator's numbers name it."""
    if route == "ocr" and read_by is not None and "/" in read_by:
        return "ai"
    if route == "ocr" and read_by == "rapidocr":
        return "local"
    return route


def expected_org(db: Database, customer_id: str, window: Window) -> dict[str, Any]:
    rows = raw(db, customer_id)
    uploaded = [d for d in rows.documents if _within(d.created_at, window)]
    pages = _read_pages(rows, {d.id for d in uploaded})
    by_route: dict[str, int] = {}
    for page in pages:
        by_route[way(page.route, page.read_by)] = by_route.get(way(page.route, page.read_by), 0) + 1
    failed: dict[str, int] = {}
    for d in uploaded:
        if d.status in ("failed", "rejected", "needs_reader"):
            failed[d.failure_code or d.status] = failed.get(d.failure_code or d.status, 0) + 1
    usage = [u for u in rows.usage if _within(u.created_at, window)]
    billed = [u for u in usage if not u.cached]
    charged = [u.cost_usd for u in usage if u.cost_usd is not None and not u.cost_estimated]
    return {
        "documents": len(uploaded),
        "documents_in_trash": sum(1 for d in uploaded if d.deleted_at is not None),
        "pages_read": len(pages),
        "pages_by_route": by_route,
        "failed_documents": failed,
        "active_users": len(
            {a.actor_user_id for a in rows.audit if a.actor_user_id and _within(a.created_at, window)}
        ),
        "questions": sum(1 for _, at, status in rows.replies if status != "clarify" and _within(at, window)),
        "ai_calls": sum(u.calls for u in billed),
        "ai_input_tokens": sum(u.input_tokens for u in billed),
        "ai_output_tokens": sum(u.output_tokens for u in billed),
        "ai_cost_usd": float(sum(charged, Decimal(0))) if charged else None,
        "ai_cost_unknown_calls": sum(u.calls for u in billed if u.cost_usd is None or u.cost_estimated),
    }


# --- the tests ------------------------------------------------------------------------------


def _two_orgs(api: Api) -> tuple[Signed, Org, Org]:
    admin_a, admin_b = api.new_admin("Recount A"), api.new_admin("Recount B")
    a, b = plant(api, admin_a), plant(api, admin_b)
    # B differs from A, so a number taken from the wrong organisation cannot pass.
    _document(api.db, b, "m2", THIS_MONTH, [("ocr", MODEL)] * 4)
    _replies(api.db, b, "idle", THIS_MONTH, ["answered"] * 3)
    return admin_a, a, b


def test_every_number_on_the_members_table_is_the_raw_rows_recounted(db: Database) -> None:
    api = Api(db)
    admin, a, _b = _two_orgs(api)
    for period, window in windows().items():
        reply = admin.client.get("/api/admin/members", params={"period": period})
        assert reply.status_code == 200, reply.text
        body = reply.json()
        assert (body["period"], body["to"]) == (period, _today().isoformat())
        got = {
            row["user_id"]: {key: row[key] for key in ("documents_uploaded", "pages_read", "questions_asked",
                                                       "values_verified", "last_active_at")}
            for row in body["members"]
        }  # fmt: skip
        assert got == expected_members(db, a.customer_id, window), period
    # And by hand, so the recount above can't agree with the route on nothing.
    month = {row["user_id"]: row for row in admin.client.get("/api/admin/members").json()["members"]}
    m1, m2, r1 = (month[a.people[name]] for name in ("m1", "m2", "r1"))
    assert (m1["documents_uploaded"], m1["pages_read"], m1["questions_asked"]) == (3, 6, 2)
    assert (m2["documents_uploaded"], m2["pages_read"], r1["documents_uploaded"], r1["values_verified"]) == (
        2,
        3,
        1,
        2,
    )
    every = {
        row["user_id"]: row
        for row in admin.client.get("/api/admin/members", params={"period": "all"}).json()["members"]
    }
    assert (every[a.people["m2"]]["pages_read"], every[a.people["r1"]]["questions_asked"]) == (8, 1)
    assert month[a.people["idle"]]["last_active_at"] is None


def test_every_number_on_the_organisation_numbers_is_the_raw_rows_recounted(db: Database) -> None:
    api = Api(db)
    _admin, a, b = _two_orgs(api)
    _operator, client = _signed_in(api)
    today = api.clock().astimezone(ZONE).date()
    for days in (7, 30, 90):
        reply = client.get("/api/operator/org-numbers", params={"days": days})
        assert reply.status_code == 200, reply.text
        body = reply.json()
        start = today - timedelta(days=days - 1)
        assert (body["from"], body["to"]) == (start.isoformat(), today.isoformat())
        rows = {row["organisation_id"]: row for row in body["organisations"]}
        window = (_day(start), _day(today + timedelta(days=1)))
        for org in (a, b):
            got = {key: value for key, value in rows[org.customer_id].items() if key != "organisation_id"}
            assert got == expected_org(db, org.customer_id, window), (days, org.customer_id)
    # And by hand for the last 30 days: every route has its own count.
    a30 = {
        row["organisation_id"]: row for row in client.get("/api/operator/org-numbers").json()["organisations"]
    }
    assert a30[a.customer_id]["pages_by_route"] == {
        "text_layer": 4,
        "ai": 4,
        "local": 2,
        "cells": 1,
        "blank": 1,
    }
    assert a30[a.customer_id]["failed_documents"] == {"too_many_pages": 1, "infected": 1}
    assert (a30[a.customer_id]["documents"], a30[a.customer_id]["documents_in_trash"]) == (8, 1)
    assert (a30[a.customer_id]["ai_calls"], a30[a.customer_id]["ai_cost_unknown_calls"]) == (6, 2)
    assert a30[b.customer_id]["pages_by_route"]["ai"] == 8
