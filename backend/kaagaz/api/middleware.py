"""What wraps every request: a request id, the rate limit, security headers
and one JSON access-log line.

Why:

* **Request id**: support can match a customer's report to the log line. A
  well-formed incoming ``X-Request-ID`` is kept, otherwise a new one is made.
* **Rate limit**: a token bucket per signed-in user (per client address before
  sign-in; per operator, ``op:{oid}``, on /api/operator), ``rate_limit_per_minute``
  tokens refilled evenly over a minute,
  held in memory. A file's upload calls (``/api/documents/upload-sessions``: start,
  parts, finish) have their own bucket of the same size, ``{key}:upload``, so a bulk
  upload and the screens' reads never use up each other's (Vrushit UAT, 25 Oct 2026:
  the batch tray's polling used up the budget and 805 calls were refused, uploads
  among them). A folder upload's file (``POST /api/documents?folder_upload_item_id=``, #484,
  council 3 Nov 2026) has a bucket of its own, ``{key}:folder``, of
  FOLDER_UPLOAD_CALLS_PER_MINUTE: one call a file, so 50,000 files go in hours, not days;
  planning, registering and progress stay on the general bucket. The route checks the item
  before it reads the body, so a made-up id gets a 404, never an upload. A refusal's
  ``Retry-After`` is the honest wait for the next token
  (about a second at 120 a minute), not a flat minute, and each refusal is logged at
  WARNING with the path, the ids and the bucket. PROVISIONAL: behind the Next.js proxy every visitor who is
  not signed in shares the proxy's address; trusting ``X-Forwarded-For`` is a
  security setting for Vrushit.
* **Headers**: ``X-Content-Type-Options: nosniff``, ``Referrer-Policy:
  same-origin`` and ``Cache-Control: no-store`` (customer data must never sit
  in a shared cache), unless a route set its own.
* **Access log**: method, path, status, time and the ids only. Never the body,
  the query string (it can hold a search for a name), file names or questions.
* **Errors** (``ErrorMiddleware``): an error nothing else handled is the usual
  500 JSON and one log line with the error's type and the request id. Never
  its message or traceback: a database error can quote row values, and a
  parser error document text.
"""

import json
import logging
import math
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from kaagaz.api.auth import OPERATOR_COOKIE, OPERATOR_PATH, read_operator, session_claims
from kaagaz.api.context import get_context
from kaagaz.api.errors import error_response

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")

access_log = logging.getLogger("kaagaz.api.access")
limit_log = logging.getLogger("kaagaz.api.rate_limit")

