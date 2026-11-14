"""Who can see what in Trash: the access view of trashed folders (PRD 7 section 5, "Trash shows each
person only what they could see before", #599, Tushar, 14 Nov 2026). NEEDS VRUSHIT'S APPROVAL:
schema and security.

* ``visible_trashed_folders``: every column of ``trashed_folders``, only the rows the current
  person may see, by the folders' rule of 0086 (R1, R4, R5; an Admin sees every folder's name;
  the worker role with no person, its whole organisation). A trashed folder keeps its stored
  department and private folder (0084), so the rule reads them as it reads a live folder's.
* The rule's text is 0086's own (``_ANYONE`` and ``_PLACE``, loaded from that file), so the rule
  is written once. ``security_invoker`` and no ``security_barrier``, as 0086 (#595).
* Each column carries its table column's comment (the data dictionary, 0007).
* Catalog only: no table is locked, read or written. SELECT to the app and the worker role alike
  (0083's equality test); PUBLIC none.

The downgrade drops the view. PR 9's per-person rule moves to 0088.

Revision ID: 0087
Revises: 0086
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0087", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0086 = _load("0086_access_views.py")

VIEW = (
    "CREATE VIEW public.visible_trashed_folders WITH (security_invoker = true) AS"
    " SELECT t.* FROM public.trashed_folders t, public.kaagaz_access() a"
    f" WHERE {_0086._ANYONE} OR (a.person IS NOT NULL AND (a.management OR a.admin OR "
    + _0086._PLACE.format(t="t", held="")
    + "))"
)
COMMENT = (
    "The folders in Trash the current person may see (PRD 7: R1, R4, R5 on their stored department"
    " and private folder; an Admin, every folder's name); the worker role with no person, its whole"
    " organisation."
)
_COLUMN_COMMENTS = """
    DO $$
    DECLARE
        col record;
    BEGIN
        FOR col IN
            SELECT a.attname, col_description(a.attrelid, a.attnum) AS comment
            FROM pg_attribute a
            WHERE a.attrelid = 'public.trashed_folders'::regclass AND a.attnum > 0 AND NOT a.attisdropped
        LOOP
            EXECUTE format('COMMENT ON COLUMN public.visible_trashed_folders.%I IS %L', col.attname, col.comment);
        END LOOP;
    END
    $$
    """

UPGRADE = [
    VIEW,
    f"COMMENT ON VIEW public.visible_trashed_folders IS {_0086._literal(COMMENT)}",
    f"GRANT SELECT ON public.visible_trashed_folders TO {_0086.ROLES}",
    _COLUMN_COMMENTS,
]
DOWNGRADE = ["DROP VIEW IF EXISTS public.visible_trashed_folders"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
