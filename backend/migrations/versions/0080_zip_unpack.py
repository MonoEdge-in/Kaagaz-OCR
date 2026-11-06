"""A zip upload unpacked into a folder (#522 PR 3, Tushar, 6 Nov 2026). NEEDS VRUSHIT'S APPROVAL:
schema.

The worker unpacks a zip as a folder upload the server makes itself (``kaagaz/jobs/unpack.py``):
its folders, its upload row and one item a file, so the zip's own document can always say what
was unpacked and why a file was not.

* ``folder_uploads.zip_document_id``: the zip's document, for an upload the worker made from a
  zip (NULL: a person's folder upload). At most one upload a zip (a unique index, partial). No
  key to ``documents``, as the items have none: a zip deleted forever leaves its upload.
* ``folder_upload_items.failure_code`` takes why a file inside a zip was refused, besides 0061c's
  codes: ``unsafe_path`` (an absolute path, ``..``, a drive letter, a backslash), ``link`` (a
  symbolic link or another special file), ``encrypted``, ``too_deep`` (past 100 levels),
  ``too_compressed`` (more than 100 times its packed size), ``damaged`` (it can't be unpacked),
  ``virus_found``, ``blocked_content`` and ``unscannable`` (its own virus check) and
  ``storage_full`` (the organisation's storage limit).

A nullable column with no default and a CHECK widened: no table rewrite, no row written. The
CHECK is added NOT VALID, then validated (0028's way). The downgrade deletes the uploads made from
zips (their items and folder rows go with them; the folders and documents stay), then drops the
column and puts 0061c's CHECK back.

Revision ID: 0080
Revises: 0078 (the file kinds; no 0079 exists when this was written)
"""

from alembic import op

revision = "0080"
down_revision = "0078"
branch_labels = None
depends_on = None

CHECK = "folder_upload_items_failure_code_check"  # 0061c's inline CHECK, by its default name
FAILURES_0061C = ("too_large", "unsupported_type", "empty_file", "folder_gone", "bad_name")
# The codes a file inside a zip may fail with besides (kaagaz.ingestion.unzip, kaagaz.jobs.unpack).
ZIP_FAILURES = (
    "unsafe_path",
    "link",
    "encrypted",
    "too_deep",
    "too_compressed",
    "damaged",
    "virus_found",
    "blocked_content",
    "unscannable",
    "storage_full",
)
FAILURES = FAILURES_0061C + ZIP_FAILURES
COLUMN_COMMENT = "The zip document this upload was unpacked from by the worker (#522); NULL for a folder upload a person made."


def _in(codes: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{code}'" for code in codes) + ")"


UPGRADE = [
    "ALTER TABLE folder_uploads ADD COLUMN zip_document_id uuid NULL",
    "CREATE UNIQUE INDEX folder_uploads_zip ON folder_uploads (customer_id, zip_document_id)"
    " WHERE zip_document_id IS NOT NULL",
    f"COMMENT ON COLUMN folder_uploads.zip_document_id IS '{COLUMN_COMMENT}'",
    f"ALTER TABLE folder_upload_items DROP CONSTRAINT {CHECK}",
    f"ALTER TABLE folder_upload_items ADD CONSTRAINT {CHECK} CHECK (failure_code IN {_in(FAILURES)}) NOT VALID",
    f"ALTER TABLE folder_upload_items VALIDATE CONSTRAINT {CHECK}",
]

DOWNGRADE = [
    "DELETE FROM folder_uploads WHERE zip_document_id IS NOT NULL",
    f"ALTER TABLE folder_upload_items DROP CONSTRAINT {CHECK}",
    f"ALTER TABLE folder_upload_items ADD CONSTRAINT {CHECK} CHECK (failure_code IN {_in(FAILURES_0061C)})",
    "DROP INDEX IF EXISTS folder_uploads_zip",
    "ALTER TABLE folder_uploads DROP COLUMN IF EXISTS zip_document_id",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
