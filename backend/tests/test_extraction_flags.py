"""Which words are flagged, how strongly and why (kaagaz.extraction.flags; scan review UX
rules, 16 Oct 2026). Pure: no database.

Every F1 issue code and every value label reason has its row below, so a new code or
reason fails here until its band and reason are decided. The word key is pinned: stored
changes are found by it, so it must never change for the same word.
"""

import re
from typing import Any

import pytest

from kaagaz.db import listing, review
from kaagaz.db.review import EVERY_REASON, WAITING_VALUE
from kaagaz.db.word_review import FLAGGED_VALUE
from kaagaz.extraction.flags import (
    LINE,
    MARK,
    REASONS,
    VALUE,
    alternatives,
    occurrences,
    overlaps,
    page_reason,
    span_flag,
    value_flag,
    word_key,
)
from kaagaz.reading.page_quality import ISSUE_CODES

BOX = {"kind": "box", "page": 1, "box": [0.5, 0.1, 0.6, 0.15]}

# F1 code -> the reason it gives a word on its page (None: it explains no word itself).
ISSUE_TABLE = {
    "blurry": "smudged_print",
    "low_resolution": "low_resolution_scan",
    "faint": "faint_print",
    "glare": "glare_or_shadow",
    "shadow": "glare_or_shadow",
    "cut_off": "cut_off_edge",
    "stamp_over_text": "overlapping_stamp",
    "overwritten": "overwritten_text",
    "handwriting_hard": "handwriting",
    "illegible_words": None,
    "readers_disagree": None,
    "unpinned": None,
    "reader_refused": None,  # a backup-read page: backup_reader, below
    "read_locally": None,
    "no_text": None,
}
# Value label reason -> (band, reason) of its flag, or None for no flag.
VALUE_TABLE: dict[str, tuple[str, str] | None] = {
    "direct_cell": None,
    "readers_agree": None,
    "readers_disagree": ("review", "readings_differ"),
    "single_reader": None,
    "low_confidence": ("check", "smudged_print"),  # its page is blurry
    "corrected": None,  # a flag only with the change that fixed it (below)
    "learned_correction": None,
    "check_failed": None,
    "model_chosen": None,
    "ocr_script": ("check", "mixed_language"),
    "unsure_text": None,  # its word marks are the flags
    "box_unanchored": None,  # its unpinned line is the flag
    "local_reader": None,  # its backup-read lines are the flags
}


def span(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "kind": None,
        "quality": "unsure",
        "box_source": "found",
        "text": "4,850",
        "region": BOX,
        "confidence": None,
        "page_no": 1,
        "read_quality": "uncertain",
        "read_issues": [],
    }
    return row | changes


def value(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "7c1ea497-85ae-4823-ae3c-998f1793220d",
        "page_no": 1,
        "region": BOX,
        "value_text": "Rs 4,850",
        "value_raw": "Rs 4,850",
        "second_reading": None,
        "confidence": None,
        "label": "needs_review",
        "label_reason": "readers_disagree",
        "votes": None,
        "read_issues": ["blurry"],
    }
    return row | changes


def test_every_f1_issue_code_has_a_row() -> None:
    assert set(ISSUE_TABLE) == set(ISSUE_CODES)


@pytest.mark.parametrize("code", sorted(ISSUE_TABLE))
def test_a_word_mark_takes_its_reason_from_the_pages_issue(code: str) -> None:
    unsure = span_flag(span(read_issues=[code]))
    illegible = span_flag(span(read_issues=[code], quality="illegible", text="[?]"))
    assert unsure is not None and illegible is not None
    backup = code in ("read_locally", "reader_refused")
    expected = "backup_reader" if backup else ISSUE_TABLE[code] or "low_confidence"
    assert (unsure.kind, unsure.reason) == (MARK, expected)
    assert unsure.band == ("review" if backup else "check")
    assert (illegible.band, illegible.reason) == ("review", expected)


def test_the_first_issue_that_explains_a_word_wins() -> None:
    assert page_reason(["blurry", "stamp_over_text"]) == "overlapping_stamp"
    assert page_reason(["glare", "faint"]) == "faint_print"
    assert page_reason([]) == "low_confidence"


def test_lines_are_flagged_only_when_unpinned_backup_read_or_on_an_unreadable_page() -> None:
    line = {"kind": "paragraph", "quality": "ok"}
    assert span_flag(span(**line, read_quality="good")) is None
    unpinned = span_flag(span(**line, box_source="model"))
    backup = span_flag(span(**line, box_source="model", read_issues=["read_locally"]))
    rapid = span_flag(span(kind=None, quality=None, box_source=None, read_issues=["reader_refused"]))
    unreadable = span_flag(span(**line, read_quality="unreadable", read_issues=["faint"]))
    assert unpinned is not None and (unpinned.kind, unpinned.band, unpinned.reason) == (
        LINE,
        "review",
        "spot_unconfirmed",
    )
    assert backup is not None and (backup.band, backup.reason) == ("review", "backup_reader")
    assert rapid is not None and (rapid.kind, rapid.reason) == (LINE, "backup_reader")
    assert unreadable is not None and (unreadable.band, unreadable.reason) == ("review", "faint_print")


def test_every_label_reason_has_a_row() -> None:
    assert set(VALUE_TABLE) == set(EVERY_REASON)


