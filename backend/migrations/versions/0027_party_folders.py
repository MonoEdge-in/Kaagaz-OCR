"""Party folders (backlog 28b, final polish 16 Oct 2026): auto-filing puts a verified
invoice into a folder for the other party inside the type folder ("Invoices / Mehta
Textiles", ``kaagaz.db.autofile``). That folder's ``folders.autofile_key`` is ``party:``
and the party's GSTIN (15 capitals and digits), so the key's CHECK (0025) now allows it
too. The unique index ``folders_autofile_key`` keeps one folder per party per customer.

Only the CHECK and the column's comment change: no new column, no table rewrite. The
CHECK reads ``folders`` once under the lock. The downgrade clears the party keys first
(the folders stay, as ordinary folders a person can use), then puts 0025's CHECK back.

Revision ID: 0027
Revises: 0026 (AI modes)
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

LOCK = "LOCK TABLE folders IN ACCESS EXCLUSIVE MODE"
CHECK = "folders_autofile_key_check"  # 0025's inline CHECK, by its default name
TYPE_KEY = "autofile_key ~ '^type:[a-z_]{1,32}$'"
PARTY_KEY = "autofile_key ~ '^party:[0-9A-Z]{15}$'"

# Plain constants with no colon before a word, which SQLAlchemy would read as a bind parameter.
COMMENT_0025 = (
    "The document type Kaagaz files into this folder: the word type, a colon"
    " and the type, one folder per type and customer. NULL, a folder Kaagaz does not file into."
)
COMMENT_NOW = (
    "The document type or invoice party Kaagaz files into this folder: the word type or party,"
    " a colon, and the type or the party's GSTIN. One folder per key and customer."
    " NULL, a folder Kaagaz does not file into."
)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


UPGRADE = [
    LOCK,
    f"ALTER TABLE folders DROP CONSTRAINT {CHECK}",
    f"ALTER TABLE folders ADD CONSTRAINT {CHECK} CHECK ({TYPE_KEY} OR {PARTY_KEY})",
    f"COMMENT ON COLUMN folders.autofile_key IS {_literal(COMMENT_NOW)}",
]

DOWNGRADE = [
    LOCK,
    "UPDATE folders SET autofile_key = NULL WHERE autofile_key ~ '^party:'",
    f"ALTER TABLE folders DROP CONSTRAINT {CHECK}",
    f"ALTER TABLE folders ADD CONSTRAINT {CHECK} CHECK ({TYPE_KEY})",
    f"COMMENT ON COLUMN folders.autofile_key IS {_literal(COMMENT_0025)}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
