"""Track resumable uploads until they finish or expire.

Revision ID: 0045
Revises: 0044
"""

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    CREATE TABLE upload_sessions (
        id uuid PRIMARY KEY,
        customer_id uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
        user_id uuid NOT NULL,
        storage_key text NOT NULL,
        storage_upload_id text NOT NULL,
        file_size bigint NOT NULL CHECK (file_size > 0),
        part_count integer NOT NULL CHECK (part_count BETWEEN 1 AND 10000),
        metadata jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        UNIQUE (customer_id, id)
    )
    """,
    "CREATE INDEX upload_sessions_expiry ON upload_sessions(expires_at)",
    "CREATE INDEX upload_sessions_owner_size ON upload_sessions(customer_id, user_id, file_size, expires_at)",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON upload_sessions TO kaagaz_app",
    "ALTER TABLE upload_sessions ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON upload_sessions
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
    """
    CREATE FUNCTION kaagaz_expired_upload_sessions(p_before timestamptz, p_limit integer)
    RETURNS TABLE(customer_id uuid, id uuid, storage_key text, storage_upload_id text)
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS
    $$
        SELECT s.customer_id, s.id, s.storage_key, s.storage_upload_id
        FROM public.upload_sessions s
        WHERE s.expires_at <= p_before
        ORDER BY s.expires_at
        LIMIT least(greatest(p_limit, 1), 100)
    $$
    """,
    "REVOKE ALL ON FUNCTION kaagaz_expired_upload_sessions(timestamptz, integer) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION kaagaz_expired_upload_sessions(timestamptz, integer) TO kaagaz_app",
]

DOWNGRADE = [
    "DROP FUNCTION kaagaz_expired_upload_sessions(timestamptz, integer)",
    "DROP TABLE upload_sessions",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
