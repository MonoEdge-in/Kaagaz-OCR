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
from tests.land_support import add_land, ensure_value_keys
from tests.settings_factory import make_settings
from tests.test_answering_owners_db import NOW

pytestmark = pytest.mark.db

QUESTION = "Which land is owned by Nikhil Varde in Pune?"


def test_the_same_owner_in_two_companies_gets_each_only_its_own_record(db: Database) -> None:
    ensure_value_keys(db)
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
    ensure_value_keys(db)
    (a, _), (b, _) = new_customer(db), new_customer(db)
    a_land = add_land(db, a, "A.pdf", [("Krishna Sohoni", "50%")], places=[("village", "Kusgaon")])
    queries, keys = PgOwners(db), ["krishna sohoni", "kusgaon"]

    assert sorted(queries.resolve(a, keys, EVERYTHING)) == [("person", keys[0]), ("place", keys[1])]
    assert queries.owned(a, keys[:1], EVERYTHING) == [(a_land, "land:1", True)]
    assert queries.resolve(b, keys, EVERYTHING) == []
    assert queries.owned(b, keys[:1], EVERYTHING) == []
    assert queries.keyed(b, "place", keys[1:], EVERYTHING) == []
    assert queries.shares(b, [a_land], EVERYTHING) == []
