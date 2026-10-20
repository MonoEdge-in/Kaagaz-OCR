"""The dashboard, the home screen (File management D1, D2): one request, plain SQL counts
over the current, untrashed documents inside the customer's transaction
(``listing.dashboard``). Any signed-in user. The week is the last 7 days; the month starts
on its first day in the customer's timezone (TIMEZONE)."""

from datetime import timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import text

from kaagaz.api.auth import User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.views import iso, timeline
from kaagaz.db import audit, listing

router = APIRouter()
DASHBOARD_ACTIVITY = 20  # the last 20 activities, A5's member timeline (document, version, folder)

Context = Annotated[AppContext, Depends(get_context)]

# The owner picker and the Owner filter (O1): names only, never an email or a role.
_PEOPLE = text(
    "SELECT id, display_name FROM users WHERE customer_id = :c AND NOT disabled"
    " ORDER BY lower(display_name), id"
)


@router.get("/people")
def list_people(user: User, context: Context) -> dict[str, Any]:
    """``{items: [{id, name}]}``: the company's active users. Any signed-in user."""
    with context.db.customer(user.customer_id) as conn:
        rows = conn.execute(_PEOPLE, {"c": user.customer_id}).all()
    return {"items": [{"id": str(row[0]), "name": row[1]} for row in rows]}


@router.get("/dashboard")
def get_dashboard(user: User, context: Context) -> dict[str, Any]:
    now = context.clock()
    local = now.astimezone(ZoneInfo(context.settings.timezone))
    month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with context.db.customer(user.customer_id) as conn:
        found = listing.dashboard(conn, user.customer_id, week=now - timedelta(days=7), month=month)
        activity = timeline(conn, user.customer_id, audit.Filters(), limit=DASHBOARD_ACTIVITY)["items"]
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
        "activity": activity,
    }
