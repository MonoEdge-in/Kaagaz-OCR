"""Held runs are not failed runs in the operator's insights (QA P3, 4 Nov 2026; NEEDS VRUSHIT'S
APPROVAL: operator functions changed, under the never-do 1 exception as 0024's).

A document held by the reading pause (#478, kaagaz/jobs/reading_pause.py) ends its run failed
with the code ``paused_daily_limit`` or ``paused_budget``, then is read later in a new run. The
insights counted each such run as a failed one. ``kaagaz_op_daily`` (last changed by 0049),
``kaagaz_op_quality`` (0052) and ``kaagaz_op_model_board`` (0024) each count the runs that
ended on their own (0024's ``_COUNTED_RUN``) once; that condition now also leaves out a run
whose code starts ``paused_``. Each body is read back from the catalogue and the condition
replaced exactly once, or the migration stops (0060's way); CREATE OR REPLACE keeps its owner,
grants and comment, and the body keeps SECURITY DEFINER and its empty search path. No table,
column, index, grant or policy changes.

The downgrade puts 0024's condition back the same way.

Revision ID: 0069a
Revises: 0069 (QA fixes)
"""

from alembic import op

revision = "0069a"
down_revision = "0069"
branch_labels = None
depends_on = None

FUNCTIONS = (
    "kaagaz_op_daily(uuid, date, date, text)",
    "kaagaz_op_quality(uuid, date, date, text)",
    "kaagaz_op_model_board(uuid, date, date, text)",
)
OLD = "r.status NOT IN ('running', 'superseded')"  # 0024's _COUNTED_RUN
NEW = OLD + " AND coalesce(r.error_code, '') NOT LIKE 'paused\\_%'"

# ``old`` replaced by ``new`` in the live body, which must hold it exactly once (0060's).
_REWRITE = """
DO $body$
DECLARE
    v_def text := pg_get_functiondef('public.{function}'::regprocedure);
BEGIN
    IF coalesce(array_length(string_to_array(v_def, $k${old}$k$), 1), 0) <> 2 THEN
        RAISE EXCEPTION '{function} does not hold its counted runs exactly once';
    END IF;
    EXECUTE replace(v_def, $k${old}$k$, $k${new}$k$);
END
$body$
"""


def upgrade() -> None:
    for function in FUNCTIONS:
        op.execute(_REWRITE.format(function=function, old=OLD, new=NEW))


def downgrade() -> None:
    for function in FUNCTIONS:
        op.execute(_REWRITE.format(function=function, old=NEW, new=OLD))
