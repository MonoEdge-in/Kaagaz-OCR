"""Who can see what: the access API's writes (PRD 7 PR 10a, #601).

Why the order of every write: the visibility read first (a folder or file the caller can't see
answers 404 exactly as one that never was), then the caller's role, then the tree lock
(``folders._lock_tree``), then a file's family lock (inside ``kaagaz_give_file`` and
``kaagaz_take_file``), then everything the decision rests on read again under the lock (the
folder, the Head tick, whether the person is switched off), then one statement that writes, its
audit row in the same transaction, and any email after the commit. Every write that changes
access raises the organisation's access version through 0085's triggers on the row the tree lock
holds; a no-op raises nothing.

Why the self-grant step (PRD 7, decision 11): an Admin who gives themselves access (Management, a
membership or the Head tick, a name on a private folder, a file, or opening a Department only
department or a private folder to themselves) types their password (403 ``password_required``;
``prove_password``: 400 ``wrong_password``, counted on the sign-in lock, and ``password_locked``);
the audit row says ``self``; and every enabled Management person and every other Admin is emailed
the kind of access only. Every Management grant is emailed too (lead decision 3).

The /admin routes need an admin session (``admin_router``). The folder and file routes take any
signed-in person, so a hidden id is checked before the role: 404 for one the person can't see,
then 403 (an Admin sees every folder's name, and finds every file by ``kaagaz_admin_document``).
"""

import uuid
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from kaagaz.api import emails
from kaagaz.api.auth import AdminSession, CurrentUser, User, require_admin_session
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.admin import MAX_PASSWORD
from kaagaz.api.routers.auth import prove_password
from kaagaz.api.views import admin_user, iso
from kaagaz.db import access as access_sql
from kaagaz.db import audit, listing
from kaagaz.db import folders as folders_sql
from kaagaz.db import users as users_sql

admin_router = APIRouter(dependencies=[Depends(require_admin_session)])
router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]
Password = Annotated[str | None, Field(default=None, min_length=1, max_length=MAX_PASSWORD)]
MAX_NAMED = 50
MAX_FILES = 100  # lead decision 12: bulk giving, 100 files x 20 people
MAX_PEOPLE = 20
SELF_GRANT = "Type your password to give yourself access."
NOT_A_DEPARTMENT = "Choose a department: a top-level folder of kind department."


class ManagementBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: bool
    password: Password = None


class MemberBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    head: bool | None = None  # left out: a new member is not Head, a member keeps their tick
    password: Password = None


class SwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: bool
    password: Password = None


class PrivateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: bool
    named: list[uuid.UUID] = Field(default=[], max_length=MAX_NAMED)
    password: Password = None


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: Password = None


class BulkGiveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_FILES)
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_PEOPLE)
    password: Password = None


# --- the steps every write shares ---------------------------------------------------------------


def _password_given(context: AppContext, user: CurrentUser, password: str | None) -> None:
    """A password sent is checked first, with no connection held (it opens its own short ones),
    whether or not this write turns out to need it (an Admin opening a wall to themselves is known
    only under the tree lock)."""
    if password is not None:
        prove_password(context, user, password)


def _self_grant(user: CurrentUser, password: str | None) -> None:
    """The Admin gives themselves access: the password must have come (checked by
    ``_password_given``), else 403."""
    if password is None:
        raise ApiError(403, "password_required", SELF_GRANT)


def _enabled(conn: Connection, user: CurrentUser, target: str) -> access_sql.Person:
    """A person of this organisation who is switched on: 404 unknown, 409 ``disabled``."""
    found = access_sql.person(conn, user.customer_id, target)
    if found is None:
        raise ApiError(404, "not_found")
    if found.disabled:
        raise ApiError(409, "disabled", "This person is switched off.")
    return found


def _audit(
    conn: Connection,
    user: CurrentUser,
    action: str,
    target: tuple[str, str],
    subject: str | None,
    details: Mapping[str, audit.DetailValue] | None = None,
) -> None:
    audit.record(
        conn,
        user.customer_id,
        actor_user_id=user.user_id,
        action=action,
        target_type=target[0],
        target_id=target[1],
        details=details,
        subject_user_id=subject,
    )


