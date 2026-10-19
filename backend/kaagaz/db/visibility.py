"""Which documents a statement may see: the fixed SQL fragments (PRD 1 WP1.2, WP1.6).

A document in Trash, or an older version of a family, must never reach a list,
a search or a cited answer (CLAUDE.md "the source on every answer"). Every
statement that joins ``documents d`` for those uses one of these fragments.

**Write this text literally in each SQL constant.** Do not join it with ``+``
or put it into an f-string: ruff's S608 refuses SQL built from strings, and a
guard test (WP1.6) checks that each listed constant contains the fragment text
with ``in``. The text is fixed, and a test asserts it exactly. The alias is
always ``d``.

* ``LIVE``: not in Trash, any version. Kept for statements that must see
  every version of a live family.
* ``LISTED``: not in Trash, the current version, in any state. The document
  list and the admin numbers: one row per family, the version search and chat
  use (a newer one still reading or failed is shown beside it, never instead).
* ``LIVE_CURRENT``: not in Trash, the current version, and read. Search,
  chat, the fixed queries and review, inside every CTE and before any LIMIT.
* ``TRASHED``: in Trash, any version. The purge.
* ``TRASHED_LISTED``: in Trash, the current version. The Trash view.

Lookups by id get no filter: a trashed document can still be opened, restored
or deleted forever.

``LIVE_SUGGESTION`` (File management V5, section 10; alias ``s``): a version suggestion still
to answer, with its new document and its candidate family both out of Trash, so one whose
side was trashed or deleted disappears. Its statement binds the customer as ``:c``.
"""

LIVE = " AND d.deleted_at IS NULL"
LISTED = " AND d.deleted_at IS NULL AND d.is_current"
LIVE_CURRENT = " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
TRASHED = " AND d.deleted_at IS NOT NULL"
TRASHED_LISTED = " AND d.deleted_at IS NOT NULL AND d.is_current"
LIVE_SUGGESTION = (
    "s.status = 'pending' AND s.outcome = 'suggested'"
    " AND EXISTS (SELECT 1 FROM documents sn WHERE sn.customer_id = :c AND sn.id = s.document_id"
    " AND sn.deleted_at IS NULL)"
    " AND EXISTS (SELECT 1 FROM documents sc WHERE sc.customer_id = :c"
    " AND sc.family_id = s.candidate_family_id AND sc.deleted_at IS NULL)"
)
