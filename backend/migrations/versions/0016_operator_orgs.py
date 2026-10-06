"""What a platform operator may do to organisations (super admin PR 3, council 6 Oct
2026, PROVISIONAL; change plan Outside-Folder/docs/plans/2026-10-06-super-admin.md,
awaiting Vrushit's approval: these are the first functions under the never-do 1 exception).
Their admins are 0017's (PR 3b).

Two ``operators`` columns, ``step_up_failures`` and ``step_up_locked_until``: the
step-up's own count and lock (review fix), so nobody who knows an operator's e-mail can
block a step-up by locking the sign-in, and wrong step-up codes never lock the sign-in.
Same thresholds as sign-in (PROVISIONAL). Each ``kaagaz_op_*`` is SECURITY DEFINER with an
empty search path and schema-qualified names, revoked from PUBLIC and runnable by
``kaagaz_operator`` ONLY (0015). Each takes ``p_operator`` and acts only for an
enabled operator whose two-step secret is confirmed.

* The list returns counts only: never a document's name or content, never a user's
  name or e-mail, never ``audit_log.details`` or ``target_id``.
* Every write names its one customer and writes exactly one ``platform_audit`` row in
  the same transaction. Disabling or enabling also writes one row in that customer's
  own ``audit_log`` (``{"by": "operator"}``), as the CLI's do, so its admins see it.
* A write answers ``(outcome, result_id)``: ``done``, ``unchanged``, ``missing``,
  ``deleting`` (being deleted: PR 4), ``step_up`` (the fresh two-step code was not
  accepted), ``taken`` (the e-mail is in use: one ``platform_audit`` row, no e-mail
  stored) or ``signed_out`` (the operator is disabled). Only ``done`` changes anything
  (``missing`` may still use up the code's step).
* Step-up: disabling a customer takes the step of a fresh code the API checked against
  the operator's own secret, after ``kaagaz_op_begin_step_up`` counted the attempt. The
  function uses the step up (never twice: the UPDATE locks the operator row) and clears
  the step-up count.

Disable and enable mirror ``db.customers.set_status`` (PR 1): the row is locked
first; disabling raises every user's session version, enabling clears their sign-in
locks; a customer being deleted is refused. Schedule and cancel delete are PR 4's.

The downgrade drops the functions and the two columns; nothing else changes.

Revision ID: 0016
Revises: 0015 (operators)
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

OPERATOR_ROLE = "kaagaz_operator"
_ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"
_WRITE = "OUT outcome text, OUT result_id uuid"
_HEAD = "LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$"
# A write by an enabled operator only (the route checked it too; this closes the race).
_ACTOR = f"""
        PERFORM 1 FROM public.operators o WHERE {_ENABLED};
        IF NOT FOUND THEN
            outcome := 'signed_out';
            RETURN;
        END IF;
