"""Which questions are owner and place questions (kaagaz/answering/owners.py ``owner_ask``)."""

from kaagaz.answering.owners import OwnerAsk, owner_ask

KEYS = {
    ("person", "nikhil varde"),
    ("person", "krishna sohoni"),
    ("place", "pune"),
    ("place", "maval"),
    ("property", "45/2"),
}


def resolve(candidates: list[str]) -> list[tuple[str, str]]:
    return [key for key in sorted(KEYS) if key[1] in candidates]


def test_people_with_an_owning_word_in_the_question_order() -> None:
    asked = owner_ask("Land owned by Krishna Sohoni and Nikhil Varde in Maval?", resolve)
    assert asked == OwnerAsk(people=("krishna sohoni", "nikhil varde"), places=("maval",))


def test_a_share_word_with_a_property_asks_for_the_shares_and_the_unowned_one() -> None:
    asked = owner_ask("Tell me the unowned percentage of survey ४५/२", resolve)
    assert asked == OwnerAsk(properties=("45/2",), shares=True, unowned=True)


def test_no_owning_or_share_word_never_looks_anything_up() -> None:
    def refused(candidates: list[str]) -> list[tuple[str, str]]:
        raise AssertionError("looked up")

    assert owner_ask("Show the invoices of Nikhil Varde in Pune", refused) is None
    assert owner_ask("How many documents mention Pune?", refused) is None


def test_a_map_word_with_a_property_asks_for_its_map_values() -> None:
    assert owner_ask("What is the length and road width of survey 45/2?", resolve) == OwnerAsk(
        properties=("45/2",), measures=("land_length", "land_road_width")
    )
    bordering = owner_ask("Which survey numbers border 45/2?", resolve)
    assert bordering == OwnerAsk(properties=("45/2",), borders=True)
    assert owner_ask("Show the map of survey 45/2", resolve) == OwnerAsk(properties=("45/2",), drawn=True)
    assert owner_ask("Show the map of Pune", resolve) is None  # a map question is about a property
    assert owner_ask("What is the size of invoice 45/2?", resolve) is None  # and names the land
    # An owning word keeps the owner question as it was.
    assert owner_ask("Who owns the land in Pune by area?", resolve) == OwnerAsk(places=("pune",))
