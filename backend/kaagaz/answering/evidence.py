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

A hit with no valid region cannot be pointed at, so it is not evidence
(CLAUDE.md never-do 5).

Neighbours (PRD 3 Q4) are fetched only for the usable hits, after the gate passed;
they pass the same two checks, are numbered after every hit (a window cut drops
them first) and are marked.

Time is O(h * t) for h hits of t characters: one pass of fixed regular
expressions and substring checks per hit.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol

from kaagaz.answering import glossary
from kaagaz.answering.sanitize import strip_ignorable
from kaagaz.chunking.script import ascii_digits
from kaagaz.db.engine import Database
from kaagaz.extraction.parsing import _INDIAN, _WESTERN
from kaagaz.reading.spans import BoxLocation, location_from_json, location_to_json
from kaagaz.search.scope import Scope

CLOSEST_DOCUMENTS = 3

# How the gate passed: a score, the English keywords, or only with the glossary's help.
Gate = Literal["score", "keywords", "glossary"]
_DIGIT = re.compile("[0-9]")
_LETTER = re.compile("[a-z]")
# Whole tokens (has_token): no letter, mark or digit (\w) right before or after.
# A number is not a token of "48-A" or "48" then an en dash and "49" (or "47" then one and
# "48"): a dash, slash or dot joins it to a letter or digit. The hyphens (U+2010, U+2011)
# and the en dash (U+2013) count; an ASCII hyphen between digits already makes one token.
_JOINERS = "\u2010\u2011\u2013"
_NUMBER_TOKEN = re.compile(
    rf"(?<!\w)(?<![0-9][{_JOINERS}])[0-9]+(?:[.,/-][0-9]+)*(?!\w)(?![-/.{_JOINERS}][0-9a-z])"
)
_ID_TOKEN = re.compile(r"(?<!\w)[a-z0-9]+(?:[-/.][a-z0-9]+)*(?!\w)")  # as normalise splits words
_WORD = re.compile(r"[a-z0-9]+(?:[-/.][a-z0-9]+)*")  # hinglish.normalise's words

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
        self, customer_id: str, text: str, embedding: list[float] | None, limit: int, scope: Scope
    ) -> Sequence[Hit]: ...

    def neighbours(self, customer_id: str, hits: Sequence[Hit], scope: Scope) -> Sequence[Hit]:
        """Q4: the pieces next to ``hits`` in ``scope``, each once, never one of them."""
        ...


class PgRetriever:
    """``Retriever`` over PostgreSQL: hybrid search, and the hits' neighbours, each inside
    ``db.customer``."""

    def __init__(self, db: Database, *, recipe: str, ef_search: int, neighbours: int) -> None:
        """``recipe``: the ``embedding_model`` tag meaning search uses (vectors with
        another tag are left out); ``ef_search``: SEARCH_EF; ``neighbours``:
        ANSWER_NEIGHBOURS, the pieces on each side of a hit (0: none)."""
        self._db = db
        self._recipe = recipe
        self._ef_search = ef_search
        self._neighbours = neighbours

    def search(
        self, customer_id: str, text: str, embedding: list[float] | None, limit: int, scope: Scope
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
            )

    def neighbours(self, customer_id: str, hits: Sequence[Hit], scope: Scope) -> Sequence[Hit]:
        from kaagaz.search.hybrid import neighbours

        if not self._neighbours or not hits:
            return []
        with self._db.customer(customer_id) as conn:
            return neighbours(conn, customer_id=customer_id, hits=hits, size=self._neighbours, scope=scope)


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
) -> Gate | None:
    """The evidence gate (see the module docstring): how it passed, or None. ``numbers``: the
    question's own numbers and ids (``numbers_asked``); None takes the keywords with a digit.
    O(k * t) for k keywords over t characters of evidence."""
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
    if all(has_token(keyword, own) for keyword in numbered):
        for score in (best.meaning_score, best.fuzzy_score):
            if score is not None and score >= min_score:
                return "score"
    if not wanted:
        return None
    english = sum(1 for keyword in wanted if keyword in haystack)
    if english * 2 >= len(wanted):
        return "keywords"
    either = sum(1 for keyword in wanted if keyword in haystack or glossary.present(keyword, haystack))
    return "glossary" if either * 2 >= len(wanted) else None


def numbers_asked(question: str, keywords: Sequence[str]) -> list[str]:
    """The numbers and ids the question asks about, whole, from its own text: "1,20,000" is
    one number, never 1, 20 and 000. Only those ``normalise`` kept as keywords count, so a
    date's year or the 30 of "last 30 days" does not. O(q)."""
    folded = ascii_digits(question).casefold()
    kept = set(keywords)
    ids = [token for token in _ID_TOKEN.findall(folded) if _LETTER.search(token) and _DIGIT.search(token)]
    found = _NUMBER_TOKEN.findall(folded) + ids
    return list(dict.fromkeys(token for token in found if all(w in kept for w in _WORD.findall(token))))


def has_token(keyword: str, folded: str) -> bool:
    """A keyword with a digit is in ``folded`` (``ascii_digits`` and casefolded text) only as
    a whole token: a number as one of the text's numbers (``_NUMBER_TOKEN``), equal by value
    (450 is 450.00, 1,20,000 is 120000) or, with a slash or dash, as written, so "48" is not in
    148, 1,48,000, 4.48, 47/48, 48/2 or 48-A; an id with a letter as one of its ids
    (``_ID_TOKEN``), as written, so "inv-1042" is not in INV-10421. A keyword with no digit
    is a plain substring, as before. O(t)."""
    if not _DIGIT.search(keyword):
        return keyword in folded
    if _LETTER.search(keyword):
        return keyword in _ID_TOKEN.findall(folded)
    value = _value(keyword)
    return any(
        token == keyword or (value is not None and _value(token) == value)
        for token in _NUMBER_TOKEN.findall(folded)
    )


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
