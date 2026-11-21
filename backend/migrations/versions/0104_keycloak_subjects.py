"""Let the API read which Keycloak accounts one customer's people are linked to (#737).

Admin actions (disable, enable, unlock, reset two-step, resend the setup email) must act on the
exact Keycloak account that is linked, never on whoever holds the same email in Keycloak. The
application role has no table access to ``keycloak_identities``, so this one owner-rights read
returns the current customer's active links, taken from the transaction context and never from a
caller-supplied customer id.

Revision ID: 0104
Revises: 0103 (Keycloak identity)
"""

from alembic import op

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
WORKER_ROLE = "kaagaz_worker"
SIGNATURE = "kaagaz_keycloak_subjects()"

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
]

DOWNGRADE = [f"DROP FUNCTION public.{SIGNATURE}"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
