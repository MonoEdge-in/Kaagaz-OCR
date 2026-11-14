"""Who can see what in Trash: the access view of trashed folders (PRD 7 section 5, "Trash shows each
person only what they could see before", #599, Tushar, 14 Nov 2026), and the rule fails closed for a
department deleted since. NEEDS VRUSHIT'S APPROVAL: schema and security.

* ``visible_trashed_folders``: every column of ``trashed_folders``, only the rows the current
  person may see, by the folders' rule of 0086 (R1, R4, R5; an Admin sees every folder's name;
  the worker role with no person, its whole organisation). A trashed folder keeps its stored
  department and private folder (0084), so the rule reads them as it reads a live folder's.
* The rule's text is 0086's own (``_ANYONE`` and ``_PLACE``, loaded from that file), so the rule
  is written once. ``security_invoker`` and no ``security_barrier``, as 0086 (#595).
* Each column carries its table column's comment (the data dictionary, 0007).
* Fail closed (the lead's decision on #599's review, 14 Nov 2026): a stored department counts as
  open only while it is an open department, live or in Trash. A file or folder in Trash keeps the
  department it had (0085), and that department may be deleted since: 0086 counted an id no
  department has any more as open, so a Department only department's Trash became everyone's. Now
  ``kaagaz_access()`` works out the open departments (``open``, the type's ``closed`` renamed) and
  every view reads ``department_id = ANY (a.open)`` where 0086 read ``<> ALL (a.closed)``: one
  change to 0086's rule text, made here once for the three views. A deleted department's rows are
  seen only by Management, a person given a file or holding it (R2, R3), the department's head and
  named people of a private folder in it (R4), and, for folders, an Admin.
* Catalog only: no table is locked, read or written. SELECT to the app and the worker role alike
  (0083's equality test); PUBLIC none.

The downgrade drops the view and puts back 0086's function and views. PR 9's per-person rule moves
to 0088.

Revision ID: 0087
Revises: 0086
"""

import importlib.util
import re
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


def _replaced(text: str, old: str, new: str, times: int = 1) -> str:
    if text.count(old) != times:
        raise RuntimeError(f"0086's text changed: {old!r}")
    return text.replace(old, new)


def _fail_closed(view: str) -> str:
    """0086's view text with the one change: an open department is one still there and open."""
    closed = re.compile(r"(\w+)\.department_id <> ALL \(a\.closed\)")
    if len(closed.findall(view)) != 1:
        raise RuntimeError("0086's rule text changed")
    return _replaced(
        closed.sub(r"\1.department_id = ANY (a.open)", view), "CREATE VIEW", "CREATE OR REPLACE VIEW"
    )


_ACCESS_0086 = _0086.FUNCTIONS["kaagaz_access"].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
# The departments still there (live or in Trash) and open, where 0086 listed the Department only ones.
ACCESS = _replaced(
    _replaced(
        _ACCESS_0086,
        "f.parent_id IS NULL AND f.department_only",
        "f.parent_id IS NULL AND f.kind = 'department' AND NOT f.department_only",
    ),
    "t.parent_id IS NULL AND t.department_only",
    "t.parent_id IS NULL AND t.kind = 'department' AND NOT t.department_only",
)
ACCESS_COMMENT = (
    "The current person's access, for the access views (PRD 7): who they are (NULL: no active user of"
    " this customer), whether one is named, Management, Admin, departments, heads, named private"
    " folders, the organisation's open departments (live or in Trash; a department deleted since is"
    " not one). Arrays are empty, never NULL."
)
VIEWS = {name: _fail_closed(view) for name, view in _0086.VIEWS.items()}
VIEW = _fail_closed(
    "CREATE VIEW public.visible_trashed_folders WITH (security_invoker = true) AS"
    " SELECT t.* FROM public.trashed_folders t, public.kaagaz_access() a"
    f" WHERE {_0086._ANYONE} OR (a.person IS NOT NULL AND (a.management OR a.admin OR "
    + _0086._PLACE.format(t="t", held="")
    + "))"
).replace("CREATE OR REPLACE VIEW", "CREATE VIEW")
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

_RENAME = "ALTER TYPE public.kaagaz_access_set RENAME ATTRIBUTE {} TO {}"
UPGRADE = [
    _RENAME.format("closed", "open"),
    ACCESS,
    f"COMMENT ON FUNCTION public.kaagaz_access() IS {_0086._literal(ACCESS_COMMENT)}",
    *VIEWS.values(),
    VIEW,
    f"COMMENT ON VIEW public.visible_trashed_folders IS {_0086._literal(COMMENT)}",
    f"GRANT SELECT ON public.visible_trashed_folders TO {_0086.ROLES}",
    _COLUMN_COMMENTS,
]
DOWNGRADE = [
    "DROP VIEW IF EXISTS public.visible_trashed_folders",
    _RENAME.format("open", "closed"),
    _ACCESS_0086,
    f"COMMENT ON FUNCTION public.kaagaz_access() IS {_0086._literal(_0086.FUNCTION_COMMENTS['kaagaz_access'])}",
    *[view.replace("CREATE VIEW", "CREATE OR REPLACE VIEW") for view in _0086.VIEWS.values()],
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
