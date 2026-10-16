"""Moving the platform operator's two-step to a new device (Tushar, 16 Oct 2026: a
handover to another person's phone). Two-step is never off. No table changes: two
``kaagaz_op_*`` functions under 0015's rules (SECURITY DEFINER, empty search path,
EXECUTE for ``kaagaz_operator`` only).

* ``kaagaz_op_move_check``: the password and the CURRENT code were right (the attempt
  counted first by ``kaagaz_op_begin_login``): the step is used up, the count cleared.
* ``kaagaz_op_move_totp``: a code from the NEW secret (sealed in the signed move token
  until now) replaces the secret in one statement and raises the session version (every
  other browser signed out), only while that version is the token's: a token works
  once. One ``operator.totp_moved`` row, ids only (an audit action has no digits).

The downgrade drops the two functions.

Revision ID: 0031
Revises: 0030 (word review)
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

OPERATOR_ROLE = "kaagaz_operator"
# As 0016's (a migration can't import another).
_ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"

FUNCTIONS = {
    "kaagaz_op_move_check(uuid, bigint)": (
        f"""
    CREATE FUNCTION public.kaagaz_op_move_check(p_operator uuid, p_step bigint) RETURNS boolean
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
        WITH used AS (
            UPDATE public.operators AS o SET totp_last_step = p_step, failed_logins = 0, locked_until = NULL
            WHERE {_ENABLED} AND p_step IS NOT NULL
              AND (o.totp_last_step IS NULL OR o.totp_last_step < p_step)
            RETURNING o.id
        )
        SELECT EXISTS (SELECT 1 FROM used)
    $$
    """,
        "Operator: the password and current code were right for a two-step move. Uses up the"
        " step and clears the sign-in count.",
    ),
    "kaagaz_op_move_totp(uuid, integer, text, bigint, timestamptz)": (
        f"""
    CREATE FUNCTION public.kaagaz_op_move_totp(
        p_operator uuid, p_version integer, p_secret text, p_step bigint, p_now timestamptz
    )
    RETURNS integer
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
        WITH moved AS (
            UPDATE public.operators AS o
            SET totp_secret_enc = p_secret, totp_last_step = p_step, totp_enabled_at = p_now,
                session_version = o.session_version + 1
            WHERE {_ENABLED} AND o.session_version = p_version AND p_secret <> '' AND p_step IS NOT NULL
            RETURNING o.id, o.session_version
        ), audited AS (
            INSERT INTO public.platform_audit (operator_id, action, target_type, target_id)
            SELECT m.id, 'operator.totp_moved', 'operator', m.id::text FROM moved m
        )
        SELECT m.session_version FROM moved m
    $$
    """,
        "Operator: replace the two-step secret with a confirmed new one, end every other"
        " session. The new session version, or NULL.",
    ),
}


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


UPGRADE = [
    statement
    for signature, (create, comment) in FUNCTIONS.items()
    for statement in (
        create,
        f"COMMENT ON FUNCTION public.{signature} IS {_literal(comment)}",
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
