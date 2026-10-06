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

A customer that is disabled or being deleted (0014) is purged all the same, on
purpose: the purge only deletes and calls no model, and what its users put in
Trash still goes when they were told it would (super admin plan, 6 Oct 2026).

Time comes from the injected clock, never SQL ``now()``.

Deleted organisations (super admin PR 4, 0018, PROVISIONAL): ``purge_deleted_customers``
runs in the same hourly turn. The customers whose grace period has ended come from
``kaagaz_customer_ids_to_purge`` (ids only). For each, one family per transaction inside
``Database.customer``: the customer is checked still being deleted and due (a cancel
waits for the family), then every version goes by ``delete_forever``, rows and files.
At most ``CUSTOMER_PURGE_FAMILIES`` families a run; the next run goes on where it
stopped. With no document left, ``delete_customer`` removes both storage prefixes, then
``kaagaz_finish_customer_delete`` the customers row (everything left cascades) and writes
``org.purged``. A storage error rolls back that family and stops the run, as above; the
prefixes and the row are only touched once every family is gone, and both steps are safe
to run again. ``purge_customer`` is that same work for one customer, which the CLI's
break-glass ``purge-customer --now`` also runs.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from kaagaz.db import customer_purge
from kaagaz.db.documents import delete_forever
from kaagaz.db.engine import Database, unavailable
from kaagaz.db.trash import customer_ids_with_trash, purge_next_family
from kaagaz.ingestion.ports import BlobStorage

logger = logging.getLogger(__name__)

# PROVISIONAL: the most families of deleted organisations one hourly run removes, so a
# large organisation never holds the jobs up for long; the next run goes on.
CUSTOMER_PURGE_FAMILIES = 500


class CustomerStorage(BlobStorage, Protocol):
    def delete_customer(self, customer_id: str) -> None:
        """Remove everything stored for this customer (both prefixes). Safe to repeat."""
        ...


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


def purge_deleted_customers(
    db: Database,
    storage: CustomerStorage,
    clock: Callable[[], datetime],
    batch: int = CUSTOMER_PURGE_FAMILIES,
) -> int:
    """Remove the organisations whose deletion is due at ``clock()``: at most ``batch``
    document families in all, oldest due first. Returns how many families went.

    Cost: one statement for the id list, O(C); then per family one transaction of
    O(v) statements and storage deletes; per finished customer one paged listing and
    delete of its k stored objects and one function call."""
    now = clock()
    if now.tzinfo is None:
        raise ValueError("the clock must give a time with a time zone")
    purged = 0
    with unavailable(), db.system() as conn:
        customers = customer_purge.ids_to_purge(conn, now=now)
    for customer_id in customers:
        if purged >= batch:
            break
        families, finished = purge_customer(db, storage, customer_id, now=now, most=batch - purged)
        purged += families
        if finished:
            logger.info("organisation purged", extra={"customer_id": customer_id})
    return purged


def purge_customer(
    db: Database, storage: CustomerStorage, customer_id: str, *, now: datetime, most: int
) -> tuple[int, bool]:
    """Remove up to ``most`` families of one customer being deleted and due at ``now``;
    once none is left, its two storage prefixes and its customers row. Returns (families
    removed, whether the customer is gone). Nothing happens unless it is being deleted
    and due."""
    families = 0
    with unavailable():
        while families < most:
            with db.customer(customer_id) as conn:
                removed = customer_purge.next_family_to_purge(conn, storage, customer_id, now=now)
            if removed is None:  # not due, cancelled or gone: nothing more to do
                return families, False
            if not removed:
                storage.delete_customer(customer_id)
                with db.system() as conn:
                    return families, customer_purge.finish_delete(conn, customer_id)
            families += 1
    return families, False
