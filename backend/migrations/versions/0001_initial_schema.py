"""The whole schema, with row level security on every customer table.

PROVISIONAL: the database design is Vrushit's (#1, #2, #12). This follows the
information list in docs/architecture.md (Data and customer separation).

Customer separation, two locks:

* Every table carries ``customer_id`` and has a policy comparing it with
  ``kaagaz_current_customer()`` (set per transaction by kaagaz.db.engine).
* The app works as the role ``kaagaz_app``: not a superuser, not the owner,
  no BYPASSRLS. The migration user owns the tables; only the two SECURITY
  DEFINER functions below run as the owner, and each does one narrow job.

Child rows point at their parent with (customer_id, id) foreign keys, so a
row can never hang under another customer's parent.

The ``vector(1024)`` column must match EMBEDDING_DIM.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"

CUSTOMER_TABLES = (
    "users",
    "documents",
    "pages",
    "reader_outputs",
    "pieces",
    "extracted_values",
    "corrections",
    "learned_corrections",
    "conversations",
    "messages",
    "feedback",
    "audit_log",
)

UPGRADE = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    f"""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
        END IF;
        -- The connecting user must be able to SET ROLE to the app role.
        EXECUTE format('GRANT {APP_ROLE} TO %I', current_user);
    END
    $$
    """,
    f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
    """
    CREATE FUNCTION kaagaz_current_customer() RETURNS uuid
    LANGUAGE sql STABLE AS
    $$ SELECT NULLIF(current_setting('kaagaz.customer_id', true), '')::uuid $$
    """,
    """
    CREATE FUNCTION kaagaz_touch() RETURNS trigger
    LANGUAGE plpgsql AS
    $$
    BEGIN
        NEW.updated_at := now();
        RETURN NEW;
    END
    $$
    """,
    # --- customers and users ------------------------------------------------
    """
    CREATE TABLE customers (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
        max_upload_mb integer NULL CHECK (max_upload_mb > 0),
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE users (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        email text NOT NULL UNIQUE
            CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
        display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 100),
        role text NOT NULL CHECK (role IN ('member', 'reviewer', 'admin')),
        password_hash text NOT NULL,
        disabled boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, id)
    )
    """,
    # --- documents and what was read from them ------------------------------
    """
    CREATE TABLE documents (
        id uuid PRIMARY KEY,
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 255),
        sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
        size_bytes bigint NOT NULL CHECK (size_bytes > 0),
        detected_type text NOT NULL
            CHECK (detected_type IN ('pdf', 'png', 'jpeg', 'tiff', 'zip', 'ole2', 'text')),
        status text NOT NULL DEFAULT 'received'
            CHECK (status IN ('received', 'clean', 'rejected', 'needs_reader', 'ready', 'failed')),
        failure_code text NULL CHECK (failure_code ~ '^[a-z_]{1,64}$'),
        page_count integer NULL CHECK (page_count >= 0),
        uploaded_by uuid NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, sha256),
        UNIQUE (customer_id, id),
        FOREIGN KEY (customer_id, uploaded_by) REFERENCES users (customer_id, id)
    )
    """,
    "CREATE INDEX documents_listing ON documents (customer_id, created_at DESC)",
    """
    CREATE FUNCTION kaagaz_keep_rejected() RETURNS trigger
    LANGUAGE plpgsql AS
    $$
    BEGIN
        -- A rejected file stays rejected: a rescan must never clear it.
        IF OLD.status = 'rejected' AND NEW.status <> 'rejected' THEN
            RAISE EXCEPTION 'rejected is final' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END
    $$
    """,
    """
    CREATE TRIGGER documents_keep_rejected BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION kaagaz_keep_rejected()
    """,
    """
    CREATE TRIGGER documents_touch BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION kaagaz_touch()
    """,
    """
    CREATE TABLE pages (
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        page_no integer NOT NULL CHECK (page_no >= 1),
        width double precision NOT NULL CHECK (width > 0),
        height double precision NOT NULL CHECK (height > 0),
        unit text NOT NULL CHECK (unit IN ('pt', 'px', 'cells')),
        has_image boolean NOT NULL DEFAULT false,
        PRIMARY KEY (document_id, page_no),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE reader_outputs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        role text NOT NULL CHECK (role IN ('first', 'second')),
        reader text NOT NULL,
        reader_version text NOT NULL,
        output jsonb NOT NULL CHECK (jsonb_typeof(output) = 'array'),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (document_id, role),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE pieces (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        seq integer NOT NULL CHECK (seq >= 0),
        page_from integer NOT NULL CHECK (page_from >= 1),
        page_to integer NOT NULL,
        text text NOT NULL CHECK (length(text) > 0),
        regions jsonb NOT NULL
            CHECK (jsonb_typeof(regions) = 'array' AND jsonb_array_length(regions) > 0),
        label text NOT NULL CHECK (label IN ('verified', 'needs_review')),
        embedding vector(1024) NULL,
        embedding_model text NULL,
        tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (document_id, seq),
        UNIQUE (customer_id, id),
        CHECK (page_to >= page_from),
        CHECK ((embedding IS NULL) = (embedding_model IS NULL)),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX pieces_document ON pieces (customer_id, document_id)",
    "CREATE INDEX pieces_tsv ON pieces USING gin (tsv)",
    "CREATE INDEX pieces_trgm ON pieces USING gin (text gin_trgm_ops)",
    "CREATE INDEX pieces_embedding ON pieces USING hnsw (embedding vector_cosine_ops)",
    """
    CREATE TABLE extracted_values (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        record_key text NOT NULL CHECK (length(record_key) BETWEEN 1 AND 100),
        field text NOT NULL CHECK (field ~ '^[a-z][a-z0-9_]{0,62}$'),
        value_text text NOT NULL,
        value_raw text NOT NULL,
        second_reading text NULL,
        value_number numeric NULL,
        value_date date NULL,
        page_no integer NOT NULL CHECK (page_no >= 1),
        region jsonb NOT NULL CHECK (jsonb_typeof(region) = 'object'),
        reader text NOT NULL,
        confidence real NULL CHECK (confidence BETWEEN 0 AND 1),
        label text NOT NULL CHECK (label IN ('verified', 'needs_review')),
        label_reason text NOT NULL CHECK (label_reason IN (
            'direct_cell', 'readers_agree', 'readers_disagree', 'single_reader',
            'low_confidence', 'corrected', 'learned_correction'
        )),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, id),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX extracted_values_field ON extracted_values (customer_id, field)",
    "CREATE INDEX extracted_values_record ON extracted_values (document_id, record_key)",
    """
    CREATE INDEX extracted_values_review ON extracted_values (customer_id, created_at)
    WHERE label = 'needs_review'
    """,
    """
    CREATE TRIGGER extracted_values_touch BEFORE UPDATE ON extracted_values
    FOR EACH ROW EXECUTE FUNCTION kaagaz_touch()
    """,
    # --- corrections ----------------------------------------------------------
    """
    CREATE TABLE corrections (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        field text NOT NULL,
        -- Re-reading a document replaces its values, so the link to the value
        -- goes empty then, but the correction history stays (who, when, what).
        value_id uuid NULL,
        old_value text NOT NULL,
        new_value text NOT NULL,
        choice text NOT NULL CHECK (choice IN ('first', 'second', 'typed')),
        corrected_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, value_id)
            REFERENCES extracted_values (customer_id, id) ON DELETE SET NULL (value_id),
        FOREIGN KEY (customer_id, corrected_by) REFERENCES users (customer_id, id)
    )
    """,
    "CREATE INDEX corrections_value ON corrections (value_id)",
    """
    CREATE TABLE learned_corrections (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        field text NOT NULL,
        raw_value text NOT NULL,
        corrected_value text NOT NULL,
        created_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, field, raw_value),
        FOREIGN KEY (customer_id, created_by) REFERENCES users (customer_id, id)
    )
    """,
    """
    CREATE TRIGGER learned_corrections_touch BEFORE UPDATE ON learned_corrections
    FOR EACH ROW EXECUTE FUNCTION kaagaz_touch()
    """,
    # --- chat -----------------------------------------------------------------
    """
    CREATE TABLE conversations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        user_id uuid NOT NULL,
        title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, id),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id)
    )
    """,
    "CREATE INDEX conversations_user ON conversations (customer_id, user_id, created_at DESC)",
    """
    CREATE TABLE messages (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        conversation_id uuid NOT NULL,
        role text NOT NULL CHECK (role IN ('user', 'assistant')),
        text text NOT NULL,
        answer jsonb NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, id),
        CHECK ((role = 'assistant') = (answer IS NOT NULL)),
        FOREIGN KEY (customer_id, conversation_id)
            REFERENCES conversations (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX messages_conversation ON messages (conversation_id, created_at)",
    """
    CREATE TABLE feedback (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        message_id uuid NOT NULL,
        user_id uuid NOT NULL,
        rating text NOT NULL CHECK (rating IN ('up', 'down')),
        reason text NULL CHECK (length(reason) <= 500),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (message_id, user_id),
        FOREIGN KEY (customer_id, message_id)
            REFERENCES messages (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id)
    )
    """,
    # --- audit ----------------------------------------------------------------
    """
    CREATE TABLE audit_log (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        actor_user_id uuid NULL,
        action text NOT NULL CHECK (action ~ '^[a-z_.]{1,64}$'),
        target_type text NULL,
        target_id text NULL,
        details jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX audit_log_listing ON audit_log (customer_id, created_at DESC)",
    # --- the two narrow owner-rights functions ----------------------------------
    """
    CREATE FUNCTION kaagaz_login_lookup(p_email text)
    RETURNS TABLE (user_id uuid, customer_id uuid, password_hash text, role text, disabled boolean)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
        SELECT u.id, u.customer_id, u.password_hash, u.role, u.disabled
        FROM users u
        WHERE u.email = lower(p_email)
    $$
    """,
    """
    CREATE FUNCTION kaagaz_create_customer(
        p_name text, p_admin_email text, p_admin_name text, p_password_hash text
    ) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    DECLARE
        new_id uuid;
    BEGIN
        INSERT INTO customers (name) VALUES (p_name) RETURNING id INTO new_id;
        INSERT INTO users (customer_id, email, display_name, role, password_hash)
        VALUES (new_id, lower(p_admin_email), p_admin_name, 'admin', p_password_hash);
        RETURN new_id;
    END
    $$
    """,
    "REVOKE ALL ON FUNCTION kaagaz_login_lookup(text) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION kaagaz_create_customer(text, text, text, text) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION kaagaz_login_lookup(text) TO {APP_ROLE}",
    f"GRANT EXECUTE ON FUNCTION kaagaz_create_customer(text, text, text, text) TO {APP_ROLE}",
    f"GRANT EXECUTE ON FUNCTION kaagaz_current_customer() TO {APP_ROLE}",
    # --- what the app role may do ---------------------------------------------
    f"GRANT SELECT, UPDATE ON customers TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE ON users TO {APP_ROLE}",
    f"""GRANT SELECT, INSERT, UPDATE, DELETE ON
        documents, pages, reader_outputs, pieces, extracted_values,
        learned_corrections, conversations, messages, feedback
        TO {APP_ROLE}""",
    f"GRANT SELECT, INSERT ON corrections, audit_log TO {APP_ROLE}",
    # --- row level security ---------------------------------------------------
    "ALTER TABLE customers ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON customers
    USING (id = kaagaz_current_customer())
    WITH CHECK (id = kaagaz_current_customer())
    """,
    *[f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" for table in CUSTOMER_TABLES],
    *[
        f"""
        CREATE POLICY customer_only ON {table}
        USING (customer_id = kaagaz_current_customer())
        WITH CHECK (customer_id = kaagaz_current_customer())
        """
        for table in CUSTOMER_TABLES
    ],
]

DOWNGRADE = [
    "DROP FUNCTION IF EXISTS kaagaz_create_customer(text, text, text, text)",
    "DROP FUNCTION IF EXISTS kaagaz_login_lookup(text)",
    *[f"DROP TABLE IF EXISTS {table} CASCADE" for table in reversed(CUSTOMER_TABLES)],
    "DROP TABLE IF EXISTS customers CASCADE",
    "DROP FUNCTION IF EXISTS kaagaz_keep_rejected()",
    "DROP FUNCTION IF EXISTS kaagaz_touch()",
    "DROP FUNCTION IF EXISTS kaagaz_current_customer()",
    # The role and the extensions stay: other databases on the server may use them.
    f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
