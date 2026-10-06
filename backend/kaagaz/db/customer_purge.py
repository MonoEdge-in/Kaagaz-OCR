"""Removing a customer whose deletion was scheduled (0018, super admin PR 4, PROVISIONAL).

* ``ids_to_purge`` and ``finish_delete`` call 0018's two owner-rights functions on a
  ``Database.system()`` connection: the first gives ids only, the second deletes ONE
  customers row that is being deleted, due on the database clock and has no document.
* ``next_family_to_purge`` runs inside ``Database.customer(customer_id)``, so row level
  security applies to every row it deletes (by today's ``delete_forever``).
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection, text

from kaagaz.db.documents import delete_forever
from kaagaz.db.trash import lock_family
from kaagaz.ingestion.ports import BlobStorage

_TO_PURGE = text("SELECT t.id FROM kaagaz_customer_ids_to_purge(:now) AS t (id)")
# Still being deleted and due by the caller's clock AND the database's (cancel's clock, so a
# worker clock running ahead never deletes what a cancel could still give back), read FOR
# SHARE: a cancel (FOR UPDATE) waits for this family.
_DUE = text(
    "SELECT 1 FROM customers WHERE id = :c AND status = 'deleting'"
    " AND delete_after <= least(CAST(:now AS timestamptz), now()) FOR SHARE"
)
_ANY_DOCUMENT = text("SELECT id FROM documents WHERE customer_id = :c ORDER BY id LIMIT 1")
_VERSIONS = text(
    "SELECT id FROM documents WHERE customer_id = :c AND family_id = :f ORDER BY (id <> family_id), id"
)


def ids_to_purge(conn: Connection, *, now: datetime) -> list[str]:
    """Ids of the customers being deleted whose ``delete_after`` is not after ``now`` nor
    the database clock, longest due first. ``conn`` from ``Database.system()``. O(C) for C customers."""
    return [str(row[0]) for row in conn.execute(_TO_PURGE, {"now": now})]


def next_family_to_purge(
    conn: Connection, storage: BlobStorage, customer_id: str, *, now: datetime
) -> Sequence[str] | None:
    """Delete one whole document family of a customer being deleted, rows and files, by
    ``delete_forever``, and return its document ids: empty when no document is left;
    None when the customer is not being deleted or not due (cancelled, or gone), and
    then nothing is deleted. ``conn`` from ``Database.customer(customer_id)``: when
    storage fails, the family's rows come back. The family lock is waited for, never
    skipped. O(v) statements and storage deletes for the family's v versions."""
    if conn.execute(_DUE, {"c": customer_id, "now": now}).first() is None:
        return None
    family = None
    while family is None:  # None: that document went meanwhile (the Trash purge), pick again
        first = conn.execute(_ANY_DOCUMENT, {"c": customer_id}).scalar()
        if first is None:
            return []
        family = lock_family(conn, customer_id, str(first))
    ids = [str(row[0]) for row in conn.execute(_VERSIONS, {"c": customer_id, "f": family})]
    for document_id in ids:
        delete_forever(conn, storage, customer_id, document_id)
    return ids


def finish_delete(conn: Connection, customer_id: str) -> bool:
    """Delete the customers row, and with it every row left of the customer, and write
    ``org.purged``: only when it is being deleted, due on the database clock and has no
    document. False: nothing was done. ``conn`` from ``Database.system()``. O(rows left)."""
    return bool(conn.execute(text("SELECT kaagaz_finish_customer_delete(:c)"), {"c": customer_id}).scalar())
