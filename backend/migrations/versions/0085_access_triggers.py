"""Who can see what: the triggers that keep the stored values right, and the access version (PRD 7
section 10, migration (b), #593, Tushar, 14 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema.

Every trigger function runs with the owner's rights (so a person's rule, PR 9, never hides a row
from it) under the house pattern: an empty search path, every table named in full, no right for
PUBLIC and none for any role. Only their triggers run them; firing one needs no right. Each walks
or writes one organisation's rows, keyed on the customer of the rows the statement changed.

* Folders (``kaagaz_folder_access``, after each INSERT and UPDATE statement): each folder created
  (a restore's too, wherever it lands) or whose parent, kind or Private changed, and every folder
  under it, gets its department (its top-level folder's id when that is a department) and private
  folder (the nearest private one, itself included) from its parent's stored values, in one walk
  down the changed subtrees; then, in a second statement, the live files of every folder whose
  values changed. Only rows whose values differ are written.
* Files (``kaagaz_document_access``, before each row's INSERT, and its UPDATE of folder_id or
  deleted_at that leaves it live): a live file takes its folder's values (none in no folder), and a
  file with a folder is held for no one. It reads the folder FOR SHARE, so a folder move waits for
  it, or it for the move, and the move's second statement then sees it. A file in Trash keeps its
  values, even when its folder link is emptied; a restore works them out again.
* New versions: a version inserted into a family takes the current version's lock and hold
  (``kaagaz_document_access``) and its files given and lock grants (``kaagaz_version_access``).
  Joining, undoing and separating move no access row: access is kept on each version (PRD 7
  section 4), and a join's folder change is a move like any other.
* A file given or a lock grant removed goes from every version of its family in the same
  statement (``kaagaz_access_removed``, decision 14).
* ``customers.access_version`` goes up by one per statement, for each organisation it changed
  (``kaagaz_access_changed``, and the two above): a membership, a person named on a private
  folder, a file given or a lock grant added or removed, a Head tick, Management, a folder's
  Department only, Private or a move that changes stored values, and a file's stored values, hold
  or lock. The app still may update only its six named organisation columns.
* Writes made by these triggers fire none of them again (``pg_trigger_depth``), and never move
  ``updated_at``: ``documents_touch`` and ``learned_corrections_touch`` now fire only for a
  statement's own rows, so a correction's key cleared by a document's delete forever (0084's SET
  NULL) does not count as a change either.

Locks. Raising the access version takes the organisation's ``customers`` row, which is the app's
tree lock (``kaagaz.db.folders._lock_tree``). So every path that can change a file's stored values
takes the tree lock before the family lock, as auto-filing and every folder change already do: a
person's move and Undo of auto-filing, the zip's move into its folder, a family's restore, and a
version's link and Undo. ``kaagaz_folder_access`` also takes that row itself before it walks, so
two folder changes that skipped the app's lock run one after the other, the second walking from
what the first committed.

Open until the app changes PR 9 needs (#594): a file restored after its folder was deleted, a held
file joined to a family and then undone, and the files of a top-level folder deleted with its
contents kept (``folders._MOVE_DOCUMENTS``) all end in no folder, held for no one: open to
everyone, as today.

The upgrade first, outside the transaction (as 0068; it commits what ran before it), builds
``audit_log_subject`` (the entries about one person, on 0084's column) CONCURRENTLY unless a valid
one is there, then validates 0084's NOT VALID keys and CHECKs (``VALIDATED``: SHARE UPDATE
EXCLUSIVE, so writes go on while documents is read). Then it sets env.py's lock_timeout again and,
with documents, folders and trashed_folders locked against writes (documents first, 0004's order),
works out every stored value again from the tree over ``folders`` and ``trashed_folders``: a live
row's always (none when no top-level folder reaches it), a row in Trash only where its tree still
reaches it, as 0084 would have; it stops if a folder is deeper than 100
(kaagaz.db.folders.MAX_DEPTH). Only rows whose values differ are written, with the touch trigger
off. Cost: the folder tree is walked five times (the depth check and one per UPDATE); documents
are read twice (live, then in Trash). Every other table is locked only by its CREATE TRIGGER at
the end.

On every statement the triggers add one small query on what it changed; a folder change walks its
subtree. A document move or a change of access also holds the organisation's row until the commit.

The downgrade (documents locked first) drops the triggers, the functions and the index, and puts
back 0001's touch triggers; the stored values stay as they are, and so do 0084's keys and CHECKs,
now validated.

Revision ID: 0085
Revises: 0084
"""

from alembic import op
from sqlalchemy import text

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None

