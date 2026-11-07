"""A folder in Trash (#542, Tushar, 7 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema.

A folder deleted with everything inside it (#537's ``?contents=trash``) is kept in Trash with its
structure, and Restore puts it back where it was, like the Windows Recycle Bin
(``kaagaz.db.trashed_folders``).

* ``trashed_folders``: each folder of such a delete, as ``folders`` held it (the same id, parent,
  name, kind, order, auto-filing key, maker and time), with ``trash_id`` (one a delete: every
  folder of one delete shares it), ``root`` (the folder the person deleted) and who deleted it
  when. ``parent_id`` has no foreign key: the parent is another trashed folder, a live folder or
  gone. The live ``folders`` table never holds a trashed folder, so no folder query changes. The
  app reads, adds and deletes rows, never updates one; row level security as every customer table
  (``customer_only``). Its users keys cascade, as only an organisation's purge deletes users.
* ``documents.trashed_folder_id`` and ``documents.trash_id``: set on the documents such a delete
  sends to Trash (their folder, which is in ``trashed_folders`` now, and the delete's id), cleared
  when they are restored. No foreign key: both name a trashed folder or its delete. A partial
  index each, empty at first.
* ``kaagaz_customer_ids_with_trash`` also returns the customers with a trashed folder that old,
  so the hourly purge removes an empty one too (still ids only).

Nullable columns with no default and a new table: no table rewrite, no row written. The downgrade
drops the table and the columns, which forgets which folder each trashed document was in (they
stay in Trash at the top level, as before 0081), and puts 0002's function back.

Revision ID: 0081
Revises: 0080
"""

from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
POLICY = "customer_id = kaagaz_current_customer()"
TRASH_FUNCTION = "kaagaz_customer_ids_with_trash(p_before timestamptz)"
_FUNCTION = """
CREATE OR REPLACE FUNCTION {name}
RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS
$$ {body} $$
"""
BODY_0002 = "SELECT DISTINCT d.customer_id FROM public.documents d WHERE d.deleted_at < p_before"
BODY = (
    "SELECT d.customer_id FROM public.documents d WHERE d.deleted_at < p_before"
    " UNION SELECT t.customer_id FROM public.trashed_folders t WHERE t.deleted_at < p_before"
)
TABLE_COMMENT = (
    "A folder deleted with everything inside it, kept in Trash with its structure until it is restored,"
    " deleted forever or purged after TRASH_DAYS (#542). The live folders table never holds one."
)
COLUMN_COMMENTS = {
    "id": "The folder's own id, the same as it had in folders and gets back on restore.",
    "customer_id": "The organisation the folder belongs to.",
    "parent_id": "The folder it was in: another trashed folder, a live folder, or gone. No key on purpose.",
    "name": "The folder's name when it was deleted.",
    "kind": "Its kind as folders.kind had it (department, category, party, property, year or custom).",
    "sort_order": "Its department order as folders.sort_order had it.",
    "autofile_key": "Its auto-filing key as folders.autofile_key had it; dropped on restore if a live folder took it.",
    "created_by": "Who made the folder (folders.created_by).",
    "created_at": "When the folder was made (folders.created_at).",
    "trash_id": "The delete that sent it to Trash: every folder of one delete shares it, and so do its documents.",
    "root": "True for the folder the person deleted; false for a folder that was inside it.",
    "deleted_at": "When it went to Trash; it is purged TRASH_DAYS later, after its documents.",
    "deleted_by": "Who deleted it.",
}
DOCUMENT_COMMENTS = {
    "trashed_folder_id": "The trashed folder this document went to Trash with (trashed_folders.id), restored into;"
    " NULL otherwise. No key on purpose.",
    "trash_id": "The folder delete that sent it to Trash (trashed_folders.trash_id); NULL otherwise.",
}


def _literal(words: str) -> str:
    return "'" + words.replace("'", "''") + "'"


UPGRADE = [
    """
    CREATE TABLE trashed_folders (
        id uuid PRIMARY KEY,
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        parent_id uuid NULL,
        name text NOT NULL,
        kind text NOT NULL,
        sort_order integer NULL,
        autofile_key text NULL,
        created_by uuid NOT NULL,
        created_at timestamptz NOT NULL,
        trash_id uuid NOT NULL,
        root boolean NOT NULL,
        deleted_at timestamptz NOT NULL,
        deleted_by uuid NOT NULL,
        UNIQUE (customer_id, id),
        FOREIGN KEY (customer_id, created_by) REFERENCES users (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, deleted_by) REFERENCES users (customer_id, id) ON DELETE CASCADE
    )
    """,
    # Down the tree and "is its parent in Trash too" (the Trash list's items), one delete's
    # folders, and the purge's oldest first.
    "CREATE INDEX trashed_folders_parent ON trashed_folders (customer_id, parent_id)",
    "CREATE INDEX trashed_folders_trash ON trashed_folders (customer_id, trash_id)",
    "CREATE INDEX trashed_folders_deleted ON trashed_folders (customer_id, deleted_at)",
    "ALTER TABLE documents ADD COLUMN trashed_folder_id uuid NULL, ADD COLUMN trash_id uuid NULL",
    "CREATE INDEX documents_trashed_folder ON documents (customer_id, trashed_folder_id)"
    " WHERE trashed_folder_id IS NOT NULL",
    "CREATE INDEX documents_trash_id ON documents (customer_id, trash_id) WHERE trash_id IS NOT NULL",
    f"COMMENT ON TABLE trashed_folders IS {_literal(TABLE_COMMENT)}",
    *[f"COMMENT ON COLUMN trashed_folders.{column} IS {_literal(c)}" for column, c in COLUMN_COMMENTS.items()],
    *[f"COMMENT ON COLUMN documents.{column} IS {_literal(c)}" for column, c in DOCUMENT_COMMENTS.items()],
    f"GRANT SELECT, INSERT, DELETE ON trashed_folders TO {APP_ROLE}",
    "ALTER TABLE trashed_folders ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY customer_only ON trashed_folders USING ({POLICY}) WITH CHECK ({POLICY})",
    "COMMENT ON POLICY customer_only ON trashed_folders IS 'A row is seen and written only by its own customer.'",
    _FUNCTION.format(name=TRASH_FUNCTION, body=BODY),
]

DOWNGRADE = [
    _FUNCTION.format(name=TRASH_FUNCTION, body=BODY_0002),
    "DROP INDEX IF EXISTS documents_trash_id",
    "DROP INDEX IF EXISTS documents_trashed_folder",
    "ALTER TABLE documents DROP COLUMN IF EXISTS trash_id, DROP COLUMN IF EXISTS trashed_folder_id",
    "DROP TABLE IF EXISTS trashed_folders",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
