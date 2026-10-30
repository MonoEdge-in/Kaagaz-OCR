"""The data dictionary's gaps (#404): 0041, 0045 and 0046 left a table and 56 columns without
the comment 0007's rule asks of every migration, so tests/test_migration_0007_db.py failed.

* ``upload_sessions`` (0045): the table and every column.
* ``documents``, ``folders``, ``audit_log`` and ``version_suggestions`` (0041): the columns
  0041 added without one.
* ``model_rate_*`` and ``user_model_rate_overrides`` (0046): every column.

Comments only, no data change; 0007's shared meanings hold (``*_at`` a timestamptz, "same
customer" a (customer_id, id) foreign key). The downgrade sets each comment written here back
to NULL, as 0058 left it.

Revision ID: 0059
Revises: 0058 (the reader choice)
"""

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

CUSTOMER = "→ customers.id, the customer the row belongs to (row level security filters on it)."
CREATED = "When the row was written."
ORGANISATION = (
    "→ customers.id, the organisation (row level security filters on it). NULL for model work no"
    " organisation asked for."
)
TABLE = "A resumable upload still being sent in parts, until it is finished or 7 days pass with no part."

# table → {column: comment}. Plain constants: no colon followed by a word, which SQLAlchemy
# would read as a bind parameter.
COLUMNS: dict[str, dict[str, str]] = {
    "upload_sessions": {
        "id": "Upload session id, chosen by the server and part of its storage key.",
        "customer_id": CUSTOMER,
        "user_id": "users.id of who is uploading (no foreign key). Only they can send its parts.",
        "storage_key": "Where the file is written in the file store, customers/{customer_id}/uploads/...",
        "storage_upload_id": "The file store's id for the multipart upload, to send, finish or cancel it.",
        "file_size": "Size of the whole file in bytes, as the browser said; more than 0.",
        "part_count": "Number of parts the file is sent in (8 MiB each, the last smaller), 1 to 10000.",
        "metadata": "JSON of what the upload asked for (file name, the browser's fingerprint of the file,"
        " folder, batch, reading, the document it is a version of, version note) and, under parts,"
        " each part received with its hash.",
        "created_at": CREATED,
        "expires_at": "When it is dropped unfinished and its parts deleted: 7 days after it started or"
        " after its last part.",
    },
    "documents": {
        "details_updated_at": "When a person last saved the family's details (owner, label or a version"
        " note). NULL = never.",
        "details_updated_by": "users.id of who last saved the family's details (no foreign key). NULL = never.",
        "name_key": "The file name as versions compare it: lower case, no extension, dates, version words"
        " or punctuation (kaagaz/versions.py). Trigram indexed.",
        "sensitivity": "public, internal, confidential or restricted, the same on every version of the"
        " family. Set by the type (land records, contracts and bank statements confidential, the rest"
        " internal) until a person chooses it.",
        "text_bands": "The fingerprint's 16 band hashes, to find documents whose text may overlap (GIN"
        " indexed). Empty under 20 words; NULL = not looked at yet.",
        "text_minhash": "MinHash fingerprint of the first reader's text, 64 numbers, to estimate how much two"
        " texts overlap. Empty under 20 words; NULL = not looked at yet.",
        "version_note": "A note on this version, at most 200 characters, given at upload or later. NULL = none.",
    },
    "folders": {
        "sort_order": "A department's place on the dashboard, lowest first; NULL after those (then by name).",
    },
    "audit_log": {
        "folder_id": "folders.id the action was in (no foreign key): the document's folder then, or the"
        " folder acted on. For the timelines. NULL otherwise.",
        "family_id": "documents.family_id of the document acted on (no foreign key), for its family's"
        " timeline. NULL for other targets.",
    },
    "version_suggestions": {
        "id": "Suggestion id.",
        "customer_id": CUSTOMER,
        "document_id": "→ documents.id (same customer), the upload that may be a new version.",
        "candidate_family_id": "documents.family_id of the family it may be a new version of (no foreign key).",
        "text_overlap": "Estimated share of the two texts that match, 0 to 1, from their text_minhash. NULL"
        " when either has none.",
        "name_similarity": "Trigram similarity of the two name keys, 0 to 1. NULL when either has none.",
        "shared_identifier": "The identifier kind (document_links) the two share, for two documents of one"
        " type. NULL when none.",
        "outcome": "auto (linked at once) or suggested (a person decides).",
        "status": "pending, accepted, rejected or undone.",
        "decided_by": "users.id of who last accepted, rejected or undid it (no foreign key). NULL while"
        " pending, or when linked at once and not undone.",
        "decided_at": "When it was last accepted, rejected or undone. NULL while pending.",
        "previous_folder_id": "folders.id the document was in before it was linked, where Undo puts it back"
        " (no foreign key). NULL, or a folder deleted since, means the top level.",
        "created_at": CREATED,
    },
    "model_rate_scheduler": {
        "id": "Always true: the table has one row.",
        "next_at": "Earliest time the next model request may start on the whole platform (requests are"
        " spaced by the platform's requests a minute).",
    },
    "model_rate_waiters": {
        "request_id": "One model request's ticket in the queue, chosen by the app.",
        "customer_id": ORGANISATION,
        "user_id": "→ users.id (same customer), who the request is for. NULL for work no person asked for.",
        "queued_at": "When the request joined the queue (clock time); earlier goes first within a turn.",
        "lease_until": "The ticket is dropped after this time (10 seconds, renewed each time it asks), so"
        " a crashed process cannot hold up the queue.",
    },
    "model_rate_customer_turns": {
        "customer_id": ORGANISATION + " One row per organisation.",
        "last_served_at": "When this organisation's last request was let through; the least recently"
        " served goes next.",
    },
    "model_rate_user_turns": {
        "customer_id": "customers.id of the organisation (row level security filters on it). No foreign key"
        " of its own: only (customer_id, user_id) → users, when user_id is set. NULL for model work no"
        " organisation asked for.",
        "user_id": "→ users.id (same customer). NULL for work no person asked for. One row per"
        " organisation and person.",
        "last_served_at": "When this person's last request was let through; within an organisation, the"
        " least recently served goes next.",
    },
    "model_rate_overrides": {
        "customer_id": "→ customers.id, the organisation capped (row level security filters on it).",
        "rpm": "The organisation's cap in model requests a minute, 1 to 600, set by an operator.",
        "updated_at": "When an operator last set it.",
    },
    "user_model_rate_overrides": {
        "customer_id": "customers.id, the customer the row belongs to (row level security filters on it),"
        " through (customer_id, user_id) → users; no foreign key of its own.",
        "user_id": "→ users.id (same customer), the person capped.",
        "rpm": "The person's cap in model requests a minute, 1 to 120, set by an operator.",
        "updated_at": "When an operator last set it.",
    },
    "model_rate_state": {
        "scope": "organisation or user, the cap this row times.",
        "customer_id": CUSTOMER,
        "user_id": "→ users.id (same customer) for scope user; NULL for scope organisation.",
        "next_at": "Earliest time the next request under this cap may start.",
    },
}


def _literal(comment: str | None) -> str:
    return "NULL" if comment is None else "'" + comment.replace("'", "''") + "'"


def _comment_all(clear: bool) -> None:
    op.execute(f"COMMENT ON TABLE upload_sessions IS {_literal(None if clear else TABLE)}")
    for table, columns in COLUMNS.items():
        for column, comment in columns.items():
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS {_literal(None if clear else comment)}")


def upgrade() -> None:
    _comment_all(clear=False)


def downgrade() -> None:
    _comment_all(clear=True)
