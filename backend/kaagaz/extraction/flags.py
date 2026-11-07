"""Which words of a scanned document are flagged for review, how strongly, and why (scan
review UX rules, Tushar, 16 Oct 2026; the council plan of 16 Oct 2026).

Pure functions over the stored reading: no flag table, so a flag is always what the
reading says now (kaagaz/db/word_review.py reads the rows). A flag is one of:

* a word mark (``mark``, PRD 5 F3): a word the reader was unsure of, or ``[?]`` where it
  could not read;
* a line (``line``) whose spot no box confirmed (G5, ``box_source`` model), or on a page the
  backup reader read (``read_locally``, ``reader_refused``) or graded unreadable (F2), or
  that Ensemble read differently after the scan (``ensemble``: changed or unresolved);
* a value (``value``) the readers read differently (``readers_disagree``, or an Ensemble
  vote with no majority), read with low confidence, or read in an unexpected script
  (``ocr_script``).

Bands (spec section 2): ``review`` (unclear source, please confirm) for an illegible mark,
an unpinned line, any line or mark on a backup-read or unreadable page, and readings that
differ; ``check`` (a second look is wise) for an unsure mark and a low-confidence or
other-script value. Everything else is High: no flag.

Reasons are plain words (Tushar's list, 16 Oct 2026), from the page's F1 issues where one
explains the word, else from the flag itself; never "unusual font" (nothing measures it).
The exact score is given only when something measured it: the span's or value's
confidence, else the share of readers that agreed; otherwise None, never made up.
Alternatives (at most three, distinct, never the current text) come only from other
readings that exist: the vote's reads, then the checker's second reading.

``word_key`` is SHA-256 of the kind, page, spot and text as read, so a word keeps its key
when another page of the document is read again.
"""

import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from kaagaz.reading.spans import BoxLocation, location_from_json

MARK, LINE, VALUE = "mark", "line", "value"
CHECK, REVIEW = "check", "review"
MAX_ALTERNATIVES = 3

BACKUP_READER = "backup_reader"
READINGS_DIFFER = "readings_differ"
SPOT_UNCONFIRMED = "spot_unconfirmed"
MIXED_LANGUAGE = "mixed_language"
LOW_CONFIDENCE = "low_confidence"

# Every reason a flag may give, with the words the user sees (Tushar approved, 16 Oct 2026).
REASONS: Mapping[str, str] = MappingProxyType(
    {
        BACKUP_READER: "read by the backup reader",
        READINGS_DIFFER: "readings differ",
        SPOT_UNCONFIRMED: "spot on the page not confirmed",
        "overlapping_stamp": "overlapping stamp",
        "handwriting": "handwriting",
        "cut_off_edge": "cut-off edge",
        "low_resolution_scan": "low-resolution scan",
        "smudged_print": "smudged print",
        "faint_print": "faint print",
        "glare_or_shadow": "glare or shadow",
        "overwritten_text": "overwritten text",
        MIXED_LANGUAGE: "mixed language",
        LOW_CONFIDENCE: "reading confidence low",
    }
)
# The page's F1 issues (kaagaz.reading.page_quality) that explain a word, first one wins.
ISSUE_REASONS: tuple[tuple[str, str], ...] = (
    ("stamp_over_text", "overlapping_stamp"),
    ("handwriting_hard", "handwriting"),
    ("cut_off", "cut_off_edge"),
    ("low_resolution", "low_resolution_scan"),
    ("blurry", "smudged_print"),
    ("faint", "faint_print"),
    ("glare", "glare_or_shadow"),
    ("shadow", "glare_or_shadow"),
    ("overwritten", "overwritten_text"),
)
BACKUP_ISSUES = frozenset({"read_locally", "reader_refused"})
WORD_MARK_QUALITIES = frozenset({"unsure", "illegible"})
# The needs-review reasons a value has because of the flags in its box: once every flag
# there is confirmed or edited by a person, the value may be Verified. Any other reason
# (check_failed, single_reader, model_chosen) has no flag behind it and is left as it is.
COVERED_REASONS = frozenset(
    {"readers_disagree", "low_confidence", "ocr_script", "unsure_text", "box_unanchored", "local_reader"}
)


@dataclass(frozen=True, slots=True)
class Flag:
    """One flagged word. ``text`` is the stored text now (a value's ``value_text``, a
    span's text); ``as_read`` is what the reader read, part of ``key``."""

    key: str
    kind: str
    page_no: int
    region: Mapping[str, Any]
    as_read: str
    text: str
    band: str
    reason: str
    score: float | None
    alternatives: tuple[str, ...] = ()
    value_id: str | None = None


