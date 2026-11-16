"""Linking files already uploaded as versions of each other, and its Undo (V13 to V16, V18, V20, V21;
#648, V-A). Only Admins and Management, checked by the route (``api/routers/version_links.py``),
which also checks every chosen file through ``visible_documents`` before anything here runs.

* **The plan** (``prepare``), all under the locks, before anything is written: the tree lock (the
  link moves files, 0085), then every version of every family of the chosen files in one statement,
  families in id order and each family's first version first (0093's ``kaagaz_lock`` and
  ``trash.lock_family``'s order, so a lock, a trash or an Accept waits or is waited for, never
  deadlocks). A chosen file whose family changed before its lock was taken is ``changed``. Every
  version of those families must be one the person can see (else ``not_found``, as a file that
  never was), out of Trash and not locked (a locked file is never linked or read); the latest
  (``current``) ready; no two chosen files of one family.
* **The order** (V14): each chosen file brings its whole family, in its own version order, at the
  chosen file's place in ``order``; the versions are numbered 1 to n. The latest's family keeps its
  id; the other families join it. The person picks the latest (``current``), which Ask answers from.
* **Where they go** (V15): into the latest's place (its folder, or whom it is held for), with its
  owner, label and tags, as Accept does (``versions.link``). Each joining family's move, for the
  route to count who loses and gains it (the confirm step, the password rule): ``access.moved``
  reads the views, which only the API may call.
* **The link** (``apply``), in the same transaction: the joining families' stories are carried
  (``kaagaz_move_stories``), their versions take the family's people (0100's trigger), the group
  members linked (V-B's ``linked``), one ``version_links`` row with what Undo puts back, and one
  ``version.linked`` row per version. Nothing is read again: no spend.
* **Undo** (V16), while the family holds exactly the versions the link left: each version's family,
  number, current, folder, held for, owner, label, tags and group decision put back, each version
  given to exactly whom it was given before (but to a person switched off since), the stories the
  link carried taken back, one ``version.unlinked`` row per version.
  Refused once a folder they came from is gone (``folder_gone``).
* **Under the per-person rule** (PRD 7 PR 9, #606), the app role neither sees nor writes a version
  the person can't see, so families are read and locked whole, and the link and its Undo written,
  by 0100's owner-rights functions (``kaagaz_link_families``, ``kaagaz_link_versions``,
  ``kaagaz_link_undo``), which check the caller and every version again inside.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, RowMapping, text
from sqlalchemy.exc import OperationalError

from kaagaz.db import audit
from kaagaz.db.folders import Place, _lock_tree, place_of, prune_empty
from kaagaz.db.sqlstate import sqlstate
from kaagaz.db.trash import InTrash, live_folders

# The most versions one link may leave in a family (review on #658).
MAX_VERSIONS = 200
# A version still being read: received (waiting) or clean (scanned, its reading under way).
BEING_READ = ("received", "clean")

# PRD 7 PR 9 (#606): every version of the families, each with whether the person sees it, and locked
# when asked; then the link and its Undo, all through 0100's owner-rights functions (the per-person
# rule hides a version from the app role, and refuses a write over one).
_WHOLE = text("SELECT id::text, family_id::text, seen FROM kaagaz_link_families(CAST(:ids AS uuid[]), :lock)")
_APPLY = text("SELECT kaagaz_link_versions(CAST(:order AS uuid[]), :current)::text")
_UNDO = text("SELECT kaagaz_link_undo(:link)")
LOCK_NOT_AVAILABLE = "55P03"
_FAMILIES = text(
    "SELECT id::text, family_id::text FROM documents"
    " WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
)
# Every version of the families, with what the plan, the move rule and Undo need.
_STATE = text(
    "SELECT d.id::text, d.family_id::text, d.version_no, d.is_current, d.status, d.display_name,"
    " d.deleted_at IS NOT NULL AS in_trash, d.locked_at IS NOT NULL AS locked,"
    " d.folder_id::text, d.held_for::text, d.department_id::text, d.private_folder_id::text,"
    " d.owner_id::text, d.sensitivity, d.sensitivity_set_by_person,"
    " EXISTS (SELECT 1 FROM visible_documents v WHERE v.customer_id = d.customer_id AND v.id = d.id) AS seen,"
    " ARRAY(SELECT t.tag_id::text FROM document_tags t WHERE t.customer_id = d.customer_id"
    " AND t.document_id = d.id ORDER BY t.tag_id) AS tags,"
    " (SELECT coalesce(json_agg(json_build_object('user', s.user_id, 'given_by', s.given_by,"
    " 'given_at', s.given_at) ORDER BY s.user_id), '[]') FROM document_shares s"
    " WHERE s.customer_id = d.customer_id AND s.document_id = d.id) AS shares,"
    " (SELECT json_build_object('state', g.state, 'by', g.decided_by, 'at', g.decided_at)"
    " FROM version_group_members g WHERE g.customer_id = d.customer_id AND g.document_id = d.id) AS member"
    " FROM documents d WHERE d.customer_id = :c AND d.family_id = ANY(CAST(:f AS uuid[]))"
    " ORDER BY d.family_id, d.version_no"
)
# Undo: the family's newest link not undone, naming the version.
_NEWEST_LINK = text(
    "SELECT id::text, document_ids::text[], snapshot, folder_ids::text[] FROM version_links"
    " WHERE customer_id = :c"
    " AND family_id = :f AND undone_at IS NULL ORDER BY linked_at DESC LIMIT 1"
)
# V18's Kept apart list: each pair kept separate or made separate whose two sides this person sees
# (the candidate family through its current version, else its newest), out of Trash and unlocked,
# and not linked since.
_KEPT_APART = text(
    "SELECT s.id::text, s.status, s.decided_at, n.id::text AS document_id, n.display_name AS document_name,"
    " o.id::text AS other_id, o.display_name AS other_name FROM version_suggestions s"
    " JOIN visible_documents n ON n.customer_id = s.customer_id AND n.id = s.document_id"
    " AND n.deleted_at IS NULL AND n.locked_at IS NULL AND n.family_id <> s.candidate_family_id"
    " JOIN LATERAL (SELECT v.id, v.display_name FROM visible_documents v WHERE v.customer_id = s.customer_id"
    " AND v.family_id = s.candidate_family_id AND v.deleted_at IS NULL AND v.locked_at IS NULL"
    " ORDER BY v.is_current DESC, v.version_no DESC LIMIT 1) o ON true"
    " WHERE s.customer_id = :c AND s.status IN ('rejected', 'separated')"
    " ORDER BY s.decided_at DESC NULLS LAST, s.id LIMIT :n"
)
# The family's newest link not undone, and the versions it holds now: the Versions tab's Undo.
_FAMILY_LINK = text(
    "SELECT l.id::text, l.linked_at, l.document_ids::text[], ARRAY(SELECT d.id::text FROM documents d"
    " WHERE d.customer_id = l.customer_id AND d.family_id = l.family_id) AS now"
    " FROM version_links l WHERE l.customer_id = :c AND l.family_id = :f AND l.undone_at IS NULL"
    " ORDER BY l.linked_at DESC LIMIT 1"
)
# Review (#658): a listed file of this name in the folder a family goes back to (separate's rule).
_NAME_TAKEN = text(
    "SELECT EXISTS (SELECT 1 FROM documents o WHERE o.customer_id = :c AND o.deleted_at IS NULL"
    " AND o.is_current AND o.id <> ALL(CAST(:ids AS uuid[]))"
    " AND o.folder_id IS NOT DISTINCT FROM CAST(:f AS uuid)"
    " AND lower(o.display_name) = lower(:name))"
)


class Refused(Exception):
    """The link or its Undo can't be made: ``code`` is not_found (404), or in_trash, locked,
    not_ready, already_versions, changed, not_linked, not_newest or folder_gone (409)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Plan:
    """A link worked out under the locks (module notes)."""

    family: str  # the latest's family, which the others join
    current: str
    order: list[str]  # every version, in its new order
    joining: list[str]  # the other families, in id order
    rows: dict[str, RowMapping]  # every version as it is now
    moves: list["Moved"]


