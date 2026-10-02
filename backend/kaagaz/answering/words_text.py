"""Check and fold the text of "my words": a word a user defined, and its meaning.

Pure functions. The fold finds duplicates, so "Malak", "MALAK" and " malak " are
one word: Unicode NFC, case folded (then NFC again), white space runs made one
space, trimmed.

* A word: 1 to 40 characters of letters, marks (Devanagari vowel signs; at most
  4 on one letter), digits, spaces and hyphens, and a zero-width (non-)joiner only
  right after a virama, with a letter, in at most 3 parts. Refused when
  every part is a question, filler or date word ("what", "kya", "last month"),
  which chat reads its own way. One the Hinglish map knows ("kam" is lowest)
  is ``built_in``, so the screen can warn.
* A meaning: 1 to 80 characters of the same plus commas, with a letter. Never
  markup: ``<``, ``&`` and the like are refused, not escaped.

Errors carry a short ``reason`` for the API's message. O(n) in the text's length.
"""

import unicodedata
from dataclasses import dataclass

from kaagaz.answering.hinglish import (
    _ENGLISH_WORDS,
    _HINDI_PHRASES,
    _HINDI_STOP,
    _HINDI_WORDS,
    _NOT_KEYWORDS,
    _SHARED_STOP,
    DATE_WORDS,
)

WORD_MAX = 40
MEANING_MAX = 80
WORD_MAX_PARTS = 3

MAX_MARKS = 4  # combining marks on one letter: Devanagari needs few, a stack of 40 is noise
_VIRAMA = "\u094d"
_JOINERS = frozenset("\u200c\u200d")  # ZWNJ and ZWJ: only right after a virama (ra + virama + ZWJ)
_COMMON = _NOT_KEYWORDS | DATE_WORDS | _HINDI_STOP | _SHARED_STOP


class WordTextError(ValueError):
    """A word or meaning that is refused. ``reason`` is one of: empty, too_long,
    characters, no_letter, too_many_parts, common_word."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class WordText:
    """Checked text: as typed (spaces tidied), and its folded form."""

    text: str
    fold: str


@dataclass(frozen=True, slots=True)
class CheckedWord:
    word: WordText
    built_in: bool


def tidy(value: str) -> str:
    """NFC, with every run of white space made one space and the ends trimmed."""
    return " ".join(unicodedata.normalize("NFC", value).split())


def fold(value: str) -> str:
    """The form duplicates are found by (see the module docstring)."""
    return " ".join(unicodedata.normalize("NFC", tidy(value).casefold()).split())


def _checked(value: str, limit: int, extra: str) -> WordText:
    text = tidy(value)
    if not text:
        raise WordTextError("empty")
    if len(text) > limit:
        raise WordTextError("too_long")
    marks, previous = 0, ""
    for character in text:
        category = unicodedata.category(character)
        joiner = character in _JOINERS and previous == _VIRAMA
        if not (category[0] in "LM" or category == "Nd" or character in extra or joiner):
            raise WordTextError("characters")
        marks = marks + 1 if category[0] == "M" else 0
        if marks > MAX_MARKS:
            raise WordTextError("characters")
        previous = character
    if not any(unicodedata.category(character)[0] == "L" for character in text):
        raise WordTextError("no_letter")
    return WordText(text=text, fold=fold(text))


def check_word(value: str) -> CheckedWord:
    """A word as typed, checked and folded. Raises WordTextError."""
    word = _checked(value, WORD_MAX, " -")
    parts = word.fold.split()
    if len(parts) > WORD_MAX_PARTS:
        raise WordTextError("too_many_parts")
    if all(part in _COMMON for part in parts):
        raise WordTextError("common_word")
    return CheckedWord(word=word, built_in=is_built_in(word.fold))


def is_built_in(word_fold: str) -> bool:
    """Whether the Hinglish map already reads this folded word, or a part of it."""
    parts = word_fold.split()
    return tuple(parts) in _HINDI_PHRASES or any(
        part in _HINDI_WORDS or part in _ENGLISH_WORDS for part in parts
    )


def check_meaning(value: str) -> WordText:
    """A meaning as typed, checked and folded. Raises WordTextError."""
    return _checked(value, MEANING_MAX, " -,")
