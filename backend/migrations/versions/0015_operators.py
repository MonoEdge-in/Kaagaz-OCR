"""Platform operators (super admin PR 2, council 6 Oct 2026, PROVISIONAL).

Change plan: Outside-Folder/docs/plans/2026-10-06-super-admin.md (awaiting
Vrushit's approval).

* ``operators``: who runs the platform. Not customer users: no ``customer_id``.
* ``platform_audit``: one row per operator or CLI action, ids and codes only.
  ``subject_customer_id`` has no foreign key, so a row outlives its customer.
* Both have row level security ENABLED and NO policy, and no grants at all: the
  app role sees nothing, and only the table owner (the CLI, the functions
  below) touches them.
* The role ``kaagaz_operator``: NOLOGIN, NOBYPASSRLS, no table privileges.
  The API takes it (``SET LOCAL ROLE``, ``db.engine.OperatorDatabase``) only on
  /api/operator. It may run the ``kaagaz_op_*`` functions and nothing else
  (every older SECURITY DEFINER function is revoked from PUBLIC).
* The ``kaagaz_op_*`` functions: SECURITY DEFINER with an empty search path,
  every table name schema-qualified. Each that decides an outcome writes its own
  ``platform_audit`` row in the same statement. Only ``kaagaz_operator`` may
  run them, never PUBLIC or ``kaagaz_app``.

Every new table and column is commented (0007's rule). The downgrade drops the
functions and tables (operators and their audit rows are lost; customer data
is untouched). The role stays, like ``kaagaz_app``: roles belong to the server.

Revision ID: 0015
Revises: 0014 (customer status)
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

OPERATOR_ROLE = "kaagaz_operator"
_RECORD = "INSERT INTO public.platform_audit (operator_id, action, target_type, target_id)"

COMMENTS = {
    "operators": (
        "Platform operators (super admins). Not customer users: they belong to no customer and"
        " never see document content. Created only by the command line.",
        {
            "id": "Primary key.",
            "email": "Sign-in name, lower case, unique among operators.",
            "display_name": "Name shown in the operator console.",
            "password_hash": "scrypt hash of the password, never the password.",
            "totp_secret_enc": "Two-step secret, sealed with TOTP_ENCRYPTION_KEY. NULL until set up.",
            "totp_enabled_at": "When two-step sign-in was set up. NULL = no session can start yet.",
            "totp_last_step": "Last accepted 30-second code step, so a code works once.",
            "failed_logins": "Failed sign-in attempts in a row.",
            "locked_until": "Sign-in is refused until this time. NULL = not locked.",
            "session_version": "Raised to end every session (sign-out, 2FA reset).",
            "disabled": "A disabled operator cannot sign in and every session ends.",
            "last_login_at": "Last successful sign-in.",
            "created_at": "When the operator was created.",
        },
    ),
    "platform_audit": (
        "One row per platform operator or command-line action. Ids and codes only, never content.",
        {
            "id": "Primary key, in order of writing.",
            "operator_id": "operators.id of who acted. NULL = the command line or a failed sign-in.",
            "action": "What happened, a short code such as operator.login.",
            "subject_customer_id": "customers.id it was about (no foreign key: it outlives the customer).",
            "target_type": "Kind of thing acted on, such as operator or customer.",
            "target_id": "Id of the thing acted on.",
            "created_at": "When it happened.",
        },
    ),
}

FUNCTIONS = {
    # Count one sign-in attempt before the password is checked (as users.begin_attempt):
    # by e-mail (sign-in) or by id (enrolment). No row: unknown or locked.
    "kaagaz_op_begin_login(text, uuid, timestamptz, integer, timestamptz)": """
    CREATE FUNCTION public.kaagaz_op_begin_login(
        p_email text, p_operator uuid, p_now timestamptz, p_max_failures integer, p_lock_end timestamptz
    )
    RETURNS TABLE (operator_id uuid, email text, display_name text, password_hash text, disabled boolean,
                   session_version integer, totp_secret_enc text, totp_enabled boolean,
                   totp_last_step bigint, failed_logins integer)
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS
    $$
        UPDATE public.operators AS o SET
            failed_logins = CASE WHEN o.locked_until <= p_now THEN 1 ELSE o.failed_logins + 1 END,
            locked_until = CASE
                WHEN (CASE WHEN o.locked_until <= p_now THEN 1 ELSE o.failed_logins + 1 END) >= p_max_failures
                THEN p_lock_end ELSE NULL END
        WHERE o.id = coalesce(p_operator, (SELECT x.id FROM public.operators x WHERE x.email = lower(p_email)))
          AND (o.locked_until IS NULL OR o.locked_until <= p_now)
        RETURNING o.id, o.email, o.display_name, o.password_hash, o.disabled, o.session_version,
                  o.totp_secret_enc, o.totp_enabled_at IS NOT NULL, o.totp_last_step, o.failed_logins
    $$
    """,
    # A refused attempt: one row, operator.locked when it started the lock.
    "kaagaz_op_login_failed(uuid, boolean)": """
    CREATE FUNCTION public.kaagaz_op_login_failed(p_operator uuid, p_locked boolean) RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS
    $$
        INSERT INTO public.platform_audit (action, target_type, target_id)
        SELECT CASE WHEN p_locked THEN 'operator.locked' ELSE 'operator.login_failed' END,
               'operator', o.id::text
        FROM public.operators o WHERE o.id = p_operator
    $$
    """,
    # A right password and code: the code's step is used up (never twice), the count
    # is cleared. The session version, or NULL (code used, disabled, not set up).
    "kaagaz_op_login_succeeded(uuid, bigint, timestamptz)": f"""
    CREATE FUNCTION public.kaagaz_op_login_succeeded(p_operator uuid, p_step bigint, p_now timestamptz)
    RETURNS integer
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS
    $$
        WITH signed AS (
            UPDATE public.operators AS o
            SET totp_last_step = p_step, failed_logins = 0, locked_until = NULL, last_login_at = p_now
            WHERE o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL
              AND (o.totp_last_step IS NULL OR o.totp_last_step < p_step)
            RETURNING o.id, o.session_version
        ), audited AS (
            {_RECORD} SELECT s.id, 'operator.login', 'operator', s.id::text FROM signed s
        )
        SELECT s.session_version FROM signed s
    $$
    """,
    # Two-step set up with its first code; only while not set up, enabled and on the
    # enrol token's session version, so a token works once.
    "kaagaz_op_enrol(uuid, text, bigint, timestamptz, integer)": f"""
    CREATE FUNCTION public.kaagaz_op_enrol(
        p_operator uuid, p_secret_enc text, p_step bigint, p_now timestamptz, p_version integer
    ) RETURNS integer
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS
    $$
        WITH enrolled AS (
            UPDATE public.operators AS o
            SET totp_secret_enc = p_secret_enc, totp_enabled_at = p_now, totp_last_step = p_step,
                failed_logins = 0, locked_until = NULL, last_login_at = p_now
            WHERE o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NULL
              AND o.session_version = p_version
            RETURNING o.id, o.session_version
        ), audited AS (
            {_RECORD} SELECT e.id, 'operator.enrol', 'operator', e.id::text FROM enrolled e
        )
        SELECT e.session_version FROM enrolled e
    $$
    """,
    # Every request: the operator behind a session, only while enabled and set up.
    "kaagaz_op_session(uuid)": """
    CREATE FUNCTION public.kaagaz_op_session(p_operator uuid)
    RETURNS TABLE (email text, display_name text, session_version integer)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS
    $$
        SELECT o.email, o.display_name, o.session_version FROM public.operators o
        WHERE o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL
    $$
    """,
    # Sign-out ends every session of this operator.
    "kaagaz_op_logout(uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_logout(p_operator uuid) RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS
    $$
        WITH ended AS (
            UPDATE public.operators AS o SET session_version = o.session_version + 1
            WHERE o.id = p_operator RETURNING o.id
        )
        {_RECORD} SELECT e.id, 'operator.logout', 'operator', e.id::text FROM ended e
    $$
    """,
}


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


