"""Everything a request handler needs, built once by ``create_app``.

Why one object: routes and dependencies get it from ``request.app.state``
through ``get_context``, so tests can build the app with fakes and nothing
reads globals.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import Request

from kaagaz.adapters.email import Mailer
from kaagaz.ai.client import ModelClient
from kaagaz.api.ports import Answers, ApiQueue, ApiStorage
from kaagaz.api.upload_guard import SendSlots
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.settings import Settings


@dataclass(frozen=True, slots=True)
class AppContext:
    settings: Settings
    db: Database
    storage: ApiStorage
    queue: ApiQueue
    answers: Answers
    clock: Callable[[], datetime]
    # The model GET /api/search embeds questions with; None with the model off.
    model: ModelClient | None = None
    # Email (#457 phase 0); None with EMAIL_ENABLED off (kaagaz.api.emails).
    mailer: Mailer | None = None
    # Folder upload (#484): the files this process is taking at once (upload_guard.SendSlots).
    sends: SendSlots = field(default_factory=SendSlots)
    # A6: the settings unsafe on a server found at start-up (kaagaz.api.app.unsafe_config).
    unsafe_config: list[str] = field(default_factory=list)


def get_operator_db(request: Request) -> OperatorDatabase:
    """The operator's database. ``create_app`` keeps it beside the context, not in
    it, so only the /api/operator routes, which ask for it, can reach it."""
    database = request.app.state.kaagaz_operator
    if not isinstance(database, OperatorDatabase):
        raise TypeError("the app was not built by create_app")
    return database


def get_context(request: Request) -> AppContext:
    context = request.app.state.kaagaz
    if not isinstance(context, AppContext):
        raise TypeError("the app was not built by create_app")
    return context
