"""check_story (pure): what a story is stored as, and what is refused."""

import pytest

from kaagaz.db.document_stories import STORY_MAX, StoryTextError, check_story, recipe_tag, story_hash


def test_a_story_is_tidied_and_spelt_in_latin_letters() -> None:
    checked = check_story("  Papa ne 2018 me\n\tjo  Daregaon wali जमीन  ")
    assert checked.text == "Papa ne 2018 me jo Daregaon wali जमीन"
    assert checked.latin == "papa ne 2018 me jo daregaon wali jamin"


def test_control_and_direction_characters_go_and_the_joiners_stay() -> None:
    # A right-to-left override, an isolate, a mark, a zero-width space, a BOM, a NUL and a bell.
    dirty = "\u202eloan\u202c \u2066bank\u2069\u200f pa\u200bper\ufeff\x00\x07"
    assert check_story(dirty).text == "loan bank paper"
    assert check_story("क्\u200dष").text == "क्\u200dष"  # ZWJ shapes Devanagari: kept


def test_the_text_is_nfc() -> None:
    assert check_story("cafe\u0301").text == "caf\u00e9"
    assert check_story("क\u093c").text == "क\u093c"  # ka and a nukta: kept apart by NFC, so it stays two


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "empty"),
        (" \n\t ", "empty"),
        ("\u202e\u200b\x00", "empty"),
        ("!!! ... ???", "no_letter"),
        ("x" * (STORY_MAX + 1), "too_long"),
    ],
)
def test_a_bad_story_is_refused(value: str, reason: str) -> None:
    with pytest.raises(StoryTextError) as refused:
        check_story(value)
    assert refused.value.reason == reason


def test_the_limit_counts_code_points_after_tidying() -> None:
    assert len(check_story("ज" * STORY_MAX).text) == STORY_MAX
    assert len(check_story("a" * STORY_MAX + "\u200b" * 5 + "   ").text) == STORY_MAX
    assert check_story("7/12").text == "7/12"  # a digit is enough


def test_the_hash_and_the_tag() -> None:
    assert story_hash("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert story_hash("जमीन") != story_hash("jamin")
    assert recipe_tag("qwen3-embedding") == "qwen3-embedding#story1"
