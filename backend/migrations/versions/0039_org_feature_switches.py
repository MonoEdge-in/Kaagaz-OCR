"""Per-organisation feature switches (Tushar, 17 Oct 2026). NEEDS VRUSHIT'S LATER REVIEW
(15 Oct decision: no wait).

* ``org_feature_switches``: the super admin turns one product feature off (or back on) for
  one organisation. A feature runs for an organisation only while the platform switch
  (0038) and its own are both on; no row is on, and the platform's off always wins. Every
  feature of 0038 but ``self_signup`` (sign-up makes a new organisation, so one has no
  say). ``customer_only`` policy; the app may read ``customer_id``, ``feature`` and
  ``enabled`` only (a column grant, so no operator id reaches it); only
  ``kaagaz_op_set_org_feature_switch`` writes, with a fresh code (step-up), and one
  ``platform_audit`` row (``feature.org_on`` or ``feature.org_off``, the organisation as its
  subject) for each change.
* ``kaagaz_op_org_feature_switches``: one organisation's switches with the platform's,
  nothing for an organisation that does not exist.
* ``kaagaz_op_feature_switches`` (0038) is replaced: ``organisations_using`` leaves out the
  organisations whose own switch is off.

The operator's functions follow 0026's rules (SECURITY DEFINER, an empty search path,
schema-qualified names, revoked from PUBLIC, runnable by ``kaagaz_operator`` only, acting
only for an enabled operator whose two-step secret is confirmed).

A new table only: no lock on an existing table. The downgrade drops the two functions and
the table and puts 0038's ``kaagaz_op_feature_switches`` back (the switches are lost).

Revision ID: 0039
Revises: 0038 (AI settings by task, the platform feature switches)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def _load_0038() -> ModuleType:
    path = Path(__file__).with_name("0038_ai_task_settings.py")
    spec = importlib.util.spec_from_file_location("migration_0038_for_0039", path)
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
ORG_FEATURE_NAMES = tuple(name for name in _OLD.FEATURE_NAMES if name != "self_signup")
ORG_FEATURES = "(" + ", ".join(f"'{name}'" for name in ORG_FEATURE_NAMES) + ")"

# 0038's platform list, with the organisations whose own switch is off left out of the count.
PLATFORM = "kaagaz_op_feature_switches(uuid)"
CREATE = "    CREATE FUNCTION public."
REPLACE = "    CREATE OR REPLACE FUNCTION public."
COUNT_EDITS = (
    ("SELECT c.autofile_on,", "SELECT c.id, c.autofile_on,"),
    (
        "ELSE true END),",
        "ELSE true END\n                                    AND NOT EXISTS ("
        "SELECT 1 FROM public.org_feature_switches x\n"
        "                                        WHERE x.customer_id = org.id AND x.feature = f.feature"
        " AND NOT x.enabled)),",
    ),
)


def _platform(edits: tuple[tuple[str, str], ...]) -> str:
    """0038's platform list as CREATE OR REPLACE, with ``edits`` (none: as 0038)."""
    body: str = _OLD.FUNCTIONS[PLATFORM]
    for old, _new in ((CREATE, REPLACE), *COUNT_EDITS):
        if body.count(old) != 1:
            raise RuntimeError(f"0038's {PLATFORM} is not the text 0039 replaces")
    for old, new in ((CREATE, REPLACE), *edits):
        body = body.replace(old, new)
    return body


