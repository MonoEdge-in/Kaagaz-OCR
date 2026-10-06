"""Deleting an organisation after a grace period (super admin PR 4, council 6 Oct 2026,
PROVISIONAL; change plan Outside-Folder/docs/plans/2026-10-06-super-admin.md, awaiting
Vrushit's approval; the plan's 0017, one later because PR 3b took 0017).

The timeline: an operator disables the organisation (0016), then schedules its deletion
(typed name + a fresh two-step code): status ``deleting``, ``delete_after`` = now + 7 days
(PROVISIONAL). Until then the operator may cancel (back to ``disabled``). After it, the
worker removes it (``kaagaz.jobs.purge.purge_deleted_customers``): every document family
inside ``Database.customer`` by today's delete forever (rows + files, row level security
applies), then both storage prefixes, then the customers row through
``kaagaz_finish_customer_delete``.

1. ``kaagaz_op_schedule_delete`` and ``kaagaz_op_cancel_delete``: made as 0016's
   (SECURITY DEFINER, empty search path, ``kaagaz_operator`` ONLY), each a step-up: the
   fresh code's step is used up first, in the same transaction. The customer row is
   locked FOR UPDATE, so a running admin change (0017, FOR SHARE) is waited for. The
   times come from the DATABASE clock (``now()``), not from the caller: no clock of the
   API can shorten the grace period of a delete that cannot be undone.
2. Two narrow owner-rights functions for the worker, runnable by ``kaagaz_app`` ONLY
   (as 0002's ``kaagaz_customer_ids_with_trash``), named outside ``kaagaz_op_*`` so the
   app still runs no operator function:
   * ``kaagaz_customer_ids_to_purge(now)``: ids only, of customers being deleted whose
     ``delete_after`` has passed. Nothing else about any customer.
   * ``kaagaz_finish_customer_delete(customer)``: deletes ONE customers row, and only
     when it is being deleted, its ``delete_after`` has passed on the database clock and
     it has no document left (the worker deleted them under row level security first),
     so it can only do what the purge was going to do. The tables whose rows point at
     ``users`` with NO ACTION are emptied first, in an order that never trips those
     keys; then ``users``; then the customers row, whose cascade takes the rest
     (``audit_log``, ``processing_runs``, ...). Writes ``org.purged`` in
     ``platform_audit`` (``subject_customer_id`` has no foreign key: it stays).
3. The story vector sweep (``kaagaz_stories_to_embed``, 0010) skips customers that are
   not active (PR 1's review): their stories no longer take the sweep's slots.

The downgrade drops the four functions and puts 0010's sweep and 0015's comment back.
Data a purge removed cannot come back: that is what the grace period is for.

Revision ID: 0018
Revises: 0017 (operator admins)
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
OPERATOR_ROLE = "kaagaz_operator"
# PROVISIONAL (plan question 4): how long a scheduled deletion can still be cancelled.
GRACE = "7 days"
# As 0016's (a migration can't import another): the operator check, the step-up, the rows.
_ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"
_WRITE = "OUT outcome text, OUT result_id uuid"
_HEAD = "LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$"
_STEP_UP = f"""
        UPDATE public.operators AS o
        SET totp_last_step = p_step, step_up_failures = 0, step_up_locked_until = NULL
        WHERE {_ENABLED} AND p_step IS NOT NULL
          AND (o.totp_last_step IS NULL OR o.totp_last_step < p_step);
        IF NOT FOUND THEN
            outcome := 'step_up';
            RETURN;
        END IF;
"""
# The customer row, locked FOR UPDATE: an admin change (0017, FOR SHARE) finishes first,
# or sees the new status.
_LOCK = """
        SELECT c.name, c.status, c.delete_after INTO v_name, v_status, v_delete_after
        FROM public.customers c WHERE c.id = p_customer FOR UPDATE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        result_id := p_customer;