def _tell(
    background: BackgroundTasks,
    context: AppContext,
    user: CurrentUser,
    kind: str,
    person: str | None,
) -> None:
    """After the commit: ``access_given`` to every enabled Management person and admin but the
    actor. ``person``: who was given it (None: the actor themselves). Sent for an Admin's own
    access, every Management grant, and any access given to a person who is an Admin (lead
    decision: an Admin's second account is still an Admin's)."""
    emails.after(background, "access_given", _send_access_given, context, user, kind, person)


def _send_access_given(context: AppContext, user: CurrentUser, kind: str, person: str | None) -> None:
    if not emails.email_on(context):
        return
    with context.db.customer(user.customer_id) as conn:
        watchers = users_sql.access_watchers(conn, user.customer_id, but=user.user_id)
    for address, name in watchers:
        message = emails.access_given(name, user.customer_name, user.display_name, person, kind)
        emails.send(context, address, "access_given", message)


def _department(conn: Connection, user: CurrentUser, department_id: str) -> access_sql.Place:
    """A live top-level department: 404 none (in Trash too), 409 ``not_a_department``."""
    place = access_sql.place(conn, user.customer_id, department_id)
    if place is None:
        raise ApiError(404, "not_found")
    if place.parent_id is not None or place.kind != "department":
        raise ApiError(409, "not_a_department", NOT_A_DEPARTMENT)
    return place


def _department_only(conn: Connection, user: CurrentUser, department_id: str | None) -> bool:
    if department_id is None:
        return False
    place = access_sql.place(conn, user.customer_id, department_id)
    return place is not None and place.department_only


# --- people (routes 2 to 5) --------------------------------------------------------------------


@admin_router.get("/admin/users/{user_id}/access")
def get_user_access(user_id: uuid.UUID, user: AdminSession, context: Context) -> dict[str, Any]:
    """The Access page: ``{user, departments, private_folders, files, locked}`` (PR 12 fills
    ``locked``). True names (decision 20). Another organisation's person is 404."""
    target, customer_id = str(user_id), user.customer_id
    with context.db.user(customer_id, user.user_id) as conn:
        found = access_sql.person(conn, customer_id, target)
        if found is None:
            raise ApiError(404, "not_found")
        role = users_sql.role_of(conn, customer_id, target)
        member_of = access_sql.departments(conn, customer_id, target)
        named = access_sql.named_on(conn, customer_id, target)
        given = access_sql.given_to(conn, customer_id, target)
        places = [str(row["id"]) for row in named] + [str(r["folder_id"]) for r in given if r["folder_id"]]
        paths = access_sql.paths(conn, customer_id, places)
    return {
        "user": {
            "id": target,
            "name": found.name,
            "role": role,
            "management": found.management,
            "disabled": found.disabled,
        },
        "departments": [
            {
                "id": str(row["department_id"]),
                "name": row["name"],
                "head": bool(row["is_head"]),
                "added_at": iso(row["added_at"]),
                "added_by": str(row["added_by"]),
            }
            for row in member_of
        ],
        "private_folders": [
            {"id": str(row["id"]), "name": row["name"], "path": paths.get(str(row["id"]))} for row in named
        ],
        "files": [
            {
                "id": str(row["id"]),
                "name": row["display_name"],
                "path": None if row["folder_id"] is None else paths.get(str(row["folder_id"])),
                "given_at": iso(row["given_at"]),
                "given_by": None if row["given_by"] is None else str(row["given_by"]),
            }
            for row in given
        ],
        "locked": [],
    }


@admin_router.put("/admin/users/{user_id}/management")
def set_management(
    user_id: uuid.UUID,
    body: ManagementBody,
    user: AdminSession,
    background: BackgroundTasks,
    context: Context,
) -> dict[str, Any]:
    """Management on or off (``AdminUser``). Switched off: 409 ``disabled``. Every grant is emailed."""
    target, customer_id = str(user_id), user.customer_id
    mine = body.on and target == user.user_id
    _password_given(context, user, body.password)
    with context.db.user(customer_id, user.user_id) as conn:
        folders_sql._lock_tree(conn, customer_id)
        found = _enabled(conn, user, target)
        if mine:
            _self_grant(user, body.password)
        row = users_sql.set_management(conn, customer_id, target, on=body.on)
        if row is None:  # can't happen: switched on, under the tree lock
            raise ApiError(404, "not_found")
        changed = found.management != body.on
        if changed:
            details = {"self": True} if mine else None
            action = "access.management_on" if body.on else "access.management_off"
            _audit(conn, user, action, ("user", target), target, details)
    if changed and body.on:
        _tell(background, context, user, "management", None if mine else found.name)
    return admin_user(row, now=context.clock())


