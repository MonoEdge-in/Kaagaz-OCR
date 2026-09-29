"""Which documents a statement may see: the fixed SQL fragments (PRD 1 WP1.2, WP1.6).

A document in Trash, or an older version of a family, must never reach a list,
a search or a cited answer (CLAUDE.md "the source on every answer"). Every
statement that joins ``documents d`` for those uses one of these fragments.

**Write this text literally in each SQL constant.** Do not join it with ``+``
or put it into an f-string: ruff's S608 refuses SQL built from strings, and a
guard test (WP1.6) checks that each listed constant contains the fragment text
with ``in``. The text is fixed, and a test asserts it exactly. The alias is
always ``d``.

* ``LIVE``: not in Trash. The document list (default view) and review.
* ``LIVE_CURRENT``: not in Trash, the current version, and read. Search,
  chat and the fixed queries, inside every CTE and before any LIMIT.
* ``TRASHED``: in Trash. The Trash view and the purge.

Lookups by id get no filter: a trashed document can still be opened, restored
or deleted forever.
"""

LIVE = " AND d.deleted_at IS NULL"
LIVE_CURRENT = " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
TRASHED = " AND d.deleted_at IS NOT NULL"
