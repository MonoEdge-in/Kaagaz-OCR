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
transaction, by today's full delete (``kaagaz.db.documents.delete_forever``). Then, in its own
transaction, the folders in Trash past the limit whose documents are all gone (#542,
``trashed_folders.purge_folders``), so no document is left naming a trashed folder.

A storage error rolls that family's rows back and **stops the run**: the
BackendUnavailable goes up to the worker loop, which logs it and pauses, and
the next run (an hour later) starts again from the oldest family. Families
purged before the error stay purged. Storage has no transactions, so files
deleted just before the error stay deleted; the next run deletes the rest.

A customer that is disabled or being deleted (0014) is purged all the same, on
purpose: the purge only deletes and calls no model, and what its users put in
Trash still goes when they were told it would (super admin plan, 6 Oct 2026).

Time comes from the injected clock, never SQL ``now()``.

Deleted organisations (super admin PR 4, 0018, PROVISIONAL), in the same hourly turn, for
at most ``CUSTOMER_PURGE_SECONDS``: ids from ``kaagaz_customer_ids_to_purge``; one family
per transaction inside ``Database.customer`` (still deleting and due on both clocks; a
cancel waits); with no document left, ``delete_customer`` empties both prefixes and
``kaagaz_finish_customer_delete`` removes the customers row and writes ``org.purged``.
A storage error rolls that family back and ends that organisation's turn (the next goes
on, the database down ends the run); the next run resumes; the last two steps repeat safely.
A day after each purge, ``sweep_purged_customers`` empties both prefixes once more (a
page image a running job wrote meanwhile) and records ``org.swept`` (0019, PR 4b).

Before the prefixes are emptied, every resumable upload still open is aborted (#482, 3 Nov
2026): its parts are no object yet, so the prefix delete can't see them. The finish keeps the
organisation's audit rows (``audit_log_kept``, 0068); ``forget_kept_audit``, in the same
hourly turn, removes them 365 days after the deletion (Tushar, 3 Nov 2026).
"""

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy.exc import SQLAlchemyError

from kaagaz.db import customer_purge
from kaagaz.db import trashed_folders as trashed_sql
from kaagaz.db import upload_sessions as upload_sessions_sql
from kaagaz.db import users as users_sql
from kaagaz.db import whatsapp as whatsapp_sql
from kaagaz.db.documents import delete_forever
from kaagaz.db.engine import Database, unavailable
from kaagaz.db.trash import customer_ids_with_trash, purge_next_family
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobStorage

logger = logging.getLogger(__name__)

# PROVISIONAL: how long one hourly run removes deleted organisations' families (the jobs
# wait meanwhile; the family in hand is finished first). The next run goes on: at about
# 10 families a second, 100,000 families take some 34 runs.
CUSTOMER_PURGE_SECONDS = 300


class CustomerStorage(BlobStorage, Protocol):
    def delete_customer(self, customer_id: str) -> None:
        """Remove everything stored for this customer (both prefixes). Safe to repeat."""
        ...

    def abort_resumable(self, key: str, upload_id: str) -> None: ...

    def delete_resumable(self, key: str) -> None: ...


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
            purged += _purge_trash_of(db, storage, customer_id, before, batch - purged)
    return purged


def _purge_trash_of(db: Database, storage: BlobStorage, customer_id: str, before: datetime, room: int) -> int:
    """Up to ``room`` families of this customer, oldest first, then its folders in Trash whose
    files are all gone (#542: a folder waits for its last file). Returns how many families."""
    purged = 0
    while purged < room and _purge_one_family(db, storage, customer_id, before):
        purged += 1
    with db.customer(customer_id) as conn:
        trashed_sql.purge_folders(conn, customer_id, before=before)
    return purged


def purge_upload_sessions(db: Database, storage: CustomerStorage, clock: Callable[[], datetime]) -> int:
    """Release multipart uploads abandoned for seven days; retry next hour on storage failure."""
    now = clock()
    rows = upload_sessions_sql.expired(db, now, limit=100)
    removed = 0
    for row in rows:
        storage.abort_resumable(str(row["storage_key"]), str(row["storage_upload_id"]))
        storage.delete_resumable(str(row["storage_key"]))
        with db.customer(str(row["customer_id"])) as conn:
            upload_sessions_sql.delete_expired(conn, str(row["customer_id"]), str(row["id"]), now)
        removed += 1
    return removed


def sweep_ended_sessions(db: Database, clock: Callable[[], datetime]) -> int:
    """Forget the sessions ended by logout (0076) whose cookie has expired since: such a cookie
    is refused by its own expiry now. Every organisation in one statement; how many."""
    with db.system() as conn:
        return users_sql.sweep_ended_sessions(conn, clock())


def sweep_whatsapp(db: Database) -> int:
    """Kaagaz on WhatsApp's retention (#603, ``kaagaz_whatsapp_sweep``), so it holds even while the
    channel process is not running: connections no longer allowed or silent ended, old messages,
    connections, codes and numbers deleted. Every organisation in one statement; how many rows."""
    with db.system() as conn:
        return whatsapp_sql.sweep(conn)


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
    seconds: float = CUSTOMER_PURGE_SECONDS,
    timer: Callable[[], float] = time.monotonic,
) -> int:
    """Remove the organisations due at ``clock()``, longest due first, for about ``seconds``
    on ``timer``; returns how many families went. One that fails is logged (id and error
    type) and the next goes on; the database down raises. O(C) for the list, then O(v)
    statements and storage deletes per family and a paged prefix delete per customer."""
    now = clock()
    if now.tzinfo is None:
        raise ValueError("the clock must give a time with a time zone")
    deadline = timer() + seconds
    purged = 0
    with unavailable(), db.system() as conn:
        customers = customer_purge.ids_to_purge(conn, now=now)
    for customer_id in customers:
        if timer() >= deadline:
            break
        try:
            families, finished = purge_customer(
                db, storage, customer_id, now=now, keep_going=lambda: timer() < deadline
            )
        except (BackendUnavailable, SQLAlchemyError) as error:
            if isinstance(error, BackendUnavailable) and str(error) == "database":
                raise
            logger.error(
                "organisation purge failed", extra={"customer_id": customer_id, "code": type(error).__name__}
            )
            continue
        purged += families
        if finished:
            logger.info("organisation purged", extra={"customer_id": customer_id})
    return purged


