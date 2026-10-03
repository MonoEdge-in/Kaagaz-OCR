"""Check and fold the text of "my words": a word a user defined, and its meaning.

Pure functions. The fold finds duplicates, so "Malak", "MALAK" and " malak " are
one word: Unicode NFC, case folded (then NFC again), white space runs made one
space, trimmed.

* A word: 1 to 40 characters of letters, marks (Devanagari vowel signs),
  digits, spaces and hyphens, with a letter, in at most 3 parts. Refused when
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
    for character in text:
        category = unicodedata.category(character)
        if not (category[0] in "LM" or category == "Nd" or character in extra):
            raise WordTextError("characters")
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
    built_in = tuple(parts) in _HINDI_PHRASES or any(
        part in _HINDI_WORDS or part in _ENGLISH_WORDS for part in parts
    )
    return CheckedWord(word=word, built_in=built_in)


def check_meaning(value: str) -> WordText:
    """A meaning as typed, checked and folded. Raises WordTextError."""
    return _checked(value, MEANING_MAX, " -,")
