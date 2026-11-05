"""Upload a whole folder (#484, Tushar, 3 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema.

A person uploads a folder; Kaagaz makes the same folders, then takes its files one by one,
each into the folder it was in. The server keeps the plan and every file's state, so a retry
or a "Continue upload" after a reload lands in the folder recorded here, never another.

* ``folder_uploads``: one upload, keyed (customer_id, id): the id is the browser's, so a
  reused id never clashes across customers. Where it went, the counts it declared and the
  counts it registered (``items_registered``, ``bytes_registered``: the limits are checked
  against these, never against a declared number alone).
* ``folder_upload_folders``: each folder of the plan by its path in the picked folder
  (``path_key``, SHA-256 of it) with its index in the last plan (``idx``) and the folder it
  became, so a re-plan reuses the same folder by path, whatever order the browser lists it
  in, even after a rename in Kaagaz. No key to
  ``folders``: a folder delete is never blocked by an old upload.
* ``folder_upload_items``: one row a file: its key (SHA-256 of its path), its folder and
  batch (fixed at registration; a re-plan that makes a deleted folder again moves its waiting
  items to the new one), its size and state. No key to ``folders``
  or ``documents``: a deleted folder makes the item ``folder_gone``, it does not vanish.
* ``org_upload_limits``: one organisation's own limits (no row: the platform's). The app
  reads four columns; only the operator's function writes.
* ``platform_ai_settings.folder_upload_max_*``: the platform's limits (NULL: the server's
  default, 50,000 files, 5 GB, 10,000 folders).
* ``kaagaz_op_folder_upload_limits`` and ``kaagaz_op_set_folder_upload_limits``: 0040's
  pattern (step-up, one ``platform_audit`` row with the old and new limits, numbers only).
* The ``folders`` comment: nested at most 100 levels (was 5). The depth is a code rule
  (``kaagaz.db.folders.MAX_DEPTH``); no CHECK or trigger held it.

The customer tables have row level security with ``customer_only`` and least grants: the app
never deletes an upload or an item (they go with the customer). New tables and nullable
columns with no default: no table rewrite, no row written. The downgrade drops them (the
limits and upload records are lost; folders deeper than 5 levels stay) and puts 0007's comment
back.

Revision ID: 0061c
Revises: 0061b (an admin's own two-step off)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0061c"
down_revision = "0061b"
branch_labels = None
depends_on = None


def _load_0038() -> ModuleType:
    path = Path(__file__).with_name("0038_ai_task_settings.py")
    spec = importlib.util.spec_from_file_location("migration_0038_for_0061c", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_OLD = _load_0038()
APP_ROLE = _OLD.APP_ROLE
OPERATOR_ROLE = _OLD.OPERATOR_ROLE
_ENABLED = _OLD._ENABLED
_literal = _OLD._literal
FILES, GB, FOLDERS = "BETWEEN 1 AND 200000", "BETWEEN 1 AND 20", "BETWEEN 1 AND 50000"
STATES = "('waiting', 'stored', 'duplicate', 'failed')"
FAILURES = "('too_large', 'unsupported_type', 'empty_file', 'folder_gone', 'bad_name')"
POLICY = "customer_id = kaagaz_current_customer()"
_LIMITS = "jsonb_build_object('max_files', {0}max_files, 'max_gb', {0}max_gb, 'max_folders', {0}max_folders)"

FUNCTIONS = {
    # The platform's limits with one organisation's own (no customer: the platform's alone).
    "kaagaz_op_folder_upload_limits(uuid, uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_folder_upload_limits(p_operator uuid, p_customer uuid)
    RETURNS TABLE (org_max_files integer, org_max_gb integer, org_max_folders integer,
                   platform_max_files integer, platform_max_gb integer, platform_max_folders integer)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT l.max_files, l.max_gb, l.max_folders,
               p.folder_upload_max_files, p.folder_upload_max_gb, p.folder_upload_max_folders
        FROM public.platform_ai_settings p
        LEFT JOIN public.customers c ON c.id = p_customer
        LEFT JOIN public.org_upload_limits l ON l.customer_id = c.id
        WHERE (p_customer IS NULL OR c.id IS NOT NULL)
          AND EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
    """,
    # Set the platform's (no customer) or one organisation's (three NULLs: follow the platform).
    "kaagaz_op_set_folder_upload_limits(uuid, uuid, integer, integer, integer, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_set_folder_upload_limits(
        p_operator uuid, p_customer uuid, p_max_files integer, p_max_gb integer, p_max_folders integer,
        p_step bigint, OUT outcome text
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_status text;
        v_old jsonb;
        v_new jsonb := {_LIMITS.format("p_")};
    BEGIN
        IF p_max_files NOT {FILES} OR p_max_gb NOT {GB} OR p_max_folders NOT {FOLDERS} THEN
            RAISE EXCEPTION 'folder upload limits out of range' USING ERRCODE = 'check_violation';
        END IF;
        {_OLD._ACTOR}
        {_OLD._STEP_UP}
        IF p_customer IS NULL THEN
            SELECT {_LIMITS.format("p.folder_upload_")} INTO v_old
            FROM public.platform_ai_settings p FOR UPDATE;
            IF v_old = v_new THEN
                outcome := 'unchanged';
                RETURN;
            END IF;
            UPDATE public.platform_ai_settings AS p SET folder_upload_max_files = p_max_files,
                folder_upload_max_gb = p_max_gb, folder_upload_max_folders = p_max_folders;
            INSERT INTO public.platform_audit (operator_id, action, target_type, target_id, details)
            VALUES (p_operator, 'upload.folder_limits_platform', 'platform', NULL,
                    jsonb_build_object('old', v_old, 'new', v_new));
            outcome := 'done';
            RETURN;
        END IF;
        SELECT c.status INTO v_status FROM public.customers c WHERE c.id = p_customer FOR SHARE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        IF v_status = 'deleting' THEN
            outcome := 'deleting';
            RETURN;
        END IF;
        SELECT {_LIMITS.format("l.")} INTO v_old
        FROM public.org_upload_limits l WHERE l.customer_id = p_customer FOR UPDATE;
        v_old := coalesce(v_old, jsonb_build_object('max_files', NULL, 'max_gb', NULL, 'max_folders', NULL));
        IF v_old = v_new THEN
            outcome := 'unchanged';
            RETURN;
        END IF;
        IF num_nonnulls(p_max_files, p_max_gb, p_max_folders) = 0 THEN
            DELETE FROM public.org_upload_limits l WHERE l.customer_id = p_customer;
        ELSE
            INSERT INTO public.org_upload_limits AS l
                (customer_id, max_files, max_gb, max_folders, updated_by, updated_at)
            VALUES (p_customer, p_max_files, p_max_gb, p_max_folders, p_operator, now())
            ON CONFLICT (customer_id) DO UPDATE
            SET max_files = EXCLUDED.max_files, max_gb = EXCLUDED.max_gb, max_folders = EXCLUDED.max_folders,
                updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at;
        END IF;
        INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id, details)
        VALUES (p_operator, 'upload.folder_limits_org', p_customer, 'customer', p_customer::text,
                jsonb_build_object('old', v_old, 'new', v_new));
        outcome := 'done';
    END
    $$
    """,
}
FUNCTION_COMMENTS = {
    "kaagaz_op_folder_upload_limits": "Operator: the platform's folder upload limits (files, GB, folders),"
    " with one organisation's own (no customer: the platform's alone). Nothing for an unknown organisation.",
    "kaagaz_op_set_folder_upload_limits": "Operator: set the platform's folder upload limits (no customer)"
    " or one organisation's (all NULL: follow the platform), within the ceilings, with a fresh code. Audited.",
}
UPLOAD = "→ folder_uploads.id (same customer), the upload it belongs to."
COMMENTS = {
    "folder_uploads": (
        "One folder upload (#484): where it went and its counts. The browser makes the id.",
        {
            "id": "The upload's id, made by the browser; unique per customer.",
            "customer_id": "→ customers.id, the organisation (row level security filters on it).",
            "user_id": "→ users.id (same customer), who uploads; only they see it.",
            "target": "department (a new top-level department) or folder (inside a folder or the top level).",
            "parent_folder_id": "The folder it went inside; NULL for the top level. No key: kept if deleted.",
            "root_folder_id": "The folder made or reused for the picked folder. No key: kept if deleted.",
            "source_name": "The picked folder's own name, 1 to 100 characters.",
            "root_name": 'The root folder\'s name in Kaagaz ("A (2)" when kept separate), 1 to 100 characters.',
            "merge": "True: same-name folders already there are added to; false: kept separate.",
            "file_count": "Files the browser said it holds, hidden and system files left out.",
            "total_bytes": "Bytes the browser said those files hold.",
            "skipped_count": "Hidden and system files skipped, never sent.",
            "folders_created": "Folders this upload made (merged ones not counted), across every plan.",
            "items_registered": "Files registered so far; never above the limit.",
            "bytes_registered": "Bytes of the files registered so far; never above the limit.",
            "created_at": "When it was planned first.",
            "updated_at": "When it was planned or registered last.",
        },
    ),
    "folder_upload_folders": (
        "Each folder of a folder upload's plan: its index in the browser's list and the folder it became.",
        {
            "customer_id": "→ customers.id, the organisation (row level security filters on it).",
            "folder_upload_id": UPLOAD,
            "idx": "Its index in the last plan's list of folders (0 is the picked folder).",
            "path_key": "SHA-256 hex of its path in the picked folder (NFC names joined by /).",
            "folder_id": "The folder in Kaagaz. No key: a folder delete is never blocked.",
        },
    ),
    "folder_upload_items": (
        "One file of a folder upload, its target folder and batch fixed at registration, and its state.",
        {
            "id": "Item id.",
            "customer_id": "→ customers.id, the organisation (row level security filters on it).",
            "folder_upload_id": UPLOAD,
            "seq": "Its number in the upload, from 0; gives its batch (seq / MAX_FILES_PER_BATCH).",
            "path_key": "SHA-256 hex of its path in the picked folder, made by the browser.",
            "name": "The file's name, cleaned, 1 to 255 characters.",
            "folder_id": "The folder it goes into; a re-plan that makes it again moves a waiting item."
            " No key: a deleted one makes it folder_gone.",
            "size_bytes": "Its size; the file sent must be exactly this.",
            "batch_id": "The batch its document goes into.",
            "state": "waiting, stored, duplicate (the same file was already in Kaagaz) or failed.",
            "failure_code": "Why it failed: too_large, unsupported_type, empty_file, folder_gone or bad_name"
            " (an unsafe name, or another file's in its folder); NULL otherwise.",
            "document_id": "The document it became, or the one it duplicates. No key: kept if deleted.",
            "updated_at": "When its state changed last.",
        },
    ),
    "org_upload_limits": (
        "One organisation's own folder upload limits. No row (or NULL) follows the platform's.",
        {
            "customer_id": "→ customers.id, the organisation (row level security filters on it).",
            "max_files": "The most files one folder upload may hold, 1 to 200,000.",
            "max_gb": "The most gigabytes one folder upload may hold, 1 to 20.",
            "max_folders": "The most folders one folder upload may make, 1 to 50,000.",
            "updated_by": "→ operators.id, who set it last (the app can't read this column).",
            "updated_at": "When it was set last (the app can't read this column).",
        },
    ),
}
COLUMN_COMMENTS = {
    "platform_ai_settings.folder_upload_max_files": "The most files one folder upload may hold, 1 to"
    " 200,000; NULL: 50,000. An organisation's own wins.",
    "platform_ai_settings.folder_upload_max_gb": "The most gigabytes one folder upload may hold, 1 to"
    " 20; NULL: 5. An organisation's own wins.",
    "platform_ai_settings.folder_upload_max_folders": "The most folders one folder upload may make, 1 to"
    " 50,000; NULL: 10,000. An organisation's own wins.",
}
FOLDERS_COMMENT = "Folders for documents, nested at most 100 levels."
FOLDERS_COMMENT_0007 = "Folders for documents, nested at most 5 levels."
TABLES = ("folder_uploads", "folder_upload_folders", "folder_upload_items", "org_upload_limits")

UPGRADE = [
    "ALTER TABLE platform_ai_settings"
    f" ADD COLUMN folder_upload_max_files integer NULL CONSTRAINT platform_folder_upload_max_files"
    f" CHECK (folder_upload_max_files {FILES}),"
    f" ADD COLUMN folder_upload_max_gb integer NULL CONSTRAINT platform_folder_upload_max_gb"
    f" CHECK (folder_upload_max_gb {GB}),"
    f" ADD COLUMN folder_upload_max_folders integer NULL CONSTRAINT platform_folder_upload_max_folders"
    f" CHECK (folder_upload_max_folders {FOLDERS})",
    """
    CREATE TABLE folder_uploads (
        id uuid NOT NULL,
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        user_id uuid NOT NULL,
        target text NOT NULL CHECK (target IN ('department', 'folder')),
        parent_folder_id uuid NULL,
        root_folder_id uuid NULL,
        source_name text NOT NULL CHECK (char_length(source_name) BETWEEN 1 AND 100),
        root_name text NOT NULL CHECK (char_length(root_name) BETWEEN 1 AND 100),
        merge boolean NOT NULL,
        file_count integer NOT NULL CHECK (file_count >= 0),
        total_bytes bigint NOT NULL CHECK (total_bytes >= 0),
        skipped_count integer NOT NULL CHECK (skipped_count >= 0),
        folders_created integer NOT NULL DEFAULT 0 CHECK (folders_created >= 0),
        items_registered integer NOT NULL DEFAULT 0 CHECK (items_registered >= 0),
        bytes_registered bigint NOT NULL DEFAULT 0 CHECK (bytes_registered >= 0),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, id),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX folder_uploads_owner ON folder_uploads (customer_id, user_id, created_at DESC)",
    """
    CREATE TABLE folder_upload_folders (
        customer_id uuid NOT NULL,
        folder_upload_id uuid NOT NULL,
        idx integer NOT NULL CHECK (idx >= 0),
        path_key text NOT NULL CHECK (path_key ~ '^[0-9a-f]{64}$'),
        folder_id uuid NOT NULL,
        PRIMARY KEY (customer_id, folder_upload_id, path_key),
        FOREIGN KEY (customer_id, folder_upload_id) REFERENCES folder_uploads (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX folder_upload_folders_folder ON folder_upload_folders (customer_id, folder_upload_id, folder_id)",
    f"""
    CREATE TABLE folder_upload_items (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        folder_upload_id uuid NOT NULL,
        seq integer NOT NULL CHECK (seq >= 0),
        path_key text NOT NULL CHECK (path_key ~ '^[0-9a-f]{{64}}$'),
        name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 255),
        folder_id uuid NOT NULL,
        size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
        batch_id uuid NOT NULL,
        state text NOT NULL DEFAULT 'waiting' CHECK (state IN {STATES}),
        failure_code text NULL CHECK (failure_code IN {FAILURES}),
        document_id uuid NULL,
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, id),
        UNIQUE (customer_id, folder_upload_id, path_key),
        UNIQUE (customer_id, folder_upload_id, seq),
        CONSTRAINT folder_upload_items_failed CHECK ((state = 'failed') = (failure_code IS NOT NULL)),
        CONSTRAINT folder_upload_items_document CHECK ((state IN ('stored', 'duplicate')) = (document_id IS NOT NULL)),
        FOREIGN KEY (customer_id, folder_upload_id) REFERENCES folder_uploads (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX folder_upload_items_state ON folder_upload_items (customer_id, folder_upload_id, state, seq)",
    f"""
    CREATE TABLE org_upload_limits (
        customer_id uuid PRIMARY KEY REFERENCES customers (id) ON DELETE CASCADE,
        max_files integer NULL CHECK (max_files {FILES}),
        max_gb integer NULL CHECK (max_gb {GB}),
        max_folders integer NULL CHECK (max_folders {FOLDERS}),
        updated_by uuid NULL REFERENCES operators (id),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT org_upload_limits_some CHECK (num_nonnulls(max_files, max_gb, max_folders) > 0)
    )
    """,
    f"GRANT SELECT, INSERT, UPDATE ON folder_uploads TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE ON folder_upload_folders TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE ON folder_upload_items TO {APP_ROLE}",
    f"GRANT SELECT (customer_id, max_files, max_gb, max_folders) ON org_upload_limits TO {APP_ROLE}",
    *[
        statement
        for table in TABLES
        for statement in (
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"CREATE POLICY customer_only ON {table} USING ({POLICY}) WITH CHECK ({POLICY})",
            f"COMMENT ON POLICY customer_only ON {table} IS 'A row is seen and written only by its own customer.'",
        )
    ],
    *[
        f"COMMENT ON {kind} {name} IS {_literal(comment)}"
        for table, (table_comment, columns) in COMMENTS.items()
        for kind, name, comment in (
            ("TABLE", table, table_comment),
            *(("COLUMN", f"{table}.{column}", text) for column, text in columns.items()),
        )
    ],
    *[f"COMMENT ON COLUMN {column} IS {_literal(text)}" for column, text in COLUMN_COMMENTS.items()],
    f"COMMENT ON TABLE folders IS {_literal(FOLDERS_COMMENT)}",
    *[
        statement
        for signature, create in FUNCTIONS.items()
        for statement in (
            create,
            f"COMMENT ON FUNCTION public.{signature} IS {_literal(FUNCTION_COMMENTS[signature.split('(')[0]])}",
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{signature} TO {OPERATOR_ROLE}",
        )
    ],
]

DOWNGRADE = [
    *[f"DROP FUNCTION IF EXISTS public.{signature}" for signature in FUNCTIONS],
    *[f"DROP TABLE IF EXISTS {table}" for table in ("org_upload_limits", *reversed(TABLES[:3]))],
    "ALTER TABLE platform_ai_settings DROP COLUMN IF EXISTS folder_upload_max_files,"
    " DROP COLUMN IF EXISTS folder_upload_max_gb, DROP COLUMN IF EXISTS folder_upload_max_folders",
    f"COMMENT ON TABLE folders IS {_literal(FOLDERS_COMMENT_0007)}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
