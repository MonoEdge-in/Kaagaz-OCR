"""The second sweep of a deleted organisation's files, a day after its purge (super admin
PR 4b, council 6 Oct 2026, PROVISIONAL; change plan
Outside-Folder/docs/plans/2026-10-06-super-admin.md, awaiting Vrushit's approval).

A job that was already running when the organisation was disabled may still write a
page image after the purge emptied its storage prefixes. So, 24 hours after each
``org.purged`` row (0018), the worker empties both prefixes once more and writes
``org.swept``. Two narrow owner-rights functions, ``kaagaz_app`` ONLY (as 0018's), that
read and write ``platform_audit`` for the worker, which has no right on it:

* ``kaagaz_purged_ids_to_sweep(now)``: ids only, of customers purged at least 24 hours
  before ``now`` and not swept yet. They are gone: nothing else about them is left.
* ``kaagaz_customer_swept(customer)``: one ``org.swept`` row (no operator), only for a
  customer that was purged, is not swept yet and has no customers row. False: none.

The downgrade drops them; the ``org.swept`` rows stay (they are only a record).

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

UPGRADE = [
    statement
    for signature, create in FUNCTIONS.items()
    for statement in (
        create,
        f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION public.{signature} TO {APP_ROLE}",
    )
]

DOWNGRADE = [f"DROP FUNCTION IF EXISTS public.{signature}" for signature in FUNCTIONS]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
