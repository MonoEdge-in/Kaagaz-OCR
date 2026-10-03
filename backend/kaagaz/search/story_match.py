"""Story matches: which of a user's own stories a question tells again (council 3 Oct
2026, "Story of this PDF", the Decision; PROVISIONAL for Vrushit: every number below).
Pure: no SQL, no model (the API branch reads the stories). Never evidence.

1. **Words** (``question_words``): the raw question, digits as ASCII, Devanagari
   in Latin letters (``latin_spelling``), lower case, then the safe builder (runs
   of ASCII letters and digits; "-", "/" or "." inside keeps "7/12" whole). Out:
   stop words (``hinglish.STOP_WORDS``, ``glossary.ROMANISED_STOP``), ``GENERIC``
   words that fit any story ("papers", "agreement", "kagaz"), letter words under
   3 letters and one-digit words. The first ``WORDS_MAX`` different words count.
   A word the glossary lists may also be found as one of its Marathi or Hindi
   words in Latin letters ("land" as jamin): an alternative of 4+ letters that
   only counts when it is in the story exactly, never by trigram or sound.
2. **A word matches a story word** when they are the same; or, both of letters
   only: both of ``TRIGRAM_LETTERS`` letters or more with pg_trgm's similarity
   (padded words, shared over all trigrams) of ``TRIGRAM_MIN`` or more
   ("daregav", "daregaon"); or the same sound key (``sound_key``, at least
   ``script.SOUND_KEY_MIN`` letters: "jameen", "jamin"); or a question key of
   ``SOUND_PREFIX_MIN`` letters or more that starts a story key at most
   ``SOUND_PREFIX_EXTRA`` letters longer. A word with a digit matches only the
   same word with "-", "/" and "." taken as one ("7/12" is "7-12", never "712").
3. **Bands** (``word_band``): coverage is the share of the words that match.
   "match": ``WORDS_MIN`` words or more, ``MATCH_COVERAGE``, and at least one
   matched word of letters (numbers alone are "maybe" at most); "maybe": as many
   words and ``MAYBE_COVERAGE``. A one-word question is "maybe" at most, only
   when the word itself, of ``SINGLE_WORD_LETTERS`` letters or more, is in the
   story exactly (not an alternative, not a near spelling).
4. **Meaning** (``meaning_band``) only with a threshold calibrated for the
   recipe in ``MEANING_THRESHOLDS``; none is, so it is OFF. With one: a cosine
   at the threshold or above is "maybe", "match" when it also leads the second
   best story by ``MEANING_MARGIN``.
5. **Order** (``rank``): a story takes the better of the two bands. When two or
   more word matches tie at the top (as many words, as many exact), the words
   can't tell them apart: every word match is "maybe". Reciprocal rank fusion of
   the word and meaning lists orders the stories that pass, every "match" first;
   at most ``MATCHES_MAX``. No score and no story text leaves this module.

Time: ``rank`` O(T + V * W): T story characters, V different story words (the
first ``STORY_WORDS_MAX`` of each story), W <= WORDS_MAX words. Each question
word's trigrams and key are made once, and each story word's once a request.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from kaagaz.answering import glossary
from kaagaz.answering.hinglish import STOP_WORDS
from kaagaz.chunking import script
from kaagaz.search.hybrid import fuse

WORDS_MAX = 12
WORDS_MIN = 2
STORY_WORDS_MAX = 200  # different words of one story that are compared
TRIGRAM_LETTERS = 4
TRIGRAM_MIN = 0.5
SOUND_LETTERS = 3
SOUND_PREFIX_MIN = 4  # a question key this long may start a longer story key...
SOUND_PREFIX_EXTRA = 2  # ...at most this many letters longer
ALTERNATIVE_LETTERS = 4
MATCH_COVERAGE = 0.75
MAYBE_COVERAGE = 0.5
SINGLE_WORD_LETTERS = 4
MEANING_MARGIN = 0.03
# The story recipe tag ("{MODEL_EMBED}#story1") -> its calibrated cosine threshold.
# Empty: meaning is OFF (uncalibrated, fails closed).
MEANING_THRESHOLDS: dict[str, float] = {}
MATCHES_MAX = 3
# Words that fit nearly any story about a document: never a content word.
GENERIC = frozenset(
    {"paper", "papers", "document", "documents", "doc", "docs", "agreement", "agreements", "file", "files"}
    | {"kagaz", "kagaj", "kaagaz", "kagad", "kagaza", "kagadpatra"}
)

Band = Literal["match", "maybe"]

_STOP = STOP_WORDS | glossary.ROMANISED_STOP | GENERIC
_WORD = re.compile(r"[a-z0-9]+(?:[-/.][a-z0-9]+)*")
_LETTERS = re.compile(r"[a-z]+")
_DIGIT = re.compile(r"[0-9]")
_DEVANAGARI = re.compile(f"[{chr(0x0900)}-{chr(0x097F)}]")  # the Devanagari block
_SEPARATORS = str.maketrans("-.", "//")


@dataclass(frozen=True, slots=True)
class QueryWord:
    """A content word, its exact-only glossary alternatives, and what it is compared by."""

    word: str
    alternatives: tuple[str, ...] = ()
    letters: bool = field(init=False)
    grams: frozenset[str] = field(init=False)
    key: str = field(init=False)

    def __post_init__(self) -> None:
        letters = bool(_LETTERS.fullmatch(self.word))
        object.__setattr__(self, "letters", letters)
        object.__setattr__(self, "grams", trigrams(self.word) if letters else frozenset())
        object.__setattr__(self, "key", script.sound_key(self.word) if letters else "")


@dataclass(frozen=True, slots=True)
class Candidate:
    """A story as the API branch reads it: ``story_latin`` is matched here and never
    leaves; ``meaning`` is its cosine, None when meaning is off or it has no vector."""

    document_id: str
    display_name: str
    doc_type: str | None
    story_latin: str
    meaning: float | None


@dataclass(frozen=True, slots=True)
class StoryMatch:
    """What the API shows: never a score or the story."""

    document_id: str
    display_name: str
    doc_type: str | None
    band: Band


def words_of(text: str) -> list[str]:
    """The safe builder (step 1, before the words that never count). O(n)."""
    text = script.ascii_digits(text)
    if _DEVANAGARI.search(text) and script.has_devanagari_letters(text):  # the regex is the fast test
        text = script.latin_spelling(text)
    return _WORD.findall(text.lower())


def _counts(word: str) -> bool:
    if word in _STOP:
        return False
    return len(word) >= SOUND_LETTERS if _LETTERS.fullmatch(word) else len(word) > 1


def question_words(question: str) -> list[QueryWord]:
    """The question's content words, with their glossary alternatives (step 1)."""
    found = list(dict.fromkeys(word for word in words_of(question) if _counts(word)))[:WORDS_MAX]
    return [QueryWord(word, _alternatives(word)) for word in found]


