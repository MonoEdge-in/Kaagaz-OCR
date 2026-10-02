"""Search one customer's pieces three ways, then merge the three rankings.

The three searches (docs/architecture.md, "Search, in simple terms"):

1. **Exact words:** ``websearch_to_tsquery('english', q)`` against the piece's
   ``tsv`` column, and ``websearch_to_tsquery('simple', k)`` against
   ``tsv_simple`` (the text and its Latin spelling, unstemmed: PRD 3 W1), where
   ``k`` is the question's ``normalise`` keywords, so the simple query does not
   require stop words ("the", "of"). A piece scores the higher ``ts_rank_cd``
   of the two. This parser never raises a syntax error, so a customer's raw
   question is safe to pass. A question of stop words only ("the and of")
   becomes an empty query that matches nothing.
2. **Typo tolerant:** pg_trgm ``word_similarity(q, ...)`` with ``q <% ...``
   against the text and its Latin spelling (W2), the higher of the two, so
   "invioce" still finds "invoice" and "shindey" finds "शिंदे". The ``<%``
   cut-off is pg_trgm's ``word_similarity_threshold``, set for this transaction
   only to ``FUZZY_THRESHOLD`` (the default 0.6 misses one swapped letter pair
   in a seven letter word: "invioce" against "invoice" scores 0.375).
3. **Meaning:** cosine distance ``<=>`` between the question's embedding and
   each piece's. Skipped when there is no embedding (no model, or it is down):
   the other two still answer. This scan is exact on purpose: the HNSW index
   applies the customer and "ready" filters only after taking its first
   candidates, so a small customer in a big table could get too few rows.
   The pgvector in use (0.8.6) has iterative index scans that could fix that,
   but they are not turned on here: the HNSW change is PRD 3's. The
   ``MATERIALIZED`` step keeps the planner from choosing the index.

Every query filters on ``p.customer_id = :c`` itself, on top of row level
security (two locks, docs/architecture.md), and sees only pieces of documents
that are ready, the current version of their family and not in Trash
(``LIVE_CURRENT`` in kaagaz/db/visibility.py, PRD 1 F5 and F6). Each list
keeps at most ``CANDIDATES`` pieces.

The scope (PRD 3 Q1, kaagaz/search/scope.py) is in the WHERE of all three
lists, before ranking: static predicates, each true for a NULL parameter (the
custom plans drop them). A scope that matches nothing returns before any SQL.
``search_documents`` (Q2) groups the candidates by document.

The lists are merged with reciprocal rank fusion (``fuse``): a piece scores the
sum of 1 / (k + rank) over the lists it is in, so a piece found two ways ranks
above one found one way, with no tuning of scores across the lists. Ties break
on the piece id, so the same question always gives the same order. Each
list's own raw score is kept on the hit: ``word_score`` is ``ts_rank_cd``,
``fuzzy_score`` is ``word_similarity`` (0 to 1) and ``meaning_score`` is the
cosine similarity, 1 minus the distance (1 is the same direction).

PROVISIONAL: the search design is Vrushit's (Sprint 2); RRF with k = 60, 50
candidates per list and the fuzzy threshold are starting values.

Cost: the word and fuzzy lists are GIN index scans whose posting lists hold
every customer's pieces, then O(m log m) to rank this customer's m matches.
The meaning list is about O(ef_search * log N * dim) on the index for N vectors
when the scope is large, at worst O(min(max_scan_tuples, N) * dim); the exact
fallback is O(n * dim) over the scope's n vectors. The merge is O(150 log 150);
then one indexed fetch of at most ``limit`` pieces. At most ten statements in
the caller's transaction (with the meaning list's savepoint).

ponytail: the GIN posting lists span all customers (the customer filter comes
after the index); fine at this size, a per-customer partition is the upgrade.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError

from kaagaz.answering.hinglish import normalise

# Candidates taken from each of the three lists before merging.
CANDIDATES = 50
# The reciprocal rank fusion constant; 60 is the usual starting value.
RRF_K = 60
# The most hits one search returns (the pagination cap, CLAUDE.md performance rules).
MAX_LIMIT = 100
# pg_trgm word similarity cut-off for the typo tolerant list. PROVISIONAL.
FUZZY_THRESHOLD = 0.3
# Every search statement is cancelled after this long (PROVISIONAL). A meaning
# list that runs out of time is dropped; the word and fuzzy lists still answer.
STATEMENT_TIMEOUT_MS = 3000
# PostgreSQL's SQLSTATE for a statement cancelled by statement_timeout.
_QUERY_CANCELED = "57014"

# Every statement below repeats the same filter in full, so each one can be read
# (and checked for the customer filter) on its own: this customer's pieces of
# ready, current documents that are not in Trash only. The ``LIVE_CURRENT`` text
# is written out in each one, before any LIMIT (tests/test_visibility_guard.py),
# and so is the scope (the s_* parameters, Scope.params; tests/test_search_scope_db.py).

_WORD = text(
    "SELECT p.id, GREATEST(ts_rank_cd(p.tsv, e.q), ts_rank_cd(p.tsv_simple, s.q)) AS score"
    " FROM pieces p"
    " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " CROSS JOIN websearch_to_tsquery('english', :q) AS e (q)"
    " CROSS JOIN websearch_to_tsquery('simple', :k) AS s (q)"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND p.tsv @@ websearch_to_tsquery('english', :q)"
    " ORDER BY score DESC, d.created_at, p.document_id, p.seq, p.id LIMIT :n"
)

# set_config(..., true) is SET LOCAL with a bound value (SET LOCAL takes none):
# each lasts until this transaction ends, so nothing leaks to the next one on
# the pooled connection. force_custom_plan: psycopg prepares a statement that has
# run five times on a connection, and after five custom plans PostgreSQL may keep
# its generic plan, which for the meaning list (no customer, vector or LIMIT
# known) is the exact scan, never the index. A custom plan uses this search's own
# values: the plan EXPLAIN shows.
_SET_FUZZY_THRESHOLD = text("SELECT set_config('pg_trgm.word_similarity_threshold', :t, true)")
_SET_SEARCH = text(
    "SELECT set_config('statement_timeout', :ms, true),"
    " set_config('plan_cache_mode', 'force_custom_plan', true)"
)
_SET_INDEX_SCAN = text(
    "SELECT set_config('hnsw.ef_search', :ef, true), set_config('hnsw.iterative_scan', 'relaxed_order', true)"
)

_FUZZY = text(
    "SELECT p.id, GREATEST(word_similarity(:q, p.text), word_similarity(:q, p.text_latin)) AS score"
    " FROM pieces p"
    " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND :q <% p.text"
    " ORDER BY score DESC, d.created_at, p.document_id, p.seq, p.id LIMIT :n"
)

# The index query (V2): every filter in WHERE, the index order and LIMIT inside
# the MATERIALIZED step, so the planner can walk pieces_embedding. relaxed_order
# may return rows slightly out of order; "distance + 0" makes the outer sort use
# the exact distances, never the index, and ties break on the piece id.
_MEANING = text(
    "WITH candidates AS MATERIALIZED ("
    " SELECT p.id, p.embedding <=> CAST(:vec AS vector) AS distance, d.created_at, p.document_id, p.seq"
    " FROM pieces p"
    " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND p.embedding IS NOT NULL"
    ") SELECT id, distance FROM candidates ORDER BY distance, created_at, document_id, seq, id LIMIT :n"
)

_DETAILS = text(
    "SELECT p.id, p.document_id, d.display_name, d.detected_type, p.page_from, p.page_to,"
    " p.text, p.regions, p.label, d.doc_type"
    " FROM pieces p"
    " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND p.id = ANY(CAST(:ids AS uuid[]))"
)


@dataclass(frozen=True, slots=True)
class SearchHit:
    piece_id: str
    document_id: str
    document_name: str
    detected_type: str
    doc_type: str | None  # what the document is (PRD 2 D3); None until it is understood
    page_from: int
    page_to: int
    # Document text: untrusted, shown as plain text only.
    text: str
    regions: list[dict[str, Any]]
    label: str
    score: float
    word_score: float | None
    fuzzy_score: float | None
    meaning_score: float | None


@dataclass(frozen=True, slots=True)
class FusedPiece:
    piece_id: str
    score: float
    word_score: float | None
    fuzzy_score: float | None
    meaning_score: float | None


def fuse(
    word: Sequence[tuple[str, float]],
    fuzzy: Sequence[tuple[str, float]],
    meaning: Sequence[tuple[str, float]],
    *,
    k: int = RRF_K,
) -> list[FusedPiece]:
    """Merge three ranked lists of (piece_id, raw score), each best first.

    A piece listed twice in one list counts once, at its best rank. The result
    is best first; a tie keeps the order the lists first gave the pieces in (each list
    breaks its own ties by upload time and position, never by the random piece id),
    so the same documents always rank the same. O(n log n) for n listed pieces.
    """
    if k < 0:
        raise ValueError("k must not be negative")
    scores: dict[str, float] = {}
    raw: dict[str, dict[str, float]] = {}
    for name, ranked in (("word", word), ("fuzzy", fuzzy), ("meaning", meaning)):
        rank = 0
        for piece_id, raw_score in ranked:
            own = raw.setdefault(piece_id, {})
            if name in own:
                continue
            rank += 1
            own[name] = raw_score
            scores[piece_id] = scores.get(piece_id, 0.0) + 1.0 / (k + rank)
    order = sorted(scores, key=lambda piece_id: -scores[piece_id])  # stable: ties keep first-seen order
    return [
        FusedPiece(
            piece_id=piece_id,
            score=scores[piece_id],
            word_score=raw[piece_id].get("word"),
            fuzzy_score=raw[piece_id].get("fuzzy"),
            meaning_score=raw[piece_id].get("meaning"),
        )
        for piece_id in order
    ]


def vector_literal(values: Sequence[float]) -> str:
    """pgvector's text form, ``[0.1,0.2]``, for a bound parameter cast to ``halfvec``."""
    if not values:
        raise ValueError("a vector needs at least one value")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("a vector holds finite numbers only")
    return "[" + ",".join(repr(float(value)) for value in values) + "]"


