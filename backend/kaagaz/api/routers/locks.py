"""Locked files: lock, unlock and who may open one (PRD 7 section 6, PR 12, #605).

Admins only, from an admin session, each after typing their own password again (p2:29; one entry
covers a whole batch). The order of every write is the access API's (kaagaz/api/routers/access.py):
the file found first with no lock held (a person who can't see it gets 404, as for one that never
was, then 403; an Admin finds any file by ``kaagaz_admin_document``), then the password, proved with
no connection held (``prove_password``: counted on the sign-in lock), then one transaction: the tree
lock, the people checked again, one owner-rights function of 0093 that takes the family locks and
reads every file again under them (all or nothing), the audit rows, the jobs queued last, and a
Word or PowerPoint file's stored PDF deleted last of all, so storage that is down rolls the lock
back.

* ``POST /documents/{id}/lock`` and ``POST /documents/lock``: lock files, every version, and allow
  ``open_to`` to open them (left out: the Admin alone, "ticked by default"); 409 ``in_trash``,
  ``not_lockable`` or ``already_locked``, a batch ``{refused: [{id, reason}]}`` and nothing locked.
  A version whose pages have no pictures yet is queued for the worker, which prepares it and
  never reads it.
* ``POST /documents/{id}/unlock`` and ``POST /documents/unlock``: the lock and its grants go; the
  current version is read again, billed to the Admin, past the hourly limit and the cooldown (the
  reading pause still holds it in the worker), audited ``document.reprocess`` (via unlock).
* ``GET``, ``PUT`` and ``DELETE /documents/{id}/lock-grants/{user}``: who may open a locked file, on
  every version. The Admin giving it to themselves types their password and the other Admins and
  Management are emailed when it lets them open the file (PRD 7 decision 11, question 17).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from kaagaz.api.auth import CurrentUser, User, require_admin_session
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.access import (
    MAX_FILES,
    Password,
    _admin_document,
    _enabled,
    _self_grant,
    _tell,
    _watchers,
)
from kaagaz.api.routers.admin import MAX_PASSWORD
from kaagaz.api.routers.auth import prove_password
from kaagaz.api.views import iso
from kaagaz.db import audit, listing
from kaagaz.db import folders as folders_sql
from kaagaz.db import locks as locks_sql
from kaagaz.ingestion.ports import Job

router = APIRouter()
Context = Annotated[AppContext, Depends(get_context)]
TypedPassword = Annotated[str, Field(min_length=1, max_length=MAX_PASSWORD)]
OpenTo = Annotated[list[uuid.UUID] | None, Field(default=None, max_length=locks_sql.MAX_OPEN_TO)]

ADMIN_ONLY = "Only an admin can lock or unlock a file."
NOT_LOCKABLE = "This kind of file can't be locked yet. Keep it in a private folder instead."  # p2:24
_REFUSED = {
    "in_trash": (409, "in_trash", "This file is in the Trash. Restore it first."),
    "kind": (409, "not_lockable", NOT_LOCKABLE),
    "no_pictures": (409, "not_lockable", NOT_LOCKABLE),
    "already_locked": (409, "already_locked", "This file is locked already."),
    "not_locked": (409, "not_locked", "This file is not locked."),
}


class LockBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_to: OpenTo = None  # left out: the Admin who locks it (p2:30)
    password: TypedPassword


class BulkLockBody(LockBody):
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_FILES)


class UnlockBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: TypedPassword


class BulkUnlockBody(UnlockBody):
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_FILES)


class GrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: Password = None  # needed when the Admin allows themselves


def _found(conn: Connection, user: CurrentUser, document_ids: list[str]) -> None:
    """Every file there before anything else (``_admin_document``: a person who can't see one gets
    404 as for one that never was, then 403; an Admin, 404 for one not there)."""
    for document_id in document_ids:
        _admin_document(conn, user, document_id)


def _admin_batch(user: CurrentUser) -> None:
    if user.role != "admin":
        raise ApiError(403, "forbidden", ADMIN_ONLY)
    require_admin_session(user)


def _refused(refused: dict[str, str], *, batch: bool) -> ApiError:
    if not batch:
        reason = next(iter(refused.values()))
        if reason == "not_found":  # deleted since it was found
            return ApiError(404, "not_found")
        return ApiError(*_REFUSED[reason])
    items = [{"id": document_id, "reason": reason} for document_id, reason in sorted(refused.items())]
    return ApiError(409, "refused", "Nothing was changed.", details={"refused": items})


def _lock(
    context: AppContext,
    user: CurrentUser,
    documents: list[str],
    open_to: list[uuid.UUID] | None,
    *,
    batch: bool,
) -> locks_sql.Outcome:
    """The write transaction of a lock (the module notes)."""
    customer_id = user.customer_id
    people = [user.user_id] if open_to is None else list(dict.fromkeys(str(each) for each in open_to))
    with context.db.user(customer_id, user.user_id) as conn:
        folders_sql._lock_tree(conn, customer_id)
        for person in people:
            _enabled(conn, user, person)
        outcome = locks_sql.lock(conn, documents, people)
        if outcome.refused:
            raise _refused(outcome.refused, batch=batch)
        if outcome.already and not batch:
            raise ApiError(*_REFUSED["already_locked"])
        versions: dict[str, int] = {}
        for version in outcome.versions:
            versions[version.document_id] = versions.get(version.document_id, 0) + 1
        for document_id, count in versions.items():
            details: dict[str, audit.DetailValue] = {"versions": count, "open_to": len(people)}
            if batch:
                details["batch"] = len(documents)
            _audit(conn, user, "lock.lock", document_id, details)
        for version in outcome.versions:  # last but one: a queue that is down rolls the lock back
            if version.queue:
                context.queue.enqueue(Job(customer_id, version.version_id))
        for version in outcome.versions:  # last: storage that is down rolls everything back
            if version.rendition:
                context.storage.delete_rendition(customer_id, version.version_id)
    return outcome


def _unlock(
    context: AppContext, user: CurrentUser, documents: list[str], *, batch: bool
) -> locks_sql.Outcome:
    """The write transaction of an unlock (the module notes)."""
    customer_id = user.customer_id
    with context.db.user(customer_id, user.user_id) as conn:
        folders_sql._lock_tree(conn, customer_id)
        outcome = locks_sql.unlock(conn, documents)
        if outcome.refused:
            raise _refused(outcome.refused, batch=batch)
        versions: dict[str, int] = {}
        for version in outcome.versions:
            versions[version.document_id] = versions.get(version.document_id, 0) + 1
        for document_id, count in versions.items():
            details: dict[str, audit.DetailValue] = {"versions": count}
            if batch:
                details["batch"] = len(documents)
            _audit(conn, user, "lock.unlock", document_id, details)
        for version in outcome.versions:
            if version.queue:
                # Read again at one reading cost billed to the Admin (``kaagaz_unlock`` named them),
                # never counted on the hourly limit (p2:31): its audit says how it came.
                _audit(conn, user, "document.reprocess", version.version_id, {"via": "unlock"})
        for version in outcome.versions:  # last: a queue that is down rolls the unlock back
            if version.queue:
                context.queue.enqueue(Job(customer_id, version.version_id))
    return outcome


def _audit(
    conn: Connection, user: CurrentUser, action: str, document_id: str, details: dict[str, audit.DetailValue]
) -> None:
    audit.record(
        conn,
        user.customer_id,
        actor_user_id=user.user_id,
        action=action,
        target_type="document",
        target_id=document_id,
        details=details,
    )


@router.post("/documents/lock")
def lock_files(body: BulkLockBody, user: User, context: Context) -> dict[str, int]:
    """Lock up to MAX_FILES files at once, all or nothing: ``{locked}``, the files locked now (one
    locked already is left as it is). One password entry covers the batch."""
    _admin_batch(user)
    documents = list(dict.fromkeys(str(each) for each in body.document_ids))
    with context.db.user(user.customer_id, user.user_id) as conn:
        _found(conn, user, documents)
    prove_password(context, user, body.password)
    outcome = _lock(context, user, documents, body.open_to, batch=True)
    return {"locked": len({version.document_id for version in outcome.versions})}


@router.post("/documents/unlock")
def unlock_files(body: BulkUnlockBody, user: User, context: Context) -> dict[str, int]:
    """Unlock up to MAX_FILES files at once, all or nothing: ``{unlocked}``."""
    _admin_batch(user)
    documents = list(dict.fromkeys(str(each) for each in body.document_ids))
    with context.db.user(user.customer_id, user.user_id) as conn:
        _found(conn, user, documents)
    prove_password(context, user, body.password)
    outcome = _unlock(context, user, documents, batch=True)
    return {"unlocked": len({version.document_id for version in outcome.versions})}


@router.post("/documents/{document_id}/lock")
def lock_file(document_id: uuid.UUID, body: LockBody, user: User, context: Context) -> dict[str, Any]:
    """Lock a file, every version: ``{locked, versions, open_to}``."""
    target = str(document_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        _found(conn, user, [target])
    prove_password(context, user, body.password)
    outcome = _lock(context, user, [target], body.open_to, batch=False)
    people = 1 if body.open_to is None else len(set(body.open_to))
    return {"locked": True, "versions": len(outcome.versions), "open_to": people}


@router.post("/documents/{document_id}/unlock")
def unlock_file(document_id: uuid.UUID, body: UnlockBody, user: User, context: Context) -> dict[str, Any]:
    """Unlock a file: ``{unlocked, reading}``, reading whether its current version is read again."""
    target = str(document_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        _found(conn, user, [target])
    prove_password(context, user, body.password)
    outcome = _unlock(context, user, [target], batch=False)
    return {"unlocked": True, "reading": any(version.queue for version in outcome.versions)}


@router.get("/documents/{document_id}/lock-grants")
def list_lock_grants(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """Who may open this locked file: ``{items: [{user: {id, name, disabled}, given_at, given_by}]}``."""
    target = str(document_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        _admin_document(conn, user, target)
        rows = locks_sql.grants(conn, user.customer_id, target)
    return {
        "items": [
            {
                "user": {
                    "id": str(row["user_id"]),
                    "name": row["display_name"],
                    "disabled": bool(row["disabled"]),
                },
                "given_at": iso(row["given_at"]),
                "given_by": {"id": str(row["given_by"]), "name": row["given_by_name"]},
            }
            for row in rows
        ]
    }


@router.put("/documents/{document_id}/lock-grants/{user_id}")
def grant_lock(
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    body: GrantBody,
    user: User,
    background: BackgroundTasks,
    context: Context,
) -> dict[str, bool]:
    """Allow a person to open a locked file, every version: ``{changed}``. 409 ``not_locked``; a
    person switched off: 409 ``disabled``."""
    target, person, customer_id = str(document_id), str(user_id), user.customer_id
    mine = person == user.user_id
    with context.db.user(customer_id, user.user_id) as conn:
        _admin_document(conn, user, target)
    if body.password is not None:
        prove_password(context, user, body.password)
    with context.db.user(customer_id, user.user_id) as conn:
        folders_sql._lock_tree(conn, customer_id)
        found = _enabled(conn, user, person)
        if mine:
            _self_grant(user, body.password)
        outcome = locks_sql.grant(conn, target, person)
        if outcome == "not_found":
            raise ApiError(404, "not_found")
        if outcome == "disabled":
            raise ApiError(409, "disabled", "This person is switched off.")
        if outcome == "not_locked":
            raise ApiError(*_REFUSED["not_locked"])
        changed = outcome == "granted"
        opens = tell = False
        if changed:
            details: dict[str, audit.DetailValue] | None = {"self": True} if mine else None
            audit.record(
                conn,
                customer_id,
                actor_user_id=user.user_id,
                action="lock.grant",
                target_type="document",
                target_id=target,
                details=details,
                subject_user_id=person,
            )
            # Q17: the other Admins and Management hear of it only when it lets the Admin open the
            # file, that is, when they could already see it (R1 to R5): the grant alone opens nothing.
            opens = mine and listing.get_document(conn, customer_id, target) is not None
        # As a file given (access.give_file): also when it is another Admin's.
        tell = opens or (changed and found.admin and not mine)
        watchers = _watchers(conn, context, user) if tell else []
    if tell:
        _tell(background, context, user, "lock", None if mine else found.name, watchers)
    return {"changed": changed}


@router.delete("/documents/{document_id}/lock-grants/{user_id}", status_code=204)
def revoke_lock(document_id: uuid.UUID, user_id: uuid.UUID, user: User, context: Context) -> Response:
    """Take a person's lock grant back, every version. 404 when they had none."""
    target, person, customer_id = str(document_id), str(user_id), user.customer_id
    with context.db.user(customer_id, user.user_id) as conn:
        _admin_document(conn, user, target)
        folders_sql._lock_tree(conn, customer_id)
        if not locks_sql.revoke(conn, target, person):
            raise ApiError(404, "not_found")
        audit.record(
            conn,
            customer_id,
            actor_user_id=user.user_id,
            action="lock.revoke",
            target_type="document",
            target_id=target,
            subject_user_id=person,
        )
    return Response(status_code=204)