def _alternatives(word: str) -> tuple[str, ...]:
    spelled = (form for term in glossary.equivalents(word) for form in words_of(term))
    kept = (f for f in spelled if f != word and len(f) >= ALTERNATIVE_LETTERS and _LETTERS.fullmatch(f))
    return tuple(dict.fromkeys(kept))


def trigrams(word: str) -> frozenset[str]:
    """pg_trgm's trigrams of one lower-case word."""
    padded = f"  {word} "
    return frozenset(padded[i : i + 3] for i in range(len(padded) - 2))


def similarity(first: str, second: str) -> float:
    """pg_trgm's similarity of two words, 0 to 1."""
    a, b = trigrams(first), trigrams(second)
    return len(a & b) / len(a | b)


class _Matcher:
    """Step 2 for every question word at once: lookup tables made once a request, so a
    story word costs a few dict lookups, its trigrams, and its sound key only when the
    key can start with a question key's first letter (that letter depends on the
    word's first three letters only)."""

    def __init__(self, words: Sequence[QueryWord]) -> None:
        self.same: dict[str, int] = {}  # the word itself -> its bits
        self.exact: dict[str, int] = {}  # the word or an alternative -> its bits
        self.digits: dict[str, int] = {}
        self.grams: dict[str, list[int]] = {}  # trigram -> places of 4+ letter words
        self.sizes: dict[int, int] = {}
        self.keys: dict[str, int] = {}  # a whole sound key -> bits
        self.starts: dict[str, int] = {}  # a key of SOUND_PREFIX_MIN+ letters -> bits
        for place, asked in enumerate(words):
            bit = 1 << place
            self.same[asked.word] = self.same.get(asked.word, 0) | bit
            for form in (asked.word, *asked.alternatives):
                self.exact[form] = self.exact.get(form, 0) | bit
            if _DIGIT.search(asked.word):
                spelled = asked.word.translate(_SEPARATORS)
                self.digits[spelled] = self.digits.get(spelled, 0) | bit
            if asked.letters and len(asked.word) >= TRIGRAM_LETTERS:
                self.sizes[place] = len(asked.grams)
                for gram in asked.grams:
                    self.grams.setdefault(gram, []).append(place)
            if len(asked.key) >= script.SOUND_KEY_MIN:
                self.keys[asked.key] = self.keys.get(asked.key, 0) | bit
                if len(asked.key) >= SOUND_PREFIX_MIN:
                    self.starts[asked.key] = self.starts.get(asked.key, 0) | bit
        self.firsts = {key[0] for key in self.keys}
        self.shortest = min(map(len, self.keys), default=0)
        self._first: dict[str, str] = {}  # three letters -> the first letter of their key

    def masks(self, story_word: str) -> tuple[int, int]:
        """(the question words this story word matches, those it is exactly) as bits."""
        exact, matched = self.same.get(story_word, 0), self.exact.get(story_word, 0)
        if _DIGIT.search(story_word):
            return matched | self.digits.get(story_word.translate(_SEPARATORS), 0), exact
        if not _LETTERS.fullmatch(story_word):
            return matched, exact
        if self.sizes and len(story_word) >= TRIGRAM_LETTERS:
            grams = trigrams(story_word)
            shared: dict[int, int] = {}
            for gram in grams:
                for place in self.grams.get(gram, ()):
                    shared[place] = shared.get(place, 0) + 1
            for place, count in shared.items():
                if count / (self.sizes[place] + len(grams) - count) >= TRIGRAM_MIN:
                    matched |= 1 << place
        if self.keys and len(story_word) + story_word.count("x") >= self.shortest:
            first = self._first.get(story_word[:3])
            if first is None:
                first = self._first[story_word[:3]] = script.sound_key(story_word[:3])[:1]
            if first in self.firsts:
                key = script.sound_key(story_word)
                matched |= self.keys.get(key, 0)
                for size in range(max(SOUND_PREFIX_MIN, len(key) - SOUND_PREFIX_EXTRA), len(key)):
                    matched |= self.starts.get(key[:size], 0)
        return matched, exact


