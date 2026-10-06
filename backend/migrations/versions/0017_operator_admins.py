"""What a platform operator may do to an organisation's admins (super admin PR 3b,
council 6 Oct 2026, PROVISIONAL; change plan Outside-Folder/docs/plans/2026-10-06-super-admin.md,
awaiting Vrushit's approval; split from 0016 for CI's 1,000-line limit, so the plan's
purge migration becomes 0018).

Functions only, no table or column, made as 0016's are: SECURITY DEFINER, empty search
path, schema-qualified names, revoked from PUBLIC, runnable by ``kaagaz_operator``
ONLY, and acting only for an enabled operator whose two-step secret is confirmed.

* The list returns ONE customer's admins (``role = 'admin'``): their name, e-mail,
  two-step on, lock, disabled, last sign-in. Members and reviewers are never listed
  (the organisation list counts them).
* Every write names its one customer AND its one admin: the UPDATE's WHERE holds
  ``customer_id``, ``id`` and ``role = 'admin'``, so another customer's admin or a
  member's id is ``missing`` and nothing changes. Each writes exactly one
  ``platform_audit`` row and one row in that customer's own ``audit_log``
  (``{"by": "operator"}``, the actions the admin screen writes), in the same transaction.
* As 0016, a write answers ``(outcome, result_id)``. Resetting an admin's two-step
  code is a step-up: it uses up the fresh code's step first.
* As the admin screen (``db.users``): disabling and the two-step reset raise the
  session version (every session ends); the reset clears the secret, so the password
  alone signs in again; unlocking clears the count and the lock. A new admin's
  password is temporary (``must_change_password``). No admin is deleted in v1.

The downgrade drops the functions; nothing else changes.

Revision ID: 0017
Revises: 0016 (operator organisations)
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

OPERATOR_ROLE = "kaagaz_operator"
# As 0016's (a migration can't import another): the operator check, the step-up, the rows.
_ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"
_WRITE = "OUT outcome text, OUT result_id uuid"
_HEAD = "LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$"
_ACTOR = f"""
        PERFORM 1 FROM public.operators o WHERE {_ENABLED};
        IF NOT FOUND THEN
            outcome := 'signed_out';
            RETURN;
        END IF;
"""
_STEP_UP = f"""
        UPDATE public.operators AS o SET totp_last_step = p_step, failed_logins = 0, locked_until = NULL
        WHERE {_ENABLED} AND p_step IS NOT NULL
          AND (o.totp_last_step IS NULL OR o.totp_last_step < p_step);
        IF NOT FOUND THEN
            outcome := 'step_up';
            RETURN;
        END IF;
"""
_PLATFORM = (
    "INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id)"
)
_CUSTOMER_LOG = (
    "INSERT INTO public.audit_log (customer_id, actor_user_id, action, target_type, target_id, details)"
)

FUNCTIONS = {
    # One customer's admins (role admin only). Empty: no such customer (each keeps an admin).
    "kaagaz_op_admins(uuid, uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_admins(p_operator uuid, p_customer uuid)
    RETURNS TABLE (user_id uuid, display_name text, email text, totp_enabled boolean,
                   locked_until timestamptz, disabled boolean, must_change_password boolean,
                   last_login_at timestamptz, created_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT u.id, u.display_name, u.email, u.totp_enabled_at IS NOT NULL, u.locked_until, u.disabled,
               u.must_change_password, u.last_login_at, u.created_at
        FROM public.users u
        WHERE u.customer_id = p_customer AND u.role = 'admin'
          AND EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        ORDER BY u.created_at, u.email
    $$
    """,
    # One more admin of one customer, with a temporary password. 23505: e-mail taken.
    "kaagaz_op_create_admin(uuid, uuid, text, text, text)": f"""
    CREATE FUNCTION public.kaagaz_op_create_admin(
        p_operator uuid, p_customer uuid, p_email text, p_name text, p_password_hash text, {_WRITE}
    )
    {_HEAD}
    DECLARE
        v_status text;
    BEGIN
        {_ACTOR}
        SELECT c.status INTO v_status FROM public.customers c WHERE c.id = p_customer;
        IF NOT FOUND OR v_status = 'deleting' THEN
            outcome := CASE WHEN v_status = 'deleting' THEN 'deleting' ELSE 'missing' END;
            RETURN;
        END IF;
        INSERT INTO public.users (customer_id, email, display_name, role, password_hash, must_change_password)
        VALUES (p_customer, lower(p_email), p_name, 'admin', p_password_hash, true)
        RETURNING id INTO result_id;
        {_CUSTOMER_LOG}
        VALUES (p_customer, NULL, 'user.create', 'user', result_id::text,
                jsonb_build_object('role', 'admin', 'by', 'operator'));
        {_PLATFORM} VALUES (p_operator, 'admin.create', p_customer, 'user', result_id::text);
        outcome := 'done';
    END
    $$
    """,
    # Disable, enable, unlock or reset the two-step code (step-up) of ONE admin of ONE
    # customer, as the admin screen does: disabling and the reset end every session.
    "kaagaz_op_change_admin(uuid, uuid, uuid, text, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_change_admin(
        p_operator uuid, p_customer uuid, p_user uuid, p_change text, p_step bigint, {_WRITE}
    )
    {_HEAD}
    BEGIN
        IF p_change NOT IN ('disable', 'enable', 'unlock', 'reset_totp') THEN
            RAISE EXCEPTION 'unknown change' USING ERRCODE = 'check_violation';
        END IF;
        IF p_change = 'reset_totp' THEN
            {_STEP_UP}
        ELSE
            {_ACTOR}
        END IF;
        UPDATE public.users AS u SET
            disabled = CASE p_change WHEN 'disable' THEN true WHEN 'enable' THEN false ELSE u.disabled END,
            totp_secret_enc = CASE WHEN p_change = 'reset_totp' THEN NULL ELSE u.totp_secret_enc END,
            totp_enabled_at = CASE WHEN p_change = 'reset_totp' THEN NULL ELSE u.totp_enabled_at END,
            totp_last_step = CASE WHEN p_change = 'reset_totp' THEN NULL ELSE u.totp_last_step END,
            failed_logins = CASE WHEN p_change = 'unlock' THEN 0 ELSE u.failed_logins END,
            locked_until = CASE WHEN p_change = 'unlock' THEN NULL ELSE u.locked_until END,
            session_version = u.session_version + CASE WHEN p_change IN ('disable', 'reset_totp') THEN 1 ELSE 0 END
        WHERE u.customer_id = p_customer AND u.id = p_user AND u.role = 'admin'
        RETURNING u.id INTO result_id;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        {_CUSTOMER_LOG}
        VALUES (p_customer, NULL,
                CASE p_change WHEN 'unlock' THEN 'user.unlock' WHEN 'reset_totp' THEN 'user.reset_totp'
                    ELSE 'user.update' END,
                'user', p_user::text,
                jsonb_build_object('by', 'operator') || CASE WHEN p_change IN ('disable', 'enable')
                    THEN jsonb_build_object('role', 'admin', 'disabled', p_change = 'disable',
                                            'password_changed', false)
                    ELSE jsonb_build_object() END);
        {_PLATFORM} VALUES (p_operator, 'admin.' || p_change, p_customer, 'user', p_user::text);
        outcome := 'done';
    END
    $$
    """,
}

UPGRADE = [
    statement
    for signature, create in FUNCTIONS.items()
    for statement in (
        create,
        f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION public.{signature} TO {OPERATOR_ROLE}",
    )
]

DOWNGRADE = [f"DROP FUNCTION IF EXISTS public.{signature}" for signature in FUNCTIONS]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
