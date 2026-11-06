"""Versions (Phase 2 PRD 1, V1 to V4, V6, V11): the rules with no database.

* ``fingerprint`` (V1): the reader text normalised (NFKC, lower case, Devanagari digits as
  ASCII, punctuation dropped, spaces collapsed), every run of SHINGLE words, MinHash with
  HASHES blake2b values (each seed its own salt) and LSH in BANDS bands of ROWS values, each
  band hashed to one number. Under MIN_WORDS words there is none. Values are signed 64-bit,
  so they fit ``bigint``. ``exact_overlap`` (#521) counts the same shingles exactly: 64 hashes
  estimate an overlap near 0.8 only to about 0.05 either way.
* ``name_key`` (V2): the rule of migration 0041's ``NAME_KEY``, step for step, so a new
  upload's key is the same as the backfill's (tests/test_versions_db.py compares them).
* ``outcome`` (V4): the scoring table, ``reasons`` the banner's plain words (V6, NB-17).
* ``compare`` (V11): a word-level difflib comparison, page by page, as runs of same, added
  and removed, with a summary.

hashlib and difflib only (the PRD: no new library).
"""

import hashlib
import math
import re
import unicodedata
from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import Any

from kaagaz.chunking.script import fold_digits

MIN_WORDS = 20
SHINGLE = 5
HASHES = 64
BANDS = 16
ROWS = HASHES // BANDS
# ponytail: a change after this many words is not seen; a streaming MinHash if files are bigger.
MAX_WORDS = 50_000
_SALTS = tuple(seed.to_bytes(16, "big") for seed in range(HASHES))
_BAND_SALTS = tuple((HASHES + band).to_bytes(16, "big") for band in range(BANDS))

# V4 as Tushar set it on 6 Nov 2026 (#521; docs/architecture.md): 80% of the text alone, or
# two signals, suggest; nothing is ever linked on its own (rows linked so before stay "auto").
TEXT_SIGNAL, NAME_SIGNAL, TEXT_ALONE = 0.8, 0.6, 0.8
# Name and identifier also need this much of the text when both have a fingerprint (monthly
# bills: one name key and account number, other text).
TEXT_FLOOR = 0.5
SUGGESTED = "suggested"

# V11: a page longer than this is compared on its first words only, and the pages together on
# at most MAX_COMPARE_TOTAL words (later pages then truncated), so one request stays bounded.
MAX_COMPARE_WORDS = 5_000
MAX_COMPARE_TOTAL = 30_000


def words(text: str) -> list[str]:
    """V1's normalised words: NFKC, lower case, Devanagari digits as ASCII, punctuation
    (Unicode P*) as a space, then split on spaces. O(n)."""
    folded = fold_digits(unicodedata.normalize("NFKC", text).lower())
    return "".join(" " if unicodedata.category(ch)[0] == "P" else ch for ch in folded).split()


def _shingles(found: list[str]) -> set[str]:
    """Every run of SHINGLE words."""
    return {" ".join(found[i : i + SHINGLE]) for i in range(len(found) - SHINGLE + 1)}


def _hash64(data: bytes, salt: bytes) -> int:
    return int.from_bytes(hashlib.blake2b(data, digest_size=8, salt=salt).digest(), "big", signed=True)


def fingerprint(text: str) -> tuple[list[int], list[int]] | None:
    """(MinHash values, band hashes) of ``text``, or None under MIN_WORDS words.
    O(HASHES * s) blake2b calls for the s distinct shingles."""
    found = words(text)[:MAX_WORDS]
    if len(found) < MIN_WORDS:
        return None
    shingles = {shingle.encode() for shingle in _shingles(found)}
    minhash = [min(_hash64(shingle, salt) for shingle in shingles) for salt in _SALTS]
    bands = [
        _hash64(
            b"".join(value.to_bytes(8, "big", signed=True) for value in minhash[n * ROWS : (n + 1) * ROWS]),
            salt,
        )
        for n, salt in enumerate(_BAND_SALTS)
    ]
    return minhash, bands


def overlap(first: list[int] | None, second: list[int] | None) -> float | None:
    """V3's estimated text overlap: the share of the HASHES values that match; None when
    either has no fingerprint."""
    if not first or not second:
        return None
    return sum(a == b for a, b in zip(first, second, strict=True)) / HASHES


