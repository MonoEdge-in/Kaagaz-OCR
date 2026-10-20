"""Versions (Phase 2 PRD 1, V1 to V4, V6, V11): the rules with no database.

* ``fingerprint`` (V1): the reader text normalised (NFKC, lower case, Devanagari digits as
  ASCII, punctuation dropped, spaces collapsed), every run of SHINGLE words, MinHash with
  HASHES blake2b values (each seed its own salt) and LSH in BANDS bands of ROWS values, each
  band hashed to one number. Under MIN_WORDS words there is none. Values are signed 64-bit,
  so they fit ``bigint``.
* ``name_key`` (V2): the rule of migration 0041's ``NAME_KEY``, step for step, so a new
  upload's key is the same as the backfill's (tests/test_versions_db.py compares them).
* ``outcome`` (V4): the scoring table, ``reasons`` the banner's plain words (V6).
* ``compare`` (V11): a word-level difflib comparison, page by page, as runs of same, added
  and removed, with a summary.

hashlib and difflib only (the PRD: no new library).
"""

import hashlib
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

# V4: the starting values (docs/architecture.md records them once tuned on the bulk test).
AUTO_NAME, AUTO_TEXT = 0.8, 0.95
TEXT_SIGNAL, NAME_SIGNAL, TEXT_ALONE = 0.8, 0.6, 0.9
AUTO, SUGGESTED = "auto", "suggested"

# V11: a page longer than this is compared on its first words only, and the pages together on
# at most MAX_COMPARE_TOTAL words (later pages then truncated), so one request stays bounded.
MAX_COMPARE_WORDS = 5_000
MAX_COMPARE_TOTAL = 30_000


def words(text: str) -> list[str]:
    """V1's normalised words: NFKC, lower case, Devanagari digits as ASCII, punctuation
    (Unicode P*) as a space, then split on spaces. O(n)."""
    folded = fold_digits(unicodedata.normalize("NFKC", text).lower())
    return "".join(" " if unicodedata.category(ch)[0] == "P" else ch for ch in folded).split()


def _hash64(data: bytes, salt: bytes) -> int:
    return int.from_bytes(hashlib.blake2b(data, digest_size=8, salt=salt).digest(), "big", signed=True)


def fingerprint(text: str) -> tuple[list[int], list[int]] | None:
    """(MinHash values, band hashes) of ``text``, or None under MIN_WORDS words.
    O(HASHES * s) blake2b calls for the s distinct shingles."""
    found = words(text)[:MAX_WORDS]
    if len(found) < MIN_WORDS:
        return None
    shingles = {" ".join(found[i : i + SHINGLE]).encode() for i in range(len(found) - SHINGLE + 1)}
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


def outcome(
    text_overlap: float | None,
    name_similarity: float | None,
    shared_identifier: bool,
    *,
    same_type: bool,
    same_folder: bool,
) -> str | None:
    """V4: ``auto`` (linked on its own, with Undo), ``suggested`` or None (nothing). A shared
    identifier alone is never enough."""
    text, name = text_overlap or 0.0, name_similarity or 0.0
    if name >= AUTO_NAME and text >= AUTO_TEXT and same_type and same_folder:
        return AUTO
    if text >= TEXT_ALONE or (text >= TEXT_SIGNAL) + (name >= NAME_SIGNAL) + shared_identifier >= 2:
        return SUGGESTED
    return None


def reasons(text_overlap: float | None, name_similarity: float | None, shared_identifier: str | None) -> str:
    """V6: the signals that matched, in plain words: "91% of the text is the same and the
    agreement number matches"."""
    parts = []
    if text_overlap is not None and text_overlap >= TEXT_SIGNAL:
        parts.append(f"{round(text_overlap * 100)}% of the text is the same")
    if name_similarity is not None and name_similarity >= NAME_SIGNAL:
        parts.append("the name is almost the same")
    if shared_identifier:
        parts.append(f"the {shared_identifier.replace('_', ' ')} matches")
    if not parts:
        return ""
    sentence = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return sentence[0].upper() + sentence[1:]


# --- V11 -------------------------------------------------------------------------------


def compare(left: Mapping[int, str], right: Mapping[int, str], *, max_pages: int) -> dict[str, Any]:
    """Page by page (at most ``max_pages``), the words of ``left`` against ``right``: runs of
    ``same``, ``removed`` (only in left) and ``added`` (only in right), each page's count of
    changes, and a summary ("2 changes on page 2"). O(w^2) worst case per page of w words."""
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
        changes = 0
        for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op == "equal":
                runs.append({"kind": "same", "text": " ".join(a[i1:i2])})
                continue
            changes += 1
            if i2 > i1:
                runs.append({"kind": "removed", "text": " ".join(a[i1:i2])})
            if j2 > j1:
                runs.append({"kind": "added", "text": " ".join(b[j1:j2])})
        pages.append({"page_no": page_no, "changes": changes, "truncated": cut, "runs": runs})
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
