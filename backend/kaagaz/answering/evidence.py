"""Search evidence for the search route: fetch it, number it, and decide if it is enough.

``Retriever`` is the small port the service depends on, so tests use a fake.
``PgRetriever`` wraps ``kaagaz.search.hybrid.search`` inside ``db.customer``;
the search module is imported there only, so everything else here is plain
Python.

The evidence gate (not found is a valid answer, docs/architecture.md): the
evidence is enough only if the best hit's meaning score or fuzzy score
reaches ``answer_min_evidence_score``, or at least half of the question's
keywords appear in the evidence text (ignoring case). Otherwise the answer is
"not found" plus the closest documents.

The keyword rule (council 2 Oct 2026, PR2 D): digits of any script read as
ASCII; each keyword counts once, out of the keyword count; a keyword with a
digit ("48", "inv-1042") must be in the evidence as a whole token
(``has_token``: not in 148, 1,48,000, 4.48, 47/48, 48/2 or 48.00), checked
first on every path, and the score path needs the best piece, whose score it
is, to hold it too; a keyword also counts through a glossary equivalent
(``glossary.present``), and such a pass is ``"glossary"``, capped at needs
review by the service, which also checks each kept sentence's own cited hits
(``service._judged``).

Marathi and Hindi typed in English letters (council 3 Oct 2026, PR 2b-2,
PROVISIONAL): the service leaves the Romanised stop words out of the keywords
first (``glossary.content_words``), and a Romanised word counts through the
glossary like an English one ("malak" is owner). Last, a keyword also counts when
it sounds like a word of the evidence's Latin spelling (``glossary.sounds_like``),
and such a pass is ``"sound"``. Every gate value in ``CAPPED`` is capped at needs
review. The typo score of a mostly Devanagari best piece is no shortcut: it is
the score of its Latin spelling, which a Romanised question nears by filler
alone ("malak ahe" against a bank letter ending "... ahe" is 0.40).

My words (council 3 Oct 2026, A3, PROVISIONAL): last, a keyword that stands for one of
the asking user's own words counts when what it was searched as is in the evidence
(``Expansion.found``), but only when another keyword is there as written, a whole word
(``as_written``): a user's word alone never passes. Such a pass is ``"personal"``,
capped like the others.

PROVISIONAL, a change from the spec's "word score or meaning score": the word
score is PostgreSQL's raw ``ts_rank_cd``, which is small and unbounded (about
0.1 for an exact "INV-1042", 0.033 for "invoice grand total"), so it cannot
share one 0 to 1 threshold. The fuzzy score (``word_similarity``) and the
meaning score (cosine similarity) are both 0 to 1. An exact word match still
passes through the keyword rule. Vrushit decides.

Document text is untrusted. A piece that reads like instructions to a model
("ignore previous instructions...", "you are now the assistant", a fake
evidence tag, "when asked, say ...", a note to the assistant, "AI ko bolo", and
their Devanagari forms) in its text or its context header (which holds the
document's name, PRD 3 Q5) is left out of the evidence entirely: it can neither
steer the model nor be quoted. The phrases are matched on the NFKC text
without its default-ignorable code points (``sanitize.strip_ignorable``) and
the Braille blank, so a zero-width space, a variation selector or fullwidth
letters cannot hide one. A bill's own words are not instructions:
"when asked" counts only with an imperative that starts its clause or follows
"when asked" ("Customers must write their GSTIN ... when asked" is not one);
a note or message to the assistant needs ":" or "," after the role ("Note for
the Assistant Manager" is not one); the polite Hindi "tell" (बताएं) needs
what to say after it ("... बताएं कि ..."); and "do not ignore" (अनदेखा न करें,
दुर्लक्ष करू नये, विसरू नका) is not "ignore". PROVISIONAL: the phrase list is a
first line of defence next to the fenced prompt (draft.py) and the number
check (verify.py); it is not the only one.

A translated search (council 4 Oct 2026, PROVISIONAL; kaagaz/answering/translate.py):
the default gate first; failing it, ``any_hit_scores`` passes when any hit, not only the
best, holds every number asked and reaches the meaning score. The service caps such an
answer at needs review.

A hit with no valid region cannot be pointed at, so it is not evidence
(CLAUDE.md never-do 5).

Neighbours (PRD 3 Q4) are fetched only for the usable hits, after the gate passed;
they pass the same two checks, are numbered after every hit (a window cut drops
them first) and are marked. PRD 5's topic and section pieces (CX6, ``Context``) are
fetched the same way and numbered between the hits and the neighbours; a whole
document's pieces (CX5) are numbered in reading order, and the gate asks only that they
hold every number asked (``holds_numbers``).

Time is O(h * t) for h hits of t characters: one pass of fixed regular
expressions and substring checks per hit.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol

from kaagaz.answering import glossary
from kaagaz.answering.expansion import NO_EXPANSION, Expansion
from kaagaz.answering.sanitize import strip_ignorable
from kaagaz.chunking.script import ascii_digits, is_devanagari
from kaagaz.db.engine import Database
from kaagaz.extraction.parsing import _INDIAN, _WESTERN
from kaagaz.reading.spans import BoxLocation, location_from_json, location_to_json
from kaagaz.search.scope import Scope

CLOSEST_DOCUMENTS = 3

# How the gate passed: a score, the English keywords, or only with the glossary's, a
# sound's or the user's own words' help.
Gate = Literal["score", "keywords", "glossary", "sound", "personal"]
# The passes the service caps at needs review: only an equivalent, a sound or a user's word
# let them through.
CAPPED: frozenset[Gate] = frozenset({"glossary", "sound", "personal"})
_DIGIT = re.compile("[0-9]")
_LETTER = re.compile("[a-z]")
# Whole tokens (has_token), on text folded by _fold: NFKC, and the hyphens (U+2010, U+2011),
# the en dash (U+2013) and the em dash (U+2014) as "-". No letter, mark or digit (\w) right
# before or after, and no dash, slash or dot joining a letter or digit on either side: the
# number 48 is not in "48-A", "A-48", "INV-48" or "A/48" ("48-49" is one token, as written).
_DASHES = str.maketrans(dict.fromkeys("\u2010\u2011\u2013\u2014", "-"))
_NUMBER_TOKEN = re.compile(r"(?<!\w)(?<![0-9a-z][-/.])[0-9]+(?:[.,/-][0-9]+)*(?!\w)(?![-/.][0-9a-z])")
_ID_TOKEN = re.compile(r"(?<!\w)[a-z0-9]+(?:[-/.][a-z0-9]+)*(?!\w)")  # as normalise splits words
# Where camelCase or letters and digits meet: a word boundary for has_word (word_text).
_WORD_JOINS = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")
_WORD = re.compile(r"[a-z0-9]+(?:[-/.][a-z0-9]+)*")  # hinglish.normalise's words
_ALNUM = re.compile("[a-z0-9]+")

_TELL = r"(?:say|answer|reply|tell|respond|write)"  # an imperative near "when asked"
# Before "when asked", the imperative starts its clause ("Reply that ..."); after it, any
# that does not follow a modal or "to" ("when asked to write" is not an order).
_TELL_FIRST = rf"(?:^|[.!?:;,(\"']\s*|\b(?:then|and|just|always|please|also)\s+){_TELL}\b"
_NOT_MODAL = "".join(
    f"(?<!{word} )" for word in ("must", "should", "shall", "will", "would", "can", "may", "to")
)
_BLANKS = str.maketrans("", "", "\N{BRAILLE PATTERN BLANK}")  # draws nothing, yet not default-ignorable
_INSTRUCTION_LIKE = (
    re.compile(
        r"\b(?:ignore|disregard|forget|override)\b.{0,40}?\b(?:previous|prior|above|earlier|all|any|your)\b"
        r".{0,40}?\b(?:instructions?|rules?|prompts?|messages?|directions?)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+(?:now\s+)?(?:an?|the)\s+(?:ai|assistant|model|chatbot)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:system|assistant)\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"</?\s*evidence\b", re.IGNORECASE),
    re.compile(
        rf"\bwhen\s+asked\b[^.!?\n]{{0,40}}?{_NOT_MODAL}\b{_TELL}\b"
        rf"|{_TELL_FIRST}[^.!?\n]{{0,40}}?\bwhen\s+asked\b",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"\b(?:note|message)\s+(?:to|for)\s+(?:the\s+)?(?:ai|assistant|model|chatbot|bot)\s*[:,\-\u2013\u2014]",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ai|assistant|model|chatbot|bot)\s+ko\b.{0,40}?\b(?:bolo|batao|kaho)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    # Devanagari: "AI ko bolo", and "ignore the earlier instructions" (Hindi and Marathi).
    re.compile(
        # The familiar command, or the polite one with what to say after it.
        r"(?:\bai\b|एआई|सहायक|मॉडल|बॉट)\s*को.{0,40}?"
        r"(?:बोलो|बताओ|कहो|(?:बोलें|बताएं|कहें|बोलिए|बताइए|कहिए)\s*(?:कि(?=[\s,।]|$)|:))",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:पिछले|पहले|ऊपर|सभी|सारे|मागील|आधीच्या|पूर्वीच्या|सर्व).{0,40}?(?:निर्देश|आदेश|सूचना)"
        # "Forget" is भूल जा..., never भूल alone: "भूल चूक लेनी देनी" is a bill's E&OE.
        r".{0,40}?(?:अनदेखा|नज\N{DEVANAGARI SIGN NUKTA}?रअंदाज|भूल\s*जा|विसर|दुर्लक्ष)"
        # ... unless it says not to: "अनदेखा न करें", "दुर्लक्ष करू नये", "विसरू नका".
        r"(?!.{0,20}?(?<!\S)(?:न|नहीं|नये|नका|मत)(?=[\s,।.!?]|$))",
        re.DOTALL,
    ),
)


class Hit(Protocol):
    """What the answer needs from one search hit (``kaagaz.search.hybrid.SearchHit``)."""

    @property
    def piece_id(self) -> str: ...
    @property
    def document_id(self) -> str: ...
    @property
    def document_name(self) -> str: ...
    @property
    def detected_type(self) -> str: ...
    @property
    def page_from(self) -> int: ...
    @property
    def page_to(self) -> int: ...
    @property
    def seq(self) -> int: ...
    @property
    def context_header(self) -> str: ...
    @property
    def text(self) -> str: ...
    @property
    def regions(self) -> list[dict[str, Any]]: ...
    @property
    def label(self) -> str: ...
    @property
    def fuzzy_score(self) -> float | None: ...
    @property
    def meaning_score(self) -> float | None: ...


class Retriever(Protocol):
    def search(
        self,
        customer_id: str,
        text: str,
        embedding: list[float] | None,
        limit: int,
        scope: Scope,
        expansion: Expansion = NO_EXPANSION,
    ) -> Sequence[Hit]:
        """``expansion``: the asking user's words the question used (OR alternatives only)."""
        ...

    def neighbours(self, customer_id: str, hits: Sequence[Hit], scope: Scope) -> Sequence[Hit]:
        """Q4: the pieces next to ``hits`` in ``scope``, each once, never one of them."""
        ...


