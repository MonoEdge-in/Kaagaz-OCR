"""The virus scan gate, the first step of every job.

Deny by default: only an explicit ``Verdict.CLEAN`` lets a file through.

* REJECTED is final: a rejected document is refused at once, never rescanned
  (a rescan with different signatures must not be able to clear it).

* CLEAN: the document is marked clean and later steps may open it.
* INFECTED, BLOCKED (active content such as scripts or macros), ENCRYPTED (a
  password needed to open it, #552) or
  UNSCANNABLE (for example encrypted, or too big for the scanner), or any
  value that is not a Verdict: the file is quarantined, the document is
  marked rejected, and the job fails for good.
* The scanner is down or times out (ScannerError): the job fails but may be
  retried. The document stays unscanned, so no later step will open it.

``check`` returns the exact bytes it scanned. Callers read those bytes and
never fetch the file again, so what is read is always what was scanned.
Later steps call ``require_clean`` before writing results, so a file that was
never scanned, or failed the scan, cannot be used by mistake.

Adapter contract: status writes must be conditional in the real store (never
move out of REJECTED), because two deliveries of one job can overlap.

PROVISIONAL: which scanner, where the scan runs and where quarantined files
go are open questions (PRD question 4). The scanner and quarantine are ports
so those answers land in their adapters. There is no default scanner, so a
worker cannot be built that skips the scan.
"""

from enum import Enum
from typing import Protocol

from kaagaz.ingestion.ports import BlobNotFound, BlobStorage, Job
from kaagaz.jobs.worker import JobFailed


class Verdict(Enum):
    CLEAN = "clean"
    INFECTED = "infected"
    UNSCANNABLE = "unscannable"
    # Not a known virus, but content a business document must not carry:
    # scripts, macros, hidden programs (kaagaz/scanning/content.py).
    BLOCKED = "blocked"
    # It needs a password to open, so nothing can look inside (content.py, #552).
    ENCRYPTED = "encrypted"


class DocumentStatus(Enum):
    """PROVISIONAL: the stored status model is database design (#1)."""

    # Not scanned yet: every new document starts here (the column's default).
    RECEIVED = "received"
    CLEAN = "clean"  # passed the scan
    REJECTED = "rejected"  # failed the scan, quarantined
    NEEDS_READER = "needs_reader"  # clean, but no reader for its type yet (set by the pipeline)
    READY = "ready"  # read and split into pieces (set by the pipeline)
    FAILED = "failed"  # a step failed for good; the code is kept with it (set by the pipeline)
    LOCKED = (
        "locked"  # PRD 7 (#605): locked, scanned and pictured; never read (set by a lock or the pipeline)
    )


class ScannerError(Exception):
    """The scanner could not give a verdict (down, timed out)."""


class Scanner(Protocol):
    def scan(self, data: bytes, *, name: str | None = None) -> Verdict:
        """``name``: the file's name (None: not known), which may only choose between text
        kinds the content check treats alike for safety (#522)."""
        ...


class Quarantine(Protocol):
    def quarantine(self, customer_id: str, document_id: str) -> None:
        """Move the file where normal reads (BlobStorage.get) cannot reach it."""
        ...


class StatusStore(Protocol):
    def get_status(self, customer_id: str, document_id: str) -> DocumentStatus | None: ...

    def set_status(
        self, customer_id: str, document_id: str, status: DocumentStatus, *, run_id: str | None = None
    ) -> None:
        """With ``run_id`` (PRD 2's run fence), raise kaagaz.jobs.worker.Superseded and
        change nothing unless that run is still open."""
        ...


# Codes that mean the file failed the scan (now or earlier). Anything made
# from such a file must be removed.
REJECTION_CODES = frozenset(
    {"virus_found", "blocked_content", "unscannable", "password_protected", "rejected"}
)

# What the customer sees for each rejection. No scanner names or details.
CUSTOMER_MESSAGES = {
    "virus_found": "This file failed the virus check and was not processed.",
    "unscannable": (
        "This file could not be checked for viruses, so it was not processed."
        " It may be damaged. Save it again and upload that copy."
    ),
    "blocked_content": (
        "This file contains scripts, macros or a hidden program, so it was not processed."
        " Save it again as a plain PDF, photo or spreadsheet and upload that."
    ),
    # #552: the words a reader's own password_protected failure says (kaagaz/api/views.py).
    "password_protected": (
        "This file has a password, so it can't be opened. Remove the password and upload it again."
    ),
}


class ScanGate:
    def __init__(
        self,
        storage: BlobStorage,
        scanner: Scanner,
        quarantine: Quarantine,
        statuses: StatusStore,
    ) -> None:
        self._storage = storage
        self._scanner = scanner
        self._quarantine = quarantine
        self._statuses = statuses

    def check(self, job: Job, *, run_id: str | None = None, name: str | None = None) -> bytes:
        """Scan the job's file and return the scanned bytes, only if clean. With
        ``run_id``, the CLEAN write is fenced by that run: a stale delivery's scan
        can never move a document read since back to clean. ``name``: the file's name, for
        the scanner (#522: it only chooses between text kinds)."""
        customer_id, document_id = job.customer_id, job.document_id
        if self._statuses.get_status(customer_id, document_id) is DocumentStatus.REJECTED:
            raise JobFailed("rejected", retryable=False)
        # Errors are raised outside the except blocks, so an adapter's error
        # (which could hold scanner output or a path) is never chained on.
        data = _get_or_none(self._storage, customer_id, document_id)
        if data is None:
            # Includes a job naming another customer's document: nothing read.
            raise JobFailed("document_missing", retryable=False)

        verdict = _scan(self._scanner, data, name)
        if verdict is _SCANNER_FAILED:
            raise JobFailed("scanner_unavailable", retryable=True)

        if verdict is Verdict.CLEAN:
            self._statuses.set_status(customer_id, document_id, DocumentStatus.CLEAN, run_id=run_id)
            return data

        codes = {
            Verdict.INFECTED: "virus_found",
            Verdict.BLOCKED: "blocked_content",
            Verdict.ENCRYPTED: "password_protected",
        }
        code = codes.get(verdict, "unscannable") if isinstance(verdict, Verdict) else "unscannable"
        # Refuse first: if moving the file to quarantine fails, it is already
        # marked rejected and no step will use it.
        self._statuses.set_status(customer_id, document_id, DocumentStatus.REJECTED)
        self._quarantine.quarantine(customer_id, document_id)
        raise JobFailed(code, retryable=False)


def _get_or_none(storage: BlobStorage, customer_id: str, document_id: str) -> bytes | None:
    try:
        return storage.get(customer_id, document_id)
    except BlobNotFound:
        return None


# Marks "the scanner gave no answer". A dedicated object, not None, so a
# scanner that wrongly returns None is still treated as unscannable.
_SCANNER_FAILED = object()


def _scan(scanner: Scanner, data: bytes, name: str | None) -> object:
    """The scanner's answer, or _SCANNER_FAILED if it could not give one."""
    try:
        return scanner.scan(data, name=name)
    except ScannerError:
        return _SCANNER_FAILED


def require_clean(statuses: StatusStore, job: Job) -> None:
    """Refuse to go on unless this customer's document passed the scan."""
    if statuses.get_status(job.customer_id, job.document_id) is not DocumentStatus.CLEAN:
        raise JobFailed("not_scanned", retryable=False)
