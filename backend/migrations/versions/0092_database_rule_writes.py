"""Who can see what, in the database itself: the writes over rows the person can't see (PRD 7
phase 5, PR 9b, #606, Tushar, 15 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema and security.

Under 0091's rule a statement of the API role skips the rows the current person can't see, so a
write over a whole folder or a whole delete would leave those behind (a folder deleted with a file
an Admin can't open keeps it, and its key refuses the delete; a restore stops below a folder the
person can't see). These owner-rights functions do those writes over the whole organisation, each
the statement the app ran before, after checking the caller:

* ``kaagaz_admin_trash_in_folders(folders, trash_id, now)``: every family filed in these folders to
  Trash with them (``trash._TRASH_IN_FOLDERS``); how many families. An Admin.
* ``kaagaz_detach_trashed(folders)``: the files in Trash filed in these folders leave them
  (``folders._DETACH_TRASHED``); how many (to an Admin only: the count holds the hidden ones). An
  Admin, or a person who sees every one of the folders (a folder they delete).
* ``kaagaz_admin_move_folder_documents(from, to)``: every file directly in ``from``, Trash too,
  into ``to`` (``folders._MOVE_DOCUMENTS``, a folder deleted with ``keep``), after #594's keep
  gate read again (``folders._SAME_ACCESS``: refused, insufficient_privilege, when other people
  would see them there); how many. An Admin.
* ``kaagaz_restore_folder(folder)`` and ``kaagaz_restore_with_folders(document)``: a folder in Trash
  restored with every folder and file of its delete inside it and the folders of its delete above
  it; or one file filed in a folder in Trash, with that folder and the ones above it
  (``kaagaz.db.trashed_folders``'s rules, #542 and #594: its folder when live, else its private
  folder or department, else the top level, private when one it was in is gone; the first free
  "Name (n)"; 100 levels). The person sees the folder (or the file) asked for, else nothing
  happens, as for an id that isn't there; a department comes back by an Admin only (F1), and a
  folder by an Admin or who uploaded every file in it, both read on the whole delete. A refusal
  writes nothing and says why in ``refused``. One row for the topmost folder and one for the folder
  asked for (a row once when they are one), the name blank when the person can't see that folder.
* ``kaagaz_trash_uploaders(folder)``: who uploaded every version of every file a folder in Trash
  holds at every depth (the restore rule), user ids only. An active person who sees the folder
  (else NULL, as for one that isn't there).
* ``kaagaz_trash_families(folder)``: the families a folder in Trash holds at every depth, for delete
  forever. An Admin, or a person who sees the folder and nothing hidden inside it (0091's F6).
* ``kaagaz_purge_family(family)``: one family deleted for good, every version, when every version
  is in Trash (else nothing); each version's id and folder, for the app to delete its stored files
  in the same transaction and prune the folder. An Admin, or a person who sees every version of
  the family (one they can't see is an Admin's to delete).
* ``kaagaz_admin_move_documents(ids, folder)`` and ``kaagaz_admin_trash_documents(ids, now)``: the
  names list's Move and Send to Trash (PRD 7 section 3) for files the Admin can't see: whole
  families, live ones only; the folder a live one of this organisation, or the top level. An Admin.
  Their route comes with the names list's screen.
* 0091's ``kaagaz_hidden_inside(trashed)`` counts only the trashed folders given that the person
  sees (the lead's decision on PR 9b's review): one they can't see answers as an id that isn't
  there (0, 0, false). Made from 0091's own text by a checked replace; the downgrade puts it back.

Every function: SECURITY DEFINER, an empty search path, every table named in full and filtered on
``kaagaz_current_customer()`` (the owner skips the organisation rule too), nothing for PUBLIC,
EXECUTE to the app and the worker role alike (0083's equality test). "An Admin" is 0091's check:
an Admin, or the worker role with no person named (``current_setting('role')``, which an
owner-rights function doesn't change and the app role can't take); anyone else gets
insufficient_privilege. Each one that writes takes the tree lock (``folders._lock_tree``), then the
family locks in family order (each family's first version, then by id), and reads again under
them; but ``kaagaz_purge_family``, which takes its family's lock only, as delete forever always
has (the stored files are deleted in its transaction, and no tree lock is held across them).

Catalog only: no table is locked, read or written. The downgrade drops the new functions and puts
0091's ``kaagaz_hidden_inside`` back.

Revision ID: 0092
Revises: 0091
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None

ROLES = "kaagaz_app, kaagaz_worker"  # 0086's: the app and the worker role alike (0083)


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0092", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0091 = _load("0091_database_rule.py")


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_ME = "public.kaagaz_current_customer()"
_HEAD = "LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$"
# The caller: who they are, and whether they are an Admin or the worker role with no person (0091's
# check); then the tree lock (folders._LOCK_TREE).
_WHO = """
        SELECT x.person, coalesce(x.admin, false)
               OR (x.person IS NULL AND NOT x.asked AND current_setting('role') = 'kaagaz_worker')
        INTO v_person, v_admin FROM public.kaagaz_access() x;"""
_AN_ADMIN = (
    _WHO
    + """
        IF NOT v_admin THEN
            RAISE EXCEPTION 'only an Admin' USING ERRCODE = 'insufficient_privilege';
        END IF;"""
)
_A_PERSON = (
    _WHO
    + """
        IF v_person IS NULL AND NOT v_admin THEN
            RAISE EXCEPTION 'only an active person of this organisation' USING ERRCODE = 'insufficient_privilege';
        END IF;"""
)
_TREE_LOCK = f"""
        PERFORM 1 FROM public.customers c WHERE c.id = {_ME} FOR NO KEY UPDATE;"""
_DECLARE = """
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_person uuid;
        v_admin boolean;"""
# Rows of these documents locked in family order (trash._LOCK_FAMILY's), for an UPDATE ... FROM.
_LOCKED = (
    "(SELECT x.id FROM public.documents x WHERE x.customer_id = v_customer AND {where}"
    " ORDER BY x.family_id, (x.id <> x.family_id), x.id FOR NO KEY UPDATE) l"
)
# #594 (folders._SAME_ACCESS): what folder p_from holds sees the same people in p_to.
_SAME_ACCESS = """
        SELECT (f.department_id, f.private_folder_id) IS NOT DISTINCT FROM (t.department_id, t.private_folder_id)
            OR (f.private_folder_id IS NULL AND t.private_folder_id IS NULL
                AND NOT EXISTS (SELECT 1 FROM public.folders d WHERE d.customer_id = v_customer
                                AND d.id IN (f.department_id, t.department_id) AND d.department_only))
        FROM public.folders f LEFT JOIN public.folders t ON t.customer_id = v_customer AND t.id = p_to
        WHERE f.customer_id = v_customer AND f.id = p_from"""

# --- a restore (kaagaz.db.trashed_folders's rules, #542 and #594) ---------------------------------
_RESTORED = """
    RETURNS TABLE (id uuid, parent_id uuid, name text, created_at timestamptz, kind text, sort_order integer,
                   department_only boolean, private boolean, is_top boolean, is_asked boolean, trash_id uuid,
                   refused text, renamed boolean, relocated boolean, sealed boolean, below uuid[], families uuid[])"""
_RESTORE_DECLARE = (
    _DECLARE
    + """
        v_chain uuid[];      -- the folders of the delete from the topmost down to the asked one
        v_inside uuid[];     -- the asked folder and every folder of its delete inside it (a folder's restore)
        v_ids uuid[];        -- every trashed folder made live, the topmost first
        v_height integer;
        v_top public.trashed_folders;
        v_parent uuid;
        v_kept uuid;
        v_seal boolean := false;
        v_relocated boolean := false;
        v_depth integer := 0;
        v_name text;
        v_families uuid[];"""
)
# The trashed folder v_chain[array_length] and the folders of its delete above it, the topmost first.
_CHAIN = """
        WITH RECURSIVE up (id, parent_id, trash_id, depth) AS (
            SELECT t.id, t.parent_id, t.trash_id, 1 FROM public.trashed_folders t
            WHERE t.customer_id = v_customer AND t.id = {start}
            UNION ALL SELECT t.id, t.parent_id, t.trash_id, up.depth + 1 FROM up
            JOIN public.trashed_folders t ON t.customer_id = v_customer AND t.id = up.parent_id
            AND t.trash_id = up.trash_id WHERE up.depth < 101
        )
        SELECT array_agg(up.id ORDER BY up.depth DESC) INTO v_chain FROM up;
        IF v_chain IS NULL THEN
            RETURN;
        END IF;"""
# Where the topmost goes, under which name, as trashed_folders._make_live (#594's _out_of_folder):
# its folder when live; else its private folder while live, else its department while live, else
# the top level, private when one it inherited is gone; 100 levels; the first free "Name (n)".
# A refusal returns one row saying why, before anything is written.
_MAKE_LIVE = """
        IF EXISTS (SELECT 1 FROM public.folders f WHERE f.customer_id = v_customer AND f.id = ANY (v_ids)) THEN
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz, NULL::text, NULL::integer,
                NULL::boolean, NULL::boolean, NULL::boolean, NULL::boolean, NULL::uuid, 'folder_live'::text,
                NULL::boolean, NULL::boolean, NULL::boolean, NULL::uuid[], NULL::uuid[];
            RETURN;
        END IF;
        SELECT t.* INTO v_top FROM public.trashed_folders t WHERE t.customer_id = v_customer AND t.id = v_ids[1];
        IF v_top.kind = 'department' AND NOT v_admin THEN
            RAISE EXCEPTION 'a department comes back by an Admin only' USING ERRCODE = 'insufficient_privilege';
        END IF;
        v_parent := v_top.parent_id;
        IF v_parent IS NOT NULL THEN
            PERFORM 1 FROM public.folders f WHERE f.customer_id = v_customer AND f.id = v_parent FOR SHARE;
        END IF;
        IF v_parent IS NULL OR NOT FOUND THEN
            v_relocated := v_parent IS NOT NULL;
            v_parent := NULL;
            FOR v_kept IN SELECT k.id FROM unnest(ARRAY[v_top.private_folder_id, v_top.department_id])
                          WITH ORDINALITY AS k (id, n) WHERE k.id IS NOT NULL AND k.id <> v_top.id ORDER BY k.n LOOP
                PERFORM 1 FROM public.folders f WHERE f.customer_id = v_customer AND f.id = v_kept FOR SHARE;
                IF FOUND THEN
                    v_parent := coalesce(v_parent, v_kept);
                ELSE
                    v_seal := true;
                END IF;
            END LOOP;
        END IF;
        IF v_parent IS NOT NULL THEN
            SELECT d.depth INTO v_depth FROM public.kaagaz_folder_depth(v_parent, NULL) d;
        END IF;
        IF v_depth + v_height > 100 THEN
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz, NULL::text, NULL::integer,
                NULL::boolean, NULL::boolean, NULL::boolean, NULL::boolean, NULL::uuid, 'folder_too_deep'::text,
                NULL::boolean, NULL::boolean, NULL::boolean, NULL::uuid[], NULL::uuid[];
            RETURN;
        END IF;
        SELECT w.n INTO v_name
        FROM unnest(ARRAY[v_top.name] || ARRAY(
                 SELECT regexp_replace(left(v_top.name, 100 - length(' (' || g || ')')), '\\s+$', '') || ' (' || g || ')'
                 FROM generate_series(2, 100) AS g ORDER BY g)) WITH ORDINALITY AS w (n, i)
        WHERE NOT EXISTS (SELECT 1 FROM public.folders f WHERE f.customer_id = v_customer
                          AND (f.parent_id = v_parent OR (v_parent IS NULL AND f.parent_id IS NULL))
                          AND lower(normalize(f.name, NFC)) = lower(normalize(w.n, NFC)))
        ORDER BY w.i LIMIT 1;
        IF v_name IS NULL THEN
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz, NULL::text, NULL::integer,
                NULL::boolean, NULL::boolean, NULL::boolean, NULL::boolean, NULL::uuid, 'folder_names_taken'::text,
                NULL::boolean, NULL::boolean, NULL::boolean, NULL::uuid[], NULL::uuid[];
            RETURN;
        END IF;
        INSERT INTO public.folders (id, customer_id, parent_id, name, created_by, created_at, kind, sort_order,
                                    autofile_key, department_only, private, department_id, private_folder_id)
        SELECT t.id, t.customer_id, CASE WHEN t.id = v_top.id THEN v_parent ELSE t.parent_id END,
            CASE WHEN t.id = v_top.id THEN v_name ELSE t.name END, t.created_by, t.created_at, t.kind, t.sort_order,
            CASE WHEN EXISTS (SELECT 1 FROM public.folders k WHERE k.customer_id = v_customer
                              AND k.autofile_key = t.autofile_key) THEN NULL ELSE t.autofile_key END,
            t.department_only, t.private OR (t.id = v_top.id AND v_seal), t.department_id, t.private_folder_id
        FROM public.trashed_folders t WHERE t.customer_id = v_customer AND t.id = ANY (v_ids);"""
# The rest of a chain's files stay in Trash, now in their live folder (trashed_folders._INTO_LIVE);
# the folders made live leave Trash; the rows of the topmost and the asked folder.
_FINISH = f"""
        UPDATE public.documents d SET folder_id = d.trashed_folder_id, trashed_folder_id = NULL, trash_id = NULL
        FROM {_LOCKED.format(where="x.trashed_folder_id = ANY ({chain_only})")}
        WHERE d.customer_id = v_customer AND d.id = l.id;
        DELETE FROM public.trashed_folders t WHERE t.customer_id = v_customer AND t.id = ANY (v_ids);
        RETURN QUERY SELECT f.id, f.parent_id,
            CASE WHEN v_person IS NULL OR EXISTS (SELECT 1 FROM public.visible_folders v
                                                  WHERE v.customer_id = v_customer AND v.id = f.id) THEN f.name END,
            f.created_at, f.kind, f.sort_order, f.department_only, f.private, f.id = v_top.id,
            f.id IS NOT DISTINCT FROM {{asked}}, v_top.trash_id, NULL::text, v_name <> v_top.name, v_relocated, v_seal,
            {{below}}, v_families
        FROM public.folders f WHERE f.customer_id = v_customer AND f.id IN (v_top.id, {{asked}});"""

FUNCTIONS = {
    "kaagaz_admin_trash_in_folders(uuid[], uuid, timestamp with time zone)": f"""
    CREATE FUNCTION public.kaagaz_admin_trash_in_folders(p_folders uuid[], p_trash uuid, p_now timestamptz)
    RETURNS integer
    {_HEAD}{_DECLARE}
        v_families integer;
    BEGIN
        {_AN_ADMIN}{_TREE_LOCK}
        WITH sent AS (
            UPDATE public.documents d SET deleted_at = p_now, deleted_by = v_person, trashed_folder_id = d.folder_id,
                trash_id = p_trash, folder_id = NULL, autofile = NULL
            FROM {_LOCKED.format(where="x.folder_id = ANY (p_folders)")}
            WHERE d.customer_id = v_customer AND d.id = l.id AND d.deleted_at IS NULL RETURNING d.family_id
        )
        SELECT count(DISTINCT sent.family_id) INTO v_families FROM sent;
        RETURN v_families;
    END
    $$
    """,
    "kaagaz_detach_trashed(uuid[])": f"""
    CREATE FUNCTION public.kaagaz_detach_trashed(p_folders uuid[]) RETURNS integer
    {_HEAD}{_DECLARE}
        v_detached integer;
    BEGIN
        {_A_PERSON}{_TREE_LOCK}
        IF NOT v_admin AND EXISTS (SELECT 1 FROM unnest(p_folders) AS p (id) WHERE NOT EXISTS (
            SELECT 1 FROM public.visible_folders v WHERE v.customer_id = v_customer AND v.id = p.id
        )) THEN
            RAISE EXCEPTION 'a folder the person can''t see' USING ERRCODE = 'insufficient_privilege';
        END IF;
        UPDATE public.documents d SET folder_id = NULL, autofile = NULL
        FROM {_LOCKED.format(where="x.folder_id = ANY (p_folders) AND x.deleted_at IS NOT NULL")}
        WHERE d.customer_id = v_customer AND d.id = l.id AND d.deleted_at IS NOT NULL;
        GET DIAGNOSTICS v_detached = ROW_COUNT;
        RETURN CASE WHEN v_admin THEN v_detached END;  -- how many, the hidden ones too: an Admin's only
    END
    $$
    """,
    "kaagaz_admin_move_folder_documents(uuid, uuid)": f"""
    CREATE FUNCTION public.kaagaz_admin_move_folder_documents(p_from uuid, p_to uuid) RETURNS integer
    {_HEAD}{_DECLARE}
        v_moved integer;
    BEGIN
        {_AN_ADMIN}{_TREE_LOCK}
        IF NOT coalesce(({_SAME_ACCESS}), true) THEN  -- no such folder: nothing to move
            RAISE EXCEPTION 'other people would see them there' USING ERRCODE = 'insufficient_privilege';
        END IF;
        UPDATE public.documents d SET folder_id = p_to, autofile = NULL
        FROM {_LOCKED.format(where="x.folder_id = p_from")}
        WHERE d.customer_id = v_customer AND d.id = l.id;
        GET DIAGNOSTICS v_moved = ROW_COUNT;
        RETURN v_moved;
    END
    $$
    """,
    "kaagaz_restore_folder(uuid)": f"""
    CREATE FUNCTION public.kaagaz_restore_folder(p_folder uuid){_RESTORED}
    {_HEAD}
    #variable_conflict use_column{_RESTORE_DECLARE}
    BEGIN
        {_A_PERSON}{_TREE_LOCK}
        IF v_person IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.visible_trashed_folders v
                                                WHERE v.customer_id = v_customer AND v.id = p_folder) THEN
            RETURN;
        END IF;{_CHAIN.format(start="p_folder")}
        WITH RECURSIVE down (id, trash_id, depth) AS (
            SELECT t.id, t.trash_id, 1 FROM public.trashed_folders t WHERE t.customer_id = v_customer AND t.id = p_folder
            UNION ALL SELECT t.id, t.trash_id, down.depth + 1 FROM down
            JOIN public.trashed_folders t ON t.customer_id = v_customer AND t.parent_id = down.id
            AND t.trash_id = down.trash_id WHERE down.depth < 101
        )
        SELECT array_agg(down.id ORDER BY down.depth, down.id), max(down.depth) INTO v_inside, v_height FROM down;
        IF NOT v_admin AND (
            NOT EXISTS (SELECT 1 FROM public.documents d WHERE d.customer_id = v_customer
                        AND d.trashed_folder_id = ANY (v_inside))
            OR EXISTS (SELECT 1 FROM public.documents d WHERE d.customer_id = v_customer
                       AND d.trashed_folder_id = ANY (v_inside) AND d.uploaded_by IS DISTINCT FROM v_person)
        ) THEN
            RAISE EXCEPTION 'someone else uploaded a file in it: an Admin restores it' USING ERRCODE = 'insufficient_privilege';
        END IF;
        v_ids := v_chain[1:array_length(v_chain, 1) - 1] || v_inside;
        v_height := v_height + array_length(v_chain, 1) - 1;{_MAKE_LIVE}
        WITH back AS (
            UPDATE public.documents d SET deleted_at = NULL, deleted_by = NULL, folder_id = d.trashed_folder_id,
                trashed_folder_id = NULL, trash_id = NULL
            FROM {_LOCKED.format(where="x.trashed_folder_id = ANY (v_inside)")}
            WHERE d.customer_id = v_customer AND d.id = l.id RETURNING d.family_id
        )
        SELECT coalesce(array_agg(DISTINCT back.family_id), '{{}}') INTO v_families FROM back;
        {_FINISH.format(chain_only="v_chain[1:array_length(v_chain, 1) - 1]", asked="p_folder", below="v_inside[2:]")}
    END
    $$
    """,
    "kaagaz_restore_with_folders(uuid)": f"""
    CREATE FUNCTION public.kaagaz_restore_with_folders(p_document uuid){_RESTORED}
    {_HEAD}
    #variable_conflict use_column{_RESTORE_DECLARE}
        v_family uuid;
        v_folder uuid;
    BEGIN
        {_A_PERSON}{_TREE_LOCK}
        IF v_person IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.visible_documents v
                                                WHERE v.customer_id = v_customer AND v.id = p_document) THEN
            RETURN;
        END IF;
        PERFORM 1 FROM public.documents d
        WHERE d.customer_id = v_customer AND d.family_id = (SELECT x.family_id FROM public.documents x
                                                            WHERE x.customer_id = v_customer AND x.id = p_document)
        ORDER BY (d.id <> d.family_id), d.id FOR NO KEY UPDATE;
        SELECT d.family_id, d.trashed_folder_id INTO v_family, v_folder FROM public.documents d
        WHERE d.customer_id = v_customer AND d.id = p_document;
        IF v_folder IS NULL THEN
            RETURN;
        END IF;{_CHAIN.format(start="v_folder")}
        v_ids := v_chain;
        v_height := array_length(v_chain, 1);{_MAKE_LIVE}
        UPDATE public.documents d SET deleted_at = NULL, deleted_by = NULL, folder_id = v_folder,
            trashed_folder_id = NULL, trash_id = NULL
        WHERE d.customer_id = v_customer AND d.family_id = v_family;
        v_families := ARRAY[v_family];
        {_FINISH.format(chain_only="v_chain", asked="NULL::uuid", below="v_chain[2:]")}
    END
    $$
    """,
    "kaagaz_trash_uploaders(uuid)": f"""
    CREATE FUNCTION public.kaagaz_trash_uploaders(p_folder uuid) RETURNS uuid[]
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
    {_DECLARE}
    BEGIN
        {_A_PERSON}
        IF v_person IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.visible_trashed_folders v
                                                WHERE v.customer_id = v_customer AND v.id = p_folder) THEN
            RETURN NULL;
        END IF;
        RETURN (WITH RECURSIVE down (id, trash_id, depth) AS (
            SELECT t.id, t.trash_id, 1 FROM public.trashed_folders t WHERE t.customer_id = v_customer AND t.id = p_folder
            UNION ALL SELECT t.id, t.trash_id, down.depth + 1 FROM down
            JOIN public.trashed_folders t ON t.customer_id = v_customer AND t.parent_id = down.id
            AND t.trash_id = down.trash_id WHERE down.depth < 101
        )
        SELECT array_agg(DISTINCT d.uploaded_by) FROM down JOIN public.documents d
        ON d.customer_id = v_customer AND d.trashed_folder_id = down.id AND d.trash_id = down.trash_id);
    END
    $$
    """,
    "kaagaz_trash_families(uuid)": f"""
    CREATE FUNCTION public.kaagaz_trash_families(p_folder uuid) RETURNS SETOF uuid
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' ROWS 100 AS $$
    {_DECLARE}
    BEGIN
        {_A_PERSON}
        IF NOT v_admin AND (
            NOT EXISTS (SELECT 1 FROM public.visible_trashed_folders v WHERE v.customer_id = v_customer AND v.id = p_folder)
            OR (SELECT h.hidden_files > 0 OR h.hidden_folders > 0 FROM public.kaagaz_hidden_inside(ARRAY[p_folder]) h)
        ) THEN
            RAISE EXCEPTION 'something inside is hidden from them: an Admin deletes it' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN QUERY WITH RECURSIVE down (id, trash_id, depth) AS (
            SELECT t.id, t.trash_id, 1 FROM public.trashed_folders t WHERE t.customer_id = v_customer AND t.id = p_folder
            UNION ALL SELECT t.id, t.trash_id, down.depth + 1 FROM down
            JOIN public.trashed_folders t ON t.customer_id = v_customer AND t.parent_id = down.id
            AND t.trash_id = down.trash_id WHERE down.depth < 101
        )
        SELECT DISTINCT d.family_id FROM public.documents d
        WHERE d.customer_id = v_customer AND d.trashed_folder_id IN (SELECT down.id FROM down) ORDER BY d.family_id;
    END
    $$
    """,
    "kaagaz_purge_family(uuid)": f"""
    CREATE FUNCTION public.kaagaz_purge_family(p_family uuid) RETURNS TABLE (document_id uuid, folder_id uuid)
    {_HEAD}{_DECLARE}
    BEGIN
        {_A_PERSON}
        IF NOT v_admin AND NOT EXISTS (SELECT 1 FROM public.visible_documents v
                                       WHERE v.customer_id = v_customer AND v.family_id = p_family) THEN
            RETURN;
        END IF;
        PERFORM 1 FROM public.documents d WHERE d.customer_id = v_customer AND d.family_id = p_family
        ORDER BY (d.id <> d.family_id), d.id FOR NO KEY UPDATE;
        IF NOT FOUND OR EXISTS (SELECT 1 FROM public.documents d WHERE d.customer_id = v_customer
                                AND d.family_id = p_family AND d.deleted_at IS NULL) THEN
            RETURN;
        END IF;
        IF NOT v_admin AND EXISTS (SELECT 1 FROM public.documents d WHERE d.customer_id = v_customer
                                   AND d.family_id = p_family AND NOT EXISTS (
                                       SELECT 1 FROM public.visible_documents v
                                       WHERE v.customer_id = v_customer AND v.id = d.id)) THEN
            RETURN;  -- a version the person can't see: an Admin's to delete
        END IF;
        RETURN QUERY DELETE FROM public.documents d WHERE d.customer_id = v_customer AND d.family_id = p_family
        RETURNING d.id, d.folder_id;
    END
    $$
    """,
    "kaagaz_admin_move_documents(uuid[], uuid)": f"""
    CREATE FUNCTION public.kaagaz_admin_move_documents(p_ids uuid[], p_folder uuid)
    RETURNS TABLE (document_id uuid, family_id uuid, from_folder uuid)
    {_HEAD}
    #variable_conflict use_column{_DECLARE}
    BEGIN
        {_AN_ADMIN}{_TREE_LOCK}
        IF p_folder IS NOT NULL THEN
            PERFORM 1 FROM public.folders f WHERE f.customer_id = v_customer AND f.id = p_folder FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'a live folder of this organisation, or the top level' USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        PERFORM 1 FROM public.documents d WHERE d.customer_id = v_customer AND d.family_id IN (
            SELECT x.family_id FROM public.documents x WHERE x.customer_id = v_customer AND x.id = ANY (p_ids))
        ORDER BY d.family_id, (d.id <> d.family_id), d.id FOR NO KEY UPDATE;
        RETURN QUERY WITH asked AS (
            SELECT x.id, x.family_id, x.folder_id FROM public.documents x
            WHERE x.customer_id = v_customer AND x.id = ANY (p_ids)
            AND NOT EXISTS (SELECT 1 FROM public.documents t WHERE t.customer_id = v_customer
                            AND t.family_id = x.family_id AND t.deleted_at IS NOT NULL)
        ), moved AS (
            UPDATE public.documents d SET folder_id = p_folder, autofile = NULL
            WHERE d.customer_id = v_customer AND d.family_id IN (SELECT asked.family_id FROM asked)
            RETURNING d.id
        )
        SELECT asked.id, asked.family_id, asked.folder_id FROM asked ORDER BY asked.family_id, asked.id;
    END
    $$
    """,
    "kaagaz_admin_trash_documents(uuid[], timestamp with time zone)": f"""
    CREATE FUNCTION public.kaagaz_admin_trash_documents(p_ids uuid[], p_now timestamptz)
    RETURNS TABLE (document_id uuid, family_id uuid)
    {_HEAD}
    #variable_conflict use_column{_DECLARE}
    BEGIN
        {_AN_ADMIN}{_TREE_LOCK}
        PERFORM 1 FROM public.documents d WHERE d.customer_id = v_customer AND d.family_id IN (
            SELECT x.family_id FROM public.documents x WHERE x.customer_id = v_customer AND x.id = ANY (p_ids))
        ORDER BY d.family_id, (d.id <> d.family_id), d.id FOR NO KEY UPDATE;
        UPDATE public.documents d SET deleted_at = p_now, deleted_by = v_person
        WHERE d.customer_id = v_customer AND d.deleted_at IS NULL AND d.family_id IN (
            SELECT x.family_id FROM public.documents x WHERE x.customer_id = v_customer AND x.id = ANY (p_ids));
        RETURN QUERY SELECT x.id, x.family_id FROM public.documents x
        WHERE x.customer_id = v_customer AND x.id = ANY (p_ids) ORDER BY x.family_id, x.id;
    END
    $$
    """,
}
_ADMIN = " An Admin (or the worker role); ids and counts only (PRD 7, #606)."
FUNCTION_COMMENTS = {
    "kaagaz_admin_trash_in_folders(uuid[], uuid, timestamp with time zone)": "Every family filed in these folders"
    " to Trash with them, under one trash_id; how many families (#537, #542)." + _ADMIN,
    "kaagaz_detach_trashed(uuid[])": "The files in Trash filed in these folders leave them (a folder deleted);"
    " how many. An Admin, or a person who sees every one of the folders (PRD 7, #606).",
    "kaagaz_admin_move_folder_documents(uuid, uuid)": "Every file directly in a folder deleted with keep, Trash too,"
    " into another, while the same people see them there (#537, #594); how many." + _ADMIN,
    "kaagaz_restore_folder(uuid)": "A folder in Trash restored with its whole delete inside it and the folders of"
    " its delete above it (#542, #594). The person sees it; a department by an Admin only, a folder by an Admin"
    " or who uploaded every file in it (PRD 7, #606).",
    "kaagaz_restore_with_folders(uuid)": "A file filed in a folder in Trash restored, with that folder and the ones"
    " of its delete above it (#542, #594). The person sees the file; a department by an Admin only (PRD 7, #606).",
    "kaagaz_trash_uploaders(uuid)": "Who uploaded every version of every file a folder in Trash holds, for the"
    " restore rule (#542, PRD 7 #606). An active person who sees the folder; user ids only.",
    "kaagaz_trash_families(uuid)": "The families a folder in Trash holds at every depth, for delete forever (#542)."
    " An Admin, or a person who sees it and nothing hidden inside it (PRD 7, #606); ids only.",
    "kaagaz_purge_family(uuid)": "One family deleted for good when every version is in Trash; each version's id and"
    " folder, for its stored files. An Admin, or a person who sees every version (PRD 7, #606).",
    "kaagaz_admin_move_documents(uuid[], uuid)": "The names list's Move (PRD 7 section 3): these files' live families"
    " into a live folder or the top level; each asked id, its family and the folder it was in." + _ADMIN,
    "kaagaz_admin_trash_documents(uuid[], timestamp with time zone)": "The names list's Send to Trash (PRD 7 section"
    " 3): these files' families to Trash (one there keeps its first time); each asked id and its family."
    + _ADMIN,
}

# 0091's F6, its walk started only from the trashed folders the person sees (its caller is always an
# active person, 0091's _A_PERSON).
_HIDDEN_INSIDE_0091 = _0091.FUNCTIONS["kaagaz_hidden_inside(uuid[])"].replace(
    "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"
)
_START = "WHERE t.customer_id = public.kaagaz_current_customer() AND t.id = ANY (p_trashed)\n"
if _HIDDEN_INSIDE_0091.count(_START) != 1:
    raise RuntimeError("0091's kaagaz_hidden_inside text changed")
HIDDEN_INSIDE = _HIDDEN_INSIDE_0091.replace(
    _START,
    "WHERE t.customer_id = public.kaagaz_current_customer() AND t.id = ANY (p_trashed)"
    " AND EXISTS (SELECT 1 FROM public.visible_trashed_folders v\n"
    "                         WHERE v.customer_id = t.customer_id AND v.id = t.id)\n",
)
HIDDEN_INSIDE_COMMENT = (
    "How many files and folders the current person can't see inside these trashed folders, of those they"
    " see, and whether any file is (PRD 7, #606). An active person; numbers only."
)

UPGRADE = [
    HIDDEN_INSIDE,
    f"COMMENT ON FUNCTION public.kaagaz_hidden_inside(uuid[]) IS {_literal(HIDDEN_INSIDE_COMMENT)}",
    *FUNCTIONS.values(),
    *[
        statement
        for name, comment in FUNCTION_COMMENTS.items()
        for statement in (
            f"COMMENT ON FUNCTION public.{name} IS {_literal(comment)}",
            f"REVOKE ALL ON FUNCTION public.{name} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{name} TO {ROLES}",
        )
    ],
]
DOWNGRADE = [
    *[f"DROP FUNCTION IF EXISTS public.{name}" for name in reversed(FUNCTIONS)],
    _HIDDEN_INSIDE_0091,
    "COMMENT ON FUNCTION public.kaagaz_hidden_inside(uuid[])"
    f" IS {_literal(_0091.READ_COMMENTS['kaagaz_hidden_inside(uuid[])'])}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
