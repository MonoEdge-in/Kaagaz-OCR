"""Gemini Reader: page reading results, the page cache, sections, topics, chat memory and
run counters (PRD 5 section 8).

Everything follows 0001's and 0004's rules: every new customer table carries
``customer_id``, has the ``customer_only`` policy (USING and WITH CHECK), the least
grants the app needs, and points at its document with a (customer_id, document_id)
foreign key, so a row can never hang under another customer's document; it goes
with its document (ON DELETE CASCADE: a deleted document is forgotten at once).
Every new table and column is commented (0007's rule).

* ``pages``: who read the page (RT2), its reading result (F1: ``read_quality``,
  ``read_issues`` from the F1 table, the three counts), "Keep as is" (F5: who and
  when, both or neither; ``accepted_by`` is a user of the same customer) and
  ``read_again_requested_at``, the page a "Read this page again" asked for (F5; a
  queue job carries only a document id, so the worker finds the page here).
* ``documents``: ``batch_id`` (Q1) with its index, and every stored ``ocr_choice``
  set to NULL (RT3: the form field is still accepted and ignored). The column
  comment says so. Each changed row's ``updated_at`` moves (the touch trigger).
* ``page_reads`` (G7): the page cache, one row per (document, page, image sha256,
  model, prompt version), written page by page outside the replacing save, so a
  crash keeps them. A row is inserted, or its result and token counts replaced (an
  upsert on the key): only those columns may be updated, never its key.
* ``document_sections`` (CX2) and ``document_topics`` (CX3): written by the one
  replacing save, so they are inserted and deleted, never updated, except a
  section's vector and its recipe, written after the save like a piece's. The
  section's ``tsv`` is a plain column the writer fills, as written in section 8.
* ``extracted_values.label_reason``: adds ``unsure_text`` (V4), ``box_unanchored``
  (G5) and ``local_reader`` (Q7).
* ``conversations``: the chat summary (at most 1200 characters), its memory (a JSON
  object of at most 16 KiB) and the turn it was written at (M3).
* ``processing_runs``: pages read by Gemini, locally, from the cache, read again and
  unreadable, and the input and output tokens billed (G9, U9). The app may update
  them, like the other outcome columns (0004's column grant).

The failure code ``unreadable`` (F4): ``documents.failure_code`` (0004) and
``processing_runs.error_code`` (0004) are checked by their shape, lower-case letters,
digits and underscores, never a fixed list, so they already allow it; nothing changes.

The upgrade and the downgrade lock documents, extracted_values, pages,
processing_runs and conversations first, in that order (documents first, as 0004 and
0005), so they queue behind the app instead of deadlocking with it
(migrations/env.py sets lock_timeout, so a busy table makes them fail fast). Adding
columns with constant defaults does not rewrite a table; the new CHECKs and the
foreign key read each table once.

The downgrade refuses while values carry a label reason 0020 does not have (0004's
pattern), then drops everything above and puts back 0013's comment on
``ocr_choice``. The ``ocr_choice`` values cleared by the upgrade do not come back
(those documents read with Auto, which is what 0013 does with a reader it no longer
has). Page reads, sections, topics, chat summaries and the new counts are lost.

Revision ID: 0021
Revises: 0020 (operator activity)
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
NEW_TABLES = ("page_reads", "document_sections", "document_topics")
LOCK = (
    "LOCK TABLE documents, extracted_values, pages, processing_runs, conversations IN ACCESS EXCLUSIVE MODE"
)

# F1: the issue codes a page may carry, in the PRD's table order (kaagaz.reading.page_quality.ISSUES).
ISSUE_CODES = (
    "blurry",
    "low_resolution",
    "faint",
    "glare",
    "shadow",
    "cut_off",
    "stamp_over_text",
    "overwritten",
    "handwriting_hard",
    "illegible_words",
    "readers_disagree",
    "unpinned",
    "reader_refused",
    "read_locally",
    "no_text",
)
# 0004's label reasons, in its order, then PRD 5's (kaagaz.db.review.EVERY_REASON).
OLD_REASONS = (
    "direct_cell",
    "readers_agree",
    "readers_disagree",
    "single_reader",
    "low_confidence",
    "corrected",
    "learned_correction",
    "check_failed",
    "model_chosen",
    "ocr_script",
)
NEW_REASONS = ("unsure_text", "box_unanchored", "local_reader")
# The run counters the app writes when a run finishes, after 0004's outcome columns.
RUN_COUNTERS = (
    "pages_gemini",
    "pages_local",
    "pages_cached",
    "pages_read_again",
    "pages_unreadable",
    "input_tokens",
    "output_tokens",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _literal(comment: str) -> str:
    return "'" + comment.replace("'", "''") + "'"


CUSTOMER = "→ customers.id, the customer the row belongs to (row level security filters on it)."
DOCUMENT = "→ documents.id (same customer). The row is deleted with its document."
CREATED = "When the row was written."

# {table: (table comment or None for a table that has one, {column: comment})}, as in 0007.
# Plain constants with no colon before a word, which SQLAlchemy would read as a bind parameter.
COMMENTS: dict[str, tuple[str | None, dict[str, str]]] = {
    "pages": (
        None,
        {
            "read_by": "Who read the page (RT2) as the model ID in OCR_MODEL, rapidocr (the backup reader),"
            " text_layer or cells. NULL for a blank page or one read before PRD 5.",
            "read_quality": "How well the page was read (F1, F2) as good, uncertain (some words unsure or"
            " illegible, a line unpinned, a value disputed, or low resolution) or unreadable."
            " Text-layer and cell pages are good.",
            "read_issues": "Why the page is not good, as codes from the F1 table such as blurry, glare,"
            " illegible_words, unpinned, reader_refused, read_locally or no_text. Empty when good.",
            "unsure_words": "Words the reader listed as unsure on this page (F1).",
            "illegible_words": "Parts of lines the reader marked [?] because it could not read them (F1).",
            "unpinned_lines": "Lines whose spot no box found on the page confirmed (G5), so their values go to review.",
            "accepted_by": "→ users.id (same customer) who chose Keep as is for this page (F5): it leaves"
            " the attention list but stays flagged. NULL together with accepted_at.",
            "accepted_at": "When Keep as is was chosen for this page (F5). Kept when the document is read"
            " again, unless this page itself was read again. NULL = not accepted.",
            "read_again_requested_at": "When a user asked to read this page again, harder (F5). The next"
            " run of the document reads it so, and its save clears this. NULL = not asked.",
        },
    ),
    "documents": (
        None,
        {
            "batch_id": "The upload batch (one drop of files in the browser) this file came in (Q1). The"
            " browser makes the id. NULL for a file uploaded on its own or before batches.",
            "ocr_choice": "No longer used (RT3) and always NULL since migration 0021 cleared it. Uploads"
            " still send the field, and it is ignored. It held the scan reader the uploader chose.",
        },
    ),
    "page_reads": (
        "The page cache (G7): each page as the reading model returned it, so a retry after a crash,"
        " an outage or a lost lease reads and bills no page twice. Deleted with its document.",
        {
            "id": "Page read id.",
            "customer_id": CUSTOMER,
            "document_id": DOCUMENT,
            "page_no": "The page, counted from 1.",
            "image_sha256": "SHA-256 of the page image's pixels as sent to the reader, in lower-case hex.",
            "model": "The reading model that read it, the OCR_MODEL value then.",
            "prompt_version": "The version of the reading instructions it was read with (GEMINI_OCR_PROMPT_VERSION).",
            "result": "The reader's reply for the page as a JSON object (the G3 schema), before any box was pinned.",
            "input_tokens": "Input tokens the read was billed, from the reply's usage numbers.",
            "output_tokens": "Output tokens the read was billed, thinking included, from the reply's usage numbers.",
            "created_at": "When the page was read and saved here (again, when it was read again).",
        },
    ),
    "document_sections": (
        "A map of each document (CX2): its sections with their heading path, pages, pieces and a"
        " short summary, so search can find the right section. Replaced with the document's pieces.",
        {
            "id": "Section id.",
            "customer_id": CUSTOMER,
            "document_id": DOCUMENT,
            "seq": "The section's place in the document, in reading order.",
            "path": "The headings above and of this section, outermost first. Empty for a section of"
            " pages in a document with no headings.",
            "page_from": "First page of the section, counted from 1.",
            "page_to": "Last page of the section, at least page_from.",
            "piece_seq_from": "→ pieces.seq of the section's first piece in the same document.",
            "piece_seq_to": "→ pieces.seq of the section's last piece in the same document, at least piece_seq_from.",
            "summary": "A short summary of the section written by the chat model, at most 400 characters."
            " An AI summary, never evidence. NULL when none was written.",
            "embedding": "Meaning vector (halfvec, 1024 numbers) of the summary, with its recipe in"
            " embed_recipe. NULL until embedded.",
            "embed_recipe": "What made the vector, the MODEL_EMBED model name and the recipe. NULL together with embedding.",
            "tsv": "Word index of the heading path and the summary, written with the row, for full-text search.",
            "created_at": CREATED,
        },
    ),
    "document_topics": (
        "Topics that join distant pages of one document (CX3), each with its other names and the"
        " pieces that discuss it. Labels for finding pieces, never shown as facts.",
        {
            "id": "Topic id.",
            "customer_id": CUSTOMER,
            "document_id": DOCUMENT,
            "topic": "The topic as a short lower-case name, such as leave. Unique within the document.",
            "aliases": "Other names for the topic, in any language, such as holidays or maternity leave.",
            "piece_seqs": "→ pieces.seq of the pieces in the same document that discuss the topic. Never empty.",
            "created_at": CREATED,
        },
    ),
    "conversations": (
        None,
        {
            "summary": "What the chat has been about so far, rewritten by the chat model every"
            " CHAT_SUMMARY_EVERY turns (M3), at most 1200 characters. Fenced as data. NULL until then.",
            "memory": "What the chat remembers (M3) as a JSON object of documents cited, names, numbers,"
            " dates and topics, each found in an earlier question or answer. At most 16 KiB. NULL until written.",
            "summary_turn": "The turn the summary and memory were last written at. 0 = never.",
        },
    ),
    "processing_runs": (
        None,
        {
            "pages_gemini": "Pages the reading model read in this run, billed.",
            "pages_local": "Pages the backup reader (rapidocr) read in this run, while the reading model was down or off.",
            "pages_cached": "Pages taken from the page cache (page_reads) in this run, neither read nor billed again.",
            "pages_read_again": "Pages read again, harder, because a user asked (F5).",
            "pages_unreadable": "Pages of this run whose reading result is unreadable (F2).",
            "input_tokens": "Input tokens this run's reading-model calls were billed.",
            "output_tokens": "Output tokens this run's reading-model calls were billed, thinking included.",
        },
    ),
}

OCR_CHOICE_0013 = (
    "The scan reader the uploader chose for this file, by its id (lower-case letters, digits"
    " and underscores). NULL = Auto, the usual reader. A reader the server no longer has is read with Auto."
)


def _comments() -> list[str]:
    statements = []
    for table, (table_comment, columns) in COMMENTS.items():
        if table_comment is not None:
            statements.append(f"COMMENT ON TABLE {table} IS {_literal(table_comment)}")
        statements.extend(
            f"COMMENT ON COLUMN {table}.{column} IS {_literal(comment)}"
            for column, comment in columns.items()
        )
    return statements


UPGRADE = [
    LOCK,
    # --- pages: who read it (RT2), its reading result (F1), Keep as is and read again (F5) ---
    f"""
    ALTER TABLE pages
        ADD COLUMN read_by text NULL CHECK (length(read_by) BETWEEN 1 AND 300),
        ADD COLUMN read_quality text NOT NULL DEFAULT 'good'
            CHECK (read_quality IN ('good', 'uncertain', 'unreadable')),
        ADD COLUMN read_issues text[] NOT NULL DEFAULT '{{}}'
            CHECK (read_issues <@ ARRAY[{_quoted(ISSUE_CODES)}]::text[]),
        ADD COLUMN unsure_words integer NOT NULL DEFAULT 0 CHECK (unsure_words >= 0),
        ADD COLUMN illegible_words integer NOT NULL DEFAULT 0 CHECK (illegible_words >= 0),
        ADD COLUMN unpinned_lines integer NOT NULL DEFAULT 0 CHECK (unpinned_lines >= 0),
        ADD COLUMN accepted_by uuid NULL,
        ADD COLUMN accepted_at timestamptz NULL,
        ADD COLUMN read_again_requested_at timestamptz NULL,
        ADD CONSTRAINT pages_accepted_both CHECK ((accepted_by IS NULL) = (accepted_at IS NULL)),
        ADD CONSTRAINT pages_accepted_by_fk
            FOREIGN KEY (customer_id, accepted_by) REFERENCES users (customer_id, id)
    """,
    # --- documents: the upload batch (Q1), and no reader choice (RT3) --------------------
    "ALTER TABLE documents ADD COLUMN batch_id uuid NULL",
    "CREATE INDEX documents_batch ON documents (customer_id, batch_id)",
    # Runs as the table owner, and 0001 has no FORCE ROW LEVEL SECURITY, so every customer's rows.
    "UPDATE documents SET ocr_choice = NULL WHERE ocr_choice IS NOT NULL",
    # --- page_reads: the page cache (G7) ---------------------------------------------------
    """
    CREATE TABLE page_reads (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        page_no integer NOT NULL CHECK (page_no >= 1),
        image_sha256 text NOT NULL CHECK (image_sha256 ~ '^[0-9a-f]{64}$'),
        model text NOT NULL CHECK (length(model) BETWEEN 1 AND 300),
        prompt_version text NOT NULL CHECK (length(prompt_version) BETWEEN 1 AND 64),
        result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
        input_tokens integer NULL CHECK (input_tokens >= 0),
        output_tokens integer NULL CHECK (output_tokens >= 0),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (document_id, page_no, image_sha256, model, prompt_version),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    # --- document_sections: a map of each document (CX2) -----------------------------------
    """
    CREATE TABLE document_sections (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        seq integer NOT NULL CHECK (seq >= 0),
        path text[] NOT NULL,
        page_from integer NOT NULL CHECK (page_from >= 1),
        page_to integer NOT NULL,
        piece_seq_from integer NOT NULL CHECK (piece_seq_from >= 0),
        piece_seq_to integer NOT NULL,
        summary text NULL CHECK (length(summary) <= 400),
        embedding halfvec(1024) NULL,
        embed_recipe text NULL CHECK (length(embed_recipe) <= 300),
        tsv tsvector NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (document_id, seq),
        CONSTRAINT document_sections_pages CHECK (page_to >= page_from),
        CONSTRAINT document_sections_pieces CHECK (piece_seq_to >= piece_seq_from),
        CONSTRAINT document_sections_vector_with_recipe CHECK ((embedding IS NULL) = (embed_recipe IS NULL)),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    # The same index settings as pieces_embedding (0006).
    """
    CREATE INDEX document_sections_embedding ON document_sections
        USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)
    """,
    "CREATE INDEX document_sections_tsv ON document_sections USING gin (tsv)",
    # --- document_topics: topics that join distant pages (CX3) ---------------------------
    """
    CREATE TABLE document_topics (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        topic text NOT NULL CHECK (length(topic) BETWEEN 1 AND 200 AND topic = lower(topic)),
        aliases text[] NOT NULL DEFAULT '{}',
        piece_seqs integer[] NOT NULL CHECK (cardinality(piece_seqs) > 0),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (document_id, topic),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX document_topics_aliases ON document_topics USING gin (aliases)",
    # --- extracted_values: PRD 5's label reasons (V4, G5, Q7) ----------------------------
    "ALTER TABLE extracted_values DROP CONSTRAINT extracted_values_label_reason_check",
    f"""
    ALTER TABLE extracted_values ADD CONSTRAINT extracted_values_label_reason_check
        CHECK (label_reason IN ({_quoted(OLD_REASONS + NEW_REASONS)}))
    """,
    # --- conversations: a chat that remembers (M3) ---------------------------------------
    """
    ALTER TABLE conversations
        ADD COLUMN summary text NULL CHECK (length(summary) <= 1200),
        ADD COLUMN memory jsonb NULL
            CHECK (jsonb_typeof(memory) = 'object' AND octet_length(memory::text) <= 16384),
        ADD COLUMN summary_turn integer NOT NULL DEFAULT 0 CHECK (summary_turn >= 0)
    """,
    # --- processing_runs: who read the pages, and the tokens billed (G9, U9) -------------
    """
    ALTER TABLE processing_runs
        ADD COLUMN pages_gemini integer NOT NULL DEFAULT 0 CHECK (pages_gemini >= 0),
        ADD COLUMN pages_local integer NOT NULL DEFAULT 0 CHECK (pages_local >= 0),
        ADD COLUMN pages_cached integer NOT NULL DEFAULT 0 CHECK (pages_cached >= 0),
        ADD COLUMN pages_read_again integer NOT NULL DEFAULT 0 CHECK (pages_read_again >= 0),
        ADD COLUMN pages_unreadable integer NOT NULL DEFAULT 0 CHECK (pages_unreadable >= 0),
        ADD COLUMN input_tokens bigint NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
        ADD COLUMN output_tokens bigint NOT NULL DEFAULT 0 CHECK (output_tokens >= 0)
    """,
    # --- what the app role may do, and row level security -------------------------------
    # The least each needs: a cached page is inserted, or its result and counts replaced;
    # sections and topics are deleted and inserted by the save, and only a section's vector
    # is written after it; the new run counters change with the other outcome columns.
    f"GRANT SELECT, INSERT, UPDATE (result, input_tokens, output_tokens, created_at), DELETE"
    f" ON page_reads TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (embedding, embed_recipe), DELETE ON document_sections TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, DELETE ON document_topics TO {APP_ROLE}",
    f"GRANT UPDATE ({', '.join(RUN_COUNTERS)}) ON processing_runs TO {APP_ROLE}",
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
    LOCK,
    # These labels have no older reason that tells the truth, so the downgrade refuses
    # instead of relabelling anything (0004's pattern).
    f"""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM extracted_values WHERE label_reason IN ({_quoted(NEW_REASONS)})) THEN
            RAISE EXCEPTION 'values labelled unsure_text, box_unanchored or local_reader exist; relabel them before downgrading'
                USING ERRCODE = 'check_violation';
        END IF;
    END
    $$
    """,
    "ALTER TABLE extracted_values DROP CONSTRAINT extracted_values_label_reason_check",
    f"""
    ALTER TABLE extracted_values ADD CONSTRAINT extracted_values_label_reason_check
        CHECK (label_reason IN ({_quoted(OLD_REASONS)}))
    """,
    # Dropping a column drops its column grant too.
    "ALTER TABLE processing_runs " + ", ".join(f"DROP COLUMN IF EXISTS {column}" for column in RUN_COUNTERS),
    """
    ALTER TABLE conversations
        DROP COLUMN IF EXISTS summary_turn,
        DROP COLUMN IF EXISTS memory,
        DROP COLUMN IF EXISTS summary
    """,
    "DROP TABLE IF EXISTS document_topics",
    "DROP TABLE IF EXISTS document_sections",
    "DROP TABLE IF EXISTS page_reads",
    f"COMMENT ON COLUMN documents.ocr_choice IS {_literal(OCR_CHOICE_0013)}",
    "DROP INDEX IF EXISTS documents_batch",
    "ALTER TABLE documents DROP COLUMN IF EXISTS batch_id",
    """
    ALTER TABLE pages
        DROP CONSTRAINT IF EXISTS pages_accepted_by_fk,
        DROP CONSTRAINT IF EXISTS pages_accepted_both,
        DROP COLUMN IF EXISTS read_again_requested_at,
        DROP COLUMN IF EXISTS accepted_at,
        DROP COLUMN IF EXISTS accepted_by,
        DROP COLUMN IF EXISTS unpinned_lines,
        DROP COLUMN IF EXISTS illegible_words,
        DROP COLUMN IF EXISTS unsure_words,
        DROP COLUMN IF EXISTS read_issues,
        DROP COLUMN IF EXISTS read_quality,
        DROP COLUMN IF EXISTS read_by
    """,
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
