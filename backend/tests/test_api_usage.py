"""The organisation's AI models and spend (model choice, council 11 Oct 2026, SP1, SP2,
SP4, MS1, MS2, MC3).

Without a database: the usage period's days (this month to today when left out, at most
366, 400 otherwise), the CSV's formula escaping, and who chat memory, search and story
model calls are billed to (``usage_scope``). Against a real database (marker ``db``):
``/admin/models`` read-only, ``/admin/usage`` in the ``UsageReport`` shape, its CSV
download, the 403 for a member or an app session, the stats' spend from the ledger,
and a chat answer billed to its asker. Across customers: ``test_isolation_model_choice.py``.
"""

import csv
import io
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest
from sqlalchemy import text

from kaagaz.ai import usage
from kaagaz.ai.usage import record_call
from kaagaz.answering.expansion import PgWords
from kaagaz.answering.memory import Memory
from kaagaz.answering.rewrite import Turn
from kaagaz.api.auth import CurrentUser
from kaagaz.api.context import AppContext
from kaagaz.api.errors import ApiError
from kaagaz.api.routers import chat, search, stories
from kaagaz.api.routers.admin import ai_enabled, csv_cell, usage_csv, usage_days
from kaagaz.db import document_stories as stories_sql
from kaagaz.db.engine import Database
from kaagaz.db.usage import UsageRow
from kaagaz.search.scope import EVERYTHING
from tests.settings_factory import make_settings
from tests.test_api_model_choice import add_model, allow
from tests.test_api_support import CSRF, Api, FakeAnswers, MovingClock, Signed, _FixedAnswer

MODEL = "google/gemini-3.1-pro-preview"
EMBED = "google/gemini-embedding-2"
NOW = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)  # 11:30 in Asia/Kolkata
THIS_MONTH = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
LAST_MONTH = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)

# Changed on purpose (features 25-27, 15 Oct 2026): no cost_* fields, org admins never see money (Tushar).
TOTALS = {"calls", "cached_calls", "input_tokens", "output_tokens", "reasoning_tokens"}


# --- without a database ----------------------------------------------------------------


def test_sp2_the_period_is_this_month_to_today_when_left_out() -> None:
    assert usage_days(None, None, date(2026, 10, 20)) == (date(2026, 10, 1), date(2026, 10, 20))
    assert usage_days("2026-09-01", None, date(2026, 10, 20)) == (date(2026, 9, 1), date(2026, 10, 20))


def test_sp2_the_period_is_at_most_366_days_both_ends_included() -> None:
    today = date(2026, 10, 20)
    assert usage_days("2025-10-20", "2026-10-20", today) == (date(2025, 10, 20), date(2026, 10, 20))  # 366
    for first, last in (("2025-10-19", "2026-10-20"), ("2026-10-02", "2026-10-01"), ("2026-02-30", None)):
        with pytest.raises(ApiError) as refused:
            usage_days(first, last, today)
        assert (refused.value.status, refused.value.code) == (400, "bad_request")


def test_sp2_a_cell_that_starts_like_a_formula_gets_a_quote() -> None:
    assert [csv_cell(value) for value in ("=SUM(A1)", "+91", "-1", "@cmd", "\tx", "\rx")] == [
        "'=SUM(A1)",
        "'+91",
        "'-1",
        "'@cmd",
        "'\tx",
        "'\rx",
    ]
    assert [csv_cell(value) for value in ("Asha", 12, "0.5", "a=b", "")] == ["Asha", "12", "0.5", "a=b", ""]


@pytest.mark.parametrize(
    ("ocr_engine", "model_enabled", "enabled"),
    [("gemini", True, True), ("rapidocr", True, True), ("gemini", False, True), ("rapidocr", False, False)],
)
def test_ai_is_on_unless_the_server_is_keyless(ocr_engine: str, model_enabled: bool, enabled: bool) -> None:
    """R9: scans read by a model, or MODEL_ENABLED, call AI; only keyless mode calls none."""
    assert ai_enabled(make_settings(ocr_engine=ocr_engine, model_enabled=model_enabled)) is enabled


def test_sp2_the_csv_has_its_columns_then_one_line_each_escaped() -> None:
    lines = [("2026-10-02", '=HYPERLINK("http://x","y")', MODEL, "scan", 3, 1200)]
    parsed = list(csv.reader(io.StringIO(usage_csv(lines))))
    # Changed on purpose (features 25-27, 15 Oct 2026): no cost columns, org admins never see money.
    assert parsed == [
        ["day", "user", "model", "kind", "calls", "tokens"],
        ["2026-10-02", '\'=HYPERLINK("http://x","y")', MODEL, "scan", "3", "1200"],
    ]