UPGRADE = [
    f"""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{OPERATOR_ROLE}') THEN
            CREATE ROLE {OPERATOR_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
        END IF;
        -- The connecting user must be able to SET ROLE to it.
        EXECUTE format('GRANT {OPERATOR_ROLE} TO %I', current_user);
    END
    $$
    """,
    f"GRANT USAGE ON SCHEMA public TO {OPERATOR_ROLE}",
    """
    CREATE TABLE operators (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email text NOT NULL UNIQUE CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
        display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 100),
        password_hash text NOT NULL,
        totp_secret_enc text NULL,
        totp_enabled_at timestamptz NULL,
        totp_last_step bigint NULL,
        failed_logins integer NOT NULL DEFAULT 0 CHECK (failed_logins >= 0),
        locked_until timestamptz NULL,
        session_version integer NOT NULL DEFAULT 1 CHECK (session_version >= 1),
        disabled boolean NOT NULL DEFAULT false,
        last_login_at timestamptz NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT operators_totp_check CHECK ((totp_enabled_at IS NULL) OR (totp_secret_enc IS NOT NULL))
    )
    """,
    """
    CREATE TABLE platform_audit (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        operator_id uuid NULL REFERENCES operators (id),
        action text NOT NULL CHECK (action ~ '^[a-z_.]{1,64}$'),
        subject_customer_id uuid NULL,
        target_type text NULL CHECK (target_type ~ '^[a-z_]{1,32}$'),
        target_id text NULL CHECK (length(target_id) <= 64),
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE operators ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE platform_audit ENABLE ROW LEVEL SECURITY",
    *[
        statement
        for table, (table_comment, columns) in COMMENTS.items()
        for statement in (
            f"COMMENT ON TABLE {table} IS {_literal(table_comment)}",
            *[f"COMMENT ON COLUMN {table}.{column} IS {_literal(text)}" for column, text in columns.items()],
        )
    ],
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
    "DROP TABLE IF EXISTS platform_audit, operators",
    f"REVOKE USAGE ON SCHEMA public FROM {OPERATOR_ROLE}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
