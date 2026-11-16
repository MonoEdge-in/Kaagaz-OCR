"""One drop of files (PRD 5 Q1): ``GET /api/batches/{batch_id}``, for the batch tray (U1)
and the upload rows' reading stage (U10).

The browser makes the id and sends it with each file of the drop (POST /api/documents,
``batch_id``). Any signed-in user may read a batch, but only their own files in it: the
query filters on the customer, the uploader and Trash (``listing.batch_rows``), as well
as row level security. Another customer's batch, another user's and one that is not
there are the same 404, so nothing tells them apart. At most MAX_FILES_PER_BATCH files,
the upload's own cap.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from kaagaz.api.auth import User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.documents import zip_unpacks
from kaagaz.api.views import batch_view
from kaagaz.db import listing
from kaagaz.db import tags as tags_sql
from kaagaz.db import versions as versions_sql

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]


@router.get("/batches/{batch_id}")
def get_batch(batch_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """``BatchView``: the files, their counts, pages read over total and the time left, and
    (File management V5) the batch's version suggestions still to answer and its automatic
    links (``VersionSuggestion``, db/versions.py) with their count. Three queries: the files,
    their tags, the suggestions."""
    customer_id, target = user.customer_id, str(batch_id)
    settings = context.settings
    with context.db.user(customer_id, user.user_id) as conn:
        rows = listing.batch_rows(
            conn, customer_id, target, user_id=user.user_id, limit=settings.max_files_per_batch
        )
        if not rows:
            raise ApiError(404, "not_found")
        tags = tags_sql.tags_for_documents(conn, customer_id, [str(row["id"]) for row in rows])
        found = versions_sql.in_batch(
            conn, customer_id, target, user_id=user.user_id, limit=settings.max_files_per_batch
        )
        unpacks = zip_unpacks(conn, customer_id, rows)  # #522 PR 3: a zip's unpacking
    view = batch_view(
        target,
        rows,
        max_pages=settings.max_pages,
        tags=tags,
        user=user,
        now=context.clock(),
        max_active=settings.max_active_per_customer,
        unpacks=unpacks,
    )
    # V5: the tray's "N files look like new versions", with Review.
    return {**view, "version_suggestion_count": len(found), "version_suggestions": found}
