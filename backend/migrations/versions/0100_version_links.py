"""Linking files already uploaded as versions of each other, and its Undo (V13 to V16, V18 to V21,
#648, V-A; Tushar, 16 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema.

0100 follows V-B's version finder (0099); it followed V-B's branch number 0098 until merge.

* ``version_links``: one row per link (``POST /documents/link-versions``), keyed on the family the
  files were linked into, with every version it left in that family (``document_ids``) and, per
  version, what Undo puts back (``snapshot``: its family, number, current or not, folder, whom it
  was held for, owner, label, tags, group decision, whom it was given to and whose stories the
  link carried), and the folders it left (``folder_ids``: Kaagaz's empty folders among them are not
  pruned while the link is not undone, ``kaagaz.db.folders``). Ids and flags only, never content.
  Undone once (``undone_at``).
* ``version_suggestions.status`` gains ``separated``: the pair kept apart by "Make it a separate
  file" (#521), the only pair its Undo may join again (V18: the join-again back door is closed to a
  pair a person kept separate). Rows from before stay ``rejected`` (no way to tell them apart), so
  their Undo of a separate file, a toast of the moment, ends with this release.
* ``kaagaz_version_joined`` (V19, the trigger fix): a version that joins a family by an UPDATE of
  ``family_id`` (Accept, link) takes the family's lock and the people its files are given and lock
  granted to, as a version inserted into the family does (0085's ``kaagaz_version_access`` and
  ``kaagaz_document_access`` act on INSERT only). Read from the family's first version, whose id is
  the family's, so a statement joining several versions never spreads one joiner's people to
  another. BEFORE the row is written, the lock (a locked family taken by a ready version then fails
  0093's CHECKs, so nothing is ever joined to a locked family); AFTER, the people, with the access
  version raised when any was added. A version leaving for a family of its own (Undo, a separate
  file) is not a join. Owner's rights, the house pattern; no role may run it, only its triggers.
* ``kaagaz_move_stories(from, to, users)``: the people's stories of a file (each person's own, so
  row level security hides them from the Admin who links) re-keyed from one family to another: a
  link carries each joining family's stories, and its Undo carries them back. A person with a story
  on both keeps the one already there; the other comes back with Undo. Only an Admin or Management
  in a transaction with a person; ``to`` must be a family of this organisation. Returns whose stories
  moved (ids only).

Cost: catalog only, but for ``version_suggestions``' CHECK, checked once against the table (ACCESS
EXCLUSIVE for that moment: a small table). The new triggers fire only for a change of family.

The downgrade turns ``separated`` back into ``rejected``, puts 0041's CHECK back, and drops the
triggers, the functions and the table; the links' snapshots are lost, so no link can be undone.

Revision ID: 0100
Revises: 0099 (the version finder)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0100", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0093 = _load("0093_locked_files.py")
_literal = _0093._literal
ROLES = ("kaagaz_app", "kaagaz_worker")
POLICY = "customer_id = kaagaz_current_customer()"
STATUSES = "'pending', 'accepted', 'rejected', 'undone'"  # 0041's
STATUS_CHECK = "version_suggestions_status_check"

LINKS = """
    CREATE TABLE version_links (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        family_id uuid NOT NULL,
        document_ids uuid[] NOT NULL CONSTRAINT version_links_documents CHECK (cardinality(document_ids) >= 2),
        folder_ids uuid[] NOT NULL DEFAULT '{}',
        snapshot jsonb NOT NULL,
        linked_by uuid NOT NULL,
        linked_at timestamptz NOT NULL DEFAULT now(),
        undone_by uuid NULL,
        undone_at timestamptz NULL,
        CONSTRAINT version_links_undone CHECK ((undone_by IS NULL) = (undone_at IS NULL)),
        FOREIGN KEY (customer_id, family_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, linked_by) REFERENCES users (customer_id, id),
        FOREIGN KEY (customer_id, undone_by) REFERENCES users (customer_id, id)
    )
    """
COMMENTS = {
    "id": "The link's id.",
    "customer_id": "The organisation.",
    "family_id": "documents.id (same customer) of the family the files were linked into: its first version.",
    "document_ids": "Every version the link left in the family, in their new order. Undo only while the"
    " family holds exactly these.",
    "folder_ids": "folders.id (no foreign key) of the folders the link moved files out of: Kaagaz's empty"
    " folders are never pruned while the link is not undone, so its Undo has them to go back to.",
    "snapshot": "Per version id, what Undo puts back: family, number, current, folder, held for, owner,"
    " label, tags, group decision, and the people the link gave it to and whose stories it carried. Ids"
    " only.",
    "linked_by": "users.id (same customer) of the Admin or Management who linked them.",
    "linked_at": "When they were linked.",
    "undone_by": "users.id (same customer) of who undid the link. NULL: not undone.",
    "undone_at": "When the link was undone. NULL: not undone.",
}
TABLE_COMMENT = (
    "Files already uploaded linked as versions of each other by an Admin or Management (V13, #648), and"
    " what Undo puts back."
)
GRANT = "SELECT, INSERT, UPDATE (undone_by, undone_at)"
STATUS_COMMENT = (
    "pending, accepted, rejected (kept separate), undone, or separated (made a separate file; only this"
    " pair's Undo may join it again)."
)
OLD_STATUS_COMMENT = "pending, accepted, rejected or undone."  # 0059's

JOINED = """
    CREATE FUNCTION public.kaagaz_version_joined() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_locked_at timestamptz;
        v_locked_by uuid;
        v_added integer;
        v_granted integer;
    BEGIN
        IF TG_WHEN = 'BEFORE' THEN
            SELECT f.locked_at, f.locked_by INTO v_locked_at, v_locked_by
            FROM public.documents f WHERE f.customer_id = NEW.customer_id AND f.id = NEW.family_id;
            IF v_locked_at IS NOT NULL THEN
                NEW.locked_at := v_locked_at;
                NEW.locked_by := v_locked_by;
            END IF;
            RETURN NEW;
        END IF;
        INSERT INTO public.document_shares (customer_id, document_id, user_id, given_by, given_at)
        SELECT s.customer_id, NEW.id, s.user_id, s.given_by, s.given_at FROM public.document_shares s
        WHERE s.customer_id = NEW.customer_id AND s.document_id = NEW.family_id
        ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS v_added = ROW_COUNT;
        INSERT INTO public.lock_grants (customer_id, document_id, user_id, given_by, given_at)
        SELECT g.customer_id, NEW.id, g.user_id, g.given_by, g.given_at FROM public.lock_grants g
        WHERE g.customer_id = NEW.customer_id AND g.document_id = NEW.family_id
        ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS v_granted = ROW_COUNT;
        IF v_added + v_granted > 0 THEN
            UPDATE public.customers c SET access_version = c.access_version + 1 WHERE c.id = NEW.customer_id;
        END IF;
        RETURN NULL;
    END
    $$
    """
MOVE_STORIES = """
    CREATE FUNCTION public.kaagaz_move_stories(p_from uuid, p_to uuid, p_users uuid[]) RETURNS SETOF uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_me public.kaagaz_access_set := public.kaagaz_access();
    BEGIN
        IF v_me.person IS NULL OR NOT (v_me.admin OR v_me.management) THEN
            RAISE EXCEPTION 'only an Admin or Management' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.documents d WHERE d.customer_id = v_customer AND d.id = p_to
                       AND d.family_id = p_to) THEN
            RAISE EXCEPTION 'not a family of this organisation' USING ERRCODE = 'check_violation';
        END IF;
        RETURN QUERY
        UPDATE public.document_stories s SET family_id = p_to
        WHERE s.customer_id = v_customer AND s.family_id = p_from
        AND (p_users IS NULL OR s.user_id = ANY (p_users))
        AND NOT EXISTS (SELECT 1 FROM public.document_stories t WHERE t.customer_id = v_customer
                        AND t.user_id = s.user_id AND t.family_id = p_to)
        RETURNING s.user_id;
    END
    $$
    """
FUNCTIONS = {"kaagaz_version_joined()": JOINED, "kaagaz_move_stories(uuid, uuid, uuid[])": MOVE_STORIES}
FUNCTION_COMMENTS = {
    "kaagaz_version_joined()": "Trigger (PRD 7, #648): a version joining a family by a change of family"
    " takes the family's lock, and the people its first version is given and lock granted to.",
    "kaagaz_move_stories(uuid, uuid, uuid[])": "The people's stories of one family re-keyed to another"
    " (a link and its Undo, #648), but where the person has one there already: whose moved. Admins and"
    " Management.",
}
# A change of family into a family that isn't the version's own: a join.
_JOINS = "WHEN (OLD.family_id IS DISTINCT FROM NEW.family_id AND NEW.family_id <> NEW.id)"
TRIGGERS = {
    "documents_version_joined": f"BEFORE UPDATE OF family_id ON documents FOR EACH ROW {_JOINS}"
    " EXECUTE FUNCTION kaagaz_version_joined()",
    "documents_version_joined_people": f"AFTER UPDATE OF family_id ON documents FOR EACH ROW {_JOINS}"
    " EXECUTE FUNCTION kaagaz_version_joined()",
}

UPGRADE = [
    LINKS,
    f"COMMENT ON TABLE version_links IS {_literal(TABLE_COMMENT)}",
    *[f"COMMENT ON COLUMN version_links.{column} IS {_literal(text)}" for column, text in COMMENTS.items()],
    "ALTER TABLE version_links ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY customer_only ON version_links USING ({POLICY}) WITH CHECK ({POLICY})",
    "COMMENT ON POLICY customer_only ON version_links IS 'A row is seen and written only by its own customer.'",
    *[f"GRANT {GRANT} ON version_links TO {role}" for role in ROLES],
    # Undo's lookup: a family's newest link.
    "CREATE INDEX version_links_family ON version_links (customer_id, family_id, linked_at DESC)",
    f"ALTER TABLE version_suggestions DROP CONSTRAINT {STATUS_CHECK},"
    f" ADD CONSTRAINT {STATUS_CHECK} CHECK (status IN ({STATUSES}, 'separated'))",
    f"COMMENT ON COLUMN version_suggestions.status IS {_literal(STATUS_COMMENT)}",
    *FUNCTIONS.values(),
    *[
        statement
        for name, comment in FUNCTION_COMMENTS.items()
        for statement in (
            f"COMMENT ON FUNCTION public.{name} IS {_literal(comment)}",
            f"REVOKE ALL ON FUNCTION public.{name} FROM PUBLIC",
        )
    ],
    *[
        f"GRANT EXECUTE ON FUNCTION public.kaagaz_move_stories(uuid, uuid, uuid[]) TO {role}"
        for role in ROLES
    ],
    *[f"CREATE TRIGGER {name} {body}" for name, body in TRIGGERS.items()],
]
DOWNGRADE = [
    *[f"DROP TRIGGER IF EXISTS {name} ON documents" for name in TRIGGERS],
    *[f"DROP FUNCTION IF EXISTS public.{name}" for name in FUNCTIONS],
    "UPDATE version_suggestions SET status = 'rejected' WHERE status = 'separated'",
    f"ALTER TABLE version_suggestions DROP CONSTRAINT {STATUS_CHECK},"
    f" ADD CONSTRAINT {STATUS_CHECK} CHECK (status IN ({STATUSES}))",
    f"COMMENT ON COLUMN version_suggestions.status IS {_literal(OLD_STATUS_COMMENT)}",
    "DROP TABLE IF EXISTS version_links",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
