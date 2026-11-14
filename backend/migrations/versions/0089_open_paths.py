"""Nothing opens to everyone by accident (#594, Tushar, 14 Nov 2026). NEEDS VRUSHIT'S APPROVAL:
schema and security.

* ``version_suggestions.previous_held_for``: who the file was held for when a link took it into its
  family (``kaagaz.db.versions.link``), so its Undo holds it for that person again instead of
  leaving it open to everyone. Keyed to ``users`` with the organisation, as
  ``documents_held_for_fk``. Rows linked before hold NULL, which is right: until #594 nothing in
  the app held a file. The table-wide grant (0041) covers the app and the worker role.
* ``customers.walls_set`` (the lead's addition to #594): the organisation has kept something to
  some people. ``kaagaz_walls_set`` sets it, and nothing clears it, when a folder or a trashed folder
  gets Department only. ``kaagaz_access()`` is 0087's with its ``walled`` read from this flag where
  0087 asked whether a Department only department exists, so a Department only department deleted
  for good no longer lets the files it left in Trash fall back to open (0087's known gap). Day one
  nothing changes: the flag starts false, and is set now for an organisation that has one.
* The trigger function follows the house pattern (0085): the owner's rights, an empty search path,
  no right for PUBLIC or any role. It takes the organisation's row, which is the app's tree lock
  (``kaagaz.db.folders._lock_tree``): every folder writer takes that first, and 0085's folder trigger
  takes it after the folder rows too, so the lock order is 0085's. It writes only while the flag is
  false, so it locks nothing once set.

Cost: the key is added NOT VALID (0084's pattern), then validated in the same transaction: the
column is new, so the check reads ``version_suggestions`` once and finds nothing, while it and
``users`` are locked against writes for that moment. ``walls_set`` has a constant default (no
rewrite); the triggers are created before the flag is worked out, so a folder made Department only
meanwhile is counted either way; working it out reads ``folders`` and ``trashed_folders`` once.

The downgrade puts back 0087's function, drops the triggers, the function and both columns; what
they held is lost (an Undo then leaves a held file in no folder, as before #594).

Revision ID: 0089
Revises: 0088
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0089", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0087 = _load("0087_trashed_folders_view.py")
_literal = _0087._0086._literal
KEY = "version_suggestions_previous_held_for_fk"
HELD_COMMENT = (
    "users.id (same customer) the document was held for before it was linked (none: not held),"
    " whom Undo holds it for again when it goes back to no folder (#594)"
)
WALLS_COMMENT = (
    "The organisation has kept something to some people: a folder, live or in Trash, was made"
    " Department only (set by kaagaz_walls_set, never cleared). Then a department deleted since is"
    " closed (kaagaz_access, #594)."
)

# 0087's function, its ``walled`` from the flag instead of the EXISTS on today's departments.
_WALLED_0087 = "EXISTS (" + _0087._CLOSED + ")"
ACCESS = _0087._replaced(
    _0087.ACCESS,
    _WALLED_0087,
    "coalesce((SELECT w.walls_set FROM public.customers w WHERE w.id = c.id), false)",
)
ACCESS_COMMENT = (
    "The current person's access, for the access views (PRD 7): who they are (NULL: no active user of"
    " this customer), whether one is named, Management, Admin, departments, heads, named private"
    " folders, the organisation's open departments (live or in Trash), whether it has ever kept"
    " something to some people (walled, customers.walls_set: then a department deleted since is"
    " closed). Arrays are empty, never NULL."
)
FUNCTION = """
    CREATE FUNCTION public.kaagaz_walls_set() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    BEGIN
        -- The organisation's row: the app's tree lock, taken first by every folder writer (0085).
        UPDATE public.customers c SET walls_set = true WHERE c.id = NEW.customer_id AND NOT c.walls_set;
        RETURN NULL;
    END
    $$
    """
FUNCTION_COMMENT = (
    "Trigger (#594): sets customers.walls_set, never clears it, when a folder or a trashed folder is"
    " Department only."
)
TABLES = ("folders", "trashed_folders")
TRIGGERS = {
    f"{table}_walls_set": (
        table,
        f"AFTER INSERT OR UPDATE OF department_only ON {table} FOR EACH ROW WHEN (NEW.department_only)"
        " EXECUTE FUNCTION kaagaz_walls_set()",
    )
    for table in TABLES
}
BACKFILL = (
    "UPDATE customers c SET walls_set = true WHERE"
    " EXISTS (SELECT 1 FROM folders f WHERE f.customer_id = c.id AND f.department_only)"
    " OR EXISTS (SELECT 1 FROM trashed_folders t WHERE t.customer_id = c.id AND t.department_only)"
)

UPGRADE = [
    "ALTER TABLE version_suggestions ADD COLUMN previous_held_for uuid NULL,"
    f" ADD CONSTRAINT {KEY} FOREIGN KEY (customer_id, previous_held_for) REFERENCES users (customer_id, id)"
    " NOT VALID",
    f"ALTER TABLE version_suggestions VALIDATE CONSTRAINT {KEY}",
    f"COMMENT ON COLUMN version_suggestions.previous_held_for IS {_literal(HELD_COMMENT)}",
    "ALTER TABLE customers ADD COLUMN walls_set boolean NOT NULL DEFAULT false",
    f"COMMENT ON COLUMN customers.walls_set IS {_literal(WALLS_COMMENT)}",
    FUNCTION,
    f"COMMENT ON FUNCTION public.kaagaz_walls_set() IS {_literal(FUNCTION_COMMENT)}",
    "REVOKE ALL ON FUNCTION public.kaagaz_walls_set() FROM PUBLIC",
    *[f"CREATE TRIGGER {name} {body}" for name, (_, body) in TRIGGERS.items()],
    BACKFILL,
    ACCESS,
    f"COMMENT ON FUNCTION public.kaagaz_access() IS {_literal(ACCESS_COMMENT)}",
]
DOWNGRADE = [
    _0087.ACCESS,
    f"COMMENT ON FUNCTION public.kaagaz_access() IS {_literal(_0087.ACCESS_COMMENT)}",
    *[f"DROP TRIGGER IF EXISTS {name} ON {table}" for name, (table, _) in TRIGGERS.items()],
    "DROP FUNCTION IF EXISTS public.kaagaz_walls_set()",
    "ALTER TABLE customers DROP COLUMN IF EXISTS walls_set",
    "ALTER TABLE version_suggestions DROP COLUMN IF EXISTS previous_held_for",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