INDEX = "audit_log_subject"
LOCK_TIMEOUT = "10s"  # as env.py's
# What the backfill reads and writes, documents first: writes wait, reads go on.
LOCK = "LOCK TABLE documents, folders, trashed_folders IN SHARE ROW EXCLUSIVE MODE"
# 0084's, added NOT VALID: {constraint: its table}.
VALIDATED = {
    "documents_held_for_fk": "documents",
    "documents_locked_by_fk": "documents",
    "documents_locked": "documents",
    "documents_status_check": "documents",
    "learned_corrections_source_fk": "learned_corrections",
}
MEMBER_TABLES = ("department_members", "private_folder_members")
GIVEN_TABLES = ("document_shares", "lock_grants")


def _literal(words: str) -> str:
    return "'" + words.replace("'", "''") + "'"


def _raise(customers: str) -> str:
    """Up by one for each organisation in the uuid[] ``customers``; no statement when it is empty."""
    return (
        f"IF cardinality({customers}) > 0 THEN UPDATE public.customers c"
        f" SET access_version = c.access_version + 1 WHERE c.id = ANY ({customers}); END IF;"
    )


# Every folder, live or in Trash, from each top-level folder down: its department and private
# folder. A trashed folder's parent is live, trashed or gone (then it is not reached).
_TREE = (
    "WITH RECURSIVE node (customer_id, id, parent_id, kind, private) AS ("
    " SELECT customer_id, id, parent_id, kind, private FROM folders"
    " UNION ALL SELECT customer_id, id, parent_id, kind, private FROM trashed_folders),"
    " tree (customer_id, id, department_id, private_folder_id, depth) AS ("
    " SELECT customer_id, id, CASE WHEN kind = 'department' THEN id END, CASE WHEN private THEN id END, 1"
    " FROM node WHERE parent_id IS NULL"
    " UNION ALL"
    " SELECT n.customer_id, n.id, t.department_id, CASE WHEN n.private THEN n.id ELSE t.private_folder_id END,"
    " t.depth + 1 FROM tree t JOIN node n ON n.customer_id = t.customer_id AND n.parent_id = t.id"
    " WHERE t.depth < 101)"
)
_DIFFERENT = (
    "({row}.department_id, {row}.private_folder_id) IS DISTINCT FROM (t.department_id, t.private_folder_id)"
)
_SET = "department_id = t.department_id, private_folder_id = t.private_folder_id"
BACKFILL = [
    f"DO $$ BEGIN IF EXISTS ({_TREE} SELECT 1 FROM tree WHERE depth > 100) THEN"
    " RAISE EXCEPTION 'a folder is deeper than 100 levels: its stored values cannot be worked out';"
    " END IF; END $$",
    f"{_TREE} UPDATE folders f SET {_SET} FROM folders l LEFT JOIN tree t"
    " ON t.customer_id = l.customer_id AND t.id = l.id"
    f" WHERE f.customer_id = l.customer_id AND f.id = l.id AND {_DIFFERENT.format(row='f')}",
    f"{_TREE} UPDATE trashed_folders f SET {_SET} FROM tree t"
    f" WHERE f.customer_id = t.customer_id AND f.id = t.id AND {_DIFFERENT.format(row='f')}",
    "ALTER TABLE documents DISABLE TRIGGER documents_touch",
    f"{_TREE} UPDATE documents d SET {_SET} FROM documents l LEFT JOIN tree t"
    " ON t.customer_id = l.customer_id AND t.id = l.folder_id"
    f" WHERE d.id = l.id AND l.deleted_at IS NULL AND {_DIFFERENT.format(row='d')}",
    # A file trashed alone keeps its folder; one trashed with it, its trashed folder (0081).
    f"{_TREE} UPDATE documents d SET {_SET} FROM tree t WHERE d.deleted_at IS NOT NULL"
    " AND t.customer_id = d.customer_id AND t.id = coalesce(d.folder_id, d.trashed_folder_id)"
    f" AND {_DIFFERENT.format(row='d')}",
    "ALTER TABLE documents ENABLE TRIGGER documents_touch",
]

