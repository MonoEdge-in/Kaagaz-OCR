"""AI settings by task (Tushar, 17 Oct 2026; spec Outside-Folder/prd/ai-task-settings-spec.md,
0038): how much effort the AI puts into scanning, Ask, Ensemble and auto-filing. Scanning's
level is also its reader (#398, ``kaagaz.db.ai_settings``): none the local reader only, save
the local reader for clean printed pages and the AI for the rest, low to max the AI for
every page.

* ``GET /ai-settings``: each task's effective level, the person's own choice, the
  organisation's default and lock, the levels the ceiling leaves (``allowed``, lowest first)
  and ``available`` (false: the super admin's switch or ceiling removed the task); and
  ``can_edit_organisation`` (admins). Never a model name, a price or a cost (Tushar, 15 Oct
  2026).
* ``PUT /ai-settings/me`` {tasks: {task: level | null}}: the person's own choices (null
  removes one). A locked task is 409 ``locked``; a level above ``allowed``, of the other
  kind, or for a task not available is 400 ``not_allowed``. Nothing is saved unless every
  task passes.
* ``PUT /ai-settings/organisation`` {tasks: {task: {level, locked}}}: admins only (403
  ``forbidden``), the defaults and locks (a null level removes the default and its lock).

The server checks everything again (``kaagaz.db.ai_settings``); every change writes one
audit row (who, the task, the old and new levels). Time: a handful of statements, O(1).
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection

from kaagaz.api.auth import Admin, CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.readers import usable
from kaagaz.db import admin as admin_sql
from kaagaz.db import ai_settings
from kaagaz.db import models as models_sql
from kaagaz.db.ai_settings import Task
from kaagaz.settings import Settings

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]
TaskName = Literal["scan", "ask", "ensemble", "autofile"]
Level = Literal["none", "save", "low", "high", "max", "off", "on"]  # save: scan only (#398)
LOCKED = "Set by your admin."
NOT_ALLOWED = "Not available for your organisation."


class MineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: dict[TaskName, Level | None]


class OrganisationTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Level | None
    locked: bool = False


class OrganisationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: dict[TaskName, OrganisationTask]


@router.get("/ai-settings")
def get_ai_settings(user: User, context: Context) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        tasks = _tasks(conn, user, context.settings)
    return _reply(user, tasks)


@router.put("/ai-settings/me")
def save_mine(body: MineBody, user: User, context: Context) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        tasks = _tasks(conn, user, context.settings)
        for name, level in body.tasks.items():
            task = tasks[name]
            if level is not None and task.locked:
                raise ApiError(409, "locked", LOCKED)
            _check(task, level)
        for name, level in body.tasks.items():
            ai_settings.save(
                conn,
                user.customer_id,
                tasks[name],
                user_id=user.user_id,
                level=level,
                locked=False,
                by=user.user_id,
            )
        tasks = _tasks(conn, user, context.settings)
    return _reply(user, tasks)


@router.put("/ai-settings/organisation")
def save_organisation(body: OrganisationBody, user: Admin, context: Context) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        tasks = _tasks(conn, user, context.settings)
        for name, choice in body.tasks.items():
            _check(tasks[name], choice.level)
        for name, choice in body.tasks.items():
            ai_settings.save(
                conn,
                user.customer_id,
                tasks[name],
                user_id=None,
                level=choice.level,
                locked=choice.locked and choice.level is not None,
                by=user.user_id,
            )
        tasks = _tasks(conn, user, context.settings)
    return _reply(user, tasks)


def _check(task: Task, level: str | None) -> None:
    """400 ``not_allowed`` for a level the task can't take now (null always may: it removes)."""
    if level is not None and (not task.available or level not in task.allowed):
        raise ApiError(400, "not_allowed", NOT_ALLOWED)


def _tasks(conn: Connection, user: CurrentUser, settings: Settings) -> dict[str, Task]:
    """Every task for ``user``: the ceiling the organisation has now (with the platform
    switches), the saved rows, the rule. Five statements."""
    on = ai_settings.switches(conn, user.customer_id)
    ceiling = models_sql.effective_settings(conn, user.customer_id, settings=settings)
    org = admin_sql.get_settings(conn, user.customer_id)
    saved = ai_settings.rows(conn, user.customer_id, user.user_id)
    ensemble = bool(ceiling["ensemble"]) and len(usable(conn, user.customer_id, settings)) >= 2
    autofile = org is not None and bool(org["autofile_granted"]) and on["autofile"]
    autofile_on = org is not None and bool(org["autofile_on"])
    return {
        "scan": ai_settings.resolve(
            "scan", saved, *ai_settings.scan_ceiling(ceiling, settings), available=on["scan_ai"]
        ),
        "ask": ai_settings.resolve("ask", saved, *ai_settings.ask_ceiling(ceiling), available=on["ask"]),
        "ensemble": ai_settings.resolve(
            "ensemble", saved, *ai_settings.grant_ceiling(ensemble, ensemble), available=ensemble
        ),
        "autofile": ai_settings.resolve(
            "autofile", saved, *ai_settings.grant_ceiling(autofile, autofile_on), available=autofile
        ),
    }


def _reply(user: CurrentUser, tasks: dict[str, Task]) -> dict[str, Any]:
    return {"tasks": [task.view() for task in tasks.values()], "can_edit_organisation": user.role == "admin"}
