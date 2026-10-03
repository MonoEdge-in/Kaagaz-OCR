"""One expansion per question: the glossary, the asking user's own words and sound keys
(council 3 Oct 2026, My words A3, PROVISIONAL).

A search question is matched as asked, and also through alternatives, all built here
or next to here and all on hybrid.py's word list: the glossary's Marathi and Hindi
equivalents and the user's own words share the ``:g`` OR arm (``word_query``); the
question's sound keys are the ``:sk`` arm (``hybrid.sound_query``, unchanged).

**Whose words.** ``PgWords.words`` reads the asking user's words inside
``Database.user`` (the RESTRICTIVE ``own_user_only`` policy: another user's row never
comes back, whatever the SQL says), at most ``user_words.MAX_WORDS`` rows, once per
question. Nothing is cached across questions or users.

**Matching** (``expand``). Forward: a word whose parts (``_parts``: folded runs of
letters, marks and digits) are in the question one after another; a single Latin word
of 4 letters or more also by its sound key ("maalak" is malak), never a key under 3
letters or one of ``glossary.MERGED_KEYS``. Its meanings are searched. Reverse: a word
with a letter outside ASCII (बोजा) whose meaning's parts are in the question: the word is
searched, so "mortgage charge" finds बोजा. At most ``MAX_WORDS_USED`` words, each with
every meaning (at most 3).

**The safe builder** (``safe_terms``). User text never reaches ``to_tsquery`` as it is:
``quote_literal`` and ``websearch_to_tsquery`` are both unsafe for it (council,
measured). Only runs of Unicode letters and marks (categories L and M) with a letter are
kept, lower-cased NFC, 2 to 40 code points, without stop words; everything else (digits,
spaces, ``& | ! ( ) : * < - > ' \\`` and every other punctuation) is dropped, never
escaped. ``word_query`` checks each term again and adds only its own ``&``, ``|``,
``( )`` and ``:*`` (a term of ``glossary.PREFIX_MIN`` code points or more matches a
word's start, as the glossary's do). A meaning's terms are ANDed; alternatives ORed.

**The rules.** Search help only: never in ``normalise``, ``plan_fixed`` or the fixed
queries, the model's prompt, ``answer_text`` or the chat history. The gate counts a
keyword through a user's word (``Expansion.found``) only next to another keyword found
as written (evidence.py, ``"personal"``, capped); any answer whose ``understood_words``
is not empty is needs review; ``service._unbacked_latin`` never reads a meaning.

Time: ``expand`` O(W * (p + q)) for W words of p parts and a question of q parts;
``word_query`` O(u * m) for u used words of m terms; ``found`` O(u * (t + m * w)) over
a text of t characters and w words.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from kaagaz.answering import glossary
from kaagaz.answering.hinglish import STOP_WORDS
from kaagaz.answering.words_text import fold
from kaagaz.chunking.script import SOUND_KEY_MIN, sound_key
from kaagaz.db import user_words
from kaagaz.db.engine import Database

MAX_WORDS_USED = 10  # words expanded per question (council: at most 10 words, 30 meanings)
TERMS_MAX = 6  # safe terms kept from one meaning or word
TERM_MIN = 2
TERM_MAX = 40
_SOUND_WORD_MIN = 4  # letters: a shorter word matches as typed only


@dataclass(frozen=True, slots=True)
class PersonalWord:
    """One of the asking user's words, as stored."""

    word: str
    meaning: str


@dataclass(frozen=True, slots=True)
class Used:
    """A word the question used: ``asked``, the question's parts it matched; ``terms``,
    what was searched for them (safe terms of the meaning, or of the word in reverse)."""

    word: str
    meaning: str
    asked: frozenset[str]
    terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Expansion:
    used: tuple[Used, ...] = ()

    def understood(self) -> tuple[tuple[str, str], ...]:
        """The answer's ``understood_words``: each (word, meaning) used, once, in order."""
        return tuple(dict.fromkeys((used.word, used.meaning) for used in self.used))

    def asks(self, keyword: str) -> bool:
        """True when ``keyword`` is (part of) what a used word matched in the question."""
        parts = set(_parts(keyword))
        return bool(parts) and any(parts <= used.asked for used in self.used)

    def found(self, keyword: str, text: str) -> bool:
        """True when ``keyword`` stands for a used word and every term searched for it is a
        word of ``text`` (by the search's rule) or has a glossary equivalent there."""
        parts = set(_parts(keyword))
        if not parts:
            return False
        words: set[str] | None = None
        for used in self.used:
            if parts <= used.asked:
                words = _runs(_lower(text)) if words is None else words
                if all(_term_in(term, words, text) for term in used.terms):
                    return True
        return False