FUNCTIONS = {
    "kaagaz_folder_access": f"""
    CREATE FUNCTION public.kaagaz_folder_access() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_roots uuid[];
        v_switched uuid[];
        v_folders uuid[];
        v_customers uuid[];
    BEGIN
        IF pg_trigger_depth() > 1 THEN
            RETURN NULL;  -- the UPDATE below
        END IF;
        IF TG_OP = 'INSERT' THEN
            SELECT array_agg(n.id) INTO v_roots FROM new_rows n;
        ELSE
            SELECT array_agg(n.id) FILTER (WHERE (n.parent_id, n.kind, n.private)
                                           IS DISTINCT FROM (o.parent_id, o.kind, o.private)),
                   array_agg(DISTINCT n.customer_id) FILTER (WHERE n.department_only <> o.department_only)
            INTO v_roots, v_switched
            FROM new_rows n JOIN old_rows o ON o.customer_id = n.customer_id AND o.id = n.id;
        END IF;
        IF v_roots IS NULL AND v_switched IS NULL THEN
            RETURN NULL;  -- a rename or a new order
        END IF;
        -- The tree lock (kaagaz.db.folders._lock_tree), which the app takes first: a change that
        -- skipped it waits here for another, then walks from what that one committed.
        PERFORM 1 FROM public.customers c WHERE c.id IN (SELECT n.customer_id FROM new_rows n)
        FOR NO KEY UPDATE;
        IF v_roots IS NOT NULL THEN
            -- Down from each changed folder, starting from its parent's stored values; a folder
            -- under two changed ones takes the walk from the higher (its longer path).
            WITH RECURSIVE tree (customer_id, id, department_id, private_folder_id, depth) AS (
                SELECT f.customer_id, f.id,
                       CASE WHEN f.parent_id IS NULL AND f.kind = 'department' THEN f.id
                            ELSE p.department_id END,
                       CASE WHEN f.private THEN f.id ELSE p.private_folder_id END, 1
                FROM public.folders f
                LEFT JOIN public.folders p ON p.customer_id = f.customer_id AND p.id = f.parent_id
                WHERE f.id = ANY (v_roots)
                UNION ALL
                SELECT f.customer_id, f.id, t.department_id,
                       CASE WHEN f.private THEN f.id ELSE t.private_folder_id END, t.depth + 1
                FROM tree t JOIN public.folders f ON f.customer_id = t.customer_id AND f.parent_id = t.id
                WHERE t.depth < 101
            ),
            walked AS (SELECT DISTINCT ON (t.id) t.* FROM tree t ORDER BY t.id, t.depth DESC),
            moved AS (
                UPDATE public.folders f SET department_id = t.department_id, private_folder_id = t.private_folder_id
                FROM walked t WHERE f.customer_id = t.customer_id AND f.id = t.id
                AND {_DIFFERENT.format(row="f")}
                RETURNING f.customer_id, f.id
            )
            SELECT array_agg(m.id), array_agg(DISTINCT m.customer_id) INTO v_folders, v_customers FROM moved m;
            -- Its own statement, so it sees a file filed meanwhile by a transaction that held one
            -- of these folders (kaagaz_document_access) until the UPDATE above could take it.
            WITH filed AS (
                UPDATE public.documents d SET department_id = t.department_id, private_folder_id = t.private_folder_id
                FROM public.folders t
                WHERE t.id = ANY (v_folders) AND d.customer_id = t.customer_id AND d.folder_id = t.id
                AND d.deleted_at IS NULL AND {_DIFFERENT.format(row="d")}
                RETURNING d.customer_id
            )
            SELECT v_customers || array_agg(DISTINCT x.customer_id) INTO v_customers FROM filed x;
        END IF;
        IF TG_OP = 'UPDATE' THEN
            {_raise("v_customers || v_switched")}
        END IF;
        RETURN NULL;
    END
    $$
    """,
    "kaagaz_document_access": """
    CREATE FUNCTION public.kaagaz_document_access() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    BEGIN
        IF TG_OP = 'INSERT' AND NEW.family_id <> NEW.id THEN
            SELECT c.locked_at, c.locked_by, c.held_for INTO NEW.locked_at, NEW.locked_by, NEW.held_for
            FROM public.documents c
            WHERE c.customer_id = NEW.customer_id AND c.family_id = NEW.family_id AND c.is_current;
        END IF;
        IF NEW.deleted_at IS NULL THEN
            SELECT f.department_id, f.private_folder_id INTO NEW.department_id, NEW.private_folder_id
            FROM public.folders f WHERE f.customer_id = NEW.customer_id AND f.id = NEW.folder_id
            FOR SHARE;
            IF NEW.folder_id IS NOT NULL THEN
                NEW.held_for := NULL;
            END IF;
        END IF;
        RETURN NEW;
    END
    $$
    """,
    "kaagaz_version_access": """
    CREATE FUNCTION public.kaagaz_version_access() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    BEGIN
        INSERT INTO public.document_shares (customer_id, document_id, user_id, given_by, given_at)
        SELECT n.customer_id, n.id, s.user_id, s.given_by, s.given_at
        FROM new_rows n
        JOIN public.documents c ON c.customer_id = n.customer_id AND c.family_id = n.family_id
            AND c.is_current AND c.id <> n.id
        JOIN public.document_shares s ON s.customer_id = c.customer_id AND s.document_id = c.id
        WHERE n.family_id <> n.id;
        INSERT INTO public.lock_grants (customer_id, document_id, user_id, given_by, given_at)
        SELECT n.customer_id, n.id, g.user_id, g.given_by, g.given_at
        FROM new_rows n
        JOIN public.documents c ON c.customer_id = n.customer_id AND c.family_id = n.family_id
            AND c.is_current AND c.id <> n.id
        JOIN public.lock_grants g ON g.customer_id = c.customer_id AND g.document_id = c.id
        WHERE n.family_id <> n.id;
        RETURN NULL;
    END
    $$
    """,
    "kaagaz_access_removed": f"""
    CREATE FUNCTION public.kaagaz_access_removed() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    BEGIN
        IF pg_trigger_depth() > 1 THEN
            RETURN NULL;  -- the DELETE below, or a version deleted for good (its rows go alone)
        END IF;
        IF TG_TABLE_NAME = 'document_shares' THEN
            DELETE FROM public.document_shares x USING old_rows o, public.documents d, public.documents v
            WHERE d.customer_id = o.customer_id AND d.id = o.document_id
            AND v.customer_id = d.customer_id AND v.family_id = d.family_id
            AND x.customer_id = v.customer_id AND x.document_id = v.id AND x.user_id = o.user_id;
        ELSE
            DELETE FROM public.lock_grants x USING old_rows o, public.documents d, public.documents v
            WHERE d.customer_id = o.customer_id AND d.id = o.document_id
            AND v.customer_id = d.customer_id AND v.family_id = d.family_id
            AND x.customer_id = v.customer_id AND x.document_id = v.id AND x.user_id = o.user_id;
        END IF;
        {_raise("ARRAY(SELECT o.customer_id FROM old_rows o)")}
        RETURN NULL;
    END
    $$
    """,
    "kaagaz_access_changed": f"""
    CREATE FUNCTION public.kaagaz_access_changed() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customers uuid[];
    BEGIN
        IF pg_trigger_depth() > 1 THEN
            RETURN NULL;  -- written by another access trigger, which raises it itself
        END IF;
        IF TG_OP = 'INSERT' THEN
            SELECT array_agg(DISTINCT n.customer_id) INTO v_customers FROM new_rows n;
        ELSIF TG_OP = 'DELETE' THEN
            SELECT array_agg(DISTINCT o.customer_id) INTO v_customers FROM old_rows o;
        ELSIF TG_TABLE_NAME = 'documents' THEN
            SELECT array_agg(DISTINCT n.customer_id) INTO v_customers
            FROM new_rows n JOIN old_rows o ON o.id = n.id
            WHERE (n.department_id, n.private_folder_id, n.held_for, n.locked_at IS NULL)
                  IS DISTINCT FROM (o.department_id, o.private_folder_id, o.held_for, o.locked_at IS NULL);
        ELSIF TG_TABLE_NAME = 'users' THEN
            SELECT array_agg(DISTINCT n.customer_id) INTO v_customers
            FROM new_rows n JOIN old_rows o ON o.id = n.id WHERE n.management <> o.management;
        ELSE  -- department_members
            SELECT array_agg(DISTINCT n.customer_id) INTO v_customers
            FROM new_rows n JOIN old_rows o ON o.customer_id = n.customer_id
                AND o.department_id = n.department_id AND o.user_id = n.user_id
            WHERE n.is_head <> o.is_head;
        END IF;
        {_raise("v_customers")}
        RETURN NULL;
    END
    $$
    """,
}
FUNCTION_COMMENTS = {
    "kaagaz_folder_access": "Trigger (PRD 7): the stored department and private folder of each folder created or"
    " whose parent, kind or Private changed, of every folder under it and of their live files; raises the"
    " access version when an update changed any, or Department only.",
    "kaagaz_document_access": "Trigger (PRD 7): a live file's stored values from its folder (FOR SHARE), held"
    " for no one once in a folder; a new version takes the current one's lock and hold. A file in Trash"
    " keeps its values.",
    "kaagaz_version_access": "Trigger (PRD 7): a new version is given to the people, and lock granted to the"
    " people, its family's current version is.",
    "kaagaz_access_removed": "Trigger (PRD 7, decision 14): a file given or lock grant removed goes from every"
    " version of the family; raises the access version.",
    "kaagaz_access_changed": "Trigger (PRD 7): raises customers.access_version once per statement for each"
    " organisation whose access it changed.",
}