def exact_overlap(first: Mapping[int, str], second: Mapping[int, str]) -> float | None:
    """#521: the exact share of SHINGLE-word runs two texts have in common (their Jaccard), each
    text's pages read in order as ``fingerprint`` reads them. None when either has under
    MIN_WORDS words, a page over MAX_COMPARE_WORDS or over MAX_COMPARE_TOTAL words in all (V11's
    limits); then only the estimate is known. O(w) for the w words."""
    found = []
    for pages in (first, second):
        page_words = [words(pages[page_no]) for page_no in sorted(pages)]
        total = sum(map(len, page_words))
        too_long = any(len(each) > MAX_COMPARE_WORDS for each in page_words)
        if too_long or not MIN_WORDS <= total <= MAX_COMPARE_TOTAL:
            return None
        found.append(_shingles([word for each in page_words for word in each]))
    mine, theirs = found
    return len(mine & theirs) / len(mine | theirs)


# --- V2: the name key, as 0041's NAME_KEY -------------------------------------------------
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}\Z")
_DATE = re.compile(r"[0-9]{1,4}[-./][0-9]{1,2}[-./][0-9]{1,4}")
_START, _END = r"(?<!\w)", r"(?<=\w)(?!\w)"  # PostgreSQL's \m and \M
_MARKER = re.compile(
    rf"{_START}(?:v|ver|version|rev)\s*[0-9]*{_END}|{_START}(?:final|latest|copy){_END}|\([0-9]+\)"
)
_TYPOGRAPHIC = "".join(map(chr, (0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D)))  # as 0041's
_PUNCTUATION = re.compile(f"[!-/:-@\\[-`{{-~।॥{_TYPOGRAPHIC}]+")
_SPACES = re.compile(r"\s+")


def name_key(display_name: str) -> str:
    """V2: the file name lower case with no extension, Devanagari digits as ASCII, dates,
    version markers (v2, ver 3, rev, final, latest, copy, (1)) and punctuation removed, and
    single spaces. O(n)."""
    key = fold_digits(_EXTENSION.sub("", display_name).lower())
    for pattern in (_DATE, _MARKER, _PUNCTUATION):
        key = pattern.sub(" ", key)
    return _SPACES.sub(" ", key).strip(" ")


# --- V4 and V6 --------------------------------------------------------------------------


def outcome(text_overlap: float | None, name_similarity: float | None, shared_identifier: bool) -> str | None:
    """V4 (#521): ``suggested`` or None (nothing); a person always decides. A shared identifier
    alone is never enough, and two signals without the text need TEXT_FLOOR of it when there is
    a text overlap (None: a text under MIN_WORDS words)."""
    text, name = text_overlap or 0.0, name_similarity or 0.0
    if text >= TEXT_ALONE:
        return SUGGESTED
    signals = (text >= TEXT_SIGNAL) + (name >= NAME_SIGNAL) + shared_identifier
    if signals >= 2 and (text_overlap is None or text >= TEXT_FLOOR):
        return SUGGESTED
    return None


def reasons(
    text_overlap: float | None,
    name_similarity: float | None,
    shared_identifier: str | None,
    *,
    estimated: bool = False,
) -> str:
    """V6 and NB-17 (#521): why, for the banner's brackets. "86% the same text" (with "about"
    when only estimated) when the text led; else the name and identifier in plain words, "same
    name and same survey number"; "" when nothing counted."""
    if text_overlap is not None and text_overlap >= TEXT_ALONE:
        about = "about " if estimated else ""
        return f"{about}{share_percent(text_overlap, estimated=estimated)}% the same text"
    parts = []
    if name_similarity is not None and name_similarity >= NAME_SIGNAL:
        parts.append("same name" if name_similarity >= 1 else "similar name")
    if shared_identifier:
        parts.append(f"same {shared_identifier.replace('_', ' ')}")
    return " and ".join(parts)


def share_percent(text_overlap: float, *, estimated: bool) -> int:
    """#521: the whole percent below the overlap (86.9% is 86), at most 99, and 100 only when it
    was counted exactly the same (never from the estimate). The overlap is stored as a 4-byte
    real, so it is first rounded to 4 places of the percent (0.83 reads 0.8299999833: 83)."""
    percent = math.floor(round(text_overlap * 100, 4))
    if percent >= 100:
        return 100 if text_overlap >= 1 and not estimated else 99
    return percent


