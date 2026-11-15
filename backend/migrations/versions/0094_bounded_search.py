"""Bounded word search (#617 part 1, 15 Nov 2026; NEEDS VRUSHIT'S SECURITY SIGN-OFF).

Production searches as ``kaagaz_app`` under row level security, and ``@@`` is not LEAKPROOF,
so PostgreSQL never runs it inside a GIN index before the policy: the word search read every
piece of the organisation (0011's note; #631's measurements), and ``tsv_sound`` had no index.

1. ``pieces_tsv_sound``: a GIN index on ``tsv_sound`` (0011's column). Built CONCURRENTLY in an
   autocommit block (0068's pattern), after dropping a failed build's leftover, so writes to
   pieces go on while it builds.
2. ``kaagaz_word_candidates(column, query, documents, cap)``: one narrow owner-rights function
   (0010's pattern), the only way past the policies here. It gives back piece ids only, never
   text: the current customer's (``kaagaz_current_customer()``, the transaction's own setting,
   never a parameter) pieces of current, ready documents out of Trash (``LIVE_CURRENT``),
   inside ``documents`` when given, whose ``tsv``, ``tsv_simple`` or ``tsv_sound`` matches
   ``query``, at most ``cap`` and never more than ``CAP_MOST`` (2,000). As the owner it uses
   the GIN indexes; the search then reads the ids' pieces under row level security, with its
   own customer filter and scope, so the function only bounds the work. Custom plans
   (``plan_cache_mode``), so each call is planned for its own query and documents. ``ROWS 100``:
   the planner's guess of a call's rows (its default is 1,000), so the search reads the ids'
   pieces by their key, never by a scan of every piece of the organisation (measured: a query
   with no match chose that scan at the default). An empty search path; only ``kaagaz_app``
   may run it, never PUBLIC.
3. ``term_df`` and ``customer_stats``: how many of an organisation's live pieces hold each word
   of each word column, and how many live pieces it has and their average length, rebuilt by
   the worker with ``ts_stat`` (kaagaz/search/word_counts.py), never counted per upload.
   ``customer_id`` in every key, a foreign key to the customer (its delete removes them), the
   ``customer_only`` policy (USING and WITH CHECK), and the least grants the rebuild needs:
   SELECT, INSERT and DELETE, never UPDATE. ``term`` sorts byte by byte (COLLATE "C"), so a
   prefix is one range of the key.

The downgrade drops the function, the two tables (derived: the worker writes them again) and
the index; no other data is touched.

PROVISIONAL (Vrushit): the number. PRD 7's 0083 to 0093 are open on their branches; this takes
0094 after this base's 0082 and is renumbered to follow whichever merges first.

Revision ID: 0094
Revises: 0082 (a trashed folder's parent)
"""

from alembic import op

revision = "0094"
down_revision = "0082"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
LOCK_TIMEOUT = "10s"  # as env.py's
INDEX = "pieces_tsv_sound"
CAP_MOST = 2000  # #617: a word search's candidates per column, at most
FIELDS = ("tsv", "tsv_simple", "tsv_sound")
SIGNATURE = "kaagaz_word_candidates(text, tsquery, uuid[], integer)"


def _literal(comment: str) -> str:
    """A SQL string literal; each colon escaped, so text() never reads one as a bind parameter."""
    return "'" + comment.replace("'", "''").replace(":", "\\:") + "'"


_FIELD_CHECK = "CHECK (field IN (" + ", ".join(f"'{field}'" for field in FIELDS) + "))"

# One branch per column, so each is a plain indexed match; the custom plan keeps only the
# branch p_column names (a false constant), and LIMIT stops the heap reads at the cap.
CANDIDATES = f"""
    CREATE FUNCTION public.kaagaz_word_candidates(
        p_column text, p_query tsquery, p_documents uuid[], p_cap integer
    )
    RETURNS SETOF uuid
    LANGUAGE plpgsql STABLE SECURITY DEFINER ROWS 100
    SET search_path = '' SET plan_cache_mode = 'force_custom_plan' AS $$
    BEGIN
        IF p_column IS NULL OR p_column NOT IN ('tsv', 'tsv_simple', 'tsv_sound') THEN
            RAISE EXCEPTION 'unknown word column' USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY
        SELECT w.id FROM (
            SELECT p.id, p.customer_id, p.document_id FROM public.pieces p
            WHERE p_column = 'tsv' AND p.tsv @@ p_query
            UNION ALL
            SELECT p.id, p.customer_id, p.document_id FROM public.pieces p
            WHERE p_column = 'tsv_simple' AND p.tsv_simple @@ p_query
            UNION ALL
            SELECT p.id, p.customer_id, p.document_id FROM public.pieces p
            WHERE p_column = 'tsv_sound' AND p.tsv_sound @@ p_query
        ) AS w
        JOIN public.documents d ON d.customer_id = w.customer_id AND d.id = w.document_id
        WHERE w.customer_id = public.kaagaz_current_customer()
          AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
          AND (p_documents IS NULL OR w.document_id = ANY (p_documents))
        LIMIT least(greatest(p_cap, 0), {CAP_MOST});
    END
    $$
    """
