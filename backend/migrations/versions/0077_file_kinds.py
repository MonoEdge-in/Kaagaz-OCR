"""All formats (#522, Tushar, 6 Nov 2026): ``documents.detected_type`` holds a file's kind.
NEEDS VRUSHIT'S APPROVAL: schema.

The upload still stores the type sniffed from the first bytes (``kaagaz/ingestion/sniff.py``),
and ``heic`` is one now. After the virus scan the worker tells the file's kind
(``kaagaz/ingestion/resolve.py``) and stores it in its place, so a .docx is ``docx``, not
``zip``, and a .txt is ``txt``, not ``text``. The column's CHECK (0001's, unnamed, so
PostgreSQL's ``documents_detected_type_check``) takes the kinds too; the sniffed types stay in
it, for an upload not read yet. Its comment says so.

Every document read before this was read by its sniffed type's one reader, so a ready one is
told here: a ready ``zip`` was read as a spreadsheet (``xlsx``) and a ready ``text`` file as CSV
(``csv``). Any other one keeps its sniffed type until it is read again (a zip that was not a
spreadsheet is ``needs_reader``). The touch trigger is off for this, as in 0002, so
``updated_at`` keeps each row's real last change. It runs as the table owner, which documents'
row level security does not hold (no FORCE), so it sees every customer's rows.

The downgrade puts every kind back to the sniffed type it came from (a HEIC photo, which
before 0077 had none, to ``ole2``: kept and never read), then 0001's CHECK and 0007's comment.

Merges after the versioning PR (#521), whose 0076 comes first; this changes nothing of it.

Revision ID: 0077
Revises: 0076 (version spotting)
"""

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None

CHECK = "documents_detected_type_check"
SNIFFED = ("pdf", "png", "jpeg", "tiff", "zip", "ole2", "text")  # 0001's
# kaagaz.ingestion.resolve.KINDS that are not sniffed types, with the sniffed type each comes from.
KINDS = {
    "heic": "ole2",  # sniffed as heic from 0077 on; before, no type held it
    "xlsx": "zip",
    "docx": "zip",
    "dotx": "zip",
    "pptx": "zip",
    "xls": "ole2",
    "doc": "ole2",
    "ppt": "ole2",
    "csv": "text",
    "txt": "text",
    "md": "text",
    "json": "text",
    "html": "text",
}
COMMENT = (
    "The file's kind. At upload, its type from the first bytes, never its name: pdf, png, jpeg, tiff,"
    " heic (HEIC or HEIF photo), zip (xlsx, docx and other zip files), ole2 (old xls, doc and ppt) or"
    " text (UTF-8 text). After the virus scan, the kind the worker read it as: pdf, png, jpeg, tiff,"
    " heic, xlsx, xls, csv, docx, dotx (Word template), doc, pptx, ppt, txt, md (Markdown), json,"
    " html or zip (a zip that is no Office file); the name's ending only chooses between the text"
    " kinds csv, txt, md, json and html."
)
OLD_COMMENT = (  # 0007's
    "Type from the file's first bytes, never its name: pdf, png, jpeg, tiff, zip (xlsx, docx and other"
    " zip files), ole2 (old xls and doc) or text (CSV and other UTF-8 text)."
)


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _check(types: tuple[str, ...]) -> str:
    listed = ", ".join(_literal(each) for each in types)
    return (
        f"ALTER TABLE documents DROP CONSTRAINT {CHECK},"
        f" ADD CONSTRAINT {CHECK} CHECK (detected_type IN ({listed}))"
    )


UPGRADE = [
    _check(SNIFFED + tuple(KINDS)),
    "ALTER TABLE documents DISABLE TRIGGER documents_touch",
    "UPDATE documents SET detected_type = 'xlsx' WHERE detected_type = 'zip' AND status = 'ready'",
    "UPDATE documents SET detected_type = 'csv' WHERE detected_type = 'text' AND status = 'ready'",
    "ALTER TABLE documents ENABLE TRIGGER documents_touch",
    f"COMMENT ON COLUMN documents.detected_type IS {_literal(COMMENT)}",
]

DOWNGRADE = [
    "ALTER TABLE documents DISABLE TRIGGER documents_touch",
    *[
        f"UPDATE documents SET detected_type = {_literal(sniffed)} WHERE detected_type = {_literal(kind)}"
        for kind, sniffed in KINDS.items()
    ],
    "ALTER TABLE documents ENABLE TRIGGER documents_touch",
    _check(SNIFFED),
    f"COMMENT ON COLUMN documents.detected_type IS {_literal(OLD_COMMENT)}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
