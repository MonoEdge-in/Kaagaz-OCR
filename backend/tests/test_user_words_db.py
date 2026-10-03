"""My words in the database (kaagaz.db.user_words): every rule of adding, changing,
deleting and linking a word, run as the word's own user (``Database.user``).
The concurrency test forces the order with ``wait_until_blocked``, never sleeps.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering.words_text import check_meaning, check_word
from kaagaz.db import user_words
from kaagaz.db.engine import Database
from kaagaz.db.user_words import MAX_MEANINGS, MAX_WORDS, Added, HasMeaning, WordError, WordRow
from tests.db_support import count_rows, insert_document, name_session, new_customer
from tests.db_support import wait_until_blocked as wait

pytestmark = pytest.mark.db

_FILL = text(
    "INSERT INTO user_words (customer_id, user_id, word, word_fold, meaning, meaning_fold, source)"
    " SELECT :c, :u, 'w' || n, 'w' || n, 'm', 'm', 'manual' FROM generate_series(1, :n) AS n"
)
_SPOT = text(
    "INSERT INTO user_word_spots (customer_id, user_id, word_id, document_id, page_no, region) VALUES"
    """ (:c, :u, :w, :d, 2, '{"kind": "box", "page": 2, "box": [0.1, 0.2, 0.3, 0.25]}')"""
)
_MEMBER = text(
    "INSERT INTO users (customer_id, email, display_name, role, password_hash)"
    " VALUES (:c, :e, 'Member', 'member', 'not-a-real-hash') RETURNING id"
)
_SPOTS_OF = "SELECT count(*) FROM user_word_spots WHERE document_id = :d"


def new_member(db: Database, customer: str) -> str:
    """A second user (a member) in ``customer``. Returns the user's id."""
    with db.customer(customer) as conn:
        return str(
            conn.execute(_MEMBER, {"c": customer, "e": f"m-{uuid.uuid4().hex}@example.test"}).scalar_one()
        )


def add(db: Database, customer: str, user: str, word: str, meaning: str, **options: Any) -> Any:
    """``add_word`` in its own transaction, with the text checked as the API does."""
    options.setdefault("source", "manual")
    with db.user(customer, user) as conn:
        return user_words.add_word(
            conn, customer, user, word=check_word(word).word, meaning=check_meaning(meaning), **options
        )


def added(db: Database, customer: str, user: str, word: str, meaning: str, **options: Any) -> WordRow:
    result = add(db, customer, user, word, meaning, **options)
    assert isinstance(result, Added)
    assert result.outcome == "created"
    return result.word


def refused(
    code: str, db: Database, customer: str, user: str, word: str, meaning: str, **options: Any
) -> None:
    with pytest.raises(WordError) as error:
        add(db, customer, user, word, meaning, **options)
    assert error.value.code == code


def meanings(db: Database, customer: str, user: str) -> list[tuple[str, str]]:
    with db.user(customer, user) as conn:
        return [(row.word, row.meaning) for row in user_words.list_words(conn, customer, user)]


def test_words_are_created_and_listed_by_word_then_meaning(db: Database) -> None:
    customer, user = new_customer(db)
    malak = added(db, customer, user, "  Malak ", "owner")
    added(db, customer, user, "bojha", "mortgage charge", source="upload")
    added(db, customer, user, "Malak", "boss", confirm_another=True)
    assert (malak.word, malak.meaning, malak.source, malak.as_read) == ("Malak", "owner", "manual", None)
    assert meanings(db, customer, user) == [
        ("bojha", "mortgage charge"),
        ("Malak", "boss"),
        ("Malak", "owner"),
    ]


def test_the_same_word_and_meaning_is_already_and_another_meaning_asks_first(db: Database) -> None:
    customer, user = new_customer(db)
    boss = added(db, customer, user, "malak", "boss")
    assert add(db, customer, user, "MALAK", " Boss ") == Added(word=boss, outcome="already")
    assert add(db, customer, user, "Malak", "owner") == HasMeaning(existing=[boss])
    assert meanings(db, customer, user) == [("malak", "boss")]  # nothing written
    added(db, customer, user, "malak", "owner", confirm_another=True)
    assert meanings(db, customer, user) == [("malak", "boss"), ("malak", "owner")]