# A file's upload calls, counted in their own bucket (see the module notes).
UPLOAD_PATH = "/api/documents/upload-sessions"
# A folder upload's file (#484): this path, POST, with this query parameter.
FOLDER_FILE_PATH, FOLDER_ITEM = "/api/documents", "folder_upload_item_id"
error_log = logging.getLogger("kaagaz.api.errors")


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Token buckets keyed by caller: ``per_minute`` a minute, or that many every
    ``window_seconds`` when given (sign-ups: 5 an hour). ``allow`` is O(1); when more than
    ``max_keys`` callers are held, full (idle) buckets are dropped in one O(n) pass."""

    def __init__(
        self,
        per_minute: int,
        *,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        if per_minute < 1:
            raise ValueError("per_minute must be at least 1")
        self._capacity = float(per_minute)
        self._refill_per_second = per_minute / window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self._max_keys:
                    self._drop_idle(now)
                bucket = _Bucket(tokens=self._capacity, updated=now)
                self._buckets[key] = bucket
            else:
                elapsed = max(0.0, now - bucket.updated)
                bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_per_second)
                bucket.updated = now
            if bucket.tokens < 1.0:
                return False
            bucket.tokens -= 1.0
            return True

    def retry_after(self, key: str) -> int:
        """Whole seconds until ``key`` has a token again, at least 1. O(1)."""
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return 1
            tokens = bucket.tokens + max(0.0, now - bucket.updated) * self._refill_per_second
        return max(1, math.ceil((1.0 - tokens) / self._refill_per_second))

    def _drop_idle(self, now: float) -> None:
        full_after = self._capacity / self._refill_per_second
        idle = [key for key, b in self._buckets.items() if now - b.updated >= full_after]
        for key in idle:
            del self._buckets[key]


class AddressLocks:
    """Wrong sign-ins in a row per account and visitor address (A2, 7 Nov 2026), in memory (the
    API is one process): ``limit`` attempts from one address lock that account for that address
    for ``lock_seconds``, so a flood from one address never locks the person out of their own.
    As ``users.begin_attempt``: counted before the check, the attempt that reaches the limit
    still runs, a lock is never extended, and an expired one starts the count again (so does
    ``lock_seconds`` with no attempt). ``count`` never goes above the account's own count, which
    a right password, an unlock or a reset link sets back to 0.
    shortcut: a restart forgets every count; past ``max_keys`` the quiet ones are dropped."""

    def __init__(self, *, max_keys: int = 10_000) -> None:
        self._held: dict[str, tuple[int, float]] = {}  # key: (count, when the last was counted)
        self._max_keys = max_keys
        self._lock = threading.Lock()

    def locked(self, key: str, now: float, limit: int, lock_seconds: float) -> int:
        """The count while ``key`` is locked, else 0. O(1)."""
        with self._lock:
            count, at = self._held.get(key, (0, -math.inf))
        return count if count >= limit and now - at < lock_seconds else 0

    def count(self, key: str, now: float, lock_seconds: float, ceiling: int) -> int:
        """Count one attempt for ``key``, at most ``ceiling``; the new count. O(1), and one O(n)
        pass dropping the quiet keys when ``max_keys`` are held."""
        with self._lock:
            count, at = self._held.get(key, (0, -math.inf))
            if now - at >= lock_seconds:
                count = 0
            if key not in self._held and len(self._held) >= self._max_keys:
                self._held = {k: v for k, v in self._held.items() if now - v[1] < lock_seconds}
            count = min(count + 1, ceiling)
            self._held[key] = (count, now)
            return count

    def clear(self, key: str) -> None:
        with self._lock:
            self._held.pop(key, None)


def client_address(request: Request) -> str:
    """The visitor's address: uvicorn takes it from Caddy's X-Forwarded-For (backend/Dockerfile)."""
    return request.client.host if request.client else "-"


class RequestMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, limiter: RateLimiter, folder_limiter: RateLimiter) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._folder_limiter = folder_limiter

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id

        user_id = self._caller(request)
        operator_id = self._operator(request)
        if operator_id:
            key = f"op:{operator_id}"
        else:
            key = f"user:{user_id}" if user_id else f"ip:{request.client.host if request.client else '-'}"
        path = request.url.path
        upload = path == UPLOAD_PATH or path.startswith(UPLOAD_PATH + "/")
        folder = request.method == "POST" and path == FOLDER_FILE_PATH and FOLDER_ITEM in request.query_params
        bucket = "folder" if folder else "upload" if upload else "general"
        if bucket != "general":
            key += f":{bucket}"
        limiter = self._folder_limiter if folder else self._limiter
        if limiter.allow(key):
            response = await call_next(request)
        else:
            wait = limiter.retry_after(key)
            response = error_response(429, "rate_limited", headers={"Retry-After": str(wait)})
            limit_log.warning(
                json.dumps(
                    {
                        "event": "rate_limited",
                        "request_id": request_id,
                        "method": request.method,
                        "path": path,
                        "bucket": bucket,
                        "retry_after": wait,
                        "user_id": user_id,
                        "operator_id": operator_id,
                    },
                    separators=(",", ":"),
                )
            )

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers.setdefault("Referrer-Policy", "same-origin")  # a route may set its own
        response.headers.setdefault("Cache-Control", "no-store")
        access_log.info(
            json.dumps(
                {
                    "event": "request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": path,
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                    "user_id": user_id,
                    "operator_id": operator_id,
                },
                separators=(",", ":"),
            )
        )
        return response

    @staticmethod
    def _caller(request: Request) -> str | None:
        """The user id from a genuine session cookie, without the database."""
        context = get_context(request)
        claims = session_claims(request, context.settings, int(context.clock().timestamp()))
        return None if claims is None else claims.user_id

    @staticmethod
    def _operator(request: Request) -> str | None:
        """The operator id from a genuine operator cookie, without the database, on
        /api/operator only (the only path its browser cookie is sent to)."""
        path = request.url.path
        token = request.cookies.get(OPERATOR_COOKIE)
        if not token or not (path == OPERATOR_PATH or path.startswith(OPERATOR_PATH + "/")):
            return None
        context = get_context(request)
        claims = read_operator(token, context.settings.session_secret, int(context.clock().timestamp()))
        return None if claims is None else claims.operator_id


class ErrorMiddleware:
    """The outermost of the app's own middleware (``create_app`` adds it last). Only
    Starlette's ServerErrorMiddleware sits outside it, and no error reaches that any
    more, so nothing is raised again for uvicorn to log with a traceback. O(1) per
    request."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = False

        async def send_watched(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        # The one broad catch, at the ASGI boundary: whatever the app lets out ends here
        # as one safe log line and a 500, and is never raised again.
        try:
            await self._app(scope, receive, send_watched)
        except Exception as error:  # noqa: BLE001 - the ASGI boundary, see the comment above
            request_id = _request_id(scope)
            error_log.error(
                json.dumps(
                    {"event": "error", "request_id": request_id, "error": type(error).__name__},
                    separators=(",", ":"),
                )
            )
            if started:
                return  # part of the reply is already sent; the server closes the connection
            headers = None if request_id is None else {REQUEST_ID_HEADER: request_id}
            await error_response(500, "internal_error", headers=headers)(scope, receive, send)


def _request_id(scope: Scope) -> str | None:
    """The id RequestMiddleware gave this request (kept in the scope's state), if it got that far."""
    state = scope.get("state")
    request_id = state.get("request_id") if isinstance(state, dict) else None
    return request_id if isinstance(request_id, str) else None