def search(
    conn: Connection,
    *,
    customer_id: str,
    query_text: str,
    embedding: list[float] | None,
    limit: int,
    recipe: str,
    ef_search: int,
) -> list[SearchHit]:
    """The best ``limit`` pieces of this customer's ready, current documents
    that are not in Trash and are inside ``scope``, best first.

    ``conn`` must come from ``Database.customer(customer_id)``; its transaction
    gets this search's settings (the timeout, custom plans, the fuzzy threshold,
    the index walk), which end with it. ``recipe`` is the ``embedding_model`` of
    vectors made the current way; ``ef_search`` is SEARCH_EF. An empty question
    with no embedding finds nothing.
    """
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit must be between 1 and 100")
    conn.execute(_SET_SEARCH, {"ms": str(STATEMENT_TIMEOUT_MS)})
    word: list[tuple[str, float]] = []
    fuzzy: list[tuple[str, float]] = []
    meaning: list[tuple[str, float]] = []
    owners: dict[str, str] = {}
    if query_text.strip():
        keywords = " ".join(normalise(query_text).keywords)
        params = {"c": customer_id, "q": query_text, "k": keywords, "n": CANDIDATES}
        word = [(str(row[0]), float(row[1])) for row in conn.execute(_WORD, params)]
        conn.execute(_SET_FUZZY_THRESHOLD, {"t": str(FUZZY_THRESHOLD)})
        fuzzy = _ranked(conn.execute(_FUZZY, {**params, **scope.params()}), owners)
    if embedding is not None:
        meaning = _meaning(conn, customer_id, vector_literal(embedding), recipe, ef_search)


