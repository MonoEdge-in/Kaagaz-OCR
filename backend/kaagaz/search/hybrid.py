"""Search one customer's pieces three ways, then merge the three rankings.

The three searches (docs/architecture.md, "Search, in simple terms"):

1. **Exact words:** ``websearch_to_tsquery('english', q)`` against the piece's
   ``tsv`` column, and ``websearch_to_tsquery('simple', k)`` against
   ``tsv_simple`` (the text and its Latin spelling, unstemmed: PRD 3 W1), where
   ``k`` is the question's ``normalise`` keywords, so the simple query does not
   require stop words ("the", "of"). This parser never raises a syntax error,
   so a customer's raw question is safe to pass. A question of stop words only
   ("the and of") becomes an empty query that matches nothing. A third query,
   ``to_tsquery('simple', g)`` against ``tsv_simple``, ORs the Marathi and
   Hindi equivalents of the keywords (``glossary.word_query``, council 2 Oct
   2026), so "owner" finds भोगवटादाराचे. ``g`` is built from the glossary's
   code constants only, never from the question's text, so ``to_tsquery``
   cannot fail on it; "" (no keyword listed) is NULL, which matches nothing.
   The asking user's own words (My words, council 3 Oct 2026, A3) join this OR
   arm only through ``expansion.word_query``'s safe builder: letter and mark
   runs of their text, never the text itself; another user's words are never
   loaded (kaagaz/answering/expansion.py).
   A fourth query, ``to_tsquery('simple', sk)``, matches the sound keys of
   the question's words (``sound_query``, council 3 Oct 2026, PR 2b) against
   the piece's ``tsv_sound`` (migration 0011: the sound keys of
   ``coalesce(text_latin, text)``, stored), so "bhogwatdar"
   finds भोगवटादार. ``sk`` is built from ``sound_key`` output only, each term
   checked to be ``[a-z]`` letters, never from the question's text; "" is
   NULL, as for ``g``. A piece scores the highest ``ts_rank_cd`` of the four,
   and every piece one of the three exact queries matched comes first, so a
   piece found only by sound fills the slots the exact matches leave. The exact
   queries need every word, the sound one any word: when no exact query
   matches, the list is "any word", not "every word". So in the sound tier a
   piece ranks by the words it matches, each weighed by how rare it is among
   the candidates (the sum of ln(candidates / candidates with the word)), then
   by its score: for "salary of Shinde" the one Shinde piece comes before sixty
   salary slips.
   ``search_documents`` shares this list, so library search finds these
   pieces too.
2. **Typo tolerant:** pg_trgm ``word_similarity(q, ...)`` with ``q <% ...``
   against the text and its Latin spelling (W2), the higher of the two, so
   "invioce" still finds "invoice" and "shindey" finds "शिंदे". The ``<%``
   cut-off is pg_trgm's ``word_similarity_threshold``, set for this transaction
   only to ``FUZZY_THRESHOLD`` (the default 0.6 misses one swapped letter pair
   in a seven letter word: "invioce" against "invoice" scores 0.375).
3. **Meaning:** cosine distance ``<=>`` between the question's embedding and
   each piece's (V2). Skipped when there is no embedding (no model, or it is
   down): the other two still answer. Only vectors whose ``embedding_model``
   is the caller's ``recipe`` count, so vectors from another model are left
   out until they are embedded again (edge case 7). The HNSW index
   ``pieces_embedding`` is used with every filter in WHERE: pgvector 0.8's
   iterative scan (``hnsw.iterative_scan = relaxed_order``) keeps walking the
   graph until LIMIT rows pass the filters, and the outer query sorts them
   again by exact distance. The walk stops after ``hnsw.max_scan_tuples``
   (20,000) tuples, so a small scope in a big table can come back short: then
   the same filters run as an exact scan. A search statement past
   ``STATEMENT_TIMEOUT_MS`` is cancelled; a meaning list that times out is
   dropped and the words and typos still answer.

Every query filters on ``p.customer_id = :c`` itself, on top of row level
security (two locks, docs/architecture.md), and sees only pieces of documents
that are ready, the current version of their family and not in Trash
(``LIVE_CURRENT`` in kaagaz/db/visibility.py, PRD 1 F5 and F6). Each list
keeps at most ``CANDIDATES`` pieces.

The scope (PRD 3 Q1, kaagaz/search/scope.py) is in the WHERE of all three
lists, before ranking: static predicates, each true for a NULL parameter (the
custom plans drop them). A scope that matches nothing returns before any SQL.
``search_documents`` (Q2) groups the candidates by document. ``neighbours`` (Q4)
fetches the pieces next to an answer's hits, with the same filters and scope. A
piece of an invoice whose check failed comes back needs review (edge case 10).

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
fallback is O(n * dim) over the scope's n vectors. The merge is O(150 log 150)
and the grouping O(150); then one indexed fetch of at most ``limit`` (or
``max_documents``) pieces. At most ten statements in the caller's transaction
(with the meaning list's savepoint).

ponytail: the GIN posting lists span all customers (the customer filter comes
after the index); fine at this size, a per-customer partition is the upgrade.
"""

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError

