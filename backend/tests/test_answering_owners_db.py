"""Owner and place questions (kaagaz/answering/owners.py, PRD 6 E) through the real AnswerService,
no model, over land values and keys inserted as #245 defines them (tests/land_support.py), and
each statement's plan under the app role."""

import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering import owners
from kaagaz.answering.service import AnswerService
from kaagaz.db.engine import Database
from kaagaz.search.scope import EVERYTHING
from tests.db_support import new_customer
from tests.land_support import add_land
from tests.settings_factory import make_settings
from tests.test_ai_settings_db import switched_off

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)
V, K, ASHA = "Nikhil Varde", "Krishna Sohoni", "Asha Shinde"
PUNE = ("district", "Pune")
_NUMBER = re.compile(r"\d+(?:[.,/]\d+)*")


@pytest.fixture
def keyed(db: Database) -> Database:
    return db


def ask(db: Database, customer: str, question: str) -> dict[str, Any]:
    service = AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW)
    return service.answer(customer, question, scope=EVERYTHING).to_json()


def numbers_allowed(answer: dict[str, Any], question: str, counts: tuple[int, ...] = ()) -> None:
    """Decision 2: every number a sentence holds is a figure as printed and cited, a page, from
    the question, or a count of what it cites or lists; a record's line holds no digit."""
    by_n = {c["n"]: c for c in answer["citations"]}
    printed = {n for c in answer["citations"] for n in _NUMBER.findall(c["snippet"].split(": ", 1)[1])}
    allowed = printed | {str(c["page_no"]) for c in answer["citations"]} | set(_NUMBER.findall(question))
    allowed |= {str(n) for n in counts} | {str(len(answer.get("documents", [])))}
    for sentence in answer["sentences"]:
        cited = [by_n[n] for n in sentence["citations"]]
        mine = {str(len(cited)), str(len({c["document_id"] for c in cited}))}
        for number in _NUMBER.findall(sentence["text"]):
            assert number in allowed | mine, (number, sentence["text"])
    for document in answer.get("documents", []):
        assert not any(ch.isdigit() for ch in document["why"]), document["why"]


def test_example_1_land_owned_together_in_places_and_the_records_naming_only_one(keyed: Database) -> None:
    c, _ = new_customer(keyed)
    both, maval = [(V, "50%"), (K, "50%")], [("taluka", "Maval")]
    kusgaon = add_land(
        keyed, c, "7/12 Kusgaon.pdf", both, places=[("village", "Kusgaon"), *maval, PUNE], survey="45/2"
    )
    raigad = [("taluka", "Khalapur"), ("district", "Raigad")]
    deed = add_land(
        keyed, c, "Sale deed.pdf", buyers=[V, K], sellers=["Ramesh More"], places=raigad, label="needs_review"
    )
    third = [(V, "1/3"), (K, "1/3"), (ASHA, "1/3")]
    ulwe = add_land(keyed, c, "7/12 Ulwe.pdf", third, places=[("village", "Ulwe"), ("taluka", "Panvel")])
    only_v = add_land(keyed, c, "7/12 V.pdf", [(V, None)], places=maval)
    only_k = add_land(keyed, c, "7/12 K.pdf", [(K, None)], places=[("taluka", "Panvel")])
    left_out = [
        add_land(keyed, c, "7/12 Nashik.pdf", both, places=[("taluka", "Niphad")]),  # another place
        add_land(keyed, c, "Sold.pdf", buyers=[ASHA], sellers=[V, K], places=maval),  # they sold it
        add_land(keyed, c, "Trashed.pdf", both, places=maval, deleted_at=NOW),
        add_land(keyed, c, "Older.pdf", both, places=maval, is_current=False),
    ]
    question = "Tell me the land owned by Nikhil Varde and Krishna Sohoni in Maval, Khalapur or Panvel"

    answer = ask(keyed, c, question)

    assert (answer["status"], answer["route"], answer["version"]) == ("answered", "fixed_query", 2)
    assert [s["text"] for s in answer["sentences"]] == [
        "Here are 3 land records that name both Nikhil Varde and Krishna Sohoni as owners.",
        "2 more records name only one of them. Want to see those too?",
    ]
    assert [(d["document_id"], d["why"]) for d in answer["documents"]] == [
        (kusgaon, "owners Nikhil Varde, Krishna Sohoni; Kusgaon, Maval, Pune"),
        (ulwe, "owners Nikhil Varde, Krishna Sohoni, Asha Shinde; Ulwe, Panvel"),
        (deed, "buyers Nikhil Varde, Krishna Sohoni, shares not printed; Khalapur, Raigad"),
    ]
    by_n = {c["n"]: c for c in answer["citations"]}
    first = [by_n[n]["snippet"] for n in answer["documents"][0]["citations"]]
    assert first[0] == "land_survey_number: 45/2" and first[-2:] == ["land_share: 50%", "land_share: 50%"]
    assert {by_n[n]["document_id"] for n in answer["sentences"][1]["citations"]} == {only_v, only_k}
    assert answer["label"] == "needs_review"  # the sale deed's values are not verified
    assert not any(document in json.dumps(answer) for document in left_out)
    numbers_allowed(answer, question)


