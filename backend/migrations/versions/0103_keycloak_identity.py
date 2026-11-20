"""Explicit Keycloak links and sessions, staged before each user's cutover.

Every existing and newly created user remains on legacy authority until a verified link is
finalized. Issuer and subject are an immutable, globally unique key; email is not consulted.
All link mutations happen in owner-rights functions that take the user and customer from the
transaction context, never from caller-supplied IDs. Authentication lookup is the only
cross-customer identity read and returns only an active, enabled user of an active customer.

The signed logout entry is deliberately narrow: its caller must first verify the Keycloak
logout token signature, issuer, audience, event, time claims and replay identifier. It revokes
only the exact issuer, subject and Keycloak session ID supplied by that verified token.

Revision ID: 0103
Revises: 0102 (Trash re-upload)
"""

from alembic import op

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
WORKER_ROLE = "kaagaz_worker"

LOOKUP = "kaagaz_keycloak_lookup(text, text)"
STAGE = "kaagaz_keycloak_stage_link(text, text, timestamptz, integer)"
FINALIZE = "kaagaz_keycloak_finalize_link(text, text, timestamptz, integer)"
RECORD = "kaagaz_keycloak_session_record(text, text, text, uuid, integer, timestamptz)"
REVOKE = "kaagaz_keycloak_session_revoke(uuid, timestamptz)"
REVOKE_ALL = "kaagaz_keycloak_sessions_revoke_all(timestamptz)"
LOGOUT = "kaagaz_keycloak_logout(text, text, text, text, timestamptz, timestamptz)"
IMMUTABLE = "kaagaz_keycloak_identity_immutable()"
FLOW_CREATE = "kaagaz_keycloak_flow_create(bytea, text, text, text, text, uuid, uuid, integer, uuid, text, timestamptz, timestamptz)"
FLOW_CONSUME = "kaagaz_keycloak_flow_consume(bytea, timestamptz)"


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


