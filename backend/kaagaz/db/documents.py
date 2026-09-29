"""The documents table behind the upload and scan ports.

Implements ``DocumentRepository`` (kaagaz.ingestion.ports) and ``StatusStore``
(kaagaz.scanning.gate). Every method opens its own transaction as the given
customer, so row level security and the explicit ``customer_id`` filter both
apply (two locks, docs/architecture.md).

Status writes never move a document out of ``rejected``: the UPDATE says so,
and a trigger refuses it as well (the gate's adapter contract).

A record filed in a folder the customer does not have is refused by the
foreign key ``documents_folder_fk`` (it names the customer too, so another
customer's folder fails the same way), and ``add_if_absent`` raises
UnknownFolder.

Each call is one indexed statement: O(log n) in the number of documents.
``mark_ready`` and ``add_version`` are the exceptions: each takes the family
lock first (below).

``delete_forever`` is today's full delete of one document, shared by the trash
purge (kaagaz/jobs/purge.py) and ``delete_family_forever`` (the permanent delete).
"""

import dataclasses
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError, OperationalError

from kaagaz.db.engine import Database
from kaagaz.db.sqlstate import FOREIGN_KEY_VIOLATION, sqlstate
from kaagaz.db.trash import lock_family
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobStorage, DocumentRecord, UnknownFolder
from kaagaz.scanning.gate import DocumentStatus

_SELECT = (
    "SELECT customer_id, id, sha256, size_bytes, detected_type, display_name, uploaded_by,"
    " folder_id, family_id, version_no"
    " FROM documents WHERE customer_id = :c"
)


# The highest ready version_no is current (PRD 1 F6, PROVISIONAL plan 7.3): is a
# newer version of this document's family ready already?
_READY_STATE = (
    "SELECT d.status, EXISTS (SELECT 1 FROM documents newer WHERE newer.customer_id = :c"
    " AND newer.family_id = d.family_id AND newer.status = 'ready'"
    " AND newer.version_no > d.version_no) AS newer_ready"
    " FROM documents d WHERE d.customer_id = :c AND d.id = :d"
)

# The others first, then this one: the unique index documents_current allows
# one current row per family at every moment.
_OTHERS_NOT_CURRENT = (
    "UPDATE documents SET is_current = false"
    " WHERE customer_id = :c AND family_id = :f AND id <> :d AND is_current"
)

_READY = (
    "UPDATE documents SET status = 'ready', failure_code = NULL, page_count = :pages, is_current = :current"
    " WHERE customer_id = :c AND id = :d"
)


def mark_ready(conn: Connection, customer_id: str, document_id: str, page_count: int) -> None:
    """The document is read: set it ready with its page count and, unless a newer
    version of its family is ready already, make it the family's one current version.

    ``conn`` comes from ``Database.customer(customer_id)``. The family lock
    (``trash.lock_family``) comes before any write, so two versions finishing at
    once queue, and trash, restore and the purge never deadlock with it. A
    missing document, or a rejected one (final), is left as it is.
    3 or 4 statements; O(v log D) time and O(v) space for the family's v versions.
    """
    family_id = lock_family(conn, customer_id, document_id)
    if family_id is None:
        return
    params = {"c": customer_id, "d": document_id, "f": family_id}
    state = conn.execute(text(_READY_STATE), params).one()
    if state.status == DocumentStatus.REJECTED.value:
        return
    current = not state.newer_ready
    if current:
        conn.execute(text(_OTHERS_NOT_CURRENT), params)
    conn.execute(text(_READY), {**params, "pages": page_count, "current": current})


def delete_forever(conn: Connection, storage: BlobStorage, customer_id: str, document_id: str) -> None:
    """Delete one document for good: its row, with its pages, pieces, values and
    the rest (cascade), then its stored files.

    ``conn`` comes from ``Database.customer(customer_id)``. The files go inside
    that transaction: if storage cannot be reached, BackendUnavailable rolls the
    row back and a retry works. One statement, plus O(k) storage calls for the
    document's k stored files.
    """
    conn.execute(
        text("DELETE FROM documents WHERE customer_id = :c AND id = :d"),
        {"c": customer_id, "d": document_id},
    )
    storage.delete(customer_id, document_id)


class NotInTrash(Exception):
    """A version of the family is not in Trash, so nothing may be deleted forever."""


# Every version of one family, and whether it is in Trash.
_FAMILY_VERSIONS = (
    "SELECT d.id, d.deleted_at IS NOT NULL AS in_trash FROM documents d"
    " WHERE d.customer_id = :c AND d.family_id = :f ORDER BY d.id"
)


