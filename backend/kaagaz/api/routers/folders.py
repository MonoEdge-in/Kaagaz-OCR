"""The folder routes: list, create, rename or move, and delete (PRD 1 F1).

Why some choices:

* **Thin.** The tree rules (at most 100 levels, no cycles, one name per parent
  in any letter case, only an empty folder can go) live in
  ``kaagaz.db.folders``, which raises a ``FolderError`` carrying the API code.
  ``_refused`` turns it into the reply: 404 for a folder that isn't there
  (another customer's included, never 403), 409 for the others.
* **"Don't move" is not "move to the top level".** PATCH moves a folder only
  when the body has a ``parent_id`` key, and an explicit null means the top
  level. Pydantic's ``model_fields_set`` tells the two apart.
* **Any signed-in user of the customer** may change folders (PRD 1 F1). Each
  change writes one audit row with ids only, never a folder name.
* **A folder that holds files** is deleted by an admin only (#537, Tushar, 7 Nov 2026):
  ``?contents=keep`` or ``?contents=trash``; without it the delete is today's, for anyone.
* **A folder in Trash** (#542) keeps its structure: Trash lists it as one item and opens it read
  only (anyone of the customer); Restore puts it back where it was (an admin, or who uploaded
  every version of every file in it: a document's restore rule); delete forever has a document's
  delete forever rule (``CanDeleteForever``). The live tree never holds one
  (``kaagaz.db.trashed_folders``).
* A refused change raises out of the ``with`` block, so its transaction rolls
  back, audit row included.
* **Who can see what** (PRD 7, #599): a folder the person can't see, live or in Trash, answers
  exactly as one that never was, checked before the tree lock is taken (``_seen``). The tree and
  the counts hold only what they may see; an Admin's delete dialog reads the true totals.
"""

import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from kaagaz.api.auth import CanDeleteForever, CurrentUser, User, deletes_forever_by_password
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.names import FOLDER_NAME_MAX, clean_name
from kaagaz.api.routers.documents import ASK_AN_ADMIN, ActivityCursor, ActivityLimit
from kaagaz.api.views import folder, shown_folder, timeline, trashed_folder, uploaded_every_version
from kaagaz.db import audit, listing
from kaagaz.db import folders as folders_sql
from kaagaz.db import trashed_folders as trashed_sql

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]


FolderKind = Literal["department", "category", "party", "property", "year", "custom"]
SortOrder = Annotated[int, Field(strict=True, ge=0, le=10_000)]


class NewFolderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    parent_id: uuid.UUID | None = None  # None: at the top level
    kind: FolderKind = "custom"  # File management F1: department is an admin's, top level only
    sort_order: SortOrder | None = None


class FolderChangesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    parent_id: uuid.UUID | None = None  # an explicit null: the top level; left out: don't move
    kind: FolderKind | None = None  # left out or null: keep it
    sort_order: SortOrder | None = None  # an explicit null clears it; left out: keep it


def _admin_for_departments(user: CurrentUser, *kinds: str | None) -> None:
    """F1: only an admin creates, renames, moves, orders or changes a department."""
    if "department" in kinds and user.role != "admin":
        raise ApiError(403, "forbidden", "Only an admin can change departments.")


@contextmanager
def _refused() -> Iterator[None]:
    """A folder change the helpers refused becomes its API error."""
    try:
        yield
    except folders_sql.FolderError as error:
        raise ApiError(404 if error.code == "not_found" else 409, error.code) from None


def _seen(conn: Connection, user: CurrentUser, *folder_ids: str | None) -> None:
    """PRD 7 (#599): each folder named is one the person may see, else FolderNotFound, as for a
    folder that isn't there. A plain read, before any lock."""
    asked = {folder for folder in folder_ids if folder is not None}
    if asked and len(listing.folders_seen(conn, user.customer_id, sorted(asked))) < len(asked):
        raise folders_sql.FolderNotFound()