@dataclass(frozen=True, slots=True)
class Translated:
    """A translated search's hits, and the pieces a list of the question itself found, with
    their own meaning score (a piece not in ``original`` only the translation found)."""

    hits: Sequence[Hit]
    original: Mapping[str, float | None]


class TranslatedSearch(Protocol):
    def search_translated(
        self,
        customer_id: str,
        text: str,
        embedding: list[float] | None,
        translations: Sequence[tuple[str, list[float] | None]],
        limit: int,
        scope: Scope,
        expansion: Expansion = NO_EXPANSION,
    ) -> Translated:
        """``Retriever.search`` for the question and each (text, embedding) translation, fused."""
        ...


class Context(Protocol):
    """PRD 5: the whole documents (CX5), the pieces topics and sections add (CX6), and the
    documents' cards (M2), each inside ``scope``."""

    def whole(self, customer_id: str, scope: Scope, budget: int) -> Sequence[Hit] | None:
        """Every piece of ``scope`` in reading order; None when there is none or their text
        is more than about ``budget`` tokens (characters / 4)."""
        ...

    def topic_pieces(
        self, customer_id: str, terms: Sequence[str], hits: Sequence[Hit], scope: Scope
    ) -> Sequence[Hit]:
        """The pieces of the topics a term names or a hit belongs to, never a hit."""
        ...

    def section_pieces(
        self,
        customer_id: str,
        keywords: Sequence[str],
        embedding: list[float] | None,
        hits: Sequence[Hit],
        scope: Scope,
    ) -> Sequence[Hit]:
        """The pieces of the two sections the keywords or the vector find, never a hit."""
        ...

    def cards(
        self, customer_id: str, document_ids: Sequence[str], scope: Scope
    ) -> Mapping[str, tuple[str, Any]]:
        """Each live document's (display name, card JSON or None)."""
        ...