@admin_router.put("/admin/users/{user_id}/departments/{department_id}")
def add_member(
    user_id: uuid.UUID,
    department_id: uuid.UUID,
    body: MemberBody,
    user: AdminSession,
    background: BackgroundTasks,
    context: Context,
) -> dict[str, Any]:
    """Add the person to the department, or set their Head tick (left out: a new member is not
    Head, and a member keeps theirs): ``{head, changed}``."""
    target, department, customer_id = str(user_id), str(department_id), user.customer_id
    _password_given(context, user, body.password)
    with context.db.user(customer_id, user.user_id) as conn:
        _department(conn, user, department)
        folders_sql._lock_tree(conn, customer_id)
        found = _enabled(conn, user, target)
        _department(conn, user, department)
        before = access_sql.membership(conn, customer_id, department, target)
        head = bool(before) if body.head is None and before is not None else bool(body.head)
        gains = before is None or (head and not before)
        mine = target == user.user_id and gains
        if mine:
            _self_grant(user, body.password)
        added = access_sql.join(conn, customer_id, department, target, head=body.head, by=user.user_id)
        if added is not None:
            details: dict[str, audit.DetailValue] = {"head": head}
            if mine:
                details["self"] = True
            action = "access.member_add" if added else ("access.head_on" if head else "access.head_off")
            _audit(conn, user, action, ("folder", department), target, details)
    if added is not None and gains and (mine or found.admin):
        _tell(background, context, user, "head" if head else "member", None if mine else found.name)
    return {"head": head, "changed": added is not None}


@admin_router.delete("/admin/users/{user_id}/departments/{department_id}", status_code=204)
def remove_member(
    user_id: uuid.UUID, department_id: uuid.UUID, user: AdminSession, context: Context
) -> Response:
    """Take the person out of the department; files given to them directly stay (PRD 7). 404 when
    they are not a member."""
    target, department, customer_id = str(user_id), str(department_id), user.customer_id
    with context.db.user(customer_id, user.user_id) as conn:
        folders_sql._lock_tree(conn, customer_id)
        was_head = access_sql.leave(conn, customer_id, department, target)
        if was_head is None:
            raise ApiError(404, "not_found")
        _audit(conn, user, "access.member_remove", ("folder", department), target, {"was_head": was_head})
    return Response(status_code=204)


# --- Department only (route 6) -------------------------------------------------------------------


@admin_router.put("/admin/departments/{department_id}/department-only")
def set_department_only(
    department_id: uuid.UUID,
    body: SwitchBody,
    user: AdminSession,
    background: BackgroundTasks,
    context: Context,
) -> dict[str, Any]:
    """Department only on or off: ``{id, department_only, members, warnings}`` (``no_members``:
    on, with nobody in it). Off, an Admin who gains the files takes the self-grant step."""
    department, customer_id = str(department_id), user.customer_id
    _password_given(context, user, body.password)
    with context.db.user(customer_id, user.user_id) as conn:
        _department(conn, user, department)
        folders_sql._lock_tree(conn, customer_id)
        place = _department(conn, user, department)
        members = access_sql.members(conn, customer_id, department)
        gained = False
        if place.department_only != body.on:
            before = access_sql.place_audience(conn, department, None, department_only=place.department_only)
            after = access_sql.place_audience(conn, department, None, department_only=body.on)
            gained = user.user_id in after - before
            if gained:
                _self_grant(user, body.password)
            access_sql.set_department_only(conn, customer_id, department, on=body.on)
            details: dict[str, audit.DetailValue] = {"members": members, "losing": len(before - after)}
            if gained:
                details["self"] = True
            action = "access.department_only_on" if body.on else "access.department_only_off"
            _audit(conn, user, action, ("folder", department), None, details)
    if gained:
        _tell(background, context, user, "wall", None)
    return {
        "id": department,
        "department_only": body.on,
        "members": members,
        "warnings": ["no_members"] if body.on and not members else [],
    }


# --- private folders and their named list (routes 7 to 9) -------------------------------------


def _folder_seen(conn: Connection, user: CurrentUser, folder_id: str) -> access_sql.Place:
    """Visibility first: a folder the person can't see is 404, as one that never was."""
    place = access_sql.place(conn, user.customer_id, folder_id)
    if place is None:
        raise ApiError(404, "not_found")
    return place