from kaagaz.answering import glossary
from kaagaz.answering.expansion import NO_EXPANSION, Expansion, word_query
from kaagaz.answering.hinglish import DATE_WORDS, STOP_WORDS, normalise
from kaagaz.chunking.script import SOUND_KEY_MIN, has_devanagari_letters, latin_spelling, sound_keys
from kaagaz.search.scope import EVERYTHING, Scope

if TYPE_CHECKING:
    from kaagaz.answering.evidence import Hit  # evidence imports this module

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
# The sound query (sound_query): at most this many terms, and prefix matching only for
# words typed with at least this many letters (council 3 Oct 2026, PR 2b).
SOUND_TERMS_MAX = 12
SOUND_PREFIX_WORD_MIN = 5
SOUND_START_MIN = 4  # letters: the shortest start of a key that is a term of its own
# Words that are never a sound term: English, Hindi and Romanised question words and fillers,
# and date words ("this", "last", "month").
_NO_SOUND = STOP_WORDS | DATE_WORDS | glossary.ROMANISED_STOP
_SOUND_TERM = re.compile(f"[a-z]{{{SOUND_KEY_MIN},}}")

# Every statement below repeats the same filter in full, so each one can be read
# (and checked for the customer filter) on its own: this customer's pieces of
# ready, current documents that are not in Trash only. The ``LIVE_CURRENT`` text
# is written out in each one, before any LIMIT (tests/test_visibility_guard.py),
# and so is the scope (the s_* parameters, Scope.params; tests/test_search_scope_db.py).

# The sound query's words are separated by ";" (sound_query): sw holds one tsquery per word,
# heard the words a piece matches, and rare each word's weight, ln(candidates / candidates
# matching it): a word on every candidate weighs 0.
_WORD = text(
    "WITH c AS MATERIALIZED ("
    "SELECT p.id, p.document_id, p.seq, d.created_at,"
    " GREATEST(ts_rank_cd(p.tsv, e.q), ts_rank_cd(p.tsv_simple, s.q),"
    " ts_rank_cd(p.tsv_simple, g.q), ts_rank_cd(p.tsv_sound, sk.q)) AS score,"
    " coalesce(p.tsv @@ e.q OR p.tsv_simple @@ s.q OR p.tsv_simple @@ g.q, false) AS exact,"
    " ARRAY(SELECT u.i FROM unnest(sw.qs) WITH ORDINALITY AS u (q, i) WHERE p.tsv_sound @@ u.q) AS heard"
    " FROM pieces p"
    " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " CROSS JOIN websearch_to_tsquery('english', :q) AS e (q)"
    " CROSS JOIN websearch_to_tsquery('simple', :k) AS s (q)"
    " CROSS JOIN (SELECT CASE WHEN CAST(:g AS text) = '' THEN NULL"
    " ELSE to_tsquery('simple', CAST(:g AS text)) END) AS g (q)"
    " CROSS JOIN (SELECT CASE WHEN CAST(:sk AS text) = '' THEN NULL"
    " ELSE to_tsquery('simple', replace(CAST(:sk AS text), ';', '|')) END) AS sk (q)"
    " CROSS JOIN (SELECT ARRAY(SELECT to_tsquery('simple', w)"
    " FROM unnest(string_to_array(CAST(:sk AS text), ';')) AS w WHERE w <> '')) AS sw (qs)"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))"
    " AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt"
    " WHERE dt.customer_id = :c AND dt.tag_id = ANY(CAST(:s_tags AS uuid[])) GROUP BY dt.document_id"
    " HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))"
    " AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))"
    " AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))"
    " AND (CAST(:s_documents AS uuid[]) IS NULL OR p.document_id = ANY(CAST(:s_documents AS uuid[])))"
    " AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL"
    " OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)"
    " BETWEEN coalesce(CAST(:s_from AS date), '-infinity') AND coalesce(CAST(:s_to AS date), 'infinity'))"
    " AND (p.tsv @@ e.q OR p.tsv_simple @@ s.q OR p.tsv_simple @@ g.q OR p.tsv_sound @@ sk.q)),"
    " rare AS MATERIALIZED (SELECT h.i, ln((SELECT count(*) FROM c) / CAST(count(*) AS float8)) AS w"
    " FROM c CROSS JOIN unnest(c.heard) AS h (i) GROUP BY h.i)"
    " SELECT c.id, c.document_id, c.score, c.exact FROM c"
    " ORDER BY c.exact DESC,"
    " CASE WHEN c.exact THEN 0 ELSE (SELECT sum(r.w) FROM rare r WHERE r.i = ANY(c.heard)) END DESC,"
    " c.score DESC, c.created_at, c.document_id, c.seq, c.id LIMIT :n"
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
    "SELECT p.id, p.document_id, GREATEST(word_similarity(:q, p.text), word_similarity(:ql, p.text_latin))"
    " AS score"
    " FROM pieces p"
    " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))"
    " AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt"
    " WHERE dt.customer_id = :c AND dt.tag_id = ANY(CAST(:s_tags AS uuid[])) GROUP BY dt.document_id"
    " HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))"
    " AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))"
    " AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))"
    " AND (CAST(:s_documents AS uuid[]) IS NULL OR p.document_id = ANY(CAST(:s_documents AS uuid[])))"
    " AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL"
    " OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)"
    " BETWEEN coalesce(CAST(:s_from AS date), '-infinity') AND coalesce(CAST(:s_to AS date), 'infinity'))"
    " AND (:q <% p.text OR :ql <% p.text_latin)"
    " ORDER BY score DESC, d.created_at, p.document_id, p.seq, p.id LIMIT :n"
)

