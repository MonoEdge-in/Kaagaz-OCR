"""check_story (pure): what a story is stored as, and what is refused."""

import pytest

from kaagaz.db import document_stories
from kaagaz.db.document_stories import (
    LATIN_MAX,
    MAX_MARKS,
    STORY_MAX,
    StoryTextError,
    check_story,
    recipe_tag,
    story_hash,
)


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
        ("\u3164", "no_letter"),  # a Hangul filler: a letter that shows nothing
        ("\u115f\u1160 \uffa0 ...", "no_letter"),
        ("\u0378\ue000\U000f0000", "empty"),  # unassigned and private use
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
    assert recipe_tag("an-embed-model") == "an-embed-model#story1"


def test_a_joiner_stays_only_right_after_a_virama() -> None:
    assert check_story("\u200d" * 999 + "a").text == "a"
    assert check_story("lo\u200can").text == "loan"
    assert check_story("\u0915\u200d\u094d\u0937").text == "\u0915\u094d\u0937"  # before the virama: dropped
    assert check_story("\u0915\u094d\u200c\u0937").text == "\u0915\u094d\u200c\u0937"


def test_marks_are_capped_per_base_and_invisible_ones_go() -> None:
    capped = check_story("a" + "\u0301" * 999 + " b" + "\u0302" * 9)
    assert capped.text == "\u00e1" + "\u0301" * (MAX_MARKS - 1) + " b" + "\u0302" * MAX_MARKS
    assert check_story("\u0915\u093c\u093f\u0902").text == "\u0915\u093c\u093f\u0902"  # three marks: all kept
    assert check_story("lo\u034fan\ufe0f \u2764\ufe0f \U000e0100x").text == "loan \u2764 x"
    assert check_story("ab\u0378c\ue000d").text == "abcd"


def test_a_story_whose_latin_spelling_is_too_long_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(check_story("\u091b" * STORY_MAX).latin) == LATIN_MAX - 1  # the longest spelling found
    monkeypatch.setattr(document_stories, "latin_spelling", lambda text: "x" * (LATIN_MAX + 1))
    with pytest.raises(StoryTextError, match="too_long"):
        check_story("short")  # the guard before the table's CHECK
