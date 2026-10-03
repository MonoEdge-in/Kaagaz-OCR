"""A story belongs to one user: nobody else reaches it, not even in the same customer.

A2 (same customer) and B1 (customer B) use A1's ids with every statement and
plain SQL: they read, change, embed and delete nothing, and A1's row stays as it
was (A1 is the positive control). ``Database.customer`` (no user: an admin, the
purge) sees and writes none: ``own_user_only`` fails closed.
"""

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import ProgrammingError

from kaagaz.db import document_stories as stories
from kaagaz.db.engine import Database
from tests.db_support import count_rows, new_customer, violation
from tests.test_document_stories_db import STORY, VECTOR, _family, embed, get, put
from tests.test_user_words_db import new_member

pytestmark = pytest.mark.db

_RAW_STORY = text(
    "INSERT INTO document_stories (customer_id, user_id, family_id, story, story_latin)"
    " VALUES (:c, :u, :f, 'x', 'x')"
)


def _a1(db: Database) -> dict[str, str]:
    """Customer A: A1's embedded story on a family of two versions, and A2."""
    customer, a1 = new_customer(db)
    first, second = _family(db, customer)
    put(db, customer, a1, second)
    assert embed(db, customer, a1, first, STORY)
    return {"customer": customer, "a1": a1, "a2": new_member(db, customer), "first": first, "second": second}


def _state(db: Database, a: dict[str, str]) -> tuple[object, ...]:
    with db.user(a["customer"], a["a1"]) as conn:
        return tuple(conn.execute(text("SELECT * FROM document_stories ORDER BY family_id")).all())


def _reaches_nothing(conn: Connection, customer: str, me: str, a: dict[str, str]) -> None:
    """Every statement with A1's ids, as ``me`` of ``customer``, finds nothing."""
    for document in (a["first"], a["second"]):
        assert stories.get_story(conn, customer, me, document) is None
        assert stories.delete_story(conn, customer, me, document) is False
    hash_ = stories.story_hash(STORY)
    for user in (me, a["a1"]):
        written = stories.set_embedding(
            conn, customer, user, a["first"], expected_hash=hash_, vector=VECTOR, model_embed="evil"
        )
        assert written is False
    assert count_rows(conn, "SELECT count(*) FROM document_stories") == 0
    for statement in (
        "UPDATE document_stories SET story = 'stolen' WHERE family_id = :f",
        "UPDATE document_stories SET embedding = NULL, embedding_model = NULL WHERE family_id = :f",
        "DELETE FROM document_stories WHERE family_id = :f",
    ):
        assert conn.execute(text(statement), {"f": a["first"]}).rowcount == 0


def _write_as_a1_is_denied(db: Database, a: dict[str, str], customer: str, me: str | None) -> None:
    session = db.customer(customer) if me is None else db.user(customer, me)
    with pytest.raises(ProgrammingError) as denied, session as conn:
        conn.execute(_RAW_STORY, {"c": a["customer"], "u": a["a1"], "f": a["first"]})
    assert violation(denied.value)[0] == "42501"  # a policy's WITH CHECK


def test_a1_is_the_positive_control(db: Database) -> None:
    a = _a1(db)
    row = get(db, a["customer"], a["a1"], a["first"])
    assert row is not None
    assert (row.family_id, row.story, row.embedding_model) == (a["first"], STORY, "m#story1")


def test_another_user_of_the_same_customer_reaches_nothing_of_a1s(db: Database) -> None:
    a = _a1(db)
    before = _state(db, a)
    with db.user(a["customer"], a["a2"]) as conn:
        _reaches_nothing(conn, a["customer"], a["a2"], a)
        _reaches_nothing(conn, a["customer"], a["a1"], a)  # even with A1's id as the filter
    mine = put(db, a["customer"], a["a2"], a["first"], "my own note")  # A2's own story: its own row
    assert mine is not None
    assert mine.story == "my own note"
    _write_as_a1_is_denied(db, a, a["customer"], a["a2"])
    assert _state(db, a) == before


def test_another_customer_reaches_nothing_of_a1s(db: Database) -> None:
    a = _a1(db)
    b, b1 = new_customer(db)
    before = _state(db, a)
    with db.user(b, b1) as conn:
        _reaches_nothing(conn, b, b1, a)
        _reaches_nothing(conn, a["customer"], a["a1"], a)  # A's ids as the filter: still nothing
    assert put(db, b, b1, a["first"]) is None  # A's document is not B's: no story is written
    for customer, me in ((a["customer"], b1), (b, a["a1"])):  # mixed ids: neither pair of policies passes
        with db.user(customer, me) as conn:
            assert count_rows(conn, "SELECT count(*) FROM document_stories") == 0
    _write_as_a1_is_denied(db, a, b, b1)
    assert _state(db, a) == before


def test_a_transaction_with_no_user_sees_and_writes_no_story(db: Database) -> None:
    a = _a1(db)
    before = _state(db, a)
    with db.customer(a["customer"]) as conn:
        _reaches_nothing(conn, a["customer"], a["a1"], a)
    _write_as_a1_is_denied(db, a, a["customer"], None)
    assert _state(db, a) == before
