"""Keycloak tables pass the isolation rules (#738).

``keycloak_flows`` carries a nullable ``customer_id``, so it gets the ``customer_only`` policy like
every other table with that column (a login flow has no customer and is reached only through the
flow functions, which run with owner rights). ``keycloak_identities`` and ``keycloak_sessions`` also
get the RESTRICTIVE ``own_user_only`` policy, so a user transaction can only ever see its own link
and its own session rows, never a colleague's. The owner-rights functions are unaffected.

Revision ID: 0104
Revises: 0103 (Keycloak identity)
"""

from alembic import op

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None

OWN_USER = "user_id = NULLIF(current_setting('kaagaz.user_id', true), '')::uuid"

UPGRADE = [
    """
    CREATE POLICY customer_only ON public.keycloak_flows
    USING (customer_id = public.kaagaz_current_customer())
    WITH CHECK (customer_id = public.kaagaz_current_customer())
    """,
    "COMMENT ON POLICY customer_only ON public.keycloak_flows IS 'No role reads flows directly; a bound flow is only ever its own customer''s, a login flow (no customer) is reached by the flow functions alone.'",
    f"CREATE POLICY own_user_only ON public.keycloak_identities AS RESTRICTIVE USING ({OWN_USER}) WITH CHECK ({OWN_USER})",
    f"CREATE POLICY own_user_only ON public.keycloak_sessions AS RESTRICTIVE USING ({OWN_USER}) WITH CHECK ({OWN_USER})",
    "COMMENT ON POLICY own_user_only ON public.keycloak_identities IS 'Must pass too: only the current user''s own link is ever visible; with no user set, none.'",
    "COMMENT ON POLICY own_user_only ON public.keycloak_sessions IS 'Must pass too: only the current user''s own sessions are ever visible; with no user set, none.'",
]

DOWNGRADE = [
    "DROP POLICY own_user_only ON public.keycloak_sessions",
    "DROP POLICY own_user_only ON public.keycloak_identities",
    "DROP POLICY customer_only ON public.keycloak_flows",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