class _Ledger:
    """A database whose one job is the ledger's save: ``insert_usage`` is replaced, so the
    rows each usage scope saves are kept here."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.saved: list[UsageRow] = []
        monkeypatch.setattr(usage, "insert_usage", lambda conn, rows: self.saved.extend(rows))

    @contextmanager
    def customer(self, customer_id: str) -> Iterator[None]:
        yield None


def _user() -> CurrentUser:
    return CurrentUser(
        user_id=str(uuid.uuid4()),
        customer_id=str(uuid.uuid4()),
        email="asha@example.test",
        display_name="Asha",
        role="member",
        customer_name="Sharma Traders",
        scope="app",
        session_version=1,
        must_change_password=False,
        totp_enabled=False,
        delete_forever_allowed=False,
    )


def _context(ledger: _Ledger, model: Any = None) -> AppContext:
    return AppContext(
        settings=make_settings(),
        db=cast(Database, ledger),
        storage=cast(Any, None),
        queue=cast(Any, None),
        answers=cast(Any, None),
        clock=lambda: NOW,
        model=model,
    )


def _bill(model: str = EMBED) -> None:
    record_call(model, "Google", 12, 0, 0, 0.000002)


class _BillingEmbedder:
    chat_model = MODEL
    embed_model = EMBED

    def embed(self, texts: list[str]) -> list[list[float]]:
        _bill()
        return [[0.5] * stories_sql.VECTOR_DIM for _ in texts]

    def chat_json(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("no chat call expected")


def _billed(saved: Sequence[UsageRow]) -> list[tuple[object, ...]]:
    return [
        (row.customer_id, row.user_id, row.kind, row.conversation_id, row.model, row.calls) for row in saved
    ]


def test_sp1_a_search_calls_no_model_and_bills_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Changed on purpose (PRD 1, activity/search): File management Q1, keyword only."""
    ledger, user = _Ledger(monkeypatch), _user()
    monkeypatch.setattr(search, "resolve", lambda *args, **kwargs: EVERYTHING)
    monkeypatch.setattr(search, "narrow", lambda conn, customer, scope, asked: scope)
    monkeypatch.setattr(search, "search_documents", lambda *args, **kwargs: [])
    monkeypatch.setattr(PgWords, "words", lambda *args: [])

    reply = search.search_inside_documents(
        search.SearchParams(q="total"), user, _context(ledger, _BillingEmbedder())
    )

    assert reply == {"documents": [], "story_matches": [], "understood_words": []}
    assert ledger.saved == []


def test_sp1_a_storys_embedding_is_billed_to_its_user_even_in_its_own_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, user = _Ledger(monkeypatch), _user()

    def embed_story(*args: object) -> bool:
        _bill()
        return True

    monkeypatch.setattr(stories, "embed_story", embed_story)
    row = stories_sql.StoryRow(
        family_id=str(uuid.uuid4()),
        story="Papa ka 7/12",
        embedding_model=None,
        created_at=NOW,
        updated_at=NOW,
    )
    assert stories._embed_slots.acquire(blocking=False)  # _embed_now frees it
    done = stories._embeds.submit(
        stories._embed_now, _context(ledger), cast(Any, _BillingEmbedder()), user, row
    )

    assert done.result(timeout=5) is True
    assert _billed(ledger.saved) == [(user.customer_id, user.user_id, "story", None, EMBED, 1)]


def test_sp1_the_chat_memory_is_billed_to_the_asker_with_the_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, user = _Ledger(monkeypatch), _user()
    conversation = str(uuid.uuid4())
    monkeypatch.setattr(chat, "_write_memory", lambda *args: _bill(MODEL))

    chat._remember(_context(ledger), user.customer_id, user.user_id, conversation, 0, 6)

    assert _billed(ledger.saved) == [(user.customer_id, user.user_id, "memory", conversation, MODEL, 1)]


# --- against a real database ------------------------------------------------------------


def add_spend(
    db: Database,
    customer_id: str,
    *,
    at: datetime,
    cost: float | None,
    user_id: str | None = None,
    document_id: str | None = None,
    kind: str = "scan",
    model: str = MODEL,
) -> None:
    """One ledger row as a usage scope leaves it, at ``at`` (``insert_usage`` takes now())."""
    with db.customer(customer_id) as conn:
        conn.execute(
            text(
                "INSERT INTO model_usage (customer_id, user_id, document_id, kind, model, calls,"
                " input_tokens, output_tokens, reasoning_tokens, cost_usd, created_at)"
                " VALUES (:c, :u, :d, :k, :m, 1, 1000, 100, 0, :cost, :at)"
            ),
            {"c": customer_id, "u": user_id, "d": document_id, "k": kind, "m": model, "cost": cost, "at": at},
        )


