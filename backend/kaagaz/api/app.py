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
from kaagaz.api.routers import (
    admin,
    ai_settings,
    answer_quality,
    auth,
    batches,
    chat,
    dashboard,
    documents,
    ensemble,
    folders,
    health,
    keycloak,
    operator,
    operator_activity,
    operator_insights,
    operator_models,
    operator_orgs,
    read_clearly,
    review,
    search,
    stories,
    tags,
    versions,
    word_review,
    words,
)
from kaagaz.db.engine import Database, OperatorDatabase, make_engine
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
    if db is None:
        # The API's PostgreSQL timeouts (#246 A1): a request never waits minutes on the database.
        engine = make_engine(
            settings.database_url,
            statement_timeout_seconds=settings.db_statement_timeout_seconds,
            lock_timeout_seconds=settings.db_lock_timeout_seconds,
            idle_in_transaction_timeout_seconds=settings.db_idle_in_transaction_timeout_seconds,
        )
        db = Database(engine, settings.db_app_role)
    database = db
    now = clock or _utc_now
    context = AppContext(
        settings=settings,
        db=database,
        storage=storage if storage is not None else _default_storage(settings),
        queue=queue if queue is not None else _default_queue(settings),
        answers=answers if answers is not None else _default_answers(settings, database, now),
        clock=now,
        model=model if model is not None else _default_model(settings, database),
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
    # The operator's own way in (super admin PR 2): only /api/operator asks for it.
    app.state.kaagaz_operator = OperatorDatabase(database.engine)
    errors.install(app)
    app.add_middleware(RequestMiddleware, limiter=RateLimiter(settings.rate_limit_per_minute))
    app.add_middleware(ErrorMiddleware)  # added last, so it is the outermost
    for router in (
        health.router,
        auth.router,
        documents.router,
        versions.router,
        read_clearly.router,
        batches.router,
        dashboard.router,
        folders.router,
        tags.router,
        chat.router,
        ai_settings.router,
        search.router,
        review.router,
        admin.router,
        answer_quality.router,
        words.router,
        stories.router,
        words.router,
        word_review.router,
        ensemble.router,
        operator.router,
        operator.signed_in,
        operator_orgs.router,
        operator_activity.router,
        operator_models.router,
        operator_insights.router,
    ):
        app.include_router(router, prefix="/api")
    if settings.keycloak_enabled:  # off, its routes are not there at all (#435)
        app.include_router(keycloak.router, prefix="/api")
    return app


def configure_logging(level: str) -> None:
    """One JSON line per log record, the worker's format: an exception's type only,
    never its message or traceback. On the root logger and on ``uvicorn.error``,
    which uvicorn set up before importing the app and which would otherwise print
    tracebacks through its own handler (so it no longer propagates to it).

    uvicorn's own access log is switched off: its line holds the query string, and
    a search there can be a name. The access line comes from RequestMiddleware."""
    # Imported here, like the adapters below: the worker module brings them in.
    from kaagaz.worker_main import JsonFormatter, quiet_parser_logs

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
    quiet_parser_logs()  # the same libraries are imported here (the readers, the page count)
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


def _default_model(settings: Settings, db: Database) -> ModelClient | None:
    from kaagaz.ai.client import build_model_client

    return build_model_client(settings, db=db)


def _default_answers(settings: Settings, db: Database, clock: Callable[[], datetime]) -> Answers:
    from kaagaz.ai.client import build_model_client
    from kaagaz.answering.service import AnswerService

    # The question translation (council 4 Oct 2026) waits at most its own timeout; the service
    # uses it only with ANSWER_TRANSLATE_QUESTION on.
    translator = build_model_client(
        settings, chat_timeout_seconds=settings.model_translate_timeout_seconds, db=db
    )
    model = build_model_client(settings, db=db)
    return AnswerService(db=db, model=model, settings=settings, clock=clock, translator=translator)
