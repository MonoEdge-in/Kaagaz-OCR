"""Removing a customer whose deletion was scheduled (0018, super admin PR 4, PROVISIONAL).

* ``ids_to_purge`` and ``finish_delete`` call 0018's two owner-rights functions on a
  ``Database.system()`` connection: the first gives ids only, the second deletes ONE
  customers row that is being deleted, due on the database clock and has no document.
* ``next_family_to_purge`` runs inside ``Database.customer(customer_id)``, so row level
  security applies to every row it deletes (by today's ``delete_forever``).
* ``purge_now`` is the CLI's break-glass, on an OWNER connection: ``kaagaz_app`` may not
  change ``delete_after`` (0014).
* ``ids_to_sweep`` and ``mark_swept`` (0019, PR 4b): the second sweep of a purged
  customer's storage, a day later, on a ``Database.system()`` connection (ids only).
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection, text

from kaagaz.db.documents import delete_forever
from kaagaz.db.trash import lock_family
from kaagaz.ingestion.ports import BlobStorage

_TO_PURGE = text("SELECT t.id FROM kaagaz_customer_ids_to_purge(:now) AS t (id)")
# Still being deleted and due, read FOR SHARE: a cancel (FOR UPDATE) waits for this family.
_DUE = text(
    "SELECT 1 FROM customers WHERE id = :c AND status = 'deleting' AND delete_after <= :now FOR SHARE"
)
_ANY_DOCUMENT = text("SELECT id FROM documents WHERE customer_id = :c ORDER BY id LIMIT 1")
_VERSIONS = text(
    "SELECT id FROM documents WHERE customer_id = :c AND family_id = :f ORDER BY (id <> family_id), id"
)
_LOCK = text("SELECT status FROM customers WHERE id = :c FOR UPDATE")
# The grace period ends now: by both the caller's clock and the database's (0018's finish).
_PURGE_NOW = text("UPDATE customers SET delete_after = least(now(), :now) WHERE id = :c")
_TO_SWEEP = text("SELECT t.id FROM kaagaz_purged_ids_to_sweep(:now) AS t (id)")


def ids_to_purge(conn: Connection, *, now: datetime) -> list[str]:
    """Ids of the customers being deleted whose ``delete_after`` is not after ``now``,
    longest due first. ``conn`` from ``Database.system()``. O(C) for C customers."""
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


def purge_now(conn: Connection, customer_id: str, *, now: datetime) -> str | None:
    """End the grace period of a customer being deleted at ``now``, on an OWNER connection
    (the CLI's break-glass). Returns its status: None when there is no such customer; only
    a customer being deleted changes. O(1)."""
    status = conn.execute(_LOCK, {"c": customer_id}).scalar()
    if status == "deleting":
        conn.execute(_PURGE_NOW, {"c": customer_id, "now": now})
    return None if status is None else str(status)


def ids_to_sweep(conn: Connection, *, now: datetime) -> list[str]:
    """Ids of the customers purged at least a day before ``now`` and not swept yet (0019).
    ``conn`` from ``Database.system()``. O(P) for the P platform audit rows."""
    return [str(row[0]) for row in conn.execute(_TO_SWEEP, {"now": now})]


def mark_swept(conn: Connection, customer_id: str) -> bool:
    """Write ``org.swept`` for a purged customer whose prefixes were emptied again. False:
    not purged, swept already or still there. ``conn`` from ``Database.system()``."""
    return bool(conn.execute(text("SELECT kaagaz_customer_swept(:c)"), {"c": customer_id}).scalar())