def test_example_2_shares_as_printed_and_never_a_derived_unowned_share(keyed: Database) -> None:
    c, _ = new_customer(keyed)
    add_land(keyed, c, "7/12.pdf", [(V, "50%"), (K, "30%")], places=[("village", "Kusgaon")], survey="45/2")
    add_land(keyed, c, "7/12 77.pdf", [(ASHA, "85%")], survey="77", unowned="15% unallotted")
    question = "Tell me the unowned percentage of survey 45/2"

    answer = ask(keyed, c, question)

    assert [s["text"] for s in answer["sentences"]] == [
        "The record for Survey 45/2 lists owners holding 50% and 30%.",
        "It does not print an unowned share, so none is worked out. Please check page 1.",
    ]
    assert [d["why"] for d in answer["documents"]] == ["owners Nikhil Varde, Krishna Sohoni; Kusgaon"]
    assert "20%" not in json.dumps(answer) and "15%" not in json.dumps(answer)  # never 100 - 50 - 30
    numbers_allowed(answer, question)

    printed = ask(keyed, c, "What is the unowned share of survey 77?")
    assert [s["text"] for s in printed["sentences"]] == [
        "The record for Survey 77 lists owners holding 85%.",
        "It prints the unowned share as 15% unallotted.",
    ]
    by_n = {c["n"]: c for c in printed["citations"]}
    assert [by_n[n]["snippet"] for n in printed["sentences"][1]["citations"]] == [
        "land_unowned_share: 15% unallotted"
    ]
    numbers_allowed(printed, "What is the unowned share of survey 77?")


def test_example_4_an_open_list_of_everyone_who_owns_property_in_a_place(keyed: Database) -> None:
    c, _ = new_customer(keyed)
    wakad = add_land(keyed, c, "Flat 302.pdf", [(V, None)], places=[("village", "Wakad"), PUNE])
    hinjewadi = add_land(keyed, c, "Survey 12.pdf", [(V, None)], places=[("village", "Hinjewadi"), PUNE])
    kothrud = add_land(keyed, c, "Shop 4.pdf", [(K, None)], places=[("village", "Kothrud"), PUNE])
    kusgaon = add_land(
        keyed, c, "Survey 45.pdf", [(V, "50%"), (K, "50%")], places=[("village", "Kusgaon"), PUNE]
    )
    elsewhere = add_land(keyed, c, "Raigad.pdf", [(V, None)], places=[("district", "Raigad")])
    question = "Tell me the property owned in Pune"

    answer = ask(keyed, c, question)

    assert [s["text"] for s in answer["sentences"]] == [
        "You didn't say whose, so here is everyone in your documents who owns property in Pune district:"
        " Nikhil Varde owns 2, Krishna Sohoni owns 1, and they share 1."
    ]
    assert [(d["document_id"], d["why"]) for d in answer["documents"]] == [
        (wakad, "owners Nikhil Varde, shares not printed; Wakad, Pune"),
        (hinjewadi, "owners Nikhil Varde, shares not printed; Hinjewadi, Pune"),
        (kothrud, "owners Krishna Sohoni, shares not printed; Kothrud, Pune"),
        (kusgaon, "owners Nikhil Varde, Krishna Sohoni; Kusgaon, Pune"),
    ]
    groups = Counter(d["why"].split(";")[0].removesuffix(", shares not printed") for d in answer["documents"])
    assert list(groups.values()) == [2, 1, 1]  # each count is its own list's length
    assert elsewhere not in json.dumps(answer)
    numbers_allowed(answer, question, tuple(groups.values()))


def test_a_record_with_25_owners_names_the_one_asked_for_and_counts_the_rest(keyed: Database) -> None:
    c, _ = new_customer(keyed)
    others = [(f"Kale {chr(ord('A') + i)}", None) for i in range(24)]
    big = add_land(keyed, c, "7/12 big.pdf", [*others[:10], (V, None), *others[10:]], survey="9")
    question = "Which land is owned by Nikhil Varde?"

    answer = ask(keyed, c, question)

    assert [s["text"] for s in answer["sentences"]] == [
        "Here is 1 land record that names Nikhil Varde as an owner.",
        "The record for Survey 9 names Nikhil Varde and 24 more owners.",
    ]
    assert [(d["document_id"], d["why"]) for d in answer["documents"]] == [
        (big, "owners Nikhil Varde and others, shares not printed")
    ]
    assert len(answer["sentences"][1]["citations"]) == 24
    numbers_allowed(answer, question)


