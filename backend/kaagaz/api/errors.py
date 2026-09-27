"""Every error the API sends has one shape: ``{"error": {"code", "message"}}``.

Why: the frontend (``frontend/lib/api.ts``) turns any failed reply into an
``ApiError`` from these two fields. The code is short and stable; the message
is plain English for a customer, with no internals: never document text, file
names, identity numbers, SQL or a stack trace. Validation errors are not
echoed back either, because they can quote what was typed (a password).
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from kaagaz.ingestion.errors import BackendUnavailable

MESSAGES = {
    "bad_request": "The request is not valid.",
    "bad_credentials": "The e-mail or password is not right.",
    "not_signed_in": "Please sign in.",
    "forbidden": "You don't have permission to do this.",
    "csrf": "This request was refused. Reload the page and try again.",
    "not_found": "We couldn't find that.",
    "method_not_allowed": "This action is not available here.",
    "conflict": "That already exists.",
    "too_large": "This file is too large.",
    "empty_file": "This file is empty.",
    "unsupported_type": "This kind of file is not supported.",
    "rate_limited": "Too many requests. Please wait a minute and try again.",
    "backend_unavailable": "The service is busy right now. Please try again in a minute.",
    "internal_error": "Something went wrong on our side.",
}

_STATUS_CODES = {
    400: "bad_request",
    401: "not_signed_in",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "too_large",
    429: "rate_limited",
    503: "backend_unavailable",
}


class ApiError(Exception):
    """Raise anywhere in a route or dependency to send an error reply."""

    def __init__(self, status: int, code: str, message: str | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message or MESSAGES.get(code, MESSAGES["bad_request"])


def error_response(
    status: int, code: str, message: str | None = None, *, headers: dict[str, str] | None = None
) -> JSONResponse:
    text = message or MESSAGES.get(code, MESSAGES["internal_error"])
    return JSONResponse({"error": {"code": code, "message": text}}, status_code=status, headers=headers)


async def _api_error(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, ApiError):
        raise TypeError("registered for ApiError only")
    return error_response(exc.status, exc.code, exc.message)


async def _validation_error(request: Request, exc: Exception) -> Response:
    return error_response(400, "bad_request")


async def _http_error(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, StarletteHTTPException):
        raise TypeError("registered for HTTPException only")
    code = _STATUS_CODES.get(exc.status_code, "bad_request" if exc.status_code < 500 else "internal_error")
    status = exc.status_code if exc.status_code in _STATUS_CODES else (400 if exc.status_code < 500 else 500)
    return error_response(status, code)


async def _unavailable(request: Request, exc: Exception) -> Response:
    return error_response(503, "backend_unavailable")


async def _internal(request: Request, exc: Exception) -> Response:
    # The last resort for a bug: the same JSON shape, and nothing from the error.
    return error_response(500, "internal_error")


def install(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(BackendUnavailable, _unavailable)
    app.add_exception_handler(OperationalError, _unavailable)
    app.add_exception_handler(500, _internal)
