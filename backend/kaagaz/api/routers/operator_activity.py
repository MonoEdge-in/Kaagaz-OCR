"""What a platform operator sees of the whole platform (super admin PR 5, council 6 Oct
2026, PROVISIONAL). Every route needs the operator cookie (``current_operator`` on the
router); only ``OperatorDatabase`` here, and each read is one ``kaagaz_op_*`` function
(0020). Reads write no ``platform_audit`` row.

* ``GET /operator/activity?before=&org=&action=&limit=``: both audit logs, newest first,
  at most 100 rows a page (50 by default). Each row: which log, the time, the action,
  the organisation (id, name) and the target TYPE, and who acted as a kind (``user``,
  ``operator`` in either log, or ``system``: no person recorded). Never
  ``audit_log.details``, a ``target_id``, a person's id or e-mail, nor a document's
  name. ``next`` is an opaque cursor (base64url of the
  last row's time, log and id) for the next page; null on the last page. ``org``
  keeps one organisation's rows, ``action`` the actions starting with it. An operator
  disabled since the cookie was checked gets 401, as on ``/health``.
* ``GET /operator/health``: per organisation, document counts by status, the failed ones
  by failure code, how many are being read and how long the oldest has waited; the
  totals; organisations purged and swept again. Counts only. PRD 5 U9 adds ``reader``:
  each worker's reader state from its reports of the last 10 minutes (``kaagaz_op_reader``,
  0022; the screen shows "not reporting" after 2 minutes, "slow" for failing), and
  ``queues``: the jobs waiting in each lane, null when the queue does not answer. Status
  only: never an error body, a key or a customer. #246 O1 adds ``release``: the commit
  this API runs (``/health``'s). The deployer's own records (last attempt, last good) are
  files on the server outside every container, so they are not here.
"""

import base64
import uuid
from collections import Counter
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import RowMapping

from kaagaz.api.auth import OperatorSession, current_operator
from kaagaz.api.context import AppContext, get_context, get_operator_db
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.health import running_commit
from kaagaz.api.views import iso
from kaagaz.db import operator_activity as activity_sql
from kaagaz.db import operators as operators_sql
from kaagaz.db import reader_status as reader_sql
from kaagaz.db.engine import OperatorDatabase
from kaagaz.db.operator_activity import SOURCES, Before
from kaagaz.ingestion.errors import BackendUnavailable

router = APIRouter(prefix="/operator", dependencies=[Depends(current_operator)])

Context = Annotated[AppContext, Depends(get_context)]
OperatorDb = Annotated[OperatorDatabase, Depends(get_operator_db)]

BAD_CURSOR = "That page link is not valid. Start again from the newest activity."
_MAX_ID = 2**63 - 1  # bigint


