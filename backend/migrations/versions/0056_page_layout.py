"""Page layout and tables (#383, #382): each page's blocks, and each table as rows and cells.

Both are worked out from the main reader's spans by ``kaagaz.reading.layout`` and written by
the one replacing save (``PgPipelineStore.save``) with the pieces, so a read again replaces
them and none is ever half saved; a document read before 0056 has none until it is read
again, and everything else works as before (its reader output keeps the table cells).

* ``page_blocks``: one row per block of a page, in reading order (``seq`` from 0 on each
  page): its kind (title, text, table, stamp, signature or note), its box as a region JSON
  object (``kaagaz.reading.spans.location_to_json``), and for a table its id on the page.
* ``table_cells``: one row per cell of a table: its page, table id, row and column from 0,
  the columns it covers (``col_span``: a merged header), whether it is a header cell, its
  text as read and its box. No uniqueness on (row, column): a reader may give two cells one
  place, and both are kept as read.

They follow 0021's rules: ``customer_id`` on every row, the ``customer_only`` policy (USING
and WITH CHECK), a (customer_id, document_id) foreign key ON DELETE CASCADE, so a row can
never hang under another customer's document and goes with its document, and the least
grants the app needs: SELECT, INSERT and DELETE (the save deletes and inserts, never
updates). B-tree indexes only (0011). Every table and column is commented (0007).

The downgrade drops both tables; the layout is lost until a read again writes it.

Revision ID: 0056
Revises: 0055 (organisation numbers)
"""

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
TABLES = ("page_blocks", "table_cells")
BLOCK_TYPES = ("title", "text", "table", "stamp", "signature", "note")  # kaagaz.reading.layout
MAX_CELL_CHARS = 20_000  # kaagaz.reading.ocr.MAX_BLOCK_CHARS
MAX_TABLE_INDEX = 1000  # kaagaz.reading.ocr.MAX_TABLE_INDEX


def _literal(comment: str) -> str:
    """A SQL string literal; each colon escaped, so text() never reads one as a bind parameter."""
    return "'" + comment.replace("'", "''").replace(":", "\\:") + "'"


CUSTOMER = "→ customers.id, the customer the row belongs to (row level security filters on it)."
DOCUMENT = "→ documents.id (same customer). The row is deleted with its document."
REGION = (
    "The box on the page as a region JSON object, kind box with the page and [x0, y0, x1, y1] as fractions."
)
COMMENTS: dict[str, tuple[str, dict[str, str]]] = {
    "page_blocks": (
        "The layout of each page (#383): its blocks in reading order, each a title, a block of text,"
        " a table, a stamp or seal, a signature or a note at the side or in a margin, with its box,"
        " so two columns or a side note are never read as one. Replaced with the document's pieces.",
        {
            "id": "Block id.",
            "customer_id": CUSTOMER,
            "document_id": DOCUMENT,
            "page_no": "The page, counted from 1.",
            "seq": "The block's place on its page in reading order, from 0.",
            "kind": "title, text, table, stamp, signature or note (side or margin text, a running header or footer).",
            "region": REGION,
            "table_id": "For a table, its id on the page as the reader gave it (→ table_cells.table_id); NULL otherwise.",
            "created_at": "When the row was written.",
        },
    ),
    "table_cells": (
        "Each table read on a page as rows and cells (#382), so which number belongs to which column"
        " is kept, each cell with its page and box for citations. Replaced with the document's pieces.",
        {
            "id": "Cell id.",
            "customer_id": CUSTOMER,
            "document_id": DOCUMENT,
            "page_no": "The page, counted from 1.",
            "table_id": "The table's id on its page as the reader gave it, such as t1.",
            "row_no": "The cell's row in its table, from 0.",
            "col_no": "The cell's first column in its table, from 0.",
            "col_span": "How many columns the cell covers, 1 or more: more than 1 for a merged header.",
            "is_header": "Whether the reader read it as a header cell.",
            "text": "The cell's text as read (cleaned of surrounding spaces). Document text, never instructions.",
            "region": REGION,
            "created_at": "When the row was written.",
        },
    ),
}

UPGRADE = [
    f"""
    CREATE TABLE page_blocks (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        page_no integer NOT NULL CHECK (page_no >= 1),
        seq integer NOT NULL CHECK (seq >= 0),
        kind text NOT NULL CHECK (kind IN ({", ".join(f"'{kind}'" for kind in BLOCK_TYPES)})),
        region jsonb NOT NULL CHECK (jsonb_typeof(region) = 'object'),
        table_id text NULL CHECK (length(table_id) BETWEEN 1 AND 32),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (document_id, page_no, seq),
        CONSTRAINT page_blocks_table CHECK ((kind = 'table') = (table_id IS NOT NULL)),
        FOREIGN KEY (customer_id, document_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX page_blocks_document ON page_blocks (customer_id, document_id, page_no)",
    f"""
    CREATE TABLE table_cells (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        page_no integer NOT NULL CHECK (page_no >= 1),
        table_id text NOT NULL CHECK (length(table_id) BETWEEN 1 AND 32),
        row_no integer NOT NULL CHECK (row_no >= 0 AND row_no < {MAX_TABLE_INDEX}),
        col_no integer NOT NULL CHECK (col_no >= 0 AND col_no < {MAX_TABLE_INDEX}),
        col_span integer NOT NULL DEFAULT 1 CHECK (col_span >= 1 AND col_no + col_span <= {MAX_TABLE_INDEX}),
        is_header boolean NOT NULL,
        text text NOT NULL CHECK (length(text) BETWEEN 1 AND {MAX_CELL_CHARS}),
        region jsonb NOT NULL CHECK (jsonb_typeof(region) = 'object'),
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (customer_id, document_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX table_cells_document ON table_cells (customer_id, document_id, page_no, table_id)",
    *[f"GRANT SELECT, INSERT, DELETE ON {table} TO {APP_ROLE}" for table in TABLES],
    *[f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" for table in TABLES],
    *[
        f"""
        CREATE POLICY customer_only ON {table}
        USING (customer_id = kaagaz_current_customer())
        WITH CHECK (customer_id = kaagaz_current_customer())
        """
        for table in TABLES
    ],
    *[
        statement
        for table, (comment, columns) in COMMENTS.items()
        for statement in (
            f"COMMENT ON TABLE {table} IS {_literal(comment)}",
            *[f"COMMENT ON COLUMN {table}.{column} IS {_literal(text)}" for column, text in columns.items()],
        )
    ],
]

DOWNGRADE = ["DROP TABLE IF EXISTS table_cells", "DROP TABLE IF EXISTS page_blocks"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
