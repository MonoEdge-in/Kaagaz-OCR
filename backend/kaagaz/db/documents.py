"""The documents table behind the upload and scan ports.

Implements ``DocumentRepository`` (kaagaz.ingestion.ports) and ``StatusStore``
(kaagaz.scanning.gate). Every method opens its own transaction as the given
customer, so row level security and the explicit ``customer_id`` filter both
apply (two locks, docs/architecture.md).

Status writes never move a document out of ``rejected``: the UPDATE says so,
and a trigger refuses it as well (the gate's adapter contract).

Each call is one indexed statement: O(log n) in the number of documents.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import DocumentRecord
from kaagaz.scanning.gate import DocumentStatus

_SELECT = (
    "SELECT customer_id, id, sha256, size_bytes, detected_type, display_name, uploaded_by"
    " FROM documents WHERE customer_id = :c"
)


def _record(row: Sequence[Any]) -> DocumentRecord:
    return DocumentRecord(
        customer_id=str(row[0]),
        document_id=str(row[1]),
        sha256=str(row[2]),
        size=int(str(row[3])),
        detected_type=str(row[4]),
        display_name=str(row[5]),
        uploaded_by=None if row[6] is None else str(row[6]),
    )


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
        """Atomic per (customer, fingerprint): the unique index decides the race."""
        with _unavailable(), self._db.customer(record.customer_id) as conn:
            inserted = conn.execute(
                text(
                    "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes,"
                    " detected_type, uploaded_by, family_id)"
                    " VALUES (:d, :c, :name, :h, :size, :type, :by, :d)"
                    " ON CONFLICT (customer_id, sha256) DO NOTHING RETURNING id"
                ),
                {
                    "d": record.document_id,
                    "c": record.customer_id,
                    "name": record.display_name,
                    "h": record.sha256,
                    "size": record.size,
                    "type": record.detected_type,
                    "by": record.uploaded_by,
                },
            ).first()
            if inserted is not None:
                return record, True
            row = conn.execute(
                text(_SELECT + " AND sha256 = :h"),
                {"c": record.customer_id, "h": record.sha256},
            ).one()
        return _record(row), False

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
