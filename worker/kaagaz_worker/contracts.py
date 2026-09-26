"""The values that cross a Temporal boundary: workflow to activity, and back.

Temporal serialises these as JSON, so they carry IDs and small values only, never file
contents (day-1-prd.html section 6: "Activities take and return IDs and small values
only, never file contents").
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RegisterResult:
    document_id: str


@dataclass(frozen=True)
class FailInput:
    upload_id: str
    document_id: str | None
    error: str