def purge_customer(
    db: Database,
    storage: CustomerStorage,
    customer_id: str,
    *,
    now: datetime,
    keep_going: Callable[[], bool],
) -> tuple[int, bool]:
    """Remove the families of one customer being deleted and due at ``now`` while
    ``keep_going()``; once none is left, its two storage prefixes and its customers row.
    Returns (families removed, whether the customer is gone). Nothing happens unless it
    is being deleted and due."""
    families = 0
    with unavailable():
        while keep_going():
            with db.customer(customer_id) as conn:
                removed = customer_purge.next_family_to_purge(conn, storage, customer_id, now=now)
            if removed is None:  # not due, cancelled or gone: nothing more to do
                return families, False
            if not removed:
                _abort_uploads(db, storage, customer_id)
                storage.delete_customer(customer_id)
                with db.system() as conn:
                    return families, customer_purge.finish_delete(conn, customer_id)
            families += 1
    return families, False


def _abort_uploads(db: Database, storage: CustomerStorage, customer_id: str) -> None:
    """Abort every resumable upload of the customer (#482): open parts are no object, so
    ``delete_customer`` would leave them. Its rows go with the customers row. O(s) storage calls."""
    with db.customer(customer_id) as conn:
        uploads = upload_sessions_sql.of_customer(conn, customer_id)
    for key, upload_id in uploads:
        storage.abort_resumable(key, upload_id)


def forget_kept_audit(db: Database, clock: Callable[[], datetime]) -> int:
    """Remove the kept audit rows of organisations deleted 365 days or more before ``clock()``
    (0068, #482); how many. One set-based statement."""
    with unavailable(), db.system() as conn:
        return customer_purge.forget_kept_audit(conn, now=clock())


def sweep_purged_customers(db: Database, storage: CustomerStorage, clock: Callable[[], datetime]) -> int:
    """Empty both storage prefixes of each customer purged at least a day before
    ``clock()`` once more, and record it. Returns how many. A storage error stops the run;
    the next one tries again. O(P) for the id list, plus a paged delete per customer."""
    swept = 0
    with unavailable():
        with db.system() as conn:
            customers = customer_purge.ids_to_sweep(conn, now=clock())
        for customer_id in customers:
            storage.delete_customer(customer_id)
            with db.system() as conn:
                swept += customer_purge.mark_swept(conn, customer_id)
    return swept