def _name(name: str | None) -> str:
    cleaned = clean_name(name or "", max_length=FOLDER_NAME_MAX)
    if cleaned is None:
        raise ApiError(400, "bad_request", f"Enter a folder name of 1 to {FOLDER_NAME_MAX} characters.")
    return cleaned


def _audit(
    conn: Connection,
    user: CurrentUser,
    action: str,
    folder_id: str,
    details: Mapping[str, audit.DetailValue] | None = None,
) -> None:
    audit.record(
        conn,
        user.customer_id,
        actor_user_id=user.user_id,
        action=action,
        target_type="folder",
        target_id=folder_id,
        details=details,
    )


@router.get("/folders")
def list_folders(user: User, context: Context) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        rows = folders_sql.list_folders(conn, user.customer_id)
    return {"items": [folder(row) for row in rows]}


@router.post("/folders", status_code=201)
def create_folder(body: NewFolderBody, user: User, context: Context) -> dict[str, Any]:
    name = _name(body.name)
    _admin_for_departments(user, body.kind)
    parent_id = None if body.parent_id is None else str(body.parent_id)
    with _refused(), context.db.user(user.customer_id, user.user_id) as conn:
        _seen(conn, user, parent_id)
        row = folders_sql.create_folder(
            conn,
            user.customer_id,
            name=name,
            parent_id=parent_id,
            created_by=user.user_id,
            kind=body.kind,
            sort_order=body.sort_order,
        )
        _audit(conn, user, "folder.create", row.id, {"parent_id": parent_id})
        return shown_folder(conn, user.customer_id, row)


@router.patch("/folders/{folder_id}")
def update_folder(
    folder_id: uuid.UUID, body: FolderChangesBody, user: User, context: Context
) -> dict[str, Any]:
    given = body.model_fields_set
    if not given:
        raise ApiError(400, "bad_request", "Say what to change: the name, the place, or both.")
    name = _name(body.name) if "name" in given else None
    target = str(folder_id)
    parent_id = None if body.parent_id is None else str(body.parent_id)
    with _refused(), context.db.user(user.customer_id, user.user_id) as conn:
        _seen(conn, user, target, parent_id if "parent_id" in given else None)
        path = folders_sql.folder_path(conn, user.customer_id, target)
        if not path:
            raise folders_sql.FolderNotFound()
        _admin_for_departments(user, path[-1].kind, body.kind)
        if body.kind not in (None, "department") and path[-1].kind == "department":
            # PRD 7 lead decision 6 (#601): under the tree lock, before anything is written.
            folders_sql._lock_tree(conn, user.customer_id)
            if folders_sql.department_in_use(conn, user.customer_id, target):
                raise folders_sql.DepartmentInUse()
        # The body names at least one change (checked above), so ``row`` is always set.
        # The move goes first: it takes the tree lock before anything is written.
        if "parent_id" in given:
            row = folders_sql.move_folder(conn, user.customer_id, target, parent_id=parent_id)
            _audit(conn, user, "folder.move", target, {"parent_id": parent_id})
        if name is not None:
            row = folders_sql.rename_folder(conn, user.customer_id, target, name=name)
            _audit(conn, user, "folder.rename", target)
        if body.kind is not None or "sort_order" in given:
            row = folders_sql.set_kind(
                conn,
                user.customer_id,
                target,
                kind=body.kind,
                sort_order=body.sort_order,
                order="sort_order" in given,
            )
            _audit(conn, user, "folder.kind", target, {"kind": row.kind, "sort_order": row.sort_order})
        return shown_folder(conn, user.customer_id, row)  # PRD 7 (#599): no hidden parent's id