FUNCTIONS = {
    LOOKUP: """
        CREATE FUNCTION public.kaagaz_keycloak_lookup(p_issuer text, p_subject text)
        RETURNS TABLE (user_id uuid, customer_id uuid, session_version integer)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
            SELECT u.id, u.customer_id, u.session_version
            FROM public.keycloak_identities i
            JOIN public.users u ON u.customer_id = i.customer_id AND u.id = i.user_id
            JOIN public.customers c ON c.id = u.customer_id
            WHERE i.issuer = p_issuer AND i.subject = p_subject AND i.status = 'active'
              AND u.auth_authority = 'keycloak' AND NOT u.disabled AND c.status = 'active'
        $$
    """,
    STAGE: """
        CREATE FUNCTION public.kaagaz_keycloak_stage_link(
            p_issuer text, p_subject text, p_now timestamptz, p_expected_session_version integer
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE
            v_customer uuid := public.kaagaz_current_customer();
            v_user uuid := NULLIF(current_setting('kaagaz.user_id', true), '')::uuid;
            v_identity uuid;
        BEGIN
            IF v_customer IS NULL OR v_user IS NULL OR p_expected_session_version IS NULL THEN
                RAISE EXCEPTION 'a user transaction is required' USING ERRCODE = '42501';
            END IF;
            PERFORM 1 FROM public.users u
            JOIN public.customers c ON c.id = u.customer_id
            WHERE u.customer_id = v_customer AND u.id = v_user
              AND u.auth_authority = 'legacy' AND NOT u.disabled AND c.status = 'active'
              AND u.session_version = p_expected_session_version
            FOR UPDATE OF u;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'no active legacy user in this transaction' USING ERRCODE = '42501';
            END IF;
            INSERT INTO public.keycloak_identities
                (customer_id, user_id, issuer, subject, status, linked_at)
            VALUES (v_customer, v_user, p_issuer, p_subject, 'pending', p_now)
            RETURNING id INTO v_identity;
            RETURN v_identity;
        END
        $$
    """,
    FINALIZE: """
        CREATE FUNCTION public.kaagaz_keycloak_finalize_link(
            p_issuer text, p_subject text, p_now timestamptz, p_expected_session_version integer
        ) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE
            v_customer uuid := public.kaagaz_current_customer();
            v_user uuid := NULLIF(current_setting('kaagaz.user_id', true), '')::uuid;
            v_identity uuid;
            v_version integer;
        BEGIN
            IF v_customer IS NULL OR v_user IS NULL OR p_expected_session_version IS NULL THEN
                RAISE EXCEPTION 'a user transaction is required' USING ERRCODE = '42501';
            END IF;
            PERFORM 1 FROM public.users u
            JOIN public.customers c ON c.id = u.customer_id
            WHERE u.customer_id = v_customer AND u.id = v_user
              AND u.auth_authority = 'legacy' AND NOT u.disabled AND c.status = 'active'
              AND u.session_version = p_expected_session_version
            FOR UPDATE OF u;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'no active legacy user in this transaction' USING ERRCODE = '42501';
            END IF;
            SELECT i.id INTO v_identity FROM public.keycloak_identities i
            WHERE i.customer_id = v_customer AND i.user_id = v_user
              AND i.issuer = p_issuer AND i.subject = p_subject AND i.status = 'pending'
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'no pending link for this user' USING ERRCODE = '55000';
            END IF;
            UPDATE public.keycloak_identities
            SET status = 'active', activated_at = p_now
            WHERE id = v_identity AND customer_id = v_customer AND user_id = v_user;
            UPDATE public.users
            SET auth_authority = 'keycloak', session_version = session_version + 1
            WHERE customer_id = v_customer AND id = v_user AND auth_authority = 'legacy'
            RETURNING session_version INTO v_version;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'user authority changed during link finalization' USING ERRCODE = '55000';
            END IF;
            RETURN v_version;
        END
        $$
    """,
    RECORD: """
        CREATE FUNCTION public.kaagaz_keycloak_session_record(
            p_issuer text, p_subject text, p_keycloak_session_id text,
            p_local_session_id uuid, p_expected_session_version integer, p_expires_at timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE
            v_customer uuid := public.kaagaz_current_customer();
            v_user uuid := NULLIF(current_setting('kaagaz.user_id', true), '')::uuid;
            v_rows integer;
        BEGIN
            IF v_customer IS NULL OR v_user IS NULL OR p_local_session_id IS NULL
                OR p_issuer IS NULL OR p_subject IS NULL OR p_keycloak_session_id IS NULL
                OR length(p_issuer) NOT BETWEEN 1 AND 2048
                OR length(p_subject) NOT BETWEEN 1 AND 255
                OR length(p_keycloak_session_id) NOT BETWEEN 1 AND 255
                OR p_expected_session_version IS NULL OR p_expires_at IS NULL THEN
                RAISE EXCEPTION 'a valid user session is required' USING ERRCODE = '42501';
            END IF;
            -- Shared with logout: a callback cannot record after an earlier revocation.
            PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
                p_issuer || pg_catalog.chr(31) || p_subject || pg_catalog.chr(31)
                    || p_keycloak_session_id, 0
            ));
            PERFORM 1 FROM public.users u
            JOIN public.customers c ON c.id = u.customer_id
            WHERE u.customer_id = v_customer AND u.id = v_user
              AND u.auth_authority = 'keycloak' AND u.session_version = p_expected_session_version
              AND NOT u.disabled AND c.status = 'active'
            FOR SHARE OF u;
            IF NOT FOUND THEN
                RETURN false;
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.keycloak_session_revocations r
                WHERE r.issuer = p_issuer AND r.subject = p_subject
                  AND r.keycloak_session_id = p_keycloak_session_id
            ) THEN
                RETURN false;
            END IF;
            INSERT INTO public.keycloak_sessions
                (customer_id, user_id, identity_id, keycloak_session_id, local_session_id, expires_at)
            SELECT i.customer_id, i.user_id, i.id, p_keycloak_session_id, p_local_session_id, p_expires_at
            FROM public.keycloak_identities i
            WHERE i.customer_id = v_customer AND i.user_id = v_user
              AND i.issuer = p_issuer AND i.subject = p_subject AND i.status = 'active'
              AND NOT EXISTS (
                  SELECT 1 FROM public.ended_sessions e
                  WHERE e.customer_id = v_customer AND e.session_id = p_local_session_id
              )
            ON CONFLICT (customer_id, local_session_id, keycloak_session_id) DO NOTHING;
            GET DIAGNOSTICS v_rows = ROW_COUNT;
            IF v_rows <> 1 THEN
                RETURN EXISTS (
                    SELECT 1 FROM public.keycloak_sessions s
                    WHERE s.customer_id = v_customer AND s.user_id = v_user
                      AND s.identity_id IN (
                          SELECT i.id FROM public.keycloak_identities i
                          WHERE i.customer_id = v_customer AND i.user_id = v_user
                            AND i.issuer = p_issuer AND i.subject = p_subject AND i.status = 'active'
                      ) AND s.keycloak_session_id = p_keycloak_session_id
                      AND s.local_session_id = p_local_session_id AND s.revoked_at IS NULL
                );
            END IF;
            RETURN true;
        END
        $$
    """,
    REVOKE: """
        CREATE FUNCTION public.kaagaz_keycloak_session_revoke(
            p_local_session_id uuid, p_now timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE
            v_customer uuid := public.kaagaz_current_customer();
            v_user uuid := NULLIF(current_setting('kaagaz.user_id', true), '')::uuid;
            v_revoked boolean;
        BEGIN
            IF v_customer IS NULL OR v_user IS NULL OR p_local_session_id IS NULL THEN
                RAISE EXCEPTION 'a user transaction is required' USING ERRCODE = '42501';
            END IF;
            WITH changed AS (
                UPDATE public.keycloak_sessions s SET revoked_at = p_now
                WHERE s.customer_id = v_customer AND s.user_id = v_user
                  AND s.local_session_id = p_local_session_id AND s.revoked_at IS NULL
                RETURNING s.customer_id, s.user_id, s.local_session_id, s.expires_at
            ), recorded AS (
                INSERT INTO public.ended_sessions
                    (customer_id, session_id, user_id, ended_at, expires_at)
                SELECT c.customer_id, c.local_session_id, c.user_id, p_now, c.expires_at
                FROM changed c ON CONFLICT (customer_id, session_id) DO NOTHING
                RETURNING 1
            )
            SELECT EXISTS (SELECT 1 FROM changed) INTO v_revoked;
            RETURN v_revoked;
        END
        $$
    """,
    REVOKE_ALL: """
        CREATE FUNCTION public.kaagaz_keycloak_sessions_revoke_all(p_now timestamptz)
        RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE
            v_customer uuid := public.kaagaz_current_customer();
            v_user uuid := NULLIF(current_setting('kaagaz.user_id', true), '')::uuid;
            v_count integer;
        BEGIN
            IF v_customer IS NULL OR v_user IS NULL OR p_now IS NULL THEN
                RAISE EXCEPTION 'a user transaction is required' USING ERRCODE = '42501';
            END IF;
            WITH changed AS (
                UPDATE public.keycloak_sessions s SET revoked_at = p_now
                WHERE s.customer_id = v_customer AND s.user_id = v_user AND s.revoked_at IS NULL
                RETURNING s.customer_id, s.user_id, s.local_session_id, s.expires_at
            ), recorded AS (
                INSERT INTO public.ended_sessions
                    (customer_id, session_id, user_id, ended_at, expires_at)
                SELECT c.customer_id, c.local_session_id, c.user_id, p_now, c.expires_at
                FROM changed c ON CONFLICT (customer_id, session_id) DO NOTHING
                RETURNING 1
            )
            SELECT count(DISTINCT local_session_id)::integer INTO v_count FROM changed;
            RETURN v_count;
        END
        $$
    """,
    LOGOUT: """
        CREATE FUNCTION public.kaagaz_keycloak_logout(
            p_issuer text, p_subject text, p_keycloak_session_id text, p_jti text,
            p_token_expires_at timestamptz, p_now timestamptz
        ) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE
            v_count integer;
        BEGIN
            IF p_issuer IS NULL OR p_subject IS NULL OR p_keycloak_session_id IS NULL
                OR p_jti IS NULL OR p_token_expires_at IS NULL OR p_now IS NULL
                OR length(p_issuer) NOT BETWEEN 1 AND 2048
                OR length(p_subject) NOT BETWEEN 1 AND 255
                OR length(p_keycloak_session_id) NOT BETWEEN 1 AND 255
                OR length(p_jti) NOT BETWEEN 1 AND 255 OR p_token_expires_at <= p_now THEN
                RETURN 0;
            END IF;
            DELETE FROM public.keycloak_logout_jtis j
            WHERE j.issuer = p_issuer AND j.expires_at <= p_now;
            INSERT INTO public.keycloak_logout_jtis (issuer, jti, expires_at)
            VALUES (p_issuer, p_jti, p_token_expires_at)
            ON CONFLICT (issuer, jti) DO NOTHING;
            GET DIAGNOSTICS v_count = ROW_COUNT;
            IF v_count <> 1 THEN
                RETURN NULL;
            END IF;
            PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
                p_issuer || pg_catalog.chr(31) || p_subject || pg_catalog.chr(31)
                    || p_keycloak_session_id, 0
            ));
            INSERT INTO public.keycloak_session_revocations
                (issuer, subject, keycloak_session_id, revoked_at)
            VALUES (p_issuer, p_subject, p_keycloak_session_id, p_now)
            ON CONFLICT (issuer, subject, keycloak_session_id) DO NOTHING;
            WITH changed AS (
                UPDATE public.keycloak_sessions s SET revoked_at = p_now
                FROM public.keycloak_identities i
                WHERE i.id = s.identity_id AND i.issuer = p_issuer AND i.subject = p_subject
                  AND s.keycloak_session_id = p_keycloak_session_id AND s.revoked_at IS NULL
                RETURNING s.customer_id, s.user_id, s.local_session_id, s.expires_at
            ), recorded AS (
                INSERT INTO public.ended_sessions
                    (customer_id, session_id, user_id, ended_at, expires_at)
                SELECT c.customer_id, c.local_session_id, c.user_id, p_now, c.expires_at
                FROM changed c ON CONFLICT (customer_id, session_id) DO NOTHING
                RETURNING 1
            )
            SELECT count(*)::integer INTO v_count FROM changed;
            RETURN v_count;
        END
        $$
    """,
    FLOW_CREATE: """
        CREATE FUNCTION public.kaagaz_keycloak_flow_create(
            p_state_hash bytea, p_nonce text, p_sealed_verifier text, p_purpose text,
            p_next_path text, p_customer_id uuid, p_user_id uuid,
            p_expected_session_version integer, p_local_session_id uuid, p_action text,
            p_started_at timestamptz, p_expires_at timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE
            v_customer uuid := public.kaagaz_current_customer();
            v_user uuid := NULLIF(current_setting('kaagaz.user_id', true), '')::uuid;
            v_authority text;
        BEGIN
            IF p_state_hash IS NULL OR pg_catalog.octet_length(p_state_hash) <> 32
                OR p_nonce IS NULL OR length(p_nonce) NOT BETWEEN 32 AND 256
                OR p_sealed_verifier IS NULL OR length(p_sealed_verifier) NOT BETWEEN 32 AND 2048
                OR p_purpose IS NULL OR p_purpose NOT IN ('login', 'link', 'reauth')
                OR p_next_path IS NULL OR length(p_next_path) NOT BETWEEN 1 AND 2048
                OR p_started_at IS NULL OR p_expires_at IS NULL OR p_expires_at <= p_started_at
                OR p_expires_at > p_started_at + interval '10 minutes' THEN
                RAISE EXCEPTION 'invalid Keycloak flow' USING ERRCODE = '22023';
            END IF;
            DELETE FROM public.keycloak_flows WHERE expires_at <= p_started_at;
            IF p_purpose = 'login' THEN
                IF p_customer_id IS NOT NULL OR p_user_id IS NOT NULL
                    OR p_expected_session_version IS NOT NULL OR p_local_session_id IS NOT NULL
                    OR p_action IS NOT NULL OR v_customer IS NOT NULL OR v_user IS NOT NULL THEN
                    RAISE EXCEPTION 'invalid login flow context' USING ERRCODE = '42501';
                END IF;
            ELSE
                IF v_customer IS NULL OR v_user IS NULL
                    OR p_customer_id IS DISTINCT FROM v_customer
                    OR p_user_id IS DISTINCT FROM v_user OR p_expected_session_version IS NULL
                    OR p_local_session_id IS NULL THEN
                    RAISE EXCEPTION 'a matching signed-in user is required' USING ERRCODE = '42501';
                END IF;
                IF (p_purpose = 'link' AND p_action IS NOT NULL)
                    OR (p_purpose = 'reauth' AND (p_action IS NULL OR p_action NOT IN (
                        'locked_open', 'delete_forever', 'whatsapp', 'access', 'locks'
                    ))) THEN
                    RAISE EXCEPTION 'invalid Keycloak flow action' USING ERRCODE = '22023';
                END IF;
                SELECT u.auth_authority INTO v_authority FROM public.users u
                JOIN public.customers c ON c.id = u.customer_id
                WHERE u.customer_id = v_customer AND u.id = v_user
                  AND u.session_version = p_expected_session_version AND NOT u.disabled
                  AND c.status = 'active'
                FOR SHARE OF u;
                IF NOT FOUND OR (p_purpose = 'link' AND v_authority <> 'legacy')
                    OR (p_purpose = 'reauth' AND v_authority <> 'keycloak') THEN
                    RAISE EXCEPTION 'the user authority or session changed' USING ERRCODE = '42501';
                END IF;
            END IF;
            INSERT INTO public.keycloak_flows
                (state_hash, nonce, sealed_verifier, purpose, next_path, customer_id, user_id,
                 session_version, local_session_id, action, started_at, expires_at)
            VALUES (p_state_hash, p_nonce, p_sealed_verifier, p_purpose, p_next_path,
                    p_customer_id, p_user_id, p_expected_session_version, p_local_session_id,
                    p_action, p_started_at, p_expires_at);
            RETURN true;
        END
        $$
    """,
    FLOW_CONSUME: """
        CREATE FUNCTION public.kaagaz_keycloak_flow_consume(p_state_hash bytea, p_now timestamptz)
        RETURNS TABLE (
            nonce text, sealed_verifier text, purpose text, next_path text, customer_id uuid,
            user_id uuid, session_version integer, local_session_id uuid, action text,
            started_at timestamptz
        )
        LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
            DELETE FROM public.keycloak_flows f
            WHERE f.state_hash = p_state_hash AND f.expires_at > p_now
            RETURNING f.nonce, f.sealed_verifier, f.purpose, f.next_path, f.customer_id,
                      f.user_id, f.session_version, f.local_session_id, f.action, f.started_at
        $$
    """,
}

