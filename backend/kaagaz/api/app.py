"""Build the FastAPI app: ``create_app(settings, *, db=None, storage=None, ...)``.

Why everything is a keyword argument: tests pass in-memory fakes for storage,
the queue and the answer service, and a real test database. Anything not
passed is built from ``settings``. The real adapters, the model client and
the answer service are imported only here, inside the default wiring, so the
API can be built and tested with fakes on its own.

The API docs pages (/docs, /openapi.json) are switched off: they list every
route to anyone who asks, and the frontend does not need them.

``configure_logging(level)`` is the API process's log setup, which
``kaagaz.api.main`` calls once.
"""

import logging
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI

from kaagaz.ai.client import ModelClient
from kaagaz.api import errors
from kaagaz.api.auth import csrf_guard
from kaagaz.api.context import AppContext
from kaagaz.api.middleware import ErrorMiddleware, RateLimiter, RequestMiddleware
from kaagaz.api.ports import Answers, ApiQueue, ApiStorage
from kaagaz.api.routers import admin, auth, chat, documents, folders, health, review, search, tags, words
from kaagaz.db.engine import Database, make_engine
from kaagaz.settings import Settings


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(
    settings: Settings,
    *,
    db: Database | None = None,
    storage: ApiStorage | None = None,
    queue: ApiQueue | None = None,
    answers: Answers | None = None,
    clock: Callable[[], datetime] | None = None,
    model: ModelClient | None = None,
) -> FastAPI:
    owns_db = db is None
    database = db if db is not None else Database(make_engine(settings.database_url), settings.db_app_role)
    now = clock or _utc_now
    context = AppContext(
        settings=settings,
        db=database,
        storage=storage if storage is not None else _default_storage(settings),
        queue=queue if queue is not None else _default_queue(settings),
        answers=answers if answers is not None else _default_answers(settings, database, now),
        clock=now,
        model=model if model is not None else _default_model(settings),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_db:
            database.engine.dispose()

    app = FastAPI(
        title="Kaagaz",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        dependencies=[Depends(csrf_guard)],
    )
    app.state.kaagaz = context
    errors.install(app)
    app.add_middleware(RequestMiddleware, limiter=RateLimiter(settings.rate_limit_per_minute))
    app.add_middleware(ErrorMiddleware)  # added last, so it is the outermost
    for router in (
        health.router,
        auth.router,
        documents.router,
        folders.router,
        tags.router,
        chat.router,
        search.router,
        review.router,
        admin.router,
        words.router,
    ):
        app.include_router(router, prefix="/api")
    return app


def configure_logging(level: str) -> None:
    """One JSON line per log record, the worker's format: an exception's type only,
    never its message or traceback. On the root logger and on ``uvicorn.error``,
    which uvicorn set up before importing the app and which would otherwise print
    tracebacks through its own handler (so it no longer propagates to it).

    uvicorn's own access log is switched off: its line holds the query string, and
    a search there can be a name. The access line comes from RequestMiddleware."""
    # Imported here, like the adapters below: the worker module brings them in.
    from kaagaz.worker_main import JsonFormatter

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
    uvicorn_errors = logging.getLogger("uvicorn.error")
    uvicorn_errors.handlers = [handler]
    uvicorn_errors.propagate = False
    logging.getLogger("uvicorn.access").disabled = True


def _default_storage(settings: Settings) -> ApiStorage:
    from kaagaz.adapters.storage import S3Storage

    return S3Storage(settings)


def _default_queue(settings: Settings) -> ApiQueue:
    from kaagaz.adapters.queue import SqsQueue

    return SqsQueue(settings)


def _default_model(settings: Settings) -> ModelClient | None:
    from kaagaz.ai.client import build_model_client

    return build_model_client(settings)


def _default_answers(settings: Settings, db: Database, clock: Callable[[], datetime]) -> Answers:
    from kaagaz.ai.client import build_model_client
    from kaagaz.answering.service import AnswerService

    return AnswerService(db=db, model=build_model_client(settings), settings=settings, clock=clock)
