"""Owner and place questions (PRD 6 E) never cross companies, even for the same owner's name.

A and B each have a land record owned by Nikhil Varde in Pune; C has none. Each company's
question finds only its own record, and the queries given another company's keys and ids find
nothing (row level security and ``WHERE customer_id =``).
"""

import json

import pytest

from kaagaz.answering.owners import PgOwners
from kaagaz.answering.service import AnswerService
from kaagaz.db.engine import Database
from kaagaz.search.scope import EVERYTHING
from tests.db_support import new_customer
from tests.land_support import add_land
from tests.settings_factory import make_settings
from tests.test_answering_owners_db import NOW

pytestmark = pytest.mark.db

QUESTION = "Which land is owned by Nikhil Varde in Pune?"


def test_the_same_owner_in_two_companies_gets_each_only_its_own_record(db: Database) -> None:
    (a, _), (b, _), (c, _) = new_customer(db), new_customer(db), new_customer(db)
    a_land = add_land(db, a, "A.pdf", [("Nikhil Varde", "60%")], places=[("district", "Pune")])
    b_land = add_land(db, b, "B.pdf", [("Nikhil Varde", "40%")], places=[("district", "Pune")])
    answers = AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW)

    for customer, own, other in ((a, a_land, b_land), (b, b_land, a_land)):
        answer = answers.answer(customer, QUESTION).to_json()
        assert [d["document_id"] for d in answer["documents"]] == [own]
        assert {citation["document_id"] for citation in answer["citations"]} == {own}
        assert other not in json.dumps(answer)
    for_c = answers.answer(c, QUESTION).to_json()
    # Contract v2 (PRD 6 A) always carries documents: none listed for C.
    assert not for_c["documents"] and a_land not in json.dumps(for_c) and b_land not in json.dumps(for_c)


def test_the_queries_given_another_companys_keys_and_ids_find_nothing(db: Database) -> None:
    (a, _), (b, _) = new_customer(db), new_customer(db)
    a_land = add_land(db, a, "A.pdf", [("Krishna Sohoni", "50%")], places=[("village", "Kusgaon")])
    queries, keys = PgOwners(db), ["krishna sohoni", "kusgaon"]

    assert sorted(queries.resolve(a, keys, EVERYTHING, user_id=None)) == [
        ("person", keys[0]),
        ("place", keys[1]),
    ]
    assert queries.owned(a, keys[:1], EVERYTHING, user_id=None) == [(a_land, "land:1", True)]
    assert queries.resolve(b, keys, EVERYTHING, user_id=None) == []
    assert queries.owned(b, keys[:1], EVERYTHING, user_id=None) == []
    assert queries.keyed(b, "place", keys[1:], EVERYTHING, user_id=None) == []
    assert queries.shares(b, [a_land], EVERYTHING, user_id=None) == []


def test_an_open_list_of_every_owner_and_number_finds_only_its_own_companys_records(db: Database) -> None:
    """#381: "all owners" and "every survey number" read every record in scope: only their own."""
    (a, _), (b, _), (c, _) = new_customer(db), new_customer(db), new_customer(db)
    a_land = add_land(db, a, "A.pdf", [("Nikhil Varde", None)], survey="45/2")
    b_land = add_land(db, b, "B.pdf", [("Asha Shinde", None)], survey="77")
    answers, queries = (
        AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW),
        PgOwners(db),
    )

    for customer, own, other, name, number in (
        (a, a_land, b_land, "Nikhil", "45/2"),
        (b, b_land, a_land, "Asha", "77"),
    ):
        for question, said in (("Who are all the owners?", name), ("List every survey number", number)):
            answer = answers.answer(customer, question).to_json()
            assert said in answer["sentences"][0]["text"] and other not in json.dumps(answer)
            assert {d["document_id"] for d in answer["documents"]} == {own}
    for question in ("Who are all the owners?", "List every survey number"):
        for_c = answers.answer(c, question).to_json()
        assert a_land not in json.dumps(for_c) and b_land not in json.dumps(for_c)
    assert queries.every(c, ["land_owner_name"], EVERYTHING, 50, user_id=None) == (0, [])
    assert queries.every(b, ["land_survey_number"], EVERYTHING, 50, user_id=None) == (1, [(b_land, "land:1")])


def test_a_map_question_finds_only_its_own_companys_map_values(db: Database) -> None:
    (a, _), (b, _) = new_customer(db), new_customer(db)
    printed = [("length", "45.5 m"), ("boundary_east", "46")]
    a_land = add_land(db, a, "A.pdf", survey="45/2", printed=printed)
    answers, queries = (
        AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW),
        PgOwners(db),
    )

    assert answers.answer(a, "Length of survey 45/2?").to_json()["documents"][0]["document_id"] == a_land
    for_b = answers.answer(b, "Length of survey 45/2?").to_json()
    assert not for_b["documents"] and a_land not in json.dumps(for_b) and "45.5" not in json.dumps(for_b)
    assert queries.keyed(b, "property", ["46"], EVERYTHING, ["land_boundary_east"], user_id=None) == []
    assert queries.keyed(a, "property", ["46"], EVERYTHING, ["land_boundary_east"], user_id=None) == [
        (a_land, "land:1")
    ]