"""
_PLATFORM = (
    "INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id)"
)
_CUSTOMER_LOG = (
    "INSERT INTO public.audit_log (customer_id, actor_user_id, action, target_type, target_id, details)"
)
_DECLARE = "DECLARE v_name text; v_status text; v_delete_after timestamptz;"

OPERATOR_FUNCTIONS = {
    # Schedule the deletion of one DISABLED customer whose name was typed exactly
    # (step-up). Outcomes: not_disabled (active), deleting (already), name_mismatch (one
    # refusal row), done (status deleting, delete_after = now + GRACE).
    "kaagaz_op_schedule_delete(uuid, uuid, bigint, text)": f"""
    CREATE FUNCTION public.kaagaz_op_schedule_delete(
        p_operator uuid, p_customer uuid, p_step bigint, p_confirm_name text, {_WRITE}
    )
    {_HEAD}
    {_DECLARE}
    BEGIN
        {_STEP_UP}
        {_LOCK}
        IF v_status <> 'disabled' THEN
            outcome := CASE WHEN v_status = 'deleting' THEN 'deleting' ELSE 'not_disabled' END;
            RETURN;
        END IF;
        IF p_confirm_name IS DISTINCT FROM v_name THEN
            {_PLATFORM} VALUES (p_operator, 'customer.delete_refused', p_customer, 'customer', p_customer::text);
            outcome := 'name_mismatch';
            RETURN;
        END IF;
        UPDATE public.customers AS c SET status = 'deleting', delete_after = now() + interval '{GRACE}'
        WHERE c.id = p_customer;
        {_CUSTOMER_LOG}
        VALUES (p_customer, NULL, 'customer.delete_scheduled', 'customer', p_customer::text,
                jsonb_build_object('by', 'operator'));
        {_PLATFORM} VALUES (p_operator, 'customer.delete_scheduled', p_customer, 'customer', p_customer::text);
        outcome := 'done';
    END
    $$
    """,
    # Cancel a scheduled deletion while its grace period runs (step-up): disabled again.
    # Outcomes: not_deleting, purging (the grace period is over: the purge may have
    # begun, so nothing is given back half), done.
    "kaagaz_op_cancel_delete(uuid, uuid, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_cancel_delete(p_operator uuid, p_customer uuid, p_step bigint, {_WRITE})
    {_HEAD}
    {_DECLARE}
    BEGIN
        {_STEP_UP}
        {_LOCK}
        IF v_status <> 'deleting' OR v_delete_after <= now() THEN
            outcome := CASE WHEN v_status <> 'deleting' THEN 'not_deleting' ELSE 'purging' END;
            RETURN;
        END IF;
        UPDATE public.customers AS c SET status = 'disabled', delete_after = NULL WHERE c.id = p_customer;
        {_CUSTOMER_LOG}
        VALUES (p_customer, NULL, 'customer.delete_cancelled', 'customer', p_customer::text,
                jsonb_build_object('by', 'operator'));
        {_PLATFORM} VALUES (p_operator, 'customer.delete_cancelled', p_customer, 'customer', p_customer::text);
        outcome := 'done';
    END
    $$
    """,
}

APP_FUNCTIONS = {
    # Ids only: the customers being deleted whose grace period ended before p_now.
    "kaagaz_customer_ids_to_purge(timestamptz)": """
    CREATE FUNCTION public.kaagaz_customer_ids_to_purge(p_now timestamptz) RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT c.id FROM public.customers c
        WHERE c.status = 'deleting' AND c.delete_after <= p_now
        ORDER BY c.delete_after, c.id
    $$
    """,
    # The last step of a purge: ONE customers row that is being deleted, past its
    # delete_after on the database clock and with no document left. False: nothing done.
    "kaagaz_finish_customer_delete(uuid)": """
    CREATE FUNCTION public.kaagaz_finish_customer_delete(p_customer uuid) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    BEGIN
        PERFORM 1 FROM public.customers c
        WHERE c.id = p_customer AND c.status = 'deleting' AND c.delete_after <= now() FOR UPDATE;
        IF NOT FOUND OR EXISTS (SELECT 1 FROM public.documents d WHERE d.customer_id = p_customer) THEN
            RETURN false;
        END IF;
        -- Rows pointing at users with NO ACTION go first (documents, and with them
        -- corrections, are gone already); conversations take messages and feedback.
        DELETE FROM public.conversations x WHERE x.customer_id = p_customer;
        DELETE FROM public.learned_corrections x WHERE x.customer_id = p_customer;
        DELETE FROM public.folders x WHERE x.customer_id = p_customer;
        DELETE FROM public.tags x WHERE x.customer_id = p_customer;
        -- users take their words, word spots and stories; the customer takes the rest.
        DELETE FROM public.users x WHERE x.customer_id = p_customer;
        DELETE FROM public.customers c WHERE c.id = p_customer;
        INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id)
        VALUES (NULL, 'org.purged', p_customer, 'customer', p_customer::text);
        RETURN true;
    END
    $$
    """,
}

# 0010's story sweep; {active} is where 0018 adds the status check.
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
_OPERATOR_ID_COMMENT = "operators.id of who acted. NULL = the command line or a failed sign-in."


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _granted(functions: dict[str, str], role: str) -> list[str]:
    return [
        statement
        for signature, create in functions.items()
        for statement in (
            create,
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{signature} TO {role}",
        )
    ]


UPGRADE = [
    *_granted(OPERATOR_FUNCTIONS, OPERATOR_ROLE),
    *_granted(APP_FUNCTIONS, APP_ROLE),
    _STORIES.format(active=_ACTIVE),
    f"COMMENT ON FUNCTION {STORIES} IS "
    + _literal(_STORIES_COMMENT + " Customers that are not active are left out (0018)."),
    "COMMENT ON COLUMN platform_audit.operator_id IS "
    + _literal("operators.id of who acted. NULL = the command line, the worker's purge or a failed sign-in."),
]

DOWNGRADE = [
    *[f"DROP FUNCTION IF EXISTS public.{signature}" for signature in {**OPERATOR_FUNCTIONS, **APP_FUNCTIONS}],
    _STORIES.format(active=""),
    f"COMMENT ON FUNCTION {STORIES} IS {_literal(_STORIES_COMMENT)}",
    f"COMMENT ON COLUMN platform_audit.operator_id IS {_literal(_OPERATOR_ID_COMMENT)}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
