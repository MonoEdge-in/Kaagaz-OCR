"""The data dictionary: a comment on every table and column in public (PRD 3 N1).

The NL-to-SQL agent (after handover) reads these straight from the catalog, so
no Markdown copy is kept. Each comment says what the column holds, its unit
where it has one, and how it joins. They hold no secrets, host names, customer
names or identity numbers (tests/test_migration_0007_db.py scans for them).
``alembic_version`` is commented too, so nothing is left out by name.

Every later migration comments its own new tables and columns, or the
completeness test in tests/test_migration_0007_db.py fails.

Shared meanings, said once here and kept short below:
* every ``*_at`` column is a timestamptz, every ``id`` a uuid unless said;
* a "spot" (``regions``, ``region``, ``label_region``) is
  {kind "box", page, box [x0, y0, x1, y1]} in fractions (0 to 1) of the page's
  width and height from the top left, or {kind "cell", page (the sheet), row,
  column} counted from 1 (kaagaz/reading/spans.py);
* "same customer" means the foreign key is (customer_id, id), so a row never
  points at another customer's row.

Comments only, no data change. The
downgrade sets every comment written here back to NULL.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

CUSTOMER = "→ customers.id, the customer the row belongs to (row level security filters on it)."
CREATED = "When the row was written."

# table → (table comment, {column: comment}). Plain constants: no colon followed by
# a word, which SQLAlchemy would read as a bind parameter.
TABLES: dict[str, tuple[str, dict[str, str]]] = {
    "alembic_version": (
        "Alembic's record of the schema revision. Not product data and not per customer.",
        {"version_num": "The migration this database is at, such as 0007."},
    ),
    "customers": (
        "One row per customer company. Every other table belongs to one customer through customer_id.",
        {
            "id": "Customer id.",
            "name": "Company name shown in the app, 1 to 200 characters.",
            "max_upload_mb": "The customer's own upload limit in MB, set by its admin. NULL = only the platform limits apply.",
            "created_at": CREATED,
        },
    ),
    "users": (
        "People who sign in. Each belongs to one customer. E-mails are unique across all customers.",
        {
            "id": "User id.",
            "customer_id": CUSTOMER,
            "email": "Sign-in e-mail, lower case, unique across all customers.",
            "display_name": "Name shown in the app, 1 to 100 characters.",
            "role": "member (upload and ask), reviewer (also review and read again) or admin (also users and settings).",
            "password_hash": "scrypt hash of the password with its salt and parameters, never the password.",
            "disabled": "true = turned off by an admin. Sign-in and existing sessions are refused.",
            "created_at": CREATED,
            "failed_logins": "Sign-in attempts since the last success. LOGIN_MAX_FAILURES of them lock the account.",
            "locked_until": "Sign-in is refused until this time. NULL = not locked.",
            "session_version": "Copied into each session. Raising it ends every other session of the user.",
            "must_change_password": "true = holds a temporary password from an admin and must set a new one first.",
            "totp_secret_enc": "Two-step sign-in secret, encrypted (AES-256-GCM, key from TOTP_ENCRYPTION_KEY). NULL = off.",
            "totp_enabled_at": "When two-step sign-in was turned on. NULL = off.",
            "totp_last_step": "Number of the last accepted 30-second code step, so a code works once. NULL = off.",
            "last_login_at": "When the user last signed in or confirmed their password.",
        },
    ),
    "documents": (
        "One row per uploaded file version. The file is in the file store under customers/{customer_id}/documents/{id}/.",
        {
            "id": "Document id, chosen before the file is stored, and part of its storage key.",
            "customer_id": CUSTOMER,
            "display_name": "File name as uploaded or renamed, 1 to 255 characters.",
            "sha256": "SHA-256 of the uploaded bytes, lower-case hex. Unique per customer, so a duplicate is found.",
            "size_bytes": "File size in bytes.",
            "detected_type": "Type from the file's first bytes, never its name (pdf, png, jpeg, tiff, zip for xlsx, ole2, text for CSV).",
            "status": "received, clean (scanned, being read), rejected (failed the scan, final), needs_reader, ready (searchable) or failed.",
            "failure_code": "Why it was rejected or failed, a short code such as corrupt_file. NULL otherwise.",
            "page_count": "Number of pages (PDF pages, image frames, or sheets with cells). NULL until read.",
            "uploaded_by": "→ users.id (same customer), who uploaded it.",
            "created_at": "When it was uploaded.",
            "updated_at": "When the row last changed (a trigger sets it).",
            "folder_id": "→ folders.id (same customer). NULL = top level.",
            "deleted_at": "When it went to Trash. NULL = not in Trash. Deleted for good after TRASH_DAYS days.",
            "deleted_by": "→ users.id (same customer), who put it in Trash.",
            "family_id": "→ documents.id of the first version. Every version of one document shares it.",
            "version_no": "Version number within the family, from 1.",
            "is_current": "true for the one version of the family that is shown and searched.",
            "attempts": "Number of the latest processing try, 0 before any. Display only.",
            "max_attempts": "JOB_MAX_ATTEMPTS at the latest try. NULL before the first. Display only.",
            "doc_type": "Type from the model's card (invoice, receipt, bank_statement, ...). NULL without a card.",
            "languages": "Language codes found, from en, hi, mr and other.",
            "summary": "Short English summary by the model, at most 600 characters. NULL without a card.",
            "document_date": "The document's own date, quoted by the model and found in its text. NULL if not found.",
            "card": "The document card as JSON (type, title, summary, parties, identifiers, key facts, each with its spot).",
            "progress": "Processing progress, JSON {step, done, total}. In the reading step done and total count pages.",
        },
    ),
    "pages": (
        "One row per page of a document (per sheet for a spreadsheet). Key (document_id, page_no).",
        {
            "customer_id": CUSTOMER,
            "document_id": "→ documents.id (same customer).",
            "page_no": "Page number from 1 (the sheet number for a spreadsheet).",
            "width": "Page width in unit (PDF points, image pixels, or the last used column of a sheet).",
            "height": "Page height in unit (PDF points, image pixels, or the last used row of a sheet).",
            "unit": "pt (PDF points), px (image pixels) or cells (a spreadsheet sheet).",
            "has_image": "true when a picture of the page is in the file store (pages/{page_no}.webp).",
            "route": "How the page was read (cells, text_layer = the PDF's own text, ocr, blank). NULL = not read.",
            "effective_dpi": "Measured resolution of the page's scan or photo, in dots per inch. NULL if not measurable.",
            "low_resolution": "true when effective_dpi is below LOW_RES_DPI. The page is flagged, never refused.",
        },
    ),
    "reader_outputs": (
        "Each reader's raw output for a document, kept next to the cleaned values so silent changes can be found.",
        {
            "id": "Row id.",
            "customer_id": CUSTOMER,
            "document_id": "→ documents.id (same customer).",
            "role": "first (the main reader) or second (an independent second reading, PDFs only).",
            "reader": "Reader name, such as pdfplumber, pdfium, xlsx, csv, rapidocr or paddleocr-vl.",
            "reader_version": "The reader's version text.",
            "output": "JSON array of spans {text, raw, region, confidence}. region is a box in fractions of the page, or a cell.",
            "created_at": CREATED,
        },
    ),
    "pieces": (
        "Searchable passages of a document's text, in order. A piece may run across pages.",
        {
            "id": "Piece id.",
            "customer_id": CUSTOMER,
            "document_id": "→ documents.id (same customer).",
            "seq": "Position in the document, from 0.",
            "page_from": "First page of the piece, from 1 (the sheet for a spreadsheet).",
            "page_to": "Last page of the piece, at least page_from.",
            "text": "The passage as read. Spreadsheet and table rows are written as header and value pairs.",
            "regions": "JSON array of spots, one per line or cell. A box is [x0, y0, x1, y1] in fractions (0 to 1) of the page.",
            "label": "verified when every line was read directly (cells or the PDF's text), else needs_review.",
            "embedding": "Meaning vector of the piece (halfvec, 1024 numbers). NULL until embedded.",
            "embedding_model": "The MODEL_EMBED model the vector was made with. NULL together with embedding.",
            "tsv": "Generated. English word index of text.",
            "created_at": CREATED,
            "context_header": "One line saying where the piece sits (document, type, date, section, pages). Never cited.",
            "text_latin": "text with its Devanagari spelt in Latin letters, when mostly Devanagari. Else NULL.",
            "tsv_simple": "Generated. Word index of text and text_latin with no stemming, for any script.",
        },
    ),
    "extracted_values": (
        "Single values read from a document (a field such as invoice_number), each with its page and spot.",
        {
            "id": "Value id.",
            "customer_id": CUSTOMER,
            "document_id": "→ documents.id (same customer).",
            "record_key": "Groups one record's values, such as card, document, a sheet row or an invoice line.",
            "field": "Field name in lower snake case, such as total_amount or invoice_date.",
            "value_text": "The value as shown. A reviewer's or a remembered fix may change it.",
            "value_raw": "The value as first read, never changed afterwards.",
            "second_reading": "The second reader's or the vision model's reading of the same spot. NULL if none.",
            "value_number": "value_text as a number when it reads as one (currency signs and commas removed, no unit kept).",
            "value_date": "value_text as a date when it reads as one (day first).",
            "page_no": "Page of the value, from 1 (the sheet for a spreadsheet).",
            "region": "The value's spot on page_no, in fractions of the page or as a cell.",
            "reader": "Name of the document's first reader.",
            "confidence": "The reading engine's confidence, 0 to 1. NULL for direct reads and some OCR engines.",
            "label": "verified or needs_review, shown to the user.",
            "label_reason": "Why the label was given, such as readers_agree, low_confidence, check_failed or corrected.",
            "created_at": CREATED,
            "updated_at": "When the row last changed (a trigger sets it).",
            "label_page_no": "Page of the field's printed label when it sits elsewhere. NULL together with label_region.",
            "label_region": "Spot of the field's printed label on label_page_no. NULL together with label_page_no.",
        },
    ),
    "corrections": (
        "Review history, one row per value a reviewer fixed. The app only adds rows.",
        {
            "id": "Row id.",
            "customer_id": CUSTOMER,
            "document_id": "→ documents.id (same customer).",
            "field": "Field name of the fixed value, as in extracted_values.field.",
            "value_id": "→ extracted_values.id (same customer). NULL once the document is read again.",
            "old_value": "value_text before the fix.",
            "new_value": "value_text after the fix.",
            "choice": "first (the first reader's reading), second (the second reading) or typed (by the reviewer).",
            "corrected_by": "→ users.id (same customer), the reviewer.",
            "created_at": CREATED,
        },
    ),
    "learned_corrections": (
        "Fixes a reviewer chose to remember, for this customer only. Used when a later reading matches exactly.",
        {
            "id": "Row id.",
            "customer_id": CUSTOMER,
            "field": "Field name the fix is for.",
            "raw_value": "The reading exactly as the reader gave it (extracted_values.value_raw).",
            "corrected_value": "The text used instead. The value is then labelled verified (learned_correction).",
            "created_by": "→ users.id (same customer), the reviewer of the latest fix.",
            "created_at": CREATED,
            "updated_at": "When the fix was last replaced (a trigger sets it).",
        },
    ),
    "conversations": (
        "Chat conversations. Each belongs to one user, who alone can open it.",
        {
            "id": "Conversation id.",
            "customer_id": CUSTOMER,
            "user_id": "→ users.id (same customer), the owner.",
            "title": "The first question, cut to 200 characters.",
            "created_at": CREATED,
        },
    ),
    "messages": (
        "Questions and answers in a conversation, in created_at order.",
        {
            "id": "Message id.",
            "customer_id": CUSTOMER,
            "conversation_id": "→ conversations.id (same customer).",
            "role": "user (a question) or assistant (an answer).",
            "text": "The question as asked, or the answer's sentences (a fixed line unless the status is answered).",
            "answer": "Answers only. JSON with status, label, sentences and citations (document, page, spots).",
            "created_at": CREATED,
            "rewritten_question": "A follow-up rewritten to stand alone, at most 300 characters. NULL when there is none.",
            "scope": "JSON object (at most 4 KiB) of the folder or documents the question was limited to. NULL when none.",
        },
    ),
    "feedback": (
        "Thumbs up or down on an answer, one per user and message. A new vote replaces the old.",
        {
            "id": "Row id.",
            "customer_id": CUSTOMER,
            "message_id": "→ messages.id (same customer), the rated answer.",
            "user_id": "→ users.id (same customer), who rated it.",
            "rating": "up or down.",
            "reason": "Optional text the user typed, at most 500 characters.",
            "created_at": "When the vote was last given.",
        },
    ),
    "audit_log": (
        "Who did what, for admins. Ids and short codes only, never document text.",
        {
            "id": "Row number, rising (bigint).",
            "customer_id": CUSTOMER,
            "actor_user_id": "users.id of who acted (no foreign key). NULL for command-line actions and failed sign-ins.",
            "action": "What happened, such as auth.login, document.upload, chat.ask or review.resolve.",
            "target_type": "Kind of thing acted on, such as document, folder, tag, user or message.",
            "target_id": "Id of that thing, as text.",
            "details": "JSON of ids, statuses, counts and flags. Never document text, names or passwords.",
            "created_at": CREATED,
        },
    ),
    "folders": (
        "Folders for documents, nested at most 5 levels.",
        {
            "id": "Folder id.",
            "customer_id": CUSTOMER,
            "parent_id": "→ folders.id (same customer). NULL = top level.",
            "name": "1 to 100 characters, unique among its siblings ignoring case.",
            "created_by": "→ users.id (same customer), who made it.",
            "created_at": CREATED,
        },
    ),
    "tags": (
        "Labels a customer puts on documents.",
        {
            "id": "Tag id.",
            "customer_id": CUSTOMER,
            "name": "1 to 40 characters, unique per customer ignoring case.",
            "created_by": "→ users.id (same customer), who made it.",
            "created_at": CREATED,
        },
    ),
    "document_tags": (
        "Which tag is on which document version. Every version of a family carries the same tags.",
        {
            "customer_id": CUSTOMER,
            "document_id": "→ documents.id (same customer).",
            "tag_id": "→ tags.id (same customer).",
            "created_at": CREATED,
        },
    ),
    "invoices": (
        "The invoice read from a document, at most one per document. Amounts are as printed, never converted.",
        {
            "id": "Invoice id.",
            "customer_id": CUSTOMER,
            "document_id": "→ documents.id (same customer), unique.",
            "invoice_number": "Invoice number as printed. NULL if not found.",
            "invoice_date": "Invoice date. NULL if not found.",
            "seller_name": "Seller's name as printed. NULL if not found.",
            "seller_gstin": "Seller's GSTIN, upper case without spaces. NULL if not found.",
            "buyer_name": "Buyer's name as printed. NULL if not found.",
            "buyer_gstin": "Buyer's GSTIN, upper case without spaces. NULL if not found.",
            "subtotal": "Amount before tax, in rupees (see currency), 2 decimals, as printed. NULL if not found.",
            "tax_total": "Total tax, in rupees (see currency), 2 decimals, as printed. NULL if not found.",
            "total": "Invoice total, in rupees (see currency), 2 decimals, as printed. NULL if not found.",
            "currency": "Currency code of the amounts. Always INR (rupees): the app sets nothing else.",
            "label": "verified only when the checks did not fail and every value used is verified.",
            "check_status": "passed, failed (see check_reasons) or not_checked (no check could run).",
            "check_reasons": "Why the checks failed, such as lines_sum_mismatch or bad_gstin. Empty unless failed.",
            "created_at": CREATED,
            "updated_at": "Same as created_at. A new reading replaces the row.",
        },
    ),
    "invoice_lines": (
        "Line items of an invoice, in printed order.",
        {
            "id": "Line id.",
            "customer_id": CUSTOMER,
            "invoice_id": "→ invoices.id (same customer and document).",
            "document_id": "→ documents.id, the invoice's own document.",
            "line_no": "Line number from 1.",
            "description": "Item text as printed.",
            "hsn": "HSN or SAC code as printed.",
            "quantity": "Quantity as printed, in unit.",
            "unit": "Unit of the quantity as printed.",
            "rate": "Price per unit, in rupees (the invoice's currency), 2 decimals, as printed.",
            "amount": "Line amount, in rupees (the invoice's currency), 2 decimals, as printed.",
            "label": "verified when every value used is verified, else needs_review.",
            "created_at": CREATED,
        },
    ),
    "document_links": (
        "Two documents of one customer that share an identifier, such as an invoice or PO number. One row each way.",
        {
            "id": "Link id.",
            "customer_id": CUSTOMER,
            "document_id": "→ documents.id (same customer), one of the two documents.",
            "linked_document_id": "→ documents.id (same customer), the other one.",
            "identifier_kind": "invoice_number, po_number, challan_number, survey_number, khata_number, account_number or reference_number.",
            "identifier_value": "The identifier, lower case with no spaces, so spellings match.",
            "created_at": CREATED,
        },
    ),
    "processing_runs": (
        "One row per processing try of a document, for the numbers on the admin page. Deleted with the document.",
        {
            "id": "Run id.",
            "customer_id": CUSTOMER,
            "document_id": "→ documents.id (same customer).",
            "started_at": "When the worker started this try.",
            "finished_at": "When it ended. NULL while running.",
            "status": "running, ready, failed, needs_reader, rejected or superseded (a newer try took over).",
            "error_code": "Short error code, such as ocr_failed, or model_... on a run that still ended ready. NULL if none.",
            "ocr_engine": "OCR engine name and version, when any page went to OCR.",
            "model_chat": "MODEL_CHAT model name, when the chat model was called.",
            "model_vision": "MODEL_VISION model name, when a value crop was read.",
            "model_embed": "MODEL_EMBED model name, when pieces were embedded.",
            "config": "JSON of the settings the run used (reader thresholds, DPI, model budgets).",
            "pages_cells": "Pages read as spreadsheet cells.",
            "pages_text_layer": "Pages read from the PDF's own text.",
            "pages_ocr": "Pages read by OCR.",
            "pages_blank": "Pages found blank.",
            "vision_crops": "Value crops sent to the vision model.",
            "model_calls": "Calls to the chat model (the card and the invoice windows).",
            "dropped_quotes": "Model quotes not found in the document's text, so dropped.",
            "quotes_returned": "Quotes the model returned.",
            "values_verified": "Extracted values labelled verified.",
            "values_needs_review": "Extracted values labelled needs_review.",
            "seconds_total": "Seconds from start to finish. NULL while running.",
            "seconds_ocr": "Seconds spent in OCR calls. NULL when no OCR engine is set.",
            "created_at": CREATED,
        },
    ),
}


def _literal(comment: str | None) -> str:
    return "NULL" if comment is None else "'" + comment.replace("'", "''") + "'"


def _comment_all(clear: bool) -> None:
    for table, (table_comment, columns) in TABLES.items():
        op.execute(f"COMMENT ON TABLE {table} IS {_literal(None if clear else table_comment)}")
        for column, comment in columns.items():
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS {_literal(None if clear else comment)}")


def upgrade() -> None:
    _comment_all(clear=False)


def downgrade() -> None:
    _comment_all(clear=True)
