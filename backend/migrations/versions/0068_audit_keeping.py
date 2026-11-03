"""Audit logs outlive a deleted organisation by 365 days, document reads are found on an index,
and the super admin may require two-step sign-in for an organisation's admins (#482, #481,
Tushar, 3 Nov 2026).

* ``audit_log_kept``: a deleted organisation's audit rows, as they were, with when it was
  deleted. ``kaagaz_finish_customer_delete`` (0047's body, one statement added) copies them
  there before the customers row goes (its cascade still takes ``audit_log``). The
  ``customer_only`` policy and NO grant to any role: the app, the operator role and every
  organisation see nothing; only the owner (the server's command line) reads it.
* ``kaagaz_forget_kept_audit(now)``: the worker's sweep (``kaagaz_app`` only, as 0019's), one
  set-based DELETE of the rows deleted KEEP_DAYS ago or more on both clocks; how many.
  ``audit_log_kept_deleted`` keeps it to those rows.
* ``audit_log_views`` (partial, ``document.view`` rows only): a person's last view of one
  document is one index probe (``kaagaz.db.audit.record_view``), now that page images, word
  lists, readings, cells, clear copies, compares and the viewer's file fetch log it too.
  Built CONCURRENTLY first, in an autocommit block, as 0020's ``audit_log_feed``.
* ``customers.admin_two_step_required``: false by default, so nothing changes on release day.
  While true, an admin of that organisation who turned two-step sign-in off sets it up at the
  next sign-in and may not turn it off (``kaagaz.db.users``). The app keeps SELECT only (its
  UPDATE on customers is per column, 0014); only ``kaagaz_op_set_admin_two_step`` writes it.
* ``kaagaz_op_admin_two_step`` (read) and ``kaagaz_op_set_admin_two_step`` (a fresh code,
  step-up; one ``org.admin_two_step_on`` or ``_off`` row in ``platform_audit`` and in the
  organisation's own log, ``{"by": "operator"}``): the operator's own functions, as 0040's.

The downgrade drops the functions, the column, the index and the table (the kept rows are
lost), and puts 0047's finish back.

Revision ID: 0068
Revises: 0067 (limited logins)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

PREVIOUS = "0067"
revision = "0068"
down_revision = PREVIOUS
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{name[:4]}_for_0068", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0038 = _load("0038_ai_task_settings.py")
_0047 = _load("0047_purge_organization_words.py")
APP_ROLE = _0038.APP_ROLE
OPERATOR_ROLE = _0038.OPERATOR_ROLE
_ENABLED = _0038._ENABLED
_literal = _0038._literal
LOCK_TIMEOUT = "10s"  # as env.py's
KEEP_DAYS = 365  # Tushar, 3 Nov 2026: a deleted organisation's audit log is kept this long
INDEX = "audit_log_views"

# Before the customers row (and with it audit_log) goes: every audit row, as it was.
_KEEP = """
    INSERT INTO public.audit_log_kept (id, customer_id, actor_user_id, action, target_type, target_id,
                                       details, created_at, folder_id, family_id, deleted_at)
    SELECT a.id, a.customer_id, a.actor_user_id, a.action, a.target_type, a.target_id, a.details,
           a.created_at, a.folder_id, a.family_id, now()
    FROM public.audit_log a WHERE a.customer_id = p_customer;"""

FUNCTIONS = {
    # The worker's sweep: rows kept KEEP_DAYS since the deletion, on both clocks; how many went.
    "kaagaz_forget_kept_audit(timestamptz)": (
        APP_ROLE,
        f"""
    CREATE FUNCTION public.kaagaz_forget_kept_audit(p_now timestamptz) RETURNS bigint
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
        WITH gone AS (
            DELETE FROM public.audit_log_kept k
            WHERE k.deleted_at <= least(p_now, now()) - interval '{KEEP_DAYS} days'
            RETURNING 1
        )
        SELECT count(*) FROM gone
    $$
    """,
    ),
    # Whether one organisation requires two-step sign-in for its admins; NULL: no such one.
    "kaagaz_op_admin_two_step(uuid, uuid)": (
        OPERATOR_ROLE,
        f"""
    CREATE FUNCTION public.kaagaz_op_admin_two_step(p_operator uuid, p_customer uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT c.admin_two_step_required FROM public.customers c
        WHERE c.id = p_customer AND EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
    """,
    ),
    # Require it or not, with a fresh code (step-up); audited in both logs when it changes.
    "kaagaz_op_set_admin_two_step(uuid, uuid, boolean, bigint)": (
        OPERATOR_ROLE,
        f"""
    CREATE FUNCTION public.kaagaz_op_set_admin_two_step(
        p_operator uuid, p_customer uuid, p_required boolean, p_step bigint, OUT outcome text
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_status text;
        v_required boolean;
        v_action text := CASE WHEN p_required THEN 'org.admin_two_step_on' ELSE 'org.admin_two_step_off' END;
    BEGIN
        IF p_required IS NULL THEN
            RAISE EXCEPTION 'required must be true or false' USING ERRCODE = 'check_violation';
        END IF;
        {_0038._ACTOR}
        {_0038._STEP_UP}
        SELECT c.status, c.admin_two_step_required INTO v_status, v_required
        FROM public.customers c WHERE c.id = p_customer FOR UPDATE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        IF v_status = 'deleting' THEN
            outcome := 'deleting';
            RETURN;
        END IF;
        IF v_required = p_required THEN
            outcome := 'unchanged';
            RETURN;
        END IF;
        UPDATE public.customers AS c SET admin_two_step_required = p_required WHERE c.id = p_customer;
        INSERT INTO public.audit_log (customer_id, actor_user_id, action, target_type, target_id, details)
        VALUES (p_customer, NULL, v_action, 'customer', p_customer::text, jsonb_build_object('by', 'operator'));
        INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id)
        VALUES (p_operator, v_action, p_customer, 'customer', p_customer::text);
        outcome := 'done';
    END
    $$
    """,
    ),
}
FUNCTION_COMMENTS = {
    "kaagaz_forget_kept_audit": f"Worker: delete the kept audit rows of organisations deleted {KEEP_DAYS}"
    " days ago or more (both clocks). Returns how many.",
    "kaagaz_op_admin_two_step": "Operator: whether one organisation requires two-step sign-in for its"
    " admins. NULL for an unknown organisation.",
    "kaagaz_op_set_admin_two_step": "Operator: require two-step sign-in for one organisation's admins, or"
    " stop requiring it, with a fresh code. Audited in both logs.",
}
TABLE_COMMENT = (
    f"A deleted organisation's audit rows, kept {KEEP_DAYS} days after the deletion, then removed by"
    " the worker. No role is granted it: only the owner reads it."
)
COLUMN_COMMENTS = {
    "id": "audit_log.id of the row, as it was.",
    "customer_id": "customers.id of the deleted organisation (no foreign key: it is gone).",
    "actor_user_id": "users.id of who acted, as it was (that user is gone too).",
    "action": "What happened, as in audit_log.",
    "target_type": "Kind of thing acted on, as in audit_log.",
    "target_id": "Id of the thing acted on, as in audit_log.",
    "details": "Ids, statuses and counts only, as in audit_log.",
    "created_at": "When it happened.",
    "folder_id": "The folder at write time, as in audit_log.",
    "family_id": "The document family at write time, as in audit_log.",
    "deleted_at": f"When the organisation was deleted; the row goes {KEEP_DAYS} days later.",
}
TWO_STEP_COMMENT = (
    "The super admin requires two-step sign-in for this organisation's admins: one who turned it off"
    " sets it up at the next sign-in and may not turn it off. Default false."
)

UPGRADE = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",  # the autocommit block ended env.py's
    """
    CREATE TABLE audit_log_kept (
        id bigint PRIMARY KEY,
        customer_id uuid NOT NULL,
        actor_user_id uuid NULL,
        action text NOT NULL,
        target_type text NULL,
        target_id text NULL,
        details jsonb NOT NULL,
        created_at timestamptz NOT NULL,
        folder_id uuid NULL,
        family_id uuid NULL,
        deleted_at timestamptz NOT NULL
    )
    """,
    "CREATE INDEX audit_log_kept_deleted ON audit_log_kept (deleted_at)",
    "ALTER TABLE audit_log_kept ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON audit_log_kept
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
    f"COMMENT ON TABLE audit_log_kept IS {_literal(TABLE_COMMENT)}",
    *[
        f"COMMENT ON COLUMN audit_log_kept.{column} IS {_literal(comment)}"
        for column, comment in COLUMN_COMMENTS.items()
    ],
    "COMMENT ON POLICY customer_only ON audit_log_kept IS"
    " 'No role is granted the table; the policy is the second lock.'",
    f"COMMENT ON INDEX {INDEX} IS"
    " 'A person''s last view of one document, for the view log''s once-in-30-minutes check.'",
    "ALTER TABLE customers ADD COLUMN admin_two_step_required boolean NOT NULL DEFAULT false",
    f"COMMENT ON COLUMN customers.admin_two_step_required IS {_literal(TWO_STEP_COMMENT)}",
    _0047._FINISH.format(words=_0047._WORDS + _KEEP),
    *[
        statement
        for signature, (role, create) in FUNCTIONS.items()
        for statement in (
            create,
            f"COMMENT ON FUNCTION public.{signature} IS {_literal(FUNCTION_COMMENTS[signature.split('(')[0]])}",
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{signature} TO {role}",
        )
    ],
]

DOWNGRADE = [
    *[f"DROP FUNCTION IF EXISTS public.{signature}" for signature in FUNCTIONS],
    _0047._FINISH.format(words=_0047._WORDS),  # 0047's body
    "ALTER TABLE customers DROP COLUMN IF EXISTS admin_two_step_required",
    f"DROP INDEX IF EXISTS public.{INDEX}",
    "DROP TABLE IF EXISTS audit_log_kept",
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{INDEX}")  # a failed build's leftover
        op.execute(
            f"CREATE INDEX CONCURRENTLY {INDEX} ON public.audit_log"
            " (customer_id, actor_user_id, target_id, created_at DESC) WHERE action = 'document.view'"
        )
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
