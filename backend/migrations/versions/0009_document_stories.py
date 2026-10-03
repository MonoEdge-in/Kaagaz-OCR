"""Story of this PDF: one user's own note about a document, to find it again (council, 3 Oct 2026).

* ``document_stories``: one row per user and document family, keyed on the
  family (``documents.family_id``, the first version's id), so a new version
  keeps the story and a "Read again" never touches it. It goes with the family
  (0005: forget everything) and with the user.

A story is what the user said, never evidence: it feeds no answer, gate or
prompt. Like My words (0008) it belongs to one user: besides ``customer_only``
it has the RESTRICTIVE ``own_user_only`` policy on ``kaagaz.user_id``, which
only ``Database.user`` sets, so any other transaction sees and writes none.
The one index serves the delete that follows a family; no vector or word index
yet (a user's own stories are few, an exact scan). The comments follow 0007.
The downgrade drops the table.

PROVISIONAL (Vrushit): personal only, one story per family, 1,000 characters,
and the number (My words and PR 2b's sound key both take 0008 on their stacks;
the second to merge renumbers, and this one may become 0010).

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
OWN_USER = "user_id = NULLIF(current_setting('kaagaz.user_id', true), '')::uuid"

# The table comment, then {column: comment}, as in 0007. No colon before a word.
TABLE = (
    "Story of this PDF: what one user wrote about a document family, in any language, so they can"
    " find it by telling the story again. Only that user sees it. Never evidence for an answer."
)
COLUMNS = {
    "customer_id": "→ customers.id, the customer the row belongs to (row level security filters on it).",
    "user_id": "→ users.id (same customer), the one user who wrote the story. Only that user's transactions see it.",
    "family_id": "→ documents.id of the family's first version (same customer), so every version shares the story.",
    "story": "The story as the user wrote it, 1 to 1000 characters, Unicode NFC, control and direction characters removed.",
    "story_latin": "story with its Devanagari spelt in Latin letters, all lower case, for typo and word matching. At most 4000 characters.",
    "tsv_simple": "Generated. Word index of story and story_latin with no stemming, for any script.",
    "embedding": "Meaning vector (halfvec, 1024 numbers) of story, with the recipe in embedding_model. NULL until embedded, and again after an edit.",
    "embedding_model": "What made the vector: the MODEL_EMBED model name, then # and the recipe story1. NULL together with embedding.",
    "created_at": "When the row was written.",
    "updated_at": "When the row last changed, its vector included (a trigger sets it).",
}


def _literal(comment: str) -> str:
    return "'" + comment.replace("'", "''") + "'"


UPGRADE = [
    """
    CREATE TABLE document_stories (
        customer_id uuid NOT NULL,
        user_id uuid NOT NULL,
        family_id uuid NOT NULL,
        story text NOT NULL CHECK (char_length(story) BETWEEN 1 AND 1000),
        story_latin text NOT NULL CHECK (char_length(story_latin) <= 4000),
        tsv_simple tsvector GENERATED ALWAYS AS (to_tsvector('simple', story || ' ' || story_latin)) STORED,
        embedding halfvec(1024) NULL,
        embedding_model text NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, user_id, family_id),
        CONSTRAINT document_stories_vector_with_model CHECK ((embedding IS NULL) = (embedding_model IS NULL)),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, family_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE TRIGGER document_stories_touch BEFORE UPDATE ON document_stories"
    " FOR EACH ROW EXECUTE FUNCTION kaagaz_touch()",
    # For the delete that follows a document family (the key above).
    "CREATE INDEX document_stories_family ON document_stories (customer_id, family_id)",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON document_stories TO {APP_ROLE}",
    "ALTER TABLE document_stories ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY customer_only ON document_stories USING (customer_id = kaagaz_current_customer())"
    " WITH CHECK (customer_id = kaagaz_current_customer())",
    f"CREATE POLICY own_user_only ON document_stories AS RESTRICTIVE USING ({OWN_USER}) WITH CHECK ({OWN_USER})",
    f"COMMENT ON TABLE document_stories IS {_literal(TABLE)}",
    *[f"COMMENT ON COLUMN document_stories.{column} IS {_literal(text)}" for column, text in COLUMNS.items()],
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS document_stories")
