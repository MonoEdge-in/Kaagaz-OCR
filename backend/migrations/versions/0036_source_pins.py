"""Ranked sources in Ask (Tushar, 16 Oct 2026): a person may put one source of an answer on
top ("Most relevant"). Each pick is kept, so the order stays on a reload and the ranking
can be tuned later from what people chose.

``source_pins`` follows 0021's rules: it carries ``customer_id``, has the ``customer_only``
policy (USING and WITH CHECK), the least grants the app needs (SELECT and INSERT: a pick is
never changed or deleted by the app; a newer one wins), and points at its message, its
document and its user with (customer_id, id) foreign keys ON DELETE CASCADE, so a row never
hangs under another customer's answer or document and goes with them. Every column is
commented (0007's rule).

No existing table changes, and the upgrade takes no lock on one (a new table only). The
downgrade drops the table: the picks are lost, the answers stay as they are.

Revision ID: 0036
Revises: 0035 (two-step sign-in required)
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"


def _literal(comment: str) -> str:
    return "'" + comment.replace("'", "''") + "'"


# Plain constants with no colon before a word, which SQLAlchemy would read as a bind parameter.
TABLE_COMMENT = (
    "One source a person put on top of an answer in Ask (Most relevant). The newest row of a"
    " message and user is the order shown; every row is kept as relevance feedback."
)
COLUMNS = {
    "id": "The pick.",
    "customer_id": "→ customers.id, the customer the row belongs to (row level security filters on it).",
    "message_id": "→ messages.id (same customer), the answer whose source was picked. Deleted with it.",
    "user_id": "→ users.id (same customer), who picked it: the conversation's owner.",
    "document_id": "→ documents.id (same customer), the picked source's document. Deleted with it.",
    "page_no": "The picked source's page.",
    "source_ref": (
        "The picked source's id in the answer (its citation's ref_id: a piece or a value), so one"
        " of two sources on the same page can be picked; NULL for a document the answer does not cite."
    ),
    "previous_rank": "Where the source was in the list before it was put on top (1 is the top).",
    "created_at": "When it was picked.",
}

UPGRADE = [
    """
    CREATE TABLE source_pins (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        message_id uuid NOT NULL,
        user_id uuid NOT NULL,
        document_id uuid NOT NULL,
        page_no integer NOT NULL CHECK (page_no >= 1),
        source_ref text NULL CHECK (length(source_ref) BETWEEN 1 AND 100),
        previous_rank integer NOT NULL CHECK (previous_rank BETWEEN 1 AND 1000),
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (customer_id, message_id) REFERENCES messages (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, document_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX source_pins_message ON source_pins (customer_id, message_id, user_id, created_at DESC)",
    f"GRANT SELECT, INSERT ON source_pins TO {APP_ROLE}",
    "ALTER TABLE source_pins ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON source_pins
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
    f"COMMENT ON TABLE source_pins IS {_literal(TABLE_COMMENT)}",
    *[
        f"COMMENT ON COLUMN source_pins.{column} IS {_literal(comment)}"
        for column, comment in COLUMNS.items()
    ],
]

DOWNGRADE = ["DROP TABLE IF EXISTS source_pins"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
