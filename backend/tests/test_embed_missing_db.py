"""``embed_missing``, the worker's missing-vector pass, with a fake model, against a real PostgreSQL.

It fills the pieces left without a vector, only in ready, current documents out
of Trash; a second pass does nothing; a pass embeds at most its limit, in
batches of 32, and the next one goes on; a bad document is skipped and the rest
still get their vectors; a model that is down stops the pass.

The pass walks every customer in the database, so each test first drains what
earlier tests left without a vector, and then counts only its own pieces.
"""

import math
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.search.indexing import MissingPass, embed_missing, recipe_tag
from tests.db_support import insert_document, new_customer
from tests.test_indexing_db import FakeModel
from tests.test_search_db import RECIPE, add_document, add_pieces, axis

pytestmark = pytest.mark.db

CURRENT = recipe_tag(FakeModel.embed_model)


class SameVector(FakeModel):
    """Embeds every text as ``axis(0)``, or as NaN when it is ``bad``; records each call."""

    def __init__(self, bad: str | None = None) -> None:
        super().__init__()
        self._bad = bad

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[math.nan] * len(axis(0)) if item == self._bad else axis(0) for item in texts]


def drain(db: Database) -> None:
    """Give every piece earlier tests left without a vector one, so a test sees only its own."""
    assert embed_missing(db, SameVector(), None, limit=1_000_000).skipped is None


def tags(db: Database, customer_id: str, document_id: str) -> list[str | None]:
    """Each piece's ``embedding_model``, in seq order (NULL with no vector)."""
    with db.customer(customer_id) as conn:
        found = conn.execute(
            text(
                "SELECT embedding_model FROM pieces WHERE customer_id = :c AND document_id = :d ORDER BY seq"
            ),
            {"c": customer_id, "d": document_id},
        )
        return [row[0] for row in found]


def test_missing_vectors_are_filled_and_a_second_pass_does_nothing(db: Database) -> None:
    drain(db)
    customer, _ = new_customer(db)
    first = add_document(db, customer)
    add_pieces(db, customer, first, [("one", None), ("kept", axis(5)), ("two", None)])
    second = add_document(db, customer)
    add_pieces(db, customer, second, [("three", None)])
    model = SameVector()

    assert embed_missing(db, model, None) == MissingPass(3, None)

    assert sorted(item for call in model.calls for item in call) == ["one", "three", "two"]
    assert tags(db, customer, first) == [CURRENT, RECIPE, CURRENT]
    assert tags(db, customer, second) == [CURRENT]
    idle = SameVector()
    assert embed_missing(db, idle, None) == MissingPass(0, None)
    assert idle.calls == []


def test_trashed_not_ready_and_older_version_pieces_are_left_alone(db: Database) -> None:
    drain(db)
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        trashed = insert_document(conn, customer, deleted_at=datetime(2026, 9, 30, tzinfo=UTC))
        older = insert_document(conn, customer, is_current=False)
        newer = insert_document(conn, customer, family_id=older, version_no=2)
    reading = add_document(db, customer, status="clean")
    for document in (trashed, older, reading, newer):
        add_pieces(db, customer, document, [(f"piece of {document}", None)])
    model = SameVector()

    assert embed_missing(db, model, None) == MissingPass(1, None)

    assert model.calls == [[f"piece of {newer}"]]  # the positive control: the current version
    for document in (trashed, older, reading):
        assert tags(db, customer, document) == [None], document
    assert tags(db, customer, newer) == [CURRENT]


def test_a_pass_embeds_at_most_its_limit_in_batches_of_32_and_the_next_goes_on(db: Database) -> None:
    drain(db)
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [(f"piece {n}", None) for n in range(100)])
    model = SameVector()

    assert embed_missing(db, model, None, limit=64) == MissingPass(64, None)  # the cursor stays
    assert [len(call) for call in model.calls] == [32, 32]
    assert tags(db, customer, document).count(None) == 36

    model.calls.clear()
    assert embed_missing(db, model, None, limit=64) == MissingPass(36, None)
    assert [len(call) for call in model.calls] == [32, 4]
    assert tags(db, customer, document) == [CURRENT] * 100


def _two_documents(db: Database, customer_id: str, first_text: str) -> tuple[str, str]:
    """Two ready documents in id order, one piece each without a vector: ``first_text``, then "fine"."""
    low, high = sorted(str(uuid.uuid4()) for _ in range(2))
    with db.customer(customer_id) as conn:
        insert_document(conn, customer_id, id=low)
        insert_document(conn, customer_id, id=high)
    add_pieces(db, customer_id, low, [(first_text, None)])
    add_pieces(db, customer_id, high, [("fine", None)])
    return low, high


def test_a_nan_vector_skips_its_document_and_the_next_pass_goes_on_from_the_cursor(db: Database) -> None:
    drain(db)
    customer, _ = new_customer(db)
    bad, good = _two_documents(db, customer, "bad piece")
    model = SameVector(bad="bad piece")  # a later drain's model embeds it fine

    assert embed_missing(db, model, None) == MissingPass(0, (customer, bad), (customer, bad, "bad_vector"))
    assert embed_missing(db, model, (customer, bad)) == MissingPass(1, None)
    assert tags(db, customer, good) == [CURRENT]
    assert tags(db, customer, bad) == [None]
    # A full round later, the bad document is tried again (and skipped again).
    assert embed_missing(db, model, None).skipped == (customer, bad, "bad_vector")


def test_a_wrong_number_of_vectors_skips_its_document_with_the_model_code(db: Database) -> None:
    drain(db)
    customer, _ = new_customer(db)
    first, _ = _two_documents(db, customer, "one")

    done = embed_missing(db, FakeModel(drop_one=True), None)

    assert done == MissingPass(0, (customer, first), (customer, first, "embedding_count"))
    assert tags(db, customer, first) == [None]


def test_a_model_that_is_down_stops_the_pass_and_keeps_the_cursor(db: Database) -> None:
    drain(db)
    customer, _ = new_customer(db)
    first, second = _two_documents(db, customer, "one")
    model = FakeModel(fail=True)

    assert embed_missing(db, model, (customer, first)) == MissingPass(
        0, (customer, first), down="unreachable"
    )

    assert model.calls == [["fine"]]  # it started after the cursor
    assert tags(db, customer, first) == tags(db, customer, second) == [None]
