"""Who can see what: the rules that make walls safe (PRD 7 PR 10c, #601, Tushar, 15 Nov 2026).
NEEDS VRUSHIT'S APPROVAL: security.

* ``kaagaz_place_audience`` (0090) may be asked by Management, and by a person about a place they
  can see (a live folder of theirs stores that department and private folder: a Head inside their
  department, a person filing a file held for them), not only by an Admin: they may make a move
  that changes who can see a file (decision 18), and the move preview shows them who loses and who
  gains it. Its body is 0090's with the caller's check changed, by a checked replace; the check
  reads ``visible_folders`` with the organisation named (an owner skips row level security).
* ``walls_set`` (0089) is set by a private folder too, live or in Trash, as the PRD counts one
  (part 1, "Files in no folder"; the one-time card's ``keeps_files_to_some_people``): 0089's two
  triggers are made again to fire on ``private`` as well, and the flag is worked out once more for
  organisations with one. Then every reader agrees: uploads with no folder, auto-filing's Undo,
  and ``kaagaz_access()``'s ``walled`` (a department deleted since is closed).
* ``kaagaz_give_uploader(document)``: auto-filing gives the uploader the file on every live
  version when it filed it where they can't see it (PRD 7 section 5, "Uploads"), so nobody is
  left unable to see their own upload. Whether they can see it is the access view's own
  predicate (0090's ``DOCUMENT_RULE``, the rule written once); ``given_by`` is NULL (Kaagaz did,
  0084's comment). Only a transaction that names no person may run it (the worker: it reads no
  view, and auto-filing runs there); a person's transaction is refused, insufficient_privilege.
  The caller holds the tree lock; it takes the family lock (0090's ``_LOCK_FAMILY``), the 0085
  order. The house pattern: owner's rights, an empty search path, nothing for PUBLIC, EXECUTE to
  the app and the worker role alike (0083's equality test).

Cost: catalog only, but for the two triggers made again (``folders`` and ``trashed_folders``
locked a moment) and the flag worked out, which reads both tables once. The downgrade puts back
0090's ``kaagaz_place_audience`` and 0089's triggers, and drops the new function; a flag set by a
private folder stays set (it is never cleared).

Revision ID: 0090b
Revises: 0090
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0090b"
down_revision = "0090"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0090b", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0089 = _load("0089_open_paths.py")
_0090 = _load("0090_access_api.py")
_literal = _0090._literal
_replaced = _0090._replaced
ROLES = _0090.ROLES

PLACE = "kaagaz_place_audience(uuid, uuid, boolean)"
_PLACE_0090 = _0090.FUNCTIONS[PLACE].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
PLACE_AUDIENCE = _replaced(
    _PLACE_0090,
    _0090._ADMIN_ONLY,
    """
        IF NOT coalesce((SELECT a.admin OR a.management FROM public.kaagaz_access() a), false)
           AND NOT EXISTS (SELECT 1 FROM public.visible_folders v WHERE v.customer_id = v_customer
                           AND v.department_id IS NOT DISTINCT FROM p_department
                           AND v.private_folder_id IS NOT DISTINCT FROM p_private) THEN
            RAISE EXCEPTION 'only an Admin, Management, or a person who can see the place'
                USING ERRCODE = 'insufficient_privilege';
        END IF;""",
)
PLACE_COMMENT = (
    "Whether each active person sees the files of a place (department, private folder, Department only"
    " as given), for the lose-access lists and the move previews (PRD 7, #601). An Admin, Management, or"
    " a person about a place they can see."
)

GIVE = "kaagaz_give_uploader(uuid)"
GIVE_UPLOADER = f"""
    CREATE FUNCTION public.kaagaz_give_uploader(p_document uuid) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_given integer;
    BEGIN
        IF (public.kaagaz_access()).asked THEN
            RAISE EXCEPTION 'only Kaagaz itself' USING ERRCODE = 'insufficient_privilege';
        END IF;
        {_0090._LOCK_FAMILY}
        WITH live AS (
            SELECT v.id FROM public.documents v
            JOIN public.documents x ON x.customer_id = v.customer_id AND x.family_id = v.family_id
            WHERE x.customer_id = v_customer AND x.id = p_document AND v.deleted_at IS NULL
        ), given AS (
            INSERT INTO public.document_shares AS ds (customer_id, document_id, user_id, given_by)
            SELECT v_customer, l.id, a.person, NULL
            FROM public.documents d
            CROSS JOIN LATERAL public.kaagaz_access_of(d.uploaded_by) a
            CROSS JOIN live l
            WHERE d.customer_id = v_customer AND d.id = p_document AND a.person IS NOT NULL
            AND NOT coalesce({_0090.DOCUMENT_RULE}, false)
            ON CONFLICT DO NOTHING RETURNING ds.document_id
        )
        SELECT count(*) INTO v_given FROM given;
        RETURN v_given;
    END
    $$
    """
GIVE_COMMENT = (
    "Auto-filing (PRD 7, #601): the file given to its uploader on every live version when they can't"
    " see it where it was filed; how many versions were given. Only a transaction with no person (the"
    " worker); the caller holds the tree lock."
)

# 0089's walls_set triggers, firing on Private too.
WALLS = {
    name: (
        table,
        f"AFTER INSERT OR UPDATE OF department_only, private ON {table} FOR EACH ROW"
        " WHEN (NEW.department_only OR NEW.private) EXECUTE FUNCTION kaagaz_walls_set()",
    )
    for name, (table, _) in _0089.TRIGGERS.items()
}
WALLS_COMMENT = (
    "The organisation has kept something to some people: a folder, live or in Trash, was made"
    " Department only or private (set by kaagaz_walls_set, never cleared). Then a department deleted"
    " since is closed (kaagaz_access, #594), and an upload with no folder is placed or held (#601)."
)
FUNCTION_COMMENT = (
    "Trigger (#594, #601): sets customers.walls_set, never clears it, when a folder or a trashed folder"
    " is Department only or private."
)
BACKFILL = (
    "UPDATE customers c SET walls_set = true WHERE NOT c.walls_set AND ("
    " EXISTS (SELECT 1 FROM folders f WHERE f.customer_id = c.id AND f.private)"
    " OR EXISTS (SELECT 1 FROM trashed_folders t WHERE t.customer_id = c.id AND t.private))"
)

UPGRADE = [
    *[f"DROP TRIGGER IF EXISTS {name} ON {table}" for name, (table, _) in WALLS.items()],
    *[f"CREATE TRIGGER {name} {body}" for name, (_, body) in WALLS.items()],
    BACKFILL,
    f"COMMENT ON COLUMN customers.walls_set IS {_literal(WALLS_COMMENT)}",
    f"COMMENT ON FUNCTION public.kaagaz_walls_set() IS {_literal(FUNCTION_COMMENT)}",
    PLACE_AUDIENCE,
    f"COMMENT ON FUNCTION public.{PLACE} IS {_literal(PLACE_COMMENT)}",
    GIVE_UPLOADER,
    f"COMMENT ON FUNCTION public.{GIVE} IS {_literal(GIVE_COMMENT)}",
    f"REVOKE ALL ON FUNCTION public.{GIVE} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION public.{GIVE} TO {ROLES}",
]
DOWNGRADE = [
    f"DROP FUNCTION IF EXISTS public.{GIVE}",
    _PLACE_0090,
    f"COMMENT ON FUNCTION public.{PLACE} IS {_literal(_0090.FUNCTION_COMMENTS[PLACE])}",
    *[f"DROP TRIGGER IF EXISTS {name} ON {table}" for name, (table, _) in WALLS.items()],
    *[f"CREATE TRIGGER {name} {body}" for name, (_, body) in _0089.TRIGGERS.items()],
    f"COMMENT ON COLUMN customers.walls_set IS {_literal(_0089.WALLS_COMMENT)}",
    f"COMMENT ON FUNCTION public.kaagaz_walls_set() IS {_literal(_0089.FUNCTION_COMMENT)}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
