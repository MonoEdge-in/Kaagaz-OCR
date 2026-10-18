"""The sign-in session timeout the super admin sets (Tushar, 18 Oct 2026). NEEDS VRUSHIT'S LATER
REVIEW (15 Oct decision: no wait).

How long an organisation's admins and members stay signed in with no activity: whole hours,
1 to 24. One platform value, and one organisation's own value over it. Nothing saved is
today's behaviour: no idle limit, only the session's own end (SESSION_HOURS,
ADMIN_SESSION_MINUTES). With hours set, a new sign-in lasts 24 hours at most (``kaagaz.api.auth``)
and the idle timeout ends it. The operator's own session is untouched.

* ``platform_ai_settings.session_idle_hours`` (its one row, 0026): the platform value, NULL
  for none. The app already reads the table (SELECT, ``read_all``); it never writes it.
* ``org_session_settings``: one organisation's own value (no row follows the platform).
  ``customer_only`` policy; the app may read ``customer_id`` and ``idle_hours`` only (a
  column grant, so no operator id reaches it).
* ``users.last_seen_at``: when the user's session was last used, written by the app at most
  once a minute (``kaagaz.api.auth``). The idle clock is the later of it and
  ``last_login_at``, so a new sign-in always starts fresh.
* ``kaagaz_op_session_timeout``: the platform value and one organisation's (no customer: the
  platform's alone); nothing for an organisation that does not exist.
* ``kaagaz_op_set_session_timeout``: sets the platform value (no customer) or one
  organisation's (NULL hours: follow the platform), with a fresh code (step-up), and one
  ``platform_audit`` row (``session.timeout_platform`` or ``session.timeout_org``, with
  ``old_hours`` and ``new_hours``) for each change.

The operator's functions follow 0026's rules (SECURITY DEFINER, an empty search path,
schema-qualified names, revoked from PUBLIC, runnable by ``kaagaz_operator`` only, acting
only for an enabled operator whose two-step secret is confirmed).

Two nullable columns with no default (no table rewrite) and a new table. The downgrade drops
the two functions, the table and the two columns (the values are lost).

Revision ID: 0040
Revises: 0039 (per-organisation feature switches)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def _load_0038() -> ModuleType:
    path = Path(__file__).with_name("0038_ai_task_settings.py")
    spec = importlib.util.spec_from_file_location("migration_0038_for_0040", path)
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
HOURS = "BETWEEN 1 AND 24"

FUNCTIONS = {
    # The platform value with one organisation's (no customer: the platform's alone).
    "kaagaz_op_session_timeout(uuid, uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_session_timeout(p_operator uuid, p_customer uuid)
    RETURNS TABLE (org_hours integer, platform_hours integer)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT s.idle_hours, p.session_idle_hours
        FROM public.platform_ai_settings p
        LEFT JOIN public.customers c ON c.id = p_customer
        LEFT JOIN public.org_session_settings s ON s.customer_id = c.id
        WHERE (p_customer IS NULL OR c.id IS NOT NULL)
          AND EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
    """,
    # Set the platform value (no customer) or one organisation's, with a fresh code (step-up).
    "kaagaz_op_set_session_timeout(uuid, uuid, integer, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_set_session_timeout(
        p_operator uuid, p_customer uuid, p_hours integer, p_step bigint, OUT outcome text
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_status text;
        v_old integer;
    BEGIN
        IF p_hours IS NOT NULL AND p_hours NOT {HOURS} THEN
            RAISE EXCEPTION 'hours must be 1 to 24' USING ERRCODE = 'check_violation';
        END IF;
        {_OLD._ACTOR}
        {_OLD._STEP_UP}
        IF p_customer IS NULL THEN
            SELECT p.session_idle_hours INTO v_old FROM public.platform_ai_settings p FOR UPDATE;
            IF v_old IS NOT DISTINCT FROM p_hours THEN
                outcome := 'unchanged';
                RETURN;
            END IF;
            UPDATE public.platform_ai_settings AS p SET session_idle_hours = p_hours;
            INSERT INTO public.platform_audit (operator_id, action, target_type, target_id, details)
            VALUES (p_operator, 'session.timeout_platform', 'platform', NULL,
                    jsonb_build_object('old_hours', v_old, 'new_hours', p_hours));
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
        SELECT s.idle_hours INTO v_old FROM public.org_session_settings s
        WHERE s.customer_id = p_customer FOR UPDATE;
        IF v_old IS NOT DISTINCT FROM p_hours THEN
            outcome := 'unchanged';
            RETURN;
        END IF;
        IF p_hours IS NULL THEN
            DELETE FROM public.org_session_settings s WHERE s.customer_id = p_customer;
        ELSE
            INSERT INTO public.org_session_settings AS s (customer_id, idle_hours, updated_by, updated_at)
            VALUES (p_customer, p_hours, p_operator, now())
            ON CONFLICT (customer_id) DO UPDATE
            SET idle_hours = EXCLUDED.idle_hours, updated_by = EXCLUDED.updated_by,
                updated_at = EXCLUDED.updated_at;
        END IF;
        INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id, details)
        VALUES (p_operator, 'session.timeout_org', p_customer, 'customer', p_customer::text,
                jsonb_build_object('old_hours', v_old, 'new_hours', p_hours));
        outcome := 'done';
    END
    $$
    """,
}
FUNCTION_COMMENTS = {
    "kaagaz_op_session_timeout": "Operator: the platform's sign-in idle timeout in hours, with one"
    " organisation's own (no customer: the platform's alone). Nothing for an unknown organisation.",
    "kaagaz_op_set_session_timeout": "Operator: set the platform's sign-in idle timeout (no customer) or"
    " one organisation's (NULL: follow the platform), 1 to 24 hours, with a fresh code. Audited.",
}
COMMENTS = {
    "org_session_settings": (
        "How long one organisation's admins and members stay signed in with no activity. No row"
        " follows the platform (platform_ai_settings.session_idle_hours).",
        {
            "customer_id": "→ customers.id, the organisation (row level security filters on it).",
            "idle_hours": "Whole hours with no activity before signing in again, 1 to 24.",
            "updated_by": "→ operators.id, who set it last (the app can't read this column).",
            "updated_at": "When it was set last (the app can't read this column).",
        },
    ),
}
COLUMN_COMMENTS = {
    "platform_ai_settings.session_idle_hours": "Hours with no activity before an organisation's admins"
    " and members sign in again, 1 to 24; NULL: no idle limit (the session's own end only).",
    "users.last_seen_at": "When this user's session was last used, written at most once a minute; the"
    " idle timeout counts from the later of it and last_login_at.",
}

UPGRADE = [
    "ALTER TABLE platform_ai_settings ADD COLUMN session_idle_hours integer NULL"
    f" CONSTRAINT platform_session_idle_hours CHECK (session_idle_hours {HOURS})",
    "ALTER TABLE users ADD COLUMN last_seen_at timestamptz NULL",
    f"""
    CREATE TABLE org_session_settings (
        customer_id uuid PRIMARY KEY REFERENCES customers (id) ON DELETE CASCADE,
        idle_hours integer NOT NULL CHECK (idle_hours {HOURS}),
        updated_by uuid NULL REFERENCES operators (id),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    f"GRANT SELECT (customer_id, idle_hours) ON org_session_settings TO {APP_ROLE}",
    "ALTER TABLE org_session_settings ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON org_session_settings
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
    *[
        f"COMMENT ON {kind} {name} IS {_literal(comment)}"
        for table, (table_comment, columns) in COMMENTS.items()
        for kind, name, comment in (
            ("TABLE", table, table_comment),
            *(("COLUMN", f"{table}.{column}", text) for column, text in columns.items()),
        )
    ],
    *[f"COMMENT ON COLUMN {column} IS {_literal(text)}" for column, text in COLUMN_COMMENTS.items()],
    "COMMENT ON POLICY customer_only ON org_session_settings IS"
    " 'A row is seen only by its own customer; the operator''s function alone writes.'",
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
    "DROP TABLE IF EXISTS org_session_settings",
    "ALTER TABLE users DROP COLUMN IF EXISTS last_seen_at",
    "ALTER TABLE platform_ai_settings DROP COLUMN IF EXISTS session_idle_hours",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
