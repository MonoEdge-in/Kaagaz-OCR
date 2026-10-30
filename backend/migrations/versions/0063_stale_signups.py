"""A sign-up that never confirmed its email stops holding it, and three operator fixes (#457 review).

* ``kaagaz_release_stale_signup(email)``: a password sign-up that is still ``pending``, whose
  only user has this e-mail, never proved it (``email_verified_at`` NULL) and was made more
  than 24 hours ago (the confirm link's life) is deleted, so the e-mail's real owner can sign
  up or be added. Anything else (a fresh sign-up, a confirmed one, an active organisation, one
  with a second user) is never touched. SECURITY DEFINER with an empty search path, revoked
  from PUBLIC, the app's only; it answers true when it deleted one. The customer's users, links
  and audit rows go with it (their keys cascade); a pending customer has nothing else.
* ``kaagaz_op_set_customer_status`` (0016, 0061) answers ``pending`` to disable on a pending
  customer too, not only to enable: Disable then Enable would route round Approve and its
  confirmed-email rule. A pending customer can do nothing, and a stale one is released above.
* ``kaagaz_op_approve_customer`` (0061) clears its users' sign-in failures and locks, as
  enable does (0016), so a waiting sign-up's admin is never locked out on approval.
* ``kaagaz_op_change_admin`` (0017) audits a two-step reset as the spec's ``auth.totp_reset``,
  not ``user.reset_totp`` (the admin screen's and the CLI's too).

Each rewrite reads the live body back and changes one line exactly once (0061's way).
The downgrade puts the three lines back and drops the function.

Revision ID: 0063
Revises: 0062 (connected accounts)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def _load_0061() -> ModuleType:
    path = Path(__file__).with_name("0061_email.py")
    spec = importlib.util.spec_from_file_location("migration_0061_for_0063", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0061 = _load_0061()
_REWRITE = _0061._REWRITE
APP_ROLE = "kaagaz_app"
RELEASE = "kaagaz_release_stale_signup(text)"
SET_STATUS = _0061.SET_STATUS
APPROVE = _0061.APPROVE
CHANGE_ADMIN = "kaagaz_op_change_admin(uuid, uuid, uuid, text, bigint)"
# (function, the line as it is, the line as it becomes)
LINES = [
    (
        SET_STATUS,
        "IF v_before = 'pending' AND p_status = 'active' THEN outcome := 'pending';",
        "IF v_before = 'pending' THEN outcome := 'pending';",
    ),
    (
        APPROVE,
        "UPDATE public.customers AS c SET status = 'active' WHERE c.id = p_customer;",
        "UPDATE public.customers AS c SET status = 'active' WHERE c.id = p_customer;"
        " UPDATE public.users AS u SET failed_logins = 0, locked_until = NULL WHERE u.customer_id = p_customer;",
    ),
    (CHANGE_ADMIN, "WHEN 'reset_totp' THEN 'user.reset_totp'", "WHEN 'reset_totp' THEN 'auth.totp_reset'"),
]


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


RELEASE_COMMENT = (
    "Delete a pending sign-up older than 24 hours whose only user has this e-mail and never"
    " proved it, so the e-mail is free again. True when one was deleted."
)
RELEASE_SQL = """
    CREATE FUNCTION public.kaagaz_release_stale_signup(p_email text) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid;
    BEGIN
        SELECT c.id INTO v_customer
        FROM public.users u JOIN public.customers c ON c.id = u.customer_id
        WHERE u.email = lower(p_email) AND u.email_verified_at IS NULL
          AND c.status = 'pending' AND c.created_at < now() - interval '24 hours'
        FOR UPDATE OF c;
        IF NOT FOUND OR (SELECT count(*) FROM public.users x WHERE x.customer_id = v_customer) <> 1 THEN
            RETURN false;
        END IF;
        DELETE FROM public.customers c WHERE c.id = v_customer;
        RETURN true;
    END
    $$
"""

UPGRADE = [
    *[_REWRITE.format(function=function, old=old, new=new) for function, old, new in LINES],
    RELEASE_SQL,
    f"COMMENT ON FUNCTION public.{RELEASE} IS {_literal(RELEASE_COMMENT)}",
    f"REVOKE ALL ON FUNCTION public.{RELEASE} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION public.{RELEASE} TO {APP_ROLE}",
]

DOWNGRADE = [
    f"DROP FUNCTION public.{RELEASE}",
    *[_REWRITE.format(function=function, old=new, new=old) for function, old, new in LINES],
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
