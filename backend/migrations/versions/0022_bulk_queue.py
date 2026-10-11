"""Bulk queue and screens (PRD 5 pull request 2, council plan of 9 Oct 2026): the reader
status each worker reports, an index for the pages read locally, and the operator's
read of the reader status.

* ``reader_status`` (U9, V4): one row per worker copy, the reading model's state as its
  gate last saw it (working, failing, down or key_problem), since when, and when the
  worker last reported. Platform data: no customer, no error text, no key, no document,
  so it has no ``customer_id`` and no row level security, like ``operators``. The app
  may read, insert and update a row (each worker upserts its own every 30 seconds),
  never delete one; a worker that stops reporting simply ages out of the operator's
  read.
* ``pages_read_locally`` (Q7): the pages the backup reader read while the main reader
  was down (``read_locally`` in ``read_issues``), so the upgrade sweep finds them per
  customer without reading every page. Partial: only those pages are in it.
* ``kaagaz_op_reader``: the reports of the last 10 minutes, one row a worker, for the
  Health screen. SECURITY DEFINER with an empty search path and schema-qualified names,
  revoked from PUBLIC and runnable by ``kaagaz_operator`` only, and answers only an
  enabled operator whose two-step secret is confirmed (no row otherwise), as 0020's.

``documents_batch`` (customer_id, batch_id), which the batch screen and "files ahead"
read, came with 0021. No customer table changes here.

The downgrade drops the function, the index and the table (the reports are lost).

Revision ID: 0022
Revises: 0021 (Gemini reader)
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
OPERATOR_ROLE = "kaagaz_operator"
SIGNATURE = "kaagaz_op_reader(uuid)"
_ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"  # as 0020's
# The states a worker reports: kaagaz.ai.openrouter's gate status (V4: "failing" shows "slow").
STATES = ("working", "failing", "down", "key_problem")
RECENT = "10 minutes"  # a worker with no report this long is not shown


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


COMMENTS = {
    "TABLE reader_status": "What each worker last saw of the reading model (PRD 5 U9): its state,"
    " since when and when it last reported. Platform data: no customer, error or key.",
    "COLUMN reader_status.worker": "The worker copy that reports, by its host name: letters, digits,"
    " dots, dashes and underscores, at most 64.",
    "COLUMN reader_status.state": "The reading model's state as this worker's gate sees it: working,"
    " failing (shown as slow), down or key_problem (a wrong key or a spent cap).",
    "COLUMN reader_status.since": "When the state began, as the gate saw it. NULL when not known.",
    "COLUMN reader_status.seen_at": "When the worker last reported (every 30 seconds). Reports older"
    " than 10 minutes are not shown.",
    "INDEX pages_read_locally": "The pages the backup reader read while the main reader was down"
    " (read_locally), per customer, for the sweep that reads them again (Q7).",
    f"FUNCTION public.{SIGNATURE}": "Operator health: each worker's reader state from its reports of"
    " the last 10 minutes. Status only, never an error, key or customer.",
}

UPGRADE = [
    f"""
    CREATE TABLE reader_status (
        worker text PRIMARY KEY CHECK (worker ~ '^[A-Za-z0-9_.-]{{1,64}}$'),
        state text NOT NULL CHECK (state IN ({", ".join(_literal(state) for state in STATES)})),
        since timestamptz NULL,
        seen_at timestamptz NOT NULL
    )
    """,
    f"GRANT SELECT, INSERT, UPDATE ON reader_status TO {APP_ROLE}",
    "CREATE INDEX pages_read_locally ON pages (customer_id, document_id) WHERE 'read_locally' = ANY (read_issues)",
    f"""
    CREATE FUNCTION public.kaagaz_op_reader(p_operator uuid)
    RETURNS TABLE (worker text, state text, since timestamptz, seen_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT r.worker, r.state, r.since, r.seen_at
        FROM public.reader_status r
        WHERE r.seen_at > now() - interval '{RECENT}'
          AND EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        ORDER BY r.worker
    $$
    """,
    f"REVOKE ALL ON FUNCTION public.{SIGNATURE} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION public.{SIGNATURE} TO {OPERATOR_ROLE}",
    *[f"COMMENT ON {target} IS {_literal(comment)}" for target, comment in COMMENTS.items()],
]

DOWNGRADE = [
    f"DROP FUNCTION IF EXISTS public.{SIGNATURE}",
    "DROP INDEX IF EXISTS pages_read_locally",
    "DROP TABLE IF EXISTS reader_status",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