def delete_family_forever(
    conn: Connection, storage: BlobStorage, customer_id: str, document_id: str
) -> int | None:
    """Delete every version of ``document_id``'s family for good, rows and files, by
    ``delete_forever``, and return how many versions went; None if there is no such
    document. Raises NotInTrash, and deletes nothing, unless every version is in Trash.

    ``conn`` comes from ``Database.customer(customer_id)``. The family lock
    (``trash.lock_family``) comes first. One transaction for the family: if storage
    cannot be reached, every row comes back. 2 statements plus one per version, and
    O(k) storage calls per version; O(v) space for the family's v versions.
    """
    family_id = lock_family(conn, customer_id, document_id)
    if family_id is None:
        return None
    versions = conn.execute(text(_FAMILY_VERSIONS), {"c": customer_id, "f": family_id}).all()
    if not all(version.in_trash for version in versions):
        raise NotInTrash(document_id)
    for version in versions:
        delete_forever(conn, storage, customer_id, str(version.id))
    return len(versions)


def _record(row: Sequence[Any]) -> DocumentRecord:
    return DocumentRecord(
        customer_id=str(row[0]),
        document_id=str(row[1]),
        sha256=str(row[2]),
        size=int(str(row[3])),
        detected_type=str(row[4]),
        display_name=str(row[5]),
        uploaded_by=None if row[6] is None else str(row[6]),
        folder_id=None if row[7] is None else str(row[7]),
        family_id=str(row[8]),
        version_no=int(str(row[9])),
    )


def folder_missing(error: IntegrityError) -> bool:
    """True when ``error`` is ``documents_folder_fk`` refusing a folder id (23503)."""
    diag = getattr(error.orig, "diag", None)
    return (
        sqlstate(error) == FOREIGN_KEY_VIOLATION
        and getattr(diag, "constraint_name", None) == "documents_folder_fk"
    )


# --- new versions (PRD 1 F6) -------------------------------------------------

# The family's folder and next number, read through the document the version
# is for, unless its family is in Trash (the whole family moves together).
_NEXT_VERSION = (
    "SELECT d.folder_id, (SELECT max(v.version_no) FROM documents v"
    " WHERE v.customer_id = :c AND v.family_id = :f) + 1 AS version_no"
    " FROM documents d WHERE d.customer_id = :c AND d.id = :of AND d.deleted_at IS NULL"
)

_INSERT_VERSION = (
    "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes, detected_type,"
    " uploaded_by, folder_id, family_id, version_no, is_current)"
    " VALUES (:d, :c, :name, :h, :size, :type, :by, :folder, :f, :version, false)"
    " ON CONFLICT (customer_id, sha256) DO NOTHING RETURNING id"
)

# Every version holds the family's tag set (tags.set_document_tags), so the
# document the version is for has the whole set.
_COPY_TAGS = (
    "INSERT INTO document_tags (customer_id, document_id, tag_id)"
    " SELECT dt.customer_id, :d, dt.tag_id FROM document_tags dt"
    " WHERE dt.customer_id = :c AND dt.document_id = :of"
)


def add_version(
    conn: Connection, record: DocumentRecord, *, of_document_id: str
) -> tuple[DocumentRecord, bool] | None:
    """``DocumentRepository.add_version_if_absent`` on a connection from
    ``Database.customer(record.customer_id)``.

    The family lock (``trash.lock_family``) comes first, so a second new version
    waits, then sees this one and takes the next number (the unique index
    ``documents_family_version`` refuses a repeat anyway). 4 statements for a
    new version (lock, family, insert, tags); O(v log D + t log N) time and
    O(v + t) space for the family's v versions and t tags.
    """
    customer_id = record.customer_id
    family_id = lock_family(conn, customer_id, of_document_id)
    if family_id is None:
        return None
    params: dict[str, Any] = {"c": customer_id, "f": family_id, "of": of_document_id}
    family = conn.execute(text(_NEXT_VERSION), params).first()
    if family is None:  # the family is in Trash
        return None
    stored = dataclasses.replace(
        record,
        folder_id=None if family[0] is None else str(family[0]),
        family_id=family_id,
        version_no=int(family[1]),
    )
    params.update(
        d=stored.document_id,
        name=stored.display_name,
        h=stored.sha256,
        size=stored.size,
        type=stored.detected_type,
        by=stored.uploaded_by,
        folder=stored.folder_id,
        version=stored.version_no,
    )
    if conn.execute(text(_INSERT_VERSION), params).first() is None:  # this customer has these bytes
        row = conn.execute(text(_SELECT + " AND sha256 = :h"), params).one()
        return _record(row), False
    conn.execute(text(_COPY_TAGS), params)
    return stored, True