"""
# Step-up: use up the fresh code's step (later than the last one used), clear the count.
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
_PLATFORM = (
    "INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id)"
)
_CUSTOMER_LOG = (
    "INSERT INTO public.audit_log (customer_id, actor_user_id, action, target_type, target_id, details)"
)

FUNCTIONS = {
    # Every customer with counts only. Documents and pages: current versions not in
    # Trash; size: every stored file. O(customers + their users, documents and audit rows).
    # ponytail: counted on every call; per-customer counters are the upgrade if it gets slow.
    "kaagaz_op_customers(uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_customers(p_operator uuid)
    RETURNS TABLE (customer_id uuid, name text, status text, disabled_at timestamptz,
                   delete_after timestamptz, created_at timestamptz, admins bigint, reviewers bigint,
                   members bigint, documents bigint, pages bigint, size_bytes bigint,
                   last_activity_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT c.id, c.name, c.status, c.disabled_at, c.delete_after, c.created_at,
               u.admins, u.reviewers, u.members, d.documents, d.pages, d.size_bytes,
               greatest(a.last_at, d.last_at)
        FROM public.customers c
        CROSS JOIN LATERAL (
            SELECT count(*) FILTER (WHERE x.role = 'admin') AS admins,
                   count(*) FILTER (WHERE x.role = 'reviewer') AS reviewers,
                   count(*) FILTER (WHERE x.role = 'member') AS members
            FROM public.users x WHERE x.customer_id = c.id
        ) u
        CROSS JOIN LATERAL (
            SELECT count(*) FILTER (WHERE x.is_current AND x.deleted_at IS NULL) AS documents,
                   coalesce(sum(x.page_count) FILTER (WHERE x.is_current AND x.deleted_at IS NULL), 0)
                       AS pages,
                   coalesce(sum(x.size_bytes), 0)::bigint AS size_bytes,
                   max(x.created_at) AS last_at
            FROM public.documents x WHERE x.customer_id = c.id
        ) d
        CROSS JOIN LATERAL (
            SELECT max(x.created_at) AS last_at FROM public.audit_log x WHERE x.customer_id = c.id
        ) a
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        ORDER BY c.created_at, c.id
    $$
    """,
    # A customer and its first admin, whose password is temporary (as kaagaz_create_customer,
    # plus must_change_password). A taken e-mail (any user's): nothing made, outcome taken
    # and one refusal row, so probing for e-mails leaves a trace.
    "kaagaz_op_create_customer(uuid, text, text, text, text)": f"""
    CREATE FUNCTION public.kaagaz_op_create_customer(
        p_operator uuid, p_name text, p_admin_email text, p_admin_name text, p_password_hash text, {_WRITE}
    )
    {_HEAD}
    BEGIN
        {_ACTOR}
        BEGIN
            INSERT INTO public.customers (name) VALUES (p_name) RETURNING id INTO result_id;
            INSERT INTO public.users (customer_id, email, display_name, role, password_hash, must_change_password)
            VALUES (result_id, lower(p_admin_email), p_admin_name, 'admin', p_password_hash, true);
        EXCEPTION WHEN unique_violation THEN
            {_PLATFORM} VALUES (p_operator, 'customer.create_refused', NULL, 'customer', NULL);
            outcome := 'taken';
            result_id := NULL;
            RETURN;
        END;
        {_PLATFORM} VALUES (p_operator, 'customer.create', result_id, 'customer', result_id::text);
        outcome := 'done';
    END
    $$
    """,
    # Disable (step-up) or enable one customer, as db.customers.set_status.
    "kaagaz_op_set_customer_status(uuid, uuid, text, timestamptz, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_set_customer_status(
        p_operator uuid, p_customer uuid, p_status text, p_now timestamptz, p_step bigint, {_WRITE}
    )
    {_HEAD}
    DECLARE
        v_before text;
        v_action text := CASE WHEN p_status = 'disabled' THEN 'customer.disable' ELSE 'customer.enable' END;
    BEGIN
        IF p_status NOT IN ('active', 'disabled') THEN
            RAISE EXCEPTION 'status must be active or disabled' USING ERRCODE = 'check_violation';
        END IF;
        IF p_status = 'disabled' THEN
            {_STEP_UP}
        ELSE
            {_ACTOR}
        END IF;
        SELECT c.status INTO v_before FROM public.customers c WHERE c.id = p_customer FOR UPDATE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        result_id := p_customer;
        IF v_before = 'deleting' OR v_before = p_status THEN
            outcome := CASE WHEN v_before = 'deleting' THEN 'deleting' ELSE 'unchanged' END;
            RETURN;
        END IF;
        UPDATE public.customers AS c
        SET status = p_status, disabled_at = CASE WHEN p_status = 'active' THEN NULL ELSE p_now END
        WHERE c.id = p_customer;
        IF p_status = 'disabled' THEN  -- every session ends for good
            UPDATE public.users AS u SET session_version = u.session_version + 1 WHERE u.customer_id = p_customer;
        ELSE  -- the failures counted while it was off
            UPDATE public.users AS u SET failed_logins = 0, locked_until = NULL WHERE u.customer_id = p_customer;
        END IF;
        {_CUSTOMER_LOG}
        VALUES (p_customer, NULL, v_action, 'customer', p_customer::text, jsonb_build_object('by', 'operator'));
        {_PLATFORM} VALUES (p_operator, v_action, p_customer, 'customer', p_customer::text);
        outcome := 'done';
    END
    $$
    """,
    # One step-up attempt counted before the code is checked (as kaagaz_op_begin_login, on
    # the step-up's own count and lock). The secret and last step; no row: locked or disabled.
    "kaagaz_op_begin_step_up(uuid, timestamptz, integer, timestamptz)": f"""
    CREATE FUNCTION public.kaagaz_op_begin_step_up(
        p_operator uuid, p_now timestamptz, p_max_failures integer, p_lock_end timestamptz
    )
    RETURNS TABLE (totp_secret_enc text, totp_last_step bigint, step_up_failures integer)
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
        UPDATE public.operators AS o SET
            step_up_failures = CASE WHEN o.step_up_locked_until <= p_now THEN 1 ELSE o.step_up_failures + 1 END,
            step_up_locked_until = CASE
                WHEN (CASE WHEN o.step_up_locked_until <= p_now THEN 1 ELSE o.step_up_failures + 1 END)
                    >= p_max_failures
                THEN p_lock_end ELSE NULL END
        WHERE {_ENABLED} AND (o.step_up_locked_until IS NULL OR o.step_up_locked_until <= p_now)
        RETURNING o.totp_secret_enc, o.totp_last_step, o.step_up_failures
    $$
    """,
    # A step-up code that was refused: one row, operator.step_up_locked when that attempt
    # started the step-up lock (the API knows: its count reached the limit).
    "kaagaz_op_step_up_failed(uuid, boolean)": """
    CREATE FUNCTION public.kaagaz_op_step_up_failed(p_operator uuid, p_locked boolean) RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
        INSERT INTO public.platform_audit (operator_id, action, target_type, target_id)
        SELECT o.id, CASE WHEN p_locked THEN 'operator.step_up_locked' ELSE 'operator.step_up_failed' END,
               'operator', o.id::text
        FROM public.operators o WHERE o.id = p_operator
    $$
    """,
}

COLUMNS = {
    "step_up_failures": "Wrong step-up codes in a row, counted apart from failed_logins (the sign-in).",
    "step_up_locked_until": "Step-up codes are refused until this time. NULL = not locked.",
}

UPGRADE = [
    """
    ALTER TABLE operators
        ADD COLUMN step_up_failures integer NOT NULL DEFAULT 0 CHECK (step_up_failures >= 0),
        ADD COLUMN step_up_locked_until timestamptz NULL
    """,
    *[f"COMMENT ON COLUMN operators.{column} IS '{comment}'" for column, comment in COLUMNS.items()],
    *[
        statement
        for signature, create in FUNCTIONS.items()
        for statement in (
            create,
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{signature} TO {OPERATOR_ROLE}",
        )
    ],
]

DOWNGRADE = [
    *[f"DROP FUNCTION IF EXISTS public.{signature}" for signature in FUNCTIONS],
    "ALTER TABLE operators DROP COLUMN step_up_locked_until, DROP COLUMN step_up_failures",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
