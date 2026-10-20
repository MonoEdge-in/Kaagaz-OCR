"""The page reading result's codes and the sentences the user sees (PRD 5 F1, F2, G3)."""

import re
from collections.abc import MutableMapping
from typing import cast

import pytest

from kaagaz.reading import page_quality
from kaagaz.reading.page_quality import ISSUE_CODES, ISSUES, Issue, fix, says

# The F1 table, row by row: code, what the user sees (with its example numbers), the fix.
F1_TABLE = [
    ("blurry", "This page is blurry.", "Scan it again, or hold the phone still in good light."),
    (
        "low_resolution",
        # Changed on purpose (UI quality council, 15 Oct 2026): no DPI on user screens.
        "This page was scanned at low quality, so small letters may be misread.",
        "Scan it again at a higher quality setting, or take the photo closer.",
    ),
    ("faint", "The text is faint.", "Scan darker, or photocopy the original first."),
    (
        "glare",
        "Glare or a shadow covers part of the page.",
        "Take the photo without flash, away from a window, with the page flat.",
    ),
    (
        "shadow",
        "Glare or a shadow covers part of the page.",
        "Take the photo without flash, away from a window, with the page flat.",
    ),
    ("cut_off", "Part of the page is cut off.", "Include all four edges of the page."),
    (
        "stamp_over_text",
        "A stamp or overwriting covers some text.",
        "Check the marked words against the original.",
    ),
    (
        "overwritten",
        "A stamp or overwriting covers some text.",
        "Check the marked words against the original.",
    ),
    (
        "handwriting_hard",
        "Some handwriting is hard to read.",
        "Check the marked words, or type the correct value.",
    ),
    ("illegible_words", "Hard to read: 12 words.", "Read this page again, or upload a better scan."),
    ("readers_disagree", "Two readers read 3 values differently.", "Confirm the marked values."),
    ("unpinned", "The exact spot of 4 lines needs a check.", "Confirm the marked values."),
    # Changed on purpose (UI quality council, 15 Oct 2026): plain words, not "declined".
    ("reader_refused", "This page needs a closer look.", "Read it again, or upload a better scan."),
    (
        "read_locally",
        "Read by the backup reader while the main reader was down. It will be read again automatically.",
        "Nothing to do.",
    ),
    ("no_text", "No text was found on this page.", "If it has text, upload a sharper scan."),
]
EXAMPLE_NUMBERS = {"illegible_words": 12, "readers_disagree": 3, "unpinned": 4}


def test_the_issue_codes_are_the_f1_tables_and_nothing_else() -> None:
    assert sorted(ISSUE_CODES) == sorted(code for code, _, _ in F1_TABLE)
    assert len(ISSUE_CODES) == 15
    assert all(ISSUES[code].code == code for code in ISSUE_CODES)


@pytest.mark.parametrize(("code", "text", "suggested"), F1_TABLE)
def test_each_issue_says_the_prds_sentence_and_fix(code: str, text: str, suggested: str) -> None:
    assert says(code, count=EXAMPLE_NUMBERS.get(code)) == text
    assert fix(code) == suggested


def test_a_count_of_one_is_singular() -> None:
    assert says("illegible_words", count=1) == "Hard to read: 1 word."
    assert says("readers_disagree", count=1) == "Two readers read 1 value differently."
    assert says("unpinned", count=1) == "The exact spot of 1 line needs a check."


def test_a_sentence_is_never_shown_with_a_made_up_number() -> None:
    for code in ("illegible_words", "readers_disagree", "unpinned"):
        for count in (None, -1):
            with pytest.raises(ValueError, match=code):
                says(code, count=count)


def test_no_sentence_or_fix_names_a_dpi_a_model_or_a_code() -> None:
    """UI quality council, 15 Oct 2026: technical words stay off user screens."""
    technical = re.compile(r"\bDPI\b|\d+\s?%|gemini|claude|openrouter|\bOCR\b|\bmodel\b|\([a-z_]+\)", re.I)
    for code, issue in ISSUES.items():
        said = says(code, count=EXAMPLE_NUMBERS.get(code))
        assert not technical.search(said), said
        assert not technical.search(issue.fix), issue.fix


def test_an_unknown_code_is_refused() -> None:
    with pytest.raises(KeyError):
        says("tilted")
    with pytest.raises(KeyError):
        fix("smudged")


def test_the_qualities_and_the_models_words_are_the_prds() -> None:
    assert sorted(page_quality.READ_QUALITIES) == ["good", "uncertain", "unreadable"]  # F1
    assert sorted(page_quality.MODEL_QUALITIES) == ["fair", "good", "poor"]  # G3 page.quality
    assert page_quality.MODEL_ISSUES == (  # G3 page.issues
        "blurry",
        "faint",
        "glare",
        "shadow",
        "cut_off",
        "stamp_over_text",
        "overwritten",
        "handwriting_hard",
        "tilted",
    )
    # Every issue the model can name is stored as itself, except tilted, which has no F1 row.
    assert set(page_quality.MODEL_ISSUES) - ISSUE_CODES == {"tilted"}


def test_the_table_cannot_be_changed_at_run_time() -> None:
    with pytest.raises(TypeError):
        cast(MutableMapping[str, Issue], ISSUES)["blurry"] = ISSUES["faint"]


def grade(
    issues: tuple[str, ...] = (),
    *,
    model_quality: str | None = "good",
    words: int = 10,
    illegible: int = 0,
    unsure: int = 0,
) -> str:
    return page_quality.grade(
        issues, model_quality=model_quality, words=words, illegible_words=illegible, unsure_words=unsure
    )


def test_f2_unreadable_no_text_or_refused_or_poor_with_over_30_percent_illegible() -> None:
    assert grade(("no_text",), model_quality=None, words=0) == "unreadable"
    assert grade(("reader_refused",), model_quality=None) == "unreadable"
    assert grade(("illegible_words",), model_quality="poor", words=10, illegible=4) == "unreadable"
    # Exactly 30% is not more than 30%; and a fair page is never unreadable by its [?] alone.
    assert grade(("illegible_words",), model_quality="poor", words=10, illegible=3) == "uncertain"
    assert grade(("illegible_words",), model_quality="fair", words=10, illegible=9) == "uncertain"


def test_f2_uncertain_unsure_illegible_unpinned_disputed_low_resolution_or_any_issue() -> None:
    assert grade(unsure=1) == "uncertain"
    assert grade(illegible=1) == "uncertain"
    for code in ("unpinned", "readers_disagree", "low_resolution", "read_locally", "blurry"):
        assert grade((code,)) == "uncertain", code


def test_f2_good_none_of_these() -> None:
    assert grade() == "good"
    assert grade(model_quality=None, words=0) == "good"
    assert page_quality.UNREADABLE_ILLEGIBLE_SHARE == 0.30