def test_the_file_list_counts_the_values_the_review_queue_lists() -> None:
    """KZ-004: the SQL picks the reasons value_flag flags (a fixed value aside), out of the
    values still waiting to be verified."""
    picked = set(re.findall(r"'(\w+)'", FLAGGED_VALUE.split(" IN ")[1].split(")")[0]))
    assert picked == {reason for reason, flag in VALUE_TABLE.items() if flag and reason != "corrected"}
    # Changed on purpose (QA issue #366, 25 Oct 2026): the list's count and attention filter
    # use the review queue's one definition of a value still waiting, no longer the review
    # screen's flagged values, which left single_reader out and said "All values verified".
    assert FLAGGED_VALUE.startswith(f"({WAITING_VALUE} AND ")
    assert WAITING_VALUE in listing._COLUMNS and WAITING_VALUE in listing._NEEDS_ATTENTION
    assert FLAGGED_VALUE not in listing._COLUMNS and FLAGGED_VALUE not in listing._NEEDS_ATTENTION
    for statement in (review._QUEUE, review._QUEUE_FOR_DOCUMENT, review._COUNT, review._COUNT_FOR_DOCUMENT):
        assert WAITING_VALUE in statement


@pytest.mark.parametrize("reason", sorted(VALUE_TABLE))
def test_a_value_is_flagged_by_its_label_reason(reason: str) -> None:
    flag = value_flag(value(label_reason=reason))
    expected = VALUE_TABLE[reason]
    assert (None if flag is None else (flag.band, flag.reason)) == expected
    if flag is not None:
        assert (flag.kind, flag.value_id, flag.text, flag.as_read) == (
            VALUE,
            "7c1ea497-85ae-4823-ae3c-998f1793220d",
            "Rs 4,850",
            "Rs 4,850",
        )


def test_a_verified_value_is_never_flagged_and_a_fixed_one_only_with_its_change() -> None:
    assert value_flag(value(label="verified", label_reason="readers_agree")) is None
    assert value_flag(value(label="verified", label_reason="corrected")) is None
    kept = value_flag(value(label="verified", label_reason="corrected"), ("check", "faint_print"))
    assert kept is not None and (kept.band, kept.reason) == ("check", "faint_print")


def test_a_vote_with_no_majority_is_review_readings_differ_with_every_reading() -> None:
    votes = {
        "value": None,
        "agreed": 1,
        "readers": 3,
        "reads": [{"text": "Rs 4,850"}, {"text": "Rs 4,350"}, {"text": None}],
    }
    flag = value_flag(value(label_reason="unsure_text", votes=votes, second_reading="Rs 4,380"))
    assert flag is not None
    assert (flag.band, flag.reason, flag.score) == ("review", "readings_differ", 0.3333)
    assert flag.alternatives == ("Rs 4,350", "Rs 4,380")


def test_a_score_is_given_only_when_measured() -> None:
    unmeasured = span_flag(span())
    assert unmeasured is not None and unmeasured.score is None
    measured = span_flag(span(confidence=0.62))
    assert measured is not None and measured.score == 0.62
    flag = value_flag(value())
    assert flag is not None and flag.score is None
    confident = value_flag(value(confidence=0.4))
    assert confident is not None and confident.score == 0.4


def test_alternatives_are_distinct_never_the_current_text_and_at_most_three() -> None:
    readings = ["Rs 4,850", "Rs 4,350", None, "Rs 4,350", "", "Rs 4,380", "Rs 4,880", "Rs 4,800"]
    assert alternatives("Rs 4,850", readings) == ("Rs 4,350", "Rs 4,380", "Rs 4,880")
    assert alternatives("Rs 4,850", ["Rs 4,850"]) == ()


def test_the_word_key_is_stable_and_names_kind_page_spot_and_text() -> None:
    key = word_key(MARK, 1, BOX, "4,850")
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    # Pinned: stored changes are found by this key.
    assert key == word_key(MARK, 1, {"page": 1, "box": [0.5, 0.1, 0.6, 0.15], "kind": "box"}, "4,850")
    assert key == "cb53f5751c1e44469de85eaed70ca106fa481044eae250ec039ded7b28f7f034"
    others = {
        word_key(LINE, 1, BOX, "4,850"),
        word_key(MARK, 2, BOX, "4,850"),
        word_key(MARK, 1, {**BOX, "box": [0.5, 0.1, 0.61, 0.15]}, "4,850"),
        word_key(MARK, 1, BOX, "4,350"),
    }
    assert key not in others and len(others) == 4
    flag = span_flag(span())
    assert flag is not None and flag.key == key


def test_reasons_are_plain_words_and_never_unusual_font() -> None:
    assert all(text == text.lower() and text.strip() for text in REASONS.values())
    assert "unusual font" not in REASONS.values()
    produced = {page_reason([code]) for code in ISSUE_CODES} | {"backup_reader", "readings_differ"}
    produced |= {"spot_unconfirmed", "mixed_language"}
    assert produced <= set(REASONS)


def test_occurrences_counts_overlapping_ones_so_once_means_one_place() -> None:
    assert occurrences("Rs 4,850", "4,850") == 1
    assert occurrences("4,850 of 4,850", "4,850") == 2
    assert occurrences("aaa", "aa") == 2
    assert occurrences("Rs 4,850", "") == 0


def test_overlaps_needs_two_boxes_on_one_page() -> None:
    line = {"kind": "box", "page": 1, "box": [0.1, 0.1, 0.9, 0.15]}
    assert overlaps(BOX, line)
    assert not overlaps(BOX, {**line, "page": 2})
    assert not overlaps(BOX, {"kind": "box", "page": 1, "box": [0.7, 0.1, 0.75, 0.15]})
    assert not overlaps(BOX, {"kind": "cell", "page": 1, "row": 1, "column": 1})
    assert not overlaps(BOX, {"kind": "box"})
