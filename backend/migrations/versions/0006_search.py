"""Search: half-precision vectors on a used index, words in three writing forms (PRD 3 V1, W1, W2).

Offline: stop the api and the worker first. The whole upgrade is one
transaction (migrations/env.py), in this order, because the old
``vector_cosine_ops`` index would refuse the new type:

1. lock pieces, then messages (0004's pattern; env.py's lock_timeout makes a
   busy table fail fast);
2. drop the old vector index;
3. ONE ``ALTER TABLE pieces``, so the table is rewritten once: the vectors cast
   to halfvec(1024) (V1: 2 KB a piece instead of 4 KB) and the generated
   ``tsv_simple`` column (W1);
4. the HNSW index on the new type, then the GIN indexes for W1 and W2;
5. the two new ``messages`` columns (PRD 3 H1, H2), used from PRD 3 part 3.2 on;
6. ``kaagaz_customer_ids()``, the customer list for ``cli reembed`` (V4, the next
   pull request): row level security hides every customer from
   ``Database.system``, so it returns ids only, like 0002's
   ``kaagaz_customer_ids_with_trash``.

PROVISIONAL (for Vrushit, PRD3-BUILD-PLAN section 7):
* item 3: the vectors are cast (the PRD's ``USING embedding::halfvec(1024)``),
  not cleared: this pull request ships without ``reembed``, so clearing would
  leave meaning search empty until the next one. reembed replaces them anyway.
* item 5: the ``messages.scope`` CHECK (an object of at most 4 KiB), not in the PRD.
* item 6: the downgrade refuses while any message has a rewritten question or
  a scope, which 0005 cannot hold (0004's pattern). It casts the vectors back.
* ``kaagaz_customer_ids()`` itself.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
LOCK = "LOCK TABLE pieces, messages IN ACCESS EXCLUSIVE MODE"

UPGRADE = [
    LOCK,
    "DROP INDEX pieces_embedding",
    """
    ALTER TABLE pieces
        ALTER COLUMN embedding TYPE halfvec(1024) USING embedding::halfvec(1024),
        ADD COLUMN tsv_simple tsvector GENERATED ALWAYS AS
            (to_tsvector('simple', text || ' ' || coalesce(text_latin, ''))) STORED
    """,
    # PROVISIONAL: memory for the one index build; SET LOCAL ends with the migration.
    "SET LOCAL maintenance_work_mem = '256MB'",
    """
    CREATE INDEX pieces_embedding ON pieces
        USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)
    """,
    "CREATE INDEX pieces_tsv_simple ON pieces USING gin (tsv_simple)",
    "CREATE INDEX pieces_latin_trgm ON pieces USING gin (text_latin gin_trgm_ops)",
    """
    ALTER TABLE messages
        ADD COLUMN rewritten_question text NULL CHECK (length(rewritten_question) <= 300),
        ADD COLUMN scope jsonb NULL
            CHECK (jsonb_typeof(scope) = 'object' AND octet_length(scope::text) <= 4096)
    """,
    """
    CREATE FUNCTION kaagaz_customer_ids()
    RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS
    $$ SELECT c.id FROM public.customers c $$
    """,
    "REVOKE ALL ON FUNCTION kaagaz_customer_ids() FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION kaagaz_customer_ids() TO {APP_ROLE}",
]

DOWNGRADE = [
    LOCK,
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM messages WHERE rewritten_question IS NOT NULL OR scope IS NOT NULL) THEN
            RAISE EXCEPTION 'messages have a rewritten question or a scope; clear them before downgrading'
                USING ERRCODE = 'check_violation';
        END IF;
    END
    $$
    """,
    "DROP FUNCTION IF EXISTS kaagaz_customer_ids()",
    "ALTER TABLE messages DROP COLUMN IF EXISTS scope, DROP COLUMN IF EXISTS rewritten_question",
    "DROP INDEX IF EXISTS pieces_latin_trgm",
    "DROP INDEX IF EXISTS pieces_tsv_simple",
    "DROP INDEX IF EXISTS pieces_embedding",
    """
    ALTER TABLE pieces
        DROP COLUMN IF EXISTS tsv_simple,
        ALTER COLUMN embedding TYPE vector(1024) USING embedding::vector(1024)
    """,
    "CREATE INDEX pieces_embedding ON pieces USING hnsw (embedding vector_cosine_ops)",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