# The index query (V2): every filter in WHERE, the index order and LIMIT inside
# the MATERIALIZED step, so the planner can walk pieces_embedding. relaxed_order
# may return rows slightly out of order; "distance + 0" makes the outer sort use
# the exact distances, never the index, and ties break on the piece id.
_MEANING = text(
    "WITH candidates AS MATERIALIZED ("
    " SELECT p.id, p.document_id, p.embedding <=> CAST(:vec AS halfvec(1024)) AS distance"
    " FROM pieces p"
    " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND p.embedding_model = :recipe"
    " AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))"
    " AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt"
    " WHERE dt.customer_id = :c AND dt.tag_id = ANY(CAST(:s_tags AS uuid[])) GROUP BY dt.document_id"
    " HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))"
    " AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))"
    " AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))"
    " AND (CAST(:s_documents AS uuid[]) IS NULL OR p.document_id = ANY(CAST(:s_documents AS uuid[])))"
    " AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL"
    " OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)"
    " BETWEEN coalesce(CAST(:s_from AS date), '-infinity') AND coalesce(CAST(:s_to AS date), 'infinity'))"
    " ORDER BY p.embedding <=> CAST(:vec AS halfvec(1024)) LIMIT :n"
    ") SELECT id, document_id, distance FROM candidates ORDER BY distance + 0, id"
)

# ponytail: the exact fallback (PROVISIONAL, plan item 4) for a scope the index
# walk came back short on, because hnsw.max_scan_tuples (20,000 tuples) ran out
# first: a small customer or document in a big table. The same filters, with no
# order inside the MATERIALIZED step, so the index is never used: O(n * dim) for
# the scope's n vectors. Upgrade path: a per-customer partial or partitioned
# index, or a higher max_scan_tuples, once a scope is too big to scan exactly.
_MEANING_EXACT = text(
    "WITH candidates AS MATERIALIZED ("
    " SELECT p.id, p.document_id, p.embedding <=> CAST(:vec AS halfvec(1024)) AS distance"
    " FROM pieces p"
    " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND p.embedding_model = :recipe"
    " AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))"
    " AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt"
    " WHERE dt.customer_id = :c AND dt.tag_id = ANY(CAST(:s_tags AS uuid[])) GROUP BY dt.document_id"
    " HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))"
    " AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))"
    " AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))"
    " AND (CAST(:s_documents AS uuid[]) IS NULL OR p.document_id = ANY(CAST(:s_documents AS uuid[])))"
    " AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL"
    " OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)"
    " BETWEEN coalesce(CAST(:s_from AS date), '-infinity') AND coalesce(CAST(:s_to AS date), 'infinity'))"
    ") SELECT id, document_id, distance FROM candidates ORDER BY distance, id LIMIT :n"
)