NO_EXPANSION = Expansion()


class WordSource(Protocol):
    def words(self, customer_id: str, user_id: str) -> Sequence[PersonalWord]:
        """The user's own words, at most user_words.MAX_WORDS."""
        ...


class PgWords:
    """``WordSource`` over PostgreSQL, inside ``Database.user``: one indexed read."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def words(self, customer_id: str, user_id: str) -> list[PersonalWord]:
        with self._db.user(customer_id, user_id) as conn:
            rows = user_words.list_words(conn, customer_id, user_id)
        return [PersonalWord(row.word, row.meaning) for row in rows]


def expand(question: str, words: Sequence[PersonalWord]) -> Expansion:
    """The words of ``words`` the question uses (the module docstring's rules)."""
    asked = _parts(question)
    used: list[Used] = []
    chosen: set[str] = set()
    for item in words:
        span = _span(_parts(item.word), asked) or _heard(item.word, asked)
        terms = safe_terms(item.meaning)
        if span is None and not item.word.isascii():
            span, terms = _span(_parts(item.meaning), asked), safe_terms(item.word)
        key = fold(item.word)
        if span is None or not terms or (key not in chosen and len(chosen) >= MAX_WORDS_USED):
            continue
        chosen.add(key)
        used.append(Used(item.word, item.meaning, span, terms))
    return Expansion(tuple(used))


def safe_terms(text: str) -> tuple[str, ...]:
    """The search terms of a user's text (the module docstring): letter and mark runs only."""
    runs = [run for run in _ordered_runs(_lower(text)) if _safe(run) and run not in STOP_WORDS]
    return tuple(dict.fromkeys(runs))[:TERMS_MAX]


def word_query(keywords: Sequence[str], expansion: Expansion = NO_EXPANSION) -> str:
    """The ``:g`` text for ``to_tsquery('simple', ...)``: the glossary equivalents of
    ``keywords``, then each used word's terms ANDed, and their glossary equivalents, all
    ORed once; "" when there is none."""
    alternatives = [glossary.word_query(list(keywords))]
    for used in expansion.used:
        group = [_term(term) for term in used.terms]
        alternatives.append(group[0] if len(group) == 1 else "( " + " & ".join(group) + " )")
        alternatives.append(glossary.word_query(list(used.terms)))
    return " | ".join(dict.fromkeys(part for part in alternatives if part))


def _term(term: str) -> str:
    if not _safe(term):
        raise ValueError("a search term from a user is letters and marks only")
    return f"{term}:*" if len(term) >= glossary.PREFIX_MIN else term


def _safe(run: str) -> bool:
    return (
        TERM_MIN <= len(run) <= TERM_MAX
        and all(unicodedata.category(ch)[0] in "LM" for ch in run)
        and any(unicodedata.category(ch)[0] == "L" for ch in run)
    )


def _term_in(term: str, words: set[str], text: str) -> bool:
    if any(word.startswith(term) if len(term) >= glossary.PREFIX_MIN else word == term for word in words):
        return True
    return glossary.present(term, text)


def _span(parts: Sequence[str], asked: Sequence[str]) -> frozenset[str] | None:
    """``parts`` when they are in ``asked`` one after another; None. O(q * p)."""
    size = len(parts)
    if not size:
        return None
    hit = any(list(asked[i : i + size]) == list(parts) for i in range(len(asked) - size + 1))
    return frozenset(parts) if hit else None


def _heard(word: str, asked: Sequence[str]) -> frozenset[str] | None:
    """The question's parts that sound like a single Latin ``word`` of 4 letters or more."""
    parts = _parts(word)
    if len(parts) != 1 or not (parts[0].isascii() and parts[0].isalpha()) or len(parts[0]) < _SOUND_WORD_MIN:
        return None
    key = sound_key(parts[0])
    if len(key) < SOUND_KEY_MIN or key in glossary.MERGED_KEYS:
        return None
    heard = frozenset(part for part in asked if part.isascii() and part.isalpha() and sound_key(part) == key)
    return heard or None


def _lower(text: str) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).lower())


def _parts(text: str) -> list[str]:
    """The folded runs of letters, marks and digits of ``text``, in order."""
    return _ordered_runs(fold(text), digits=True)


def _runs(text: str) -> set[str]:
    return set(_ordered_runs(text))


def _ordered_runs(text: str, *, digits: bool = False) -> list[str]:
    """Runs of letters and marks (and digits when ``digits``), in order. O(t)."""
    runs: list[str] = []
    current: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        if category[0] in "LM" or (digits and category == "Nd"):
            current.append(ch)
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return runs
