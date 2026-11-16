"""Which questions are owner and place questions (kaagaz/answering/owners.py ``owner_ask``)."""

from types import SimpleNamespace
from typing import Any

from kaagaz.answering.fixed_queries import ValueRef
from kaagaz.answering.owners import OWNING, OwnerAsk, PgOwners, _lands, _map_lines, owner_ask
from kaagaz.search.scope import EVERYTHING, Scope

KEYS = {
    ("person", "nikhil varde"),
    ("person", "krishna sohoni"),
    ("place", "pune"),
    ("place", "maval"),
    ("property", "45/2"),
}
SURVEY = "land_survey_number"


def resolve(candidates: list[str]) -> list[tuple[str, str]]:
    return [key for key in sorted(KEYS) if key[1] in candidates]


def test_people_with_an_owning_word_in_the_question_order() -> None:
    asked = owner_ask("Land owned by Krishna Sohoni and Nikhil Varde in Maval?", resolve)
    assert asked == OwnerAsk(people=("krishna sohoni", "nikhil varde"), places=("maval",))


def test_a_share_word_with_a_property_asks_for_the_shares_and_the_unowned_one() -> None:
    asked = owner_ask("Tell me the unowned percentage of survey ४५/२", resolve)
    assert asked == OwnerAsk(properties=("45/2",), shares=True, unowned=True, within=(SURVEY,))


def test_no_owning_or_share_word_never_looks_anything_up() -> None:
    def refused(candidates: list[str]) -> list[tuple[str, str]]:
        raise AssertionError("looked up")

    assert owner_ask("Show the invoices of Nikhil Varde in Pune", refused) is None
    assert owner_ask("How many documents mention Pune?", refused) is None


def test_a_map_word_with_a_property_asks_for_its_map_values() -> None:
    assert owner_ask("What is the length, the width and road width of survey 45/2?", resolve) == OwnerAsk(
        properties=("45/2",), measures=("land_length", "land_width", "land_road_width"), within=(SURVEY,)
    )
    bordering = owner_ask("Which survey numbers border 45/2?", resolve)
    assert bordering == OwnerAsk(properties=("45/2",), borders=True)  # any kind of number
    on_map = OwnerAsk(properties=("45/2",), drawn=True, within=(SURVEY,))
    assert owner_ask("Show the map of survey 45/2", resolve) == on_map
    assert owner_ask("What is the size of survey 45/2?", resolve) == on_map
    wide = owner_ask("How wide is plot 45/2?", resolve)
    assert wide == OwnerAsk(properties=("45/2",), measures=("land_width",), within=("land_plot_number",))
    assert owner_ask("Show the map of Pune", resolve) is None  # a map question is about a property
    assert owner_ask("What is the size of invoice 45/2?", resolve) is None  # and names the land
    # An owning word keeps the owner question as it was.
    assert owner_ask("Who owns the land in Pune by area?", resolve) == OwnerAsk(places=("pune",))


def test_a_measure_word_routes_only_when_asked_as_a_measure() -> None:
    """Long, area and road in an ordinary question never ask for a printed measurement."""
    for question in (
        "How long is the lease on plot 45/2?",
        "Which area is survey 45/2 in?",
        "Which road is survey 45/2 on?",
    ):
        assert owner_ask(question, resolve) is None, question


def test_an_open_list_naming_nothing_asks_for_every_owner_or_every_number() -> None:
    """#381: every one in scope, from the stored values; anything else named keeps its own route."""

    def nobody(candidates: list[str]) -> list[tuple[str, str]]:
        return []

    for question in ("Who are all the owners?", "List the owners in these documents", "every owner"):
        assert owner_ask(question, nobody) == OwnerAsk(every=OWNING), question
    assert owner_ask("List every survey number", nobody) == OwnerAsk(every=(SURVEY,))
    assert owner_ask("all khata numbers", nobody) == OwnerAsk(every=("land_khata_number",))
    assert owner_ask("Show all plot nos", nobody) == OwnerAsk(every=("land_plot_number",))
    # Someone or somewhere named: the question as before.
    assert owner_ask("All land owned by Nikhil Varde", resolve) == OwnerAsk(people=("nikhil varde",))
    for question in (
        "List all the owners of Sharma Traders",  # a word outside the frame: not a land list
        "Who are the owners?",  # no list word
        "Which survey numbers border 45/2?",  # a map question, not a list
        "all survey and khata numbers",  # two kinds at once
        "List all invoices",
    ):
        assert owner_ask(question, nobody) is None, question


class _Looked(PgOwners):
    """PgOwners with its one statement replaced: the fields each lookup looked in, and a record
    only where ``kept_as`` is among them."""

    def __init__(self, kept_as: str) -> None:
        self.kept_as = kept_as
        self.looked: list[list[str]] = []

    def _rows(
        self, customer_id: str, query: Any, params: dict[str, Any], scope: Scope, user_id: str | None
    ) -> list[Any]:
        self.looked.append(params["fields"])
        return [SimpleNamespace(document_id="d1", land="land:1")] if self.kept_as in params["fields"] else []


def test_a_plot_number_kept_as_a_survey_number_is_still_found() -> None:
    # Read before plot numbers had their own field (#274): plot 56 is kept as a survey number.
    old = _Looked(kept_as=SURVEY)
    assert old.keyed("c", "property", ["56"], EVERYTHING, ("land_plot_number",), user_id=None) == [
        ("d1", "land:1")
    ]
    assert old.looked == [["land_plot_number"], ["land_plot_number", SURVEY]]
    # Kept as a plot number: found there, and survey numbers are never looked in.
    new = _Looked(kept_as="land_plot_number")
    assert new.keyed("c", "property", ["56"], EVERYTHING, ("land_plot_number",), user_id=None) == [
        ("d1", "land:1")
    ]
    assert new.looked == [["land_plot_number"]]
    # A survey number asked as one never falls back to anything.
    survey = _Looked(kept_as="land_plot_number")
    assert survey.keyed("c", "property", ["56"], EVERYTHING, (SURVEY,), user_id=None) == []
    assert survey.looked == [[SURVEY]]


def _map_value(field: str, said: str, region: dict[str, Any]) -> tuple[str, str | None, ValueRef]:
    ref = ValueRef("v-" + said, "d1", "Map 59.pdf", "pdf", 1, region, field, said, "verified")
    return "land:1", said.casefold(), ref


def test_a_map_value_with_no_spot_is_never_quoted_even_a_word() -> None:
    spot = {"kind": "box", "page": 1, "box": [0.1, 0.1, 0.2, 0.12]}
    lands = _lands(
        [
            _map_value(SURVEY, "45", spot),
            _map_value("land_boundary_east", "46", spot),
            _map_value("land_boundary_west", "Nala", {"kind": "box", "page": 1, "box": [0.5, 0.5, 0.4, 0.4]}),
        ]
    )
    said = _map_lines(lands[("d1", "land:1")], OwnerAsk(properties=("45",), borders=True))
    # Changed on purpose (review of #564): the west neighbour, printed with no spot, is never quoted
    # but never left out as if not printed either.
    assert [text for text, _ in said] == [
        "The neighbours printed for Survey 45: 46 on the east.",
        "Other neighbours of Survey 45 are printed on the map where Kaagaz can't point to them; open the map"
        " to read them.",
    ]
