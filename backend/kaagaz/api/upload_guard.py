"""Checks on an upload request before its body is read (PRD 1, U1 and U2).

Shared by every route that takes a file. Why each check:

* **No Transfer-Encoding** (400). A chunked body declares no size, so the
  size check below could not run, and the form parser would write the whole
  body to disk before the upload service ever counted a byte.
* **A Content-Length no larger than the largest limit plus the multipart
  wrapping** (413). The server never hands over more body than the
  Content-Length says, so this bounds what is read and spooled.

The type of the file is not known yet here, so a refusal names the largest
limit only. Time O(1).
"""

from fastapi import Request

from kaagaz.api.errors import ApiError
from kaagaz.ingestion.messages import over_every_limit_message
from kaagaz.ingestion.policy import UploadPolicy

# Room for the multipart boundary and part headers around the file itself.
MULTIPART_OVERHEAD = 64 * 1024


def check_upload_request(request: Request, policy: UploadPolicy) -> None:
    """Raise ApiError unless the request may be read as an upload under ``policy``."""
    if "transfer-encoding" in request.headers:
        raise ApiError(400, "bad_request", "The upload must say its size. Please try again.")
    length = request.headers.get("content-length", "")
    if not (length.isascii() and length.isdigit()):
        raise ApiError(400, "bad_request", "The upload has no size. Please try again.")
    if int(length) > policy.largest_limit + MULTIPART_OVERHEAD:
        raise ApiError(413, "too_large", over_every_limit_message(policy.largest_limit))
