"""How rare each word is in one organisation (#617 part 1, migration 0094's ``term_df`` and
``customer_stats``).

**Kept by the worker, never per upload.** ``refresh`` rebuilds an organisation's counts when
they are a day old (``REBUILD_EVERY``), missing, or its live pieces (current, ready, out of
Trash) have changed by ``CHANGE_SHARE`` or more since they were counted; it is checked on the
worker's hourly turn. A rebuild is one transaction: the old rows deleted, then ``ts_stat`` over
each word column (``FIELDS``) of the live pieces, and each column's piece count and average
length (distinct words a piece). A search meanwhile reads the old counts.

**Read by the word search** (kaagaz/search/hybrid.py): ``shares`` gives the share of the live
pieces that hold each of a query's alternatives (each one or more ``to_tsquery`` terms, ORed).
One on more than ``COMMON_SHARE`` only scores; it never brings candidates. A prefix term
(``jamin:*``) counts every word it starts, summed (pieces holding two of them count twice, so
it can only look more common than it is). No counts yet, or no more live pieces than a word
column can give candidates (``hybrid.CANDIDATE_CAP``): no shares, so nothing is common, as the
cap already bounds the work.

Cost: the check is one count over the organisation's pieces index; a rebuild reads each live
piece's three columns once (O(P) for P pieces) and writes one row a distinct word; ``shares`` is
one indexed range read a term.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import Connection, text

FIELDS = ("tsv", "tsv_simple", "tsv_sound")
COMMON_SHARE = 0.2  # #617: a word on more than this share of the live pieces only scores
CHANGE_SHARE = 0.05  # #617: rebuilt when the live pieces change by this share or more
REBUILD_EVERY = timedelta(hours=24)  # and at least nightly
REBUILD_TIMEOUT_MS = 600_000  # one organisation's rebuild, in the worker

_STATS = text("SELECT min(built_at), min(n) FROM customer_stats WHERE customer_id = :c")
_LIVE = text(
    "SELECT count(*) FROM pieces p JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
)
_CLEAR_TERMS = text("DELETE FROM term_df WHERE customer_id = :c")
_CLEAR_STATS = text("DELETE FROM customer_stats WHERE customer_id = :c")
# ts_stat runs its query as the caller, under row level security, with the customer filter in
# it; the column is one of FIELDS (format's %I quotes it) and the customer a uuid (%L).
_COUNT_TERMS = text(
    "INSERT INTO term_df (customer_id, field, term, df)"
    " SELECT :c, :f, s.word, s.ndoc FROM ts_stat(format("
    "$q$SELECT p.%I FROM pieces p JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " WHERE p.customer_id = %L AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'$q$,"
    " CAST(:f AS text), CAST(:c AS uuid))) AS s"
)
_COUNT_PIECES = text(
    "INSERT INTO customer_stats (customer_id, field, n, avgdl, built_at)"
    " SELECT :c, f.field, s.n, f.avgdl, :now FROM ("
    "SELECT count(*) AS n, coalesce(avg(length(p.tsv)), 0) AS tsv,"
    " coalesce(avg(length(p.tsv_simple)), 0) AS tsv_simple,"
    " coalesce(avg(length(p.tsv_sound)), 0) AS tsv_sound"
    " FROM pieces p JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready') AS s"
    " CROSS JOIN LATERAL (VALUES ('tsv', s.tsv), ('tsv_simple', s.tsv_simple), ('tsv_sound', s.tsv_sound))"
    " AS f (field, avgdl)"
)
_SET_TIMEOUT = text("SELECT set_config('statement_timeout', :ms, true)")
# Each term's pieces: the stored words from :lows up to :highs (byte order, term's COLLATE "C"),
# summed per alternative; with the column's live piece count.
_SHARES = text(
    "SELECT t.g, sum((SELECT coalesce(sum(x.df), 0) FROM term_df x WHERE x.customer_id = :c"
    ' AND x.field = :f AND x.term >= t.low COLLATE "C" AND x.term < t.high COLLATE "C")),'
    " (SELECT s.n FROM customer_stats s WHERE s.customer_id = :c AND s.field = :f)"
    " FROM unnest(CAST(:groups AS integer[]), CAST(:lows AS text[]), CAST(:highs AS text[]))"
    " AS t (g, low, high)"
    " GROUP BY t.g"
)
# The first string after every word that starts with a prefix, and after one word alone, in
# byte order: U+10FFFF is UTF-8's highest character, U+0001 its lowest (text holds no U+0000).
_AFTER_PREFIX, _AFTER_WORD = "\U0010ffff", "\x01"


def due(conn: Connection, customer_id: str, now: datetime) -> bool:
    """Whether the organisation's counts need a rebuild: none yet, a day old, or its live pieces
    changed by CHANGE_SHARE or more. ``conn`` comes from ``Database.customer(customer_id)``."""
    built_at, counted = conn.execute(_STATS, {"c": customer_id}).one()
    if built_at is None or built_at <= now - REBUILD_EVERY:
        return True
    live = int(conn.execute(_LIVE, {"c": customer_id}).scalar_one())
    return abs(live - int(counted)) >= CHANGE_SHARE * max(int(counted), 1)


def rebuild(conn: Connection, customer_id: str, now: datetime) -> None:
    """The organisation's counts written again from its live pieces (the module docstring)."""
    conn.execute(_SET_TIMEOUT, {"ms": str(REBUILD_TIMEOUT_MS)})
    conn.execute(_CLEAR_TERMS, {"c": customer_id})
    conn.execute(_CLEAR_STATS, {"c": customer_id})
    for field in FIELDS:
        conn.execute(_COUNT_TERMS, {"c": customer_id, "f": field})
    conn.execute(_COUNT_PIECES, {"c": customer_id, "now": now})


def shares(
    conn: Connection, customer_id: str, field: str, alternatives: Sequence[Sequence[str]], most: int
) -> list[float] | None:
    """For each alternative (its ``to_tsquery`` terms, ORed; ``x:*`` a prefix), the share of the
    organisation's live pieces of ``field`` that hold it (0 for one with no terms); None when the
    organisation has no counts or at most ``most`` live pieces. One statement."""
    groups, lows, highs = [], [], []
    for index, terms in enumerate(alternatives):
        for term in terms:
            word = term.removesuffix(":*").lower()
            groups.append(index)
            lows.append(word)
            highs.append(word + (_AFTER_PREFIX if term.endswith(":*") else _AFTER_WORD))
    if not groups:
        return [0.0] * len(alternatives)
    params = {"c": customer_id, "f": field, "groups": groups, "lows": lows, "highs": highs}
    found = {int(row[0]): (int(row[1]), row[2]) for row in conn.execute(_SHARES, params)}
    pieces = next(iter(found.values()))[1]
    if pieces is None or pieces <= most:
        return None
    return [found[index][0] / pieces if index in found else 0.0 for index in range(len(alternatives))]
