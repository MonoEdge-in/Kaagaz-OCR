"""A failed or never-read copy in Trash never blocks a new upload of the same file (PRD 9 F-03,
decision D3, #680, 19 Nov 2026). NEEDS VRUSHIT'S APPROVAL: a security-definer function's rule.

* ``kaagaz_duplicate(text[], bigint[])`` (0091): the copy it skips (so it reads as ``none`` and the
  upload replaces it) was one refused by its scan, alone in its family (#554). Now also one in
  Trash whose status is ``failed``, ``received``, ``clean`` or ``needs_reader`` and never read (no
  ``ready`` processing run and no page: a read again that failed after a good read keeps its status
  ``failed`` but also its pages, corrections and values), alone in its family, that the person
  sees (``visible_documents``, as the answer's own ``seen``), so a copy they can't see is still ``hidden`` and is never deleted. A ``ready`` copy, or a locked one
  (``locked_at``, whatever its status), is still in. ``db/documents.py``'s ``REFUSED_ALONE`` is
  the same rule for the delete itself.
* The live body is read back and its rule replaced exactly once, or the migration stops (0101's
  way); CREATE OR REPLACE keeps the owner, grants, SECURITY DEFINER and empty search path. Its
  comment says so. Catalog only: no table is locked, read or written.

The downgrade puts 0091's rule and comment back exactly. Nothing is lost.

Revision ID: 0102
Revises: 0101 (the backup reader)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0102", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0091 = _load("0091_database_rule.py")
DUPLICATE = "kaagaz_duplicate(text[], bigint[])"
OLD = _0091._REFUSED_ALONE
_STATUS = "d.status = 'rejected'"
NEW = OLD.replace(
    _STATUS,
    f"({_STATUS} OR (d.deleted_at IS NOT NULL AND d.locked_at IS NULL"
    " AND d.status IN ('failed', 'received', 'clean', 'needs_reader')"
    " AND NOT EXISTS (SELECT 1 FROM public.processing_runs r WHERE r.customer_id = d.customer_id"
    " AND r.document_id = d.id AND r.status = 'ready')"
    " AND NOT EXISTS (SELECT 1 FROM public.pages p WHERE p.customer_id = d.customer_id AND p.document_id = d.id)"
    f" AND {_0091._SEES.format(view='documents', me=_0091._ME, id='d.id')}))",
    1,
)
OLD_COMMENT = _0091.READ_COMMENTS[DUPLICATE]
NEW_COMMENT = OLD_COMMENT.replace(
    "a refused copy alone is not in.",
    "a refused copy alone is not in, nor one in Trash that failed or was never read, alone, that the person"
    " sees (PRD 9 F-03).",
)
_REWRITE = """
DO $body$
DECLARE
    v_def text := pg_get_functiondef('public.{function}'::regprocedure);
BEGIN
    IF coalesce(array_length(string_to_array(v_def, $k${old}$k$), 1), 0) <> 2 THEN
        RAISE EXCEPTION '{function} does not skip a refused copy as 0102 expects';
    END IF;
    EXECUTE replace(v_def, $k${old}$k$, $k${new}$k$);
END
$body$
"""


def _statements(old: str, new: str, comment: str) -> list[str]:
    return [
        _REWRITE.format(function=DUPLICATE, old=old, new=new),
        f"COMMENT ON FUNCTION public.{DUPLICATE} IS {_0091._literal(comment)}",
    ]


def upgrade() -> None:
    for statement in _statements(OLD, NEW, NEW_COMMENT):
        op.execute(statement)


def downgrade() -> None:
    for statement in _statements(NEW, OLD, OLD_COMMENT):
        op.execute(statement)
