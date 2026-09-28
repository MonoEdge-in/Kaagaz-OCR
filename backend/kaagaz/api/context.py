"""Everything a request handler needs, built once by ``create_app``.

Why one object: routes and dependencies get it from ``request.app.state``
through ``get_context``, so tests can build the app with fakes and nothing
reads globals.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import Request

from kaagaz.api.ports import Answers, ApiQueue, ApiStorage
from kaagaz.db.engine import Database
from kaagaz.settings import Settings


@dataclass(frozen=True, slots=True)
class AppContext:
    settings: Settings
    db: Database
    storage: ApiStorage
    queue: ApiQueue
    answers: Answers
    clock: Callable[[], datetime]


def get_context(request: Request) -> AppContext:
    context = request.app.state.kaagaz
    if not isinstance(context, AppContext):
        raise TypeError("the app was not built by create_app")
    return context
