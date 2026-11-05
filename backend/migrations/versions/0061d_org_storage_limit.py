"""Storage limit per organisation (#516, Tushar, 5 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema.

Each organisation may store at most its limit: the sum of its ``documents.size_bytes`` (every
file, every version, and the Trash until it is purged). A new upload past it is refused; nothing
stored is ever touched. The super admin sets the limit, 1 to 10,000 GB.

* ``org_storage_limits``: one organisation's own limit (no row: the platform's). Its own table
  on purpose: ``kaagaz_op_set_folder_upload_limits`` deletes an organisation's
  ``org_upload_limits`` row when its three limits are NULL, so a column there would be wiped.
  The app reads two columns; only the operator's function writes.
* ``platform_ai_settings.storage_gb``: the platform's limit (NULL: the server's default, 50 GB,
  ``kaagaz.db.storage_limits.DEFAULT_STORAGE_GB``).
* ``kaagaz_op_storage_limit`` and ``kaagaz_op_set_storage_limit``: 0061c's pattern (step-up,
  one ``platform_audit`` row with the old and new limit, numbers only). The read also gives the
  bytes the organisation uses now (0 for the platform).

The table has row level security with ``customer_only`` and a column grant (the app never
writes it); it goes with its customer. A new table and a nullable column with no default: no
table rewrite, no row written. The downgrade drops them (the limits are lost).

Revision ID: 0061d
Revises: 0061c (folder upload)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0061d"
down_revision = "0061c"
branch_labels = None
depends_on = None


def _load_0038() -> ModuleType:
    path = Path(__file__).with_name("0038_ai_task_settings.py")
    spec = importlib.util.spec_from_file_location("migration_0038_for_0061d", path)
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
GB = "BETWEEN 1 AND 10000"
POLICY = "customer_id = kaagaz_current_customer()"

FUNCTIONS = {
    # The platform's limit with one organisation's own and the bytes it uses (no customer: the
    # platform's alone, 0 bytes).
    "kaagaz_op_storage_limit(uuid, uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_storage_limit(p_operator uuid, p_customer uuid)
    RETURNS TABLE (org_storage_gb integer, platform_storage_gb integer, used_bytes bigint)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT l.storage_gb, p.storage_gb,
               CASE WHEN p_customer IS NULL THEN 0::bigint ELSE
                   (SELECT coalesce(sum(d.size_bytes), 0)::bigint FROM public.documents d
                    WHERE d.customer_id = p_customer) END
        FROM public.platform_ai_settings p
        LEFT JOIN public.customers c ON c.id = p_customer
        LEFT JOIN public.org_storage_limits l ON l.customer_id = c.id
        WHERE (p_customer IS NULL OR c.id IS NOT NULL)
          AND EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
    """,
    # Set the platform's (no customer; NULL: the default) or one organisation's (NULL: follow the
    # platform).
    "kaagaz_op_set_storage_limit(uuid, uuid, integer, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_set_storage_limit(
        p_operator uuid, p_customer uuid, p_storage_gb integer, p_step bigint, OUT outcome text
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_status text;
        v_old integer;
    BEGIN
        IF p_storage_gb NOT {GB} THEN
            RAISE EXCEPTION 'storage limit out of range' USING ERRCODE = 'check_violation';
        END IF;
        {_OLD._ACTOR}
        {_OLD._STEP_UP}
        IF p_customer IS NULL THEN
            SELECT p.storage_gb INTO v_old FROM public.platform_ai_settings p FOR UPDATE;
            IF v_old IS NOT DISTINCT FROM p_storage_gb THEN
                outcome := 'unchanged';
                RETURN;
            END IF;
            UPDATE public.platform_ai_settings AS p SET storage_gb = p_storage_gb;
            INSERT INTO public.platform_audit (operator_id, action, target_type, target_id, details)
            VALUES (p_operator, 'storage.limit_platform', 'platform', NULL,
                    jsonb_build_object('old', v_old, 'new', p_storage_gb));
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
        SELECT l.storage_gb INTO v_old
        FROM public.org_storage_limits l WHERE l.customer_id = p_customer FOR UPDATE;
        IF v_old IS NOT DISTINCT FROM p_storage_gb THEN
            outcome := 'unchanged';
            RETURN;
        END IF;
        IF p_storage_gb IS NULL THEN
            DELETE FROM public.org_storage_limits l WHERE l.customer_id = p_customer;
        ELSE
            INSERT INTO public.org_storage_limits AS l (customer_id, storage_gb, updated_by, updated_at)
            VALUES (p_customer, p_storage_gb, p_operator, now())
            ON CONFLICT (customer_id) DO UPDATE
            SET storage_gb = EXCLUDED.storage_gb, updated_by = EXCLUDED.updated_by,
                updated_at = EXCLUDED.updated_at;
        END IF;
        INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id, details)
        VALUES (p_operator, 'storage.limit_org', p_customer, 'customer', p_customer::text,
                jsonb_build_object('old', v_old, 'new', p_storage_gb));
        outcome := 'done';
    END
    $$
    """,
}
FUNCTION_COMMENTS = {
    "kaagaz_op_storage_limit": "Operator: the platform's storage limit in GB, with one organisation's own"
    " and the bytes its documents hold (no customer: the platform's alone, 0 bytes). Nothing for an"
    " unknown organisation.",
    "kaagaz_op_set_storage_limit": "Operator: set the platform's storage limit (no customer; NULL: the"
    " default) or one organisation's (NULL: follow the platform), 1 to 10,000 GB, with a fresh code. Audited.",
}
COMMENTS = {
    "org_storage_limits": (
        "One organisation's own storage limit. No row follows the platform's.",
        {
            "customer_id": "→ customers.id, the organisation (row level security filters on it).",
            "storage_gb": "The most gigabytes its documents may hold (every version and the Trash),"
            " 1 to 10,000.",
            "updated_by": "→ operators.id, who set it last (the app can't read this column).",
            "updated_at": "When it was set last (the app can't read this column).",
        },
    ),
}
COLUMN_COMMENTS = {
    "platform_ai_settings.storage_gb": "The most gigabytes one organisation's documents may hold, 1 to"
    " 10,000; NULL: 50. An organisation's own wins.",
}

UPGRADE = [
    f"ALTER TABLE platform_ai_settings ADD COLUMN storage_gb integer NULL CONSTRAINT platform_storage_gb"
    f" CHECK (storage_gb {GB})",
    f"""
    CREATE TABLE org_storage_limits (
        customer_id uuid PRIMARY KEY REFERENCES customers (id) ON DELETE CASCADE,
        storage_gb integer NOT NULL CHECK (storage_gb {GB}),
        updated_by uuid NULL REFERENCES operators (id),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    f"GRANT SELECT (customer_id, storage_gb) ON org_storage_limits TO {APP_ROLE}",
    "ALTER TABLE org_storage_limits ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY customer_only ON org_storage_limits USING ({POLICY}) WITH CHECK ({POLICY})",
    "COMMENT ON POLICY customer_only ON org_storage_limits IS"
    " 'A row is seen and written only by its own customer.'",
    *[
        f"COMMENT ON {kind} {name} IS {_literal(comment)}"
        for table, (table_comment, columns) in COMMENTS.items()
        for kind, name, comment in (
            ("TABLE", table, table_comment),
            *(("COLUMN", f"{table}.{column}", text) for column, text in columns.items()),
        )
    ],
    *[f"COMMENT ON COLUMN {column} IS {_literal(text)}" for column, text in COLUMN_COMMENTS.items()],
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
    "DROP TABLE IF EXISTS org_storage_limits",
    "ALTER TABLE platform_ai_settings DROP COLUMN IF EXISTS storage_gb",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