@dataclass(frozen=True, slots=True)
class Moved:
    """A family's move as the access rules see it (#601): its place, whom it is held for and whom
    its versions are given to, before and after. The route counts who loses and gains it
    (``access.audience_of`` reads the views: the API's alone). Review (#658): the gifts count, since
    0100's trigger gives every joining version to whom the latest's family is given."""

    before: Place
    held: str | None
    given: frozenset[str]
    after: Place
    held_after: str | None
    given_after: frozenset[str]


def _given(rows: Sequence[RowMapping | dict[str, Any]]) -> frozenset[str]:
    """Everyone these versions are given to."""
    return frozenset(str(share["user"]) for row in rows for share in row["shares"])


def _place(row: RowMapping) -> tuple[str | None, str | None]:
    return row["department_id"], row["private_folder_id"]


def prepare(
    conn: Connection,
    customer_id: str,
    document_ids: Sequence[str],
    order: Sequence[str],
    current_id: str,
    *,
    lock: bool = True,
) -> Plan:
    """The plan (module notes), under the tree and family locks unless ``lock`` is False (the
    preview, in one snapshot). ``order`` holds each of ``document_ids`` once, ``current_id`` among
    them (the route checked). Raises Refused. O(v log D) for the families' v versions."""
    params: dict[str, Any] = {"c": customer_id, "ids": list(document_ids)}
    if lock:
        _lock_tree(conn, customer_id)
    asked: dict[str, str] = {row[0]: row[1] for row in conn.execute(_FAMILIES, params)}
    if len(asked) < len(set(document_ids)):
        raise Refused("not_found")
    whole = _whole(conn, list(asked), lock=lock)
    if not all(seen for _, seen in whole.values()):
        raise Refused("not_found")
    families = sorted({family for family, _ in whole.values()})
    rows = {row["id"]: row for row in conn.execute(_STATE, {"c": customer_id, "f": families}).mappings()}
    if set(rows) != set(whole) or any(whole.get(each, ("", True))[0] != asked[each] for each in asked):
        raise Refused("changed")  # linked, separated or deleted before its family's lock was taken
    if len(rows) > MAX_VERSIONS:
        raise Refused("too_many_versions")
    if len(families) < len(asked):
        raise Refused("already_versions")
    if any(row["in_trash"] for row in rows.values()):
        raise Refused("in_trash")
    if any(row["locked"] for row in rows.values()):
        raise Refused("locked")
    latest = rows[current_id]
    # Review (#658): nor any version still being read, which its run would make current, or close as
    # gone when it found its family changed under it.
    if latest["status"] != "ready" or any(row["status"] in BEING_READ for row in rows.values()):
        raise Refused("not_ready")
    by_family: dict[str, list[str]] = {}
    for row in rows.values():  # in version order (the statement's)
        by_family.setdefault(row["family_id"], []).append(row["id"])
    family = latest["family_id"]
    joining = [each for each in families if each != family]
    after = place_of(conn, customer_id, latest["folder_id"])
    given_after = _given([rows[id_] for id_ in by_family[family]])
    moves: list[Moved] = []
    for each in joining:
        first = rows[by_family[each][0]]
        given = _given([rows[id_] for id_ in by_family[each]])
        moves.append(Moved(_place(first), first["held_for"], given, after, latest["held_for"], given_after))
    return Plan(
        family=family,
        current=current_id,
        order=[version for document in order for version in by_family[asked[document]]],
        joining=joining,
        rows=rows,
        moves=list(dict.fromkeys(moves)),  # review (#658): each move counted once
    )


