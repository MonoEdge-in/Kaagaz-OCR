"""What the upload service needs from the database, file storage and queue.

These are interfaces only. The real ones are PgDocuments (kaagaz.db),
S3Storage and SqsQueue (kaagaz.adapters); the tests use in-memory fakes
that live under tests/.

Every method takes ``customer_id`` first. There is no method that reads or
lists across customers (CLAUDE.md never-do 1).

PROVISIONAL: the record fields are a placeholder until the database design
(#1) is written.
"""

from dataclasses import dataclass
from typing import IO, Protocol


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """One stored file. Only its display name is kept, never the raw file name."""

    customer_id: str
    document_id: str
    sha256: str
    size: int
    detected_type: str
    # What the file list and source cards show: the uploaded name, cleaned by the API
    # (path and control characters stripped, at most 255 characters).
    # The upload service never reads it; the API sets it.
    display_name: str = "document"
    uploaded_by: str | None = None  # the signed-in user's id
    folder_id: str | None = None  # None: at the top level, in no folder
    family_id: str | None = None  # None: its own id (a first version)
    version_no: int = 1
    # The scan reader the upload chose (kaagaz.reading.ocr.ocr_choices); None: Auto.
    ocr_choice: str | None = None
    # PRD 5 Q1: the drop it was uploaded in (the browser's id for it); None: in none.
    batch_id: str | None = None
    # Folder upload (#484): it stays in folder_id, never auto-filed, even in a department.
    keep_place: bool = False


@dataclass(frozen=True, slots=True)
class Job:
    """A request to process one document.

    It carries the customer so the worker can set the current customer before
    its first query, and carries ids only, never the file or its name.
    ``run_id`` (scan review UX rules, 16 Oct 2026): an Ensemble run of the document
    (``ensemble_runs.id``) to read, not the document itself; None for every other job.
    ``check_id`` (#463): an operator's accuracy check (``scan_accuracy_checks.id``, also its
    ``document_id``: it reads pages of several documents); None for every other job.
    """

    customer_id: str
    document_id: str
    run_id: str | None = None
    check_id: str | None = None


class BlobNotFound(Exception):
    """No file with this id exists for this customer."""


class UnknownFolder(Exception):
    """The record names a folder this customer does not have (never there, or deleted)."""


class UnknownFamily(Exception):
    """A new version names a document this customer does not have, or one whose
    family is in Trash, so there is no family to add it to."""


class DocumentRepository(Protocol):
    def get(self, customer_id: str, document_id: str) -> DocumentRecord | None:
        """This customer's document with this id, or None (also for another
        customer's id)."""
        ...

    def find_by_fingerprint(self, customer_id: str, sha256: str) -> DocumentRecord | None:
        """This customer's document with this fingerprint, if any."""
        ...

    def add_if_absent(self, record: DocumentRecord) -> tuple[DocumentRecord, bool]:
        """Insert unless this customer already has this fingerprint.

        Must be atomic per (customer_id, sha256), so two identical uploads at
        the same moment cannot both be inserted. Returns the stored record and
        True if it was inserted, or the existing record and False. Raises
        UnknownFolder, and inserts nothing, when ``record.folder_id`` is not
        one of this customer's folders.
        """
        ...

    def add_version_if_absent(
        self, record: DocumentRecord, *, of_document_id: str
    ) -> tuple[DocumentRecord, bool] | None:
        """Insert ``record`` as the newest version of ``of_document_id``'s family
        (PRD 1 F6), unless this customer already has this fingerprint.

        Under the family lock, so two new versions at once get two numbers:
        version_no is the family's highest plus one, not current (the pipeline
        makes it current once it is read), with the family's id, folder and
        tags. The record's own folder_id, family_id and version_no are not used.
        Returns the stored record and True, or the existing record and False,
        like ``add_if_absent``. None, with nothing inserted, when
        ``of_document_id`` is not this customer's or its family is in Trash.
        """
        ...

    def remove(self, record: DocumentRecord) -> None:
        """Undo an insert. Removes only if the stored document id matches."""
        ...


class BlobStorage(Protocol):
    """File storage. The adapter owns how keys are named (#11)."""

    def put(self, customer_id: str, document_id: str, data: IO[bytes]) -> None: ...

    def get(self, customer_id: str, document_id: str) -> bytes:
        """The file's bytes. Raises BlobNotFound for another customer's id."""
        ...

    def delete(self, customer_id: str, document_id: str) -> None: ...


class JobQueue(Protocol):
    def enqueue(self, job: Job, *, express: bool = False) -> None:
        """Queue ``job``. ``express`` asks for the express lane (PRD 5 Q4); the server
        alone decides it, from the page count it stored."""
        ...