FUNCTION_COMMENTS = {
    LOOKUP: "Find a Keycloak identity only when its user is explicitly on Keycloak, enabled and in an active customer.",
    STAGE: "Create a pending link for the user and customer already set in this transaction.",
    FINALIZE: "Finalize the current user's pending, securely proven link and end their legacy sessions.",
    RECORD: "Associate one current user's local browser session with their active Keycloak session.",
    REVOKE: "Revoke one of the current user's local browser sessions and add it to ended_sessions.",
    REVOKE_ALL: "Revoke the current user's mapped browser sessions and add each to ended_sessions.",
    LOGOUT: "Consume a verified logout jti and persist its issuer, subject and sid tombstone before revoking sessions.",
    FLOW_CREATE: "Create a one-use Keycloak flow, binding non-login purposes to the current user session.",
    FLOW_CONSUME: "Consume one unexpired opaque Keycloak flow state exactly once.",
}


def _function(signature: str) -> list[str]:
    return [
        FUNCTIONS[signature],
        f"COMMENT ON FUNCTION public.{signature} IS {_literal(FUNCTION_COMMENTS[signature])}",
        f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION public.{signature} TO {APP_ROLE}, {WORKER_ROLE}",
    ]


UPGRADE = [
    """
    ALTER TABLE public.users
        ADD COLUMN auth_authority text NOT NULL DEFAULT 'legacy'
            CHECK (auth_authority IN ('legacy', 'keycloak'))
    """,
    "COMMENT ON COLUMN public.users.auth_authority IS 'The explicit sign-in authority for this user. Existing users stay legacy until a proven link is finalized.'",
    """
    CREATE OR REPLACE FUNCTION public.kaagaz_login_lookup(p_email text)
    RETURNS TABLE (user_id uuid, customer_id uuid, password_hash text, role text, disabled boolean)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT u.id, u.customer_id, u.password_hash, u.role, u.disabled
        FROM public.users u WHERE u.email = lower(p_email) AND u.auth_authority = 'legacy'
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION public.kaagaz_identity_lookup(p_provider text, p_issuer text, p_subject text)
    RETURNS TABLE (identity_id uuid, user_id uuid, customer_id uuid, status text)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT i.id, i.user_id, i.customer_id, i.status
        FROM public.user_identities i
        JOIN public.users u ON u.customer_id = i.customer_id AND u.id = i.user_id
        WHERE i.provider = p_provider AND i.issuer = p_issuer AND i.subject = p_subject
          AND u.auth_authority = 'legacy'
    $$
    """,
    """
    CREATE TABLE public.keycloak_identities (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        user_id uuid NOT NULL,
        issuer text NOT NULL CHECK (length(issuer) BETWEEN 1 AND 2048),
        subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 255),
        status text NOT NULL CHECK (status IN ('pending', 'active')),
        linked_at timestamptz NOT NULL,
        activated_at timestamptz NULL,
        CHECK ((status = 'active') = (activated_at IS NOT NULL)),
        UNIQUE (issuer, subject),
        UNIQUE (customer_id, user_id),
        UNIQUE (customer_id, user_id, id),
        FOREIGN KEY (customer_id, user_id) REFERENCES public.users (customer_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE public.keycloak_sessions (
        customer_id uuid NOT NULL,
        user_id uuid NOT NULL,
        identity_id uuid NOT NULL,
        keycloak_session_id text NOT NULL CHECK (length(keycloak_session_id) BETWEEN 1 AND 255),
        local_session_id uuid NOT NULL,
        expires_at timestamptz NOT NULL,
        revoked_at timestamptz NULL,
        PRIMARY KEY (customer_id, local_session_id, keycloak_session_id),
        FOREIGN KEY (customer_id, user_id, identity_id)
            REFERENCES public.keycloak_identities (customer_id, user_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE public.keycloak_session_revocations (
        issuer text NOT NULL CHECK (length(issuer) BETWEEN 1 AND 2048),
        subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 255),
        keycloak_session_id text NOT NULL CHECK (length(keycloak_session_id) BETWEEN 1 AND 255),
        revoked_at timestamptz NOT NULL,
        PRIMARY KEY (issuer, subject, keycloak_session_id)
    )
    """,
    """
    CREATE TABLE public.keycloak_logout_jtis (
        issuer text NOT NULL CHECK (length(issuer) BETWEEN 1 AND 2048),
        jti text NOT NULL CHECK (length(jti) BETWEEN 1 AND 255),
        expires_at timestamptz NOT NULL,
        PRIMARY KEY (issuer, jti)
    )
    """,
    """
    CREATE TABLE public.keycloak_flows (
        state_hash bytea PRIMARY KEY CHECK (pg_catalog.octet_length(state_hash) = 32),
        nonce text NOT NULL CHECK (length(nonce) BETWEEN 32 AND 256),
        sealed_verifier text NOT NULL CHECK (length(sealed_verifier) BETWEEN 32 AND 2048),
        purpose text NOT NULL CHECK (purpose IN ('login', 'link', 'reauth')),
        next_path text NOT NULL CHECK (length(next_path) BETWEEN 1 AND 2048),
        customer_id uuid NULL,
        user_id uuid NULL,
        session_version integer NULL,
        local_session_id uuid NULL,
        action text NULL CHECK (action IS NULL OR action IN (
            'locked_open', 'delete_forever', 'whatsapp', 'access', 'locks'
        )),
        started_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL,
        CHECK ((purpose = 'login' AND customer_id IS NULL AND user_id IS NULL
                AND session_version IS NULL AND local_session_id IS NULL AND action IS NULL)
            OR (purpose = 'link' AND customer_id IS NOT NULL AND user_id IS NOT NULL
                AND session_version IS NOT NULL AND local_session_id IS NOT NULL AND action IS NULL)
            OR (purpose = 'reauth' AND customer_id IS NOT NULL AND user_id IS NOT NULL
                AND session_version IS NOT NULL AND local_session_id IS NOT NULL AND action IS NOT NULL))
    )
    """,
    "CREATE INDEX keycloak_sessions_logout ON public.keycloak_sessions (identity_id, keycloak_session_id) WHERE revoked_at IS NULL",
    "CREATE INDEX keycloak_flows_expiry ON public.keycloak_flows (expires_at)",
    f"GRANT SELECT ON public.keycloak_sessions TO {APP_ROLE}, {WORKER_ROLE}",
    "ALTER TABLE public.keycloak_identities ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.keycloak_sessions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.keycloak_session_revocations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.keycloak_logout_jtis ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.keycloak_flows ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON public.keycloak_identities
    USING (customer_id = public.kaagaz_current_customer())
    WITH CHECK (customer_id = public.kaagaz_current_customer())
    """,
    """
    CREATE POLICY customer_only ON public.keycloak_sessions
    USING (customer_id = public.kaagaz_current_customer())
    WITH CHECK (customer_id = public.kaagaz_current_customer())
    """,
    "COMMENT ON TABLE public.keycloak_identities IS 'One explicit Keycloak issuer and subject binding per Kaagaz user. Pending bindings cannot sign in; active bindings are immutable.'",
    "COMMENT ON COLUMN public.keycloak_identities.id IS 'The local id of this identity binding.'",
    "COMMENT ON COLUMN public.keycloak_identities.customer_id IS 'The customer of the mapped Kaagaz user.'",
    "COMMENT ON COLUMN public.keycloak_identities.user_id IS 'The Kaagaz user this exact Keycloak identity signs in as.'",
    "COMMENT ON COLUMN public.keycloak_identities.issuer IS 'The verified OIDC issuer, stored exactly and never used with email for identity.'",
    "COMMENT ON COLUMN public.keycloak_identities.subject IS 'The verified OIDC subject, stored exactly and never shown or logged.'",
    "COMMENT ON COLUMN public.keycloak_identities.status IS 'pending until the proven link is finalized; active after Keycloak authority cutover.'",
    "COMMENT ON COLUMN public.keycloak_identities.linked_at IS 'When the authenticated user staged this verified identity.'",
    "COMMENT ON COLUMN public.keycloak_identities.activated_at IS 'When this binding became active and the user changed authority.'",
    "COMMENT ON POLICY customer_only ON public.keycloak_identities IS 'Any future direct read remains restricted to the row''s customer.'",
    "COMMENT ON TABLE public.keycloak_sessions IS 'Keycloak session IDs associated with local browser session IDs for individual logout.'",
    "COMMENT ON COLUMN public.keycloak_sessions.customer_id IS 'The customer of the mapped user.'",
    "COMMENT ON COLUMN public.keycloak_sessions.user_id IS 'The user who owns this local browser session.'",
    "COMMENT ON COLUMN public.keycloak_sessions.identity_id IS 'The immutable Keycloak issuer and subject binding.'",
    "COMMENT ON COLUMN public.keycloak_sessions.keycloak_session_id IS 'The opaque Keycloak sid claim for this browser session.'",
    "COMMENT ON COLUMN public.keycloak_sessions.local_session_id IS 'The random local cookie session ID stored in ended_sessions on logout.'",
    "COMMENT ON COLUMN public.keycloak_sessions.expires_at IS 'The local cookie expiry copied to ended_sessions when revoked.'",
    "COMMENT ON COLUMN public.keycloak_sessions.revoked_at IS 'When the local association was revoked; NULL while active.'",
    "COMMENT ON POLICY customer_only ON public.keycloak_sessions IS 'Session associations are directly visible only inside their customer.'",
    "COMMENT ON TABLE public.keycloak_session_revocations IS 'Persistent issuer, subject and sid tombstones prevent a callback from recording a session after signed logout.'",
    "COMMENT ON COLUMN public.keycloak_session_revocations.issuer IS 'The configured issuer from a verified logout token.'",
    "COMMENT ON COLUMN public.keycloak_session_revocations.subject IS 'The verified opaque subject paired with the issuer.'",
    "COMMENT ON COLUMN public.keycloak_session_revocations.keycloak_session_id IS 'The verified sid that cannot be recorded after revocation.'",
    "COMMENT ON COLUMN public.keycloak_session_revocations.revoked_at IS 'When the verified logout tombstone was recorded.'",
    "COMMENT ON TABLE public.keycloak_logout_jtis IS 'Replay identifiers consumed from verified Keycloak backchannel logout tokens.'",
    "COMMENT ON COLUMN public.keycloak_logout_jtis.issuer IS 'The configured issuer that issued this replay identifier.'",
    "COMMENT ON COLUMN public.keycloak_logout_jtis.jti IS 'The signed logout token identifier, unique per issuer until expiry.'",
    "COMMENT ON COLUMN public.keycloak_logout_jtis.expires_at IS 'The validated token expiry; expired replay identifiers may be removed.'",
    "COMMENT ON TABLE public.keycloak_flows IS 'Short-lived OIDC flow state consumed once; the PKCE verifier is encrypted and no browser stores it.'",
    "COMMENT ON COLUMN public.keycloak_flows.state_hash IS 'SHA-256 of the random state held in the HttpOnly flow cookie and callback query.'",
    "COMMENT ON COLUMN public.keycloak_flows.nonce IS 'Random OIDC nonce checked against the verified ID token.'",
    "COMMENT ON COLUMN public.keycloak_flows.sealed_verifier IS 'PKCE verifier encrypted with the dedicated flow key and bound to state hash.'",
    "COMMENT ON COLUMN public.keycloak_flows.purpose IS 'Login, authenticated legacy-user link, or action-bound Keycloak reauthentication.'",
    "COMMENT ON COLUMN public.keycloak_flows.next_path IS 'A validated same-origin relative path used after callback.'",
    "COMMENT ON COLUMN public.keycloak_flows.customer_id IS 'Customer captured from the authenticated user transaction for link and reauthentication.'",
    "COMMENT ON COLUMN public.keycloak_flows.user_id IS 'User captured from the authenticated user transaction for link and reauthentication.'",
    "COMMENT ON COLUMN public.keycloak_flows.session_version IS 'Local session version bound when the flow starts.'",
    "COMMENT ON COLUMN public.keycloak_flows.local_session_id IS 'Browser session id bound when the flow starts.'",
    "COMMENT ON COLUMN public.keycloak_flows.action IS 'The sole proof action the callback may complete, when purpose is reauth.'",
    "COMMENT ON COLUMN public.keycloak_flows.started_at IS 'Injected-clock start time; ID token auth_time must be no older than this flow.'",
    "COMMENT ON COLUMN public.keycloak_flows.expires_at IS 'The short flow expiry; consume rejects and deletes expired state.'",
    """
    CREATE FUNCTION public.kaagaz_keycloak_identity_immutable() RETURNS trigger
    LANGUAGE plpgsql SET search_path = '' AS $$
    BEGIN
        IF (NEW.customer_id, NEW.user_id, NEW.issuer, NEW.subject)
            IS DISTINCT FROM (OLD.customer_id, OLD.user_id, OLD.issuer, OLD.subject) THEN
            RAISE EXCEPTION 'Keycloak identity bindings are immutable' USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    END
    $$
    """,
    "CREATE TRIGGER keycloak_identity_binding_immutable BEFORE UPDATE OF customer_id, user_id, issuer, subject ON public.keycloak_identities FOR EACH ROW EXECUTE FUNCTION public.kaagaz_keycloak_identity_immutable()",
    "COMMENT ON TRIGGER keycloak_identity_binding_immutable ON public.keycloak_identities IS 'A Keycloak subject and its Kaagaz user cannot be reassigned.'",
    f"REVOKE ALL ON FUNCTION public.{IMMUTABLE} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION public.{IMMUTABLE} TO {APP_ROLE}, {WORKER_ROLE}",
    # The app and worker previously had table-wide UPDATE on users. Retain that access for
    # every existing column while reserving the new authority column for the definer function.
    f"REVOKE INSERT, UPDATE ON public.users FROM {APP_ROLE}, {WORKER_ROLE}",
    f"""
    DO $$
    DECLARE
        insert_columns text;
        update_columns text;
    BEGIN
        SELECT string_agg(format('%I', a.attname), ', ' ORDER BY a.attnum)
        INTO insert_columns
        FROM pg_attribute a
        WHERE a.attrelid = 'public.users'::regclass AND a.attnum > 0 AND NOT a.attisdropped
          AND a.attname <> 'auth_authority';
        update_columns := insert_columns;
        EXECUTE format(
            'GRANT INSERT (%s), UPDATE (%s) ON public.users TO {APP_ROLE}, {WORKER_ROLE}',
            insert_columns, update_columns
        );
    END
    $$
    """,
    *_function(LOOKUP),
    *_function(STAGE),
    *_function(FINALIZE),
    *_function(RECORD),
    *_function(REVOKE),
    *_function(REVOKE_ALL),
    *_function(LOGOUT),
    *_function(FLOW_CREATE),
    *_function(FLOW_CONSUME),
    f"COMMENT ON FUNCTION public.{IMMUTABLE} IS 'Reject any attempt to move a Keycloak identity to another user or change its issuer or subject.'",
]

DOWNGRADE = [
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM public.users WHERE auth_authority = 'keycloak')
            OR EXISTS (SELECT 1 FROM public.keycloak_identities WHERE status = 'active')
            OR EXISTS (SELECT 1 FROM public.keycloak_sessions WHERE revoked_at IS NULL)
            OR EXISTS (SELECT 1 FROM public.keycloak_flows WHERE expires_at > pg_catalog.clock_timestamp())
        THEN
            RAISE EXCEPTION 'cannot downgrade while Keycloak users, identities or sessions are active'
                USING ERRCODE = '55000';
        END IF;
    END
    $$
    """,
    f"""
    DO $$
    DECLARE columns text;
    BEGIN
        SELECT string_agg(format('%I', a.attname), ', ' ORDER BY a.attnum)
        INTO columns FROM pg_attribute a
        WHERE a.attrelid = 'public.users'::regclass AND a.attnum > 0 AND NOT a.attisdropped
          AND a.attname <> 'auth_authority';
        EXECUTE format(
            'REVOKE INSERT (%s), UPDATE (%s) ON public.users FROM {APP_ROLE}, {WORKER_ROLE}',
            columns, columns
        );
    END
    $$
    """,
    f"GRANT INSERT, UPDATE ON public.users TO {APP_ROLE}, {WORKER_ROLE}",
    f"DROP FUNCTION public.{FLOW_CONSUME}",
    f"DROP FUNCTION public.{FLOW_CREATE}",
    f"DROP FUNCTION public.{LOGOUT}",
    f"DROP FUNCTION public.{REVOKE}",
    f"DROP FUNCTION public.{RECORD}",
    f"DROP FUNCTION public.{REVOKE_ALL}",
    f"DROP FUNCTION public.{FINALIZE}",
    f"DROP FUNCTION public.{STAGE}",
    f"DROP FUNCTION public.{LOOKUP}",
    "DROP TABLE public.keycloak_sessions",
    "DROP TABLE public.keycloak_flows",
    "DROP TABLE public.keycloak_logout_jtis",
    "DROP TABLE public.keycloak_session_revocations",
    "DROP TABLE public.keycloak_identities",
    f"DROP FUNCTION public.{IMMUTABLE}",
    """
    CREATE OR REPLACE FUNCTION public.kaagaz_login_lookup(p_email text)
    RETURNS TABLE (user_id uuid, customer_id uuid, password_hash text, role text, disabled boolean)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT u.id, u.customer_id, u.password_hash, u.role, u.disabled
        FROM public.users u WHERE u.email = lower(p_email)
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION public.kaagaz_identity_lookup(p_provider text, p_issuer text, p_subject text)
    RETURNS TABLE (identity_id uuid, user_id uuid, customer_id uuid, status text)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT i.id, i.user_id, i.customer_id, i.status
        FROM public.user_identities i
        WHERE i.provider = p_provider AND i.issuer = p_issuer AND i.subject = p_subject
    $$
    """,
    "ALTER TABLE public.users DROP COLUMN auth_authority",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