@router.get("/activity")
def list_activity(
    operator: OperatorSession,
    db: OperatorDb,
    before: Annotated[str | None, Query(max_length=200, pattern=r"^[A-Za-z0-9_-]+$")] = None,
    org: uuid.UUID | None = None,
    action: Annotated[str | None, Query(pattern=r"^[a-z_.]{1,64}$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    cursor = None if before is None else decode_cursor(before)
    with db.operator() as conn:
        rows = activity_sql.activity(
            conn,
            operator.operator_id,
            before=cursor,
            customer_id=None if org is None else str(org),
            action_prefix=action,
            limit=limit,
        )
        # No row is also what the function gives an operator disabled since current_operator.
        signed_out = not rows and operators_sql.session_operator(conn, operator.operator_id) is None
    if signed_out:
        raise ApiError(401, "not_signed_in")
    last = rows[-1] if len(rows) == limit else None
    following = None if last is None else Before(last["created_at"], last["source"], int(last["entry_id"]))
    return {
        "items": [_entry(row) for row in rows],
        "next": None if following is None else encode_cursor(following),
    }


@router.get("/health")
def get_health(operator: OperatorSession, db: OperatorDb, context: Context) -> dict[str, Any]:
    with db.operator() as conn:
        rows = activity_sql.health(conn, operator.operator_id)
        purges = activity_sql.purges(conn, operator.operator_id)
        readers = reader_sql.read(conn, operator.operator_id)
    if purges is None:  # disabled meanwhile
        raise ApiError(401, "not_signed_in")
    now = context.clock()
    by_status: Counter[str] = Counter()
    by_code: Counter[str] = Counter()
    for row in rows:
        by_status.update(row["documents_by_status"])
        by_code.update(row["failed_by_code"])
    waiting = [row["oldest_in_progress_at"] for row in rows if row["oldest_in_progress_at"] is not None]
    return {
        "organisations": [_health(row, now) for row in rows],
        "totals": {
            "documents_by_status": dict(by_status),
            "failed_by_code": dict(by_code),
            "in_progress": sum(int(row["in_progress"]) for row in rows),
            "oldest_in_progress_seconds": _seconds(now, min(waiting)) if waiting else None,
        },
        "purges": {
            "purged": int(purges["purged"]),
            "last_purged_at": _when(purges["last_purged_at"]),
            "swept": int(purges["swept"]),
            "last_swept_at": _when(purges["last_swept_at"]),
        },
        "reader": [_reader(row) for row in readers],
        "queues": _queues(context),
        "release": {"commit": running_commit()},
    }


def encode_cursor(before: Before) -> str:
    raw = f"{before.created_at.isoformat()}|{before.source}|{before.entry_id}".encode("ascii")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(value: str) -> Before:
    """The cursor ``encode_cursor`` made, else 400: an aware time, a known log, an id."""
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        when, source, entry = raw.decode("ascii").split("|")
        created_at = datetime.fromisoformat(when)
    except ValueError:  # not base64, not ASCII, not three parts, not a time
        raise ApiError(400, "bad_request", BAD_CURSOR) from None
    if created_at.tzinfo is None or source not in SOURCES or not (entry.isascii() and entry.isdigit()):
        raise ApiError(400, "bad_request", BAD_CURSOR)
    entry_id = int(entry)
    if not 1 <= entry_id <= _MAX_ID:
        raise ApiError(400, "bad_request", BAD_CURSOR)
    return Before(created_at, source, entry_id)


def _entry(row: RowMapping) -> dict[str, Any]:
    """Exactly these keys: no details, target id, person or document."""
    return {
        "source": row["source"],
        "at": iso(row["created_at"]),
        "action": row["action"],
        "organisation_id": None if row["customer_id"] is None else str(row["customer_id"]),
        "organisation_name": row["customer_name"],
        "target_type": row["target_type"],
        "by": row["actor"],
    }


def _health(row: RowMapping, now: datetime) -> dict[str, Any]:
    oldest: datetime | None = row["oldest_in_progress_at"]
    return {
        "id": str(row["customer_id"]),
        "name": row["name"],
        "status": row["status"],
        "delete_after": _when(row["delete_after"]),
        "documents_by_status": dict(row["documents_by_status"]),
        "failed_by_code": dict(row["failed_by_code"]),
        "in_progress": int(row["in_progress"]),
        "oldest_in_progress_at": _when(oldest),
        "oldest_in_progress_seconds": None if oldest is None else _seconds(now, oldest),
    }


def _reader(row: RowMapping) -> dict[str, Any]:
    """``ReaderReport``: exactly these keys."""
    return {
        "worker": row["worker"],
        "state": row["state"],
        "since": _when(row["since"]),
        "seen_at": iso(row["seen_at"]),
    }


def _queues(context: AppContext) -> dict[str, int] | None:
    """The jobs waiting in each lane, or None when the queue does not answer (the
    reason is never shown)."""
    try:
        depth = context.queue.depth()
    except BackendUnavailable:
        return None
    return {"express": int(depth["express"]), "main": int(depth["main"])}


def _seconds(now: datetime, since: datetime) -> int:
    return max(0, int((now - since).total_seconds()))


def _when(value: datetime | None) -> str | None:
    return None if value is None else iso(value)
