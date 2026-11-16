"""One expansion per question: the glossary, the asking user's own words and sound keys
(council 3 Oct 2026, My words A3, PROVISIONAL).

A search question is matched as asked, and also through alternatives, all built here
or next to here and all on hybrid.py's word list: the glossary's Marathi and Hindi
equivalents and the user's own words share the ``:g`` OR arm (``word_query``); the
question's sound keys are the ``:sk`` arm (``hybrid.sound_query``, unchanged).

**Whose words.** ``PgWords.words`` reads the asking user's private words and approved
customer-shared words inside ``Database.user``. Customer RLS scopes the shared rows;
private rows remain under ``own_user_only``. Nothing is cached across questions or users.

**Matching** (``expand``). Forward: a word whose parts (``_parts``: folded runs of
letters, marks and digits) are in the question one after another; a single Latin word
of 4 letters or more also by its sound key ("maalak" is malak), never a key under 3
letters or one of ``glossary.MERGED_KEYS``. Its meanings are searched. Reverse: a word
with a letter outside ASCII (बोजा) whose meaning's parts are in the question, one of them
a word that is not a stop word or GENERIC ("of" or "the amount" never match): the word is
searched, so "mortgage charge" finds बोजा. At most ``MAX_WORDS_USED`` words, each with
every meaning (at most 3). The numbers and ids in what is searched for ("gat 47") are
asked too (``Expansion.numbers``): the service checks them first and whole, like the
question's own, so a meaning's number is never silently dropped.

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

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from kaagaz.answering import glossary
from kaagaz.answering.hinglish import STOP_WORDS
from kaagaz.answering.words_text import fold
from kaagaz.chunking.script import SOUND_KEY_MIN, ascii_digits, sound_key
from kaagaz.db import organization_words, user_words
from kaagaz.db.engine import Database

MAX_WORDS_USED = 10  # words expanded per question (council: at most 10 words, 30 meanings)
TERMS_MAX = 6  # safe terms kept from one meaning or word
TERM_MIN = 2
TERM_MAX = 40
_SOUND_WORD_MIN = 4  # letters: a shorter word matches as typed only
_ID = re.compile(r"[0-9a-z]+(?:[-/.,][0-9a-z]+)*")  # as the gate reads numbers and ids


@dataclass(frozen=True, slots=True)
class PersonalWord:
    """One personal or organization word available to the asking user."""

    word: str
    meaning: str
    origin: str = "personal"


@dataclass(frozen=True, slots=True)
class Used:
    """A word the question used: ``asked``, the question's parts it matched; ``terms``,
    what was searched for them (safe terms of the meaning, or of the word in reverse)."""

    word: str
    meaning: str
    asked: frozenset[str]
    terms: tuple[str, ...]
    numbers: tuple[str, ...] = ()  # the numbers and ids of what was searched for
    origin: str = "personal"


@dataclass(frozen=True, slots=True)
class Expansion:
    used: tuple[Used, ...] = ()

    def understood(self) -> tuple[tuple[str, str, str], ...]:
        """Each (word, meaning, origin) used, once, in order."""
        return tuple(dict.fromkeys((used.word, used.meaning, used.origin) for used in self.used))

    def numbers(self) -> list[str]:
        """Every used word's numbers and ids, once: asked like the question's own."""
        return list(dict.fromkeys(number for used in self.used for number in used.numbers))

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
    def words(
        self, customer_id: str, user_id: str, *, document_id: str | None = None
    ) -> Sequence[PersonalWord]:
        """Private words plus approved organization words."""
        ...


class PgWords:
    """``WordSource`` over PostgreSQL, inside ``Database.user``: two indexed reads."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def words(self, customer_id: str, user_id: str, *, document_id: str | None = None) -> list[PersonalWord]:
        """``document_id`` (the scope's one document) no longer narrows anything (#602, decision
        9); kept for ``WordSource``."""
        with self._db.user(customer_id, user_id) as conn:
            rows = user_words.list_words(conn, customer_id, user_id)
            # #602, decision 9: a word made on a page helps in any scope, for whoever sees its page.
            shared = organization_words.approved(conn, customer_id)
        personal = [PersonalWord(row.word, row.meaning) for row in rows]
        seen = {(fold(row.word), fold(row.meaning)) for row in personal}
        words = personal.copy()
        for row in shared:
            key = (fold(row.word), fold(row.meaning))
            if key not in seen:
                words.append(PersonalWord(row.word, row.meaning, "organization"))
                seen.add(key)
        return words


def expand(question: str, words: Sequence[PersonalWord]) -> Expansion:
    """The words of ``words`` the question uses (the module docstring's rules). The question's
    parts and their sound keys are worked out once; a word's searched text is read only
    after it matched. O(q + W * p)."""
    asked = _parts(question)
    heard: dict[str, set[str]] = {}
    for part in asked:
        if part.isascii() and part.isalpha():
            heard.setdefault(sound_key(part), set()).add(part)
    used: list[Used] = []
    chosen: set[str] = set()
    for item in words:
        searched = item.meaning
        span = _span(_parts(item.word), asked) or _heard(item.word, heard)
        if span is None and not item.word.isascii():
            meaning = _parts(item.meaning)
            span, searched = (_span(meaning, asked) if _says_something(meaning) else None), item.word
        if span is None:
            continue
        terms, key = safe_terms(searched), fold(item.word)
        if not terms or (key not in chosen and len(chosen) >= MAX_WORDS_USED):
            continue
        chosen.add(key)
        numbers = tuple(
            t for t in _ID.findall(ascii_digits(searched).casefold()) if any(c.isdigit() for c in t)
        )
        used.append(Used(item.word, item.meaning, span, terms, numbers, item.origin))
    return Expansion(tuple(used))


def _says_something(parts: Sequence[str]) -> bool:
    """A meaning a question can be read through backwards: a part with a letter that is not
    a stop word or a GENERIC word (on nearly every form). O(p * r)."""
    return any(
        any(ch.isalpha() for ch in part)
        and part not in STOP_WORDS
        and part not in glossary.GENERIC
        and glossary.concept(part) not in glossary.GENERIC
        for part in parts
    )


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
    if not size or parts[0] not in asked:
        return None
    hit = any(list(asked[i : i + size]) == list(parts) for i in range(len(asked) - size + 1))
    return frozenset(parts) if hit else None


def _heard(word: str, heard: dict[str, set[str]]) -> frozenset[str] | None:
    """The question's parts (``heard``: by sound key) that sound like a single Latin
    ``word`` of 4 letters or more. O(p)."""
    if not (word.isascii() and len(word) >= _SOUND_WORD_MIN):
        return None
    parts = _parts(word)
    if len(parts) != 1 or not parts[0].isalpha() or len(parts[0]) < _SOUND_WORD_MIN:
        return None
    key = sound_key(parts[0])
    if len(key) < SOUND_KEY_MIN or key in glossary.MERGED_KEYS:
        return None
    return frozenset(heard.get(key, ())) or None


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
