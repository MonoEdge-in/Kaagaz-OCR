"""Who can see what: the access API's owner-rights functions, and switching a person off (PRD 7
PR 10a, #601, Tushar, 14 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema and security.

Every function follows the house pattern: SECURITY DEFINER, an empty search path, every table
named in full, nothing for PUBLIC, and a check of the caller inside. An owner skips row level
security, so every table and view each one reads also names ``kaagaz_current_customer()`` (the
views carry no customer filter of their own, 0086).

* ``kaagaz_access_of(p_user)``: ``kaagaz_access()``'s text (0089's) with the person named instead
  of read from ``kaagaz.user_id``, made by checked replaces, so the rule is written once: one
  person's access (person NULL for an unknown, switched-off or other organisation's person).
  Internal: no role may run it.
* ``kaagaz_audience(kind, id, user)``: for a document or folder, one row per active person of the
  organisation (only ``user`` when given, switched off or not), with why they see it (each rule's
  flag) and ``sees``, the access view's own predicate (0087's text, the person's files given and
  lock grants read for them). The caller is an Admin, or the Head of the target's department who
  can see it; anyone else gets insufficient_privilege.
* ``kaagaz_place_audience(department, private, department_only)``: whether each active person
  sees the files of a place (R1, R4, R5, from the folders view's text), with Department only as
  given rather than as stored: the "N people will lose access" lists. Admins only.
* ``kaagaz_kept_from_me(folder)``: the names of the files in a folder the calling Admin can't see
  (PRD 7, "5 files here are kept from you"). Admins only.
* ``kaagaz_admin_document(id)``: a file's id, family, name, whether in Trash and locked, for an
  Admin whether they can see it or not (decision 20). Admins only.
* ``kaagaz_give_file(document, users)`` and ``kaagaz_take_file(document, user)``: a file given to
  people on every live version (one family lock and one insert for all of them; a person
  switched off, unknown or another organisation's is refused, check_violation), or taken back
  from one person on every version (Trash too), under the family lock
  (``kaagaz.db.trash.lock_family``'s order). Admins only; the caller takes the tree lock first.
  They work once PR 9's rule hides a file's rows from an Admin who can't open it.
* ``users_switched`` (``kaagaz_user_switched``): when ``users.disabled`` changes either way, the
  person's memberships, names on private folders, files given and lock grants go and Management
  is cleared, so a person switched on again starts with none (PRD 7 section 9); the access version
  goes up once (by ``users_access_changed`` when Management was on), and switching off writes one
  ``access.switched_off`` audit row about them (switching on, ``access.switched_on``, only when it
  cleared something left over).
* ``kaagaz_op_change_admin`` (0017) takes the organisation's row FOR NO KEY UPDATE (the app's tree
  lock), not FOR SHARE: its update of ``users`` now raises the access version on that row, and the
  app takes the tree lock before ``users``. Its body is read back and that one line changed exactly
  once (0061's way).

EXECUTE goes to the app and the worker role alike (0083's equality test). Catalog only, but for
the trigger's CREATE TRIGGER (``users`` locked a moment). The downgrade drops the trigger and the
functions and puts the operator's line back.

Revision ID: 0090
Revises: 0089
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0090", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0061 = _load("0061_email.py")
_0086 = _load("0086_access_views.py")
_0087 = _load("0087_trashed_folders_view.py")
_0089 = _load("0089_open_paths.py")
ROLES = _0086.ROLES
_literal = _0086._literal


def _replaced(text: str, old: str, new: str, times: int = 1) -> str:
    if text.count(old) != times:
        raise RuntimeError(f"the rule's text changed: {old!r}")
    return text.replace(old, new)


def _rule(view: str) -> str:
    """A view's predicate (after its WHERE), with the worker's whole-organisation case taken out."""
    where = view[view.index(" WHERE ") + len(" WHERE ") :]
    return _replaced(where, _0086._ANYONE, "false")


ACCESS_OF = _replaced(
    _replaced(
        _replaced(
            _0089.ACCESS,
            "CREATE OR REPLACE FUNCTION public.kaagaz_access()",
            "CREATE FUNCTION public.kaagaz_access_of(p_user uuid)",
        ),
        f"{_0086._PERSON} IS NOT NULL",
        "p_user IS NOT NULL",
    ),
    f"{_0086._PERSON}::uuid",
    "p_user",
)

_GIVEN = "SELECT s.document_id FROM public.document_shares s WHERE s.customer_id = d.customer_id AND s.user_id = a.person"
_LOCK_GRANTS = "SELECT g.document_id FROM public.lock_grants g WHERE g.customer_id = d.customer_id AND g.user_id = a.person"
# visible_documents' predicate, the person's files given and lock grants read for them.
DOCUMENT_RULE = _replaced(
    _replaced(
        _rule(_0087.VIEWS["visible_documents"]), "SELECT public.kaagaz_lock_open_to_me()", _LOCK_GRANTS
    ),
    "SELECT public.kaagaz_shared_with_me()",
    _GIVEN,
)
FOLDER_RULE = _rule(_0087.VIEWS["visible_folders"])
# A place's files (not a folder's name: an Admin's sight of every folder goes), with Department
# only as given: the stored open check becomes the flag.
PLACE_RULE = _replaced(
    _replaced(
        _replaced(
            _replaced(FOLDER_RULE, "a.admin OR ", ""),
            "f.department_id IS NULL OR NOT a.walled OR f.department_id = ANY (a.open)",
            "f.department_id IS NULL OR NOT p_department_only",
        ),
        "f.department_id",
        "p_department",
        times=3,
    ),
    "f.private_folder_id",
    "p_private",
    times=3,
)

# Why each person sees a file or folder ({t}: d or f): one flag per rule, for "who can see this".
_PLACE_FLAGS = """
            coalesce(a.person IS NOT NULL AND {t}.private_folder_id IS NULL{held}
                     AND {t}.department_id = ANY (a.departments), false),
            coalesce(a.person IS NOT NULL AND {t}.private_folder_id IS NOT NULL
                     AND {t}.department_id = ANY (a.heads), false),
            coalesce(a.person IS NOT NULL AND {t}.private_folder_id = ANY (a.named), false),"""
_EVERYONE = (
    "coalesce(a.person IS NOT NULL AND {t}.private_folder_id IS NULL{held} AND ({t}.department_id IS NULL"
    " OR NOT a.walled OR {t}.department_id = ANY (a.open)), false)"
)
# Each active person, or the one asked ({join}: the file or folder, by p_id).
_PEOPLE = """
        FROM public.users u
        CROSS JOIN LATERAL public.kaagaz_access_of(u.id) a{join}
        WHERE u.customer_id = v_customer AND {who}
        ORDER BY u.id;"""
_ASKED = "CASE WHEN p_user IS NULL THEN NOT u.disabled ELSE u.id = p_user END"
_ADMIN_ONLY = """
        IF NOT (public.kaagaz_access()).admin THEN
            RAISE EXCEPTION 'only an Admin' USING ERRCODE = 'insufficient_privilege';
        END IF;"""
# trash._LOCK_FAMILY's lock: the family's first version first, then by id.
_LOCK_FAMILY = """
        PERFORM 1 FROM public.documents d
        WHERE d.customer_id = v_customer AND d.family_id = (SELECT x.family_id FROM public.documents x
                                                            WHERE x.customer_id = v_customer AND x.id = p_document)
        ORDER BY (d.id <> d.family_id), d.id FOR NO KEY UPDATE;"""

FUNCTIONS = {
    "kaagaz_access_of(uuid)": ACCESS_OF,
    "kaagaz_audience(text, uuid, uuid)": f"""
    CREATE FUNCTION public.kaagaz_audience(p_kind text, p_id uuid, p_user uuid DEFAULT NULL)
    RETURNS TABLE (user_id uuid, management boolean, admin boolean, member boolean, head boolean,
                   named boolean, given boolean, held boolean, everyone boolean, may_open_locked boolean,
                   sees boolean)
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_me public.kaagaz_access_set := public.kaagaz_access();
        v_department uuid;
        v_seen boolean;
    BEGIN
        IF p_kind = 'document' THEN
            SELECT d.department_id INTO v_department FROM public.documents d
            WHERE d.customer_id = v_customer AND d.id = p_id;
            v_seen := EXISTS (SELECT 1 FROM public.visible_documents v WHERE v.customer_id = v_customer AND v.id = p_id);
        ELSIF p_kind = 'folder' THEN
            SELECT f.department_id INTO v_department FROM public.folders f
            WHERE f.customer_id = v_customer AND f.id = p_id;
            v_seen := EXISTS (SELECT 1 FROM public.visible_folders v WHERE v.customer_id = v_customer AND v.id = p_id);
        ELSE
            RAISE EXCEPTION 'a document or a folder' USING ERRCODE = 'check_violation';
        END IF;
        IF NOT coalesce(v_me.admin OR (v_seen AND v_department = ANY (v_me.heads)), false) THEN
            RAISE EXCEPTION 'only an Admin, or the Head of its department' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF p_kind = 'document' THEN
            RETURN QUERY SELECT u.id, coalesce(a.management, false), coalesce(a.admin, false),
                {_PLACE_FLAGS.format(t="d", held=" AND d.held_for IS NULL")}
                coalesce(a.person IS NOT NULL AND d.id IN ({_GIVEN}), false),
                coalesce(d.held_for = a.person, false),
                {_EVERYONE.format(t="d", held=" AND d.held_for IS NULL")},
                coalesce(a.person IS NOT NULL AND d.locked_at IS NOT NULL AND d.id IN ({_LOCK_GRANTS}), false),
                coalesce({DOCUMENT_RULE}, false)
            {_PEOPLE.format(join=" JOIN public.documents d ON d.customer_id = v_customer AND d.id = p_id", who=_ASKED)}
        ELSE
            RETURN QUERY SELECT u.id, coalesce(a.management, false), coalesce(a.admin, false),
                {_PLACE_FLAGS.format(t="f", held="")}
                false, false,
                {_EVERYONE.format(t="f", held="")},
                false,
                coalesce({FOLDER_RULE}, false)
            {_PEOPLE.format(join=" JOIN public.folders f ON f.customer_id = v_customer AND f.id = p_id", who=_ASKED)}
        END IF;
    END
    $$
    """,
    "kaagaz_place_audience(uuid, uuid, boolean)": f"""
    CREATE FUNCTION public.kaagaz_place_audience(p_department uuid, p_private uuid, p_department_only boolean)
    RETURNS TABLE (user_id uuid, sees boolean)
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
    BEGIN
        {_ADMIN_ONLY}
        RETURN QUERY SELECT u.id, coalesce({PLACE_RULE}, false)
        {_PEOPLE.format(join="", who="NOT u.disabled")}
    END
    $$
    """,
    "kaagaz_kept_from_me(uuid)": f"""
    CREATE FUNCTION public.kaagaz_kept_from_me(p_folder uuid)
    RETURNS TABLE (id uuid, display_name text, detected_type text, uploader text, created_at timestamptz,
                   version_no integer, locked boolean)
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
    BEGIN
        {_ADMIN_ONLY}
        RETURN QUERY SELECT d.id, d.display_name, d.detected_type, u.display_name, d.created_at, d.version_no,
            d.locked_at IS NOT NULL
        FROM public.documents d
        LEFT JOIN public.users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by
        WHERE d.customer_id = v_customer AND d.folder_id = p_folder AND d.deleted_at IS NULL AND d.is_current
        AND NOT EXISTS (SELECT 1 FROM public.visible_documents v WHERE v.customer_id = v_customer AND v.id = d.id)
        ORDER BY lower(d.display_name), d.id;
    END
    $$
    """,
    "kaagaz_admin_document(uuid)": f"""
    CREATE FUNCTION public.kaagaz_admin_document(p_id uuid)
    RETURNS TABLE (id uuid, family_id uuid, display_name text, in_trash boolean, locked boolean)
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
    BEGIN
        {_ADMIN_ONLY}
        RETURN QUERY SELECT d.id, d.family_id, d.display_name, d.deleted_at IS NOT NULL, d.locked_at IS NOT NULL
        FROM public.documents d WHERE d.customer_id = v_customer AND d.id = p_id;
    END
    $$
    """,
    "kaagaz_give_file(uuid, uuid[])": f"""
    CREATE FUNCTION public.kaagaz_give_file(p_document uuid, p_users uuid[])
    RETURNS TABLE (user_id uuid, versions integer, added integer)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
    BEGIN
        {_ADMIN_ONLY}
        IF EXISTS (SELECT 1 FROM unnest(p_users) AS p (id) WHERE NOT EXISTS (
            SELECT 1 FROM public.users u WHERE u.customer_id = v_customer AND u.id = p.id AND NOT u.disabled
        )) THEN
            RAISE EXCEPTION 'only people of this organisation who are switched on' USING ERRCODE = 'check_violation';
        END IF;
        {_LOCK_FAMILY}
        RETURN QUERY WITH live AS (
            SELECT d.id FROM public.documents d
            JOIN public.documents x ON x.customer_id = d.customer_id AND x.family_id = d.family_id
            WHERE x.customer_id = v_customer AND x.id = p_document AND d.deleted_at IS NULL
        ), given AS (
            INSERT INTO public.document_shares AS s (customer_id, document_id, user_id, given_by)
            SELECT v_customer, l.id, p.id, (public.kaagaz_access()).person
            FROM live l CROSS JOIN (SELECT DISTINCT q.id FROM unnest(p_users) AS q (id)) p
            ON CONFLICT DO NOTHING RETURNING s.user_id
        )
        SELECT p.id, (SELECT count(*) FROM live)::integer,
               (SELECT count(*) FROM given g WHERE g.user_id = p.id)::integer
        FROM (SELECT DISTINCT q.id FROM unnest(p_users) AS q (id)) p ORDER BY p.id;
    END
    $$
    """,
    "kaagaz_take_file(uuid, uuid)": f"""
    CREATE FUNCTION public.kaagaz_take_file(p_document uuid, p_user uuid) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_taken integer;
    BEGIN
        {_ADMIN_ONLY}
        {_LOCK_FAMILY}
        DELETE FROM public.document_shares s USING public.documents x, public.documents d
        WHERE x.customer_id = v_customer AND x.id = p_document
        AND d.customer_id = x.customer_id AND d.family_id = x.family_id
        AND s.customer_id = d.customer_id AND s.document_id = d.id AND s.user_id = p_user;
        GET DIAGNOSTICS v_taken = ROW_COUNT;
        RETURN v_taken;
    END
    $$
    """,
}
FUNCTION_COMMENTS = {
    "kaagaz_access_of(uuid)": "kaagaz_access() for the person named (PRD 7, #601): their access; person NULL"
    " for one who is unknown, switched off or another organisation's. Internal.",
    "kaagaz_audience(text, uuid, uuid)": "Who sees a document or folder and why (PRD 7, #601): one row per"
    " active person (or the one asked), a flag per rule and sees, the access view's own rule. An Admin, or the"
    " Head of its department who can see it.",
    "kaagaz_place_audience(uuid, uuid, boolean)": "Whether each active person sees the files of a place"
    " (department, private folder, Department only as given), for the lose-access lists (PRD 7, #601). Admins.",
    "kaagaz_kept_from_me(uuid)": "The names of the files in a folder the calling Admin can't see (PRD 7, #601).",
    "kaagaz_admin_document(uuid)": "A file's id, family, name, in Trash and locked, for an Admin whether they can"
    " see it or not (PRD 7 decision 20, #601).",
    "kaagaz_give_file(uuid, uuid[])": "Give a file to people (switched on, of this organisation) on every"
    " live version, under the family lock; per person (versions, added). Admins; the caller holds the tree"
    " lock (PRD 7, #601).",
    "kaagaz_take_file(uuid, uuid)": "Take a file back from a person on every version, Trash too, under the"
    " family lock; how many rows went. Admins; the caller holds the tree lock (PRD 7, #601).",
}
GRANTED = [name for name in FUNCTIONS if not name.startswith("kaagaz_access_of")]

SWITCHED = """
    CREATE FUNCTION public.kaagaz_user_switched() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_departments integer;
        v_named integer;
        v_files integer;
        v_lock_grants integer;
    BEGIN
        DELETE FROM public.department_members x WHERE x.customer_id = NEW.customer_id AND x.user_id = NEW.id;
        GET DIAGNOSTICS v_departments = ROW_COUNT;
        DELETE FROM public.private_folder_members x WHERE x.customer_id = NEW.customer_id AND x.user_id = NEW.id;
        GET DIAGNOSTICS v_named = ROW_COUNT;
        DELETE FROM public.document_shares x WHERE x.customer_id = NEW.customer_id AND x.user_id = NEW.id;
        GET DIAGNOSTICS v_files = ROW_COUNT;
        DELETE FROM public.lock_grants x WHERE x.customer_id = NEW.customer_id AND x.user_id = NEW.id;
        GET DIAGNOSTICS v_lock_grants = ROW_COUNT;
        NEW.management := false;
        IF NOT OLD.management THEN  -- else users_access_changed raises it, for Management cleared
            UPDATE public.customers c SET access_version = c.access_version + 1 WHERE c.id = NEW.customer_id;
        END IF;
        IF NEW.disabled OR OLD.management OR v_departments + v_named + v_files + v_lock_grants > 0 THEN
            INSERT INTO public.audit_log (customer_id, actor_user_id, action, target_type, target_id, details,
                                          subject_user_id)
            VALUES (NEW.customer_id, NULLIF(current_setting('kaagaz.user_id', true), '')::uuid,
                    CASE WHEN NEW.disabled THEN 'access.switched_off' ELSE 'access.switched_on' END,
                    'user', NEW.id::text,
                    jsonb_build_object('management', OLD.management, 'departments', v_departments,
                                       'named', v_named, 'files', v_files, 'lock_grants', v_lock_grants),
                    NEW.id);
        END IF;
        RETURN NEW;
    END
    $$
    """
SWITCHED_COMMENT = (
    "Trigger (PRD 7, #601): a person switched off or on starts with no access: memberships, names on"
    " private folders, files given and lock grants go, Management is cleared; switching off is audited,"
    " and switching on when it cleared something."
)
TRIGGER = (
    "CREATE TRIGGER users_switched BEFORE UPDATE OF disabled ON users FOR EACH ROW"
    " WHEN (OLD.disabled IS DISTINCT FROM NEW.disabled) EXECUTE FUNCTION kaagaz_user_switched()"
)

CHANGE_ADMIN = "kaagaz_op_change_admin(uuid, uuid, uuid, text, bigint)"
SHARE_LINE = "FROM public.customers c WHERE c.id = p_customer FOR SHARE;"
TREE_LINE = "FROM public.customers c WHERE c.id = p_customer FOR NO KEY UPDATE;"

UPGRADE = [
    *FUNCTIONS.values(),
    *[
        statement
        for name, comment in FUNCTION_COMMENTS.items()
        for statement in (
            f"COMMENT ON FUNCTION public.{name} IS {_literal(comment)}",
            f"REVOKE ALL ON FUNCTION public.{name} FROM PUBLIC",
        )
    ],
    *[f"GRANT EXECUTE ON FUNCTION public.{name} TO {ROLES}" for name in GRANTED],
    SWITCHED,
    f"COMMENT ON FUNCTION public.kaagaz_user_switched() IS {_literal(SWITCHED_COMMENT)}",
    "REVOKE ALL ON FUNCTION public.kaagaz_user_switched() FROM PUBLIC",
    TRIGGER,
    _0061._REWRITE.format(function=CHANGE_ADMIN, old=SHARE_LINE, new=TREE_LINE),
]

DOWNGRADE = [
    _0061._REWRITE.format(function=CHANGE_ADMIN, old=TREE_LINE, new=SHARE_LINE),
    "DROP TRIGGER IF EXISTS users_switched ON users",
    "DROP FUNCTION IF EXISTS public.kaagaz_user_switched()",
    *[f"DROP FUNCTION IF EXISTS public.{name}" for name in reversed(FUNCTIONS)],
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