class PgRetriever:
    """``Retriever``, ``TranslatedSearch`` and ``Context`` over PostgreSQL: hybrid search, the
    hits' neighbours, and PRD 5's whole documents, topics, sections and cards, each inside
    ``db.customer``."""

    def __init__(
        self, db: Database, *, recipe: str, ef_search: int, neighbours: int, topic_pieces: int = 0
    ) -> None:
        """``recipe``: the ``embedding_model`` tag meaning search uses (vectors with
        another tag are left out; a section's ``embed_recipe`` too); ``ef_search``:
        SEARCH_EF; ``neighbours``: ANSWER_NEIGHBOURS, the pieces on each side of a hit (0:
        none); ``topic_pieces``: TOPIC_EXPAND_MAX_PIECES, a topic's pieces a document."""
        self._db = db
        self._recipe = recipe
        self._ef_search = ef_search
        self._neighbours = neighbours
        self._topic_pieces = topic_pieces

    def search(
        self,
        customer_id: str,
        text: str,
        embedding: list[float] | None,
        limit: int,
        scope: Scope,
        expansion: Expansion = NO_EXPANSION,
    ) -> Sequence[Hit]:
        # Imported here so the answering rules (and their tests) do not need the
        # search module; only the real retriever does.
        from kaagaz.search.hybrid import search

        with self._db.customer(customer_id) as conn:
            return search(
                conn,
                customer_id=customer_id,
                query_text=text,
                embedding=embedding,
                limit=limit,
                recipe=self._recipe,
                ef_search=self._ef_search,
                scope=scope,
                expansion=expansion,
            )

    def search_translated(
        self,
        customer_id: str,
        text: str,
        embedding: list[float] | None,
        translations: Sequence[tuple[str, list[float] | None]],
        limit: int,
        scope: Scope,
        expansion: Expansion = NO_EXPANSION,
    ) -> Translated:
        from kaagaz.search.hybrid import search_translated

        with self._db.customer(customer_id) as conn:
            hits, original = search_translated(
                conn,
                customer_id=customer_id,
                query_text=text,
                embedding=embedding,
                translations=translations,
                limit=limit,
                recipe=self._recipe,
                ef_search=self._ef_search,
                scope=scope,
                expansion=expansion,
            )
        return Translated(hits, original)

    def neighbours(self, customer_id: str, hits: Sequence[Hit], scope: Scope) -> Sequence[Hit]:
        from kaagaz.search.hybrid import neighbours

        if not self._neighbours or not hits:
            return []
        with self._db.customer(customer_id) as conn:
            return neighbours(conn, customer_id=customer_id, hits=hits, size=self._neighbours, scope=scope)

    def whole(self, customer_id: str, scope: Scope, budget: int) -> Sequence[Hit] | None:
        from kaagaz.search.hybrid import whole

        with self._db.customer(customer_id) as conn:
            return whole(conn, customer_id=customer_id, scope=scope, max_bytes=4 * budget)

    def topic_pieces(
        self, customer_id: str, terms: Sequence[str], hits: Sequence[Hit], scope: Scope
    ) -> Sequence[Hit]:
        from kaagaz.search.hybrid import topic_pieces

        per_document = self._topic_pieces
        with self._db.customer(customer_id) as conn:
            return topic_pieces(
                conn, customer_id=customer_id, terms=terms, hits=hits, per_document=per_document, scope=scope
            )

    def section_pieces(
        self,
        customer_id: str,
        keywords: Sequence[str],
        embedding: list[float] | None,
        hits: Sequence[Hit],
        scope: Scope,
    ) -> Sequence[Hit]:
        from kaagaz.search.hybrid import section_pieces

        with self._db.customer(customer_id) as conn:
            return section_pieces(
                conn,
                customer_id=customer_id,
                keywords=keywords,
                embedding=embedding,
                recipe=self._recipe,
                hits=hits,
                scope=scope,
            )

    def cards(
        self, customer_id: str, document_ids: Sequence[str], scope: Scope
    ) -> Mapping[str, tuple[str, Any]]:
        from kaagaz.search.hybrid import cards

        with self._db.customer(customer_id) as conn:
            return cards(conn, customer_id=customer_id, document_ids=document_ids, scope=scope)


