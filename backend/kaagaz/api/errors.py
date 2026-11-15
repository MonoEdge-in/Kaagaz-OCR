"""Every error the API sends has one shape: ``{"error": {"code", "message"}}``, plus
``details`` on the few errors whose screen needs more (my words' 409 ``word_has_meaning``).

Why: the frontend (``frontend/lib/api.ts``) turns any failed reply into an
``ApiError`` from these two fields. The code is short and stable; the message
is plain English for a customer, with no internals: never document text, file
names, identity numbers, SQL or a stack trace. Validation errors are not
echoed back either, because they can quote what was typed (a password).
"""

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException

from kaagaz.db.documents import RETRY_STUCK_HOURS
from kaagaz.db.sqlstate import INSUFFICIENT_PRIVILEGE, sqlstate
from kaagaz.ingestion.errors import BackendUnavailable

_log = logging.getLogger(__name__)

MESSAGES = {
    "bad_request": "The request is not valid.",
    # PRD 1 A3: one text for a wrong password, an unknown e-mail, a locked account
    # and a wrong code. {minutes} is LOGIN_LOCK_MINUTES: use bad_credentials(). One message:
    # check what was typed, or wait out the lock (QA, 15 Oct 2026).
    "bad_credentials": (
        "E-mail or password is wrong, or the account is locked."
        " Check them, or wait {minutes} minutes, then try again."
    ),
    "not_signed_in": "Please sign in.",
    "forbidden": "You don't have permission to do this.",
    "admin_session_required": "Only admins can use this. Ask your admin if you need it.",
    # MFA required for admins (0035): an admin signs in again and sets up two-step sign-in.
    "mfa_enrol_required": "Set up two-step sign-in to continue. Sign in again to set it up.",
    "gone": "This is not available any more.",
    "password_change_required": "Set a new password to continue.",
    # Council 4 Oct 2026 (PROVISIONAL): a member or reviewer allowed to delete forever.
    "password_confirm_required": "Type your password again to delete forever.",
    "csrf": "This request was refused. Reload the page and try again.",
    "not_found": "We couldn't find that.",
    "method_not_allowed": "This action is not available here.",
    "conflict": "That already exists.",
    "folder_cycle": "A folder can't go inside itself or one of its own folders.",
    "folder_too_deep": "Folders can be at most 100 levels deep.",
    "folder_not_empty": "This folder isn't empty. Move or delete what is inside it first.",
    "folder_upload_running": "An upload into this folder is still running. Try again when it finishes.",
    "folder_live": "This folder is already in Documents. Refresh the page.",  # #542
    "folder_not_emptied": "Some files in this folder were not deleted. Try again.",  # #542
    "folder_changed": (
        "Something was added to this folder while it was being deleted. Nothing was deleted. Try again."
    ),
    "not_needs_review": "This value doesn't need review any more.",
    "not_in_trash": "Only a document in Trash can be deleted forever.",
    # PRD 2 X1 (PROVISIONAL wording); issue 407: or one still being read RETRY_STUCK_HOURS on.
    "not_reprocessable": (
        "This document can't be read again right now. Only a ready, failed or unreadable document,"
        f" or one still waiting {RETRY_STUCK_HOURS} hours after it was sent, that is not in Trash can be."
    ),
    # Critical #5: the tab chose the file while another account was signed in. It must
    # not invite a retry: that would send the file into the account signed in now.
    "session_changed": (
        "Not uploaded. A different account has signed in on this browser since you chose this file."
        " Reload the page to see who is signed in now."
    ),
    "too_large": "This file is too large.",
    "empty_file": "This file is empty.",
    "unsupported_type": "This kind of file is not supported.",
    "rate_limited": "Too many requests. Please wait a minute and try again.",
    "backend_unavailable": "The service is busy right now. Please try again in a minute.",
    "internal_error": "Something went wrong on our side.",
    # PRD 7 (#606, Q8): the database rule refused a write the route let through.
    "access_would_change": "This would change who can see a file, so it wasn't done. Ask your admin.",
    # PRD 7 (#606, Q6): an Admin may not upload into a place whose files they can't see.
    "upload_place_hidden": (
        "You can't upload into this folder: you can't see its files. Choose another folder."
    ),
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

    def __init__(
        self,
        status: int,
        code: str,
        message: str | None = None,
        *,
        details: Mapping[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message or MESSAGES.get(code, MESSAGES["bad_request"])
        self.details = details
        self.headers = headers  # e.g. a 429's Retry-After


def bad_credentials(lock_minutes: int) -> ApiError:
    """The one 401 for every failed sign-in, with the lock time filled in."""
    return ApiError(401, "bad_credentials", MESSAGES["bad_credentials"].format(minutes=lock_minutes))


def error_response(
    status: int,
    code: str,
    message: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    details: Mapping[str, Any] | None = None,
) -> JSONResponse:
    text = message or MESSAGES.get(code, MESSAGES["internal_error"])
    error: dict[str, Any] = {"code": code, "message": text}
    if details is not None:
        error["details"] = dict(details)
    return JSONResponse({"error": error}, status_code=status, headers=headers)


async def _api_error(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, ApiError):
        raise TypeError("registered for ApiError only")
    return error_response(exc.status, exc.code, exc.message, headers=exc.headers, details=exc.details)


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


async def _refused_by_rule(request: Request, exc: Exception) -> Response:
    """PRD 7 (#606, Q8): the database rule refused a statement (42501, a row the person can't see):
    a route's own check missed it, so it is logged critical, and the reply is 409, never a 500.
    Any other ProgrammingError is a bug, left to ErrorMiddleware."""
    if not isinstance(exc, ProgrammingError) or sqlstate(exc) != INSUFFICIENT_PRIVILEGE:
        raise exc
    _log.critical("the database rule refused a statement", extra={"code": "access_would_change"})
    return error_response(409, "access_would_change")


def install(app: FastAPI) -> None:
    # The last resort for a bug is kaagaz.api.middleware.ErrorMiddleware: the same
    # JSON shape, and only the error's type in the log.
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(BackendUnavailable, _unavailable)
    app.add_exception_handler(OperationalError, _unavailable)
    app.add_exception_handler(PoolTimeout, _unavailable)  # no free pooled connection
    app.add_exception_handler(ProgrammingError, _refused_by_rule)
