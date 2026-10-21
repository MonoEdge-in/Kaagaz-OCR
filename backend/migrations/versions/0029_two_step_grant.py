"""Two-step sign-in by grant (Tushar, 16 Oct 2026): ``customers.two_step_granted``. Until
the operator allows it, the organisation's admins sign in with the password alone. Written
only by ``kaagaz_op_set_org_two_step`` (the app may read it); stopping it never touches an
admin's stored secret, so a later grant brings each code back.

The function is 0025's ``kaagaz_op_set_org_autofile`` pattern (no step-up, the row locked,
an organisation being deleted refused, audited in ``platform_audit`` and the organisation's
``audit_log``). ``kaagaz_op_customers`` is made again with the column at the end; the
downgrade puts 0025's back. No table is rewritten (a constant default).

Revision ID: 0029
Revises: 0028 (progress steps)
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

OPERATOR_ROLE = "kaagaz_operator"

# As 0016's, 0023's and 0025's (a migration can't import another).
_ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"
_ACTOR = f"""
        PERFORM 1 FROM public.operators o WHERE {_ENABLED};
        IF NOT FOUND THEN
            outcome := 'signed_out';
            RETURN;
        END IF;
"""

SET_TWO_STEP = "kaagaz_op_set_org_two_step(uuid, uuid, boolean)"
CUSTOMERS = "kaagaz_op_customers(uuid)"

_SET_TWO_STEP = f"""
    CREATE FUNCTION public.kaagaz_op_set_org_two_step(
        p_operator uuid, p_customer uuid, p_granted boolean, OUT outcome text, OUT result_id uuid
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_status text;
        v_granted boolean;
        v_action text := CASE WHEN p_granted THEN 'org.two_step_grant' ELSE 'org.two_step_revoke' END;
    BEGIN
        IF p_granted IS NULL THEN
            RAISE EXCEPTION 'granted must be true or false' USING ERRCODE = 'check_violation';
        END IF;
        {_ACTOR}
        SELECT c.status, c.two_step_granted INTO v_status, v_granted
        FROM public.customers c WHERE c.id = p_customer FOR UPDATE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        result_id := p_customer;
        IF v_status = 'deleting' THEN
            outcome := 'deleting';
            RETURN;
        END IF;
        IF v_granted = p_granted THEN
            outcome := 'unchanged';
            RETURN;
        END IF;
        UPDATE public.customers AS c SET two_step_granted = p_granted WHERE c.id = p_customer;
        INSERT INTO public.audit_log (customer_id, actor_user_id, action, target_type, target_id, details)
        VALUES (p_customer, NULL, v_action, 'customer', p_customer::text, jsonb_build_object('by', 'operator'));
        INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id)
        VALUES (p_operator, v_action, p_customer, 'customer', p_customer::text);
        outcome := 'done';
    END
    $$
"""

# 0025's list, with or without the two-step grant at the end.
_CUSTOMERS = """
    CREATE FUNCTION public.kaagaz_op_customers(p_operator uuid)
    RETURNS TABLE (customer_id uuid, name text, status text, disabled_at timestamptz,
                   delete_after timestamptz, created_at timestamptz, admins bigint, reviewers bigint,
                   members bigint, documents bigint, pages bigint, size_bytes bigint,
                   last_activity_at timestamptz, autofile_granted boolean, autofile_on boolean{columns})
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT c.id, c.name, c.status, c.disabled_at, c.delete_after, c.created_at,
               u.admins, u.reviewers, u.members, d.documents, d.pages, d.size_bytes,
               greatest(a.last_at, d.last_at), c.autofile_granted, c.autofile_on{values}
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
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {enabled})
        ORDER BY c.created_at, c.id
    $$
"""
CUSTOMERS_NOW = _CUSTOMERS.format(
    columns=", two_step_granted boolean", values=", c.two_step_granted", enabled=_ENABLED
)
CUSTOMERS_0025 = _CUSTOMERS.format(columns="", values="", enabled=_ENABLED)
COMMENT_0025 = (
    "Operator: every organisation with its status, counts only, and its auto-filing grant and switch."
)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# Plain constants with no colon before a word, which SQLAlchemy would read as a bind parameter.
COLUMN_COMMENT = (
    "The platform operator allowed two-step sign-in for this organisation's admins; while false"
    " they sign in with the password alone. Written only by kaagaz_op_set_org_two_step."
)
FUNCTION_COMMENTS = {
    SET_TWO_STEP: "Operator: allow or stop two-step sign-in for one organisation's admins. An"
    " admin's stored code is kept. Audited in both logs.",
    CUSTOMERS: "Operator: every organisation with its status, counts only, its auto-filing grant"
    " and switch, and its two-step sign-in grant.",
}


def _function(signature: str, create: str, comment: str) -> list[str]:
    return [
        create,
        f"COMMENT ON FUNCTION public.{signature} IS {_literal(comment)}",
        f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION public.{signature} TO {OPERATOR_ROLE}",
    ]


UPGRADE = [
    "ALTER TABLE customers ADD COLUMN two_step_granted boolean NOT NULL DEFAULT false",
    f"COMMENT ON COLUMN customers.two_step_granted IS {_literal(COLUMN_COMMENT)}",
    *_function(SET_TWO_STEP, _SET_TWO_STEP, FUNCTION_COMMENTS[SET_TWO_STEP]),
    f"DROP FUNCTION public.{CUSTOMERS}",
    *_function(CUSTOMERS, CUSTOMERS_NOW, FUNCTION_COMMENTS[CUSTOMERS]),
]

DOWNGRADE = [
    f"DROP FUNCTION IF EXISTS public.{SET_TWO_STEP}",
    f"DROP FUNCTION public.{CUSTOMERS}",
    *_function(CUSTOMERS, CUSTOMERS_0025, COMMENT_0025),
    "ALTER TABLE customers DROP COLUMN IF EXISTS two_step_granted",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