@pytest.mark.parametrize("confirm_another", [False, True])
def test_a_fourth_meaning_is_too_many_even_before_the_question(db: Database, confirm_another: bool) -> None:
    customer, user = new_customer(db)
    for meaning in ("boss", "owner", "holder")[:MAX_MEANINGS]:
        added(db, customer, user, "malak", meaning, confirm_another=True)
    refused("too_many_meanings", db, customer, user, "malak", "master", confirm_another=confirm_another)
    assert add(db, customer, user, "malak", "owner").outcome == "already"


def test_the_501st_word_is_too_many_but_an_exact_one_is_still_already(db: Database) -> None:
    customer, user = new_customer(db)
    with db.user(customer, user) as conn:
        conn.execute(_FILL, {"c": customer, "u": user, "n": MAX_WORDS})
    refused("too_many_words", db, customer, user, "malak", "owner")
    assert add(db, customer, user, "w7", "m").outcome == "already"
    assert len(meanings(db, customer, user)) == MAX_WORDS


def test_replace_changes_that_meaning_only_of_the_same_word(db: Database) -> None:
    customer, user = new_customer(db)
    boss = added(db, customer, user, "malak", "boss")
    holder = added(db, customer, user, "malak", "holder", confirm_another=True)
    result = add(db, customer, user, "Malak", "owner", replace_id=boss.id)
    assert (result.outcome, result.word.id, result.word.meaning) == ("replaced", boss.id, "owner")
    refused("conflict", db, customer, user, "malak", "holder", replace_id=boss.id)
    refused("not_same_word", db, customer, user, "bojha", "owner", replace_id=holder.id)
    refused("not_found", db, customer, user, "malak", "owner", replace_id=str(uuid.uuid4()))
    assert meanings(db, customer, user) == [("malak", "holder"), ("Malak", "owner")]


def test_update_changes_the_word_or_the_meaning_by_the_same_rules(db: Database) -> None:
    customer, user = new_customer(db)
    for meaning in ("boss", "owner", "holder"):
        added(db, customer, user, "malak", meaning, confirm_another=True)
    bojha = added(db, customer, user, "bojha", "boss")
    with db.user(customer, user) as conn:
        changed = user_words.update_word(conn, customer, user, bojha.id, meaning=check_meaning("charge"))
        renamed = user_words.update_word(conn, customer, user, bojha.id, word=check_word("Bojaa").word)
    assert isinstance(changed, WordRow)
    assert isinstance(renamed, WordRow)
    assert (changed.id, changed.word, changed.meaning) == (bojha.id, "bojha", "charge")
    assert (renamed.word, renamed.meaning) == ("Bojaa", "charge")
    malak = check_word("malak").word
    cases: list[tuple[str, str, dict[str, Any]]] = [
        ("conflict", bojha.id, {"word": malak, "meaning": check_meaning("boss")}),
        ("too_many_meanings", bojha.id, {"word": malak}),
        ("not_found", str(uuid.uuid4()), {"meaning": check_meaning("x")}),
    ]
    for code, word_id, change in cases:
        with pytest.raises(WordError) as error, db.user(customer, user) as conn:
            user_words.update_word(conn, customer, user, word_id, **change)
        assert error.value.code == code
    assert ("Bojaa", "charge") in meanings(db, customer, user)


def test_a_word_or_a_document_deleted_takes_its_spots(db: Database) -> None:
    customer, user = new_customer(db)
    malak, bojha = added(db, customer, user, "malak", "owner"), added(db, customer, user, "bojha", "charge")
    with db.user(customer, user) as conn:
        document = insert_document(conn, customer)
        for word_id in (malak.id, bojha.id):
            conn.execute(_SPOT, {"c": customer, "u": user, "w": word_id, "d": document})
        assert user_words.delete_word(conn, customer, user, bojha.id) is True
        assert user_words.delete_word(conn, customer, user, bojha.id) is False
        assert count_rows(conn, _SPOTS_OF, {"d": document}) == 1
    with db.customer(customer) as conn:  # a purge works as the customer, with no user
        conn.execute(
            text("DELETE FROM documents WHERE customer_id = :c AND id = :d"), {"c": customer, "d": document}
        )
    with db.user(customer, user) as conn:
        assert count_rows(conn, _SPOTS_OF, {"d": document}) == 0
    assert meanings(db, customer, user) == [("malak", "owner")]  # the word stays