@router.get("/folders/{folder_id}/summary")
def get_folder_summary(folder_id: uuid.UUID, user: User, context: Context) -> dict[str, int]:
    """#537: ``{files, folders, bytes}`` the folder holds at every depth (live files only), so
    the Delete dialog can say it before anything is pressed. Another customer's folder is 404.
    PRD 7 (#599): what the person may see (a hidden folder is 404); an Admin, the true totals."""
    with _refused(), context.db.user(user.customer_id, user.user_id) as conn:
        admin = user.role == "admin"
        held = folders_sql.folder_summary(
            conn, user.customer_id, str(folder_id), seen_only=not admin, admin=admin
        )
    return {"files": held.files, "folders": held.folders, "bytes": held.bytes}


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(
    folder_id: uuid.UUID,
    user: User,
    context: Context,
    contents: Literal["keep", "trash"] | None = None,
    to: uuid.UUID | None = None,
) -> Response:
    """Today's delete of an empty folder, for anyone. #537, an admin only: ``contents=keep``
    moves what is inside into ``to`` (left out: the top level) and deletes the folder;
    ``contents=trash`` sends every file at every depth to Trash and deletes the folder with
    every folder inside it (``folders_sql.delete_folder_with_contents``). Those two reply 200
    with ``{files, folders}``: what moved, or the files that went to Trash."""
    if contents is not None and user.role != "admin":
        raise ApiError(403, "forbidden", "Only an admin can delete a folder that holds files.")
    if to is not None and contents != "keep":
        raise ApiError(400, "bad_request", "Choose where things go only when moving them.")
    target = str(folder_id)
    with _refused(), context.db.user(user.customer_id, user.user_id) as conn:
        _seen(conn, user, target)
        path = folders_sql.folder_path(conn, user.customer_id, target)
        _admin_for_departments(user, path[-1].kind if path else None)
        if contents is None:
            folders_sql.delete_folder(conn, user.customer_id, target)
            _audit(conn, user, "folder.delete", target)
            return Response(status_code=204)
        destination = None if to is None else str(to)
        try:
            held = folders_sql.delete_folder_with_contents(
                conn,
                user.customer_id,
                target,
                contents=contents,
                to=destination,
                by=user.user_id,
                now=context.clock(),
            )
        except folders_sql.FolderCycle:
            raise ApiError(400, "bad_request", "Choose a folder outside the one you are deleting.") from None
        except folders_sql.DestinationNotFound:
            raise ApiError(
                404, "not_found", "The folder you chose isn't there any more. Choose another."
            ) from None
        except folders_sql.FolderNameTaken:
            raise ApiError(
                409,
                "folder_names_taken",
                "The folder you chose already has too many folders with these names. Choose another.",
            ) from None
        except folders_sql.AccessWouldChange:  # #594 (Q5)
            raise ApiError(
                409,
                "access_would_change",
                "Moving what's inside there would let more people see it."
                " Choose another folder or send it to Trash.",
            ) from None
        details: dict[str, audit.DetailValue] = {
            "contents": contents,
            "files": held.files,
            "folders": held.folders,
        }
        if contents == "keep":
            details["to"] = destination
        else:
            details["trash_id"] = held.trash_id  # #542: the folder is in Trash, restorable
        _audit(conn, user, "folder.delete" if contents == "keep" else "folder.trash", target, details)
    return JSONResponse({"files": held.files, "folders": held.folders})


# --- a folder in Trash (#542) -------------------------------------------------------------------
NAMES_TAKEN = "Too many folders there already have this name. Rename one first."
# Under /documents/trash, beside a document's Trash routes, so the typed password's cookie
# (auth.CONFIRM_PATH, /api/documents) reaches delete forever as it does a document's.


