"""Your words on the page (package H, 24 Oct 2026): the lines of one document that hold
the words a person asked or searched with, so the viewer can light them on the page image.

``clean_words``: at most ``MAX_WORDS`` words, each at most ``MAX_WORD_CHARS`` characters,
the runs of letters, marks and digits (``_WORD``) of the NFC, casefolded text, each once,
never a stop word (``hinglish.STOP_WORDS``, ``glossary.ROMANISED_STOP``) or one character.

A line holds a word (``Matcher``) in one of four ways, none looser than search
(kaagaz/search/hybrid.py):

1. the same word, in any case;
2. the same Latin spelling (``latin_spelling``, the recipe of ``pieces.text_latin``):
   "krishna" lights कृष्ण, and कृष्ण lights "Krishna";
3. the same sound (``glossary.sounds_like``, the extractive answer's rule): "Krishana"
   lights "Krishna";
4. a near spelling: pg_trgm's similarity of the two Latin spellings of at least
   ``hybrid.FUZZY_THRESHOLD``, search's typo cut-off. Similarity is never more than
   search's ``word_similarity``, so this is no looser. Letter words only: a word with a
   digit matches as written, so "1042" never lights "1043".

The lines (``segments``): a text piece keeps one region per line, in text order
(kaagaz/chunking/lines.py), so line i is region i; a table or sheet row piece keeps one
per cell, each row's cells joined by " | " as "Column: text", so cell i is region i, matched
without its column name. A piece whose lines and cells both fail to pair with its regions
is left out: its spot is unknown. ``pieces.text_latin`` pairs the same way; without it
(a mostly English piece) a line with Devanagari letters is spelled here.

Only regions leave this module, never text. Time: O(T) for the document's T characters to
split them, then each different word is matched once (cached): O(W * q) for W different
words and q <= MAX_WORDS search words.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any

from kaagaz.answering import glossary
from kaagaz.answering.evidence import valid_regions
from kaagaz.answering.hinglish import STOP_WORDS
from kaagaz.chunking.script import has_devanagari_letters, latin_spelling, sound_key
from kaagaz.search.hybrid import FUZZY_THRESHOLD
from kaagaz.search.story_match import similarity

MAX_WORDS = 8
MAX_WORD_CHARS = 40
# A letter, digit or Devanagari sign, but not the danda (U+0964, U+0965): a word goes on.
_WORD = re.compile(r"[\wऀ-ॣ०-ॿ]+")
_ASCII_LETTERS = re.compile(r"[a-z]+")
_STOP = STOP_WORDS | glossary.ROMANISED_STOP


def _words(text: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFC", text).casefold())


def _latin(word: str) -> str:
    return latin_spelling(word) if has_devanagari_letters(word) else word


def clean_words(raw: Iterable[str]) -> list[str]:
    """The search words to light (the module docstring), in the order given. O(n)."""
    found: dict[str, None] = {}
    for text in raw:
        for word in _words(text):
            word = word[:MAX_WORD_CHARS]
            if len(word) > 1 and word not in _STOP:
                found.setdefault(word)
    return list(found)[:MAX_WORDS]


class Matcher:
    """Whether a line holds one of ``words`` (cleaned), each different word decided once."""

    def __init__(self, words: Sequence[str]) -> None:
        self._exact = frozenset(words)
        self._latin = [_latin(word) for word in words]
        self._seen: dict[str, bool] = {}

    def holds(self, line: str, latin_line: str | None) -> bool:
        """``latin_line``: the line's Latin spelling, None to spell it here. O(len(line))."""
        if any(word in self._exact for word in _words(line)):
            return True
        if latin_line is None:
            latin_line = latin_spelling(line) if has_devanagari_letters(line) else line
        return any(self._near(word) for word in _words(latin_line))

    def _near(self, word: str) -> bool:
        if word not in self._seen:
            self._seen[word] = any(_near(typed, word) for typed in self._latin)
        return self._seen[word]


def _near(typed: str, word: str) -> bool:
    """Ways 2 to 4 for one Latin search word and one Latin word of a line."""
    if typed == word:
        return True
    if not (_ASCII_LETTERS.fullmatch(typed) and _ASCII_LETTERS.fullmatch(word)):
        return False
    return (
        glossary.sounds_like(typed, [(word, sound_key(word))]) or similarity(typed, word) >= FUZZY_THRESHOLD
    )


def segments(text: str, count: int) -> list[str] | None:
    """The piece's lines, or its cells without their column names, when there are ``count``
    of them (its regions); else None (the module docstring)."""
    lines = text.split("\n")
    if len(lines) == count:
        return lines
    cells = [cell for line in lines for cell in line.split(" | ")]
    if len(cells) == count:
        return [cell.partition(": ")[2] or cell for cell in cells]
    return None


def word_regions(
    pieces: Iterable[tuple[str, str | None, Sequence[Any]]], words: Sequence[str], page_no: int
) -> tuple[list[dict[str, Any]], list[int]]:
    """The regions on ``page_no`` of the lines holding ``words``, in the pieces' order, and
    every page with such a line, ascending. ``pieces``: (text, text_latin, regions)."""
    if not words:
        return [], []
    matcher = Matcher(words)
    boxes: list[dict[str, Any]] = []
    pages: set[int] = set()
    for text, text_latin, raw in pieces:
        regions = valid_regions(raw)
        lines = segments(text, len(regions)) if len(regions) == len(raw) else None
        if lines is None:
            continue
        latin = segments(text_latin, len(regions)) if text_latin is not None else None
        for index, (line, region) in enumerate(zip(lines, regions, strict=True)):
            if matcher.holds(line, None if latin is None else latin[index]):
                pages.add(region["page"])
                if region["page"] == page_no:
                    boxes.append(region)
    return boxes, sorted(pages)
