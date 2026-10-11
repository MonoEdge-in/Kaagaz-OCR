"""``cli reembed`` bills each customer's model calls to that customer as "embed", with no
user (model choice, council 11 Oct 2026, SP1): every embedding call it makes for a
customer is inside that customer's usage scope. Without a database: the reembed itself is
``test_reembed_db.py``'s."""

import io
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import pytest
from sqlalchemy import Connection

from kaagaz import cli
from kaagaz.ai.client import ModelClient
from kaagaz.db import customers as customers_sql
from kaagaz.db.engine import Database
from kaagaz.search import indexing

A, B = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"


class _Db:
    @contextmanager
    def customer(self, customer_id: str) -> Iterator[Connection]:
        yield cast(Connection, None)


class _Model:
    embed_model = "example/embed-1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


def test_each_customers_embedding_calls_are_billed_to_it_with_no_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_scope: list[dict[str, Any]] = []
    scopes: list[dict[str, Any]] = []
    embedded: list[tuple[str, dict[str, Any] | None]] = []

    @contextmanager
    def scope(db: object, **kwargs: Any) -> Iterator[None]:
        scopes.append(kwargs)
        open_scope.append(kwargs)
        try:
            yield
        finally:
            open_scope.pop()

    def embed_pieces(db: object, customer: str, document: str, model: object) -> int:
        embedded.append((customer, open_scope[-1] if open_scope else None))
        return 1

    def stories(db: object, model: object, customer: str) -> int:
        embedded.append((customer, open_scope[-1] if open_scope else None))
        return 0

    monkeypatch.setattr(cli, "usage_scope", scope)
    monkeypatch.setattr(customers_sql, "is_active", lambda conn, customer: True)
    monkeypatch.setattr(indexing, "document_ids", lambda conn, customer: [f"doc-of-{customer}"])
    monkeypatch.setattr(indexing, "clear_stale", lambda *args, **kwargs: 0)
    monkeypatch.setattr(indexing, "embed_pieces", embed_pieces)
    monkeypatch.setattr(cli, "_reembed_stories", stories)

    out = io.StringIO()
    cli._reembed(cast(Database, _Db()), cast(ModelClient, _Model()), A, False, out)
    cli._reembed(cast(Database, _Db()), cast(ModelClient, _Model()), B, False, out)

    expected = [{"customer_id": c, "user_id": None, "kind": "embed"} for c in (A, B)]
    assert scopes == expected
    assert embedded == [(A, expected[0]), (A, expected[0]), (B, expected[1]), (B, expected[1])]
