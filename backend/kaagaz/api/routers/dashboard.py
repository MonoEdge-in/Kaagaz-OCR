"""The dashboard, the home screen (File management D1, D2): one request, plain SQL counts
over the current, untrashed documents inside the customer's transaction
(``listing.dashboard``). Any signed-in user. The week is the last 7 days; the month starts
on its first day in the customer's timezone (TIMEZONE)."""

from datetime import timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from kaagaz.api.auth import User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.views import iso
from kaagaz.db import listing

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]


@router.get("/dashboard")
def get_dashboard(user: User, context: Context) -> dict[str, Any]:
    now = context.clock()
    local = now.astimezone(ZoneInfo(context.settings.timezone))
    month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with context.db.customer(user.customer_id) as conn:
        found = listing.dashboard(conn, user.customer_id, week=now - timedelta(days=7), month=month)
    tiles, departments = found["tiles"], found["departments"]
    return {
        "tiles": {
            "documents": int(tiles["documents"]),
            "documents_this_week": int(tiles["this_week"]),
            "departments": len(departments),
            "new_versions_this_month": int(tiles["new_versions"]),
            "versions_to_confirm": int(tiles["to_confirm"]),
        },
        "by_type": [{"doc_type": row["doc_type"], "count": int(row["n"])} for row in found["by_type"]],
        "departments": [
            {
                "id": str(row["id"]),
                "name": row["name"],
                "sort_order": row["sort_order"],
                "files": int(row["files"]),
            }
            for row in departments
        ],
        "not_in_department": found["not_in_department"],
        "recent": [
            {
                "id": str(row["id"]),
                "display_name": row["display_name"],
                "doc_type": row["doc_type"],
                "sensitivity": row["sensitivity"],
                "version_no": int(row["version_no"]),
                "status": row["status"],
                "created_at": iso(row["created_at"]),
            }
            for row in found["recent"]
        ],
        "activity": [
            {
                "id": int(row["id"]),
                "action": row["action"],
                "actor_user_id": None if row["actor_user_id"] is None else str(row["actor_user_id"]),
                "actor_name": row["actor_name"],
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "details": row["details"],
                "created_at": iso(row["created_at"]),
            }
            for row in found["activity"]
        ],
    }
