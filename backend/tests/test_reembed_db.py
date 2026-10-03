"""``python -m kaagaz.cli reembed`` (PRD 3 V4) with a fake model, against a real PostgreSQL.

It clears the vectors whose tag is not the current recipe, then embeds the
pieces with no vector, document by document: it can stop when the model goes
down and go on later, a run with nothing to do embeds no piece (only the
probe that checks the model answers before anything is cleared), and
one customer's run never touches another customer's rows. Then the
customer's stories with no vector or another tag are embedded, each as its user.
"""

import io
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.ai.client import ModelUnavailable
from kaagaz.cli import REEMBED_PROBE, main
from kaagaz.db import document_stories, listing
from kaagaz.db.engine import Database
from kaagaz.search.indexing import piece_input, recipe_tag
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer
from tests.test_document_stories_db import embed, put
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

    code, _ = _reembed(db, FailingModel(good=1), "--customer-id", customer)  # the probe, then down
    assert code == 2
    assert all((row[1] is None) == (row[2] is None) for row in _pieces(db, customer))  # 0001's CHECK

    resumed = FakeModel()
    code, out = _reembed(db, resumed, "--customer-id", customer)
    # Documents go in id order, so the first run may already have cleared "one".
    assert code == 0
    assert out.startswith(f"customer {customer}: 3 documents, ") and out.endswith(", 2 embedded, 0 stories\n")
    assert sorted(call for batch in resumed.calls[1:] for call in batch) == ["one", "two"]
    assert {row[2] for row in _pieces(db, customer)} == {CURRENT}
    assert [row for row in _pieces(db, customer) if row[0] == current_before[0][0]] == current_before

    idle = FakeModel()
    assert _reembed(db, idle, "--customer-id", customer) == (
        0,
        f"customer {customer}: 3 documents, 0 cleared, 0 embedded, 0 stories\n",
    )
    assert idle.calls == [[REEMBED_PROBE]]


def test_all_embeds_every_piece_again(db: Database) -> None:
    customer = _stale_customer(db)
    model = FakeModel()

    code, out = _reembed(db, model, "--customer-id", customer, "--all")

    assert (code, out) == (0, f"customer {customer}: 3 documents, 2 cleared, 3 embedded, 0 stories\n")
    assert sorted(call for batch in model.calls[1:] for call in batch) == ["one", "three", "two"]


def test_one_customers_run_leaves_another_byte_identical(db: Database) -> None:
    a = _stale_customer(db)
    b = _stale_customer(db)
    b_before = _pieces(db, b)

    assert _reembed(db, FakeModel(), "--customer-id", a, "--all")[0] == 0

    assert _pieces(db, b) == b_before
    assert {row[2] for row in _pieces(db, a)} == {CURRENT}


def test_stories_without_a_current_vector_are_embedded_and_others_left(db: Database) -> None:
    customer, user = new_customer(db)
    other, other_user = new_customer(db)
    documents = []
    for owner, me, story in (
        (customer, user, "no vector"),
        (customer, user, "old tag"),
        (other, other_user, "b"),
    ):
        with db.customer(owner) as conn:
            documents.append(insert_document(conn, owner))
        put(db, owner, me, documents[-1], story)
    assert embed(db, customer, user, documents[1], "old tag")  # tagged "m#story1"
    model = FakeModel()

    code, out = _reembed(db, model, "--customer-id", customer)

    assert (code, out) == (0, f"customer {customer}: 0 documents, 0 cleared, 0 embedded, 2 stories\n")
    assert sorted(model.calls[1]) == ["no vector", "old tag"]  # one pass, after the probe
    tags = text("SELECT embedding_model FROM document_stories")
    with db.user(customer, user) as conn:
        assert {row[0] for row in conn.execute(tags)} == {document_stories.recipe_tag(model.embed_model)}
    with db.user(other, other_user) as conn:
        assert [row[0] for row in conn.execute(tags)] == [None]  # another customer: untouched


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


def test_a_model_already_down_stops_reembed_before_anything_is_cleared(db: Database) -> None:
    customer = _stale_customer(db)
    before = _pieces(db, customer)
    model = FailingModel(good=0)

    assert _reembed(db, model, "--customer-id", customer, "--all") == (2, "")

    assert model.calls == [[REEMBED_PROBE]]
    assert _pieces(db, customer) == before


fresh = m0002.fresh  # the fixture, by its name


def test_with_no_customer_id_every_customer_is_reembedded(fresh: tuple[str, Database]) -> None:
    """A fresh database, so "every customer" is exactly these two."""
    url, database = fresh
    migrate(url, "head")
    customers = sorted([_stale_customer(database), _stale_customer(database)])

    code, out = _reembed(database, FakeModel())

    assert code == 0
    lines = out.splitlines()
    assert [line.split(":")[0] for line in lines] == [f"customer {c}" for c in customers]
    assert all(line.endswith(", 2 embedded, 0 stories") for line in lines)
    for customer in customers:
        assert {row[2] for row in _pieces(database, customer)} == {CURRENT}
