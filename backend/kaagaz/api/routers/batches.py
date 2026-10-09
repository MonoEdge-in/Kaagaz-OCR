"""One drop of files (PRD 5 Q1): ``GET /api/batches/{batch_id}``, for the batch tray (U1)
and the upload rows' reading stage (U10).

The browser makes the id and sends it with each file of the drop (POST /api/documents,
``batch_id``). Any signed-in user may read a batch, but only their own files in it: the
query filters on the customer, the uploader and Trash (``listing.batch_rows``), as well
as row level security. Another customer's batch, another user's and one that is not
there are the same 404, so nothing tells them apart. At most MAX_BATCH_ROWS files.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from kaagaz.api.auth import User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.views import batch_view
from kaagaz.db import listing
from kaagaz.db import tags as tags_sql

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]


@router.get("/batches/{batch_id}")
def get_batch(batch_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """``BatchView``: the files, their counts, pages read over total and the time left.
    Two queries: the files, then their tags (one batch)."""
    customer_id, target = user.customer_id, str(batch_id)
    with context.db.customer(customer_id) as conn:
        rows = listing.batch_rows(conn, customer_id, target, user_id=user.user_id)
        if not rows:
            raise ApiError(404, "not_found")
        tags = tags_sql.tags_for_documents(conn, customer_id, [str(row["id"]) for row in rows])
    settings = context.settings
    return batch_view(
        target,
        rows,
        max_pages=settings.max_pages,
        tags=tags,
        user=user,
        now=context.clock(),
        max_active=settings.max_active_per_customer,
    )
