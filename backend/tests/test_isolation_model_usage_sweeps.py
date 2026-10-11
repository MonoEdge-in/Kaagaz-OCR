"""AI spend from the vector sweeps and the story embed (model choice, council 11 Oct 2026,
SP1): each customer's calls are billed to that customer only, in its own usage scope, with
no user for a sweep (the missing-vector pass, the story pass) and the author for a story's
own embed.

No database: the sweeps' queries and writes are swapped for fakes, and the ledger's save
(``kaagaz.ai.usage.insert_usage``) is kept with the customer whose transaction saved it,
so a row saved in another customer's transaction would show."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from kaagaz.ai import usage
from kaagaz.ai.client import ModelClient
from kaagaz.db.document_stories import StoryRow
from kaagaz.db.engine import Database
from kaagaz.db.usage import UsageRow
from kaagaz.search import indexing, story_vectors

A = "00000000-0000-0000-0000-0000000000a1"
B = "00000000-0000-0000-0000-0000000000b2"
A_USER = "00000000-0000-0000-0000-0000000000a3"
B_USER = "00000000-0000-0000-0000-0000000000b4"
EMBED = "google/gemini-embedding-2"
NOW = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)


class _Conn:
    """One transaction, of ``customer`` (None: the system's): ``_MISSING`` gives its documents."""

    def __init__(self, customer: str | None, documents: list[str]) -> None:
        self.customer = customer
        self.documents = documents

    def execute(self, statement: object, params: Any = None) -> list[tuple[str]]:
        return [(document,) for document in self.documents]


class _Db:
    """Each customer's documents; ``saved`` is (the transaction's customer, the row) for every
    ledger row."""

    def __init__(self, documents: dict[str, list[str]] | None = None) -> None:
        self.documents = documents or {}
        self.saved: list[tuple[str | None, UsageRow]] = []

    @contextmanager
    def system(self) -> Iterator[_Conn]:
        yield _Conn(None, [])

    @contextmanager
    def customer(self, customer_id: str) -> Iterator[_Conn]:
        yield _Conn(customer_id, self.documents.get(customer_id, []))

    @contextmanager
    def user(self, customer_id: str, user_id: str) -> Iterator[_Conn]:
        yield _Conn(customer_id, [])


class _Model:
    """The embedding model: each text is one billed call, as the model client records it."""

    embed_model = EMBED

    def embed(self, texts: list[str]) -> list[list[float]]:
        for text in texts:
            usage.record_call(EMBED, None, len(text), 0, 0, 0.0001)
        return [[0.0] * 4 for _ in texts]


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> _Db:
    fake = _Db()

    def insert(conn: _Conn, rows: list[UsageRow]) -> None:
        fake.saved.extend((conn.customer, row) for row in rows)

    monkeypatch.setattr(usage, "insert_usage", insert)
    return fake


def _model() -> ModelClient:
    return cast(ModelClient, _Model())


def test_sp1_the_missing_vector_pass_bills_each_document_to_its_own_customer_with_no_user(
    db: _Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.documents = {A: ["doc-a"], B: ["doc-b"]}

    def embed_pieces(database: Any, customer: str, document: str, model: ModelClient, **_: Any) -> int:
        model.embed([f"{customer}:{document}"])
        return 1

    monkeypatch.setattr(indexing, "customer_ids", lambda conn: [A, B])
    monkeypatch.setattr(indexing, "is_active", lambda conn, customer: True)
    monkeypatch.setattr(indexing, "embed_pieces", embed_pieces)

    assert indexing.embed_missing(cast(Database, db), _model(), None).written == 2

    rows = {(saved_in, row.customer_id, row.document_id, row.kind, row.user_id) for saved_in, row in db.saved}
    assert rows == {(A, A, "doc-a", "embed", None), (B, B, "doc-b", "embed", None)}
    assert [row.calls for _, row in db.saved] == [1, 1]


def test_sp1_the_story_pass_bills_each_story_to_its_own_customer_with_no_user(
    db: _Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    def story(conn: Any, customer: str, user: str, family: str) -> StoryRow:
        return StoryRow(family, f"story of {customer}", None, NOW, NOW)

    monkeypatch.setattr(
        story_vectors, "stories_to_embed", lambda conn, **_: [(A, A_USER, "fa"), (B, B_USER, "fb")]
    )
    monkeypatch.setattr(story_vectors, "is_active", lambda conn, customer: True)
    monkeypatch.setattr(story_vectors, "get_story", story)
    monkeypatch.setattr(story_vectors, "_write", lambda *args: True)

    found = story_vectors.embed_missing_stories(cast(Database, db), _model(), limit=10)

    assert (found.found, found.written) == (2, 2)
    rows = sorted((saved_in, row.customer_id, row.kind, row.user_id, row.calls) for saved_in, row in db.saved)
    assert rows == [(A, A, "story", None, 1), (B, B, "story", None, 1)]


def test_sp1_a_storys_own_embed_is_billed_to_its_author(db: _Db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(story_vectors, "_write", lambda *args: True)
    assert story_vectors.embed_story(cast(Database, db), _model(), B, B_USER, "fb", "My story")
    [(saved_in, row)] = db.saved
    assert (saved_in, row.customer_id, row.user_id, row.kind, row.model) == (B, B, B_USER, "story", EMBED)
