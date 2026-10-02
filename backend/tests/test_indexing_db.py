"""``embed_pieces`` with a fake model, against a real PostgreSQL, and what it embeds (PRD 3 V3)."""

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.chunking.context import context_header
from kaagaz.chunking.piece import Piece
from kaagaz.db.engine import Database
from kaagaz.reading.spans import BoxLocation
from kaagaz.search.indexing import embed_pieces, piece_input, query_input, recipe_tag
from tests.db_support import new_customer
from tests.test_search_db import RECIPE, add_document, add_pieces, axis, run

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
    assert _embeddings(db, customer, document) == [(True, "fake-embed#ctx1")] * 5


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
    assert _embeddings(db, customer, document) == [(True, RECIPE), (True, "fake-embed#ctx1")]
    assert _embeddings(db, customer, other) == [(False, None)]


def test_embedded_pieces_are_found_by_meaning(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    first, second = add_pieces(db, customer, document, [("alpha", None), ("beta", None)])
    embed_pieces(db, customer, document, FakeModel())

    hits = run(db, customer, "", embedding=axis(1), recipe=recipe_tag(FakeModel.embed_model))

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


def test_a_piece_is_embedded_with_its_reduced_header_and_no_instruction(db: Database) -> None:
    """PRD 3 V3: Type, Date and Section only; never "Before:", the name or the pages."""
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    _, second = add_pieces(db, customer, document, [("Revenue was $60,500.", None), ("$12,000", None)])
    location = BoxLocation(15, 0.1, 0.1, 0.9, 0.12)
    header = context_header(
        Piece(customer, document, "$12,000", (location,), "verified", section=("Monthly results", "Profit")),
        document_name="Sept report.pdf",
        doc_type="other",
        document_date=date(2026, 9, 30),
        before="Revenue was $60,500.",
        sheet=None,
    )
    with db.customer(customer) as conn:
        conn.execute(
            text("UPDATE pieces SET context_header = :h WHERE customer_id = :c AND id = :id"),
            {"h": header, "c": customer, "id": second},
        )
    model = FakeModel()

    embed_pieces(db, customer, document, model)

    assert "Before: " in header and "Document: " in header  # the stored header keeps them
    assert model.calls == [
        [
            "Revenue was $60,500.",
            "Type: other | Date: 2026-09-30 | Section: Monthly results > Profit\n\n$12,000",
        ]
    ]
    for embedded in model.calls[0]:
        assert not embedded.startswith("Instruct:")
        assert "Before:" not in embedded and "Sept report" not in embedded


def test_the_query_and_piece_inputs_follow_the_model_card() -> None:
    assert query_input("total of INV-1042") == (
        "Instruct: Given a question about business documents, retrieve passages that answer it"
        "\nQuery:total of INV-1042"
    )
    assert piece_input("", "text only") == "text only"
    assert piece_input("Document: a.pdf | Sheet 2", "cells") == "cells"
