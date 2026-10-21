"""Shared OpenRouter scheduling and operator RPM controls.

Each request gets one live queue ticket. PostgreSQL serves active organisations in round-
robin order, then active users within an organisation. The platform spaces admissions at
1,200 RPM. Optional organisation and user overrides only lower that allowance. Expired
tickets are removed so a crashed process cannot hold up the queue.

Revision ID: 0027
Revises: 0026
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
OPERATOR_ROLE = "kaagaz_operator"

UPGRADE = [
    """
    CREATE TABLE model_rate_scheduler (
        id boolean PRIMARY KEY DEFAULT true CHECK (id),
        next_at timestamptz NOT NULL DEFAULT '-infinity'
    )
    """,
    "INSERT INTO model_rate_scheduler DEFAULT VALUES",
    """
    CREATE TABLE model_rate_waiters (
        request_id uuid PRIMARY KEY,
        customer_id uuid NULL REFERENCES customers(id) ON DELETE CASCADE,
        user_id uuid NULL,
        queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        lease_until timestamptz NOT NULL,
        FOREIGN KEY (customer_id, user_id) REFERENCES users(customer_id, id) ON DELETE CASCADE,
        CHECK (user_id IS NULL OR customer_id IS NOT NULL)
    )
    """,
    "CREATE INDEX model_rate_waiters_turn ON model_rate_waiters (queued_at, request_id)",
    """
    CREATE TABLE model_rate_customer_turns (
        customer_id uuid NULL REFERENCES customers(id) ON DELETE CASCADE,
        last_served_at timestamptz NOT NULL DEFAULT '-infinity',
        UNIQUE NULLS NOT DISTINCT (customer_id)
    )
    """,
    """
    CREATE TABLE model_rate_user_turns (
        customer_id uuid NULL,
        user_id uuid NULL,
        last_served_at timestamptz NOT NULL DEFAULT '-infinity',
        CHECK (user_id IS NULL OR customer_id IS NOT NULL),
        FOREIGN KEY (customer_id, user_id) REFERENCES users(customer_id, id) ON DELETE CASCADE,
        UNIQUE NULLS NOT DISTINCT (customer_id, user_id)
    )
    """,
    """
    CREATE TABLE model_rate_overrides (
        customer_id uuid PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
        rpm integer NOT NULL CHECK (rpm BETWEEN 1 AND 600),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE user_model_rate_overrides (
        customer_id uuid NOT NULL,
        user_id uuid NOT NULL,
        rpm integer NOT NULL CHECK (rpm BETWEEN 1 AND 120),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, user_id),
        FOREIGN KEY (customer_id, user_id) REFERENCES users(customer_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE model_rate_state (
        scope text NOT NULL CHECK (scope IN ('organisation', 'user')),
        customer_id uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
        user_id uuid NULL,
        next_at timestamptz NOT NULL,
        CHECK ((scope = 'organisation' AND user_id IS NULL) OR (scope = 'user' AND user_id IS NOT NULL)),
        FOREIGN KEY (customer_id, user_id) REFERENCES users(customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE UNIQUE INDEX model_rate_state_org ON model_rate_state (customer_id) WHERE scope = 'organisation'",
    "CREATE UNIQUE INDEX model_rate_state_user ON model_rate_state (customer_id, user_id) WHERE scope = 'user'",
    "ALTER TABLE model_rate_overrides ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE user_model_rate_overrides ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE model_rate_state ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE model_rate_waiters ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY customer_only ON model_rate_overrides USING (customer_id = current_setting('kaagaz.customer_id', true)::uuid)",
    "CREATE POLICY customer_only ON user_model_rate_overrides USING (customer_id = current_setting('kaagaz.customer_id', true)::uuid)",
    "CREATE POLICY customer_only ON model_rate_state USING (customer_id = current_setting('kaagaz.customer_id', true)::uuid)",
    """
    CREATE FUNCTION kaagaz_model_rate_slot(
        p_request uuid, p_customer uuid, p_user uuid, p_wait_budget double precision
    ) RETURNS double precision
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_now timestamptz;
        v_start timestamptz;
        v_org_next timestamptz := '-infinity';
        v_user_next timestamptz := '-infinity';
        v_org_rpm integer;
        v_user_rpm integer;
        v_next_request uuid;
        v_next_customer uuid;
        v_next_user uuid;
    BEGIN
        IF p_request IS NULL OR (p_customer IS NOT NULL AND (
            current_setting('kaagaz.customer_id', true) IS DISTINCT FROM p_customer::text
            OR NOT EXISTS (SELECT 1 FROM public.customers c WHERE c.id = p_customer)
        )) OR (p_user IS NOT NULL AND (
            current_setting('kaagaz.user_id', true) IS DISTINCT FROM p_user::text
            OR p_customer IS NULL
            OR NOT EXISTS (SELECT 1 FROM public.users u WHERE u.customer_id = p_customer AND u.id = p_user)
        )) THEN
            RAISE EXCEPTION 'invalid model rate scope' USING ERRCODE = 'foreign_key_violation';
        END IF;

        PERFORM 1 FROM public.model_rate_scheduler WHERE id FOR UPDATE;
        v_now := clock_timestamp();
        DELETE FROM public.model_rate_waiters WHERE lease_until <= v_now;
        INSERT INTO public.model_rate_waiters (request_id, customer_id, user_id, lease_until)
            VALUES (p_request, p_customer, p_user, v_now + interval '2 minutes')
            ON CONFLICT (request_id) DO UPDATE SET lease_until = EXCLUDED.lease_until;
        INSERT INTO public.model_rate_customer_turns (customer_id) VALUES (p_customer)
            ON CONFLICT (customer_id) DO NOTHING;
        INSERT INTO public.model_rate_user_turns (customer_id, user_id) VALUES (p_customer, p_user)
            ON CONFLICT (customer_id, user_id) DO NOTHING;
        IF p_wait_budget IS NOT NULL AND p_wait_budget <= 0 THEN
            DELETE FROM public.model_rate_waiters WHERE request_id = p_request;
            RETURN -1;
        END IF;
        -- Each active customer and user waits for the one least recently served.
        SELECT w.customer_id INTO v_next_customer
            FROM public.model_rate_waiters w
            JOIN public.model_rate_customer_turns t ON t.customer_id IS NOT DISTINCT FROM w.customer_id
            GROUP BY w.customer_id, t.last_served_at
            ORDER BY t.last_served_at, min(w.queued_at), w.customer_id NULLS FIRST LIMIT 1;
        SELECT w.request_id, w.user_id INTO v_next_request, v_next_user
            FROM public.model_rate_waiters w
            JOIN public.model_rate_user_turns t
              ON t.customer_id IS NOT DISTINCT FROM w.customer_id
             AND t.user_id IS NOT DISTINCT FROM w.user_id
            WHERE w.customer_id IS NOT DISTINCT FROM v_next_customer
            ORDER BY t.last_served_at, w.queued_at, w.request_id LIMIT 1;
        IF v_next_request IS DISTINCT FROM p_request THEN
            IF p_wait_budget IS NOT NULL AND p_wait_budget <= 0.05 THEN
                DELETE FROM public.model_rate_waiters WHERE request_id = p_request;
                RETURN -1;
            END IF;
            RETURN 0.05;
        END IF;

        IF p_customer IS NOT NULL THEN
            SELECT rpm INTO v_org_rpm FROM public.model_rate_overrides WHERE customer_id = p_customer;
        END IF;
        IF p_user IS NOT NULL THEN
            SELECT rpm INTO v_user_rpm
                FROM public.user_model_rate_overrides WHERE customer_id = p_customer AND user_id = p_user;
        END IF;
        IF v_org_rpm IS NOT NULL THEN
            SELECT coalesce(max(r.next_at), '-infinity') INTO v_org_next
                FROM public.model_rate_state r
                WHERE r.scope = 'organisation' AND r.customer_id = p_customer;
        END IF;
        IF v_user_rpm IS NOT NULL THEN
            SELECT coalesce(max(r.next_at), '-infinity') INTO v_user_next
                FROM public.model_rate_state r
                WHERE r.scope = 'user' AND r.customer_id = p_customer AND r.user_id = p_user;
        END IF;
        SELECT greatest(v_now, s.next_at, v_org_next, v_user_next) INTO v_start
            FROM public.model_rate_scheduler s WHERE s.id;
        IF p_wait_budget IS NOT NULL AND (
            p_wait_budget <= 0 OR extract(epoch FROM v_start - v_now) >= p_wait_budget
        ) THEN
            DELETE FROM public.model_rate_waiters WHERE request_id = p_request;
            RETURN -1;
        END IF;
        IF v_start > v_now THEN
            RETURN extract(epoch FROM v_start - v_now);
        END IF;

        UPDATE public.model_rate_scheduler
            SET next_at = v_start + interval '50 milliseconds'
            WHERE id;
        DELETE FROM public.model_rate_waiters WHERE request_id = p_request;
        UPDATE public.model_rate_customer_turns SET last_served_at = v_now
            WHERE customer_id IS NOT DISTINCT FROM p_customer;
        UPDATE public.model_rate_user_turns SET last_served_at = v_now
            WHERE customer_id IS NOT DISTINCT FROM p_customer AND user_id IS NOT DISTINCT FROM p_user;
        IF v_org_rpm IS NOT NULL THEN
            INSERT INTO public.model_rate_state (scope, customer_id, user_id, next_at)
                VALUES ('organisation', p_customer, NULL, v_start + make_interval(secs => 60.0 / v_org_rpm))
                ON CONFLICT (customer_id) WHERE scope = 'organisation'
                DO UPDATE SET next_at = EXCLUDED.next_at;
        END IF;
        IF v_user_rpm IS NOT NULL THEN
            INSERT INTO public.model_rate_state (scope, customer_id, user_id, next_at)
                VALUES ('user', p_customer, p_user,
                    v_start + make_interval(secs => 60.0 / least(coalesce(v_org_rpm, 1200), v_user_rpm)))
                ON CONFLICT (customer_id, user_id) WHERE scope = 'user'
                DO UPDATE SET next_at = EXCLUDED.next_at;
        END IF;
        RETURN 0;
    END
    $$
    """,
    "REVOKE ALL ON FUNCTION kaagaz_model_rate_slot(uuid, uuid, uuid, double precision) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION kaagaz_model_rate_slot(uuid, uuid, uuid, double precision) TO {APP_ROLE}",
    """
    CREATE FUNCTION kaagaz_op_model_rates(p_operator uuid, p_customer uuid)
    RETURNS TABLE (org_rpm integer, user_id uuid, email text, user_rpm integer)
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM public.operators o
            WHERE o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL
        ) OR NOT EXISTS (SELECT 1 FROM public.customers c WHERE c.id = p_customer) THEN
            RETURN;
        END IF;
        RETURN QUERY
        SELECT (SELECT r.rpm FROM public.model_rate_overrides r WHERE r.customer_id = p_customer),
               u.id, u.email,
               (SELECT x.rpm FROM public.user_model_rate_overrides x
                WHERE x.customer_id = p_customer AND x.user_id = u.id)
        FROM public.customers c
        LEFT JOIN public.users u ON u.customer_id = c.id
        WHERE c.id = p_customer
        ORDER BY u.id;
    END
    $$
    """,
    """
    CREATE FUNCTION kaagaz_op_set_model_rate(
        p_operator uuid, p_customer uuid, p_user uuid, p_rpm integer, p_step bigint
    ) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE v_old integer; v_scope text; v_target text;
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM public.operators o
            WHERE o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL
        ) THEN
            RETURN 'signed_out';
        END IF;
        IF (p_user IS NULL AND p_rpm IS NOT NULL AND p_rpm NOT BETWEEN 1 AND 600)
           OR (p_user IS NOT NULL AND p_rpm IS NOT NULL AND p_rpm NOT BETWEEN 1 AND 120) THEN
            RETURN 'invalid';
        END IF;
        UPDATE public.operators o
            SET totp_last_step = p_step, step_up_failures = 0, step_up_locked_until = NULL
            WHERE o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL
              AND p_step IS NOT NULL AND (o.totp_last_step IS NULL OR o.totp_last_step < p_step);
        IF NOT FOUND THEN RETURN 'step_up'; END IF;
        PERFORM 1 FROM public.model_rate_scheduler WHERE id FOR UPDATE;
        IF p_user IS NULL THEN
            IF NOT EXISTS (SELECT 1 FROM public.customers c WHERE c.id = p_customer) THEN RETURN 'missing'; END IF;
            SELECT rpm INTO v_old FROM public.model_rate_overrides WHERE customer_id = p_customer;
            v_scope := 'organisation'; v_target := p_customer::text;
            IF p_rpm IS NULL THEN
                DELETE FROM public.model_rate_overrides WHERE customer_id = p_customer;
            ELSE
                INSERT INTO public.model_rate_overrides (customer_id, rpm) VALUES (p_customer, p_rpm)
                    ON CONFLICT (customer_id) DO UPDATE SET rpm = EXCLUDED.rpm, updated_at = now();
            END IF;
            UPDATE public.model_rate_state SET next_at = clock_timestamp() WHERE customer_id = p_customer;
        ELSE
            SELECT rpm INTO v_old FROM public.user_model_rate_overrides
                WHERE customer_id = p_customer AND user_id = p_user;
            IF NOT EXISTS (
                SELECT 1 FROM public.users u WHERE u.customer_id = p_customer AND u.id = p_user
            ) THEN RETURN 'missing'; END IF;
            v_scope := 'user'; v_target := p_user::text;
            IF p_rpm IS NULL THEN
                DELETE FROM public.user_model_rate_overrides WHERE customer_id = p_customer AND user_id = p_user;
            ELSE
                INSERT INTO public.user_model_rate_overrides (customer_id, user_id, rpm)
                    VALUES (p_customer, p_user, p_rpm)
                    ON CONFLICT (customer_id, user_id) DO UPDATE SET rpm = EXCLUDED.rpm, updated_at = now();
            END IF;
            UPDATE public.model_rate_state SET next_at = clock_timestamp()
                WHERE customer_id = p_customer AND scope = 'user' AND user_id = p_user;
        END IF;
        INSERT INTO public.platform_audit
            (operator_id, action, subject_customer_id, target_type, target_id, details)
            VALUES (p_operator, 'model.rpm_set', p_customer, v_scope, v_target,
                    jsonb_build_object('old_rpm', v_old, 'new_rpm', p_rpm));
        RETURN 'done';
    END
    $$
    """,
    "REVOKE ALL ON FUNCTION kaagaz_op_model_rates(uuid, uuid) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION kaagaz_op_set_model_rate(uuid, uuid, uuid, integer, bigint) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION kaagaz_op_model_rates(uuid, uuid), kaagaz_op_set_model_rate(uuid, uuid, uuid, integer, bigint) TO {OPERATOR_ROLE}",
    "COMMENT ON TABLE model_rate_scheduler IS 'Shared OpenRouter RPM schedule and last served organisation/user.'",
    "COMMENT ON TABLE model_rate_waiters IS 'Live model requests waiting for fair shared RPM admission.'",
    "COMMENT ON TABLE model_rate_customer_turns IS 'Last served time for each active customer queue; least recently served gets the next platform turn.'",
    "COMMENT ON TABLE model_rate_user_turns IS 'Last served time for each active user queue inside a customer turn.'",
    "COMMENT ON TABLE model_rate_overrides IS 'Optional operator-set organisation RPM caps; missing rows use fair sharing under the platform ceiling.'",
    "COMMENT ON TABLE user_model_rate_overrides IS 'Optional operator-set user RPM caps; missing rows use fair sharing under the organisation/platform ceiling.'",
    "COMMENT ON TABLE model_rate_state IS 'Next eligible model-request time for scopes with an explicit organisation or user RPM cap.'",
    "COMMENT ON INDEX model_rate_waiters_turn IS 'The request ordering used by the fair model-request queue.'",
    "COMMENT ON INDEX model_rate_state_org IS 'One next-eligible time per organisation with an RPM override.'",
    "COMMENT ON INDEX model_rate_state_user IS 'One next-eligible time per user with an RPM override.'",
    "COMMENT ON POLICY customer_only ON model_rate_overrides IS 'An organisation can see its own saved RPM override only.'",
    "COMMENT ON POLICY customer_only ON user_model_rate_overrides IS 'An organisation can see its own user RPM overrides only.'",
    "COMMENT ON POLICY customer_only ON model_rate_state IS 'An organisation can see only its own RPM scheduling state.'",
    "COMMENT ON FUNCTION kaagaz_model_rate_slot(uuid, uuid, uuid, double precision) IS 'Fairly queues and admits one platform, organisation and user model API request.'",
    "COMMENT ON FUNCTION kaagaz_op_model_rates(uuid, uuid) IS 'Operator: read one organisation and its users model RPM overrides.'",
    "COMMENT ON FUNCTION kaagaz_op_set_model_rate(uuid, uuid, uuid, integer, bigint) IS 'Operator, with a fresh code: set or clear one organisation or user RPM override and audit it.'",
]

DOWNGRADE = [
    "DROP FUNCTION IF EXISTS kaagaz_op_set_model_rate(uuid, uuid, uuid, integer, bigint)",
    "DROP FUNCTION IF EXISTS kaagaz_op_model_rates(uuid, uuid)",
    "DROP FUNCTION IF EXISTS kaagaz_model_rate_slot(uuid, uuid, uuid, double precision)",
    "DROP TABLE IF EXISTS model_rate_user_turns, model_rate_customer_turns, model_rate_waiters, model_rate_state, user_model_rate_overrides, model_rate_overrides, model_rate_scheduler",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
