"""Speed controls the super admin changes live (package S, 24 Oct 2026): three platform values
on ``platform_ai_settings``' one row (0026), each NULL for "use the server's setting". The app
never lets one go above the server's own value, the deployed capacity: the operator can only go
lower.

* ``model_rpm``: model requests per minute for the whole platform, read at every admission of
  the shared scheduler (0046), under MODEL_RPM (``kaagaz.db.engine``).
* ``documents_at_once``: documents read at the same time across every worker copy, read before
  each job, under WORKER_REPLICAS (``kaagaz.jobs.fair_share.ReadingCap``).
* ``pages_at_once``: pages of one document read at the same time, read at the start of each
  job, under OCR_PAGE_CONCURRENCY (``kaagaz.pipeline``).

The app already reads the table (SELECT, ``read_all``); it never writes it.

* ``kaagaz_op_speed``: the three values; nothing for an operator who is not enabled.
* ``kaagaz_op_set_speed``: sets all three (NULL: the server's setting) with a fresh code
  (step-up) and one ``platform_audit`` row (``speed.set``, the old and new values). A changed
  RPM frees the scheduler's next turn, so the next admission uses it.

The operator's functions follow 0026's rules (SECURITY DEFINER, an empty search path,
schema-qualified names, revoked from PUBLIC, runnable by ``kaagaz_operator`` only, acting only
for an enabled operator whose two-step secret is confirmed).

Three nullable columns with no default (no table rewrite). The downgrade drops the two
functions and the three columns (the values are lost).

Revision ID: 0053
Revises: 0052 (PRD 6 switches)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def _load_0038() -> ModuleType:
    path = Path(__file__).with_name("0038_ai_task_settings.py")
    spec = importlib.util.spec_from_file_location("migration_0038_for_0053", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_OLD = _load_0038()
OPERATOR_ROLE = _OLD.OPERATOR_ROLE
_ENABLED = _OLD._ENABLED
_literal = _OLD._literal
RPM = "BETWEEN 1 AND 1200"  # 0046's platform ceiling

FUNCTIONS = {
    "kaagaz_op_speed(uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_speed(p_operator uuid)
    RETURNS TABLE (model_rpm integer, documents_at_once integer, pages_at_once integer)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT p.model_rpm, p.documents_at_once, p.pages_at_once
        FROM public.platform_ai_settings p
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
    """,
    "kaagaz_op_set_speed(uuid, integer, integer, integer, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_set_speed(
        p_operator uuid, p_rpm integer, p_documents integer, p_pages integer, p_step bigint,
        OUT outcome text
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_rpm integer;
        v_documents integer;
        v_pages integer;
    BEGIN
        {_OLD._ACTOR}
        IF p_rpm NOT {RPM} OR p_documents < 1 OR p_pages < 1 THEN
            RAISE EXCEPTION 'speed setting out of range' USING ERRCODE = 'check_violation';
        END IF;
        {_OLD._STEP_UP}
        SELECT p.model_rpm, p.documents_at_once, p.pages_at_once INTO v_rpm, v_documents, v_pages
        FROM public.platform_ai_settings p FOR UPDATE;
        IF (v_rpm, v_documents, v_pages) IS NOT DISTINCT FROM (p_rpm, p_documents, p_pages) THEN
            outcome := 'unchanged';
            RETURN;
        END IF;
        UPDATE public.platform_ai_settings AS p
        SET model_rpm = p_rpm, documents_at_once = p_documents, pages_at_once = p_pages;
        IF v_rpm IS DISTINCT FROM p_rpm THEN
            UPDATE public.model_rate_scheduler AS s SET next_at = least(s.next_at, clock_timestamp()) WHERE s.id;
        END IF;
        INSERT INTO public.platform_audit (operator_id, action, target_type, target_id, details)
        VALUES (p_operator, 'speed.set', 'platform', NULL, jsonb_build_object(
            'old_model_rpm', v_rpm, 'new_model_rpm', p_rpm,
            'old_documents_at_once', v_documents, 'new_documents_at_once', p_documents,
            'old_pages_at_once', v_pages, 'new_pages_at_once', p_pages));
        outcome := 'done';
    END
    $$
    """,
}
FUNCTION_COMMENTS = {
    "kaagaz_op_speed": "Operator: the platform's speed controls (NULL: the server's setting).",
    "kaagaz_op_set_speed": "Operator: set the platform's model RPM, documents and pages read at once"
    " (NULL: the server's setting), with a fresh code. Audited.",
}
COLUMN_COMMENTS = {
    "model_rpm": "Model requests per minute for the whole platform, 1 to 1200; the app uses the lower"
    " of it and MODEL_RPM. NULL: MODEL_RPM.",
    "documents_at_once": "Documents read at the same time across the workers; the app uses the lower of"
    " it and WORKER_REPLICAS. NULL: WORKER_REPLICAS.",
    "pages_at_once": "Pages of one document read at the same time; the app uses the lower of it and"
    " OCR_PAGE_CONCURRENCY. NULL: OCR_PAGE_CONCURRENCY.",
}

UPGRADE = [
    "ALTER TABLE platform_ai_settings"
    f" ADD COLUMN model_rpm integer NULL CONSTRAINT platform_model_rpm CHECK (model_rpm {RPM}),"
    " ADD COLUMN documents_at_once integer NULL CONSTRAINT platform_documents_at_once"
    " CHECK (documents_at_once >= 1),"
    " ADD COLUMN pages_at_once integer NULL CONSTRAINT platform_pages_at_once CHECK (pages_at_once >= 1)",
    *[
        f"COMMENT ON COLUMN platform_ai_settings.{column} IS {_literal(comment)}"
        for column, comment in COLUMN_COMMENTS.items()
    ],
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
    "ALTER TABLE platform_ai_settings DROP COLUMN IF EXISTS model_rpm,"
    " DROP COLUMN IF EXISTS documents_at_once, DROP COLUMN IF EXISTS pages_at_once",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
