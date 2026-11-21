"""Keycloak links for admin actions, and an audit row for Move everyone (#737).

Admin actions (disable, enable, unlock, reset two-step, resend the setup email) must act on the
exact Keycloak account that is linked, never on whoever holds the same email in Keycloak. The
application role has no table access to ``keycloak_identities``, so one owner-rights read returns
the current customer's active links, taken from the transaction context and never from a
caller-supplied customer id.

``kaagaz_op_move_everyone`` is the operator's step-up-protected audit write for moving an
organisation's people to company sign-in: it uses up the fresh code, then writes one
``platform_audit`` row and one row in the organisation's own log with counts only.

Revision ID: 0105
Revises: 0104 (Keycloak isolation)
"""

from alembic import op

revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
WORKER_ROLE = "kaagaz_worker"
OPERATOR_ROLE = "kaagaz_operator"
SIGNATURE = "kaagaz_keycloak_subjects()"
MOVE = "kaagaz_op_move_everyone(uuid, uuid, integer, integer, bigint)"

UPGRADE = [
    """
    CREATE FUNCTION public.kaagaz_keycloak_subjects()
    RETURNS TABLE (user_id uuid, issuer text, subject text)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT i.user_id, i.issuer, i.subject
        FROM public.keycloak_identities i
        WHERE i.customer_id = public.kaagaz_current_customer() AND i.status = 'active'
    $$
    """,
    f"COMMENT ON FUNCTION public.{SIGNATURE} IS 'The current customer''s active Keycloak links: user, issuer and subject.'",
    f"REVOKE ALL ON FUNCTION public.{SIGNATURE} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION public.{SIGNATURE} TO {APP_ROLE}, {WORKER_ROLE}",
    """
    CREATE FUNCTION public.kaagaz_op_move_everyone(
        p_operator uuid, p_customer uuid, p_moved integer, p_attention integer, p_step bigint,
        OUT outcome text, OUT result_id uuid
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_status text;
    BEGIN
        UPDATE public.operators AS o
        SET totp_last_step = p_step, step_up_failures = 0, step_up_locked_until = NULL
        WHERE o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL AND p_step IS NOT NULL
          AND (o.totp_last_step IS NULL OR o.totp_last_step < p_step);
        IF NOT FOUND THEN
            outcome := 'step_up';
            RETURN;
        END IF;
        SELECT c.status INTO v_status FROM public.customers c WHERE c.id = p_customer FOR SHARE;
        IF NOT FOUND OR v_status = 'deleting' THEN
            outcome := CASE WHEN v_status = 'deleting' THEN 'deleting' ELSE 'missing' END;
            RETURN;
        END IF;
        result_id := p_customer;
        INSERT INTO public.audit_log (customer_id, actor_user_id, action, target_type, target_id, details)
        VALUES (p_customer, NULL, 'org.move_everyone', 'customer', p_customer::text,
                jsonb_build_object('by', 'operator', 'moved', p_moved, 'need_attention', p_attention));
        INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id)
        VALUES (p_operator, 'org.move_everyone', p_customer, 'customer', p_customer::text);
        outcome := 'done';
    END
    $$
    """,
    f"COMMENT ON FUNCTION public.{MOVE} IS 'Use up the operator''s fresh code and record that an organisation''s people were moved to company sign-in: counts only.'",
    f"REVOKE ALL ON FUNCTION public.{MOVE} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION public.{MOVE} TO {OPERATOR_ROLE}",
]

DOWNGRADE = [f"DROP FUNCTION public.{MOVE}", f"DROP FUNCTION public.{SIGNATURE}"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