def word_key(kind: str, page_no: int, region: Mapping[str, Any], as_read: str) -> str:
    """SHA-256 in lower-case hex of the canonical JSON of [kind, page, region, as_read]."""
    canonical = json.dumps(
        [kind, page_no, region, as_read], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def page_reason(issues: Collection[str]) -> str:
    """The reason a page's F1 issues give a word on it, else reading confidence low."""
    return next((reason for issue, reason in ISSUE_REASONS if issue in issues), LOW_CONFIDENCE)


def span_flag(row: Mapping[Any, Any]) -> Flag | None:
    """The flag of one stored span read by OCR, or None when it is High. ``row`` has the
    span's ``kind``, ``quality``, ``box_source``, ``text``, ``region``, ``confidence`` and
    its page's ``page_no``, ``read_quality`` and ``read_issues``."""
    issues = tuple(row["read_issues"] or ())
    backup = not BACKUP_ISSUES.isdisjoint(issues)
    unreadable = row["read_quality"] == "unreadable"
    if row["kind"] is None and row["quality"] in WORD_MARK_QUALITIES:
        kind = MARK
        band = REVIEW if row["quality"] == "illegible" or backup or unreadable else CHECK
        reason = BACKUP_READER if backup else page_reason(issues)
    elif backup:
        kind, band, reason = LINE, REVIEW, BACKUP_READER
    elif row["box_source"] == "model":
        kind, band, reason = LINE, REVIEW, SPOT_UNCONFIRMED
    elif unreadable:
        kind, band, reason = LINE, REVIEW, page_reason(issues)
    elif row.get("ensemble"):
        kind, band, reason = LINE, REVIEW, READINGS_DIFFER
    else:
        return None
    page_no, region, text = int(row["page_no"]), row["region"], str(row["text"])
    return Flag(
        word_key(kind, page_no, region, text),
        kind,
        page_no,
        region,
        text,
        text,
        band,
        reason,
        _score(row["confidence"]),
    )


def value_key(row: Mapping[Any, Any]) -> str:
    """A value's word key: its page, spot and raw text as read, which a fix never changes."""
    return word_key(VALUE, int(row["page_no"]), row["region"], str(row["value_raw"]))


def value_flag(row: Mapping[Any, Any], settled: tuple[str, str] | None = None) -> Flag | None:
    """The flag of one value, or None. ``row`` has the value's columns (id, page_no, region,
    value_text, value_raw, second_reading, confidence, label, label_reason, votes) and its
    page's ``read_issues``. A value a person already fixed (``corrected``) is a flag only
    with ``settled``, the (band, reason) it had when the word change that fixed it was made."""
    votes = row["votes"] if isinstance(row["votes"], Mapping) else None
    reason = row["label_reason"]
    if reason == "corrected":
        if settled is None:
            return None
        band, why = settled
    elif row["label"] != "needs_review":
        return None
    elif reason == "readers_disagree" or (votes is not None and votes.get("value") is None):
        band, why = REVIEW, READINGS_DIFFER
    elif reason == "low_confidence":
        band, why = CHECK, page_reason(tuple(row["read_issues"] or ()))
    elif reason == "ocr_script":
        band, why = CHECK, MIXED_LANGUAGE
    else:
        return None
    text = str(row["value_text"])
    score = _score(row["confidence"])
    if score is None and votes is not None and votes.get("readers"):
        score = round(int(votes.get("agreed") or 0) / int(votes["readers"]), 4)
    reads = [read.get("text") for read in votes.get("reads", [])] if votes is not None else []
    others = alternatives(text, [*reads, row["second_reading"]])
    return Flag(
        value_key(row),
        VALUE,
        int(row["page_no"]),
        row["region"],
        str(row["value_raw"]),
        text,
        band,
        why,
        score,
        others,
        str(row["id"]),
    )


def alternatives(current: str, readings: Sequence[object]) -> tuple[str, ...]:
    """At most MAX_ALTERNATIVES other readings, in order, distinct, never ``current``."""
    found: list[str] = []
    for reading in readings:
        if isinstance(reading, str) and reading.strip() and reading != current and reading not in found:
            found.append(reading)
    return tuple(found[:MAX_ALTERNATIVES])


def overlaps(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Two stored regions are boxes on the same page that share some area (a cell never)."""
    first, second = _box(a), _box(b)
    if first is None or second is None or first.page != second.page:
        return False
    return max(first.x0, second.x0) < min(first.x1, second.x1) and max(first.y0, second.y0) < min(
        first.y1, second.y1
    )


def centre_in(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Box ``a``'s centre is in box ``b`` on the same page (a box inside ``b`` too; a cell never)."""
    inner, outer = _box(a), _box(b)
    if inner is None or outer is None or inner.page != outer.page:
        return False
    x, y = (inner.x0 + inner.x1) / 2, (inner.y0 + inner.y1) / 2
    return outer.x0 <= x <= outer.x1 and outer.y0 <= y <= outer.y1


def occurrences(text: str, part: str) -> int:
    """How many times ``part`` is in ``text``, overlapping ones too ("aa" is twice in "aaa"),
    so "exactly once" means one place only."""
    return len(re.findall(f"(?={re.escape(part)})", text)) if part else 0


def _box(region: Mapping[str, Any]) -> BoxLocation | None:
    try:
        location = location_from_json(dict(region))
    except (KeyError, TypeError, ValueError):
        return None
    return location if isinstance(location, BoxLocation) else None


def _score(confidence: object) -> float | None:
    return float(confidence) if isinstance(confidence, int | float) and 0 <= confidence <= 1 else None