def _whole(conn: Connection, documents: list[str], *, lock: bool) -> dict[str, tuple[str, bool]]:
    """Every version of these files' families, ``{id: (family, seen)}``, read whole (``_WHOLE``):
    under the per-person rule the app role would miss a version the person can't see."""
    return {row[0]: (row[1], bool(row[2])) for row in conn.execute(_WHOLE, {"ids": documents, "lock": lock})}


def _written(conn: Connection, statement: Any, params: dict[str, Any]) -> Any:
    """One of 0100's writing functions; Refused("changed") when a group decision holds one of its
    members (they are taken NOWAIT; nothing is written: the caller's transaction rolls back)."""
    try:
        return conn.execute(statement, params).scalar()
    except OperationalError as error:
        if sqlstate(error) != LOCK_NOT_AVAILABLE:
            raise
        raise Refused("changed") from None


def apply(conn: Connection, customer_id: str, plan: Plan, *, by: str) -> str:
    """The link (module notes), in the caller's transaction after ``prepare``: 0100's
    ``kaagaz_link_versions``, which checks the versions again under the locks and writes them, the
    group members and the ``version_links`` row; then one ``version.linked`` row per version; the
    link's id. O(v) for the v versions, plus one statement per joining family."""
    link = str(_written(conn, _APPLY, {"order": list(plan.order), "current": plan.current}))
    for number, version in enumerate(plan.order, 1):
        audit.record(
            conn,
            customer_id,
            actor_user_id=by,
            action="version.linked",
            target_type="document",
            target_id=version,
            details={
                "link_id": link,
                "family_id": plan.family,
                "from_family_id": plan.rows[version]["family_id"],
                "version_no": number,
                "current": version == plan.current,
            },
        )
    return link


@dataclass(frozen=True, slots=True)
class UndoPlan:
    """An Undo worked out under the locks (module notes)."""

    link: str
    family: str
    order: list[str]
    snapshot: dict[str, Any]
    moves: list[Moved]
    folders: list[str]  # the folders the link left


