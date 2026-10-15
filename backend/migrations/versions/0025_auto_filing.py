"""Auto-filing (feature 28, council 15 Oct 2026; plan
Outside-Folder/council/2026-10-15-auto-filing.md, section D).

* ``customers.autofile_granted``: the platform operator allowed it (R1), written only by
  ``kaagaz_op_set_org_autofile``; the app has no grant on it.
* ``customers.autofile_on``: the organisation's admin switched it on. The app may update
  it (a column grant, next to 0014's ``max_upload_mb``), and the CHECK
  ``customers_autofile_check`` refuses it on without the grant. Revoking the grant turns
  it off in the same UPDATE.
* ``folders.autofile_key``: the folder Kaagaz files one type into (``type:invoice``), set
  when Kaagaz makes the folder or adopts one of the same name (R9). Unique per customer
  (a partial unique index), so two filings never make two Invoices folders. A deleted
  folder takes its key with it (R10).
* ``documents.autofile``: a family's state, on its first version only (id = family_id):
  pending (the upload went in no folder while the switch was on), filed or skipped. Every
  write of ``folder_id`` clears it (R3), so a person's move always wins.

``kaagaz_op_set_org_autofile`` is 0023's pattern (no step-up, as ``kaagaz_op_set_org_models``):
SECURITY DEFINER, empty search path, the operator checked, the row locked FOR UPDATE, an
organisation being deleted refused, one ``platform_audit`` row and one row in the
organisation's own ``audit_log`` (``{"by": "operator"}``) for each change. Outcomes:
signed_out, missing, deleting, unchanged or done. ``kaagaz_op_customers`` (0016) is dropped
and made again with the two customers columns (its return type changes); the downgrade
puts 0016's back.

No table is rewritten: the new columns have constant defaults. Each CHECK reads its table
once under the lock. The lock order is customers, folders, documents, the order filing
locks them in (the tree lock, then the family). The purge (0018) is unchanged: folders
already go with their customer, and no new column is a foreign key.

Revision ID: 0025
Revises: 0024 (operator insights)
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
OPERATOR_ROLE = "kaagaz_operator"
LOCK = "LOCK TABLE customers, folders, documents IN ACCESS EXCLUSIVE MODE"

# As 0016's and 0023's (a migration can't import another).
_ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"
_ACTOR = f"""
        PERFORM 1 FROM public.operators o WHERE {_ENABLED};
        IF NOT FOUND THEN
            outcome := 'signed_out';
            RETURN;
        END IF;
"""

SET_AUTOFILE = "kaagaz_op_set_org_autofile(uuid, uuid, boolean)"
CUSTOMERS = "kaagaz_op_customers(uuid)"

_SET_AUTOFILE = f"""
    CREATE FUNCTION public.kaagaz_op_set_org_autofile(
        p_operator uuid, p_customer uuid, p_granted boolean, OUT outcome text, OUT result_id uuid
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_status text;
        v_granted boolean;
        v_action text := CASE WHEN p_granted THEN 'org.autofile_grant' ELSE 'org.autofile_revoke' END;
    BEGIN
        IF p_granted IS NULL THEN
            RAISE EXCEPTION 'granted must be true or false' USING ERRCODE = 'check_violation';
        END IF;
        {_ACTOR}
        SELECT c.status, c.autofile_granted INTO v_status, v_granted
        FROM public.customers c WHERE c.id = p_customer FOR UPDATE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        result_id := p_customer;
        IF v_status = 'deleting' THEN
            outcome := 'deleting';
            RETURN;
        END IF;
        IF v_granted = p_granted THEN
            outcome := 'unchanged';
            RETURN;
        END IF;
        UPDATE public.customers AS c
        SET autofile_granted = p_granted, autofile_on = c.autofile_on AND p_granted
        WHERE c.id = p_customer;
        INSERT INTO public.audit_log (customer_id, actor_user_id, action, target_type, target_id, details)
        VALUES (p_customer, NULL, v_action, 'customer', p_customer::text, jsonb_build_object('by', 'operator'));
        INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id)
        VALUES (p_operator, v_action, p_customer, 'customer', p_customer::text);
        outcome := 'done';
    END
    $$