def test_without_an_owning_word_or_a_known_name_the_other_routes_answer(keyed: Database) -> None:
    c, _ = new_customer(keyed)
    add_land(keyed, c, "7/12.pdf", [(V, "50%")], places=[PUNE])

    # Contract v2 (PRD 6 A) always carries documents: none listed, and not the owner route.
    assert ask(keyed, c, "Show the documents of Nikhil Varde in Pune")["route"] != "fixed_query"
    assert not ask(keyed, c, "Land owned by Krishna Sohoni")["documents"]  # not in this company's keys


MAP = [("plot_number", "12"), ("length", "45.5 m"), ("road_width", "9 m"), ("dimension", "30 m")]
MAP += [("boundary_east", "46"), ("boundary_west", "Nala"), ("scale", "1:500")]


def test_a_maps_measurements_are_quoted_as_printed_and_a_missing_one_never_worked_out(
    keyed: Database,
) -> None:
    c, _ = new_customer(keyed)
    seven_twelve = add_land(keyed, c, "7/12 Kusgaon.pdf", [(V, None)], survey="45/2", printed=MAP)
    question = "What is the length, width and area of survey 45/2?"

    answer = ask(keyed, c, question)

    assert (answer["status"], answer["route"], answer["label"]) == ("answered", "fixed_query", "verified")
    assert [s["text"] for s in answer["sentences"]] == [  # decision 2's final check kept every figure
        "The length of Survey 45/2, plot 12 is printed as 45.5 m.",
        "The width of Survey 45/2, plot 12 is not printed on the map, so none is worked out.",
        "The area of Survey 45/2, plot 12 is not printed on the map, so none is worked out.",
    ]  # never 45.5 m times a width, nor a width from the scale
    by_n = {c["n"]: c for c in answer["citations"]}
    assert [by_n[n]["snippet"] for n in answer["sentences"][0]["citations"]] == [
        "land_survey_number: 45/2",
        "land_plot_number: 12",
        "land_length: 45.5 m",
    ]
    assert [(d["document_id"], d["why"]) for d in answer["documents"]] == [
        (seven_twelve, "owners Nikhil Varde, shares not printed")
    ]
    numbers_allowed(answer, question)

    road = ask(keyed, c, "Road width of plot 12?")
    assert [s["text"] for s in road["sentences"]] == [
        "The road width of Survey 45/2, plot 12 is printed as 9 m."
    ]
    numbers_allowed(road, "Road width of plot 12?")

    drawn = ask(keyed, c, "Show the map of survey 45/2")
    assert [s["text"] for s in drawn["sentences"]] == [
        "The map values printed for Survey 45/2, plot 12: length 45.5 m; road width 9 m;"
        " other measurements 30 m; scale 1:500; east neighbour 46; west neighbour Nala."
    ]
    numbers_allowed(drawn, "Show the map of survey 45/2")


def test_the_neighbours_by_side_and_the_records_printing_it_as_a_neighbour(keyed: Database) -> None:
    c, _ = new_customer(keyed)
    add_land(keyed, c, "7/12 45.pdf", survey="45/2", printed=MAP)
    west = [("boundary_west", "४५/२")]
    beside = add_land(keyed, c, "7/12 46.pdf", survey="46", printed=west, label="needs_review")
    elsewhere = add_land(keyed, c, "7/12 47.pdf", survey="47", printed=[("boundary_west", "46")])
    question = "Which survey numbers border 45/2?"

    answer = ask(keyed, c, question)

    assert [s["text"] for s in answer["sentences"]] == [
        "The neighbours printed for Survey 45/2, plot 12: 46 on the east and Nala on the west.",
        "Survey 46 prints ४५/२ as its west neighbour.",
    ]
    assert [d["why"] for d in answer["documents"][1:]] == ["values printed on its map"]
    assert answer["documents"][1]["document_id"] == beside
    assert answer["label"] == "needs_review"  # the other record's values are not verified
    assert elsewhere not in json.dumps(answer)
    numbers_allowed(answer, question)

    lone = ask(keyed, c, "Which survey numbers border 47?")
    assert [s["text"] for s in lone["sentences"]] == [
        "The neighbours printed for Survey 47: 46 on the west.",
    ]