@router.get("/documents/trash/folders")
def list_trashed_folders(user: User, context: Context) -> dict[str, Any]:
    """``{items: [TrashedFolder]}``: Trash's folders, newest first; their files are not loose
    items of Trash (``GET /documents?trashed=true`` leaves them out)."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        rows = trashed_sql.list_items(conn, user.customer_id)
    return {"items": [trashed_folder(row, user) for row in rows]}


@router.get("/documents/trash/folders/{folder_id}")
def get_trashed_folder(folder_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """A folder in Trash opened, read only: ``{folder, path: [{id, name}] (Trash's item first),
    folders: [TrashedFolder]}``; its files come from ``GET /documents?trashed=true&trashed_folder_id=``.
    One not in Trash, or another customer's, is 404."""
    with _refused(), context.db.user(user.customer_id, user.user_id) as conn:
        row, path, children = trashed_sql.contents(conn, user.customer_id, str(folder_id))
    return {
        "folder": trashed_folder(row, user),
        "path": [{"id": each, "name": name} for each, name in path],
        "folders": [trashed_folder(child, user) for child in children],
    }


@router.post("/documents/trash/folders/{folder_id}/restore")
def restore_trashed_folder(folder_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """Put a folder in Trash back where it was, with everything that went to Trash with it:
    ``{folder, location, renamed, relocated, sealed, files, folders}``. ``location`` is the path it
    went into ([] the top level); ``relocated``: its folder was gone, so ``location`` instead (#594:
    the private folder or department it was in, else the top level); ``sealed``: a department or
    private folder it was in is gone, so it came back private (#594, Q6); ``renamed``: a live folder
    there had its name, so "Name (n)". Another customer's is 404; a member who did not upload every
    version of every file in it is 403."""
    target = str(folder_id)
    with _refused(), context.db.user(user.customer_id, user.user_id) as conn:
        found = trashed_sql.one(conn, user.customer_id, target)
        if not uploaded_every_version(found.uploaders, user):
            raise ApiError(403, "forbidden")
        try:
            restored = trashed_sql.restore_folder(conn, user.customer_id, target, admin=user.role == "admin")
        except trashed_sql.DepartmentAdminOnly as refused:
            raise ApiError(403, "forbidden", department_only_admin(conn, user, refused)) from None
        except folders_sql.FolderNameTaken:
            raise ApiError(409, "folder_names_taken", NAMES_TAKEN) from None
        _audit(conn, user, "folder.restore", target, trashed_sql.audit_details(restored))
        # PRD 7 (#599): what came back, its place and what came with it, as the person may see
        # them: the folder they asked for is theirs to see (it was their item of Trash).
        shown = trashed_sql.as_seen(conn, user.customer_id, restored)
        if shown is None:  # can't happen: the folder asked for was found visible above
            raise folders_sql.FolderNotFound()
        reply = shown_folder(conn, user.customer_id, shown.folder)
    return {
        "folder": reply,
        "location": None if shown.location is None else list(shown.location),
        "renamed": shown.renamed,
        "relocated": shown.relocated,
        "sealed": shown.sealed,
        "files": shown.files,
        "folders": shown.folders,
    }


def department_only_admin(
    conn: Connection, user: CurrentUser, refused: trashed_sql.DepartmentAdminOnly
) -> str:
    """The words for a restore only an admin may make, as it brings a department back (F1); PRD 7
    (#599): the department is named as such only to a person who may see it."""
    if trashed_sql.seen(conn, user.customer_id, [refused.folder_id]):
        return "Only an admin can restore a department."
    return ASK_AN_ADMIN


# PRD 7 section 5: a folder holding anything the person can't see needs an Admin.
HIDDEN_INSIDE = "You can't delete this folder. Ask an admin."
# A12 (stability pass, 7 Nov 2026): the files one delete forever request deletes at most.
FOREVER_FAMILIES = 200


@router.delete("/documents/trash/folders/{folder_id}/permanent", status_code=204)
def delete_trashed_folder_forever(folder_id: uuid.UUID, user: CanDeleteForever, context: Context) -> Response:
    """Delete a folder in Trash for good: every file that went with it (rows and files, every
    version), one family per transaction as the purge does, so a storage failure part way keeps
    every other file whole in Trash and a retry goes on; then its folders, once none of their
    files is left. At most FOREVER_FAMILIES files a request (A12): while more are left the reply
    is 202 ``{remaining}`` and the client asks again; the last part is 204. Two at once are safe:
    a family deleted by the other is skipped, and the folders go once, under the tree lock.
    Delete forever's own rule (``require_delete_forever``), and its audit marks (``by_grant``,
    ``by_password``); each request writes one row with the files it deleted: ``finished: false``
    while files are left (or it stopped part way), the folders too on the last. Another
    customer's is 404. PRD 7 (#599): one the person can't see is 404 too, and one holding a folder
    or a file they can't see is deleted forever by an Admin only (403)."""
    target, customer_id = str(folder_id), user.customer_id
    with context.db.user(customer_id, user.user_id) as conn:
        try:
            item = trashed_sql.one(conn, customer_id, target)
        except folders_sql.FolderNotFound:
            raise ApiError(404, "not_found") from None
        if item.hidden_inside and user.role != "admin":
            raise ApiError(403, "forbidden", HIDDEN_INSIDE)
        found = trashed_sql.families_in(conn, customer_id, target)
    if found is None:
        raise ApiError(404, "not_found")
    trash_id, families = found
    marks: dict[str, audit.DetailValue] = {}
    if user.role != "admin":
        marks["by_grant"] = True
    elif deletes_forever_by_password(user):
        marks["by_password"] = True
    deleted = 0
    try:
        for family in families[:FOREVER_FAMILIES]:
            with context.db.user(customer_id, user.user_id) as conn:
                deleted += trashed_sql.delete_family(conn, context.storage, customer_id, family)
        if len(families) > FOREVER_FAMILIES:
            with context.db.user(customer_id, user.user_id) as conn:
                left = trashed_sql.families_in(conn, customer_id, target)
            remaining = 0 if left is None else len(left[1])
            # Fewer left (this part, or another request at once): ask again. None gone at all
            # (never seen, but never a 202 for ever): drop_folder below says folder_not_emptied.
            if 0 < remaining < len(families):
                if deleted:
                    with context.db.user(customer_id, user.user_id) as conn:
                        part = {"trash_id": trash_id, "files": deleted, "finished": False, **marks}
                        _audit(conn, user, "folder.delete_forever", target, part)
                return JSONResponse({"remaining": remaining}, status_code=202)
        with _refused(), context.db.user(customer_id, user.user_id) as conn:
            gone = trashed_sql.drop_folder(conn, customer_id, target, files=deleted)
            details = {"trash_id": trash_id, "folders": gone.folders, "files": deleted, **marks}
            _audit(conn, user, "folder.delete_forever", target, details)
    except Exception:
        if deleted:  # what went for good is on record, whatever stopped the rest
            with context.db.user(customer_id, user.user_id) as conn:
                stopped = {"trash_id": trash_id, "files": deleted, "finished": False, **marks}
                _audit(conn, user, "folder.delete_forever", target, stopped)
        raise
    return Response(status_code=204)


@router.get("/folders/{folder_id}/activity")
def get_folder_activity(
    folder_id: uuid.UUID,
    user: User,
    context: Context,
    limit: ActivityLimit = 50,
    cursor: ActivityCursor = None,
) -> dict[str, Any]:
    """File management A5: ``{items: [AuditEntry], next_cursor}``, what happened in the folder
    and every folder below it, newest first (document, version and folder actions only).
    Another customer's folder, or none, is 404."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        seen = listing.folders_seen(conn, user.customer_id, [str(folder_id)])  # PRD 7 (#599)
        subtree = folders_sql.descendant_ids(conn, user.customer_id, str(folder_id)) if seen else []
        if not subtree:
            raise ApiError(404, "not_found")
        filters = audit.Filters(folder_ids=tuple(subtree))
        return timeline(conn, user.customer_id, filters, limit=limit, cursor=cursor)