def test_spots_are_kept_once_and_listed_per_page_with_their_word(db: Database) -> None:
    customer, user = new_customer(db)
    malak, bojha = added(db, customer, user, "malak", "owner"), added(db, customer, user, "bojha", "charge")
    box, cell = (
        {"kind": "box", "page": 2, "box": [0.1, 0.2, 0.3, 0.25]},
        {"kind": "cell", "page": 2, "row": 4, "column": 3},
    )
    with db.user(customer, user) as conn:
        document, other = insert_document(conn, customer), insert_document(conn, customer)
        for word_id, document_id, page, region in (
            (malak.id, document, 2, box),
            (malak.id, document, 2, box),  # the same spot again
            (bojha.id, document, 2, cell),
            (malak.id, document, None, None),  # the whole document, twice
            (malak.id, document, None, None),
            (malak.id, other, 2, box),
        ):
            user_words.add_spot(
                conn, customer, user, word_id=word_id, document_id=document_id, page_no=page, region=region
            )
        on_page = user_words.spots_on_page(conn, customer, user, document, 2)
        assert count_rows(conn, _SPOTS_OF, {"d": document}) == 3
        assert user_words.spots_on_page(conn, customer, user, document, 1) == []
    # Written in one transaction, so created_at ties: then by word, never by the random ids.
    assert [(s.word_id, s.word, s.meaning, s.page_no, s.region) for s in on_page] == [
        (bojha.id, "bojha", "charge", 2, cell),
        (malak.id, "malak", "owner", 2, box),
    ]


def test_two_adds_of_one_user_wait_for_each_other(db: Database) -> None:
    """The second add waits on the user lock, then finds the first one's row."""
    customer, user = new_customer(db)
    session = f"words-{uuid.uuid4().hex[:16]}"
    word, meaning = check_word("malak").word, check_meaning("owner")

    def add_the_same_word() -> Any:
        with db.user(customer, user) as conn:
            name_session(conn, session)
            return user_words.add_word(conn, customer, user, word=word, meaning=meaning, source="manual")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.user(customer, user) as conn:
            first = user_words.add_word(conn, customer, user, word=word, meaning=meaning, source="manual")
            later = pool.submit(add_the_same_word)
            wait(db, application_name=session)
        assert isinstance(first, Added)
        assert later.result(timeout=10) == Added(word=first.word, outcome="already")


def test_a_delete_waits_for_an_add_of_the_same_user_so_its_spot_never_fails(db: Database) -> None:
    """Without the user lock in delete_word, the delete would remove the word between
    the add's read and its spot, and the spot's key would fail (a 500)."""
    customer, user = new_customer(db)
    malak = added(db, customer, user, "malak", "owner")
    session = f"words-{uuid.uuid4().hex[:16]}"
    with db.user(customer, user) as conn:
        document = insert_document(conn, customer)

    def delete_it() -> bool:
        with db.user(customer, user) as conn:
            name_session(conn, session)
            return user_words.delete_word(conn, customer, user, malak.id)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.user(customer, user) as conn:
            again = user_words.add_word(
                conn,
                customer,
                user,
                word=check_word("malak").word,
                meaning=check_meaning("owner"),
                source="upload",
            )
            later = pool.submit(delete_it)
            wait(db, application_name=session)
            user_words.add_spot(conn, customer, user, word_id=malak.id, document_id=document)
        assert again == Added(word=malak, outcome="already")
        assert later.result(timeout=10) is True
    assert meanings(db, customer, user) == []


def test_a_malformed_user_id_fails_before_any_policy(db: Database) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(ValueError), db.user(customer, "not-a-uuid"):
        pass
