"""My words belong to one user: nobody else reaches them, not even in the same customer.

A2 (same customer) and B1 (customer B) use A1's ids with every statement and
plain SQL: they see, change and delete nothing, and A1's rows stay as they were
(A1 is the positive control). ``Database.customer`` (no user: an admin, the
purge) sees and writes none: ``own_user_only`` fails closed. A disabled user's
words stay, out of everyone else's reach.
"""

import uuid

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from kaagaz.answering.words_text import check_meaning
from kaagaz.db import user_words, users
from kaagaz.db.engine import Database
from kaagaz.db.user_words import WordError
from tests.db_support import count_rows, insert_document, new_customer, violation
from tests.test_user_words_db import _SPOT, add, added, new_member, refused

pytestmark = pytest.mark.db

_COUNTS = "SELECT (SELECT count(*) FROM user_words) + (SELECT count(*) FROM user_word_spots)"
_RAW_WORD = text(
    "INSERT INTO user_words (customer_id, user_id, word, word_fold, meaning, meaning_fold, source)"
    " VALUES (:c, :u, 'x', 'x', 'y', 'y', 'manual')"
)


def _a1(db: Database) -> dict[str, str]:
    """Customer A: A1 with two meanings of one word and a spot on page 2, and A2."""
    customer, a1 = new_customer(db)
    word = added(db, customer, a1, "malak", "owner")
    added(db, customer, a1, "malak", "boss", confirm_another=True)
    with db.user(customer, a1) as conn:
        document = insert_document(conn, customer, page_count=3)
        conn.execute(_SPOT, {"c": customer, "u": a1, "w": word.id, "d": document})
    a2 = new_member(db, customer)
    return {"customer": customer, "a1": a1, "a2": a2, "word": word.id, "document": document}


def _state(db: Database, a: dict[str, str]) -> tuple[object, ...]:
    with db.user(a["customer"], a["a1"]) as conn:
        words = conn.execute(text("SELECT * FROM user_words ORDER BY id")).all()
        spots = conn.execute(text("SELECT * FROM user_word_spots ORDER BY id")).all()
    return tuple(words), tuple(spots)


def _reaches_nothing(conn: Connection, customer: str, me: str, a: dict[str, str]) -> None:
    """Every statement with A1's ids, as ``me`` of ``customer``, finds nothing."""
    assert user_words.list_words(conn, customer, me) == []
    assert user_words.delete_word(conn, customer, me, a["word"]) is False
    with pytest.raises(WordError, match="not_found"):
        user_words.update_word(conn, customer, me, a["word"], meaning=check_meaning("thief"))
    assert count_rows(conn, _COUNTS) == 0
    for statement in (
        "UPDATE user_words SET meaning = 'thief' WHERE id = :w",
        "DELETE FROM user_words WHERE id = :w",
        "DELETE FROM user_word_spots WHERE word_id = :w",
    ):
        assert conn.execute(text(statement), {"w": a["word"]}).rowcount == 0


def _write_as_a1_is_denied(db: Database, a: dict[str, str], customer: str, me: str | None) -> None:
    session = db.customer(customer) if me is None else db.user(customer, me)
    with pytest.raises(ProgrammingError) as denied, session as conn:
        conn.execute(_RAW_WORD, {"c": a["customer"], "u": a["a1"]})
    assert violation(denied.value)[0] == "42501"  # a policy's WITH CHECK


def test_a1_is_the_positive_control(db: Database) -> None:
    a = _a1(db)
    with db.user(a["customer"], a["a1"]) as conn:
        listed = [(row.word, row.meaning) for row in user_words.list_words(conn, a["customer"], a["a1"])]
        spots = count_rows(conn, "SELECT count(*) FROM user_word_spots WHERE word_id = :w", {"w": a["word"]})
    assert listed == [("malak", "boss"), ("malak", "owner")]
    assert spots == 1


def test_another_user_of_the_same_customer_reaches_nothing_of_a1s(db: Database) -> None:
    a = _a1(db)
    before = _state(db, a)
    with db.user(a["customer"], a["a2"]) as conn:
        _reaches_nothing(conn, a["customer"], a["a2"], a)
        _reaches_nothing(conn, a["customer"], a["a1"], a)  # even with A1's id as the filter
    # A2's own "malak" asks nothing about A1's meanings; replacing A1's row is not found.
    assert add(db, a["customer"], a["a2"], "malak", "thief").outcome == "created"
    refused("not_found", db, a["customer"], a["a2"], "malak", "thief", replace_id=a["word"])
    with pytest.raises(IntegrityError) as refused_key, db.user(a["customer"], a["a2"]) as conn:
        conn.execute(_SPOT, {"c": a["customer"], "u": a["a2"], "w": a["word"], "d": a["document"]})
    assert violation(refused_key.value) == ("23503", "user_word_spots_customer_id_user_id_word_id_fkey")
    _write_as_a1_is_denied(db, a, a["customer"], a["a2"])
    assert _state(db, a) == before


def test_another_customer_reaches_nothing_of_a1s(db: Database) -> None:
    a = _a1(db)
    b, b1 = new_customer(db)
    before = _state(db, a)
    with db.user(b, b1) as conn:
        _reaches_nothing(conn, b, b1, a)
        _reaches_nothing(conn, a["customer"], a["a1"], a)  # A's ids as the filter: still nothing
    refused("not_found", db, b, b1, "malak", "thief", replace_id=a["word"])
    for customer, me in ((a["customer"], b1), (b, a["a1"])):  # mixed ids: neither pair of policies passes
        with db.user(customer, me) as conn:
            assert count_rows(conn, _COUNTS) == 0
    _write_as_a1_is_denied(db, a, b, b1)
    assert _state(db, a) == before


def test_a_transaction_with_no_user_sees_and_writes_no_word(db: Database) -> None:
    a = _a1(db)
    before = _state(db, a)
    with db.customer(a["customer"]) as conn:
        _reaches_nothing(conn, a["customer"], a["a1"], a)
    _write_as_a1_is_denied(db, a, a["customer"], None)
    stranger = str(uuid.uuid4())  # a user id from nowhere
    with db.user(a["customer"], stranger) as conn:
        _reaches_nothing(conn, a["customer"], stranger, a)
    refused("not_found", db, a["customer"], stranger, "malak", "owner")  # no users row to lock
    assert _state(db, a) == before


def test_a_disabled_users_words_stay_and_stay_out_of_reach(db: Database) -> None:
    a = _a1(db)
    before = _state(db, a)
    with db.customer(a["customer"]) as conn:
        assert users.update_user(conn, a["customer"], a["a1"], role=None, disabled=True)
    assert _state(db, a) == before  # kept: users are only disabled, never deleted
    with db.user(a["customer"], a["a2"]) as conn:
        _reaches_nothing(conn, a["customer"], a["a2"], a)
