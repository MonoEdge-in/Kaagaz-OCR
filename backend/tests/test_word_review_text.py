"""The words word review sends (kaagaz/api/routers/word_review.py, kaagaz.extraction.flags):
the summary bar and export status in the spec's own shape, and the spec's banned words
(section 6) in none of them. Pure: no database.
"""

import re

import pytest

from kaagaz.api.routers import word_review as router
from kaagaz.extraction.flags import REASONS

# Scan review UX rules, section 6: never over-commit, never lower confidence.
BANNED = (
    "perfect", "flawless", "100% accurate", "guaranteed", "always correct", "error-free", "zero mistakes",
    "instant", "effortless", "magic", "revolutionary", "best-in-class", "ai-powered", "fixed",
    "you don't need to check", "fully verified",
    "oops", "sorry", "unfortunately", "we think", "maybe", "probably", "we're not sure", "might be wrong",
    "failed to understand", "garbage", "bad scan", "can't read", "confused", "struggling", "uh-oh", "hmm",
    "try your luck", "hopefully", "something went wrong",
)  # fmt: skip


def every_string() -> list[str]:
    samples = [
        router.summary_text(14, 2431, 12),
        router.summary_text(1, 1, 0),
        router.export_text(12, 12, 0),
        router.export_text(12, 9, 3),
        router.export_text(0, 0, 0),
    ]
    fixed = [router.ENSEMBLE_WAIT, router.ENSEMBLE_ROLE, router.ENSEMBLE_OFF, router.GONE]
    return [*REASONS.values(), *router.MESSAGES.values(), *fixed, *samples]


def test_the_summary_bar_says_pages_words_and_flags() -> None:
    assert (
        router.summary_text(14, 2431, 12) == "Scan complete · 14 pages · 2,431 words · 12 flagged for review"
    )
    assert router.summary_text(14, 2431, 0) == "Scan complete · 14 pages · 2,431 words · Nothing flagged"
    assert router.summary_text(1, 1, 1) == "Scan complete · 1 page · 1 word · 1 flagged for review"


def test_the_export_status_says_what_is_left() -> None:
    assert router.export_text(12, 12, 0) == "12 flagged · 12 reviewed. Ready to export."
    assert router.export_text(12, 9, 3) == "3 flagged words not yet reviewed. Export anyway or review first."
    assert router.export_text(5, 4, 1) == "1 flagged word not yet reviewed. Export anyway or review first."
    assert router.export_text(0, 0, 0) == "Nothing flagged. Ready to export."


@pytest.mark.parametrize("words", every_string())
def test_no_banned_word_and_no_exclamation_mark(words: str) -> None:
    found = [phrase for phrase in BANNED if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", words.lower())]
    assert found == [], words
    assert "!" not in words
