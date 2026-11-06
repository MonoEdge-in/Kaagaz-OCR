"""Storage limit per organisation: the merge of production's 0061d with 0074 (#516, Tushar, 5 Nov
2026). NEEDS VRUSHIT'S APPROVAL: schema.

Production ran ``0061d`` (this branch carries its file byte for byte, a branch off 0061c):
``org_storage_limits``, ``platform_ai_settings.storage_gb`` and the operator's two functions
(``kaagaz_op_storage_limit``, ``kaagaz_op_set_storage_limit``). This revision only merges 0061d
with 0074 and changes nothing, as 0071 merged 0061c.

Nothing of 0061d is missing on Testing-Env, so nothing is added here:

* The limited logins (0067): ``kaagaz_api`` and ``kaagaz_ops`` hold no right of their own; each
  only switches into its mode (``kaagaz_app``, ``kaagaz_operator``), and every right comes
  through that switch (tests/test_isolation_logins.py: nothing in ``public`` names a login).
  0061d grants the app its two columns and the operator role its two functions, as 0061c did the
  folder upload limits, which 0067 never granted again either. A grant to a login itself would
  break that rule.
* 0068 and later: the new table has row level security, ``customer_only``, a comment on it, each
  column and the policy, and goes with its customer (ON DELETE CASCADE, so 0068's finish needs
  no change); each function is SECURITY DEFINER with the empty search path, no PUBLIC
  execute, and a comment. No later migration walks every table or function, so the order 0061d
  runs in changes nothing.

The test server (at 0074: 0061d, then this), production (0061d, then 0062 ... 0074, then this)
and a fresh database all end on the same schema, grants and function bodies
(tests/test_migration_0075_db.py).

The downgrade does nothing: the database is at 0074 and 0061d, 0061d's table and rows stay.

Revision ID: 0075
Revises: 0074 (Google sign-in), 0061d (production's storage limit)
"""

revision = "0075"
down_revision = ("0074", "0061d")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