def _hits(conn: Connection, customer_id: str, fused: Sequence[FusedPiece]) -> list[SearchHit]:
    """The fused pieces with their details, in the same order: one indexed fetch.
    No scope here: the ids came from the scoped lists of this transaction."""
    if not fused:
        return []
    details = {
        str(row[0]): row
        for row in conn.execute(_DETAILS, {"c": customer_id, "ids": [hit.piece_id for hit in fused]})
    }
    hits: list[SearchHit] = []
    for hit in fused:
        row = details.get(hit.piece_id)
        if row is None:  # removed between the two statements
            continue
        hits.append(
            SearchHit(
                piece_id=hit.piece_id,
                document_id=str(row[1]),
                document_name=str(row[2]),
                detected_type=str(row[3]),
                doc_type=None if row[9] is None else str(row[9]),
                page_from=int(row[4]),
                page_to=int(row[5]),
                text=str(row[6]),
                regions=list(row[7]),
                label=str(row[8]),
                score=hit.score,
                word_score=hit.word_score,
                fuzzy_score=hit.fuzzy_score,
                meaning_score=hit.meaning_score,
            )
        )
    return hits


def _meaning(
    conn: Connection, customer_id: str, vec: str, recipe: str, ef_search: int
) -> list[tuple[str, float]]:
    """The meaning list: the index walk, then the exact scan when the walk came
    back with fewer than CANDIDATES rows (a scope with fewer vectors than that
    is scanned exactly too, which is cheap). Empty when the statement timeout
    cancels it: the savepoint keeps the transaction usable for the rest."""
    conn.execute(_SET_INDEX_SCAN, {"ef": str(ef_search)})
    params = {"c": customer_id, "vec": vec, "recipe": recipe, "n": CANDIDATES}
    try:
        with conn.begin_nested():
            rows = conn.execute(_MEANING, params).all()
            if len(rows) < CANDIDATES:
                rows = conn.execute(_MEANING_EXACT, params).all()
    except OperationalError as error:
        if getattr(error.orig, "sqlstate", None) != _QUERY_CANCELED:
            raise
        return []
    # A zero vector has no direction: its distance is NaN and says nothing.
    return [(str(row[0]), 1.0 - float(row[1])) for row in rows if math.isfinite(float(row[1]))]
