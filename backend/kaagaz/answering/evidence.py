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

PROVISIONAL, a change from the spec's "word score or meaning score": the word
score is PostgreSQL's raw ``ts_rank_cd``, which is small and unbounded (about
0.1 for an exact "INV-1042", 0.033 for "invoice grand total"), so it cannot
share one 0 to 1 threshold. The fuzzy score (``word_similarity``) and the
meaning score (cosine similarity) are both 0 to 1. An exact word match still
passes through the keyword rule. Vrushit decides.

Document text is untrusted. A piece that reads like instructions to a model
("ignore previous instructions...", "you are now the assistant", a fake
evidence tag) in its text or its context header (which holds the document's
name, PRD 3 Q5) is left out of the evidence entirely: it can neither steer the
model nor be quoted. PROVISIONAL: the phrase list is a first line of defence
next to the fenced prompt (draft.py) and the number check (verify.py); it is
not the only one.

A hit with no valid region cannot be pointed at, so it is not evidence
(CLAUDE.md never-do 5).

Neighbours (PRD 3 Q4, fetched in the search's transaction) pass the same two
checks, are numbered after every hit (a window cut drops them first) and are
marked; the gate never sees them.

Time is O(h * t) for h hits of t characters: one pass of fixed regular
expressions and substring checks per hit.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from kaagaz.db.engine import Database
from kaagaz.reading.spans import BoxLocation, location_from_json, location_to_json
from kaagaz.search.scope import Scope

CLOSEST_DOCUMENTS = 3

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


@dataclass(frozen=True, slots=True)
class Retrieved:
    hits: Sequence[Hit]  # best first
    neighbours: Sequence[Hit] = ()  # Q4: the pieces next to the hits, each once, never a hit


class Retriever(Protocol):
    def search(
        self, customer_id: str, text: str, embedding: list[float] | None, limit: int, scope: Scope
    ) -> Retrieved: ...


class PgRetriever:
    """``Retriever`` over PostgreSQL: hybrid search and the hits' neighbours, in one
    ``db.customer`` transaction."""

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
    ) -> Retrieved:
        # Imported here so the answering rules (and their tests) do not need the
        # search module; only the real retriever does.
        from kaagaz.search.hybrid import neighbours, search

        with self._db.customer(customer_id) as conn:
            hits = search(
                conn,
                customer_id=customer_id,
                query_text=text,
                embedding=embedding,
                limit=limit,
                recipe=self._recipe,
                ef_search=self._ef_search,
                scope=scope,
            )
            near = neighbours(conn, customer_id=customer_id, hits=hits, size=self._neighbours, scope=scope)
        return Retrieved(hits, near)


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


def enough_evidence(evidence: Sequence[Evidence], keywords: Sequence[str], min_score: float) -> bool:
    """The evidence gate (see the module docstring)."""
    if not evidence:
        return False
    best = evidence[0].hit
    for score in (best.meaning_score, best.fuzzy_score):
        if score is not None and score >= min_score:
            return True
    if not keywords:
        return False
    haystack = "\n".join(item.hit.text for item in evidence).casefold()
    present = sum(1 for keyword in keywords if keyword.casefold() in haystack)
    return present * 2 >= len(keywords)


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
    return any(pattern.search(text) for pattern in _INSTRUCTION_LIKE)


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
