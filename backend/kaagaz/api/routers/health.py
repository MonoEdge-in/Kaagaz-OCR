"""GET /api/health: can the API reach the database, storage and the queue?

Why: the load balancer and ``docker compose`` need one call that says whether
this instance can serve: ``{"status": "ok"}``, or ``{"status": "degraded"}`` with a 503 when
any check fails. Anyone may ask, so it says nothing more (A5, 7 Nov 2026): which part failed
(``components``, each ``ok`` or ``error``, never why) and the running commit are on the
operator's Health page (routers/operator_activity.py).

``running_commit`` (#246 O1) is the commit this image was built from: the deployer builds each
release with ``KAAGAZ_TAG=<commit>``, which compose passes to backend/Dockerfile as the
KAAGAZ_COMMIT build argument. "local" for an image built by hand or on a laptop.
"""

import os
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from kaagaz.api.context import AppContext, get_context
from kaagaz.ingestion.errors import BackendUnavailable

router = APIRouter()


def running_commit() -> str:
    """The commit this image was built from (KAAGAZ_COMMIT), "local" when not given."""
    return os.environ.get("KAAGAZ_COMMIT", "").strip() or "local"


def _check(ping: Callable[[], None]) -> str:
    try:
        ping()
    except BackendUnavailable:
        return "error"
    return "ok"


def components(context: AppContext) -> dict[str, str]:
    """Each part the API needs: ``ok`` or ``error``."""
    return {
        "database": _check(context.db.ping),
        "storage": _check(context.storage.ping),
        "queue": _check(context.queue.ping),
    }


@router.get("/health")
def health(context: Annotated[AppContext, Depends(get_context)]) -> JSONResponse:
    healthy = all(value == "ok" for value in components(context).values())
    return JSONResponse({"status": "ok" if healthy else "degraded"}, status_code=200 if healthy else 503)
