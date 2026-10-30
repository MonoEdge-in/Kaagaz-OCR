"""Email (#457 phase 0): links, proven e-mails, and sign-ups that wait for an operator.

* ``email_links``: one row per emailed link (confirm your email, an invite, a password
  reset), stored only as the SHA-256 hash of its 32 random bytes, with its purpose, expiry
  and used time. ``customer_id`` on every row, the ``customer_only`` policy (0002's), and a
  (customer_id, user_id) key to ``users`` that goes with the user. The app may read and add
  rows, and update ``used_at`` only (a column grant): never a hash, purpose or expiry.
* ``kaagaz_email_link_lookup(hash)``: a clicked link arrives with no customer, so it is
  found by its hash through this SECURITY DEFINER function (empty search path, revoked from
  PUBLIC, the app's only), as signing in finds an e-mail. One row: ids, purpose and times.
* ``users.email_verified_at``: when the user proved their e-mail (a confirm, invite or reset
  link). Every existing user keeps NULL, so they are asked to confirm at their next sign-in.
* ``customers.status`` gains ``pending``: a password sign-up waits for an operator's
  approval. 0014's CHECK ``(status = 'active') = (disabled_at IS NULL)`` becomes
  ``(status IN ('active', 'pending')) = (disabled_at IS NULL)``: the same for active,
  disabled and deleting, and a pending customer has no ``disabled_at``; ``delete_after``'s
  CHECK already keeps it NULL unless deleting. Everything that asks for ``active`` (sign-in,
  sessions, the worker, the sweeps) already refuses a pending customer.
* ``kaagaz_create_pending_customer``: ``kaagaz_create_customer`` (0001) for self sign-up
  with email on, its customer made ``pending``; the app still may not update a status (0014).
* ``kaagaz_op_customers`` (0035's) gains ``admin_email_confirmed``: one of its admins proved
  their e-mail, which the operator's Approve needs.
* ``kaagaz_op_approve_customer``: the operator's Approve, a pending customer whose admin
  proved their e-mail made active, audited ``org.approved`` in both logs (0016's rules).
  Outcomes: done, missing, not_pending, unconfirmed, signed_out.
* ``kaagaz_op_set_customer_status`` (0016) answers ``pending`` to enable on a pending
  customer, so Approve's e-mail rule cannot be routed round (0060's rewrite: the body read
  back and one line changed exactly once).

The downgrade refuses while any customer is pending (0014's pattern: approve or disable
it first), then puts every object back as 0060 left it; the links and proofs are lost.

Revision ID: 0061
Revises: 0060 (Dynamic's count)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def _load_0035() -> ModuleType:
    path = Path(__file__).with_name("0035_mfa_required.py")
    spec = importlib.util.spec_from_file_location("migration_0035_for_0061", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_OLD = _load_0035()
APP_ROLE = "kaagaz_app"
OPERATOR_ROLE = _OLD.OPERATOR_ROLE
_ENABLED = _OLD._ENABLED
_ACTOR = _OLD._ACTOR
_literal = _OLD._literal

LOOKUP = "kaagaz_email_link_lookup(bytea)"
CREATE_PENDING = "kaagaz_create_pending_customer(text, text, text, text)"
CUSTOMERS = _OLD.CUSTOMERS
APPROVE = "kaagaz_op_approve_customer(uuid, uuid)"
SET_STATUS = "kaagaz_op_set_customer_status(uuid, uuid, text, timestamptz, bigint)"
# Enable on a pending customer is refused ('pending'): only Approve makes it active.
STATUS_OLD = "IF v_before = 'deleting' OR v_before = p_status THEN"
STATUS_NEW = (
    "IF v_before = 'pending' AND p_status = 'active' THEN outcome := 'pending'; RETURN; END IF; " + STATUS_OLD
)

TABLE_COMMENT = (
    "Emailed links (confirm your email, an invite, a password reset), each stored only as the"
    " hash of its token. Single use: used_at is set when it acts, or when a newer link of the"
    " same purpose is sent to the same user."
)
COLUMN_COMMENTS = {
    "id": "The link's id.",
    "customer_id": "The customer of the user the link is for.",
    "user_id": "The user the link is for.",
    "purpose": "confirm (prove the e-mail, 24 hours), invite (choose a password, 7 days) or reset"
    " (a forgotten password, 30 minutes).",
    "token_hash": "SHA-256 of the 32 random bytes the email carries; the token itself is never stored.",
    "created_at": "When the link was made and sent.",
    "expires_at": "After this time the link no longer works.",
    "used_at": "When the link was used, or replaced by a newer one. NULL while it can still be used.",
}
EMAIL_VERIFIED_COMMENT = (
    "When the user proved their e-mail by opening a confirm, invite or reset link. NULL: not"
    " yet (users made before email was sent are asked to confirm)."
)
STATUS_COMMENT = (
    "active (its users sign in and its files are read), pending (a sign-up waiting for the"
    " platform operator's approval: no sign-in, no session, no reading), disabled (no sign-in,"
    " no session, no reading; changed only by the platform operator) or deleting (disabled, and"
    " removed for good after delete_after)."
)
STATUS_COMMENT_0014 = (
    "active (its users sign in and its files are read), disabled (no sign-in, no session,"
    " no reading; changed only by the platform operator) or deleting (disabled, and"
    " removed for good after delete_after)."
)
DISABLED_AT_COMMENT = "When the customer was disabled. NULL while it is active or pending."
DISABLED_AT_COMMENT_0014 = "When the customer was disabled. NULL while it is active."
FUNCTION_COMMENTS = {
    LOOKUP: "Find an emailed link by the hash of its token (it arrives with no customer): ids,"
    " purpose and times only.",
    CREATE_PENDING: "Self sign-up with email on: a customer waiting for the operator's approval,"
    " and its first admin.",
    CUSTOMERS: "Operator: every organisation with its status, counts only, its auto-filing grant"
    " and switch, and whether one of its admins proved their e-mail.",
    APPROVE: "Operator: approve one pending organisation whose admin proved their e-mail."
    " Audited in both logs.",
}

LOOKUP_SQL = """
    CREATE FUNCTION public.kaagaz_email_link_lookup(p_hash bytea)
    RETURNS TABLE (link_id uuid, customer_id uuid, user_id uuid, purpose text,
                   expires_at timestamptz, used_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT l.id, l.customer_id, l.user_id, l.purpose, l.expires_at, l.used_at
        FROM public.email_links l WHERE l.token_hash = p_hash
    $$
"""
CREATE_PENDING_SQL = """
    CREATE FUNCTION public.kaagaz_create_pending_customer(
        p_name text, p_admin_email text, p_admin_name text, p_password_hash text
    ) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        new_id uuid;
    BEGIN
        INSERT INTO public.customers (name, status) VALUES (p_name, 'pending') RETURNING id INTO new_id;
        INSERT INTO public.users (customer_id, email, display_name, role, password_hash)
        VALUES (new_id, lower(p_admin_email), p_admin_name, 'admin', p_password_hash);
        RETURN new_id;
    END
    $$
"""
CUSTOMERS_NOW = _OLD._CUSTOMERS.format(
    columns=", admin_email_confirmed boolean",
    values=", EXISTS (SELECT 1 FROM public.users x WHERE x.customer_id = c.id AND x.role = 'admin'"
    " AND x.email_verified_at IS NOT NULL)",
    enabled=_ENABLED,
)
APPROVE_SQL = f"""
    CREATE FUNCTION public.kaagaz_op_approve_customer(
        p_operator uuid, p_customer uuid, OUT outcome text, OUT result_id uuid
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_status text;
    BEGIN
        {_ACTOR}
        SELECT c.status INTO v_status FROM public.customers c WHERE c.id = p_customer FOR UPDATE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        result_id := p_customer;
        IF v_status <> 'pending' THEN
            outcome := 'not_pending';
            RETURN;
        END IF;
        PERFORM 1 FROM public.users u
        WHERE u.customer_id = p_customer AND u.role = 'admin' AND u.email_verified_at IS NOT NULL;
        IF NOT FOUND THEN
            outcome := 'unconfirmed';
            RETURN;
        END IF;
        UPDATE public.customers AS c SET status = 'active' WHERE c.id = p_customer;
        INSERT INTO public.audit_log (customer_id, actor_user_id, action, target_type, target_id, details)
        VALUES (p_customer, NULL, 'org.approved', 'customer', p_customer::text, jsonb_build_object('by', 'operator'));
        INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id)
        VALUES (p_operator, 'org.approved', p_customer, 'customer', p_customer::text);
        outcome := 'done';
    END
    $$
"""

# ``old`` replaced by ``new`` in the live body, which must hold it exactly once (0060's).
_REWRITE = """
DO $body$
DECLARE
    v_def text := pg_get_functiondef('public.{function}'::regprocedure);
BEGIN
    IF coalesce(array_length(string_to_array(v_def, $k${old}$k$), 1), 0) <> 2 THEN
        RAISE EXCEPTION '{function} does not hold the line to change exactly once';
    END IF;
    EXECUTE replace(v_def, $k${old}$k$, $k${new}$k$);
END
$body$
"""


def _function(signature: str, create: str, role: str) -> list[str]:
    return [
        create,
        f"COMMENT ON FUNCTION public.{signature} IS {_literal(FUNCTION_COMMENTS[signature])}",
        f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION public.{signature} TO {role}",
    ]


UPGRADE = [
    # --- customers: pending ----------------------------------------------------------------
    """
    ALTER TABLE customers
        DROP CONSTRAINT customers_status_check,
        DROP CONSTRAINT customers_disabled_at_check,
        ADD CONSTRAINT customers_status_check CHECK (status IN ('active', 'pending', 'disabled', 'deleting')),
        ADD CONSTRAINT customers_disabled_at_check
            CHECK ((status IN ('active', 'pending')) = (disabled_at IS NULL))
    """,
    f"COMMENT ON COLUMN customers.status IS {_literal(STATUS_COMMENT)}",
    f"COMMENT ON COLUMN customers.disabled_at IS {_literal(DISABLED_AT_COMMENT)}",
    # --- users: a proven e-mail ------------------------------------------------------------
    "ALTER TABLE users ADD COLUMN email_verified_at timestamptz NULL",
    f"COMMENT ON COLUMN users.email_verified_at IS {_literal(EMAIL_VERIFIED_COMMENT)}",
    # --- the links -------------------------------------------------------------------------
    """
    CREATE TABLE email_links (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        user_id uuid NOT NULL,
        purpose text NOT NULL CHECK (purpose IN ('confirm', 'invite', 'reset')),
        token_hash bytea NOT NULL UNIQUE CHECK (length(token_hash) = 32),
        created_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL CHECK (expires_at > created_at),
        used_at timestamptz NULL,
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX email_links_open ON email_links (customer_id, user_id, purpose) WHERE used_at IS NULL",
    f"GRANT SELECT, INSERT ON email_links TO {APP_ROLE}",
    f"GRANT UPDATE (used_at) ON email_links TO {APP_ROLE}",
    "ALTER TABLE email_links ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON email_links
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
    f"COMMENT ON TABLE email_links IS {_literal(TABLE_COMMENT)}",
    *[
        f"COMMENT ON COLUMN email_links.{column} IS {_literal(comment)}"
        for column, comment in COLUMN_COMMENTS.items()
    ],
    # --- the functions -------------------------------------------------------------------
    *_function(LOOKUP, LOOKUP_SQL, APP_ROLE),
    *_function(CREATE_PENDING, CREATE_PENDING_SQL, APP_ROLE),
    f"DROP FUNCTION public.{CUSTOMERS}",
    *_function(CUSTOMERS, CUSTOMERS_NOW, OPERATOR_ROLE),
    *_function(APPROVE, APPROVE_SQL, OPERATOR_ROLE),
    _REWRITE.format(function=SET_STATUS, old=STATUS_OLD, new=STATUS_NEW),
]

DOWNGRADE = [
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM customers WHERE status = 'pending') THEN
            RAISE EXCEPTION 'a customer is pending; approve or disable it before downgrading'
                USING ERRCODE = 'check_violation';
        END IF;
    END
    $$
    """,
    _REWRITE.format(function=SET_STATUS, old=STATUS_NEW, new=STATUS_OLD),
    f"DROP FUNCTION public.{APPROVE}",
    f"DROP FUNCTION public.{CUSTOMERS}",
    *_OLD._function(CUSTOMERS, _OLD.CUSTOMERS_NOW, _OLD.COMMENT_NOW),
    f"DROP FUNCTION public.{CREATE_PENDING}",
    f"DROP FUNCTION public.{LOOKUP}",
    "DROP TABLE email_links",
    "ALTER TABLE users DROP COLUMN email_verified_at",
    """
    ALTER TABLE customers
        DROP CONSTRAINT customers_status_check,
        DROP CONSTRAINT customers_disabled_at_check,
        ADD CONSTRAINT customers_status_check CHECK (status IN ('active', 'disabled', 'deleting')),
        ADD CONSTRAINT customers_disabled_at_check CHECK ((status = 'active') = (disabled_at IS NULL))
    """,
    f"COMMENT ON COLUMN customers.status IS {_literal(STATUS_COMMENT_0014)}",
    f"COMMENT ON COLUMN customers.disabled_at IS {_literal(DISABLED_AT_COMMENT_0014)}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