FUNCTIONS = {
    # One organisation's switches, with the platform's (nothing for an unknown organisation).
    "kaagaz_op_org_feature_switches(uuid, uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_org_feature_switches(p_operator uuid, p_customer uuid)
    RETURNS TABLE (feature text, enabled boolean, platform_enabled boolean)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT f.feature, COALESCE(s.enabled, true), COALESCE(w.enabled, true)
        FROM public.customers c
        CROSS JOIN unnest(ARRAY[{ORG_FEATURES[1:-1]}]::text[]) WITH ORDINALITY AS f (feature, n)
        LEFT JOIN public.org_feature_switches s ON s.customer_id = c.id AND s.feature = f.feature
        LEFT JOIN public.feature_switches w ON w.feature = f.feature
        WHERE c.id = p_customer AND EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        ORDER BY f.n
    $$
    """,
    # Turn one feature on or off for one organisation, with a fresh code (step-up).
    "kaagaz_op_set_org_feature_switch(uuid, uuid, text, boolean, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_set_org_feature_switch(
        p_operator uuid, p_customer uuid, p_feature text, p_enabled boolean, p_step bigint, OUT outcome text
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_status text;
        v_enabled boolean;
    BEGIN
        IF p_enabled IS NULL OR p_feature IS NULL OR p_feature NOT IN {ORG_FEATURES} THEN
            RAISE EXCEPTION 'unknown feature or switch' USING ERRCODE = 'check_violation';
        END IF;
        {_OLD._ACTOR}
        {_OLD._STEP_UP}
        SELECT c.status INTO v_status FROM public.customers c WHERE c.id = p_customer FOR SHARE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        IF v_status = 'deleting' THEN
            outcome := 'deleting';
            RETURN;
        END IF;
        SELECT s.enabled INTO v_enabled FROM public.org_feature_switches s
        WHERE s.customer_id = p_customer AND s.feature = p_feature FOR UPDATE;
        IF COALESCE(v_enabled, true) = p_enabled THEN
            outcome := 'unchanged';
            RETURN;
        END IF;
        INSERT INTO public.org_feature_switches AS s (customer_id, feature, enabled, updated_by, updated_at)
        VALUES (p_customer, p_feature, p_enabled, p_operator, now())
        ON CONFLICT (customer_id, feature) DO UPDATE
        SET enabled = EXCLUDED.enabled, updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at;
        INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id, details)
        VALUES (p_operator, CASE WHEN p_enabled THEN 'feature.org_on' ELSE 'feature.org_off' END, p_customer,
                'feature', p_feature, jsonb_build_object('enabled', p_enabled));
        outcome := 'done';
    END
    $$
    """,
}
FUNCTION_COMMENTS = {
    "kaagaz_op_org_feature_switches": "Operator: one organisation's feature switches, each with the"
    " platform's. Nothing for an unknown organisation.",
    "kaagaz_op_set_org_feature_switch": "Operator: turn one product feature on or off for one organisation,"
    " with a fresh code. Audited.",
}
COMMENTS = {
    "org_feature_switches": (
        "A product feature the super admin turned on or off for one organisation. No row is on;"
        " the platform switch (feature_switches) off always wins.",
        {
            "customer_id": "→ customers.id, the organisation (row level security filters on it).",
            "feature": "scan_ai, ask, ensemble, autofile, cost_saving, review or my_words.",
            "enabled": "On for this organisation (true) or off.",
            "updated_by": "→ operators.id, who flipped it last (the app can't read this column).",
            "updated_at": "When it was flipped last (the app can't read this column).",
        },
    ),
}

UPGRADE = [
    f"""
    CREATE TABLE org_feature_switches (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        feature text NOT NULL CHECK (feature IN {ORG_FEATURES}),
        enabled boolean NOT NULL DEFAULT true,
        updated_by uuid NULL REFERENCES operators (id),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, feature)
    )
    """,
    f"GRANT SELECT (customer_id, feature, enabled) ON org_feature_switches TO {APP_ROLE}",
    "ALTER TABLE org_feature_switches ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON org_feature_switches
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
    "COMMENT ON POLICY customer_only ON org_feature_switches IS"
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
    _platform(COUNT_EDITS),
]

DOWNGRADE = [
    _platform(()),
    *[f"DROP FUNCTION IF EXISTS public.{signature}" for signature in FUNCTIONS],
    "DROP TABLE IF EXISTS org_feature_switches",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