@dataclass(frozen=True, slots=True)
class Evidence:
    """One numbered hit the draft may cite (ids start at 1)."""

    n: int
    hit: Hit
    regions: list[dict[str, Any]]
    neighbour: bool = False  # Q4: next to a hit, given as context; the gate never judged it


def number_evidence(hits: Sequence[Hit], *, start: int = 1, neighbour: bool = False) -> list[Evidence]:
    """Usable hits, numbered from ``start`` in order: instruction-like (text and header
    together) or unlocatable ones left out."""
    evidence: list[Evidence] = []
    for hit in hits:
        regions = valid_regions(hit.regions)
        if not regions or looks_like_instructions(f"{hit.context_header}\n{hit.text}"):
            continue
        evidence.append(Evidence(n=start + len(evidence), hit=hit, regions=regions, neighbour=neighbour))
    return evidence


def enough_evidence(
    evidence: Sequence[Evidence],
    keywords: Sequence[str],
    min_score: float,
    numbers: Sequence[str] | None = None,
    expansion: Expansion = NO_EXPANSION,
) -> Gate | None:
    """The evidence gate (see the module docstring): how it passed, or None. ``numbers``: the
    question's own numbers and ids (``numbers_asked``); None takes the keywords with a digit.
    ``expansion``: the asking user's words the question used. O(k * t) for k keywords over
    t characters of evidence."""
    if not evidence:
        return None
    wanted = list(dict.fromkeys(ascii_digits(keyword).casefold() for keyword in keywords))
    numbered = [word for word in wanted if _DIGIT.search(word)] if numbers is None else list(numbers)
    haystack = ascii_digits("\n".join(item.hit.text for item in evidence)).casefold()
    # First, on every path: a close score for "gat 48" over a gat 47 piece is no answer.
    if not all(has_token(keyword, haystack) for keyword in numbered):
        return None
    best = evidence[0].hit
    # The score belongs to the best piece, so that piece itself must hold every number asked.
    own = ascii_digits(best.text).casefold()
    # A mostly Devanagari piece's typo score is its Latin spelling's (the module docstring).
    scores = (best.meaning_score,) if is_devanagari(best.text) else (best.meaning_score, best.fuzzy_score)
    if all(has_token(keyword, own) for keyword in numbered):
        for score in scores:
            if score is not None and score >= min_score:
                return "score"
    if not wanted:
        return None
    # As written: a whole word (a number, a whole token). A Romanised word found as written
    # ("malak" in a Latin text) is still a guess about meaning: it counts from the glossary on.
    worded = word_text("\n".join(item.hit.text for item in evidence))
    written = [
        keyword in haystack if _DIGIT.search(keyword) else has_word(keyword, worded) for keyword in wanted
    ]
    english = sum(
        1
        for keyword, found in zip(wanted, written, strict=True)
        if found and not glossary.is_romanised(keyword)
    )
    if english * 2 >= len(wanted):
        return "keywords"
    glossed = [
        found or glossary.present(keyword, haystack) for keyword, found in zip(wanted, written, strict=True)
    ]
    if sum(glossed) * 2 >= len(wanted):
        return "glossary"
    words = glossary.sound_words(haystack)
    heard = [
        found or glossary.sounds_like(keyword, words) for keyword, found in zip(wanted, glossed, strict=True)
    ]
    if sum(heard) * 2 >= len(wanted):
        return "sound"
    if not expansion.used or not any(
        as_written(keyword, haystack)
        for keyword in wanted
        if not (expansion.asks(keyword) or glossary.is_romanised(keyword))
    ):
        return None
    mine = [found or expansion.found(keyword, haystack) for keyword, found in zip(wanted, heard, strict=True)]
    return "personal" if sum(mine) * 2 >= len(wanted) else None