def _admin_or_head(conn: Connection, user: CurrentUser, place: access_sql.Place) -> None:
    """An Admin (with an admin session), or the Head of the folder's department; else 403."""
    if user.role == "admin":
        require_admin_session(user)
        return
    if place.department_id is None or place.department_id not in access_sql.me(conn)[1]:
        raise ApiError(403, "forbidden", "Only an admin or the head of its department can do this.")


def _named_reply(conn: Connection, user: CurrentUser, place: access_sql.Place) -> list[dict[str, str]]:
    return access_sql.people_on(conn, user.customer_id, place.id) if place.private else []


@router.put("/folders/{folder_id}/private")
def set_private(
    folder_id: uuid.UUID, body: PrivateBody, user: User, background: BackgroundTasks, context: Context
) -> dict[str, Any]:
    """Make a folder private, naming people on it, or open it again (the names are kept, unused,
    so making it private again brings them back): ``{folder, private, named, you_lose_access}``.
    Admins only. Refused: 409 ``department`` (use Department only), ``inside_private``,
    ``holds_private``. The password is proved between two transactions, holding no connection."""
    target, customer_id = str(folder_id), user.customer_id
    named = list(dict.fromkeys(str(each) for each in body.named))
    if named and not body.on:
        raise ApiError(400, "bad_request", "Name people only on a private folder.")
    with context.db.user(customer_id, user.user_id) as conn:
        _folder_seen(conn, user, target)
        if user.role != "admin":
            raise ApiError(403, "forbidden", "Only an admin can make a folder private.")
        require_admin_session(user)
    _password_given(context, user, body.password)
    with context.db.user(customer_id, user.user_id) as conn:
        folders_sql._lock_tree(conn, customer_id)
        place = _folder_seen(conn, user, target)
        people = {person: _enabled(conn, user, person) for person in named}
        switched = place.private != body.on
        if switched and body.on:
            if place.parent_id is None and place.kind == "department":
                raise ApiError(409, "department", "Make a department Department only instead.")
            if place.private_folder_id is not None:
                raise ApiError(409, "inside_private", "This folder is inside a private folder already.")
            inside = folders_sql.descendant_ids(conn, customer_id, target)
            if access_sql.holds_private(conn, customer_id, target, inside):
                raise ApiError(409, "holds_private", "A folder inside this one is private already.")
        closed = _department_only(conn, user, place.department_id)
        before = access_sql.place_audience(
            conn, place.department_id, place.private_folder_id, department_only=closed
        )
        if switched:
            access_sql.set_private(conn, customer_id, target, on=body.on)
        newly = [
            person for person in named if access_sql.name(conn, customer_id, target, person, by=user.user_id)
        ]
        place = _folder_seen(conn, user, target)
        after = access_sql.place_audience(
            conn, place.department_id, place.private_folder_id, department_only=closed
        )
        mine = user.user_id in newly or user.user_id in after - before
        if mine:
            _self_grant(user, body.password)
        if switched:
            details: dict[str, audit.DetailValue] = {"named": len(newly), "losing": len(before - after)}
            if mine:
                details["self"] = True
            action = "access.private_on" if body.on else "access.private_off"
            _audit(conn, user, action, ("folder", target), None, details)
        for person in newly:
            _audit(conn, user, "access.named_add", ("folder", target), person, _self(user, person))
        reply_named = _named_reply(conn, user, place)
    if mine:
        _tell(background, context, user, "named" if user.user_id in newly else "wall", None)
    for person in newly:
        if person != user.user_id and people[person].admin:
            _tell(background, context, user, "named", people[person].name)
    return {
        "folder": {"id": place.id, "name": place.name},
        "private": place.private,
        "named": reply_named,
        "you_lose_access": user.user_id in before - after,
    }


def _self(user: CurrentUser, person: str) -> dict[str, audit.DetailValue] | None:
    return {"self": True} if person == user.user_id else None


def _private_seen(conn: Connection, user: CurrentUser, folder_id: str) -> access_sql.Place:
    """Routes 8 and 9: the folder seen (404), its Admin or Head (403), private (409)."""
    place = _folder_seen(conn, user, folder_id)
    _admin_or_head(conn, user, place)
    if not place.private:
        raise ApiError(409, "not_private", "This folder isn't private.")
    return place


