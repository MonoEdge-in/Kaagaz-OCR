"""Who can see what: the tables and columns (PRD 7 section 10, migration (a), #590, Tushar,
14 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema.

Every new setting defaults to today's behaviour, so nothing changes on release day: no code reads
the new columns yet but the two Trash copies (``kaagaz.db.folders._TRASH_TREE``,
``kaagaz.db.trashed_folders._MAKE_LIVE``) and ``python -m kaagaz.cli check-access``.

* ``users.management`` (false), ``customers.access_version`` (1) and
  ``customers.department_pointer`` (false). The app keeps SELECT only on customers' new columns.
* ``folders.department_only`` (false, only on a department: ``folders_department_only``) and
  ``folders.private`` (false); ``folders.department_id`` and ``folders.private_folder_id``, each
  folder's stored department (the top-level department folder it is in, itself for a department)
  and private folder (the private folder it is in, itself for a private one). All four on
  ``trashed_folders`` too, as both Trash copies carry them.
* ``documents.department_id`` and ``private_folder_id`` (its folder's), ``held_for`` (NULL: a
  file in no folder stays open), ``locked_at`` and ``locked_by`` (both NULL or both set,
  ``documents_locked``); ``documents_status_check`` takes ``locked`` (unused until PR 12).
* ``audit_log.subject_user_id`` (the person an entry is about; no key, as ``actor_user_id``),
  empty; ``audit_log_kept`` keeps it. ``learned_corrections
  .source_document_id`` (the document a correction was made on; old rows NULL), its key SET NULL
  when that document is deleted for good, so the correction stays as an old row does.
* ``department_members``, ``private_folder_members``, ``document_shares`` and ``lock_grants``,
  empty. Every key holds the organisation; a user is never deleted, so their user keys are NO
  ACTION and ``kaagaz_finish_customer_delete`` (0068's, with one DELETE each and the audit column
  added) deletes them by name; shares and lock grants go with their document version (CASCADE).
  Row level security with ``customer_only``; the app reads, adds and deletes them and may change
  a membership's ``is_head`` only.
* No folder id here has a key to ``folders``: a folder in Trash leaves ``folders`` and comes back
  with the same id (0081), and its members and stored values must survive that. The check
  command reports a member row whose folder is in neither table.
* Every grant goes to ``kaagaz_app`` and ``kaagaz_worker`` alike (0083's equality test).

Schema only: every stored department and private folder starts NULL. The backfill from the folder
tree and ``audit_log_subject`` (the index on ``subject_user_id``) are 0085's (PR 5), with the
triggers that keep the values right from then on, so 0084 and 0085 must run in the same release:
alone, 0084 leaves every stored value NULL, which ``check-access`` reports.

Cost, so the migration can run while the app serves (infra/ec2/deploy.sh): no table is rewritten
and no row is written (the new columns are NULL or constant defaults). ``documents`` is locked
first (0004's order), then read once for its two new CHECKs (one ALTER) and once for each of its
two new keys; ``folders`` once for its CHECK, ``learned_corrections`` once for its key. The other
tables take their own locks, the busy ones last: ``customers``, ``users`` and ``audit_log`` are
held ACCESS EXCLUSIVE only for the catalog-only statements at the end (the new keys hold
``customers`` and ``users`` SHARE ROW EXCLUSIVE from the first of them: reads go on, writes wait).
Every lock is held until the commit.

The downgrade drops the four tables and every column (their rows are lost), puts back 0001's
status CHECK (refused while a document is ``locked``), 0007's status comment and 0068's finish.

Revision ID: 0084
Revises: 0083
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{name[:4]}_for_0084", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0047 = _load("0047_purge_organization_words.py")
_0068 = _load("0068_audit_keeping.py")
_literal = _0068._literal
ROLES = ("kaagaz_app", "kaagaz_worker")
POLICY = "customer_id = kaagaz_current_customer()"
TABLES = ("department_members", "private_folder_members", "document_shares", "lock_grants")
# Taken first, as 0004's: the statements after it that reach documents (its ALTER, the keys of
# learned_corrections, document_shares and lock_grants) then queue behind the app, never deadlock.
LOCK = "LOCK TABLE documents IN ACCESS EXCLUSIVE MODE"
STATUSES = "'received', 'clean', 'rejected', 'needs_reader', 'ready', 'failed'"  # 0001's


def _status_check(statuses: str) -> str:
    return (
        " DROP CONSTRAINT documents_status_check,"
        f" ADD CONSTRAINT documents_status_check CHECK (status IN ({statuses}))"
    )


CREATE_TABLES = (
    """
    CREATE TABLE department_members (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        department_id uuid NOT NULL,
        user_id uuid NOT NULL,
        is_head boolean NOT NULL DEFAULT false,
        added_by uuid NOT NULL,
        added_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, department_id, user_id),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id),
        FOREIGN KEY (customer_id, added_by) REFERENCES users (customer_id, id)
    )
    """,
    """
    CREATE TABLE private_folder_members (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        folder_id uuid NOT NULL,
        user_id uuid NOT NULL,
        added_by uuid NOT NULL,
        added_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, folder_id, user_id),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id),
        FOREIGN KEY (customer_id, added_by) REFERENCES users (customer_id, id)
    )
    """,
    """
    CREATE TABLE document_shares (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        document_id uuid NOT NULL,
        user_id uuid NOT NULL,
        given_by uuid NULL,
        given_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, document_id, user_id),
        FOREIGN KEY (customer_id, document_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id),
        FOREIGN KEY (customer_id, given_by) REFERENCES users (customer_id, id)
    )
    """,
    """
    CREATE TABLE lock_grants (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        document_id uuid NOT NULL,
        user_id uuid NOT NULL,
        given_by uuid NOT NULL,
        given_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, document_id, user_id),
        FOREIGN KEY (customer_id, document_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id),
        FOREIGN KEY (customer_id, given_by) REFERENCES users (customer_id, id)
    )
    """,
)

_ACCESS_ROWS = "".join(
    f"\n    DELETE FROM public.{table} x WHERE x.customer_id = p_customer;" for table in TABLES
)
# 0068's copy of a deleted organisation's audit rows, with the new column.
_KEEP = """
    INSERT INTO public.audit_log_kept (id, customer_id, actor_user_id, action, target_type, target_id,
                                       details, created_at, folder_id, family_id, subject_user_id,
                                       deleted_at)
    SELECT a.id, a.customer_id, a.actor_user_id, a.action, a.target_type, a.target_id, a.details,
           a.created_at, a.folder_id, a.family_id, a.subject_user_id, now()
    FROM public.audit_log a WHERE a.customer_id = p_customer;"""

_FOLDER_STORED = {
    "department_id": "The department this folder is in: the id of its top-level folder of kind department"
    " (its own for a department), NULL in none. A folder id with no key: it lasts through Trash (PRD 7).",
    "private_folder_id": "The private folder this folder is in (its own id when it is private), NULL in none."
    " A folder id with no key: it lasts through Trash (PRD 7).",
}
COMMENTS = {
    "users.management": "Management (PRD 7): sees every file of the organisation but locked files not given"
    " to them. Default false.",
    "customers.access_version": "Goes up by one on every change that can alter who sees what, so a kept"
    " answer keyed on it is made again (PRD 7). Starts at 1.",
    "customers.department_pointer": "Ask may name the department that holds what a person cannot see"
    " (PRD 7, the pointer). Default false.",
    "folders.department_only": "Department only (PRD 7): only its members, its head, Management and people"
    " given a file see its files. Only on a department; default false.",
    "folders.private": "A private folder (PRD 7): only the people named on it, its department's head and"
    " Management see it and everything inside it. Default false.",
    **{f"folders.{column}": comment for column, comment in _FOLDER_STORED.items()},
    "trashed_folders.department_only": "Its Department only switch as folders.department_only had it.",
    "trashed_folders.private": "Its Private switch as folders.private had it.",
    "trashed_folders.department_id": "Its department as folders.department_id had it, kept in Trash.",
    "trashed_folders.private_folder_id": "Its private folder as folders.private_folder_id had it, kept in Trash.",
    "documents.department_id": "The department of this version's folder (folders.department_id), NULL in"
    " none; kept while it is in Trash. A folder id with no key (PRD 7).",
    "documents.private_folder_id": "The private folder of this version's folder"
    " (folders.private_folder_id), NULL in none; kept while it is in Trash. A folder id with no key.",
    "documents.held_for": "users.id (same customer) of the uploader a file in no folder is held for until it"
    " is filed (PRD 7); NULL, held for no one.",
    "documents.locked_at": "When this version was locked (PRD 7); NULL, not locked. Set with locked_by.",
    "documents.locked_by": "users.id (same customer) who locked this version; NULL, not locked.",
    "documents.status": "received, clean (scanned, being read), rejected (failed the scan, final),"
    " needs_reader, ready (searchable), failed, or locked (PRD 7: never read, searched or counted).",
    "audit_log.subject_user_id": "users.id of the person the entry is about (given access, for example),"
    " NULL for none. No key, as actor_user_id.",
    "audit_log_kept.subject_user_id": "The person the entry is about, as in audit_log.",
    "learned_corrections.source_document_id": "documents.id (same customer) of the version the correction"
    " was made on; NULL for one made before PRD 7 or whose document was deleted for good.",
    "department_members.customer_id": "The organisation the membership belongs to.",
    "department_members.department_id": "The department (a top-level folder of kind department, live or in"
    " Trash). A folder id with no key: it lasts through Trash.",
    "department_members.user_id": "users.id (same customer) of the member.",
    "department_members.is_head": "The member heads the department: sees all of it, private folders"
    " included, and names the people on them.",
    "department_members.added_by": "users.id (same customer) who added the member.",
    "department_members.added_at": "When the member was added.",
    "private_folder_members.customer_id": "The organisation the name on the list belongs to.",
    "private_folder_members.folder_id": "The private folder (live or in Trash). A folder id with no key: it"
    " lasts through Trash.",
    "private_folder_members.user_id": "users.id (same customer) of the person named on it.",
    "private_folder_members.added_by": "users.id (same customer) who named them.",
    "private_folder_members.added_at": "When they were named.",
    "document_shares.customer_id": "The organisation the file given belongs to.",
    "document_shares.document_id": "documents.id (same customer) of the version given; each version has its"
    " own row, and goes with its version.",
    "document_shares.user_id": "users.id (same customer) of the person it was given to.",
    "document_shares.given_by": "users.id (same customer) who gave it; NULL when Kaagaz did (auto-filing).",
    "document_shares.given_at": "When it was given.",
    "lock_grants.customer_id": "The organisation the locked file belongs to.",
    "lock_grants.document_id": "documents.id (same customer) of the version the person may open while it is"
    " locked; each version has its own row, and goes with its version.",
    "lock_grants.user_id": "users.id (same customer) of the person who may open it.",
    "lock_grants.given_by": "users.id (same customer) who allowed it.",
    "lock_grants.given_at": "When it was allowed.",
}
TABLE_COMMENTS = {
    "department_members": "A person's membership of a department, with the Head tick (PRD 7).",
    "private_folder_members": "A person named on a private folder (PRD 7).",
    "document_shares": "A file version given to one person directly (PRD 7).",
    "lock_grants": "A person who may open a locked file version (PRD 7).",
}
OLD_STATUS_COMMENT = (  # 0007's
    "received, clean (scanned, being read), rejected (failed the scan, final), needs_reader, ready"
    " (searchable) or failed."
)

UPGRADE = [
    LOCK,
    "ALTER TABLE documents ADD COLUMN department_id uuid NULL, ADD COLUMN private_folder_id uuid NULL,"
    " ADD COLUMN held_for uuid NULL, ADD COLUMN locked_at timestamptz NULL, ADD COLUMN locked_by uuid NULL,"
    " ADD CONSTRAINT documents_held_for_fk FOREIGN KEY (customer_id, held_for) REFERENCES users (customer_id, id),"
    " ADD CONSTRAINT documents_locked_by_fk FOREIGN KEY (customer_id, locked_by) REFERENCES users (customer_id, id),"
    " ADD CONSTRAINT documents_locked CHECK ((locked_at IS NULL) = (locked_by IS NULL)),"
    + _status_check(STATUSES + ", 'locked'"),
    "ALTER TABLE learned_corrections ADD COLUMN source_document_id uuid NULL,"
    " ADD CONSTRAINT learned_corrections_source_fk FOREIGN KEY (customer_id, source_document_id)"
    " REFERENCES documents (customer_id, id) ON DELETE SET NULL (source_document_id)",
    # A document's delete forever finds its corrections on this, not by reading them all.
    "CREATE INDEX learned_corrections_source ON learned_corrections (customer_id, source_document_id)"
    " WHERE source_document_id IS NOT NULL",
    *CREATE_TABLES,
    # A person's own rows (their access, their Access page): the key serves the other way.
    *[f"CREATE INDEX {table}_user ON {table} (customer_id, user_id)" for table in TABLES],
    "ALTER TABLE folders ADD COLUMN department_only boolean NOT NULL DEFAULT false,"
    " ADD COLUMN private boolean NOT NULL DEFAULT false,"
    " ADD COLUMN department_id uuid NULL, ADD COLUMN private_folder_id uuid NULL,"
    " ADD CONSTRAINT folders_department_only CHECK (kind = 'department' OR NOT department_only)",
    "ALTER TABLE trashed_folders ADD COLUMN department_only boolean NOT NULL DEFAULT false,"
    " ADD COLUMN private boolean NOT NULL DEFAULT false,"
    " ADD COLUMN department_id uuid NULL, ADD COLUMN private_folder_id uuid NULL",
    "ALTER TABLE audit_log_kept ADD COLUMN subject_user_id uuid NULL",
    # The busy tables last, catalog only: held ACCESS EXCLUSIVE for the end of the migration alone.
    "ALTER TABLE customers ADD COLUMN access_version bigint NOT NULL DEFAULT 1 CHECK (access_version >= 1),"
    " ADD COLUMN department_pointer boolean NOT NULL DEFAULT false",
    "ALTER TABLE users ADD COLUMN management boolean NOT NULL DEFAULT false",
    "ALTER TABLE audit_log ADD COLUMN subject_user_id uuid NULL",
    *[f"COMMENT ON TABLE {table} IS {_literal(comment)}" for table, comment in TABLE_COMMENTS.items()],
    *[f"COMMENT ON COLUMN {column} IS {_literal(comment)}" for column, comment in COMMENTS.items()],
    *[
        statement
        for table in TABLES
        for statement in (
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"CREATE POLICY customer_only ON {table} USING ({POLICY}) WITH CHECK ({POLICY})",
            f"COMMENT ON POLICY customer_only ON {table} IS 'A row is seen and written only by its own customer.'",
            *[f"GRANT SELECT, INSERT, DELETE ON {table} TO {role}" for role in ROLES],
        )
    ],
    *[f"GRANT UPDATE (is_head) ON department_members TO {role}" for role in ROLES],
    _0047._FINISH.format(words=_0047._WORDS + _ACCESS_ROWS + _KEEP),
]

DOWNGRADE = [
    LOCK,
    _0047._FINISH.format(words=_0047._WORDS + _0068._KEEP),  # 0068's
    *[f"DROP TABLE IF EXISTS {table}" for table in reversed(TABLES)],
    "DROP INDEX IF EXISTS learned_corrections_source",
    "ALTER TABLE learned_corrections DROP COLUMN IF EXISTS source_document_id",
    "ALTER TABLE audit_log_kept DROP COLUMN IF EXISTS subject_user_id",
    "ALTER TABLE audit_log DROP COLUMN IF EXISTS subject_user_id",
    "ALTER TABLE documents" + _status_check(STATUSES),
    f"COMMENT ON COLUMN documents.status IS {_literal(OLD_STATUS_COMMENT)}",
    "ALTER TABLE documents DROP COLUMN IF EXISTS department_id, DROP COLUMN IF EXISTS private_folder_id,"
    " DROP COLUMN IF EXISTS held_for, DROP COLUMN IF EXISTS locked_at, DROP COLUMN IF EXISTS locked_by",
    "ALTER TABLE trashed_folders DROP COLUMN IF EXISTS department_only, DROP COLUMN IF EXISTS private,"
    " DROP COLUMN IF EXISTS department_id, DROP COLUMN IF EXISTS private_folder_id",
    "ALTER TABLE folders DROP COLUMN IF EXISTS department_only, DROP COLUMN IF EXISTS private,"
    " DROP COLUMN IF EXISTS department_id, DROP COLUMN IF EXISTS private_folder_id",
    "ALTER TABLE users DROP COLUMN IF EXISTS management",
    "ALTER TABLE customers DROP COLUMN IF EXISTS access_version, DROP COLUMN IF EXISTS department_pointer",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
