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
* After PRD 7 PR 9 (0091, 0092; #606), where the app role neither sees nor writes a version the
  person can't see, owner-rights functions of the house pattern (an Admin or Management in a
  transaction with a person, checked inside; EXECUTE to the app and the worker role alike):

  * ``kaagaz_link_families(files, lock)``: every version of the files' families, each with whether
    the caller sees it (``visible_documents``), locked in family order when asked, so a link or its
    Undo refuses a family with a version hidden from the person instead of missing it.
  * ``kaagaz_link_versions(order, latest)``: the link's writes, after checking again under the
    locks that ``order`` is every version of its families once, of two families at least, none
    locked, in Trash, being read or hidden from the caller, the latest ready; the snapshot is made
    inside. The link's id.
  * ``kaagaz_link_undo(link)``: Undo's writes from the snapshot, after checking again under the
    family lock that the link is its family's newest not undone, the family holds exactly what it
    left, and no version is locked, in Trash, being read or hidden from the caller.

  **NEEDS VRUSHIT'S APPROVAL:** ``version_links`` carries no ``person_only`` policy (listed in
  ``test_isolation_person_rule.EXEMPT``): ids only, read by the API only after every version is
  checked whole, and read by ``folders._PRUNE``, which must keep a live link's folder whoever
  prunes (a person who can't see the link would otherwise prune it).

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
SHIFT = 1_000_000  # a version's number while a link or its Undo renumbers (unique within a family, 0002)
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
# PRD 7 PR 9 (#606) on dev-env: under the per-person rule the app role neither sees nor writes a version
# it can't see, so the link reads and locks whole families, and the link and its Undo write, through
# these owner-rights functions, each checking its caller and its files again inside.
_CALLER = """
        IF v_me.person IS NULL OR NOT (v_me.admin OR v_me.management) THEN
            RAISE EXCEPTION 'only an Admin or Management' USING ERRCODE = 'insufficient_privilege';
        END IF;"""
_THEIR_FAMILIES = (
    "SELECT x.family_id FROM public.documents x WHERE x.customer_id = v_customer AND x.id = ANY ({ids})"
)
# Every version of the families, locked in family order (each family's first version first, then by
# id: trash.lock_family's, kaagaz_lock's).
_LOCK_FAMILIES = (
    "PERFORM 1 FROM public.documents d WHERE d.customer_id = v_customer AND d.family_id IN ({families})"
    " ORDER BY d.family_id, (d.id <> d.family_id), d.id FOR NO KEY UPDATE;"
)
_SEEN = "EXISTS (SELECT 1 FROM public.visible_documents v WHERE v.customer_id = v_customer AND v.id = {id})"
# A version none of these may be: locked, in Trash, being read, or one the caller can't see.
_UNFIT = (
    "SELECT 1 FROM public.documents d WHERE d.customer_id = v_customer AND d.id = ANY ({ids})"
    " AND (d.locked_at IS NOT NULL OR d.deleted_at IS NOT NULL OR d.status IN ('received', 'clean')"
    " OR NOT " + _SEEN.format(id="d.id") + ")"
)
# The group members of these versions, in id order, never waited for (V-B's NOWAIT).
_MEMBERS_LOCK = (
    "PERFORM 1 FROM public.version_group_members g WHERE g.customer_id = v_customer"
    " AND g.document_id = ANY ({ids}) ORDER BY g.document_id FOR NO KEY UPDATE NOWAIT;"
)
_REFUSE = "RAISE EXCEPTION '{why}' USING ERRCODE = 'check_violation';"

LINK_FAMILIES = f"""
    CREATE FUNCTION public.kaagaz_link_families(p_documents uuid[], p_lock boolean)
    RETURNS TABLE (id uuid, family_id uuid, seen boolean)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_me public.kaagaz_access_set := public.kaagaz_access();
    BEGIN
        {_CALLER}
        IF p_lock THEN
            {_LOCK_FAMILIES.format(families=_THEIR_FAMILIES.format(ids="p_documents"))}
        END IF;
        RETURN QUERY SELECT d.id, d.family_id, {_SEEN.format(id="d.id")}
        FROM public.documents d
        WHERE d.customer_id = v_customer AND d.family_id IN ({_THEIR_FAMILIES.format(ids="p_documents")})
        ORDER BY d.family_id, d.version_no;
    END
    $$
    """

LINK = f"""
    CREATE FUNCTION public.kaagaz_link_versions(p_order uuid[], p_current uuid) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_me public.kaagaz_access_set := public.kaagaz_access();
        v_latest public.documents;
        v_joined uuid[];
        v_moving uuid[];
        v_family uuid;
        v_versions jsonb;
        v_families jsonb := '{{}}';
        v_link uuid;
    BEGIN
        {_CALLER}
        {_LOCK_FAMILIES.format(families=_THEIR_FAMILIES.format(ids="p_order"))}
        -- Under the locks: every version of their families given once, two families at least.
        IF cardinality(p_order) <> (SELECT count(DISTINCT o) FROM unnest(p_order) AS o)
           OR (SELECT count(*) FROM public.documents d WHERE d.customer_id = v_customer AND d.id = ANY (p_order))
              <> cardinality(p_order)
           OR (SELECT count(*) FROM public.documents d WHERE d.customer_id = v_customer
               AND d.family_id IN ({_THEIR_FAMILIES.format(ids="p_order")})) <> cardinality(p_order)
           OR (SELECT count(DISTINCT d.family_id) FROM public.documents d WHERE d.customer_id = v_customer
               AND d.id = ANY (p_order)) < 2 THEN
            {_REFUSE.format(why="the versions given are not their families")}
        END IF;
        IF EXISTS ({_UNFIT.format(ids="p_order")}) THEN
            {_REFUSE.format(why="a version is locked, in Trash, being read or not seen")}
        END IF;
        SELECT d.* INTO v_latest FROM public.documents d
        WHERE d.customer_id = v_customer AND d.id = p_current AND d.id = ANY (p_order) AND d.status = 'ready';
        IF NOT FOUND THEN
            {_REFUSE.format(why="the latest is not a ready version given")}
        END IF;
        v_family := v_latest.family_id;
        v_joined := ARRAY(SELECT d.id FROM public.documents d WHERE d.customer_id = v_customer
                          AND d.id = ANY (p_order) AND d.family_id <> v_family);
        v_moving := ARRAY(SELECT DISTINCT d.family_id FROM public.documents d WHERE d.customer_id = v_customer
                          AND d.id = ANY (v_joined) ORDER BY 1);
        -- What Undo puts back, version by version (kaagaz.db.version_links reads it too).
        SELECT jsonb_object_agg(d.id::text, jsonb_build_object(
            'family_id', d.family_id, 'version_no', d.version_no, 'is_current', d.is_current,
            'folder_id', d.folder_id, 'held_for', d.held_for, 'owner_id', d.owner_id,
            'sensitivity', d.sensitivity, 'sensitivity_set_by_person', d.sensitivity_set_by_person,
            'tags', to_jsonb(ARRAY(SELECT t.tag_id::text FROM public.document_tags t
                                   WHERE t.customer_id = v_customer AND t.document_id = d.id ORDER BY t.tag_id)),
            'shares', coalesce((SELECT jsonb_agg(jsonb_build_object('user', s.user_id, 'given_by', s.given_by,
                                                                    'given_at', s.given_at) ORDER BY s.user_id)
                                FROM public.document_shares s
                                WHERE s.customer_id = v_customer AND s.document_id = d.id), '[]'),
            'member', (SELECT jsonb_build_object('state', g.state, 'by', g.decided_by, 'at', g.decided_at)
                       FROM public.version_group_members g
                       WHERE g.customer_id = v_customer AND g.document_id = d.id)))
        INTO v_versions FROM public.documents d WHERE d.customer_id = v_customer AND d.id = ANY (p_order);
        UPDATE public.documents d SET is_current = false
        WHERE d.customer_id = v_customer AND d.id = ANY (p_order) AND d.is_current;
        UPDATE public.documents d SET version_no = {SHIFT} + n.no FROM unnest(p_order) WITH ORDINALITY AS n (id, no)
        WHERE d.customer_id = v_customer AND d.id = n.id;
        -- One family a statement: 0100's trigger gives each joining version the family's people.
        FOR i IN 1 .. coalesce(cardinality(v_moving), 0) LOOP
            UPDATE public.documents d SET family_id = v_family, folder_id = v_latest.folder_id,
                held_for = v_latest.held_for, owner_id = v_latest.owner_id, sensitivity = v_latest.sensitivity,
                sensitivity_set_by_person = v_latest.sensitivity_set_by_person, autofile = NULL
            WHERE d.customer_id = v_customer AND d.family_id = v_moving[i];
            v_families := v_families || jsonb_build_object(v_moving[i]::text, jsonb_build_object('stories',
                coalesce((SELECT jsonb_agg(m.u ORDER BY m.u)
                          FROM public.kaagaz_move_stories(v_moving[i], v_family, NULL::uuid[]) AS m (u)), '[]')));
        END LOOP;
        UPDATE public.documents d SET version_no = n.no FROM unnest(p_order) WITH ORDINALITY AS n (id, no)
        WHERE d.customer_id = v_customer AND d.id = n.id;
        UPDATE public.documents d SET is_current = true WHERE d.customer_id = v_customer AND d.id = p_current;
        DELETE FROM public.document_tags t WHERE t.customer_id = v_customer AND t.document_id = ANY (v_joined);
        INSERT INTO public.document_tags (customer_id, document_id, tag_id)
        SELECT v_customer, j.id, t.tag_id FROM unnest(v_joined) AS j (id)
        JOIN public.document_tags t ON t.customer_id = v_customer AND t.document_id = p_current;
        {_MEMBERS_LOCK.format(ids="p_order")}
        UPDATE public.version_group_members g SET state = 'linked', decided_by = v_me.person, decided_at = now()
        WHERE g.customer_id = v_customer AND g.document_id = ANY (p_order) AND g.state <> 'linked';
        INSERT INTO public.version_links (customer_id, family_id, document_ids, folder_ids, snapshot, linked_by)
        VALUES (v_customer, v_family, p_order,
                ARRAY(SELECT DISTINCT (v_versions -> j.id::text ->> 'folder_id')::uuid FROM unnest(v_joined) AS j (id)
                      WHERE v_versions -> j.id::text ->> 'folder_id' IS NOT NULL ORDER BY 1),
                jsonb_build_object('versions', v_versions, 'families', v_families), v_me.person)
        RETURNING id INTO v_link;
        RETURN v_link;
    END
    $$
    """

UNDO = f"""
    CREATE FUNCTION public.kaagaz_link_undo(p_link uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_me public.kaagaz_access_set := public.kaagaz_access();
        v_link public.version_links;
        v_back jsonb;
        v_moving uuid[];
        v_part uuid[];
        v_moved uuid[];
    BEGIN
        {_CALLER}
        SELECT l.* INTO v_link FROM public.version_links l
        WHERE l.customer_id = v_customer AND l.id = p_link AND l.undone_at IS NULL;
        IF NOT FOUND OR EXISTS (SELECT 1 FROM public.version_links n WHERE n.customer_id = v_customer
                               AND n.family_id = v_link.family_id AND n.undone_at IS NULL
                               AND n.linked_at > v_link.linked_at) THEN
            {_REFUSE.format(why="not the newest link of its family")}
        END IF;
        {_LOCK_FAMILIES.format(families="v_link.family_id")}
        -- Under the lock: the family holds exactly what the link left, every version fit and seen.
        IF (SELECT array_agg(d.id ORDER BY d.id) FROM public.documents d
            WHERE d.customer_id = v_customer AND d.family_id = v_link.family_id)
           IS DISTINCT FROM (SELECT array_agg(x ORDER BY x) FROM unnest(v_link.document_ids) AS x) THEN
            {_REFUSE.format(why="the family changed since the link")}
        END IF;
        IF EXISTS ({_UNFIT.format(ids="v_link.document_ids")}) THEN
            {_REFUSE.format(why="a version is locked, in Trash, being read or not seen")}
        END IF;
        v_back := v_link.snapshot -> 'versions';
        v_moving := ARRAY(SELECT k::uuid FROM jsonb_object_keys(v_link.snapshot -> 'families') AS k ORDER BY 1);
        v_moved := ARRAY(SELECT x FROM unnest(v_link.document_ids) AS x
                         WHERE (v_back -> x::text ->> 'family_id')::uuid <> v_link.family_id);
        UPDATE public.documents d SET is_current = false
        WHERE d.customer_id = v_customer AND d.id = ANY (v_link.document_ids) AND d.is_current;
        UPDATE public.documents d SET version_no = {SHIFT} + n.no
        FROM unnest(v_link.document_ids) WITH ORDINALITY AS n (id, no)
        WHERE d.customer_id = v_customer AND d.id = n.id;
        FOR i IN 1 .. coalesce(cardinality(v_moving), 0) LOOP
            v_part := ARRAY(SELECT x FROM unnest(v_link.document_ids) AS x
                            WHERE (v_back -> x::text ->> 'family_id')::uuid = v_moving[i]);
            UPDATE public.documents d SET family_id = v_moving[i],
                folder_id = (v_back -> d.id::text ->> 'folder_id')::uuid,
                held_for = (v_back -> d.id::text ->> 'held_for')::uuid,
                owner_id = (v_back -> d.id::text ->> 'owner_id')::uuid,
                sensitivity = v_back -> d.id::text ->> 'sensitivity',
                sensitivity_set_by_person = (v_back -> d.id::text ->> 'sensitivity_set_by_person')::boolean,
                autofile = NULL
            WHERE d.customer_id = v_customer AND d.id = ANY (v_part);
            -- Each version given to exactly whom it was (a gift taken goes from the whole family, 0085),
            -- but to a person switched off since (0090 took their gifts then).
            DELETE FROM public.document_shares x WHERE x.customer_id = v_customer AND x.document_id = ANY (v_part)
            AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_back -> x.document_id::text -> 'shares') AS s
                            WHERE (s ->> 'user')::uuid = x.user_id);
            INSERT INTO public.document_shares (customer_id, document_id, user_id, given_by, given_at)
            SELECT v_customer, p.id, (s ->> 'user')::uuid, (s ->> 'given_by')::uuid, (s ->> 'given_at')::timestamptz
            FROM unnest(v_part) AS p (id) CROSS JOIN LATERAL jsonb_array_elements(v_back -> p.id::text -> 'shares') AS s
            JOIN public.users u ON u.customer_id = v_customer AND u.id = (s ->> 'user')::uuid AND NOT u.disabled
            ON CONFLICT DO NOTHING;
            IF jsonb_array_length(v_link.snapshot -> 'families' -> v_moving[i]::text -> 'stories') > 0 THEN
                PERFORM public.kaagaz_move_stories(v_link.family_id, v_moving[i], ARRAY(
                    SELECT u::uuid FROM jsonb_array_elements_text(
                        v_link.snapshot -> 'families' -> v_moving[i]::text -> 'stories') AS u));
            END IF;
        END LOOP;
        UPDATE public.documents d SET version_no = (v_back -> d.id::text ->> 'version_no')::integer
        WHERE d.customer_id = v_customer AND d.id = ANY (v_link.document_ids);
        UPDATE public.documents d SET is_current = true
        WHERE d.customer_id = v_customer AND d.id = ANY (v_link.document_ids)
        AND (v_back -> d.id::text ->> 'is_current')::boolean;
        -- The tags still there (one deleted since is gone from every file anyway).
        DELETE FROM public.document_tags t WHERE t.customer_id = v_customer AND t.document_id = ANY (v_moved);
        INSERT INTO public.document_tags (customer_id, document_id, tag_id)
        SELECT v_customer, p.id, g.id FROM unnest(v_moved) AS p (id)
        CROSS JOIN LATERAL jsonb_array_elements_text(v_back -> p.id::text -> 'tags') AS x (tag)
        JOIN public.tags g ON g.customer_id = v_customer AND g.id = x.tag::uuid;
        {_MEMBERS_LOCK.format(ids="v_link.document_ids")}
        UPDATE public.version_group_members g SET state = s.value -> 'member' ->> 'state',
            decided_by = (s.value -> 'member' ->> 'by')::uuid, decided_at = (s.value -> 'member' ->> 'at')::timestamptz
        FROM jsonb_each(v_back) AS s
        WHERE g.customer_id = v_customer AND g.document_id = s.key::uuid AND g.state = 'linked'
        AND jsonb_typeof(s.value -> 'member') = 'object';
        UPDATE public.version_links l SET undone_by = v_me.person, undone_at = now()
        WHERE l.customer_id = v_customer AND l.id = p_link;
    END
    $$
    """
FUNCTIONS = {
    "kaagaz_version_joined()": JOINED,
    "kaagaz_move_stories(uuid, uuid, uuid[])": MOVE_STORIES,
    "kaagaz_link_families(uuid[], boolean)": LINK_FAMILIES,
    "kaagaz_link_versions(uuid[], uuid)": LINK,
    "kaagaz_link_undo(uuid)": UNDO,
}
CALLED = [
    name for name in FUNCTIONS if name != "kaagaz_version_joined()"
]  # run by the app; the trigger by none
FUNCTION_COMMENTS = {
    "kaagaz_version_joined()": "Trigger (PRD 7, #648): a version joining a family by a change of family"
    " takes the family's lock, and the people its first version is given and lock granted to.",
    "kaagaz_move_stories(uuid, uuid, uuid[])": "The people's stories of one family re-keyed to another"
    " (a link and its Undo, #648), but where the person has one there already: whose moved. Admins and"
    " Management.",
    "kaagaz_link_families(uuid[], boolean)": "Every version of the families of these files, each with whether"
    " the caller sees it, locked in family order when asked (a link and its Undo, #648). Admins and"
    " Management.",
    "kaagaz_link_versions(uuid[], uuid)": "Link these versions, every version of their families in their new"
    " order, as versions of the latest's family, after checking them again under the locks; the link's id"
    " (#648). Admins and Management who see every version.",
    "kaagaz_link_undo(uuid)": "Undo a link while its family holds exactly what it left, from its snapshot,"
    " after checking it again under the lock (#648). Admins and Management who see every version.",
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
    *[f"GRANT EXECUTE ON FUNCTION public.{name} TO {role}" for name in CALLED for role in ROLES],
    *[f"CREATE TRIGGER {name} {body}" for name, body in TRIGGERS.items()],
]
DOWNGRADE = [
    *[f"DROP TRIGGER IF EXISTS {name} ON documents" for name in TRIGGERS],
    *[f"DROP FUNCTION IF EXISTS public.{name}" for name in reversed(FUNCTIONS)],
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
