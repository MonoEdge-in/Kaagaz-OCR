"""My words: each user's own words and meanings, and where they were added (council, 3 Oct 2026).

* ``user_words``: one row per word and meaning ("malak" means "owner"). The
  folds (``word_fold``, ``meaning_fold``) find duplicates: UNIQUE per user.
* ``user_word_spots``: a word linked to a document, or to a spot on one of its
  pages. It goes with the document (0005: forget everything) and the word.

Words belong to a user, not the customer: besides ``customer_only`` each table
has the RESTRICTIVE ``own_user_only`` policy on ``kaagaz.user_id``, which only
``Database.user`` sets. It must pass too, so any other transaction (another
user, an admin, ``Database.customer``) sees and writes none: it fails closed.
Users are only disabled, never deleted; a deleted one would take their words.
Every new table and column has a comment (the 0007 rule), no index does. The
downgrade drops both tables.

PROVISIONAL (Vrushit): the number (PR 2b may take 0008, then this is 0009), the
limits (40, 80 and 200 characters, a 256-byte region) and the restrictive policy.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
TABLES = ("user_words", "user_word_spots")
OWN_USER = "user_id = NULLIF(current_setting('kaagaz.user_id', true), '')::uuid"

CUSTOMER = "→ customers.id, the customer the row belongs to (row level security filters on it)."
USER = "→ users.id (same customer), the one user who owns the row. Only that user's transactions see it."
CREATED = "When the row was written."

# table → (table comment, {column: comment}), as in 0007. No colon before a word.
COMMENTS: dict[str, tuple[str, dict[str, str]]] = {
    "user_words": (
        "My words: a word one user defined and its meaning in English, used only for that user's questions."
        " At most 500 per user and 3 meanings per word.",
        {
            "id": "Word id.",
            "customer_id": CUSTOMER,
            "user_id": USER,
            "word": "The word as the user typed it, 1 to 40 characters (letters, marks, digits, spaces, hyphens).",
            "word_fold": "word in Unicode NFC, case folded, spaces collapsed. Duplicates are found by it.",
            "meaning": "What the word means, in English, 1 to 80 characters (letters, marks, digits, spaces, hyphens, commas).",
            "meaning_fold": "meaning folded the same way as word_fold.",
            "as_read": "The text as the reader read it on the page, verbatim, when added from the viewer. NULL otherwise.",
            "source": "Where it was added: manual (the My words page), upload (with an upload) or viewer (on a page).",
            "created_at": CREATED,
            "updated_at": "When the row last changed (a trigger sets it).",
        },
    ),
    "user_word_spots": (
        "Where one user added a word: an uploaded document, or a spot on one of its pages."
        " Deleted with the document or the word.",
        {
            "id": "Spot id.",
            "customer_id": CUSTOMER,
            "user_id": USER,
            "word_id": "→ user_words.id of the same user and customer.",
            "document_id": "→ documents.id (same customer).",
            "page_no": "Page number from 1. NULL when the word is linked to the whole document.",
            "region": 'The spot on that page, JSON {"kind": "box", "page", "box": [x0, y0, x1, y1]} or'
            ' {"kind": "cell", "page", "row", "column"} as in pieces.regions, at most 256 bytes. NULL with page_no.',
            "created_at": CREATED,
        },
    ),
}


def _literal(comment: str) -> str:
    return "'" + comment.replace("'", "''") + "'"


UPGRADE = [
    """
    CREATE TABLE user_words (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        user_id uuid NOT NULL,
        word text NOT NULL CHECK (char_length(word) BETWEEN 1 AND 40),
        word_fold text NOT NULL CHECK (char_length(word_fold) >= 1),
        meaning text NOT NULL CHECK (char_length(meaning) BETWEEN 1 AND 80),
        meaning_fold text NOT NULL CHECK (char_length(meaning_fold) >= 1),
        as_read text NULL CHECK (char_length(as_read) BETWEEN 1 AND 200),
        source text NOT NULL CHECK (source IN ('manual', 'upload', 'viewer')),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, id),
        UNIQUE (customer_id, user_id, id),
        CONSTRAINT user_words_same_meaning UNIQUE (customer_id, user_id, word_fold, meaning_fold),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE TRIGGER user_words_touch BEFORE UPDATE ON user_words FOR EACH ROW EXECUTE FUNCTION kaagaz_touch()",
    """
    CREATE TABLE user_word_spots (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        user_id uuid NOT NULL,
        word_id uuid NOT NULL,
        document_id uuid NOT NULL,
        page_no integer NULL CHECK (page_no >= 1),
        region jsonb NULL CHECK (jsonb_typeof(region) = 'object' AND pg_column_size(region) <= 256),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT user_word_spots_page_with_region CHECK ((page_no IS NULL) = (region IS NULL)),
        CONSTRAINT user_word_spots_region_page CHECK (region -> 'page' = to_jsonb(page_no)),
        CONSTRAINT user_word_spots_once UNIQUE NULLS NOT DISTINCT
            (customer_id, user_id, word_id, document_id, page_no, region),
        FOREIGN KEY (customer_id, user_id, word_id)
            REFERENCES user_words (customer_id, user_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    # For the page list, and for the delete that follows a document.
    "CREATE INDEX user_word_spots_page ON user_word_spots (customer_id, document_id, user_id, page_no)",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON user_words TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, DELETE ON user_word_spots TO {APP_ROLE}",
    *[
        statement
        for table in TABLES
        for statement in (
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"CREATE POLICY customer_only ON {table} USING (customer_id = kaagaz_current_customer())"
            " WITH CHECK (customer_id = kaagaz_current_customer())",
            f"CREATE POLICY own_user_only ON {table} AS RESTRICTIVE USING ({OWN_USER}) WITH CHECK ({OWN_USER})",
        )
    ],
    *[
        statement
        for table, (table_comment, columns) in COMMENTS.items()
        for statement in (
            f"COMMENT ON TABLE {table} IS {_literal(table_comment)}",
            *[f"COMMENT ON COLUMN {table}.{column} IS {_literal(text)}" for column, text in columns.items()],
        )
    ],
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_word_spots, user_words")
