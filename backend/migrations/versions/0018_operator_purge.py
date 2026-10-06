"""Deleting an organisation after a grace period (super admin PR 4, council 6 Oct 2026,
PROVISIONAL; change plan Outside-Folder/docs/plans/2026-10-06-super-admin.md, awaiting
Vrushit's approval; the plan's 0017, one later because PR 3b took 0017).

1. ``kaagaz_op_schedule_delete`` (a DISABLED customer, its exact name) and
   ``kaagaz_op_cancel_delete`` (in the grace period): as 0016's (SECURITY DEFINER, empty
   search path, ``kaagaz_operator`` ONLY), step-ups, the row locked FOR UPDATE (0017's
   admin changes hold FOR SHARE). ``delete_after`` = now + 7 days (PROVISIONAL) on the
   DATABASE clock, so no API clock can shorten it.
2. For the worker, ``kaagaz_app`` ONLY (as 0002's ``kaagaz_customer_ids_with_trash``;
   not ``kaagaz_op_*``, which the app never runs): ``kaagaz_customer_ids_to_purge`` gives
   ids only, of customers due on the caller's AND the database's clock (review);
   ``kaagaz_finish_customer_delete`` deletes ONE customers row that is deleting, due on
   the database clock and has no document left, so it only does what the purge would.
   Rows pointing at ``users`` with NO ACTION go first, then ``users``, then the customer
   (its cascade takes the rest); ``org.purged`` (``subject_customer_id`` has no FK) stays.

The downgrade drops the four functions (0019, PR 4b, makes the story sweep skip customers
that are not active). What a purge removed cannot come back: that is what the grace period is for.

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
    # Ids only: the customers being deleted whose grace period ended (both clocks).
    "kaagaz_customer_ids_to_purge(timestamptz)": """
    CREATE FUNCTION public.kaagaz_customer_ids_to_purge(p_now timestamptz) RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT c.id FROM public.customers c
        WHERE c.status = 'deleting' AND c.delete_after <= least(p_now, now())
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
]

DOWNGRADE = [
    *[f"DROP FUNCTION IF EXISTS public.{signature}" for signature in {**OPERATOR_FUNCTIONS, **APP_FUNCTIONS}],
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