# A piece of an invoice whose check failed is never verified (edge case 10): its
# reading may be sure, but the figures do not add up.
_DETAILS = text(
    "SELECT p.id, p.document_id, d.display_name, d.detected_type, p.page_from, p.page_to, p.text,"
    " p.regions, CASE WHEN EXISTS (SELECT 1 FROM invoices i WHERE i.customer_id = :c"
    " AND i.document_id = p.document_id AND i.check_status = 'failed') THEN 'needs_review' ELSE p.label END,"
    " d.doc_type, p.seq, p.context_header"
    " FROM pieces p"
    " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND p.id = ANY(CAST(:ids AS uuid[]))"
)

# Q4: the pieces next to each hit (seq - :n to seq + :n of its document) through
# UNIQUE (document_id, seq), with the same filters and scope as the three lists,
# so a piece in Trash, an older version or outside the scope is never one (edge
# case 5). The columns are _DETAILS's; ordered by the hit's rank, then seq.
_NEIGHBOURS = text(
    "SELECT p.id, p.document_id, d.display_name, d.detected_type, p.page_from, p.page_to, p.text,"
    " p.regions, CASE WHEN EXISTS (SELECT 1 FROM invoices i WHERE i.customer_id = :c"
    " AND i.document_id = p.document_id AND i.check_status = 'failed') THEN 'needs_review' ELSE p.label END,"
    " d.doc_type, p.seq, p.context_header"
    " FROM unnest(CAST(:documents AS uuid[]), CAST(:seqs AS integer[]))"
    " WITH ORDINALITY AS h (document_id, seq, rank)"
    " JOIN pieces p ON p.customer_id = :c AND p.document_id = h.document_id"
    " AND p.seq BETWEEN h.seq - :n AND h.seq + :n"
    " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))"
    " AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt"
    " WHERE dt.customer_id = :c AND dt.tag_id = ANY(CAST(:s_tags AS uuid[])) GROUP BY dt.document_id"
    " HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))"
    " AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))"
    " AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))"
    " AND (CAST(:s_documents AS uuid[]) IS NULL OR p.document_id = ANY(CAST(:s_documents AS uuid[])))"
    " AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL"
    " OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)"
    " BETWEEN coalesce(CAST(:s_from AS date), '-infinity') AND coalesce(CAST(:s_to AS date), 'infinity'))"
    " ORDER BY h.rank, p.seq"
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
    seq: int  # the piece's place in its document, from 0
    context_header: str  # where it sits (kaagaz/chunking/context.py): document text too, untrusted


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


def sound_query(question: str) -> str:
    """The sound keys of the question's words as ``to_tsquery('simple', ...)`` texts, one
    per word, joined by " ; " (``_WORD`` ORs them all, and counts the words a piece
    matches); "" when none has a key of ``SOUND_KEY_MIN`` letters or more (shorter keys are never
    matched: kar, khar and khara are all kr). A part of the question with a digit in it
    ("INV-1042", "gat48") is an identifier, which the other queries match as written: it
    gives no key (``sound_keys``, as on the pieces' side). A question word or filler
    (``_NO_SOUND``: "and", "of", "kiti", "ahe") gives none either.

    A word typed with ``SOUND_PREFIX_WORD_MIN`` letters or more matches by prefix both
    ways (council 3 Oct 2026, narrowed by its code review): ``key:*`` finds a longer key
    ("jamin" finds jaminicha's jminic), and each shorter start of its key, down to
    ``SOUND_START_MIN`` letters, is a whole term ("jaminicha" finds jamin's jmin). A
    shorter word matches its whole key only. Every word's own key comes first, then
    each word's shorter starts, longest first, at most ``SOUND_TERMS_MAX`` terms in all.
    Each term is checked to be ``[a-z]`` letters, so no character of the question reaches
    ``to_tsquery``. O(n) for n characters: a key has at most 40 letters.
    """
    words: dict[str, list[str]] = {}  # each word's key -> its terms, its own key first
    for word, key in sound_keys(question):
        if len(key) < SOUND_KEY_MIN or word.casefold() in _NO_SOUND or key in words:
            continue
        if len(word) < SOUND_PREFIX_WORD_MIN:
            words[key] = [key]
            continue
        words[key] = [f"{key}:*", *(key[:end] for end in range(len(key) - 1, SOUND_START_MIN - 1, -1))]
    # Every word's own key first, then the shorter starts, at most SOUND_TERMS_MAX in all.
    ranked = [terms[0] for terms in words.values()] + [term for terms in words.values() for term in terms[1:]]
    kept = set(list(dict.fromkeys(ranked))[:SOUND_TERMS_MAX])
    for term in kept:
        if not _SOUND_TERM.fullmatch(term.removesuffix(":*")):
            raise ValueError("a sound term is ASCII letters only")
    groups = [" | ".join(term for term in terms if term in kept) for terms in words.values()]
    return " ; ".join(group for group in groups if group)


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
    scope: Scope = EVERYTHING,
    expansion: Expansion = NO_EXPANSION,
) -> list[SearchHit]:
    """The best ``limit`` pieces of this customer's ready, current documents
    that are not in Trash and are inside ``scope``, best first.

    ``conn`` must come from ``Database.customer(customer_id)``; its transaction
    gets this search's settings (the timeout, custom plans, the fuzzy threshold,
    the index walk), which end with it. ``recipe`` is the ``embedding_model`` of
    vectors made the current way; ``ef_search`` is SEARCH_EF. An empty question
    with no embedding finds nothing, and so does a scope that matches nothing,
    before any SQL runs (edge case 1). ``expansion``: the asking user's words the
    question used, searched as OR alternatives only (kaagaz/answering/expansion.py).
    """
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit must be between 1 and 100")
    if scope.matches_nothing:
        return []
    fused, _ = _candidates(conn, customer_id, query_text, embedding, recipe, ef_search, scope, expansion)
    return _hits(conn, customer_id, fused[:limit])


