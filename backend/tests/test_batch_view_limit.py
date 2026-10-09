"""``GET /api/batches/{id}`` lists at most MAX_FILES_PER_BATCH files (PRD 5 Q1), the
upload's own cap, never a fixed number: the route passes the setting to
``listing.batch_rows``, which binds it as the query's LIMIT. No database: the route is
called with a stand-in context; tests/test_batches_api.py checks it against PostgreSQL."""

import contextlib
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import Connection

from kaagaz.api.context import AppContext
from kaagaz.api.errors import ApiError
from kaagaz.api.routers import batches
from kaagaz.db import listing
from tests.settings_factory import make_settings

CUSTOMER = "aaaaaaaa-0000-4000-8000-00000000000a"
USER = "bbbbbbbb-0000-4000-8000-00000000000b"


class Recorded:
    """A connection that notes the one statement's parameters and finds no rows."""

    def __init__(self) -> None:
        self.params: dict[str, object] = {}

    def execute(self, statement: object, params: dict[str, object]) -> "Recorded":
        self.params = params
        return self

    def mappings(self) -> "Recorded":
        return self

    def all(self) -> list[object]:
        return []


def test_batch_rows_binds_the_limit_it_is_given() -> None:
    conn = Recorded()
    assert listing.batch_rows(cast(Connection, conn), CUSTOMER, "b", user_id=USER, limit=7) == []
    assert conn.params == {"c": CUSTOMER, "b": "b", "u": USER, "limit": 7}


def test_the_batch_route_asks_for_at_most_max_files_per_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[int] = []

    def batch_rows(conn: object, customer_id: str, batch_id: str, *, user_id: str, limit: int) -> list[Any]:
        asked.append(limit)
        return []

    @contextlib.contextmanager
    def customer(customer_id: str) -> Iterator[object]:
        yield object()

    monkeypatch.setattr(listing, "batch_rows", batch_rows)
    context = SimpleNamespace(
        settings=make_settings(max_files_per_batch=40), db=SimpleNamespace(customer=customer)
    )
    user = SimpleNamespace(customer_id=CUSTOMER, user_id=USER)
    with pytest.raises(ApiError):  # no rows: the same 404 as any batch not this user's
        batches.get_batch(uuid.uuid4(), cast(Any, user), cast(AppContext, context))
    assert asked == [40]
