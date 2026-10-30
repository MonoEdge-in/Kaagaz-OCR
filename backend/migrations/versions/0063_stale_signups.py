"""A sign-up that never confirmed its email stops holding it (#457 review).

* ``kaagaz_release_stale_signup(email)``: a password sign-up that is still ``pending``, whose
  only user has this e-mail, never proved it (``email_verified_at`` NULL) and was made more
  than 24 hours ago (the confirm link's life) is deleted, so the e-mail's real owner can sign
  up or be added. Anything else (a fresh sign-up, a confirmed one, an active organisation, one
  with a second user) is never touched. SECURITY DEFINER with an empty search path, revoked
  from PUBLIC, the app's only; it answers true when it deleted one. The customer's users, links
  and audit rows go with it (their keys cascade); a pending customer has nothing else.

The downgrade drops the function.

Revision ID: 0063
Revises: 0062 (connected accounts)
"""

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
RELEASE = "kaagaz_release_stale_signup(text)"


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
    RELEASE_SQL,
    f"COMMENT ON FUNCTION public.{RELEASE} IS {_literal(RELEASE_COMMENT)}",
    f"REVOKE ALL ON FUNCTION public.{RELEASE} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION public.{RELEASE} TO {APP_ROLE}",
]

DOWNGRADE = [f"DROP FUNCTION public.{RELEASE}"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
