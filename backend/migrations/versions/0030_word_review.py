"""Word-level review and Ensemble after the scan (scan review UX rules, Tushar, 16 Oct 2026;
the council plan of 16 Oct 2026): the history of every word a person or Ensemble settled,
and Ensemble's runs and readings (the job that fills them comes later).

Everything follows 0021's rules: every new table carries ``customer_id``, has the
``customer_only`` policy (USING and WITH CHECK), the least grants the app needs, and points
at its document with a (customer_id, document_id) foreign key ON DELETE CASCADE, so a row
never hangs under another customer's document and goes with its document. Every table and
column is commented (0007's rule).

* ``word_changes``: append-only. A flagged word (a word mark, a line or a value, found from
  the stored reading by ``kaagaz.extraction.flags``) is confirmed or edited by a person, or
  resolved by Ensemble and kept by a person. The row keeps what was read, before and after,
  who and when, and what the change wrote into values and search pieces (``applied``), so
  it can be undone and written again after a read of another page. The app may update only
  ``applied`` (written again on a save) and the undo pair; it never deletes a row.
* ``ensemble_runs``: one Ensemble run on some words, the flagged words or the whole
  document, with its pages done and failed, stop and retry; at most one queued or running
  run per document (a partial unique index).
* ``ensemble_readings``: what a run found for each word (agreed, changed, unresolved, or
  skipped because a person had edited it), and a person's Keep or Revert of a change.

No existing table changes, and the upgrade takes no lock on one (new tables only). The
downgrade drops the three tables: the history and Ensemble's results are lost; the values
and pieces a change wrote stay as they are.

Revision ID: 0030
Revises: 0029 (two-step sign-in by grant)
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
NEW_TABLES = ("ensemble_runs", "ensemble_readings", "word_changes")
HEX_KEY = "~ '^[0-9a-f]{64}$'"
MAX_TEXT = 10000  # characters of one word's or line's text


def _literal(comment: str) -> str:
    return "'" + comment.replace("'", "''") + "'"


CUSTOMER = "→ customers.id, the customer the row belongs to (row level security filters on it)."
DOCUMENT = "→ documents.id (same customer). The row is deleted with its document."
WORD_KEY = (
    "The word's key: SHA-256, in lower-case hex, of its kind, page, spot and text as read"
    " (kaagaz.extraction.flags.word_key), so it stays the same when another page is read again."
)
REGION = "The word's spot on the page, as the reading stored it (kind, page and box)."

# {table: (table comment, {column: comment})}, as in 0021.
# Plain constants with no colon before a word, which SQLAlchemy would read as a bind parameter.
COMMENTS: dict[str, tuple[str, dict[str, str]]] = {
    "ensemble_runs": (
        "One Ensemble run after the scan: some words, the flagged words or the whole document read"
        " again by other models and compared. Deleted with its document.",
        {
            "id": "Ensemble run id.",
            "customer_id": CUSTOMER,
            "document_id": DOCUMENT,
            "scope": "What the run reads: word (one word), words (the words a person chose), flagged"
            " (every flagged word) or document (every scanned page).",
            "pages": "The pages the run reads, counted from 1.",
            "pages_done": "The pages finished and saved, each one of pages.",
            "pages_failed": "The pages that could not be finished, each one of pages; a retry reads them.",
            "models": "The models that read again, by their OpenRouter ids (1 to 3).",
            "status": "queued, running, done, stopped (a person stopped it; finished pages stay) or"
            " failed (error_code says why).",
            "requested_by": "→ users.id (same customer) who started the run.",
            "stop_requested_at": "When a person asked the run to stop. NULL = not asked.",
            "error_code": "Why the run failed or left pages, as a short code. NULL when none.",
            "retry_of": "→ ensemble_runs.id (same customer) of the run whose failed pages this one reads"
            " again. NULL for a first run.",
            "attempts": "How many times a worker started the run.",
            "created_at": "When the run was asked for.",
            "started_at": "When a worker first started it. NULL until then.",
            "finished_at": "When it ended (done, stopped or failed). NULL while queued or running.",
        },
    ),
    "ensemble_readings": (
        "What one Ensemble run found for one word, and a person's Keep or Revert of a change."
        " Deleted with its run.",
        {
            "id": "Ensemble reading id.",
            "customer_id": CUSTOMER,
            "document_id": DOCUMENT,
            "run_id": "→ ensemble_runs.id (same customer) that made the reading.",
            "word_key": WORD_KEY,
            "page_no": "The word's page, counted from 1.",
            "region": REGION,
            "before_text": "The word's text when the run read it.",
            "outcome": "agreed (the readings match the text), changed (a majority read another text,"
            " in proposal), unresolved (no majority; every reading in reads) or skipped_user_edit"
            " (a person had edited the word, which always wins).",
            "proposal": "The text the majority read, for a change. NULL otherwise.",
            "reads": "Each model's reading as a JSON array of objects with model and text, the main"
            " reader first.",
            "decision": "kept or reverted: a person's choice on a change. NULL = not decided.",
            "decided_by": "→ users.id (same customer) who kept or reverted it. NULL together with decided_at.",
            "decided_at": "When it was kept or reverted. NULL = not decided.",
            "created_at": "When the reading was saved.",
        },
    ),
    "word_changes": (
        "Every flagged word a person confirmed or edited, or Ensemble resolved and a person kept,"
        " oldest first. Append-only: an undo marks the row. Deleted with its document.",
        {
            "id": "Word change id.",
            "customer_id": CUSTOMER,
            "document_id": DOCUMENT,
            "word_key": WORD_KEY,
            "kind": "What was flagged: mark (a word the reader was unsure of or could not read), line"
            " (a line whose spot or reader needs a check) or value (a value the readers read differently"
            " or with low confidence).",
            "page_no": "The word's page, counted from 1.",
            "region": REGION,
            "as_read": "The word's text as the reader read it, part of its key.",
            "action": "confirm (a person said the text is right), edit (a person typed the right"
            " text), resolve (Ensemble's readings agreed) or keep (a person kept Ensemble's change).",
            "before_text": "The word's text before the change.",
            "after_text": "The word's text after the change; the same as before_text for confirm and resolve.",
            "reading_id": "→ ensemble_readings.id (same customer) behind a resolve or a keep. NULL for"
            " confirm and edit.",
            "user_id": "→ users.id (same customer) who made the change. NULL only for a resolve.",
            "applied": "What the change wrote, as a JSON object: the values and search pieces it changed"
            " with their earlier text and labels, so an undo can put them back. Written again when the"
            " document is saved again.",
            "created_at": "When the change was made.",
            "undone_at": "When the change was undone. NULL = in force. NULL together with undone_by.",
            "undone_by": "→ users.id (same customer) who undid it. NULL = in force.",
        },
    ),
}


def _comments() -> list[str]:
    statements = []
    for table, (table_comment, columns) in COMMENTS.items():
        statements.append(f"COMMENT ON TABLE {table} IS {_literal(table_comment)}")
        statements.extend(
            f"COMMENT ON COLUMN {table}.{column} IS {_literal(comment)}"
            for column, comment in columns.items()
        )
    return statements


UPGRADE = [
    # --- ensemble_runs: one Ensemble run after the scan ---------------------------------
    """
    CREATE TABLE ensemble_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        scope text NOT NULL CHECK (scope IN ('word', 'words', 'flagged', 'document')),
        pages integer[] NOT NULL CHECK (cardinality(pages) > 0 AND 1 <= ALL (pages)),
        pages_done integer[] NOT NULL DEFAULT '{}',
        pages_failed integer[] NOT NULL DEFAULT '{}',
        models text[] NOT NULL CHECK (cardinality(models) BETWEEN 1 AND 3),
        status text NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued', 'running', 'done', 'stopped', 'failed')),
        requested_by uuid NOT NULL,
        stop_requested_at timestamptz NULL,
        error_code text NULL CHECK (error_code ~ '^[a-z0-9_]{1,64}$'),
        retry_of uuid NULL,
        attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        created_at timestamptz NOT NULL DEFAULT now(),
        started_at timestamptz NULL,
        finished_at timestamptz NULL,
        UNIQUE (customer_id, id),
        CONSTRAINT ensemble_runs_pages_done CHECK (pages_done <@ pages),
        CONSTRAINT ensemble_runs_pages_failed CHECK (pages_failed <@ pages),
        CONSTRAINT ensemble_runs_finished CHECK
            ((finished_at IS NULL) = (status IN ('queued', 'running'))),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, requested_by) REFERENCES users (customer_id, id),
        FOREIGN KEY (customer_id, retry_of) REFERENCES ensemble_runs (customer_id, id)
    )
    """,
    # One queued or running run per document: a second one waits for it, or stops it first.
    """
    CREATE UNIQUE INDEX ensemble_runs_one_active ON ensemble_runs (document_id)
        WHERE status IN ('queued', 'running')
    """,
    "CREATE INDEX ensemble_runs_document ON ensemble_runs (customer_id, document_id, created_at)",
    # --- ensemble_readings: what a run found for each word --------------------------------
    f"""
    CREATE TABLE ensemble_readings (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        run_id uuid NOT NULL,
        word_key text NOT NULL CHECK (word_key {HEX_KEY}),
        page_no integer NOT NULL CHECK (page_no >= 1),
        region jsonb NOT NULL CHECK (jsonb_typeof(region) = 'object'),
        before_text text NOT NULL CHECK (length(before_text) <= {MAX_TEXT}),
        outcome text NOT NULL CHECK (outcome IN ('agreed', 'changed', 'unresolved', 'skipped_user_edit')),
        proposal text NULL CHECK (length(proposal) BETWEEN 1 AND {MAX_TEXT}),
        reads jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(reads) = 'array'),
        decision text NULL CHECK (decision IN ('kept', 'reverted')),
        decided_by uuid NULL,
        decided_at timestamptz NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, id),
        UNIQUE (run_id, word_key),
        CONSTRAINT ensemble_readings_proposal CHECK ((proposal IS NOT NULL) = (outcome = 'changed')),
        CONSTRAINT ensemble_readings_decided_both CHECK ((decided_by IS NULL) = (decided_at IS NULL)),
        CONSTRAINT ensemble_readings_decided_pair CHECK ((decision IS NULL) = (decided_at IS NULL)),
        CONSTRAINT ensemble_readings_decision_on_change CHECK (decision IS NULL OR outcome = 'changed'),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, run_id) REFERENCES ensemble_runs (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, decided_by) REFERENCES users (customer_id, id)
    )
    """,
    "CREATE INDEX ensemble_readings_document ON ensemble_readings (customer_id, document_id, word_key)",
    # --- word_changes: the history of every settled word ----------------------------------
    f"""
    CREATE TABLE word_changes (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        word_key text NOT NULL CHECK (word_key {HEX_KEY}),
        kind text NOT NULL CHECK (kind IN ('mark', 'line', 'value')),
        page_no integer NOT NULL CHECK (page_no >= 1),
        region jsonb NOT NULL CHECK (jsonb_typeof(region) = 'object'),
        as_read text NOT NULL CHECK (length(as_read) <= {MAX_TEXT}),
        action text NOT NULL CHECK (action IN ('confirm', 'edit', 'resolve', 'keep')),
        before_text text NOT NULL CHECK (length(before_text) <= {MAX_TEXT}),
        after_text text NOT NULL CHECK (length(after_text) BETWEEN 1 AND {MAX_TEXT}),
        reading_id uuid NULL,
        user_id uuid NULL,
        applied jsonb NOT NULL DEFAULT '{{}}' CHECK (jsonb_typeof(applied) = 'object'),
        created_at timestamptz NOT NULL DEFAULT now(),
        undone_at timestamptz NULL,
        undone_by uuid NULL,
        CONSTRAINT word_changes_undone_pair CHECK ((undone_at IS NULL) = (undone_by IS NULL)),
        CONSTRAINT word_changes_same_text CHECK (action NOT IN ('confirm', 'resolve') OR before_text = after_text),
        CONSTRAINT word_changes_reading CHECK ((reading_id IS NOT NULL) = (action IN ('resolve', 'keep'))),
        CONSTRAINT word_changes_person CHECK (action = 'resolve' OR user_id IS NOT NULL),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, reading_id) REFERENCES ensemble_readings (customer_id, id),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id),
        FOREIGN KEY (customer_id, undone_by) REFERENCES users (customer_id, id)
    )
    """,
    "CREATE INDEX word_changes_document ON word_changes (customer_id, document_id, created_at, id)",
    # --- what the app role may do, and row level security -------------------------------
    # The least each needs: a change is added, its applied written again by a save and its
    # undo pair set, never deleted; a run's progress and outcome columns change as it goes;
    # a reading is added and only its decision set.
    f"GRANT SELECT, INSERT, UPDATE (applied, undone_at, undone_by) ON word_changes TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (pages_done, pages_failed, status, stop_requested_at, error_code,"
    f" attempts, started_at, finished_at) ON ensemble_runs TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (decision, decided_by, decided_at) ON ensemble_readings TO {APP_ROLE}",
    *[f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" for table in NEW_TABLES],
    *[
        f"""
        CREATE POLICY customer_only ON {table}
        USING (customer_id = kaagaz_current_customer())
        WITH CHECK (customer_id = kaagaz_current_customer())
        """
        for table in NEW_TABLES
    ],
    *_comments(),
]

DOWNGRADE = [
    "DROP TABLE IF EXISTS word_changes",
    "DROP TABLE IF EXISTS ensemble_readings",
    "DROP TABLE IF EXISTS ensemble_runs",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