def set_budget(db: Database, customer_id: str, usd: float) -> None:
    with db.engine.begin() as conn:  # the owner, as kaagaz_op_set_org_models leaves it
        conn.execute(
            text("UPDATE customers SET ai_budget_usd = :b WHERE id = :c"), {"b": usd, "c": customer_id}
        )


def _gets(signed: Signed, url: str) -> tuple[int, Any]:
    reply = signed.client.get(url)
    return reply.status_code, reply.json() if reply.headers["content-type"].startswith(
        "application/json"
    ) else None


@pytest.mark.db
def test_ms1_ms2_the_admin_sees_the_models_read_only_by_name(db: Database) -> None:
    # Changed on purpose (features 25-27, 15 Oct 2026): names only, no prices, budget or spend,
    # org admins never see money (Tushar).
    api = Api(db, settings=make_settings(ocr_engine="gemini"), clock=MovingClock(NOW))
    admin = api.new_admin()
    c = admin.customer_id
    default = add_model(db, name="Default model")
    other = add_model(db, name="Other model", tokens=(500, 100))
    allow(db, c, [default, other], default=default)
    set_budget(db, c, 10.0)
    add_spend(db, c, at=THIS_MONTH, cost=2.5)

    status, body = _gets(admin, "/api/admin/models")

    assert status == 200
    assert body == {
        "scan": [{"name": "Default model", "default": True}, {"name": "Other model", "default": False}],
        "chat": [],  # MODEL_ENABLED off: no model answers
    }


@pytest.mark.db
def test_ms1_with_no_models_set_the_platforms_model_reads(db: Database) -> None:
    # Changed on purpose (features 25-27, 15 Oct 2026): the platform's model is listed as the default.
    settings = make_settings(ocr_engine="gemini", model_enabled=True)
    admin = Api(db, settings=settings, clock=MovingClock(NOW)).new_admin()
    status, body = _gets(admin, "/api/admin/models")
    assert (status, body) == (
        200,
        {
            "scan": [{"name": settings.ocr_model, "default": True}],
            "chat": [{"name": settings.model_chat, "default": True}],
        },
    )


@pytest.mark.db
def test_sp2_the_usage_report_has_the_usage_report_shape_in_tokens(db: Database) -> None:
    # Changed on purpose (features 25-27, 15 Oct 2026): no budget and no costs, org admins never see
    # money (Tushar).
    api = Api(db, clock=MovingClock(NOW))
    admin = api.new_admin()
    c = admin.customer_id
    member, _ = api.add_user(c, "member")
    document = api.add_document(c, uploaded_by=member)
    set_budget(db, c, 10.0)
    add_spend(db, c, at=THIS_MONTH, cost=0.5, user_id=member, document_id=document)
    add_spend(db, c, at=THIS_MONTH, cost=0.25, user_id=member, kind="chat")
    add_spend(db, c, at=THIS_MONTH, cost=0.1, kind="embed", model=EMBED)  # no user asked
    add_spend(db, c, at=LAST_MONTH, cost=9.0, user_id=member)  # outside the period

    status, report = _gets(admin, "/api/admin/usage")

    assert status == 200
    assert set(report) == {
        "from",
        "to",
        "ai_enabled",
        "totals",
        "by_user",
        "by_model",
        "by_kind",
        "by_day",
        "top_documents",
    }
    assert (report["from"], report["to"]) == ("2026-10-01", "2026-10-20")
    assert set(report["totals"]) == TOTALS
    assert (report["totals"]["calls"], report["totals"]["input_tokens"]) == (3, 3000)
    for part, own in (
        ("by_user", {"user_id", "user_name"}),
        ("by_model", {"model", "model_name"}),
        ("by_kind", {"kind"}),
        ("by_day", {"day"}),
        ("top_documents", {"document_id", "name"}),
    ):
        assert report[part], part
        assert all(set(item) == TOTALS | own for item in report[part]), part
    assert {item["user_id"] for item in report["by_user"]} == {member, None}
    assert [item["day"] for item in report["by_day"]] == ["2026-10-02"]

    _, one = _gets(admin, f"/api/admin/usage?from=2026-10-01&to=2026-10-20&user_id={member}")
    assert (one["totals"]["calls"], [item["user_id"] for item in one["by_user"]]) == (2, [member])


@pytest.mark.db
@pytest.mark.parametrize(
    "query",
    [
        "from=2026-10-2",
        "from=yesterday",
        "from=2026-02-30",
        "from=2026-10-05&to=2026-10-01",
        "from=2025-01-01&to=2026-10-01",
    ],
)
def test_sp2_bad_dates_are_400(db: Database, query: str) -> None:
    admin = Api(db, clock=MovingClock(NOW)).new_admin()
    for url in ("/api/admin/usage", "/api/admin/usage.csv"):
        status, body = _gets(admin, f"{url}?{query}")
        assert (status, body["error"]["code"]) == (400, "bad_request"), url


