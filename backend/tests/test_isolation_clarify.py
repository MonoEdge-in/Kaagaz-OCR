"""PRD 6 B1 against a real database: one company's names, places and properties are never
offered or matched for another, even identical ones; nor is Trash or an older version's."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.answering.clarify import Entity, Pending, PgVocabulary, after_reply, outcome
from kaagaz.db.engine import Database
from tests.db_support import ensure_value_keys, insert_document, insert_key, new_customer

pytestmark = pytest.mark.db

ASKED = [Entity("person", "Nikhil"), Entity("place", "Puny"), Entity("place", "Punewadi")]


def company(db: Database, *printed: tuple[str, str]) -> tuple[str, str]:
    """A company with one live document printing each (kind, name), Punewadi only in Trash and
    Pimpri only on an older version; its id and its live document."""
    ensure_value_keys(db)
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
        binned = insert_document(conn, customer, deleted_at=datetime(2026, 10, 1, tzinfo=UTC))
        older = insert_document(conn, customer, is_current=False)
        for at, kind, name in [*((document, *each) for each in printed), (binned, "place", "Punewadi")]:
            insert_key(conn, customer, at, kind, name)
        insert_key(conn, customer, older, "place", "Pimpri")
    return customer, document


def test_b_is_never_offered_as_names_or_places_even_identical_ones(db: Database) -> None:
    shared = (("person", "Nikhil Varde"), ("place", "Pune"), ("property", "Survey 45/2"))
    a, _ = company(db, *shared, ("person", "Nikhil Desai"), ("property", "Gat 118"))
    b, b_document = company(db, *shared)
    empty, _ = company(db)
    vocabulary = PgVocabulary(db)

    a_people, _, a_wadi = vocabulary.candidates(a, ASKED)
    b_people, b_places, b_wadi = vocabulary.candidates(b, ASKED)

    assert outcome(ASKED[0], a_people)[0] == "several"
    assert outcome(ASKED[0], b_people) == ("one", (b_people[0],))  # A's Nikhil Desai never shows
    assert {d for e in [*b_people, *b_places, *b_wadi] for d in e.documents} == {b_document}
    assert outcome(ASKED[1], b_places)[0] == "close"
    assert [e.label for e in a_wadi] == [e.label for e in b_wadi] == ["Pune"]  # never Trash or older
    assert vocabulary.candidates(empty, ASKED) == [[], [], []]
    (newest,) = vocabulary.candidates(b, [Entity("property", "")])  # a missing subject's choices
    assert [(e.label, e.documents) for e in newest] == [("Survey 45/2", (b_document,))]
    assert len(vocabulary.candidates(a, [Entity("property", "")])[0]) == 2


def test_a_confirmed_spelling_and_a_districts_places_are_one_companys_own(db: Database) -> None:  # B2
    approve = (
        "INSERT INTO organization_word_suggestions (customer_id, submitted_by, word, word_fold, meaning,"
        " meaning_fold, status, decided_by, decided_at) VALUES (:c, :u, 'Puny', 'puny', 'Pune', 'pune',"
        " 'approved', :u, now())"
    )
    companies = [company(db, ("place", "Pune"), ("place", "Maval")) for _ in range(2)]
    (a, a_document), (b, _) = companies
    with db.customer(a) as conn:
        user = conn.execute(text("SELECT id FROM users LIMIT 1")).scalar_one()
        conn.execute(text(approve), {"c": a, "u": user})  # A confirms "Puny" as Pune
        insert_key(conn, a, a_document, "place", "Pune", "district")
    vocabulary = PgVocabulary(db)

    (a_places,), (b_places,) = (vocabulary.candidates(c, [Entity("place", "Puny")]) for c in (a, b))

    assert (outcome(ASKED[1], a_places)[0], outcome(ASKED[1], b_places)[0]) == ("one", "close")
    assert [(e.label, e.documents) for e in vocabulary.near(a, ["pune"])] == [("Maval", (a_document,))]
    assert vocabulary.near(b, ["pune"]) == []  # B prints no district: never A's places


def test_a_pick_moved_to_trash_since_is_dropped_when_it_is_answered(db: Database) -> None:
    customer, document = company(db, ("place", "Pune"))
    pending = Pending("Land in Puny?", "place", "Puny", (), (), 1, ("Pune",))
    assert after_reply(PgVocabulary(db), customer, pending).question == "Land in Pune?"
    with db.customer(customer) as conn:
        conn.execute(text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": document})
    assert after_reply(PgVocabulary(db), customer, pending).question == "Land in Puny?"
