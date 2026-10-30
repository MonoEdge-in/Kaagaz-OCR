"""An admin turns their own two-step sign-in off, and it stays off (Tushar, 30 Oct 2026).

* ``users.totp_off_at``: when the admin turned it off themselves; NULL otherwise. With no
  code, it tells "turned off, sign in with the password" from "new or reset, set it up at
  the next sign-in" (0035). No existing column says that: a reset clears the same code
  columns as turning off does.

Every reset of the code (the admin screen, the CLI, the operator) means "set it up again",
so each clears it too: the app's ``users.reset_totp`` in code, and the operator's
``kaagaz_op_change_admin`` (0017) here, its body read back from the catalogue and one line
added exactly once, or the migration stops (0060's way). CREATE OR REPLACE keeps its owner,
grants and comment, and the body keeps SECURITY DEFINER and its empty search path.

One nullable column with no default (no table rewrite, no row written). ``users`` keeps its
row level security and table grants (0001), which cover it. The downgrade takes the line out
of the function, then drops the column (an admin who turned it off sets it up at the next
sign-in).

Revision ID: 0061b
Revises: 0060 (Dynamic's count)
"""

from alembic import op

revision = "0061b"
down_revision = "0060"
branch_labels = None
depends_on = None

FUNCTION = "kaagaz_op_change_admin(uuid, uuid, uuid, text, bigint)"
OLD = "totp_last_step = CASE WHEN p_change = 'reset_totp' THEN NULL ELSE u.totp_last_step END,"
NEW = OLD + " totp_off_at = CASE WHEN p_change = 'reset_totp' THEN NULL ELSE u.totp_off_at END,"
COMMENT = (
    "When the admin turned their own two-step sign-in off; NULL otherwise. While set (and no code"
    " is on) they sign in with the password alone; turning it on again or any reset clears it."
)

# ``old`` replaced by ``new`` in the live body, which must hold it exactly once. The message
# leaves ``old`` out: its quotes would end the string.
_REWRITE = """
DO $body$
DECLARE
    v_def text := pg_get_functiondef('public.{function}'::regprocedure);
BEGIN
    IF coalesce(array_length(string_to_array(v_def, $k${old}$k$), 1), 0) <> 2 THEN
        RAISE EXCEPTION '{function} does not hold the reset line exactly once';
    END IF;
    EXECUTE replace(v_def, $k${old}$k$, $k${new}$k$);
END
$body$
"""


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN totp_off_at timestamptz NULL")
    op.execute("COMMENT ON COLUMN users.totp_off_at IS '" + COMMENT.replace("'", "''") + "'")
    op.execute(_REWRITE.format(function=FUNCTION, old=OLD, new=NEW))


def downgrade() -> None:
    op.execute(_REWRITE.format(function=FUNCTION, old=NEW, new=OLD))
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS totp_off_at")
