"""How rare each word is in one organisation (#617 part 1, migration 0094's ``term_df`` and
``customer_stats``).

**Kept by the worker, never per upload.** ``due`` says an organisation's counts need a rebuild
when there are none yet, its live pieces (current, ready, out of Trash) have changed by
``CHANGE_SHARE`` or more since they were counted, or they are a day old (``REBUILD_EVERY``) and
the live pieces are not as many as counted; the worker checks on its hourly turn. A rebuild is
one transaction: the old rows deleted, then ``ts_stat`` over each word column (``FIELDS``) of the
live pieces, and how many there are. An organisation with no more live pieces than a word query
can give candidates (``most``, ``hybrid.CANDIDATE_CAP``) gets its piece count only: nothing is
common there. A search meanwhile reads the old counts.

**Read by the word search** (kaagaz/search/hybrid.py): ``shares`` gives the share of the live
pieces that hold each of a query's alternatives (each one or more ``to_tsquery`` terms, ORed).
One on more than ``COMMON_SHARE`` only scores; it never brings candidates. A prefix term
(``jamin:*``) counts every word it starts, summed (pieces holding two of them count twice, so
it can only look more common than it is). No counts yet, or no more live pieces than ``most``:
no shares, so nothing is common, as the cap already bounds the work.

Cost: the check is one count over the organisation's pieces index; a rebuild reads each live
piece's two columns once (O(P) for P pieces) and writes one row a distinct word; ``shares`` is
one indexed range read a term.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import Connection, text

FIELDS = ("tsv_simple", "tsv_sound")  # the columns whose ORed queries are judged (hybrid._word)
COMMON_SHARE = 0.2  # #617: a word on more than this share of the live pieces only scores
CHANGE_SHARE = 0.05  # #617: rebuilt when the live pieces change by this share or more
REBUILD_EVERY = timedelta(hours=24)  # and at least nightly, unless the count is the same
# Each statement of one organisation's rebuild, at most (its transaction only): the worker's own
# statement timeout may be shorter than a big organisation's ts_stat.
REBUILD_TIMEOUT_MS = 300_000

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
    "INSERT INTO customer_stats (customer_id, field, n, built_at)"
    " SELECT :c, f.field, :n, :now FROM unnest(CAST(:fields AS text[])) AS f (field)"
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


def due(conn: Connection, customer_id: str, now: datetime) -> tuple[bool, datetime | None]:
    """Whether the organisation's counts need a rebuild (the module docstring), and when they
    were built (None: never). ``conn`` comes from ``Database.customer(customer_id)``."""
    built_at, counted = conn.execute(_STATS, {"c": customer_id}).one()
    if built_at is None:
        return True, None
    live = int(conn.execute(_LIVE, {"c": customer_id}).scalar_one())
    changed = abs(live - int(counted)) >= CHANGE_SHARE * max(int(counted), 1)
    return changed or (built_at <= now - REBUILD_EVERY and live != int(counted)), built_at


def rebuild(conn: Connection, customer_id: str, now: datetime, most: int) -> None:
    """The organisation's counts written again from its live pieces; with at most ``most`` live
    pieces, their count only (the module docstring)."""
    conn.execute(_SET_TIMEOUT, {"ms": str(REBUILD_TIMEOUT_MS)})
    live = int(conn.execute(_LIVE, {"c": customer_id}).scalar_one())
    conn.execute(_CLEAR_TERMS, {"c": customer_id})
    conn.execute(_CLEAR_STATS, {"c": customer_id})
    if live > most:
        for field in FIELDS:
            conn.execute(_COUNT_TERMS, {"c": customer_id, "f": field})
    conn.execute(_COUNT_PIECES, {"c": customer_id, "n": live, "now": now, "fields": list(FIELDS)})


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