def test_the_land_facts_switch_off_leaves_map_questions_to_the_other_routes(keyed: Database) -> None:
    c, _ = new_customer(keyed)
    add_land(keyed, c, "7/12.pdf", survey="45/2", printed=MAP)
    assert ask(keyed, c, "Show the map of survey 45/2")["route"] == "fixed_query"
    with switched_off(keyed, "land_facts"):
        answer = ask(keyed, c, "Show the map of survey 45/2")
    assert answer["route"] != "fixed_query" and not answer["documents"]


# Each statement: the condition its B-tree index must serve, and its own parameters.
_PLANS: dict[str, tuple[str, dict[str, Any]]] = {
    "_RESOLVE": ("latin = ANY", {"candidates": ["nikhil varde", "pune"]}),
    "_OWNED": ("latin = ANY", {"people": ["nikhil varde"], "fields": list(owners.OWNING)}),
    "_KEYED": ("latin = ANY", {"kind": "place", "keys": ["pune"], "fields": ["land_district"]}),
    "_KEYED neighbours": (
        "latin = ANY",
        {"kind": "property", "keys": ["45/2"], "fields": ["land_boundary_east"]},
    ),
    "_SHARES_AS_PRINTED": ("document_id = ANY", {"fields": ["land_share"]}),
}
# A company with 2,000 land records, each with one owner in one of 200 villages, so the planner
# knows from the statistics how few rows a key finds.
_BULK = (
    "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes, detected_type, status,"
    " family_id)"
    " SELECT id, :c, 'bulk.pdf', md5(id::text) || md5(n::text), 10, 'pdf', 'ready', id"
    " FROM (SELECT gen_random_uuid() AS id, n FROM generate_series(1, 2000) n) g",
    "INSERT INTO extracted_values (customer_id, document_id, record_key, field, value_text, value_raw,"
    " page_no, region, reader, label, label_reason)"
    " SELECT :c, d.id, f.key, f.field, f.word || (row_number() OVER (PARTITION BY f.field) % f.spread),"
    " 'raw', 1, '{}', 'test', 'verified', 'readers_agree'"
    " FROM documents d CROSS JOIN (VALUES ('land:1:party:1', 'land_owner_name', 'person ', 2000),"
    " ('land:1', 'land_village', 'village ', 200)) f (key, field, word, spread) WHERE d.customer_id = :c",
    "INSERT INTO value_keys (customer_id, value_id, document_id, kind, place_level, latin, sound)"
    " SELECT :c, v.id, v.document_id, CASE WHEN v.field = 'land_village' THEN 'place' ELSE 'person' END,"
    " CASE WHEN v.field = 'land_village' THEN 'village' END, v.value_text, '' FROM extracted_values v"
    " WHERE v.customer_id = :c",
)


@pytest.fixture(scope="module")
def bulk(db: Database) -> str:
    c, _ = new_customer(db)
    with db.customer(c) as conn:
        for statement in _BULK:
            conn.execute(text(statement), {"c": c})
    with db.engine.begin() as conn:  # the owner: fresh statistics, no customer's rows read
        conn.execute(text("ANALYZE documents, extracted_values, value_keys"))
    return c


@pytest.mark.parametrize("name", sorted(_PLANS))
def test_each_statement_uses_its_b_tree_index_under_row_level_security(
    db: Database, bulk: str, name: str
) -> None:
    """As the app role, each statement finds its rows through the B-tree index on its key; a trigram,
    array or LIKE operator, which the index cannot serve under the policy, would leave a scan."""
    condition, params = _PLANS[name]
    with db.customer(bulk) as conn:
        documents = conn.execute(
            text("SELECT id::text FROM documents WHERE customer_id = :c LIMIT 2"), {"c": bulk}
        )
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        bound = {"customer_id": bulk, "documents": list(documents.scalars()), **params, **EVERYTHING.params()}
        statement = getattr(owners, name.split()[0]).text
        plan = "\n".join(conn.execute(text("EXPLAIN " + statement), bound).scalars())
    index = [
        line for line in plan.splitlines() if line.strip().startswith("Index Cond:") and condition in line
    ]
    assert index and "Seq Scan" not in plan, plan


def test_the_land_facts_switch_off_leaves_owner_questions_to_the_other_routes(keyed: Database) -> None:
    """PRD 6 integration (0052): the super admin's switch off, no owner route and no record listed."""
    c, _ = new_customer(keyed)
    add_land(keyed, c, "7/12.pdf", [(V, "50%")], places=[PUNE])
    assert ask(keyed, c, "Which land is owned by Nikhil Varde?")["route"] == "fixed_query"
    with switched_off(keyed, "land_facts"):
        answer = ask(keyed, c, "Which land is owned by Nikhil Varde?")
    assert answer["route"] != "fixed_query" and not answer["documents"]
