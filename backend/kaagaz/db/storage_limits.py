"""Storage limit per organisation (#516, Tushar, 5 Nov 2026): how much an organisation may store
and how much it holds now.

* **The limit**: the organisation's own (``org_storage_limits``), else the platform's
  (``platform_ai_settings.storage_gb``), else ``DEFAULT_STORAGE_GB``. The super admin sets
  them (``kaagaz_op_set_storage_limit``, 0061d), 1 to ``MOST_STORAGE_GB``.
* **The bytes used**: the sum of the organisation's ``documents.size_bytes``: every uploaded
  file, every version, and the Trash until it is purged (a purge deletes the row). Page images
  are not counted.
* A new upload past the limit is refused ``storage_full`` (the upload service, the resumable
  start and the folder upload plan); nothing already stored is touched.

``storage_room`` takes a connection from ``Database.customer(customer_id)`` and also filters on
``customer_id`` (two locks, docs/architecture.md). One statement; the sum walks the
organisation's document rows by an index that starts with ``customer_id``
(``documents_listing``, 0001). shortcut: O(D) for the organisation's D document rows on each
upload; keep a running total per organisation if one holds millions of rows.
"""

from sqlalchemy import Connection, text

from kaagaz.ingestion.ports import StorageRoom

GIB = 1024**3
# The server's default (Tushar, 5 Nov 2026) and the most the super admin may set (0061d's CHECKs).
DEFAULT_STORAGE_GB = 50
MOST_STORAGE_GB = 10_000

_ROOM = text(
    "SELECT coalesce(o.storage_gb, p.storage_gb, :gb),"
    " (SELECT coalesce(sum(d.size_bytes), 0) FROM documents d WHERE d.customer_id = :c)"
    " FROM platform_ai_settings p LEFT JOIN org_storage_limits o ON o.customer_id = :c"
)


def storage_room(conn: Connection, customer_id: str) -> StorageRoom:
    """This organisation's storage limit and the bytes its documents hold now."""
    gb, used = conn.execute(_ROOM, {"c": customer_id, "gb": DEFAULT_STORAGE_GB}).one()
    return StorageRoom(limit_bytes=int(gb) * GIB, used_bytes=int(used))
