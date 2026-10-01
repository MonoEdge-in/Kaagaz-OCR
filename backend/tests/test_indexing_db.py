"""``embed_pieces`` with a fake model, against a real PostgreSQL."""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.db.engine import Database
from kaagaz.search.indexing import embed_pieces
from tests.db_support import new_customer
from tests.test_search_db import add_document, add_pieces, axis, run

pytestmark = pytest.mark.db


class FakeModel:
    """Embeds the n-th text it has seen as ``axis(n)``; records each call."""

    chat_model = "fake-chat"
    embed_model = "fake-embed"

    def __init__(self, *, fail: bool = False, drop_one: bool = False) -> None:
        self.calls: list[list[str]] = []
        self._fail = fail
        self._drop_one = drop_one
        self._seen = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self._fail:
            raise ModelUnavailable("unreachable")
        vectors = [axis(self._seen + i) for i in range(len(texts))]
        self._seen += len(texts)
        return vectors[1:] if self._drop_one else vectors

    def chat_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int | None = None
    ) -> dict[str, Any]:
        raise AssertionError("indexing never chats")

    def read_image(self, *, png: bytes, instruction: str) -> str:
        raise AssertionError("indexing never reads images")


def _embeddings(db: Database, customer_id: str, document_id: str) -> list[tuple[bool, str | None]]:
    with db.customer(customer_id) as conn:
        rows = conn.execute(
            text(
                "SELECT embedding IS NOT NULL, embedding_model FROM pieces"
                " WHERE customer_id = :c AND document_id = :d ORDER BY seq"
            ),
            {"c": customer_id, "d": document_id},
        ).all()
    return [(bool(row[0]), row[1]) for row in rows]


def test_every_piece_is_embedded_in_batches_in_order(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [(f"piece {n}", None) for n in range(5)])
    model = FakeModel()

    assert embed_pieces(db, customer, document, model, batch_size=2) == 5

    assert model.calls == [["piece 0", "piece 1"], ["piece 2", "piece 3"], ["piece 4"]]
    assert _embeddings(db, customer, document) == [(True, "fake-embed")] * 5


def test_each_batch_is_reported_once_its_write_has_committed(db: Database) -> None:
    """The pipeline's heartbeat and "indexing i of n". Each report comes after the
    batch's transaction, never inside one: another transaction already sees the vectors."""
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [(f"piece {n}", None) for n in range(5)])
    reports: list[tuple[int, int, int]] = []

    def on_batch(done: int, total: int) -> None:
        embedded = sum(has_vector for has_vector, _ in _embeddings(db, customer, document))
        reports.append((done, total, embedded))

    embed_pieces(db, customer, document, FakeModel(), batch_size=2, on_batch=on_batch)

    assert reports == [(2, 5, 2), (4, 5, 4), (5, 5, 5)]


def test_the_last_fetch_that_finds_nothing_reports_nothing(db: Database) -> None:
    """4 pieces in batches of 2: the second batch is full, so one more fetch finds nothing,
    and that fetch is not a batch."""
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [(f"piece {n}", None) for n in range(4)])
    reports: list[tuple[int, int]] = []

    embed_pieces(
        db, customer, document, FakeModel(), batch_size=2, on_batch=lambda *report: reports.append(report)
    )

    assert reports == [(2, 4), (4, 4)]


def test_a_second_run_does_nothing(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [("one", None), ("two", None)])
    embed_pieces(db, customer, document, FakeModel())
    model = FakeModel()

    assert embed_pieces(db, customer, document, model) == 0
    assert model.calls == []


def test_only_pieces_without_a_vector_are_embedded(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [("kept", axis(9)), ("new", None)])
    other = add_document(db, customer)
    add_pieces(db, customer, other, [("other document", None)])
    model = FakeModel()

    assert embed_pieces(db, customer, document, model) == 1

    assert model.calls == [["new"]]
    assert _embeddings(db, customer, document) == [(True, "test-embed"), (True, "fake-embed")]
    assert _embeddings(db, customer, other) == [(False, None)]


def test_embedded_pieces_are_found_by_meaning(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    first, second = add_pieces(db, customer, document, [("alpha", None), ("beta", None)])
    embed_pieces(db, customer, document, FakeModel())

    hits = run(db, customer, "", embedding=axis(1))

    assert [hit.piece_id for hit in hits] == [second, first]
    assert hits[0].meaning_score == pytest.approx(1.0)


def test_an_unavailable_model_propagates_and_writes_nothing(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [("one", None)])

    with pytest.raises(ModelUnavailable):
        embed_pieces(db, customer, document, FakeModel(fail=True))
    assert _embeddings(db, customer, document) == [(False, None)]


def test_a_wrong_number_of_vectors_is_refused(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [("one", None), ("two", None)])

    with pytest.raises(ModelOutputError):
        embed_pieces(db, customer, document, FakeModel(drop_one=True))
    assert _embeddings(db, customer, document) == [(False, None), (False, None)]


def test_batch_size_must_be_positive(db: Database) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(ValueError, match="batch_size"):
        embed_pieces(db, customer, add_document(db, customer), FakeModel(), batch_size=0)
