"""``python -m kaagaz.cli reembed`` (PRD 3 V4) with a fake model, against a real PostgreSQL.

It clears the vectors whose tag is not the current recipe, then embeds the
pieces with no vector, document by document: it can stop when the model goes
down and go on later, a run with nothing to do calls the model 0 times, and
one customer's run never touches another customer's rows.
"""

import io
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.ai.client import ModelUnavailable
from kaagaz.cli import main
from kaagaz.db import listing
from kaagaz.db.engine import Database
from kaagaz.search.indexing import piece_input, recipe_tag
from tests.db_support import new_customer
from tests.test_indexing_db import FakeModel
from tests.test_search_db import add_document, add_pieces, axis

pytestmark = pytest.mark.db

CURRENT = recipe_tag(FakeModel.embed_model)
_PIECES = text(
    "SELECT id::text, embedding::text, embedding_model, context_header FROM pieces"
    " WHERE customer_id = :c ORDER BY document_id, seq"
)


class FailingModel(FakeModel):
    """Embeds ``good`` batches, then is down."""

    def __init__(self, good: int) -> None:
        super().__init__()
        self._good = good

    def embed(self, texts: list[str]) -> list[list[float]]:
        if len(self.calls) >= self._good:
            self.calls.append(list(texts))
            raise ModelUnavailable("unreachable")
        return super().embed(texts)


def _pieces(db: Database, customer_id: str) -> list[tuple[Any, ...]]:
    with db.customer(customer_id) as conn:
        return [tuple(row) for row in conn.execute(_PIECES, {"c": customer_id})]


def _reembed(db: Database, model: FakeModel, *flags: str) -> tuple[int, str]:
    out = io.StringIO()
    return main(["reembed", *flags], db=db, model=model, out=out), out.getvalue()


def _stale_customer(db: Database) -> str:
    """Two documents: an old-model vector and a piece with none, then a current one."""
    customer, _ = new_customer(db)
    add_pieces(db, customer, add_document(db, customer), [("one", axis(1))], model="old-model")
    add_pieces(db, customer, add_document(db, customer), [("two", None)])
    add_pieces(db, customer, add_document(db, customer), [("three", axis(3))], model=CURRENT)
    return customer


def test_a_run_stopped_by_the_model_goes_on_and_then_has_nothing_to_do(db: Database) -> None:
    customer = _stale_customer(db)
    current_before = [row for row in _pieces(db, customer) if row[2] == CURRENT]

    code, _ = _reembed(db, FailingModel(good=0), "--customer-id", customer)
    assert code == 2
    assert all((row[1] is None) == (row[2] is None) for row in _pieces(db, customer))  # 0001's CHECK

    resumed = FakeModel()
    code, out = _reembed(db, resumed, "--customer-id", customer)
    # Documents go in id order, so the first run may already have cleared "one".
    assert code == 0
    assert out.startswith(f"customer {customer}: 3 documents, ") and out.endswith(", 2 embedded\n")
    assert sorted(call for batch in resumed.calls for call in batch) == ["one", "two"]
    assert {row[2] for row in _pieces(db, customer)} == {CURRENT}
    assert [row for row in _pieces(db, customer) if row[0] == current_before[0][0]] == current_before

    idle = FakeModel()
    assert _reembed(db, idle, "--customer-id", customer) == (
        0,
        f"customer {customer}: 3 documents, 0 cleared, 0 embedded\n",
    )
    assert idle.calls == []


def test_all_embeds_every_piece_again(db: Database) -> None:
    customer = _stale_customer(db)
    model = FakeModel()

    code, out = _reembed(db, model, "--customer-id", customer, "--all")

    assert (code, out) == (0, f"customer {customer}: 3 documents, 2 cleared, 3 embedded\n")
    assert sorted(call for batch in model.calls for call in batch) == ["one", "three", "two"]


def test_one_customers_run_leaves_another_byte_identical(db: Database) -> None:
    a = _stale_customer(db)
    b = _stale_customer(db)
    b_before = _pieces(db, b)

    assert _reembed(db, FakeModel(), "--customer-id", a, "--all")[0] == 0

    assert _pieces(db, b) == b_before
    assert {row[2] for row in _pieces(db, a)} == {CURRENT}


def test_a_malformed_customer_id_stops_with_code_2(db: Database) -> None:
    model = FakeModel()
    assert _reembed(db, model, "--customer-id", "not-a-uuid") == (2, "")
    assert model.calls == []


def test_a_rename_keeps_the_vectors_because_the_name_is_never_embedded(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer, name="bill.pdf")
    add_pieces(db, customer, document, [("Total 5,000", axis(2))])
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "UPDATE pieces SET context_header = 'Document: bill.pdf | Type: invoice | Page 1'"
                " WHERE customer_id = :c AND document_id = :d"
            ),
            {"c": customer, "d": document},
        )
    before = _pieces(db, customer)

    with db.customer(customer) as conn:
        listing.rename_document(conn, customer, document, name="Sharma bill.pdf")

    after = _pieces(db, customer)
    assert after[0][3] == "Document: Sharma bill.pdf | Type: invoice | Page 1"
    assert after[0][1:3] == before[0][1:3]
    assert piece_input(after[0][3], "Total 5,000") == piece_input(before[0][3], "Total 5,000")
