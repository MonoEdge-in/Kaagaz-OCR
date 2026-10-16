"""What wraps every request: a request id, the rate limit, security headers
and one JSON access-log line.

Why:

* **Request id**: support can match a customer's report to the log line. A
  well-formed incoming ``X-Request-ID`` is kept, otherwise a new one is made.
* **Rate limit**: a token bucket per signed-in user (per client address before
  sign-in; per operator, ``op:{oid}``, on /api/operator), ``rate_limit_per_minute``
  tokens refilled evenly over a minute,
  held in memory. PROVISIONAL: behind the Next.js proxy every visitor who is
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

    def _drop_idle(self, now: float) -> None:
        full_after = self._capacity / self._refill_per_second
        idle = [key for key, b in self._buckets.items() if now - b.updated >= full_after]
        for key in idle:
            del self._buckets[key]


class RequestMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, limiter: RateLimiter) -> None:
        super().__init__(app)
        self._limiter = limiter

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
        if self._limiter.allow(key):
            response = await call_next(request)
        else:
            response = error_response(429, "rate_limited", headers={"Retry-After": "60"})

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers.setdefault("Cache-Control", "no-store")
        access_log.info(
            json.dumps(
                {
                    "event": "request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
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