def any_hit_scores(evidence: Sequence[Evidence], min_score: float, numbers: Sequence[str]) -> bool:
    """A translated search's gate: any hit (not a neighbour) whose meaning score reaches
    ``min_score`` and that holds every number asked as a whole token. O(h * t)."""
    return any(
        not item.neighbour
        and item.hit.meaning_score is not None
        and item.hit.meaning_score >= min_score
        and all(has_token(number, ascii_digits(item.hit.text).casefold()) for number in numbers)
        for item in evidence
    )


def holds_numbers(evidence: Sequence[Evidence], numbers: Sequence[str]) -> bool:
    """The gate of a whole document (CX5): some evidence, and every number asked in it as a
    whole token (``has_token``). No keyword rule: an English question about a Hindi or Marathi
    document shares no word with it, so the model and ``verify`` decide. O(t)."""
    haystack = ascii_digits("\n".join(item.hit.text for item in evidence)).casefold()
    return bool(evidence) and all(has_token(number, haystack) for number in numbers)


def numbers_asked(question: str, keywords: Sequence[str]) -> list[str]:
    """The numbers and ids the question asks about, whole, from its own text (``_fold``ed):
    "1,20,000" is one number, never 1, 20 and 000; "48-49" and "48-A" are kept as written. A
    token none of whose parts ``normalise`` kept as a keyword is left out, so a date's year or
    the 30 of "last 30 days" is not asked. Fail closed: a keyword with a digit that no token
    covers is asked as it is, so a number is never lost. O(q)."""
    folded = _fold(ascii_digits(question).casefold())
    kept = set(keywords)
    ids = [t for t in _ID_TOKEN.findall(folded) if _LETTER.search(t) and _DIGIT.search(t)]
    tokens = _NUMBER_TOKEN.findall(folded) + ids
    asked = [t for t in tokens if kept & {*_WORD.findall(t), *_ALNUM.findall(t)}]
    covered = {part for t in asked for part in (t, *_WORD.findall(t), *_ALNUM.findall(t))}
    asked += [word for word in keywords if _DIGIT.search(word) and word not in covered]
    return list(dict.fromkeys(asked))