"""

# 0016's list, with or without the two auto-filing columns at the end.
_CUSTOMERS = """
    CREATE FUNCTION public.kaagaz_op_customers(p_operator uuid)
    RETURNS TABLE (customer_id uuid, name text, status text, disabled_at timestamptz,
                   delete_after timestamptz, created_at timestamptz, admins bigint, reviewers bigint,
                   members bigint, documents bigint, pages bigint, size_bytes bigint,
                   last_activity_at timestamptz{columns})
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT c.id, c.name, c.status, c.disabled_at, c.delete_after, c.created_at,
               u.admins, u.reviewers, u.members, d.documents, d.pages, d.size_bytes,
               greatest(a.last_at, d.last_at){values}
        FROM public.customers c
        CROSS JOIN LATERAL (
            SELECT count(*) FILTER (WHERE x.role = 'admin') AS admins,
                   count(*) FILTER (WHERE x.role = 'reviewer') AS reviewers,
                   count(*) FILTER (WHERE x.role = 'member') AS members
            FROM public.users x WHERE x.customer_id = c.id
        ) u
        CROSS JOIN LATERAL (
            SELECT count(*) FILTER (WHERE x.is_current AND x.deleted_at IS NULL) AS documents,
                   coalesce(sum(x.page_count) FILTER (WHERE x.is_current AND x.deleted_at IS NULL), 0)
                       AS pages,
                   coalesce(sum(x.size_bytes), 0)::bigint AS size_bytes,
                   max(x.created_at) AS last_at
            FROM public.documents x WHERE x.customer_id = c.id
        ) d
        CROSS JOIN LATERAL (
            SELECT max(x.created_at) AS last_at FROM public.audit_log x WHERE x.customer_id = c.id
        ) a
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {enabled})
        ORDER BY c.created_at, c.id
    $$
"""
CUSTOMERS_NOW = _CUSTOMERS.format(
    columns=", autofile_granted boolean, autofile_on boolean",
    values=", c.autofile_granted, c.autofile_on",
    enabled=_ENABLED,
)
CUSTOMERS_0016 = _CUSTOMERS.format(columns="", values="", enabled=_ENABLED)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# Plain constants with no colon before a word, which SQLAlchemy would read as a bind parameter.
COMMENTS = {
    "customers.autofile_granted": "The platform operator allowed auto-filing for this organisation."
    " Written only by kaagaz_op_set_org_autofile.",
    "customers.autofile_on": "The organisation's admin switched auto-filing on: new uploads with no"
    " folder go into a folder for their type. Only while autofile_granted (CHECK).",
    "folders.autofile_key": "The document type Kaagaz files into this folder: the word type, a colon"
    " and the type, one folder per type and customer. NULL, a folder Kaagaz does not file into.",
    "documents.autofile": "Auto-filing state of the family, on its first version only (id = family_id):"
    " pending, filed or skipped. NULL, nothing to do. Every write of folder_id clears it.",
}
INDEX_COMMENT = "One folder per document type a customer's documents are filed into."
FUNCTION_COMMENTS = {
    SET_AUTOFILE: "Operator: allow or stop auto-filing for one organisation; stopping it also"
    " switches it off. Audited in both logs.",
    CUSTOMERS: "Operator: every organisation with its status, counts only, and its auto-filing"
    " grant and switch.",
}


def _function(signature: str, create: str, comment: str | None) -> list[str]:
    return [
        create,
        *([] if comment is None else [f"COMMENT ON FUNCTION public.{signature} IS {_literal(comment)}"]),
        f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION public.{signature} TO {OPERATOR_ROLE}",
    ]


UPGRADE = [
    LOCK,
    """
    ALTER TABLE customers
        ADD COLUMN autofile_granted boolean NOT NULL DEFAULT false,
        ADD COLUMN autofile_on boolean NOT NULL DEFAULT false,
        ADD CONSTRAINT customers_autofile_check CHECK (autofile_granted OR NOT autofile_on)
    """,
    f"GRANT UPDATE (autofile_on) ON customers TO {APP_ROLE}",
    "ALTER TABLE folders ADD COLUMN autofile_key text NULL CHECK (autofile_key ~ '^type:[a-z_]{1,32}$')",
    "CREATE UNIQUE INDEX folders_autofile_key ON folders (customer_id, autofile_key)"
    " WHERE autofile_key IS NOT NULL",
    "ALTER TABLE documents ADD COLUMN autofile text NULL CHECK (autofile IN ('pending', 'filed', 'skipped'))",
    *[f"COMMENT ON COLUMN {column} IS {_literal(comment)}" for column, comment in COMMENTS.items()],
    f"COMMENT ON INDEX folders_autofile_key IS {_literal(INDEX_COMMENT)}",
    *_function(SET_AUTOFILE, _SET_AUTOFILE, FUNCTION_COMMENTS[SET_AUTOFILE]),
    f"DROP FUNCTION public.{CUSTOMERS}",
    *_function(CUSTOMERS, CUSTOMERS_NOW, FUNCTION_COMMENTS[CUSTOMERS]),
]

DOWNGRADE = [
    LOCK,
    f"DROP FUNCTION IF EXISTS public.{SET_AUTOFILE}",
    f"DROP FUNCTION public.{CUSTOMERS}",
    *_function(CUSTOMERS, CUSTOMERS_0016, None),
    f"REVOKE UPDATE (autofile_on) ON customers FROM {APP_ROLE}",
    "DROP INDEX IF EXISTS folders_autofile_key",
    "ALTER TABLE documents DROP COLUMN IF EXISTS autofile",
    "ALTER TABLE folders DROP COLUMN IF EXISTS autofile_key",
    """
    ALTER TABLE customers
        DROP CONSTRAINT IF EXISTS customers_autofile_check,
        DROP COLUMN IF EXISTS autofile_on,
        DROP COLUMN IF EXISTS autofile_granted
    """,
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
