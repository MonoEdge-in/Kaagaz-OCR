"""Story of this PDF: the ids of stories that need a meaning vector (council, 3 Oct 2026).

A story is embedded when it is saved (S2's PUT). When the model was off or
down then, the worker embeds it later, but the worker cannot see a story: the
RESTRICTIVE ``own_user_only`` policy (0009) hides every row from any
transaction that is not its author's. So this adds one narrow owner-rights
function, like 0002's and 0006's: ``kaagaz_stories_to_embed`` gives back the
ids only (customer, user, family) of at most ``p_limit`` stories, at most 100,
with no vector (``p_tag`` NULL, the worker) or with no vector or another tag
(``p_tag`` set, reembed), of every customer or of ``p_customer``. They come in
key order, after the cursor ``p_after_*`` (NULL: from the start), so a story
whose vector is refused never blocks the ones behind it. Never the text: the
worker then reads each story as its own user (``Database.user``) and writes the
vector there.

It runs as the table owner (0009 has no FORCE ROW LEVEL SECURITY), with an
empty search path and every name and operator schema-qualified. Only
``kaagaz_app`` (the role the API, the worker and the CLI take) may run it, never
PUBLIC. The downgrade drops it.

PROVISIONAL (Vrushit): the number (My words A1 and PR 2b's sound key both take
0008 on their stacks; the second to merge renumbers, and 0009 and this one move
with it).

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
LIMIT_MOST = 100
SIGNATURE = "kaagaz_stories_to_embed(text, uuid, integer, uuid, uuid, uuid)"
COMMENT = (
    "Story of this PDF: (customer, user, family) of at most p_limit stories (at most 100) with no"
    " vector, or with another tag than p_tag, after the key p_after_customer, p_after_user,"
    " p_after_family, in key order. Ids only, never the text. For the worker's sweep and reembed."
)

UPGRADE = [
    f"""
    CREATE FUNCTION kaagaz_stories_to_embed(
        p_tag text, p_customer uuid, p_limit integer,
        p_after_customer uuid, p_after_user uuid, p_after_family uuid
    )
    RETURNS TABLE (customer_id uuid, user_id uuid, family_id uuid)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS
    $$
        SELECT s.customer_id, s.user_id, s.family_id
        FROM public.document_stories s
        WHERE (s.embedding IS NULL OR (p_tag IS NOT NULL AND s.embedding_model OPERATOR(pg_catalog.<>) p_tag))
          AND (p_customer IS NULL OR s.customer_id OPERATOR(pg_catalog.=) p_customer)
          AND (p_after_customer IS NULL
               OR ROW(s.customer_id, s.user_id, s.family_id)
                  OPERATOR(pg_catalog.>) ROW(p_after_customer, p_after_user, p_after_family))
        ORDER BY s.customer_id, s.user_id, s.family_id
        LIMIT LEAST(GREATEST(p_limit, 0), {LIMIT_MOST})
    $$
    """,
    f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO {APP_ROLE}",
    f"COMMENT ON FUNCTION {SIGNATURE} IS '" + COMMENT.replace("'", "''") + "'",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {SIGNATURE}")