def form_matches(form: str, story_word: str) -> bool:
    """Step 2 for one word and one story word, both as text (tests and probes)."""
    return _Matcher([QueryWord(form, _alternatives(form))]).masks(story_word)[0] == 1


def word_band(words: Sequence[QueryWord], matched: Sequence[bool], exact: Sequence[bool]) -> Band | None:
    """A story's band from which words match it and which are in it exactly, as
    themselves (step 3)."""
    if len(words) == 1:
        alone = exact[0] and words[0].letters and len(words[0].word) >= SINGLE_WORD_LETTERS
        return "maybe" if alone else None
    coverage = sum(matched) / len(words) if len(words) >= WORDS_MIN else 0.0
    letter = any(m and w.letters for m, w in zip(matched, words, strict=True))
    if coverage >= MATCH_COVERAGE and letter:
        return "match"
    return "maybe" if coverage >= MAYBE_COVERAGE else None


def meaning_band(cosine: float | None, second: float | None, threshold: float | None) -> Band | None:
    """The band by meaning (step 4): None when it is off or under the threshold."""
    if threshold is None or cosine is None or cosine < threshold:
        return None
    return "match" if second is None or cosine - second >= MEANING_MARGIN else "maybe"


def rank(
    words: Sequence[QueryWord], candidates: Sequence[Candidate], *, threshold: float | None
) -> list[StoryMatch]:
    """The stories that pass, best first, at most MATCHES_MAX (step 5). ``candidates``
    come in the API branch's fixed order, which breaks every tie."""
    matcher = _Matcher(words)
    seen: dict[str, tuple[int, int]] = {}  # story word -> bit masks: the words it matches, and is
    word_bands: dict[int, Band] = {}
    hits: dict[int, tuple[int, int]] = {}
    places = range(len(words))
    for index, candidate in enumerate(candidates):
        matched = exact = 0
        for story_word in list(dict.fromkeys(words_of(candidate.story_latin)))[:STORY_WORDS_MAX]:
            if story_word not in seen:
                seen[story_word] = matcher.masks(story_word)
            matched, exact = matched | seen[story_word][0], exact | seen[story_word][1]
        hits[index] = (matched.bit_count(), exact.bit_count())
        band = word_band(
            words, [matched >> p & 1 == 1 for p in places], [exact >> p & 1 == 1 for p in places]
        )
        if words and band is not None:
            word_bands[index] = band
    top = [i for i, band in word_bands.items() if band == "match"]
    best = max((hits[i] for i in top), default=None)
    if sum(hits[i] == best for i in top) > 1:  # a tie at the top
        word_bands.update(dict.fromkeys(top, "maybe"))
    cosines = sorted(
        ((c.meaning, i) for i, c in enumerate(candidates) if c.meaning is not None), key=lambda p: -p[0]
    )
    bands = dict(word_bands)
    for place, (cosine, index) in enumerate(cosines):
        second = cosines[1 if place == 0 else 0][0] if len(cosines) > 1 else None
        band = meaning_band(cosine, second, threshold)
        if band is not None and bands.get(index) != "match":
            bands[index] = band
    by_words = sorted(bands, key=lambda i: (-hits[i][0], -hits[i][1], i))
    by_meaning = [i for _, i in cosines if threshold is not None and i in bands]
    fused = fuse([(str(i), 0.0) for i in by_words], [], [(str(i), 0.0) for i in by_meaning])
    order = sorted((int(piece.piece_id) for piece in fused), key=lambda i: bands[i] != "match")
    shown = [candidates[i] for i in order[:MATCHES_MAX]]
    return [
        StoryMatch(c.document_id, c.display_name, c.doc_type, bands[order[n]]) for n, c in enumerate(shown)
    ]