@dataclass(frozen=True, slots=True)
class DocumentResult:
    """A document found (Q2): its best piece, whose score is the document's, and how many
    more of its pieces were among the candidates (not every match in the document)."""

    best: SearchHit
    more: int


def search_documents(
    conn: Connection,
    *,
    customer_id: str,
    query_text: str,
    embedding: list[float] | None,
    max_documents: int,
    recipe: str,
    ef_search: int,
    scope: Scope = EVERYTHING,
) -> list[DocumentResult]:
    """The best ``max_documents`` documents, best first (Q2). ``fuse`` ranks best first, so
    one pass groups the c candidates in an insertion-ordered dict, opening no document
    after ``max_documents``: O(c) time and space. Details are fetched for the best pieces only.
    """
    if not 1 <= max_documents <= MAX_LIMIT:
        raise ValueError("max_documents must be between 1 and 100")
    if scope.matches_nothing:
        return []
    fused, owners = _candidates(conn, customer_id, query_text, embedding, recipe, ef_search, scope)
    best: dict[str, FusedPiece] = {}
    more: dict[str, int] = {}
    for piece in fused:
        document_id = owners[piece.piece_id]
        if document_id in best:
            more[document_id] += 1
        elif len(best) < max_documents:
            best[document_id] = piece
            more[document_id] = 0
    hits = _hits(conn, customer_id, list(best.values()))
    return [DocumentResult(hit, more[hit.document_id]) for hit in hits]


