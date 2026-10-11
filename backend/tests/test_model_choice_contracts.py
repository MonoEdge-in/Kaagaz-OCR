"""Model choice and AI spend (council 11 Oct 2026): the usage scope's tallies and save, the
provider block and its ceiling, and the scan model resolver's fallback. No database: a fake
connection stands in; the SQL itself is tested against 0023 by the database tests."""

import contextvars
import logging
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, cast

import pytest
from sqlalchemy import Connection
from sqlalchemy.exc import OperationalError

from kaagaz.ai import usage
from kaagaz.ai.openrouter import PROVIDER, OpenRouterClient, ProviderNotAllowed, provider
from kaagaz.db import models
from kaagaz.db.engine import Database
from tests.settings_factory import make_settings

CUSTOMER = "00000000-0000-0000-0000-00000000000a"
USER = "00000000-0000-0000-0000-00000000000b"
DOCUMENT = "00000000-0000-0000-0000-00000000000c"
CLAUDE = "anthropic/claude-sonnet-5.5"


class _Result:
    def __init__(self, rows: list[Mapping[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[Mapping[str, Any]]:
        return self._rows


class _Conn:
    def __init__(self, rows: list[Mapping[str, Any]] | None = None, *, fail: bool = False) -> None:
        self.rows = rows or []
        self.fail = fail
        self.sent: list[Any] = []

    def execute(self, statement: object, params: Any) -> _Result:
        if self.fail:
            raise OperationalError("insert", {}, Exception("down"))
        self.sent.append(params)
        return _Result(self.rows)


class _Db:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn
        self.customers: list[str] = []

    @contextmanager
    def customer(self, customer_id: str) -> Iterator[_Conn]:
        self.customers.append(customer_id)
        yield self.conn


def _db(conn: _Conn) -> tuple[_Db, Database]:
    fake = _Db(conn)
    return fake, cast(Database, fake)


def test_a_scope_saves_one_row_per_model_and_cached_in_its_customer() -> None:
    fake, db = _db(_Conn())
    with usage.usage_scope(db, customer_id=CUSTOMER, user_id=USER, kind="scan", document_id=DOCUMENT):
        usage.record_call(CLAUDE, "google-vertex", 100, 50, 20, 0.01)
        usage.record_call(CLAUDE, "google-vertex", 10, 5, 2, 0.02)
        usage.record_call(CLAUDE, None, 0, 0, 0, 0.0, cached=True)
        usage.record_call("google/gemini-3.8-flash", None, 7, 3, 0, None)  # no cost: estimated later
    assert fake.customers == [CUSTOMER]
    [rows] = fake.conn.sent
    by_key = {(row["model"], row["cached"]): row for row in rows}
    assert by_key[(CLAUDE, False)] == {
        **by_key[(CLAUDE, False)],
        "calls": 2,
        "input_tokens": 110,
        "output_tokens": 55,
        "reasoning_tokens": 22,
        "provider_name": "google-vertex",
        "kind": "scan",
        "user_id": USER,
        "document_id": DOCUMENT,
        "conversation_id": None,
    }
    assert by_key[(CLAUDE, False)]["cost_usd"] == pytest.approx(0.03)
    assert by_key[(CLAUDE, True)]["calls"] == 1 and by_key[(CLAUDE, True)]["cost_usd"] == 0.0
    assert by_key[("google/gemini-3.8-flash", False)]["cost_usd"] is None


def test_no_scope_no_calls_and_an_unknown_kind_save_nothing() -> None:
    fake, db = _db(_Conn())
    usage.record_call(CLAUDE, None, 1, 1, 0, 0.1)  # no scope open: not recorded
    with usage.usage_scope(db, customer_id=CUSTOMER, user_id=None, kind="embed"):
        pass
    with (
        pytest.raises(ValueError, match="kind"),
        usage.usage_scope(db, customer_id=CUSTOMER, user_id=None, kind="x"),
    ):
        pass
    assert fake.conn.sent == []


def test_threads_add_to_the_scope_and_an_inner_scope_is_its_own() -> None:
    fake, db = _db(_Conn())
    with usage.usage_scope(db, customer_id=CUSTOMER, user_id=USER, kind="scan"):
        threads = [
            threading.Thread(
                target=contextvars.copy_context().run, args=(usage.record_call, CLAUDE, None, 1, 1, 0, 0.5)
            )
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with usage.usage_scope(db, customer_id=CUSTOMER, user_id=USER, kind="card"):
            usage.record_call(CLAUDE, None, 2, 2, 0, 0.1)
        usage.record_call(CLAUDE, None, 1, 1, 0, 0.5)
    inner, outer = fake.conn.sent
    assert [(row["kind"], row["calls"]) for row in inner] == [("card", 1)]
    assert [(row["kind"], row["calls"]) for row in outer] == [("scan", 9)]


def test_a_save_that_fails_is_logged_and_never_fails_the_work(caplog: pytest.LogCaptureFixture) -> None:
    _, db = _db(_Conn(fail=True))
    with (
        caplog.at_level(logging.WARNING),
        usage.usage_scope(db, customer_id=CUSTOMER, user_id=None, kind="map"),
    ):
        usage.record_call(CLAUDE, None, 1, 1, 0, 0.1)
    assert [getattr(record, "code", None) for record in caplog.records] == ["usage_not_saved"]


def test_the_provider_block_names_one_slug_and_keeps_the_rules() -> None:
    assert provider() == dict(PROVIDER)
    assert provider("azure") == {**PROVIDER, "only": ["azure"]}
    assert provider("azure", embeddings=True)["require_parameters"] is False
    with pytest.raises(ValueError, match="provider slug"):
        provider("Azure")


def test_a_page_read_through_a_provider_outside_the_ceiling_sends_nothing() -> None:
    client = OpenRouterClient(make_settings(openrouter_api_key="sk-or-v1-" + "q" * 64))
    with pytest.raises(ProviderNotAllowed, match="provider_not_allowed"):
        client.read_page(
            png=b"",
            instruction="",
            schema={},
            media_resolution="high",
            thinking_level="low",
            deadline=client.now() + 1,
            model=CLAUDE,
            provider="azure",
        )
    assert client.usage().input_tokens == 0


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], ("google/gemini-3.1-pro-preview", "google-vertex")),  # no models set: as before
        (
            [
                {
                    "id": "google/gemini-3.8-flash",
                    "providers": ["google-vertex"],
                    "is_default_scan": True,
                    "chosen": None,
                },
                {
                    "id": CLAUDE,
                    "providers": ["amazon-bedrock", "google-vertex"],
                    "is_default_scan": False,
                    "chosen": True,
                },
            ],
            (CLAUDE, "google-vertex"),  # the chosen model, through its first provider in the ceiling
        ),
        (
            [
                {
                    "id": "google/gemini-3.8-flash",
                    "providers": ["google-vertex"],
                    "is_default_scan": True,
                    "chosen": False,
                },
                {"id": "openai/gpt-6-luna", "providers": ["azure"], "is_default_scan": False, "chosen": True},
            ],
            ("google/gemini-3.8-flash", "google-vertex"),  # azure is outside the ceiling: the default
        ),
    ],
)
def test_the_scan_model_is_the_choice_else_the_default_else_ocr_model(
    rows: list[Mapping[str, Any]], expected: tuple[str, str]
) -> None:
    conn = cast(Connection, _Conn(rows))
    assert models.resolve(conn, CUSTOMER, DOCUMENT, settings=make_settings(), prompt_version="g2") == expected
