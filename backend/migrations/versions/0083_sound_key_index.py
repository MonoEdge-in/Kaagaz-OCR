"""A GIN index on ``pieces.tsv_sound`` (Tushar, 19 Nov 2026, after the RDS review). NEEDS
VRUSHIT'S APPROVAL: a schema change.

The word statement (``_WORD``, kaagaz/search/hybrid.py) ORs four ``@@`` tests, and
``tsv_sound`` was the one column of the four without an index (0011 left it out), so a plan that
may use GIN indexes (the owner's, which row level security does not apply to) could not combine
them and read every piece instead. 0011's reason may still hold for the app: under row level
security PostgreSQL uses no GIN index for ``@@`` (it is not LEAKPROOF), so ``kaagaz_app``'s
plans may not use this one either. EXPLAIN as ``kaagaz_app`` after this runs says whether it
pays for its writes; if not, a later migration drops it.

Built CONCURRENTLY, in an autocommit block, as 0020's ``audit_log_feed``: uploads and searches
go on while it builds. A failed build leaves an invalid index of that name, dropped and built
again on the next run. The downgrade drops it.

Revision ID: 0083
Revises: 0082
"""

from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None

LOCK_TIMEOUT = "10s"  # as env.py's
INDEX = "pieces_tsv_sound"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{INDEX}")  # a failed build's leftover
        op.execute(f"CREATE INDEX CONCURRENTLY {INDEX} ON public.pieces USING gin (tsv_sound)")
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")  # the autocommit block ended env.py's
    op.execute(f"COMMENT ON INDEX {INDEX} IS 'The sound keys of the word search (0011''s tsv_sound).'")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS public.{INDEX}")
