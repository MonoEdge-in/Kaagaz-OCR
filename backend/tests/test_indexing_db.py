"""``embed_pieces`` with a fake model, against a real PostgreSQL, and what it embeds (PRD 3 V3)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError

from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.chunking.context import context_header
from kaagaz.chunking.piece import Piece
from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading.spans import BoxLocation
from kaagaz.search.indexing import embed_pieces, piece_input, recipe_tag
from tests.db_support import APP_ROLE, new_customer
from tests.test_search_db import RECIPE, add_document, add_pieces, axis, run

pytestmark = pytest.mark.db

CURRENT = recipe_tag("fake-embed")  # FakeModel's tag


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
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
        model: str | None = None,
        thinking: str | None = None,
        provider: str | None = None,
        deadline: float | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        raise AssertionError("indexing never chats")


class GoesDown(Database):
    """The test database, until transaction number ``down_at``: from then on the server
    cannot be reached (OperationalError), as in an outage."""

    def __init__(self, db: Database, down_at: int) -> None:
        super().__init__(db.engine, APP_ROLE)
        self._left = down_at - 1

    @contextmanager
    def customer(self, customer_id: str) -> Iterator[Connection]:
        if self._left == 0:
            raise OperationalError("SELECT 1", {}, ConnectionRefusedError())
        self._left -= 1
        with super().customer(customer_id) as conn:
            yield conn


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


def test_only_pieces_without_a_vector_of_the_current_tag_are_embedded(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [("kept", axis(9)), ("new", None)], model=CURRENT)
    other = add_document(db, customer)
    add_pieces(db, customer, other, [("other document", None)])
    model = FakeModel()

    assert embed_pieces(db, customer, document, model) == 1

    assert model.calls == [["new"]]
    assert _embeddings(db, customer, document) == [(True, CURRENT), (True, CURRENT)]
    assert _embeddings(db, customer, other) == [(False, None)]


def test_m5_a_stale_tag_is_embedded_again_and_then_left(db: Database) -> None:
    """PRD 5 M5: a vector of another model or recipe counts as missing."""
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [("old one", axis(9)), ("old two", axis(8))], model=RECIPE)
    model = FakeModel()

    assert RECIPE != CURRENT
    assert embed_pieces(db, customer, document, model) == 2
    assert model.calls == [["old one", "old two"]]
    assert _embeddings(db, customer, document) == [(True, CURRENT), (True, CURRENT)]
    idle = FakeModel()
    assert embed_pieces(db, customer, document, idle) == 0
    assert idle.calls == []


def test_m5_a_search_over_mixed_tags_sees_only_the_current_tag(db: Database) -> None:
    customer, _ = new_customer(db)
    (stale,) = add_pieces(db, customer, add_document(db, customer), [("stale", axis(1))], model=RECIPE)
    (current,) = add_pieces(db, customer, add_document(db, customer), [("current", axis(1))], model=CURRENT)

    assert [hit.piece_id for hit in run(db, customer, "", embedding=axis(1), recipe=CURRENT)] == [current]
    assert [hit.piece_id for hit in run(db, customer, "", embedding=axis(1), recipe=RECIPE)] == [stale]


def _add_sections(db: Database, customer_id: str, document_id: str, summaries: list[str | None]) -> None:
    """Sections 0, 1, ... each over piece 0, with a path, a summary (or none) and no vector."""
    with db.customer(customer_id) as conn:
        conn.execute(
            text(
                "INSERT INTO document_sections (customer_id, document_id, seq, path, page_from, page_to,"
                " piece_seq_from, piece_seq_to, summary) VALUES (:c, :d, :seq, CAST(:path AS text[]), 1, 1,"
                " 0, 0, :summary)"
            ),
            [
                {"c": customer_id, "d": document_id, "seq": seq, "path": ["Leave"], "summary": summary}
                for seq, summary in enumerate(summaries)
            ],
        )


def _section_tags(db: Database, customer_id: str, document_id: str) -> list[str | None]:
    with db.customer(customer_id) as conn:
        found = conn.execute(
            text(
                "SELECT embed_recipe FROM document_sections WHERE customer_id = :c AND document_id = :d"
                " ORDER BY seq"
            ),
            {"c": customer_id, "d": document_id},
        )
        return [row[0] for row in found]


SECTION_INPUT = "Section: Leave\n\nHolidays and casual leave."


def test_cx2_section_summaries_are_embedded_after_the_pieces_with_their_tag(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [("Twelve holidays.", None), ("Eight days.", None)])
    _add_sections(db, customer, document, ["Holidays and casual leave.", None])
    reports: list[tuple[int, int]] = []
    model = FakeModel()

    written = embed_pieces(
        db, customer, document, model, batch_size=2, on_batch=lambda *report: reports.append(report)
    )

    assert written == 3
    assert model.calls == [["Twelve holidays.", "Eight days."], [SECTION_INPUT]]
    assert reports == [(2, 3), (3, 3)]
    assert _section_tags(db, customer, document) == [CURRENT, None]  # no summary, no vector
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "UPDATE document_sections SET embed_recipe = :old"
                " WHERE customer_id = :c AND document_id = :d AND seq = 0"
            ),
            {"c": customer, "d": document, "old": RECIPE},
        )
    again = FakeModel()
    assert embed_pieces(db, customer, document, again) == 1  # the stale section only
    assert again.calls == [[SECTION_INPUT]]
    assert _section_tags(db, customer, document) == [CURRENT, None]


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


@pytest.mark.parametrize("down_at", [1, 2, 3])  # the count, the first fetch, the first write
def test_a_database_outage_is_backend_unavailable(db: Database, down_at: int) -> None:
    """So the pipeline ends the run database_unavailable and the job is retried later,
    rather than an internal error that also stops the worker."""
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [("one", None), ("two", None)])

    with pytest.raises((BackendUnavailable, OperationalError)) as caught:
        embed_pieces(GoesDown(db, down_at), customer, document, FakeModel())
    assert isinstance(caught.value, BackendUnavailable)
    assert str(caught.value) == "database"


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


def test_the_piece_input_drops_the_document_name_and_pages() -> None:
    assert piece_input("", "text only") == "text only"
    assert piece_input("Document: a.pdf | Sheet 2", "cells") == "cells"
