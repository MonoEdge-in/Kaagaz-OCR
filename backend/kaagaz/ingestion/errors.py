"""Errors raised while accepting an upload.

Messages carry a short code only, never file bytes, file names or anything
read from the file, so an error can be logged or shown without leaking
customer data (CLAUDE.md never-do 3).
"""

from enum import StrEnum

from kaagaz.ingestion.policy import Kind


class RejectCode(StrEnum):
    """Why an upload was refused. Safe to show to the customer."""

    EMPTY_FILE = "empty_file"
    TOO_LARGE = "too_large"
    UNSUPPORTED_TYPE = "unsupported_type"


class UploadRejected(Exception):
    """The file is refused. Nothing was stored, recorded or queued.

    A file over its own type's limit also carries its kind, its size and that
    limit in bytes, so the refusal can name them (PRD 1, U2). They are None
    when the file was refused before its type was known (over every limit).
    """

    def __init__(
        self,
        code: RejectCode,
        *,
        kind: Kind | None = None,
        size: int | None = None,
        limit: int | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.kind = kind
        self.size = size
        self.limit = limit


class BackendUnavailable(Exception):
    """Storage, the database or the queue could not be reached.

    Adapters raise this for failures the caller may retry. The upload service
    catches only this type, never a blanket Exception.
    """