def prepare_undo(conn: Connection, customer_id: str, document_id: str) -> UndoPlan:
    """The Undo of the newest link of the version's family (module notes), under the tree then the
    family lock. Raises Refused (not_found, locked, not_linked, not_newest, folder_gone) or
    trash.InTrash. O(v log D)."""
    c = customer_id
    _lock_tree(conn, c)
    whole = _whole(conn, [document_id], lock=True)
    if document_id not in whole or not all(seen for _, seen in whole.values()):
        raise Refused("not_found")  # review (#658): every version, not only the one asked
    family = whole[document_id][0]
    rows = {row["id"]: row for row in conn.execute(_STATE, {"c": c, "f": [family]}).mappings()}
    if set(rows) != set(whole):
        raise Refused("changed")
    if any(row["in_trash"] for row in rows.values()):
        raise InTrash(document_id)
    if any(row["locked"] for row in rows.values()):
        raise Refused("locked")
    if any(row["status"] in BEING_READ for row in rows.values()):
        raise Refused("not_ready")
    found = conn.execute(_NEWEST_LINK, {"c": c, "f": family}).first()
    if found is None or document_id not in found[1]:
        raise Refused("not_linked")
    link, order, snapshot, left = found[0], list(found[1]), found[2], list(found[3])
    if set(rows) != set(order):
        raise Refused("not_newest")  # a version uploaded, linked or made separate since
    back = snapshot["versions"]
    folders = sorted({v["folder_id"] for v in back.values() if v["folder_id"] is not None})
    if len(live_folders(conn, c, folders)) < len(folders):
        raise Refused("folder_gone")
    for id_ in order:
        version = back[id_]
        if version["family_id"] in snapshot["families"] and version["is_current"]:
            params = {"c": c, "ids": order, "f": version["folder_id"], "name": rows[id_]["display_name"]}
            if conn.execute(_NAME_TAKEN, params).scalar_one():
                raise Refused("name_taken")
    here = rows[document_id]  # where every version is now: the family's place
    moves: list[Moved] = []
    for each in snapshot["families"]:
        versions = [id_ for id_ in order if back[id_]["family_id"] == each]
        first = back[versions[0]]
        now, then = _given([rows[id_] for id_ in versions]), _given([back[id_] for id_ in versions])
        after = place_of(conn, c, first["folder_id"])
        moves.append(Moved(_place(here), here["held_for"], now, after, first["held_for"], then))
    return UndoPlan(link, family, order, snapshot, list(dict.fromkeys(moves)), left)


def apply_undo(conn: Connection, customer_id: str, plan: UndoPlan, *, by: str) -> None:
    """Undo (module notes), in the caller's transaction after ``prepare_undo``: 0100's
    ``kaagaz_link_undo``, which checks the family again under its lock and writes every version
    back; then the folders the link kept are pruned as any move's are, and one ``version.unlinked``
    row per version. O(v) for the v versions, plus one statement per family put back."""
    c, back = customer_id, plan.snapshot["versions"]
    _written(conn, _UNDO, {"link": plan.link})
    prune_empty(conn, c, plan.folders)  # no longer kept for this link (``folders._PRUNE``)
    for version in plan.order:
        audit.record(
            conn,
            c,
            actor_user_id=by,
            action="version.unlinked",
            target_type="document",
            target_id=version,
            details={"link_id": plan.link, "family_id": back[version]["family_id"]},
        )


def kept_apart(conn: Connection, customer_id: str, limit: int) -> list[dict[str, Any]]:
    """V18's Kept apart pairs (``_KEPT_APART``), newest decision first, at most ``limit``: ``{id,
    separated, decided_at, document: {id, name}, other: {id, name}}``. One statement."""
    return [
        {
            "id": row["id"],
            "separated": row["status"] == "separated",
            "decided_at": None if row["decided_at"] is None else row["decided_at"].isoformat(),
            "document": {"id": row["document_id"], "name": row["document_name"]},
            "other": {"id": row["other_id"], "name": row["other_name"]},
        }
        for row in conn.execute(_KEPT_APART, {"c": customer_id, "n": limit}).mappings()
    ]


def family_link(conn: Connection, customer_id: str, family_id: str) -> dict[str, Any] | None:
    """The family's newest link not undone (the Versions tab, V16): ``{id, linked_at, can_undo}``,
    ``can_undo`` while the family holds exactly the versions it left; None when there is none. One
    statement."""
    row = conn.execute(_FAMILY_LINK, {"c": customer_id, "f": family_id}).first()
    if row is None:
        return None
    return {"id": row[0], "linked_at": row[1].isoformat(), "can_undo": set(row[2]) == set(row[3])}