def has_token(keyword: str, folded: str) -> bool:
    """A keyword with a digit is in ``folded`` (``ascii_digits`` and casefolded text, then
    ``_fold``ed here, like the keyword) only as a whole token: a number as one of the text's
    numbers (``_NUMBER_TOKEN``), equal by value (450 is 450.00, 1,20,000 is 120000) or, with
    a slash or dash, as written, so "48" is not in 148, 1,48,000, 4.48, 47/48, 48/2, 48-A or
    A-48; one with a letter as one of its ids (``_ID_TOKEN``), as written, so "inv-1042" is
    not in INV-10421. A keyword with no digit is a plain substring, as before. O(t)."""
    if not _DIGIT.search(keyword):
        return keyword in folded
    keyword, folded = _fold(keyword), _fold(folded)
    if _LETTER.search(keyword):
        return keyword in _ID_TOKEN.findall(folded)
    value = _value(keyword)
    return any(
        token == keyword or (value is not None and _value(token) == value)
        for token in _NUMBER_TOKEN.findall(folded)
    )


def word_text(text: str) -> str:
    """``text`` for ``has_word``: digits as ASCII, a space where a lower-case letter meets a
    capital or a letter meets a digit ("GrandTotal" is grand total, "GSTIN27X" is gstin 27 x),
    NFC, casefolded. Never for numbers: "INV1042" holds no 1042 (``has_token``). O(n)."""
    return unicodedata.normalize("NFC", _WORD_JOINS.sub(" ", ascii_digits(text))).casefold()


