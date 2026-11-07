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

At start-up (uvicorn's lifespan, never with a test's ``db``) the operator's login is tried once
(``check_operator_login``, QA W1): one that cannot switch into kaagaz_operator stops the API with
one plain log line, instead of a console that fails while /api/health stays green. Then the
settings unsafe on a server (``unsafe_config``, A6, 7 Nov 2026) are logged at error level, sent as
one ops alert and kept for the operator's Health page; the API starts all the same, since a
refusal could take the server down on a deploy.
"""

import logging
import sys
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from kaagaz import ops_alert
from kaagaz.adapters.email import Mailer
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
    email,
    ensemble,
    folder_uploads,
    folders,
    health,
    oidc,
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
from kaagaz.db.sqlstate import sqlstate
from kaagaz.settings import Settings

_log = logging.getLogger("kaagaz.api")

OPERATOR_LOGIN_FAILED = (
    "the super admin's database login cannot be used: set OPS_DATABASE_URL to kaagaz_ops's login"
    " whenever APP_DATABASE_URL is set (docs/running-locally.md, Limited database logins)"
)
DATABASE_UNREACHABLE = (
    "the API cannot reach the database: check that postgres is running and on the api's network"
    " (docs/running-locally.md, The closed lines and the storage keys)"
)
# A6: the settings unsafe on a server, by code, in the order they are checked.
UNSAFE_SAID = {
    "cookie_not_secure": "APP_URL is https but COOKIE_SECURE is false: set COOKIE_SECURE=true",
    "eicar_scanner": "SCANNER is eicar_dev, the laptop's stand-in: uploads are not scanned for viruses",
    "superuser_login": (
        "the API's database login is a superuser, so it could leave the app role and reach every"
        " organisation's rows: use the limited login (docs/running-locally.md, Limited database logins)"
    ),
    "superuser_unchecked": "whether the API's database login is a superuser could not be read at start-up",
}


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
    mailer: Mailer | None = None,
) -> FastAPI:
    owns_db = db is None
    # The API's PostgreSQL timeouts (#246 A1): a request never waits minutes on the database.
    timeouts = {
        "statement_timeout_seconds": settings.db_statement_timeout_seconds,
        "lock_timeout_seconds": settings.db_lock_timeout_seconds,
        "idle_in_transaction_timeout_seconds": settings.db_idle_in_transaction_timeout_seconds,
    }
    if db is None:
        db = Database(make_engine(settings.database_url, **timeouts), settings.db_app_role)
    database = db
    # The operator's own way in (super admin PR 2): only /api/operator asks for it. Its own
    # login, kaagaz_ops, once OPS_DATABASE_URL differs from the app's (#479); until then, and
    # with a test's ``db``, the app's engine, as before.
    operator_engine = database.engine
    if owns_db and settings.ops_database_url != settings.database_url:
        operator_engine = make_engine(settings.ops_database_url, **timeouts)
    operator_db = OperatorDatabase(operator_engine)
    now = clock or _utc_now
    context = AppContext(
        settings=settings,
        db=database,
        storage=storage if storage is not None else _default_storage(settings),
        queue=queue if queue is not None else _default_queue(settings),
        answers=answers if answers is not None else _default_answers(settings, database, now),
        clock=now,
        model=model if model is not None else _default_model(settings, database),
        mailer=mailer if mailer is not None else _default_mailer(settings),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            if owns_db:
                check_operator_login(operator_db)
                context.unsafe_config.extend(unsafe_config(settings, database))
                report_unsafe_config(settings, context.unsafe_config)
            yield
        finally:  # a refused start too: its connections are closed, never left to the collector
            if owns_db:
                database.engine.dispose()
                operator_engine.dispose()  # on release day the same engine: twice is harmless

    app = FastAPI(
        title="Kaagaz",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        dependencies=[Depends(csrf_guard)],
    )
    app.state.kaagaz = context
    app.state.kaagaz_operator = operator_db
    errors.install(app)
    app.add_middleware(
        RequestMiddleware,
        limiter=RateLimiter(settings.rate_limit_per_minute),
        folder_limiter=RateLimiter(settings.folder_upload_calls_per_minute),
    )
    app.add_middleware(ErrorMiddleware)  # added last, so it is the outermost
    for router in (
        health.router,
        auth.router,
        email.router,
        oidc.router,
        documents.router,
        versions.router,
        read_clearly.router,
        batches.router,
        dashboard.router,
        folders.router,
        folder_uploads.router,
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
    return app


def check_operator_login(operator_db: OperatorDatabase) -> None:
    """One ``SELECT 1`` as kaagaz_operator. On failure, one plain log line (the SQLSTATE or the
    error's type, never its message), then RuntimeError, which stops uvicorn's start-up. On the
    server the usual failure is OPS_DATABASE_URL unset while APP_DATABASE_URL is set: Settings
    then gives the operator part the app's login, which may switch into kaagaz_app alone."""
    try:
        with operator_db.operator() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        why = sqlstate(error) if isinstance(error, DBAPIError) else None
        # No SQLSTATE (no answer at all) or class 08 (connection): the database, not the login.
        unreachable = why is None or why.startswith("08")
        said, code = (
            (DATABASE_UNREACHABLE, "database_unreachable")
            if unreachable
            else (OPERATOR_LOGIN_FAILED, "operator_login_failed")
        )
        _log.critical("%s (%s)", said, why or type(error).__name__, extra={"code": code})
        raise RuntimeError(said) from None


def unsafe_config(settings: Settings, database: Database) -> list[str]:
    """A6: the codes (UNSAFE_SAID) of the settings unsafe on a server. One short read of whether
    the login is a superuser; should that fail, it is ``superuser_unchecked`` (its error's type
    logged), never raised: the start goes on."""
    superuser: bool | None = None
    try:
        with database.system() as conn:
            row = conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = session_user")).scalar()
        superuser = bool(row)
    except SQLAlchemyError as error:
        _log.error(
            "the superuser check could not run (%s)", type(error).__name__, extra={"code": "unsafe_config"}
        )
    found = {
        "cookie_not_secure": (settings.app_url or "").startswith("https://") and not settings.cookie_secure,
        "eicar_scanner": settings.scanner == "eicar_dev",
        "superuser_login": superuser is True,
        "superuser_unchecked": superuser is None,
    }
    return [code for code, unsafe in found.items() if unsafe]


def report_unsafe_config(
    settings: Settings, found: list[str], *, mailer: Mailer | None = None
) -> threading.Thread | None:
    """A6: each of ``found`` logged at error level, then one ops alert naming them all, on a thread
    of its own so the start never waits on SES. The thread (tests join it), or None for none."""
    for code in found:
        _log.error(UNSAFE_SAID[code], extra={"code": "unsafe_config"})
    if not found:
        return None
    said = (
        "".join(f"{UNSAFE_SAID[code]}\n" for code in found) + "\nThe operator's Health page lists them too.\n"
    )

    def send() -> None:
        sent = ops_alert.send_ops_alert(
            settings, "Kaagaz server: unsafe settings", said, purpose="unsafe_config", mailer=mailer
        )
        if not sent:  # SES's own refusal is logged by ops_alert; a missing setting is said here
            why = ops_alert.missing(settings) or "SES refused it"
            _log.error("the unsafe settings alert was not sent: %s", why, extra={"code": "unsafe_config"})

    sending = threading.Thread(target=send, daemon=True)
    sending.start()
    return sending


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


def _default_mailer(settings: Settings) -> Mailer | None:
    """Amazon SES while EMAIL_ENABLED is on (#457 phase 0); none while it is off."""
    if not settings.email_enabled or settings.email_from is None:
        return None
    from kaagaz.adapters.email import SesMailer

    return SesMailer(settings.ses_region, settings.email_from)


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
