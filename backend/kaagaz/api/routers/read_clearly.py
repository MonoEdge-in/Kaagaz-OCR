"""Read clearly (Tushar, 19 Oct 2026): a clean copy of a whole document, and a page of it
in English, Hindi or Marathi.

* ``GET /documents/{id}/clear-copy``: every page's lines as read, in order, with each
  line's kind, table place and region, and the page's reading marks: the main reader's
  lines of the Scanned text panel, capped for the whole document (``views.clear_copy``,
  a top-level ``truncated``).
  Who may see it is who may see the scanned text. No model call. Never logged.
* ``POST /documents/{id}/pages/{n}/translate`` ``{language}``: that page's lines in the
  language (kaagaz.answering.page_translate), ``cached`` when a second view made no model
  call; every line or none: 503 ``backend_unavailable`` when the model can't be reached,
  422 ``page_too_long`` past the page's cap, 422 ``translation_unusable`` for a reply that
  could not be used. It uses AI, so it follows Ask: 403 ``feature_off`` while Ask is
  switched off for the organisation (0038, 0039) or the asker's Ask level is None. The
  model call is outside any transaction, billed to the asker as ``translate``. No model
  name is ever in a reply.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, RowMapping

from kaagaz.ai.usage import usage_scope
from kaagaz.answering.page_translate import MODEL_DOWN, PAGE_TOO_LONG, NotTranslated, translate_page
from kaagaz.api import views
from kaagaz.api.auth import FEATURE_OFF, User, require_feature
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.views import page_readings
from kaagaz.db import ai_settings, listing
from kaagaz.db.models import effective_settings

router = APIRouter()
Context = Annotated[AppContext, Depends(get_context)]

# Pages a user may ask to translate a minute: a whole document, a few pages at a time.
_limits = RateLimiter(60)


class TranslateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["en", "hi", "mr"]


def _readable(conn: Connection, customer_id: str, document_id: uuid.UUID) -> RowMapping:
    """The document, as the page image and scanned text find it: never a rejected one."""
    row = listing.get_document(conn, customer_id, str(document_id))
    if row is None or row["status"] == "rejected":
        raise ApiError(404, "not_found")
    return row


def _main_reader(readings: dict[str, Any]) -> dict[str, Any]:
    """The main reader's lines (the first role), else the checker's, else none."""
    readers = readings["readers"]
    return readers[0] if readers else {"lines": [], "truncated": False}


@router.get("/documents/{document_id}/clear-copy")
def clear_copy(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """Three statements for the whole document: its pages, then its lines and its marks, each
    unpacking the stored output once, under a document-wide cap (``truncated``)."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        _readable(conn, customer_id, document_id)
        pages = [page["page_no"] for page in listing.list_pages(conn, customer_id, target)]
        lines, marks = listing.clear_copy(conn, customer_id, target)
    return views.clear_copy(pages, lines, marks)


@router.post(
    "/documents/{document_id}/pages/{page_no}/translate", dependencies=[Depends(require_feature("ask"))]
)
def translate(
    document_id: uuid.UUID, page_no: int, body: TranslateBody, user: User, context: Context
) -> dict[str, Any]:
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        _readable(conn, customer_id, document_id)
        if page_no < 1 or listing.get_page(conn, customer_id, target, page_no) is None:
            raise ApiError(404, "not_found")
        saved = ai_settings.rows(conn, customer_id, user.user_id)
        ask = ai_settings.resolve(
            "ask", saved, *ai_settings.ask_ceiling(effective_settings(conn, customer_id))
        )
        if ask.effective == "none":
            raise ApiError(403, "feature_off", FEATURE_OFF)
        rows = listing.page_readings(conn, customer_id, target, page_no)
    if context.model is None:
        raise ApiError(503, "backend_unavailable", headers={"Retry-After": "60"})
    if not _limits.allow(user.user_id):
        raise ApiError(429, "rate_limited", headers={"Retry-After": "60"})
    lines = [line["text"] for line in _main_reader(page_readings(page_no, rows))["lines"]]
    try:
        with usage_scope(
            context.db, customer_id=customer_id, user_id=user.user_id, kind="translate", document_id=target
        ):
            found = translate_page(context.model, (customer_id, target, str(page_no)), lines, body.language)
    except NotTranslated as error:
        if error.code == MODEL_DOWN:
            raise ApiError(503, "backend_unavailable", headers={"Retry-After": "60"}) from None
        raise ApiError(
            422, "page_too_long" if error.code == PAGE_TOO_LONG else "translation_unusable"
        ) from None
    return {"page_no": page_no, "language": body.language, "lines": found.lines, "cached": found.cached}