# --- V11 -------------------------------------------------------------------------------


def compare(left: Mapping[int, str], right: Mapping[int, str], *, max_pages: int) -> dict[str, Any]:
    """Page by page (at most ``max_pages``), the words of ``left`` against ``right``: runs of
    ``same``, ``removed`` (only in left) and ``added`` (only in right), each page's count of
    changes, words changed (a replacement counts its longer side) and words on each side, and a
    summary ("2 changes on page 2"). O(w^2) worst case per page of w words."""
    last = min(max([*left, *right], default=0), max_pages)
    pages = []
    budget = MAX_COMPARE_TOTAL
    for page_no in range(1, last + 1):
        a, b = left.get(page_no, "").split(), right.get(page_no, "").split()
        cap = min(MAX_COMPARE_WORDS, budget)
        cut = len(a) > cap or len(b) > cap
        a, b = a[:cap], b[:cap]
        budget -= max(len(a), len(b))
        runs: list[dict[str, str]] = []
        changes = changed = 0
        for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op == "equal":
                runs.append({"kind": "same", "text": " ".join(a[i1:i2])})
                continue
            changes += 1
            changed += max(i2 - i1, j2 - j1)
            if i2 > i1:
                runs.append({"kind": "removed", "text": " ".join(a[i1:i2])})
            if j2 > j1:
                runs.append({"kind": "added", "text": " ".join(b[j1:j2])})
        pages.append(
            {
                "page_no": page_no,
                "changes": changes,
                "words_changed": changed,
                "left_words": len(a),
                "right_words": len(b),
                "truncated": cut,
                "runs": runs,
            }
        )
    truncated = any(page["truncated"] for page in pages)
    return {"pages": pages, "summary": summary(pages), "truncated": truncated}


def summary(pages: list[dict[str, Any]]) -> str:
    """The summary: "No changes", "1 change on page 2" or "5 changes on 3 pages"."""
    changed = [page for page in pages if page["changes"]]
    total = sum(page["changes"] for page in changed)
    if not changed:
        return "No changes"
    noun = "change" if total == 1 else "changes"
    if len(changed) == 1:
        return f"{total} {noun} on page {changed[0]['page_no']}"
    return f"{total} {noun} on {len(changed)} pages"


def _page_list(numbers: list[int]) -> str:
    """ "page 2", "pages 2 and 5", "pages 2, 4 and 7"."""
    if len(numbers) == 1:
        return f"page {numbers[0]}"
    return "pages " + ", ".join(map(str, numbers[:-1])) + f" and {numbers[-1]}"


def overview(pages: list[dict[str, Any]], overlap: float | None, *, truncated: bool = False) -> str:
    """#521, See the differences: the compare in one line of plain words. "94% the same. 38
    words changed on pages 2 and 5. Page 6 is new." The share is the exact overlap (None: a
    text under MIN_WORDS words, no share said); 1.0, or no change at all, is "The text is the
    same in both files." Over V11's limits (``truncated``) only that is said."""
    if truncated:
        return "These files are long, so only the first part is compared."
    if (overlap is not None and overlap >= 1) or not any(page["words_changed"] for page in pages):
        return "The text is the same in both files."
    said = [] if overlap is None else [f"{share_percent(overlap, estimated=False)}% the same."]
    new = [page["page_no"] for page in pages if not page["left_words"] and page["right_words"]]
    gone = [page["page_no"] for page in pages if page["left_words"] and not page["right_words"]]
    changed = [page for page in pages if page["words_changed"] and page["page_no"] not in new + gone]
    if changed:
        words = sum(page["words_changed"] for page in changed)
        noun = "word" if words == 1 else "words"
        said.append(f"{words} {noun} changed on {_page_list([page['page_no'] for page in changed])}.")
    for numbers, what in ((new, "new"), (gone, "gone")):
        if numbers:
            verb = "is" if len(numbers) == 1 else "are"
            said.append(f"{_page_list(numbers)[0].upper()}{_page_list(numbers)[1:]} {verb} {what}.")
    return " ".join(said)
