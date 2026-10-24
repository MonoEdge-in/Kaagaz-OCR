"""Which questions are owner and place questions (kaagaz/answering/owners.py ``owner_ask``)."""

from kaagaz.answering.owners import OwnerAsk, owner_ask

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
