"""The company admin's Answer quality page (PRD 6 F2, decision 7, 24 Oct 2026).

* ``GET /admin/answer-quality?status=open|handled&weeks=&limit=&offset=``: the answers
  marked Not useful after the notice in the last ``weeks`` weeks (this one included,
  Monday to Sunday in TIMEZONE), newest first, at most 100 a page: who asked (their name;
  the notice told them), the question, the answer as it may be shown now (a source moved to
  Trash or deleted loses its words, ``answer_with_source_states``), the reasons, the typed
  reason and when it was given; and Useful and Not useful counts for each of those weeks.
* ``PUT /admin/answer-quality/{feedback_id}/handled`` ``{handled}``: Mark as handled, or
  open it again (the screen's Undo). Audited as ``feedback.handled`` or ``feedback.reopened``
  with ids only, never the question, the answer or the reason.

Admins only, on the router (``require_admin_session``): a member or reviewer gets 403.
Another company's id matches no row (row level security and the customer filter): 404.
Ids go in the path, never a name or a text (Caddy logs URLs). Everything is sent as plain
text for the screen to show as text.
"""

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from kaagaz.api.auth import AdminSession, require_admin_session
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.views import answer_with_source_states, cited_document_ids, iso
from kaagaz.db import answer_quality as quality_sql
from kaagaz.db import audit
from kaagaz.db import chat as chat_sql

router = APIRouter(dependencies=[Depends(require_admin_session)])

Context = Annotated[AppContext, Depends(get_context)]

MAX_WEEKS = 52


class HandledBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handled: bool


def first_monday(now: datetime, zone: str, weeks: int) -> datetime:
    """Midnight in ``zone`` of the Monday ``weeks - 1`` weeks before this week's."""
    today = now.astimezone(ZoneInfo(zone)).date()
    monday = today - timedelta(days=today.weekday() + 7 * (weeks - 1))
    return datetime(monday.year, monday.month, monday.day, tzinfo=ZoneInfo(zone))


@router.get("/admin/answer-quality")
def list_answer_quality(
    user: AdminSession,
    context: Context,
    status: Literal["open", "handled"] = "open",
    weeks: Annotated[int, Query(ge=1, le=MAX_WEEKS)] = 4,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Three statements: the page, the documents its answers cite, the weekly counts."""
    zone = context.settings.timezone
    since = first_monday(context.clock(), zone, weeks)
    with context.db.customer(user.customer_id) as conn:
        rows, total = quality_sql.not_useful(
            conn, user.customer_id, since=since, handled=status == "handled", limit=limit, offset=offset
        )
        cited = [document_id for row in rows for document_id in cited_document_ids(row["answer"])]
        states = chat_sql.document_states(conn, user.customer_id, cited)
        counts = quality_sql.weekly_counts(conn, user.customer_id, since=since, zone=zone)
    mondays = [(since + timedelta(days=7 * n)).date().isoformat() for n in range(weeks)]
    return {
        "from": since.date().isoformat(),
        "weeks": [
            {
                "week": monday,
                "useful": counts.get(monday, (0, 0))[0],
                "not_useful": counts.get(monday, (0, 0))[1],
            }
            for monday in reversed(mondays)
        ],
        "items": [
            {
                "id": str(row["id"]),
                "message_id": str(row["message_id"]),
                "asked_by": row["asked_by"],
                "question": row["question"],
                "answer": answer_with_source_states(row["answer"], states),
                "reason_codes": list(row["reason_codes"]),
                "reason": row["reason"],
                "given_at": iso(row["created_at"]),
                "handled_at": None if row["handled_at"] is None else iso(row["handled_at"]),
            }
            for row in rows
        ],
        "total": total,
    }


@router.put("/admin/answer-quality/{feedback_id}/handled")
def set_handled(
    feedback_id: uuid.UUID, body: HandledBody, user: AdminSession, context: Context
) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        row = quality_sql.set_handled(
            conn, user.customer_id, str(feedback_id), user.user_id, handled=body.handled
        )
        if row is None:
            raise ApiError(404, "not_found")
        audit.record(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action="feedback.handled" if body.handled else "feedback.reopened",
            target_type="feedback",
            target_id=str(feedback_id),
            details={"message_id": str(row["message_id"])},
        )
    return {
        "id": str(feedback_id),
        "handled_at": None if row["handled_at"] is None else iso(row["handled_at"]),
    }