@pytest.mark.db
def test_sp2_the_csv_downloads_one_escaped_line_per_day_user_model_and_kind(db: Database) -> None:
    api = Api(db, clock=MovingClock(NOW))
    admin = api.new_admin()
    c = admin.customer_id
    member, _ = api.add_user(c, "member")
    with db.customer(c) as conn:
        conn.execute(text("UPDATE users SET display_name = '=cmd|calc' WHERE id = :u"), {"u": member})
    add_spend(db, c, at=THIS_MONTH, cost=0.5, user_id=member)
    add_spend(db, c, at=THIS_MONTH, cost=0.25, user_id=member)
    add_spend(db, c, at=THIS_MONTH, cost=None, kind="embed", model=EMBED)

    reply = admin.client.get("/api/admin/usage.csv?from=2026-10-01&to=2026-10-20")

    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/csv")
    assert (
        reply.headers["content-disposition"] == 'attachment; filename="ai-usage-2026-10-01-to-2026-10-20.csv"'
    )
    # Changed on purpose (features 25-27, 15 Oct 2026): no cost columns, org admins never see money.
    assert list(csv.reader(io.StringIO(reply.text))) == [
        ["day", "user", "model", "kind", "calls", "tokens"],
        ["2026-10-02", "", EMBED, "embed", "1", "1100"],
        ["2026-10-02", "'=cmd|calc", MODEL, "scan", "2", "2200"],
    ]


@pytest.mark.db
@pytest.mark.parametrize("url", ["/api/admin/usage", "/api/admin/usage.csv", "/api/admin/models"])
def test_sp2_a_member_gets_403(db: Database, url: str) -> None:
    # An admin's app session no longer exists (one sign-in, 0035): test_auth_tokens.
    api = Api(db, clock=MovingClock(NOW))
    customer_id, _ = api.customer()
    member = api.sign_in(customer_id, "member")
    assert _gets(member, url)[0] == 403


class _BillingAnswers(FakeAnswers):
    def answer(self, customer_id: str, question: str, **kwargs: Any) -> _FixedAnswer:
        _bill(MODEL)
        return super().answer(customer_id, question, **kwargs)

    def remember(self, turns: Sequence[Turn], memory: Memory, cited: Sequence[str]) -> Memory | None:
        _bill(MODEL)
        return super().remember(turns, memory, cited)


def _ledger(db: Database, customer_id: str) -> list[tuple[object, ...]]:
    with db.customer(customer_id) as conn:
        rows = conn.execute(
            text("SELECT user_id, kind, conversation_id, model, calls FROM model_usage ORDER BY id")
        ).all()
    return [
        tuple(
            None if value is None else str(value) if isinstance(value, uuid.UUID) else value for value in row
        )
        for row in rows
    ]


@pytest.mark.db
def test_sp1_a_chat_answer_and_its_memory_are_billed_to_the_asker_with_the_conversation(db: Database) -> None:
    api = Api(db, settings=make_settings(chat_summary_every=1), answers=_BillingAnswers())
    asker = api.new_signed("member")

    reply = asker.client.post("/api/chat", json={"question": "What is the total?"}, headers=CSRF)

    assert reply.status_code == 200, reply.text
    conversation = reply.json()["conversation_id"]
    assert _ledger(db, asker.customer_id) == [
        (asker.user_id, "chat", conversation, MODEL, 1),
        (asker.user_id, "memory", conversation, MODEL, 1),
    ]


@pytest.mark.db
def test_sp1_a_search_bills_nothing(db: Database) -> None:
    """Changed on purpose (PRD 1, activity/search): File management Q1, keyword only."""
    api = Api(db, model=cast(Any, _BillingEmbedder()))
    searcher = api.new_signed("member")
    assert searcher.client.get("/api/search?q=total").status_code == 200
    assert _ledger(db, searcher.customer_id) == []


@pytest.mark.db
@pytest.mark.parametrize(
    ("overrides", "enabled"),
    [
        ({"ocr_engine": "gemini", "model_enabled": True}, True),
        ({"ocr_engine": "rapidocr", "model_enabled": True}, True),  # chat and cards call models
        ({"ocr_engine": "gemini", "model_enabled": False}, True),  # scans are read by a model
        ({"ocr_engine": "rapidocr", "model_enabled": False}, False),  # keyless mode
    ],
)
def test_sp2_the_report_says_whether_ai_is_on_on_this_server(
    db: Database, overrides: dict[str, Any], enabled: bool
) -> None:
    admin = Api(db, settings=make_settings(**overrides), clock=MovingClock(NOW)).new_admin()
    status, report = _gets(admin, "/api/admin/usage")
    assert (status, report["ai_enabled"]) == (200, enabled)
