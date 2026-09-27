"""GET /api/health: can the API reach the database, storage and the queue?

Why: the load balancer and ``docker compose`` need one call that says whether
this instance can serve. Each check reports ``ok`` or ``error`` only, never why,
and any error makes the reply 503.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from kaagaz.api.context import AppContext, get_context
from kaagaz.ingestion.errors import BackendUnavailable

router = APIRouter()


def _check(ping: Callable[[], None]) -> str:
    try:
        ping()
    except BackendUnavailable:
        return "error"
    return "ok"


@router.get("/health")
def health(context: Annotated[AppContext, Depends(get_context)]) -> JSONResponse:
    checks = {
        "database": _check(context.db.ping),
        "storage": _check(context.storage.ping),
        "queue": _check(context.queue.ping),
    }
    healthy = all(value == "ok" for value in checks.values())
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", **checks}, status_code=200 if healthy else 503
    )