class PgDocuments:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- DocumentRepository ---------------------------------------------------

    def get(self, customer_id: str, document_id: str) -> DocumentRecord | None:
        with _unavailable(), self._db.customer(customer_id) as conn:
            row = conn.execute(
                text(_SELECT + " AND id = :d"),
                {"c": customer_id, "d": document_id},
            ).first()
        return None if row is None else _record(row)

    def find_by_fingerprint(self, customer_id: str, sha256: str) -> DocumentRecord | None:
        with _unavailable(), self._db.customer(customer_id) as conn:
            row = conn.execute(
                text(_SELECT + " AND sha256 = :h"),
                {"c": customer_id, "h": sha256},
            ).first()
        return None if row is None else _record(row)

    def add_if_absent(self, record: DocumentRecord) -> tuple[DocumentRecord, bool]:
        """Atomic per (customer, fingerprint): the unique index decides the race.
        Raises UnknownFolder when the record's folder is not this customer's."""
        with _unavailable(), self._db.customer(record.customer_id) as conn:
            params = {
                "d": record.document_id,
                "c": record.customer_id,
                "name": record.display_name,
                "h": record.sha256,
                "size": record.size,
                "type": record.detected_type,
                "by": record.uploaded_by,
                "folder": record.folder_id,
                "family": record.family_id or record.document_id,
                "version": record.version_no,
            }
            try:
                inserted = conn.execute(
                    text(
                        "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes,"
                        " detected_type, uploaded_by, folder_id, family_id, version_no)"
                        " VALUES (:d, :c, :name, :h, :size, :type, :by, :folder, :family, :version)"
                        " ON CONFLICT (customer_id, sha256) DO NOTHING RETURNING id"
                    ),
                    params,
                ).first()
            except IntegrityError as error:
                if folder_missing(error):
                    raise UnknownFolder(record.document_id) from None
                raise
            if inserted is not None:
                return record, True
            row = conn.execute(
                text(_SELECT + " AND sha256 = :h"),
                {"c": record.customer_id, "h": record.sha256},
            ).one()
        return _record(row), False

    def add_version_if_absent(
        self, record: DocumentRecord, *, of_document_id: str
    ) -> tuple[DocumentRecord, bool] | None:
        """``add_version`` in its own transaction."""
        with _unavailable(), self._db.customer(record.customer_id) as conn:
            return add_version(conn, record, of_document_id=of_document_id)

    def remove(self, record: DocumentRecord) -> None:
        with _unavailable(), self._db.customer(record.customer_id) as conn:
            conn.execute(
                text("DELETE FROM documents WHERE customer_id = :c AND id = :d AND sha256 = :h"),
                {"c": record.customer_id, "d": record.document_id, "h": record.sha256},
            )

    # --- StatusStore ----------------------------------------------------------

    def get_status(self, customer_id: str, document_id: str) -> DocumentStatus | None:
        with _unavailable(), self._db.customer(customer_id) as conn:
            status = conn.execute(
                text("SELECT status FROM documents WHERE customer_id = :c AND id = :d"),
                {"c": customer_id, "d": document_id},
            ).scalar()
        if status is None or status == DocumentStatus.RECEIVED.value:
            # "received" means not scanned yet, which the gate treats as None.
            return None
        return DocumentStatus(status)

    def set_status(self, customer_id: str, document_id: str, status: DocumentStatus) -> None:
        with _unavailable(), self._db.customer(customer_id) as conn:
            conn.execute(
                text(
                    "UPDATE documents SET status = :s, failure_code = NULL"
                    " WHERE customer_id = :c AND id = :d AND status <> 'rejected'"
                ),
                {"s": status.value, "c": customer_id, "d": document_id},
            )

    # --- used by the pipeline -----------------------------------------------

    def set_failed(self, customer_id: str, document_id: str, code: str) -> None:
        """Mark a document failed for good with a short code. Rejected stays rejected,
        but keeps its code so the file list can say why."""
        with _unavailable(), self._db.customer(customer_id) as conn:
            conn.execute(
                text(
                    "UPDATE documents"
                    " SET status = CASE WHEN status = 'rejected' THEN 'rejected' ELSE 'failed' END,"
                    " failure_code = :code"
                    " WHERE customer_id = :c AND id = :d"
                ),
                {"code": code, "c": customer_id, "d": document_id},
            )

    def mark_ready(self, customer_id: str, document_id: str, page_count: int) -> None:
        """``mark_ready`` in its own transaction: the pipeline's last step."""
        with _unavailable(), self._db.customer(customer_id) as conn:
            mark_ready(conn, customer_id, document_id, page_count)

    def set_page_count(self, customer_id: str, document_id: str, page_count: int) -> None:
        with _unavailable(), self._db.customer(customer_id) as conn:
            conn.execute(
                text("UPDATE documents SET page_count = :n WHERE customer_id = :c AND id = :d"),
                {"n": page_count, "c": customer_id, "d": document_id},
            )

    # --- used by the worker ---------------------------------------------------

    def mark_exhausted(self, customer_id: str, document_id: str) -> None:
        """The queue's last delivery of this document's job came: mark it failed
        with ``retries_exhausted``, so it does not show "Reading…" for good.

        Only a document still in progress (received or clean) changes, in the
        one conditional statement: a rejected one keeps its reason, and a ready,
        needs_reader or failed one keeps its state and code.
        """
        with _unavailable(), self._db.customer(customer_id) as conn:
            conn.execute(
                text(
                    "UPDATE documents SET status = 'failed', failure_code = 'retries_exhausted'"
                    " WHERE customer_id = :c AND id = :d AND status IN ('received', 'clean')"
                ),
                {"c": customer_id, "d": document_id},
            )


@contextmanager
def _unavailable() -> Iterator[None]:
    """Turn "cannot reach the database" into BackendUnavailable, the one error the
    upload service may undo and retry on. Constraint errors stay what they are."""
    try:
        yield
    except OperationalError:
        raise BackendUnavailable("database") from None
