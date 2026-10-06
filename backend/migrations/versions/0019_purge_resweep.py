"""The second sweep of a deleted organisation's files, a day after its purge (super admin
PR 4b, council 6 Oct 2026, PROVISIONAL; change plan
Outside-Folder/docs/plans/2026-10-06-super-admin.md, awaiting Vrushit's approval).

A job that was already running when the organisation was disabled may still write a
page image after the purge emptied its storage prefixes. So, 24 hours after each
``org.purged`` row (0018), the worker empties both prefixes once more and writes
``org.swept``. Two narrow owner-rights functions, ``kaagaz_app`` ONLY (as 0018's), that
read and write ``platform_audit`` for the worker, which has no right on it:

* ``kaagaz_purged_ids_to_sweep(now)``: ids only, of customers purged at least 24 hours
  before ``now``, not swept yet and with no customers row (review: a live organisation is
  never swept, whatever an audit row says).
* ``kaagaz_customer_swept(customer)``: one ``org.swept`` row (no operator), only for a
  customer that was purged, is not swept yet and has no customers row. False: none.

Also (moved from 0018 for CI's size limit): the story vector sweep
(``kaagaz_stories_to_embed``, 0010) skips customers that are not active (PR 1's review),
and ``platform_audit.operator_id``'s comment says NULL is also the worker.

The downgrade drops the two functions (the ``org.swept`` rows stay, as a record) and puts
0010's sweep and 0015's comment back.

Revision ID: 0019
Revises: 0018 (operator purge)
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
# A purged customer not swept yet: its org.purged row and no org.swept row.
_NOT_SWEPT = """
        FROM public.platform_audit p
        WHERE p.action = 'org.purged' AND NOT EXISTS (
            SELECT 1 FROM public.platform_audit s
            WHERE s.action = 'org.swept' AND s.subject_customer_id = p.subject_customer_id)
"""

FUNCTIONS = {
    # Ids only: purged at least 24 hours (PROVISIONAL) before p_now, not swept yet.
    "kaagaz_purged_ids_to_sweep(timestamptz)": f"""
    CREATE FUNCTION public.kaagaz_purged_ids_to_sweep(p_now timestamptz) RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT DISTINCT p.subject_customer_id {_NOT_SWEPT}
          AND p.created_at <= p_now - interval '24 hours'
          AND NOT EXISTS (SELECT 1 FROM public.customers c WHERE c.id = p.subject_customer_id)
    $$
    """,
    # The record that both prefixes were emptied once more. False: not purged, swept already,
    # or the customer still exists.
    "kaagaz_customer_swept(uuid)": f"""
    CREATE FUNCTION public.kaagaz_customer_swept(p_customer uuid) RETURNS boolean
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
        WITH swept AS (
            INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id)
            SELECT DISTINCT NULL::uuid, 'org.swept', p.subject_customer_id, 'customer', p.subject_customer_id::text
            {_NOT_SWEPT}
              AND p.subject_customer_id = p_customer
              AND NOT EXISTS (SELECT 1 FROM public.customers c WHERE c.id = p_customer)
            RETURNING 1
        )
        SELECT EXISTS (SELECT 1 FROM swept)
    $$
    """,
}

# 0010's story sweep; {active} is where 0019 adds the status check.
STORIES = "kaagaz_stories_to_embed(text, uuid, integer, uuid, uuid, uuid)"
_STORIES = """
    CREATE OR REPLACE FUNCTION kaagaz_stories_to_embed(
        p_tag text, p_customer uuid, p_limit integer,
        p_after_customer uuid, p_after_user uuid, p_after_family uuid
    )
    RETURNS TABLE (customer_id uuid, user_id uuid, family_id uuid)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS
    $$
        SELECT s.customer_id, s.user_id, s.family_id
        FROM public.document_stories s
        WHERE (s.embedding IS NULL OR (p_tag IS NOT NULL AND s.embedding_model OPERATOR(pg_catalog.<>) p_tag))
          AND (p_customer IS NULL OR s.customer_id OPERATOR(pg_catalog.=) p_customer){active}
          AND (p_after_customer IS NULL
               OR ROW(s.customer_id, s.user_id, s.family_id)
                  OPERATOR(pg_catalog.>) ROW(p_after_customer, p_after_user, p_after_family))
        ORDER BY s.customer_id, s.user_id, s.family_id
        LIMIT LEAST(GREATEST(p_limit, 0), 100)
    $$
"""
_ACTIVE = """
          AND EXISTS (SELECT 1 FROM public.customers c WHERE c.id OPERATOR(pg_catalog.=) s.customer_id
                      AND c.status OPERATOR(pg_catalog.=) 'active')"""
_STORIES_COMMENT = (
    "Story of this PDF: (customer, user, family) of at most p_limit stories (at most 100) with no"
    " vector, or with another tag than p_tag, after the key p_after_customer, p_after_user,"
    " p_after_family, in key order. Ids only, never the text. For the worker's sweep and reembed."
)
_OPERATOR_ID = "operators.id of who acted. NULL = the command line{worker} or a failed sign-in."


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


UPGRADE = [
    *[
        statement
        for signature, create in FUNCTIONS.items()
        for statement in (
            create,
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{signature} TO {APP_ROLE}",
        )
    ],
    _STORIES.format(active=_ACTIVE),
    f"COMMENT ON FUNCTION {STORIES} IS "
    + _literal(_STORIES_COMMENT + " Customers that are not active are left out (0019)."),
    "COMMENT ON COLUMN platform_audit.operator_id IS "
    + _literal(_OPERATOR_ID.format(worker=", the worker's purge and sweep")),
]

DOWNGRADE = [
    *[f"DROP FUNCTION IF EXISTS public.{signature}" for signature in FUNCTIONS],
    _STORIES.format(active=""),
    f"COMMENT ON FUNCTION {STORIES} IS {_literal(_STORIES_COMMENT)}",
    f"COMMENT ON COLUMN platform_audit.operator_id IS {_literal(_OPERATOR_ID.format(worker=''))}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
