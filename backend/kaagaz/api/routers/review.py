"""GET /api/review and POST /api/review/{value_id}: the correction screen.

Why one transaction for a resolve: the value is locked, changed, its
correction row written, the correction remembered (if asked) and the audit row
written together, so a half-done correction can never be seen. Numbers and
dates are parsed again from the new text with ``kaagaz.extraction.parsing``,
the same rules the pipeline uses, so the fixed queries see the corrected value.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from kaagaz.api.auth import Reviewer
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.views import review_item
from kaagaz.db import audit
from kaagaz.db import review as review_sql

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: Literal["first", "second", "typed"]
    value: str | None = Field(default=None, max_length=500)
    remember: bool


@router.get("/review")
def list_review(
    user: Reviewer,
    context: Context,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    document_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        rows, total = review_sql.list_queue(
            conn,
            user.customer_id,
            limit=limit,
            offset=offset,
            document_id=None if document_id is None else str(document_id),
        )
    return {"items": [review_item(row) for row in rows], "total": total}


@router.post("/review/{value_id}")
def resolve(value_id: uuid.UUID, body: ResolveBody, user: Reviewer, context: Context) -> dict[str, Any]:
    # Imported here so the API can be built and tested before the pipeline's
    # parsing module is (the same lazy wiring as create_app).
    from kaagaz.extraction.parsing import parse_amount, parse_date

    typed = None
    if body.choice == "typed":
        typed = (body.value or "").strip()
        if not typed:
            raise ApiError(400, "bad_request", "Type the correct value.")

    customer_id, value = user.customer_id, str(value_id)
    with context.db.customer(customer_id) as conn:
        current = review_sql.lock_value(conn, customer_id, value)
        if current is None:
            raise ApiError(404, "not_found")
        if body.choice == "first":
            new_text = str(current["value_raw"])
        elif body.choice == "second":
            if current["second_reading"] is None:
                raise ApiError(400, "bad_request", "There is no second reading for this value.")
            new_text = str(current["second_reading"])
        else:
            new_text = str(typed)

        review_sql.apply_correction(
            conn,
            customer_id,
            value,
            value_text=new_text,
            value_number=parse_amount(new_text),
            value_date=parse_date(new_text),
        )
        review_sql.add_correction(
            conn,
            customer_id,
            value,
            old_value=str(current["value_text"]),
            new_value=new_text,
            choice=body.choice,
            user_id=user.user_id,
        )
        if body.remember:
            review_sql.remember_correction(
                conn,
                customer_id,
                field=str(current["field"]),
                raw_value=str(current["value_raw"]),
                corrected_value=new_text,
                user_id=user.user_id,
            )
        audit.record(
            conn,
            customer_id,
            actor_user_id=user.user_id,
            action="review.resolve",
            target_type="extracted_value",
            target_id=value,
            details={
                "choice": body.choice,
                "remember": body.remember,
                "document_id": str(current["document_id"]),
            },
        )
        item = review_sql.get_item(conn, customer_id, value)
        if item is None:  # no page row: not reviewable, and raising here rolls back
            raise ApiError(404, "not_found")
    return review_item(item)
