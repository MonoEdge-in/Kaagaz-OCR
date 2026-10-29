"""Dynamic's count with no hidden "on" (#405): the operator's ``kaagaz_op_feature_switches``
(0038, last replaced by 0052) counts an organisation with no Dynamic of its own as using Cost
saving when there is no platform row, by ``COALESCE(s.dynamic, p.dynamic, true)``. The app's
rule (kaagaz/db/models.py) has no such default since #405: no row is off. The body is read back
from the catalogue and that text dropped exactly once, or the migration stops (0052's way);
CREATE OR REPLACE keeps its owner, grants and comment, and the body keeps SECURITY DEFINER and
its empty search path.

The downgrade puts ``, true`` back the same way.

Revision ID: 0060
Revises: 0059 (the data dictionary's gaps)
"""

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None

FUNCTION = "kaagaz_op_feature_switches(uuid)"
OLD = "COALESCE(s.dynamic, p.dynamic, true)"
NEW = "COALESCE(s.dynamic, p.dynamic)"

# ``old`` replaced by ``new`` in the live body, which must hold it exactly once.
_REWRITE = """
DO $body$
DECLARE
    v_def text := pg_get_functiondef('public.{function}'::regprocedure);
BEGIN
    IF coalesce(array_length(string_to_array(v_def, $k${old}$k$), 1), 0) <> 2 THEN
        RAISE EXCEPTION '{function} does not hold {old} exactly once';
    END IF;
    EXECUTE replace(v_def, $k${old}$k$, $k${new}$k$);
END
$body$
"""


def upgrade() -> None:
    op.execute(_REWRITE.format(function=FUNCTION, old=OLD, new=NEW))


def downgrade() -> None:
    op.execute(_REWRITE.format(function=FUNCTION, old=NEW, new=OLD))
