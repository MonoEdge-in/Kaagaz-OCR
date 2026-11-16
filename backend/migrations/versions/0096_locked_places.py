"""Locked files' pop-ups: an Admin's names of a place's locked files and of a person's (PRD 7 section
6, "Giving a folder or department that holds locked files", and the Access page's "Locked files";
PR 13, #605, Tushar, 16 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema and security.

0091's per-person rule hides from an Admin the rows of a file they can't open, a locked one not
given to them among them. The give pop-up and the Access page show such files by their true names
(decision 20), so they read them through owner-rights functions, as 0091's
``kaagaz_admin_given_to`` does for files given:

* ``kaagaz_admin_place_files(department, private, head)``: how many files a place gives, the locked
  ones too: a private folder's (``private``), else the department's outside its private folders
  (and, for its Head, inside them as well); current versions out of Trash, none held for its
  uploader.
* ``kaagaz_admin_place_locked(department, private, head, user)``: the place's locked files by name,
  with whether ``user`` may open each already (a lock grant).
* ``kaagaz_admin_locked_for(user)``: the locked files a person may open, one a family (its current
  version), with when and by whom.

Each follows the house pattern (0091's ``_AN_ADMIN``): SECURITY DEFINER, an empty search path, every
table named in full and filtered on ``kaagaz_current_customer()``, the caller checked inside (an
Admin of this organisation; anyone else insufficient_privilege), nothing for PUBLIC, EXECUTE to the
app and the worker role alike (0083's equality test). Names and ids only, never content. Catalog
only: no table is locked, read or written. The downgrade drops them.

Revision ID: 0096
Revises: 0095 (each model's supported parameters)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0096", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0091 = _load("0091_database_rule.py")
ROLES = _0091.ROLES
_literal = _0091._literal
_HEAD = _0091._ADMIN_HEAD
_AN_ADMIN = _0091._AN_ADMIN
_ME = _0091._ME
# The files a place gives (R5, and R4 for its Head; a private folder's: R4), for alias d.
_PLACE = (
    f"d.customer_id = {_ME} AND d.deleted_at IS NULL AND d.is_current AND d.held_for IS NULL"
    " AND CASE WHEN p_private IS NOT NULL THEN d.private_folder_id = p_private"
    " ELSE d.department_id = p_department AND (d.private_folder_id IS NULL OR p_head) END"
)

FUNCTIONS = {
    "kaagaz_admin_place_files(uuid, uuid, boolean)": f"""
    CREATE FUNCTION public.kaagaz_admin_place_files(p_department uuid, p_private uuid, p_head boolean)
    RETURNS bigint
    {_HEAD}{_AN_ADMIN}
        RETURN (SELECT count(*) FROM public.documents d WHERE {_PLACE});
    END
    $$
    """,
    "kaagaz_admin_place_locked(uuid, uuid, boolean, uuid)": f"""
    CREATE FUNCTION public.kaagaz_admin_place_locked(
        p_department uuid, p_private uuid, p_head boolean, p_user uuid
    )
    RETURNS TABLE (id uuid, display_name text, folder_id uuid, open boolean)
    {_HEAD}{_AN_ADMIN}
        RETURN QUERY SELECT d.id, d.display_name, d.folder_id, EXISTS (
            SELECT 1 FROM public.lock_grants g
            WHERE g.customer_id = d.customer_id AND g.document_id = d.id AND g.user_id = p_user)
        FROM public.documents d WHERE {_PLACE} AND d.locked_at IS NOT NULL
        ORDER BY lower(d.display_name), d.id;
    END
    $$
    """,
    "kaagaz_admin_locked_for(uuid)": f"""
    CREATE FUNCTION public.kaagaz_admin_locked_for(p_user uuid)
    RETURNS TABLE (id uuid, display_name text, folder_id uuid, given_at timestamptz, given_by uuid)
    {_HEAD}{_AN_ADMIN}
        RETURN QUERY SELECT DISTINCT ON (c.family_id) c.id, c.display_name, c.folder_id, g.given_at, g.given_by
        FROM public.lock_grants g
        JOIN public.documents v ON v.customer_id = g.customer_id AND v.id = g.document_id
        JOIN public.documents c ON c.customer_id = v.customer_id AND c.family_id = v.family_id AND c.is_current
        WHERE g.customer_id = {_ME} AND g.user_id = p_user AND c.deleted_at IS NULL AND c.locked_at IS NOT NULL
        ORDER BY c.family_id, g.given_at;
    END
    $$
    """,
}
_NAMES = " An Admin; names and ids only (PRD 7 decision 20, #605)."
FUNCTION_COMMENTS = {
    "kaagaz_admin_place_files(uuid, uuid, boolean)": "How many files a place gives (a private folder's, or a"
    " department's outside its private folders, inside them too for its Head), the locked ones too: the give"
    " pop-up." + _NAMES,
    "kaagaz_admin_place_locked(uuid, uuid, boolean, uuid)": "A place's locked files by name, with whether the"
    " person may open each already: the give pop-up." + _NAMES,
    "kaagaz_admin_locked_for(uuid)": "The locked files a person may open, one per family: the Access page."
    + _NAMES,
}

UPGRADE = [
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
DOWNGRADE = [f"DROP FUNCTION IF EXISTS public.{name}" for name in reversed(FUNCTIONS)]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