_TRANSITION = {
    "INSERT": "REFERENCING NEW TABLE AS new_rows",
    "DELETE": "REFERENCING OLD TABLE AS old_rows",
    "UPDATE": "REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows",
}


def _after(table: str, event: str, function: str) -> str:
    return (
        f"AFTER {event} ON {table} {_TRANSITION[event]} FOR EACH STATEMENT"
        f" EXECUTE FUNCTION kaagaz_{function}()"
    )


# {trigger: (its table, what follows CREATE TRIGGER and its name)}
TRIGGERS = {
    "folders_access_insert": ("folders", _after("folders", "INSERT", "folder_access")),
    "folders_access_update": ("folders", _after("folders", "UPDATE", "folder_access")),
    "documents_access_insert": (
        "documents",
        "BEFORE INSERT ON documents FOR EACH ROW EXECUTE FUNCTION kaagaz_document_access()",
    ),
    "documents_access_move": (
        "documents",
        "BEFORE UPDATE OF folder_id, deleted_at ON documents FOR EACH ROW WHEN (NEW.deleted_at IS NULL"
        " AND (OLD.deleted_at IS NOT NULL OR OLD.folder_id IS DISTINCT FROM NEW.folder_id))"
        " EXECUTE FUNCTION kaagaz_document_access()",
    ),
    "documents_access_version": ("documents", _after("documents", "INSERT", "version_access")),
    "documents_access_changed": ("documents", _after("documents", "UPDATE", "access_changed")),
    "users_access_changed": ("users", _after("users", "UPDATE", "access_changed")),
    "department_members_access_changed": (
        "department_members",
        _after("department_members", "UPDATE", "access_changed"),
    ),
    **{f"{t}_access_added": (t, _after(t, "INSERT", "access_changed")) for t in MEMBER_TABLES + GIVEN_TABLES},
    **{f"{t}_access_removed": (t, _after(t, "DELETE", "access_changed")) for t in MEMBER_TABLES},
    **{f"{t}_access_removed": (t, _after(t, "DELETE", "access_removed")) for t in GIVEN_TABLES},
}
# 0001's, firing now only for a statement's own rows (not for a trigger's or a key's writes).
TOUCH = ("documents", "learned_corrections")


