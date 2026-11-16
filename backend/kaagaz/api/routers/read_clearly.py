"""Read clearly (Tushar, 19 Oct 2026): a clean copy of a whole document.

* ``GET /documents/{id}/clear-copy``: every page's lines as read, in order, with each
  line's kind, table place and region, and the page's reading marks: the main reader's
  lines of the Scanned text panel, capped for the whole document (``views.clear_copy``,
  a top-level ``truncated``). Each line has its key and the correction in force (#527:
  ``views.clear_line``; a reviewer corrects a line through word review's routes).
  Who may see it is who may see the scanned text. No model call. A view of the document
  (#482: ``document.view``, at most once per person per document every half hour).

The page translation route went with its last caller (#328; removed, issue 358).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import Connection, RowMapping

from kaagaz.api import views
from kaagaz.api.auth import User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.locked import refuse_locked
from kaagaz.db import audit, listing, word_review

router = APIRouter()
Context = Annotated[AppContext, Depends(get_context)]


def _readable(conn: Connection, customer_id: str, document_id: uuid.UUID) -> RowMapping:
    """The document, as the page image and scanned text find it: never a rejected one."""
    row = listing.get_document(conn, customer_id, str(document_id))
    if row is None or row["status"] == "rejected":
        raise ApiError(404, "not_found")
    refuse_locked(row)  # PRD 7 (#605)
    return row


@router.get("/documents/{document_id}/clear-copy")
def clear_copy(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """Three statements for the whole document: its pages, then its lines and its marks, each
    unpacking the stored output once, under a document-wide cap (``truncated``)."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.user(customer_id, user.user_id) as conn:
        _readable(conn, customer_id, document_id)
        pages = [page["page_no"] for page in listing.list_pages(conn, customer_id, target)]
        lines, marks = listing.clear_copy(conn, customer_id, target)
        live = word_review.in_force(conn, customer_id, target)  # #527: the corrections
        audit.record_view(conn, customer_id, actor_user_id=user.user_id, document_id=target, via="clear_copy")
    return views.clear_copy(pages, lines, marks, live)
