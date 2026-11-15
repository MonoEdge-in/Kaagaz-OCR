"""PROVISIONAL NUMBER: renumbered with the WhatsApp drafts (after #612's 0088) before raising.

WhatsApp's voice notes in the operator's spend split (#603 W6, PRD 7 section 15; NEEDS VRUSHIT'S
APPROVAL: one operator function changed, under the never-do 1 exception as 0034's and 0049's).

**``kaagaz_op_daily``** (0034, last changed by 0069a) counts the new usage kind ``transcribe`` (a
WhatsApp voice note written down, part of asking a question; 0023's CHECK already allows it) as
question spend: its body is read back from the catalogue and ``'chat', 'memory', 'search',
'ensemble_chat'`` becomes ``'chat', 'memory', 'search', 'ensemble_chat', 'transcribe'``, exactly
once or the migration stops (0049's way); its grants and comment are kept (CREATE OR REPLACE). No
table, column, index, grant or policy changes.

The downgrade puts the body back as it was, the same way.

Revision ID: 0089
Revises: 0088 (Kaagaz on WhatsApp's tables)
"""

from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None

DAILY = "public.kaagaz_op_daily(uuid, date, date, text)"
KINDS_0034 = "'chat', 'memory', 'search', 'ensemble_chat'"
KINDS_0089 = "'chat', 'memory', 'search', 'ensemble_chat', 'transcribe'"

# The question kinds in the function's body, replaced exactly once (the module docstring).
_REWRITE_DAILY = """
DO $body$
DECLARE
    v_def text := pg_get_functiondef('{daily}'::regprocedure);
BEGIN
    IF coalesce(array_length(string_to_array(v_def, $k${old}$k$), 1), 0) <> 2 THEN
        RAISE EXCEPTION 'kaagaz_op_daily does not list its question kinds exactly once';
    END IF;
    EXECUTE replace(v_def, $k${old}$k$, $k${new}$k$);
END
$body$
"""


def upgrade() -> None:
    op.execute(_REWRITE_DAILY.format(daily=DAILY, old=KINDS_0034, new=KINDS_0089))


def downgrade() -> None:
    op.execute(_REWRITE_DAILY.format(daily=DAILY, old=KINDS_0089, new=KINDS_0034))
