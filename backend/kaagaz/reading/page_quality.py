"""How well each page was read, and what the user sees about it (PRD 5 F1, F2).

Every page gets a reading result, stored on its ``pages`` row:

* ``read_quality``, one of ``READ_QUALITIES``: ``good``, ``uncertain`` or
  ``unreadable`` (F2). Text-layer and cell pages are good.
* ``read_issues``, codes from the F1 table (``ISSUES``): why the page is not
  good, each with the sentence the user sees and the fix they can make.
* the counts ``unsure_words``, ``illegible_words`` and ``unpinned_lines``.

The reading model also grades the page itself in its reply (G3): its
``page.quality`` is one of ``MODEL_QUALITIES`` (good, fair, poor) and its
``page.issues`` are from ``MODEL_ISSUES``. Those are the model's words, used
by the F2 rules; only the codes in the F1 table are stored in ``read_issues``,
and ``tilted`` has no row there.

The sentences are the PRD's own. Some hold a number, filled in by ``says``:
"12 words couldn't be read.", "This page was scanned at 96 DPI, ...".
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

ReadQuality = Literal["good", "uncertain", "unreadable"]
# F1: pages.read_quality.
READ_QUALITIES: frozenset[str] = frozenset({"good", "uncertain", "unreadable"})

ModelQuality = Literal["good", "fair", "poor"]
# G3: the reading model's own grade of the page.
MODEL_QUALITIES: frozenset[str] = frozenset({"good", "fair", "poor"})
# G3: the scripts the reading model may name for a page.
MODEL_SCRIPTS: tuple[str, ...] = ("latin", "devanagari", "other")
# G3 and G4: what the reading model writes for each part of a line it can't read, never a guess.
ILLEGIBLE_MARK = "[?]"
# G3: the issues the reading model may name for a page (its JSON schema's enum).
MODEL_ISSUES: tuple[str, ...] = (
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

# F1: the issue codes, and where each comes from.
BLURRY = "blurry"  # the reading model
LOW_RESOLUTION = "low_resolution"  # the page's effective DPI
FAINT = "faint"  # the reading model
GLARE = "glare"  # the reading model
SHADOW = "shadow"  # the reading model
CUT_OFF = "cut_off"  # the reading model
STAMP_OVER_TEXT = "stamp_over_text"  # the reading model
OVERWRITTEN = "overwritten"  # the reading model
HANDWRITING_HARD = "handwriting_hard"  # the reading model
ILLEGIBLE_WORDS = "illegible_words"  # [?] in lines
READERS_DISAGREE = "readers_disagree"  # V2: the checker read a value differently
UNPINNED = "unpinned"  # G5: a line no found box confirmed
READER_REFUSED = "reader_refused"  # G6: a blocked reply
READ_LOCALLY = "read_locally"  # Q7: read by the backup reader during an outage
NO_TEXT = "no_text"  # no lines on a page that is not blank


@dataclass(frozen=True, slots=True)
class Issue:
    """One row of the F1 table. ``text`` is what the user sees; when ``counts`` names
    what it counts ("word", "value", "line"), ``{n}`` in it is that count, as
    "12 words"; ``{dpi}`` is the page's effective DPI. ``fix`` is the suggested fix."""

    code: str
    text: str
    fix: str
    counts: str | None = None


_REREAD_OR_RESCAN = "Read this page again, or upload a better scan."
_CONFIRM_VALUES = "Confirm the marked values."
_LIGHT = "Take the photo without flash, away from a window, with the page flat."
_COVERED = "A stamp or overwriting covers some text."

ISSUES: Mapping[str, Issue] = MappingProxyType(
    {
        issue.code: issue
        for issue in (
            Issue(BLURRY, "This page is blurry.", "Scan it again, or hold the phone still in good light."),
            Issue(
                LOW_RESOLUTION,
                "This page was scanned at {dpi} DPI, so small letters may be misread.",
                "Scan at 300 DPI.",
            ),
            Issue(FAINT, "The text is faint.", "Scan darker, or photocopy the original first."),
            Issue(GLARE, "Glare or a shadow covers part of the page.", _LIGHT),
            Issue(SHADOW, "Glare or a shadow covers part of the page.", _LIGHT),
            Issue(CUT_OFF, "Part of the page is cut off.", "Include all four edges of the page."),
            Issue(STAMP_OVER_TEXT, _COVERED, "Check the marked words against the original."),
            Issue(OVERWRITTEN, _COVERED, "Check the marked words against the original."),
            Issue(
                HANDWRITING_HARD,
                "Some handwriting couldn't be read with certainty.",
                "Check the marked words, or type the correct value.",
            ),
            Issue(ILLEGIBLE_WORDS, "{n} couldn't be read.", _REREAD_OR_RESCAN, counts="word"),
            Issue(READERS_DISAGREE, "Two readers read {n} differently.", _CONFIRM_VALUES, counts="value"),
            Issue(UNPINNED, "The exact spot of {n} couldn't be confirmed.", _CONFIRM_VALUES, counts="line"),
            Issue(READER_REFUSED, "The reader declined this page.", "Read again, or upload a better scan."),
            Issue(
                READ_LOCALLY,
                "Read by the backup reader while the main reader was down. It will be read again"
                " automatically.",
                "Nothing to do.",
            ),
            Issue(NO_TEXT, "No text was found on this page.", "If it has text, upload a sharper scan."),
        )
    }
)
ISSUE_CODES: frozenset[str] = frozenset(ISSUES)


def says(code: str, *, count: int | None = None, dpi: int | None = None) -> str:
    """What the user sees for issue ``code``, with its count or DPI filled in.

    A code not in the F1 table is a KeyError; a count or DPI the sentence needs and
    was not given (or a negative one) is a ValueError: a sentence is never shown
    with a made-up number."""
    issue = ISSUES[code]
    if issue.counts is not None:
        if count is None or count < 0:
            raise ValueError(f"{code} needs a count of 0 or more")
        noun = issue.counts if count == 1 else f"{issue.counts}s"
        return issue.text.format(n=f"{count} {noun}")
    if "{dpi}" in issue.text:
        if dpi is None or dpi < 1:
            raise ValueError(f"{code} needs the page's DPI")
        return issue.text.format(dpi=dpi)
    return issue.text


def fix(code: str) -> str:
    """The fix the user can make for issue ``code`` (KeyError when it is not in the table)."""
    return ISSUES[code].fix
