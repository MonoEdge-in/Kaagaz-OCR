"""Delete for good what has been in Trash longer than ``TRASH_DAYS`` (PRD 1 F5).

The worker runs ``purge_expired`` at most once an hour (kaagaz/worker_main.py).

How it stays inside the customer walls: a transaction with no current customer
sees no customer's rows (row level security), so the customers with old Trash
come from the one SECURITY DEFINER function, ``kaagaz_customer_ids_with_trash``
(ids only), through ``Database.system()``. Everything else runs inside
``Database.customer(customer_id)``, one customer at a time, and the helpers
filter on ``customer_id`` as well.

One family per transaction: ``purge_next_family`` locks the family longest in
Trash (a family another purger or a Trash action holds is skipped, never waited
for), then every version's rows and stored files are deleted in that same
transaction, by today's full delete (``kaagaz.db.documents.delete_forever``).

A storage error rolls that family's rows back and **stops the run**: the
BackendUnavailable goes up to the worker loop, which logs it and pauses, and
the next run (an hour later) starts again from the oldest family. Families
purged before the error stay purged. Storage has no transactions, so files
deleted just before the error stay deleted; the next run deletes the rest.

Time comes from the injected clock, never SQL ``now()``.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from kaagaz.db.documents import delete_forever
from kaagaz.db.engine import Database, unavailable
from kaagaz.db.trash import customer_ids_with_trash, purge_next_family
from kaagaz.ingestion.ports import BlobStorage


def purge_expired(
    db: Database, storage: BlobStorage, clock: Callable[[], datetime], trash_days: int, batch: int = 100
) -> int:
    """Delete up to ``batch`` families that went to Trash more than ``trash_days``
    days before ``clock()``, oldest first per customer. Returns how many.

    Cost: one statement for the customer list, O(T) over all T trashed rows;
    then for each family one transaction of 2 lock statements plus one delete
    statement and one storage delete per version, so O(batch * v) statements
    for families of v versions, and one more pick per customer that runs out.
    Memory O(C) for the C customers listed.
    """
    now = clock()
    if now.tzinfo is None:
        raise ValueError("the clock must give a time with a time zone")
    before = now - timedelta(days=trash_days)
    purged = 0
    with unavailable():
        with db.system() as conn:
            customers = customer_ids_with_trash(conn, before=before)
        for customer_id in customers:
            while purged < batch and _purge_one_family(db, storage, customer_id, before):
                purged += 1
    return purged


def _purge_one_family(db: Database, storage: BlobStorage, customer_id: str, before: datetime) -> bool:
    """One family of this customer, rows and files, in one transaction. False if none is left."""
    with db.customer(customer_id) as conn:
        document_ids = purge_next_family(conn, customer_id, before=before)
        for document_id in document_ids:
            delete_forever(conn, storage, customer_id, document_id)
    return bool(document_ids)