@router.get("/folders/{folder_id}/named")
def get_named(folder_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """A private folder's list: ``{folder: {id, name, path}, named, heads, can_edit}``. An Admin,
    or the Head of its department; ``can_edit`` says which may change it."""
    target = str(folder_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        place = _private_seen(conn, user, target)
        path = access_sql.paths(conn, user.customer_id, [target]).get(target)
        heads = access_sql.me(conn)[1]
        return {
            "folder": {"id": place.id, "name": place.name, "path": path},
            "named": access_sql.people_on(conn, user.customer_id, target),
            "heads": access_sql.heads_of(conn, user.customer_id, place.department_id),
            "can_edit": user.role == "admin" or place.department_id in heads,
        }


def _change_named(
    context: AppContext, user: CurrentUser, folder_id: str, person: str, password: str | None, *, add: bool
) -> tuple[bool, str | None]:
    """Name or unname: the checks in a short first transaction, the password proved with no
    connection held, then the write under the tree lock, with the folder and the Head tick read
    again there. (changed, whom to tell: "" the Admin themselves, a named Admin's name, None
    nobody). A person unknown or switched off is 404 (one answer, for a Head too)."""
    customer_id = user.customer_id
    with context.db.user(customer_id, user.user_id) as conn:
        _private_seen(conn, user, folder_id)
    _password_given(context, user, password)
    with context.db.user(customer_id, user.user_id) as conn:
        folders_sql._lock_tree(conn, customer_id)
        _private_seen(conn, user, folder_id)
        if not add:
            if not access_sql.unname(conn, customer_id, folder_id, person):
                raise ApiError(404, "not_found")
            _audit(conn, user, "access.named_remove", ("folder", folder_id), person)
            return True, None
        found = access_sql.person(conn, customer_id, person)
        if found is None or found.disabled:
            raise ApiError(404, "not_found")
        mine = person == user.user_id
        if mine and user.role == "admin":
            _self_grant(user, password)
        changed = access_sql.name(conn, customer_id, folder_id, person, by=user.user_id)
        if changed:
            _audit(conn, user, "access.named_add", ("folder", folder_id), person, _self(user, person))
    if not changed or not found.admin:  # an Admin's own, or any Admin's: emailed
        return changed, None
    return changed, "" if mine else found.name


@router.put("/folders/{folder_id}/named/{user_id}")
def add_named(
    folder_id: uuid.UUID,
    user_id: uuid.UUID,
    body: PasswordBody,
    user: User,
    background: BackgroundTasks,
    context: Context,
) -> dict[str, bool]:
    """Name a person on a private folder: ``{changed}`` (false: they were already)."""
    changed, tell = _change_named(context, user, str(folder_id), str(user_id), body.password, add=True)
    if tell is not None:
        _tell(background, context, user, "named", tell or None)
    return {"changed": changed}


@router.delete("/folders/{folder_id}/named/{user_id}", status_code=204)
def remove_named(folder_id: uuid.UUID, user_id: uuid.UUID, user: User, context: Context) -> Response:
    """Take a person off a private folder's list; 404 when they are not on it."""
    _change_named(context, user, str(folder_id), str(user_id), None, add=False)
    return Response(status_code=204)


# --- files given directly (routes 10 to 13) ---------------------------------------------------


def _admin_document(conn: Connection, user: CurrentUser, document_id: str) -> access_sql.AdminDocument:
    """An Admin finds any file (404 only for one that isn't there); anyone else gets 404 for a
    file they can't see, as for one that never was, then 403."""
    if user.role != "admin":
        if listing.get_document(conn, user.customer_id, document_id) is None:
            raise ApiError(404, "not_found")
        raise ApiError(403, "forbidden", "Only an admin can give a file to someone.")
    require_admin_session(user)
    found = access_sql.admin_document(conn, document_id)
    if found is None:
        raise ApiError(404, "not_found")
    return found


def _live(found: access_sql.AdminDocument) -> None:
    if found.in_trash:
        raise ApiError(409, "in_trash", "This file is in the Trash. Restore it first.")


@router.get("/documents/{document_id}/shares")
def list_shares(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """Who this file version is given to: ``{items: [{user: {id, name}, given_at, given_by}]}``."""
    target = str(document_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        _admin_document(conn, user, target)
        rows = access_sql.shares(conn, user.customer_id, target)
    return {
        "items": [
            {
                "user": {"id": str(row["user_id"]), "name": row["display_name"]},
                "given_at": iso(row["given_at"]),
                "given_by": None
                if row["given_by"] is None
                else {"id": str(row["given_by"]), "name": row["given_by_name"]},
            }
            for row in rows
        ]
    }


def _give(conn: Connection, user: CurrentUser, document: str, people: list[str]) -> tuple[int, list[str]]:
    """Give one file to people under its family lock (``kaagaz_give_file``: one lock, one insert),
    with one audit row per person it changed something for, in one statement: (the versions it
    has now, those given it now). Sent to Trash meanwhile: 409."""
    versions, added = access_sql.give(conn, document, people)
    if not versions:
        _live(_admin_document(conn, user, document))
        raise ApiError(404, "not_found")
    given = [person for person in people if added.get(person)]
    if given:
        audit.record_about(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action="access.file_give",
            document_id=document,
            subjects={
                person: {"versions": versions, **({"self": True} if person == user.user_id else {})}
                for person in given
            },
        )
    return versions, given


@router.put("/documents/{document_id}/shares/{user_id}")
def give_file(
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    body: PasswordBody,
    user: User,
    background: BackgroundTasks,
    context: Context,
) -> dict[str, Any]:
    """Give a file to a person on every version it has now (and every later one): ``{versions,
    changed}``. In Trash: 409 ``in_trash``; a person switched off: 409 ``disabled``. The password
    is proved between two transactions, holding no connection."""
    target, person, customer_id = str(document_id), str(user_id), user.customer_id
    mine = person == user.user_id
    with context.db.user(customer_id, user.user_id) as conn:
        _admin_document(conn, user, target)
    _password_given(context, user, body.password)
    with context.db.user(customer_id, user.user_id) as conn:
        folders_sql._lock_tree(conn, customer_id)
        _live(_admin_document(conn, user, target))
        found = _enabled(conn, user, person)
        if mine:
            _self_grant(user, body.password)
        versions, given = _give(conn, user, target, [person])
    if given and (mine or found.admin):
        _tell(background, context, user, "file", None if mine else found.name)
    return {"versions": versions, "changed": bool(given)}


@router.delete("/documents/{document_id}/shares/{user_id}", status_code=204)
def take_file(document_id: uuid.UUID, user_id: uuid.UUID, user: User, context: Context) -> Response:
    """Take a file back from a person: every version, Trash too. 404 when it wasn't given."""
    target, person, customer_id = str(document_id), str(user_id), user.customer_id
    with context.db.user(customer_id, user.user_id) as conn:
        _admin_document(conn, user, target)
        folders_sql._lock_tree(conn, customer_id)
        if not access_sql.take(conn, target, person):
            raise ApiError(404, "not_found")
        _audit(conn, user, "access.file_take", ("document", target), person)
    return Response(status_code=204)


@router.post("/documents/shares")
def give_files(
    body: BulkGiveBody, user: User, background: BackgroundTasks, context: Context
) -> dict[str, int]:
    """Give up to MAX_FILES files to up to MAX_PEOPLE people at once: ``{given}``, the (file,
    person) pairs newly given. A file unknown is 404 and one in Trash 409, and nothing is written.
    One family lock and one insert a file, families in family order, so two at once queue instead
    of deadlocking."""
    if user.role != "admin":
        raise ApiError(403, "forbidden", "Only an admin can give a file to someone.")
    require_admin_session(user)
    customer_id = user.customer_id
    documents = list(dict.fromkeys(str(each) for each in body.document_ids))
    people = list(dict.fromkeys(str(each) for each in body.user_ids))
    mine = user.user_id in people
    with context.db.user(customer_id, user.user_id) as conn:
        for document in documents:
            _admin_document(conn, user, document)
    _password_given(context, user, body.password)
    with context.db.user(customer_id, user.user_id) as conn:
        folders_sql._lock_tree(conn, customer_id)
        found = [_admin_document(conn, user, document) for document in documents]
        for each in found:
            _live(each)
        persons = {person: _enabled(conn, user, person) for person in people}
        if mine:
            _self_grant(user, body.password)
        given: list[str] = []
        for each in sorted(found, key=lambda row: (row.family_id, row.id)):
            given += _give(conn, user, each.id, people)[1]
    for person in dict.fromkeys(given):
        if person == user.user_id or persons[person].admin:
            _tell(background, context, user, "file", None if person == user.user_id else persons[person].name)
    return {"given": len(given)}