def has_word(keyword: str, text: str) -> bool:
    """``keyword`` (casefolded) in ``text`` (``word_text``) as a whole word, maybe with an
    English plural "s" or "es" after it: no letter, mark or digit right before or after (PR
    2b-2's code review: "rin" is not in "printing", "ekar" not in "ekart"; "invoice" is in
    "invoices"). A keyword with a digit is never a word here: numbers are ``has_token``'s.
    O(t)."""
    keyword = unicodedata.normalize("NFC", keyword)
    start = text.find(keyword)
    while start >= 0:
        end = start + len(keyword)
        if not _word_char(text[start - 1 : start]):
            for suffix in ("", "s", "es"):
                after = end + len(suffix)
                if text[end:after] == suffix and not _word_char(text[after : after + 1]):
                    return True
        start = text.find(keyword, start + 1)
    return False


def _word_char(ch: str) -> bool:
    return bool(ch) and unicodedata.category(ch)[0] in "LMN"


def as_written(keyword: str, folded: str) -> bool:
    """For My words: ``keyword`` as written and whole, one with a digit by ``has_token``,
    any other by ``has_word``. O(t)."""
    return has_token(keyword, folded) if _DIGIT.search(keyword) else has_word(keyword, folded)


def _fold(text: str) -> str:
    """NFKC, and every hyphen and dash as "-", so the question and the evidence write a
    joined number the same way. O(n)."""
    return unicodedata.normalize("NFKC", text).translate(_DASHES)


def _value(token: str) -> Decimal | None:
    """A plain number's value, its commas only as Indian or Western grouping; None for a
    token with a slash or dash, or other commas. O(n)."""
    whole, _, fraction = token.partition(".")
    if any(ch in token for ch in "/-") or (fraction and not fraction.isdigit()):
        return None
    if "," in whole and not (_INDIAN.fullmatch(whole) or _WESTERN.fullmatch(whole)):
        return None
    return Decimal(token.replace(",", ""))


def closest_documents(hits: Sequence[Hit]) -> list[dict[str, str]]:
    """The distinct documents of the top hits, best first, at most ``CLOSEST_DOCUMENTS``."""
    seen: dict[str, str] = {}
    for hit in hits:
        if hit.document_id not in seen:
            seen[hit.document_id] = hit.document_name
        if len(seen) == CLOSEST_DOCUMENTS:
            break
    return [{"id": document_id, "display_name": name} for document_id, name in seen.items()]


def looks_like_instructions(text: str) -> bool:
    """Any phrase of ``_INSTRUCTION_LIKE`` in the NFKC text, without the marks that draw nothing. O(n)."""
    folded = strip_ignorable(unicodedata.normalize("NFKC", text)).translate(_BLANKS)
    return any(pattern.search(folded) for pattern in _INSTRUCTION_LIKE)


def valid_regions(regions: Sequence[Any]) -> list[dict[str, Any]]:
    """Regions rebuilt through the one region format (reading/spans.py); malformed ones dropped."""
    valid: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        try:
            location = location_from_json(region)
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(location, BoxLocation):
            usable = location.is_valid()
        else:
            usable = min(location.sheet, location.row, location.column) >= 1
        if not usable:
            continue
        valid.append(location_to_json(location))
    return valid
