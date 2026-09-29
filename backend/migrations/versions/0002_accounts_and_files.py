"""Accounts and files: sign-in safety, folders, tags, trash and versions (PRD 1, WP1.2).

Everything follows 0001's rules: every customer table carries ``customer_id``,
has the ``customer_only`` policy, and points at its parents with
(customer_id, id) foreign keys, so a row can never hang under another
customer's parent. Foreign-key checks run without RLS, which is why the
composite keys matter here too.

PROVISIONAL (council 2026-09-29, for Vrushit to confirm):
* ``tags UNIQUE (customer_id, id)`` and the composite ``document_tags`` keys.
* The extra indexes: ``document_tags_tag``, ``documents_family_version``,
  ``documents_trash``, ``corrections_document``, and ``documents_folder``
  without the PRD's partial condition (so it also serves the foreign-key check
  when a folder is deleted, and an ordered page per folder).
* ``kaagaz_customer_ids_with_trash()``: the worker's trash purge needs the
  customers that have old trash, which RLS hides from it otherwise.
* NO ACTION instead of RESTRICT on the folder keys: the same for a single
  delete, but checked at the end of the statement, so deleting a customer can
  still cascade through its folders.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
NEW_TABLES = ("folders", "tags", "document_tags")

UPGRADE = [
    # --- users: lockout, sessions, temporary passwords, two-step codes -------
    """
    ALTER TABLE users
        ADD COLUMN failed_logins integer NOT NULL DEFAULT 0 CHECK (failed_logins >= 0),
        ADD COLUMN locked_until timestamptz NULL,
        ADD COLUMN session_version integer NOT NULL DEFAULT 1 CHECK (session_version >= 1),
        ADD COLUMN must_change_password boolean NOT NULL DEFAULT false,
        ADD COLUMN totp_secret_enc text NULL,
        ADD COLUMN totp_enabled_at timestamptz NULL,
        ADD COLUMN totp_last_step bigint NULL,
        ADD COLUMN last_login_at timestamptz NULL
    """,
    # --- folders -------------------------------------------------------------
    """
    CREATE TABLE folders (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        parent_id uuid NULL,
        name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
        created_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, id),
        CHECK (parent_id IS NULL OR parent_id <> id),
        FOREIGN KEY (customer_id, parent_id) REFERENCES folders (customer_id, id),
        FOREIGN KEY (customer_id, created_by) REFERENCES users (customer_id, id)
    )
    """,
    """
    CREATE UNIQUE INDEX folders_sibling_name
    ON folders (customer_id, parent_id, lower(name)) NULLS NOT DISTINCT
    """,
    # --- tags ------------------------------------------------------------------
    """
    CREATE TABLE tags (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        name text NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
        created_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, id),
        FOREIGN KEY (customer_id, created_by) REFERENCES users (customer_id, id)
    )
    """,
    "CREATE UNIQUE INDEX tags_name ON tags (customer_id, lower(name))",
    """
    CREATE TABLE document_tags (
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        tag_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (document_id, tag_id),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, tag_id) REFERENCES tags (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX document_tags_tag ON document_tags (customer_id, tag_id, document_id)",
    # --- documents: folder, trash, versions -------------------------------------
    """
    ALTER TABLE documents
        ADD COLUMN folder_id uuid NULL,
        ADD COLUMN deleted_at timestamptz NULL,
        ADD COLUMN deleted_by uuid NULL,
        ADD COLUMN family_id uuid NULL,
        ADD COLUMN version_no integer NOT NULL DEFAULT 1 CHECK (version_no >= 1),
        ADD COLUMN is_current boolean NOT NULL DEFAULT true
    """,
    # Runs as the table owner, and 0001 has no FORCE ROW LEVEL SECURITY, so this
    # sees every customer's rows: each existing document is its own first version.
    "UPDATE documents SET family_id = id",
    "ALTER TABLE documents ALTER COLUMN family_id SET NOT NULL",
    """
    ALTER TABLE documents ADD CONSTRAINT documents_folder_fk
        FOREIGN KEY (customer_id, folder_id) REFERENCES folders (customer_id, id)
    """,
    """
    ALTER TABLE documents ADD CONSTRAINT documents_deleted_by_fk
        FOREIGN KEY (customer_id, deleted_by) REFERENCES users (customer_id, id)
    """,
    "CREATE UNIQUE INDEX documents_current ON documents (customer_id, family_id) WHERE is_current",
    """
    CREATE UNIQUE INDEX documents_family_version
    ON documents (customer_id, family_id, version_no)
    """,
    """
    CREATE INDEX documents_folder
    ON documents (customer_id, folder_id, created_at DESC, id DESC)
    """,
    """
    CREATE INDEX documents_trash ON documents (customer_id, deleted_at)
    WHERE deleted_at IS NOT NULL
    """,
    "CREATE INDEX corrections_document ON corrections (customer_id, document_id)",
    # --- the one owner-rights helper for the trash purge ------------------------
    """
    CREATE FUNCTION kaagaz_customer_ids_with_trash(p_before timestamptz)
    RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS
    $$ SELECT DISTINCT d.customer_id FROM public.documents d WHERE d.deleted_at < p_before $$
    """,
    "REVOKE ALL ON FUNCTION kaagaz_customer_ids_with_trash(timestamptz) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION kaagaz_customer_ids_with_trash(timestamptz) TO {APP_ROLE}",
    # --- what the app role may do, and row level security -------------------------
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON folders, tags, document_tags TO {APP_ROLE}",
    *[f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" for table in NEW_TABLES],
    *[
        f"""
        CREATE POLICY customer_only ON {table}
        USING (customer_id = kaagaz_current_customer())
        WITH CHECK (customer_id = kaagaz_current_customer())
        """
        for table in NEW_TABLES
    ],
]

DOWNGRADE = [
    "DROP FUNCTION IF EXISTS kaagaz_customer_ids_with_trash(timestamptz)",
    "DROP INDEX IF EXISTS corrections_document",
    "DROP INDEX IF EXISTS documents_trash",
    "DROP INDEX IF EXISTS documents_folder",
    "DROP INDEX IF EXISTS documents_family_version",
    "DROP INDEX IF EXISTS documents_current",
    "ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_deleted_by_fk",
    "ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_folder_fk",
    """
    ALTER TABLE documents
        DROP COLUMN IF EXISTS is_current,
        DROP COLUMN IF EXISTS version_no,
        DROP COLUMN IF EXISTS family_id,
        DROP COLUMN IF EXISTS deleted_by,
        DROP COLUMN IF EXISTS deleted_at,
        DROP COLUMN IF EXISTS folder_id
    """,
    "DROP TABLE IF EXISTS document_tags",
    "DROP TABLE IF EXISTS tags",
    "DROP TABLE IF EXISTS folders",
    """
    ALTER TABLE users
        DROP COLUMN IF EXISTS last_login_at,
        DROP COLUMN IF EXISTS totp_last_step,
        DROP COLUMN IF EXISTS totp_enabled_at,
        DROP COLUMN IF EXISTS totp_secret_enc,
        DROP COLUMN IF EXISTS must_change_password,
        DROP COLUMN IF EXISTS session_version,
        DROP COLUMN IF EXISTS locked_until,
        DROP COLUMN IF EXISTS failed_logins
    """,
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
