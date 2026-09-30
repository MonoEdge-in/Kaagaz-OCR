"""Document understanding: page routes, the card, invoices, links and runs (PRD 2, WP2.1).

Everything follows 0001's rules: every customer table carries ``customer_id``,
has the ``customer_only`` policy, and points at its parents with
(customer_id, id) foreign keys, so a row can never hang under another
customer's parent.

The upgrade and the downgrade both lock documents, extracted_values, pieces and
pages first, in that order, so they queue behind the app instead of
deadlocking with it (migrations/env.py sets lock_timeout, so a busy table makes
them fail fast). Adding columns with constant defaults does not rewrite a table.

PROVISIONAL additions beyond PRD 2 section 7 (for Vrushit to confirm):
* ``processing_runs.error_code`` (the edge case "the run records the error
  code"; digits allowed, as the CSV reader's ``not_utf8`` needs), ``config``,
  ``quotes_returned``, ``values_verified``, ``values_needs_review`` (ML10) and the
  status ``superseded`` (a run that a newer run of the document replaced).
* ``processing_runs.document_id`` may be NULL and is set NULL when its document
  is deleted, so the pages-per-month numbers outlive a purge (AR12).
* ``created_at`` on runs and lines, ``UNIQUE (customer_id, id)`` on all four new
  tables, and ``NOT NULL`` on the run status and counters.
* The composite foreign keys; lines point at their invoice with
  (customer_id, document_id, invoice_id), so a line's document is its invoice's (AR13).
* The CHECKs on languages, card (at most 64 KiB), progress, check_reasons, the
  label pair, the run times, the text lengths (A7), and the number ranges: every
  amount and quantity within plus or minus 1e12, which also refuses NaN and
  Infinity (numeric orders NaN above every number).
* The label reasons ``check_failed`` (K3), ``model_chosen`` (A1) and ``ocr_script`` (ML1).
* The grants, the least each table needs (A9 and the council's review): invoices
  and links are deleted and inserted, never updated (so ``invoices`` has no touch
  trigger); lines are only inserted; runs are never deleted, and only their
  outcome columns may be updated.
* The indexes ``document_links_linked`` and ``processing_runs_document``, and the
  partial unique index ``processing_runs_one_running``: at most one running run
  per document, the backstop behind start_run's lock.
* The downgrade refuses while values carry a label reason 0003 does not have,
  or documents a failure code 0003 does not allow.

Fixes a main bug: a non-UTF-8 CSV could not be marked failed. 0001's
``documents_failure_code_check`` allows no digits, and the CSV reader's code is
``not_utf8``; it now allows digits, like ``processing_runs.error_code``.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
NEW_TABLES = ("invoices", "invoice_lines", "document_links", "processing_runs")
LOCK = "LOCK TABLE documents, extracted_values, pieces, pages IN ACCESS EXCLUSIVE MODE"

UPGRADE = [
    LOCK,
    # --- pages: the route each page was read by (R1), and its resolution (R4) --
    """
    ALTER TABLE pages
        ADD COLUMN route text NULL CHECK (route IN ('cells', 'text_layer', 'ocr', 'blank')),
        ADD COLUMN effective_dpi integer NULL CHECK (effective_dpi > 0),
        ADD COLUMN low_resolution boolean NOT NULL DEFAULT false
    """,
    # --- documents: the card (D3) and progress (X3) --------------------------------
    # A NULL CHECK passes, so progress names each key it needs with the ? operator,
    # and checks each value's JSON type (->> turns a JSON null into NULL).
    """
    ALTER TABLE documents
        ADD COLUMN doc_type text NULL CHECK (doc_type IN (
            'invoice', 'receipt', 'bank_statement', 'land_record', 'contract',
            'register', 'form', 'letter', 'other'
        )),
        ADD COLUMN languages text[] NOT NULL DEFAULT '{}'
            CHECK (languages <@ ARRAY['en', 'hi', 'mr', 'other']::text[]),
        ADD COLUMN summary text NULL CHECK (length(summary) <= 600),
        ADD COLUMN document_date date NULL,
        ADD COLUMN card jsonb NULL
            CHECK (jsonb_typeof(card) = 'object' AND octet_length(card::text) <= 65536),
        ADD COLUMN progress jsonb NULL CHECK (
            jsonb_typeof(progress) = 'object'
            AND progress ? 'step' AND progress ? 'done' AND progress ? 'total'
            AND jsonb_typeof(progress -> 'step') = 'string'
            AND progress ->> 'step' IN ('scanning', 'preparing', 'reading', 'understanding', 'indexing')
            AND jsonb_typeof(progress -> 'done') = 'number' AND progress ->> 'done' ~ '^[0-9]{1,9}$'
            AND jsonb_typeof(progress -> 'total') = 'number' AND progress ->> 'total' ~ '^[0-9]{1,9}$'
        )
    """,
    # --- documents: failure codes may hold digits (the main bug above) ---------------
    "ALTER TABLE documents DROP CONSTRAINT documents_failure_code_check",
    """
    ALTER TABLE documents ADD CONSTRAINT documents_failure_code_check
        CHECK (failure_code ~ '^[a-z0-9_]{1,64}$')
    """,
    # --- pieces: context header (C4) and Latin spelling (L3) -----------------------
    """
    ALTER TABLE pieces
        ADD COLUMN context_header text NOT NULL DEFAULT '',
        ADD COLUMN text_latin text NULL
    """,
    # --- extracted_values: the label's own spot (D3), and the new reasons ----------
    """
    ALTER TABLE extracted_values
        ADD COLUMN label_page_no integer NULL CHECK (label_page_no >= 1),
        ADD COLUMN label_region jsonb NULL CHECK (jsonb_typeof(label_region) = 'object'),
        ADD CONSTRAINT extracted_values_label_both
            CHECK ((label_page_no IS NULL) = (label_region IS NULL))
    """,
    # 0001's inline CHECK is named extracted_values_label_reason_check. Every
    # existing reason is kept, in 0001's order.
    "ALTER TABLE extracted_values DROP CONSTRAINT extracted_values_label_reason_check",
    """
    ALTER TABLE extracted_values ADD CONSTRAINT extracted_values_label_reason_check
        CHECK (label_reason IN (
            'direct_cell', 'readers_agree', 'readers_disagree', 'single_reader',
            'low_confidence', 'corrected', 'learned_correction',
            'check_failed', 'model_chosen', 'ocr_script'
        ))
    """,
    # --- invoices (E5) ---------------------------------------------------------------
    """
    CREATE TABLE invoices (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        invoice_number text NULL CHECK (length(invoice_number) <= 200),
        invoice_date date NULL,
        seller_name text NULL CHECK (length(seller_name) <= 200),
        seller_gstin text NULL CHECK (length(seller_gstin) <= 200),
        buyer_name text NULL CHECK (length(buyer_name) <= 200),
        buyer_gstin text NULL CHECK (length(buyer_gstin) <= 200),
        subtotal numeric(14,2) NULL CHECK (subtotal > -1e12 AND subtotal < 1e12),
        tax_total numeric(14,2) NULL CHECK (tax_total > -1e12 AND tax_total < 1e12),
        total numeric(14,2) NULL CHECK (total > -1e12 AND total < 1e12),
        currency text NOT NULL DEFAULT 'INR' CHECK (currency ~ '^[A-Z]{3}$'),
        label text NOT NULL CHECK (label IN ('verified', 'needs_review')),
        check_status text NOT NULL CHECK (check_status IN ('passed', 'failed', 'not_checked')),
        check_reasons text[] NOT NULL DEFAULT '{}' CHECK (check_reasons <@ ARRAY[
            'lines_sum_mismatch', 'total_mismatch', 'missing_invoice_number', 'missing_total',
            'bad_date', 'bad_gstin', 'conflicting_values', 'truncated'
        ]::text[]),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, id),
        UNIQUE (customer_id, document_id, id),
        UNIQUE (document_id),
        CHECK ((check_status = 'failed') = (cardinality(check_reasons) > 0)),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    # --- invoice_lines (E5) --------------------------------------------------------------
    # One foreign key, through the invoice: deleting the document deletes the
    # invoice, and that deletes its lines (AR13).
    """
    CREATE TABLE invoice_lines (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        invoice_id uuid NOT NULL,
        document_id uuid NOT NULL,
        line_no integer NOT NULL CHECK (line_no >= 1),
        description text NULL CHECK (length(description) <= 1000),
        hsn text NULL CHECK (length(hsn) <= 200),
        quantity numeric NULL CHECK (quantity BETWEEN -1e12 AND 1e12),
        unit text NULL CHECK (length(unit) <= 200),
        rate numeric(14,2) NULL CHECK (rate > -1e12 AND rate < 1e12),
        amount numeric(14,2) NULL CHECK (amount > -1e12 AND amount < 1e12),
        label text NOT NULL CHECK (label IN ('verified', 'needs_review')),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, id),
        UNIQUE (invoice_id, line_no),
        FOREIGN KEY (customer_id, document_id, invoice_id)
            REFERENCES invoices (customer_id, document_id, id) ON DELETE CASCADE
    )
    """,
    # --- document_links (D5) --------------------------------------------------------------
    """
    CREATE TABLE document_links (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        linked_document_id uuid NOT NULL,
        identifier_kind text NOT NULL CHECK (identifier_kind IN (
            'invoice_number', 'po_number', 'challan_number', 'survey_number',
            'khata_number', 'account_number', 'reference_number'
        )),
        identifier_value text NOT NULL CHECK (length(identifier_value) BETWEEN 1 AND 200),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, id),
        UNIQUE (document_id, linked_document_id, identifier_kind, identifier_value),
        CHECK (document_id <> linked_document_id),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, linked_document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX document_links_linked ON document_links (customer_id, linked_document_id)",
    # --- processing_runs (R6, X2) ----------------------------------------------------------
    """
    CREATE TABLE processing_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        document_id uuid NULL,
        started_at timestamptz NOT NULL,
        finished_at timestamptz NULL,
        status text NOT NULL CHECK (status IN (
            'running', 'ready', 'failed', 'needs_reader', 'rejected', 'superseded'
        )),
        error_code text NULL CHECK (error_code ~ '^[a-z0-9_]{1,64}$'),
        ocr_engine text NULL CHECK (length(ocr_engine) <= 100),
        model_chat text NULL CHECK (length(model_chat) <= 200),
        model_vision text NULL CHECK (length(model_vision) <= 200),
        model_embed text NULL CHECK (length(model_embed) <= 200),
        config jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(config) = 'object'),
        pages_cells integer NOT NULL DEFAULT 0 CHECK (pages_cells >= 0),
        pages_text_layer integer NOT NULL DEFAULT 0 CHECK (pages_text_layer >= 0),
        pages_ocr integer NOT NULL DEFAULT 0 CHECK (pages_ocr >= 0),
        pages_blank integer NOT NULL DEFAULT 0 CHECK (pages_blank >= 0),
        vision_crops integer NOT NULL DEFAULT 0 CHECK (vision_crops >= 0),
        model_calls integer NOT NULL DEFAULT 0 CHECK (model_calls >= 0),
        dropped_quotes integer NOT NULL DEFAULT 0 CHECK (dropped_quotes >= 0),
        quotes_returned integer NOT NULL DEFAULT 0 CHECK (quotes_returned >= 0),
        values_verified integer NOT NULL DEFAULT 0 CHECK (values_verified >= 0),
        values_needs_review integer NOT NULL DEFAULT 0 CHECK (values_needs_review >= 0),
        seconds_total real NULL CHECK (seconds_total >= 0),
        seconds_ocr real NULL CHECK (seconds_ocr >= 0),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, id),
        CHECK ((status = 'running') = (finished_at IS NULL)),
        CHECK (finished_at IS NULL OR finished_at >= started_at),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE SET NULL (document_id)
    )
    """,
    "CREATE INDEX processing_runs_started ON processing_runs (customer_id, started_at DESC)",
    """
    CREATE INDEX processing_runs_document
    ON processing_runs (customer_id, document_id, started_at DESC)
    """,
    """
    CREATE UNIQUE INDEX processing_runs_one_running
    ON processing_runs (customer_id, document_id) WHERE status = 'running'
    """,
    # --- what the app role may do, and row level security ---------------------------------
    # The least each needs: a save deletes and inserts invoices and links, never
    # updates them; lines are only inserted; runs are never deleted, and only their
    # outcome columns change.
    f"GRANT SELECT, INSERT, DELETE ON invoices, document_links TO {APP_ROLE}",
    f"GRANT SELECT, INSERT ON invoice_lines TO {APP_ROLE}",
    f"""
    GRANT SELECT, INSERT, UPDATE (status, error_code, ocr_engine, model_chat, model_vision,
        model_embed, config, pages_cells, pages_text_layer, pages_ocr, pages_blank, vision_crops,
        model_calls, dropped_quotes, quotes_returned, values_verified, values_needs_review,
        seconds_total, seconds_ocr, finished_at)
    ON processing_runs TO {APP_ROLE}
    """,
    *[f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" for table in NEW_TABLES],
    *[
        f"""
        CREATE POLICY customer_only ON {table}
        USING (customer_id = kaagaz_current_customer())
        WITH CHECK (customer_id = kaagaz_current_customer())
        """
        for table in NEW_TABLES
    ],
]

DOWNGRADE = [
    LOCK,
    # These labels have no older reason that tells the truth, so the downgrade
    # refuses instead of relabelling anything (PROVISIONAL).
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM extracted_values
                   WHERE label_reason IN ('check_failed', 'model_chosen', 'ocr_script')) THEN
            RAISE EXCEPTION 'values labelled check_failed, model_chosen or ocr_script exist; relabel them before downgrading'
                USING ERRCODE = 'check_violation';
        END IF;
    END
    $$
    """,
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM documents WHERE failure_code !~ '^[a-z_]{1,64}$') THEN
            RAISE EXCEPTION 'documents have failure codes 0003 does not allow (such as not_utf8); change them before downgrading'
                USING ERRCODE = 'check_violation';
        END IF;
    END
    $$
    """,
    "ALTER TABLE extracted_values DROP CONSTRAINT extracted_values_label_reason_check",
    """
    ALTER TABLE extracted_values ADD CONSTRAINT extracted_values_label_reason_check
        CHECK (label_reason IN (
            'direct_cell', 'readers_agree', 'readers_disagree', 'single_reader',
            'low_confidence', 'corrected', 'learned_correction'
        ))
    """,
    "ALTER TABLE documents DROP CONSTRAINT documents_failure_code_check",
    """
    ALTER TABLE documents ADD CONSTRAINT documents_failure_code_check
        CHECK (failure_code ~ '^[a-z_]{1,64}$')
    """,
    "DROP TABLE IF EXISTS processing_runs",
    "DROP TABLE IF EXISTS document_links",
    "DROP TABLE IF EXISTS invoice_lines",
    "DROP TABLE IF EXISTS invoices",
    """
    ALTER TABLE extracted_values
        DROP CONSTRAINT IF EXISTS extracted_values_label_both,
        DROP COLUMN IF EXISTS label_region,
        DROP COLUMN IF EXISTS label_page_no
    """,
    "ALTER TABLE pieces DROP COLUMN IF EXISTS text_latin, DROP COLUMN IF EXISTS context_header",
    """
    ALTER TABLE documents
        DROP COLUMN IF EXISTS progress,
        DROP COLUMN IF EXISTS card,
        DROP COLUMN IF EXISTS document_date,
        DROP COLUMN IF EXISTS summary,
        DROP COLUMN IF EXISTS languages,
        DROP COLUMN IF EXISTS doc_type
    """,
    """
    ALTER TABLE pages
        DROP COLUMN IF EXISTS low_resolution,
        DROP COLUMN IF EXISTS effective_dpi,
        DROP COLUMN IF EXISTS route
    """,
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