def _touch(table: str, when: str) -> str:
    return (
        f"CREATE OR REPLACE TRIGGER {table}_touch BEFORE UPDATE ON {table} FOR EACH ROW{when}"
        " EXECUTE FUNCTION kaagaz_touch()"
    )


INDEX_COMMENT = "The entries about one person, newest first (PRD 7)."

UPGRADE = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",  # the autocommit block ended env.py's
    LOCK,
    f"COMMENT ON INDEX {INDEX} IS {_literal(INDEX_COMMENT)}",
    *BACKFILL,
    *FUNCTIONS.values(),
    *[
        statement
        for name, comment in FUNCTION_COMMENTS.items()
        for statement in (
            f"COMMENT ON FUNCTION public.{name}() IS {_literal(comment)}",
            f"REVOKE ALL ON FUNCTION public.{name}() FROM PUBLIC",
        )
    ],
    *[f"CREATE TRIGGER {name} {body}" for name, (_, body) in TRIGGERS.items()],
    *[_touch(table, " WHEN (pg_trigger_depth() < 1)") for table in TOUCH],
]

DOWNGRADE = [
    "LOCK TABLE documents IN ACCESS EXCLUSIVE MODE",  # first, 0004's order
    *[f"DROP TRIGGER IF EXISTS {name} ON {table}" for name, (table, _) in TRIGGERS.items()],
    *[f"DROP FUNCTION IF EXISTS public.{name}()" for name in FUNCTIONS],
    *[_touch(table, "") for table in TOUCH],
    f"DROP INDEX IF EXISTS public.{INDEX}",
]


_VALID_INDEX = text(f"SELECT i.indisvalid FROM pg_index i WHERE i.indexrelid = to_regclass('public.{INDEX}')")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        if not op.get_bind().execute(_VALID_INDEX).scalar():  # none yet, or a failed build's leftover
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{INDEX}")
            op.execute(
                f"CREATE INDEX CONCURRENTLY {INDEX} ON public.audit_log"
                " (customer_id, subject_user_id, created_at DESC) WHERE subject_user_id IS NOT NULL"
            )
        for constraint, table in VALIDATED.items():
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint}")
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