def _candidates(
    conn: Connection,
    customer_id: str,
    query_text: str,
    embedding: list[float] | None,
    recipe: str,
    ef_search: int,
    scope: Scope,
    expansion: Expansion = NO_EXPANSION,
) -> tuple[list[FusedPiece], dict[str, str]]:
    """The three lists inside ``scope``, merged best first, and each piece's document id.
    W3: ``text_latin`` is matched with the question's ``latin_spelling``, and the simple
    word query takes the ``normalise`` keywords, which spell Devanagari in Latin letters;
    the glossary query takes their Marathi and Hindi equivalents (the module docstring)."""
    conn.execute(_SET_SEARCH, {"ms": str(STATEMENT_TIMEOUT_MS)})
    word: list[tuple[str, float]] = []
    fuzzy: list[tuple[str, float]] = []
    meaning: list[tuple[str, float]] = []
    owners: dict[str, str] = {}
    if query_text.strip():
        asked = normalise(query_text).keywords
        latin = latin_spelling(query_text) if has_devanagari_letters(query_text) else query_text
        params = {
            "c": customer_id,
            "q": query_text,
            "k": " ".join(asked),
            "g": word_query(asked, expansion),
            "sk": sound_query(latin),
            "ql": latin,
            "n": CANDIDATES,
        }
        word = _ranked(conn.execute(_WORD, {**params, **scope.params()}), owners)
        conn.execute(_SET_FUZZY_THRESHOLD, {"t": str(FUZZY_THRESHOLD)})
        fuzzy = _ranked(conn.execute(_FUZZY, {**params, **scope.params()}), owners)
    if embedding is not None:
        vector = {"c": customer_id, "vec": vector_literal(embedding), "recipe": recipe, "n": CANDIDATES}
        meaning = _meaning(conn, vector | scope.params(), ef_search, owners)
    return fuse(word, fuzzy, meaning), owners


def _ranked(rows: Iterable[Sequence[Any]], owners: dict[str, str]) -> list[tuple[str, float]]:
    """(piece id, score) of rows (piece id, document id, score), noting each piece's document."""
    ranked = []
    for row in rows:
        owners[str(row[0])] = str(row[1])
        ranked.append((str(row[0]), float(row[2])))
    return ranked


def _hits(conn: Connection, customer_id: str, fused: Sequence[FusedPiece]) -> list[SearchHit]:
    """The fused pieces with their details, in the same order: one indexed fetch.
    No scope here: the ids came from the scoped lists of this transaction."""
    if not fused:
        return []
    details = {
        str(row[0]): row
        for row in conn.execute(_DETAILS, {"c": customer_id, "ids": [hit.piece_id for hit in fused]})
    }
    # A piece removed between the two statements is left out.
    return [_hit(details[hit.piece_id], hit) for hit in fused if hit.piece_id in details]


def neighbours(
    conn: Connection, *, customer_id: str, hits: "Sequence[Hit]", size: int, scope: Scope = EVERYTHING
) -> list[SearchHit]:
    """Q4: the pieces up to ``size`` either side of each hit in its document, in ``scope``,
    each once, never a hit: the first hit's first, in seq order. ``conn`` comes from
    ``Database.customer(customer_id)``. One statement, O(h * size * log N) for h hits."""
    if size < 1 or not hits or scope.matches_nothing:
        return []
    params = {
        "c": customer_id,
        "documents": [hit.document_id for hit in hits],
        "seqs": [hit.seq for hit in hits],
        "n": size,
    }
    seen = {hit.piece_id for hit in hits}
    found: list[SearchHit] = []
    for row in conn.execute(_NEIGHBOURS, params | scope.params()):
        if str(row[0]) not in seen:
            seen.add(str(row[0]))
            found.append(_hit(row, FusedPiece(str(row[0]), 0.0, None, None, None)))
    return found


def _hit(row: Sequence[Any], fused: FusedPiece) -> SearchHit:
    """A _DETAILS or _NEIGHBOURS row as a hit, with ``fused``'s scores."""
    return SearchHit(
        piece_id=fused.piece_id,
        document_id=str(row[1]),
        document_name=str(row[2]),
        detected_type=str(row[3]),
        doc_type=None if row[9] is None else str(row[9]),
        page_from=int(row[4]),
        page_to=int(row[5]),
        text=str(row[6]),
        regions=list(row[7]),
        label=str(row[8]),
        score=fused.score,
        word_score=fused.word_score,
        fuzzy_score=fused.fuzzy_score,
        meaning_score=fused.meaning_score,
        seq=int(row[10]),
        context_header=str(row[11]),
    )


def _meaning(
    conn: Connection, params: dict[str, Any], ef_search: int, owners: dict[str, str]
) -> list[tuple[str, float]]:
    """The meaning list: the index walk, then the exact scan when the walk came
    back with fewer than CANDIDATES rows (a scope with fewer vectors than that
    is scanned exactly too, which is cheap). Empty when the statement timeout
    cancels it: the savepoint keeps the transaction usable for the rest."""
    conn.execute(_SET_INDEX_SCAN, {"ef": str(ef_search)})
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
    found = [row for row in rows if math.isfinite(float(row[2]))]
    return _ranked([(row[0], row[1], 1.0 - float(row[2])) for row in found], owners)
