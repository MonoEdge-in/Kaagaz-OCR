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

import threading
from collections.abc import Iterator
from contextlib import contextmanager

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


class SendSlots:
    """Folder upload sends at once (#484, council 3 Nov 2026): this API process takes at most
    ``per_org`` of one organisation's files and ``total`` of every organisation's at the same
    time, so one big upload can't hold every worker thread or database connection (POOL_SIZE
    10) and slow everyone else. Past either it is 429 ``rate_limited`` with ``Retry-After: 2``,
    which the browser waits out and sends again. Counters under a lock, O(1)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_org: dict[str, int] = {}
        self._total = 0

    @contextmanager
    def held(self, org: str, *, per_org: int, total: int) -> Iterator[None]:
        with self._lock:
            if self._total >= total or self._by_org.get(org, 0) >= per_org:
                raise ApiError(
                    429,
                    "rate_limited",
                    "Kaagaz is taking other files right now. This one goes again in a moment.",
                    headers={"Retry-After": "2"},
                )
            self._total += 1
            self._by_org[org] = self._by_org.get(org, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                self._total -= 1
                self._by_org[org] -= 1
                if not self._by_org[org]:
                    del self._by_org[org]
