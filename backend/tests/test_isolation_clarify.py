"""PRD 6 B1 against a real database: one company's names, places and properties are never
offered or matched for another, even identical ones; nor is Trash or an older version's."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.answering.clarify import Entity, Pending, PgVocabulary, after_reply, first_step, outcome
from kaagaz.answering.rewrite import Plan
from kaagaz.db.engine import Database
from tests.db_support import insert_document, insert_key, new_customer

NAME = "document.pdf"  # insert_document's name

pytestmark = pytest.mark.db

ASKED = [Entity("person", "Nikhil"), Entity("place", "Puny"), Entity("place", "Punewadi")]


def company(db: Database, *printed: tuple[str, str]) -> tuple[str, str, str]:
    """A company with one live document printing each (kind, name), Punewadi only in Trash and
    Pimpri only on an older version; its id, its user (who asks, PRD 7) and its live document."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
        binned = insert_document(conn, customer, deleted_at=datetime(2026, 10, 1, tzinfo=UTC))
        older = insert_document(conn, customer, is_current=False)
        for at, kind, name in [*((document, *each) for each in printed), (binned, "place", "Punewadi")]:
            insert_key(conn, customer, at, kind, name)
        insert_key(conn, customer, older, "place", "Pimpri")
    return customer, user, document


def test_b_is_never_offered_as_names_or_places_even_identical_ones(db: Database) -> None:
    shared = (("person", "Nikhil Varde"), ("place", "Pune"), ("property", "Survey 45/2"))
    a, a_user, _ = company(db, *shared, ("person", "Nikhil Desai"), ("property", "Gat 118"))
    b, b_user, b_document = company(db, *shared)
    empty, empty_user, _ = company(db)
    vocabulary = PgVocabulary(db)

    a_people, _, a_wadi = vocabulary.candidates(a, ASKED, user_id=a_user)
    b_people, b_places, b_wadi = vocabulary.candidates(b, ASKED, user_id=b_user)

    assert outcome(ASKED[0], a_people)[0] == "several"
    assert outcome(ASKED[0], b_people) == ("one", (b_people[0],))  # A's Nikhil Desai never shows
    assert {d for e in [*b_people, *b_places, *b_wadi] for d in e.documents} == {b_document}
    assert outcome(ASKED[1], b_places)[0] == "close"
    assert [e.label for e in a_wadi] == [e.label for e in b_wadi] == ["Pune"]  # never Trash or older
    assert vocabulary.candidates(empty, ASKED, user_id=empty_user) == [[], [], []]
    (newest,) = vocabulary.candidates(
        b, [Entity("property", "")], user_id=b_user
    )  # a missing subject's choices
    assert [(e.label, e.documents) for e in newest] == [("Survey 45/2", (b_document,))]
    assert len(vocabulary.candidates(a, [Entity("property", "")], user_id=a_user)[0]) == 2


def test_a_confirmed_spelling_and_a_districts_places_are_one_companys_own(db: Database) -> None:  # B2
    approve = (
        "INSERT INTO organization_word_suggestions (customer_id, submitted_by, word, word_fold, meaning,"
        " meaning_fold, status, decided_by, decided_at) VALUES (:c, :u, 'Puny', 'puny', 'Pune', 'pune',"
        " 'approved', :u, now())"
    )
    companies = [company(db, ("place", "Pune"), ("place", "Maval")) for _ in range(2)]
    (a, a_user, a_document), (b, b_user, _) = companies
    with db.customer(a) as conn:
        user = conn.execute(text("SELECT id FROM users LIMIT 1")).scalar_one()
        conn.execute(text(approve), {"c": a, "u": user})  # A confirms "Puny" as Pune
        insert_key(conn, a, a_document, "place", "Pune", "district")
    vocabulary = PgVocabulary(db)

    (a_places,), (b_places,) = (
        vocabulary.candidates(c, [Entity("place", "Puny")], user_id=u) for c, u in ((a, a_user), (b, b_user))
    )

    assert (outcome(ASKED[1], a_places)[0], outcome(ASKED[1], b_places)[0]) == ("one", "close")
    assert [(e.label, e.documents) for e in vocabulary.near(a, ["pune"], user_id=a_user)] == [
        ("Maval", (a_document,))
    ]
    assert vocabulary.near(b, ["pune"], user_id=b_user) == []  # B prints no district: never A's places


def test_a_pick_moved_to_trash_since_is_dropped_when_it_is_answered(db: Database) -> None:
    customer, user, document = company(db, ("place", "Pune"))
    pending = Pending("Land in Puny?", "place", "Puny", (), (), 1, ("Pune",))
    assert after_reply(PgVocabulary(db), customer, user, pending).question == "Land in Pune?"
    with db.customer(customer) as conn:
        conn.execute(text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": document})
    assert after_reply(PgVocabulary(db), customer, user, pending).question == "Land in Puny?"


def test_a_missing_subject_offers_the_chats_cited_properties_of_its_own_company_only(db: Database) -> None:
    a, a_user, _ = company(db)  # PRD 6 B: the documents cited earlier, never another company's
    # Changed on purpose (#629): B has two properties, as one alone is no question.
    b, b_user, b_document = company(db, ("property", "Survey 99/9"), ("property", "Survey 98/1"))
    with db.customer(a) as conn:
        old = insert_document(conn, a, created_at=datetime(2026, 1, 5, tzinfo=UTC))
        insert_key(conn, a, old, "property", "Survey 45/2")
        new = insert_document(conn, a)
        for n in range(101, 108):
            insert_key(conn, a, new, "property", f"Gat {n}")
    which = Plan("documents", (), (), None, ("property",))

    def choices(customer: str, user: str, *cited: str) -> list[tuple[str, list[str]]]:
        asked = first_step(PgVocabulary(db), customer, user, "What is the area?", which, False, cited).clarify
        assert asked is not None
        return [(c["label"], c.get("document_ids", [])) for c in asked["choices"]]

    # Changed on purpose (#629): at most five property choices, each naming its document.
    gats = [(f"Gat {n}, {NAME}", [new]) for n in range(101, 106)]
    other: tuple[str, list[str]] = ("Something else", [])
    assert choices(a, a_user) == [*gats, other]  # 45/2 is not among the newest
    assert choices(a, a_user, b_document, old) == [
        (f"Survey 45/2, {NAME}", [old]),
        *gats[:4],
        other,
    ]  # never B's
    assert choices(b, b_user, old) == [  # never A's 45/2
        (f"Survey 98/1, {NAME}", [b_document]),
        (f"Survey 99/9, {NAME}", [b_document]),
        other,
    ]


def test_any_keys_sees_one_companys_own_keys_only(db: Database) -> None:  # Ask speed, 24 Oct 2026
    a, a_user, a_document = company(db, ("person", "Nikhil Varde"))
    b, b_user = new_customer(db)  # no key at all
    vocabulary = PgVocabulary(db)
    assert vocabulary.any_keys(a, user_id=a_user) and vocabulary.within([a_document]).any_keys(
        a, user_id=a_user
    )  # the control
    assert not vocabulary.any_keys(b, user_id=b_user) and not vocabulary.within([a_document]).any_keys(
        b, user_id=b_user
    )