FUNCTION_COMMENT = (
    "Word search (#617): ids of at most p_cap (at most 2,000) of the current customer's pieces of"
    " current, ready documents out of Trash, within p_documents when given, whose tsv, tsv_simple or"
    " tsv_sound (p_column) matches p_query. Ids only, never text. As the owner it uses the GIN"
    " indexes, which row level security cannot; the search reads the pieces under it."
)

TABLE_COMMENTS = {
    "term_df": "How many of an organisation's live pieces (current, ready, out of Trash) hold each word"
    " of each word column, from ts_stat. Rebuilt by the worker (nightly, or when the live pieces"
    " change by 5% or more); the search reads it to skip common words.",
    "customer_stats": "An organisation's live pieces per word column: how many, and their average"
    " length in words, with when they were counted. Rebuilt with term_df.",
}
COLUMN_COMMENTS = {
    "term_df": {
        "customer_id": "→ customers.id, the organisation the count belongs to (row level security filters"
        " on it). Deleted with it.",
        "field": "The pieces column counted: tsv (English stems), tsv_simple (words as written and their"
        " Latin spelling) or tsv_sound (sound keys).",
        "term": "One word of that column as stored (a lexeme), compared byte by byte.",
        "df": "How many live pieces hold it, at least 1.",
    },
    "customer_stats": {
        "customer_id": "→ customers.id, the organisation (row level security filters on it). Deleted with"
        " it.",
        "field": "The pieces column counted: tsv, tsv_simple or tsv_sound.",
        "n": "How many live pieces the organisation has.",
        "avgdl": "Their average length: distinct words of the column per piece.",
        "built_at": "When the worker counted them.",
    },
}

UPGRADE = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",  # the autocommit block ended env.py's
    f"""
    CREATE TABLE term_df (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        field text NOT NULL {_FIELD_CHECK},
        term text COLLATE "C" NOT NULL CHECK (length(term) >= 1),
        df integer NOT NULL CHECK (df >= 1),
        PRIMARY KEY (customer_id, field, term)
    )
    """,
    f"""
    CREATE TABLE customer_stats (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        field text NOT NULL {_FIELD_CHECK},
        n integer NOT NULL CHECK (n >= 0),
        avgdl double precision NOT NULL CHECK (avgdl >= 0),
        built_at timestamptz NOT NULL,
        PRIMARY KEY (customer_id, field)
    )
    """,
    *[
        statement
        for table in ("term_df", "customer_stats")
        for statement in (
            f"GRANT SELECT, INSERT, DELETE ON {table} TO {APP_ROLE}",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"""
    CREATE POLICY customer_only ON {table}
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
            f"COMMENT ON TABLE {table} IS {_literal(TABLE_COMMENTS[table])}",
            *[
                f"COMMENT ON COLUMN {table}.{column} IS {_literal(comment)}"
                for column, comment in COLUMN_COMMENTS[table].items()
            ],
        )
    ],
    f"COMMENT ON INDEX {INDEX} IS"
    " 'The sound-key word search (#617), through kaagaz_word_candidates: a GIN index is not used under"
    " row level security.'",
    CANDIDATES,
    f"COMMENT ON FUNCTION public.{SIGNATURE} IS {_literal(FUNCTION_COMMENT)}",
    f"REVOKE ALL ON FUNCTION public.{SIGNATURE} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION public.{SIGNATURE} TO {APP_ROLE}",
]

DOWNGRADE = [
    f"DROP FUNCTION IF EXISTS public.{SIGNATURE}",
    "DROP TABLE IF EXISTS customer_stats",
    "DROP TABLE IF EXISTS term_df",
    f"DROP INDEX IF EXISTS public.{INDEX}",
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{INDEX}")  # a failed build's leftover
        op.execute(f"CREATE INDEX CONCURRENTLY {INDEX} ON public.pieces USING gin (tsv_sound)")
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
