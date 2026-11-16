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
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, RowMapping, text
from sqlalchemy.exc import OperationalError

from kaagaz.db import audit
from kaagaz.db.folders import Place, _lock_tree, place_of
from kaagaz.db.sqlstate import sqlstate
from kaagaz.db.trash import LIVE_FOLDERS, lock_live_family

# The most versions one link may leave in a family (review on #658).
MAX_VERSIONS = 200
# A version still being read: received (waiting) or clean (scanned, its reading under way).
BEING_READ = ("received", "clean")
# A version's number while a link or its Undo renumbers (unique within a family, 0002).
_SHIFT = 1_000_000

_FAMILIES = text(
    "SELECT id::text, family_id::text FROM documents"
    " WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
)
_LOCK = text(
    "SELECT id FROM documents WHERE customer_id = :c AND family_id = ANY(CAST(:f AS uuid[]))"
    " ORDER BY family_id, (id <> family_id), id FOR NO KEY UPDATE"
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
_NOT_CURRENT = text(
    "UPDATE documents SET is_current = false WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
    " AND is_current"
)
_NUMBERS = text(
    "UPDATE documents d SET version_no = n.no FROM unnest(CAST(:ids AS uuid[]), CAST(:nos AS integer[]))"
    " AS n (id, no) WHERE d.customer_id = :c AND d.id = n.id"
)
_CURRENT = text(
    "UPDATE documents SET is_current = true WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
)
# A joining family into the latest's place, with its owner and label (Accept's ``_JOIN``).
_JOIN = text(
    "UPDATE documents SET family_id = :t, folder_id = :folder, held_for = :held, owner_id = :owner,"
    " sensitivity = :label, sensitivity_set_by_person = :set, autofile = NULL"
    " WHERE customer_id = :c AND family_id = :g"
)
_DROP_TAGS = text(
    "DELETE FROM document_tags WHERE customer_id = :c AND document_id = ANY(CAST(:ids AS uuid[]))"
)
_COPY_TAGS = text(
    "INSERT INTO document_tags (customer_id, document_id, tag_id)"
    " SELECT :c, j.id, t.tag_id FROM unnest(CAST(:ids AS uuid[])) AS j (id)"
    " JOIN document_tags t ON t.customer_id = :c AND t.document_id = :from"
)
# Review (#658): only the tags still there; one deleted since is gone from every file anyway.
_PUT_TAGS = text(
    "INSERT INTO document_tags (customer_id, document_id, tag_id)"
    " SELECT :c, x.id, x.tag FROM unnest(CAST(:ids AS uuid[]), CAST(:tags AS uuid[])) AS x (id, tag)"
    " JOIN tags t ON t.customer_id = :c AND t.id = x.tag"
)
# Undo (review on #658): each version given to exactly whom it was given before the link, version by
# version (a family's versions need not be given alike). First every other gift goes (a gift taken
# goes from the whole family, 0085), then the saved ones come back, but to a person switched off
# since (0090 took their gifts then).
_EXTRA_SHARES = text(
    "DELETE FROM document_shares x WHERE x.customer_id = :c AND x.document_id = ANY(CAST(:ids AS uuid[]))"
    ' AND NOT EXISTS (SELECT 1 FROM jsonb_to_recordset(CAST(:keep AS jsonb)) AS k (id uuid, "user" uuid)'
    ' WHERE k.id = x.document_id AND k."user" = x.user_id)'
)
_PUT_SHARES = text(
    "INSERT INTO document_shares (customer_id, document_id, user_id, given_by, given_at)"
    ' SELECT :c, k.id, k."user", k.given_by, k.given_at FROM jsonb_to_recordset(CAST(:keep AS jsonb))'
    ' AS k (id uuid, "user" uuid, given_by uuid, given_at timestamptz)'
    ' JOIN users u ON u.customer_id = :c AND u.id = k."user" AND NOT u.disabled'
    " ON CONFLICT DO NOTHING"
)
_MOVE_STORIES = text("SELECT kaagaz_move_stories(:from, :to, CAST(:users AS uuid[]))")
# The group members of these versions, taken in id order without waiting (V-B's NOWAIT, review on
# #648): a decision on them under way never waits on the families held here, nor they on it.
_MEMBERS_LOCK = text(
    "SELECT document_id FROM version_group_members WHERE customer_id = :c"
    " AND document_id = ANY(CAST(:ids AS uuid[])) ORDER BY document_id FOR NO KEY UPDATE NOWAIT"
)
LOCK_NOT_AVAILABLE = "55P03"
_LINKED = text(
    "UPDATE version_group_members SET state = 'linked', decided_by = :by, decided_at = now()"
    " WHERE customer_id = :c AND document_id = ANY(CAST(:ids AS uuid[])) AND state <> 'linked'"
)
_MEMBERS_BACK = text(
    "UPDATE version_group_members g SET state = m.state, decided_by = m.decided_by, decided_at = m.decided_at"
    " FROM jsonb_to_recordset(CAST(:members AS jsonb))"
    " AS m (id uuid, state text, decided_by uuid, decided_at timestamptz)"
    " WHERE g.customer_id = :c AND g.document_id = m.id AND g.state = 'linked'"
)
_LINK = text(
    "INSERT INTO version_links (customer_id, family_id, document_ids, snapshot, linked_by)"
    " VALUES (:c, :f, CAST(:ids AS uuid[]), CAST(:snapshot AS jsonb), :by) RETURNING id::text"
)
# Undo: the family's newest link not undone, naming the version.
_NEWEST_LINK = text(
    "SELECT id::text, document_ids::text[], snapshot FROM version_links WHERE customer_id = :c"
    " AND family_id = :f AND undone_at IS NULL ORDER BY linked_at DESC LIMIT 1"
)
# Each restored version's own place, owner and label, in one statement for one family.
_BACK = text(
    "UPDATE documents d SET family_id = :g, folder_id = s.folder, held_for = s.held, owner_id = s.owner,"
    " sensitivity = s.label, sensitivity_set_by_person = s.by_person, autofile = NULL"
    " FROM jsonb_to_recordset(CAST(:rows AS jsonb))"
    " AS s (id uuid, folder uuid, held uuid, owner uuid, label text, by_person boolean)"
    " WHERE d.customer_id = :c AND d.id = s.id"
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
_UNDONE = text(
    "UPDATE version_links SET undone_by = :by, undone_at = now() WHERE customer_id = :c AND id = :l"
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
    families = sorted(set(asked.values()))
    if lock:
        conn.execute(_LOCK, {"c": customer_id, "f": families})
    rows = {row["id"]: row for row in conn.execute(_STATE, {"c": customer_id, "f": families}).mappings()}
    if any(document not in rows or rows[document]["family_id"] != asked[document] for document in asked):
        raise Refused("changed")  # linked, separated or deleted before its family's lock was taken
    if not all(row["seen"] for row in rows.values()):
        raise Refused("not_found")
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


def _lock_members(conn: Connection, customer_id: str, versions: Sequence[str]) -> None:
    """``_MEMBERS_LOCK``; Refused("changed") when a decision holds one (nothing is written: the
    caller's transaction rolls back)."""
    try:
        conn.execute(_MEMBERS_LOCK, {"c": customer_id, "ids": list(versions)})
    except OperationalError as error:
        if sqlstate(error) != LOCK_NOT_AVAILABLE:
            raise
        raise Refused("changed") from None


def _snapshot(row: RowMapping) -> dict[str, Any]:
    member = row["member"]
    return {
        "family_id": row["family_id"],
        "version_no": int(row["version_no"]),
        "is_current": bool(row["is_current"]),
        "folder_id": row["folder_id"],
        "held_for": row["held_for"],
        "owner_id": row["owner_id"],
        "sensitivity": row["sensitivity"],
        "sensitivity_set_by_person": bool(row["sensitivity_set_by_person"]),
        "tags": list(row["tags"]),
        "shares": list(row["shares"]),
        "member": None if member is None else {k: member[k] for k in ("state", "by", "at")},
    }


def apply(conn: Connection, customer_id: str, plan: Plan, *, by: str) -> str:
    """The link (module notes), in the caller's transaction after ``prepare``; the link's id.
    O(v) statements of O(log D) for the v versions, plus one per joining family."""
    c, latest = customer_id, plan.rows[plan.current]
    snapshot: dict[str, Any] = {"versions": {id_: _snapshot(row) for id_, row in plan.rows.items()}}
    snapshot["families"] = {}
    every = list(plan.order)
    conn.execute(_NOT_CURRENT, {"c": c, "ids": every})
    shifted = [_SHIFT + n for n in range(1, len(every) + 1)]
    conn.execute(_NUMBERS, {"c": c, "ids": every, "nos": shifted})
    joined: list[str] = []
    for family in plan.joining:  # one family a statement (0100's trigger reads the family's first)
        versions = [id_ for id_ in every if plan.rows[id_]["family_id"] == family]
        conn.execute(
            _JOIN,
            {
                "c": c,
                "t": plan.family,
                "g": family,
                "folder": latest["folder_id"],
                "held": latest["held_for"],
                "owner": latest["owner_id"],
                "label": latest["sensitivity"],
                "set": latest["sensitivity_set_by_person"],
            },
        )
        stories = conn.execute(_MOVE_STORIES, {"from": family, "to": plan.family, "users": None}).scalars()
        snapshot["families"][family] = {"stories": sorted(str(u) for u in stories)}
        joined += versions
    conn.execute(_NUMBERS, {"c": c, "ids": every, "nos": list(range(1, len(every) + 1))})
    conn.execute(_CURRENT, {"c": c, "ids": [plan.current]})
    conn.execute(_DROP_TAGS, {"c": c, "ids": joined})
    conn.execute(_COPY_TAGS, {"c": c, "ids": joined, "from": plan.current})
    _lock_members(conn, c, every)
    conn.execute(_LINKED, {"c": c, "ids": every, "by": by})
    link = str(
        conn.execute(
            _LINK, {"c": c, "f": plan.family, "ids": every, "snapshot": json.dumps(snapshot), "by": by}
        ).scalar_one()
    )
    for number, version in enumerate(every, 1):
        audit.record(
            conn,
            c,
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


def prepare_undo(conn: Connection, customer_id: str, document_id: str) -> UndoPlan:
    """The Undo of the newest link of the version's family (module notes), under the tree then the
    family lock. Raises Refused (not_found, locked, not_linked, not_newest, folder_gone) or
    trash.InTrash. O(v log D)."""
    c = customer_id
    _lock_tree(conn, c)
    family = lock_live_family(conn, c, document_id)
    if family is None:
        raise Refused("not_found")
    rows = {row["id"]: row for row in conn.execute(_STATE, {"c": c, "f": [family]}).mappings()}
    if not all(row["seen"] for row in rows.values()):
        raise Refused("not_found")  # review (#658): every version, not only the one asked
    if any(row["locked"] for row in rows.values()):
        raise Refused("locked")
    found = conn.execute(_NEWEST_LINK, {"c": c, "f": family}).first()
    if found is None or document_id not in found[1]:
        raise Refused("not_linked")
    link, order, snapshot = found[0], list(found[1]), found[2]
    if set(rows) != set(order):
        raise Refused("not_newest")  # a version uploaded, linked or made separate since
    back = snapshot["versions"]
    folders = sorted({v["folder_id"] for v in back.values() if v["folder_id"] is not None})
    if len(conn.execute(text(LIVE_FOLDERS), {"c": c, "ids": folders}).all()) < len(folders):
        raise Refused("folder_gone")
    here = rows[document_id]  # where every version is now: the family's place
    moves: list[Moved] = []
    for each in snapshot["families"]:
        versions = [id_ for id_ in order if back[id_]["family_id"] == each]
        first = back[versions[0]]
        now, then = _given([rows[id_] for id_ in versions]), _given([back[id_] for id_ in versions])
        after = place_of(conn, c, first["folder_id"])
        moves.append(Moved(_place(here), here["held_for"], now, after, first["held_for"], then))
    return UndoPlan(link, family, order, snapshot, list(dict.fromkeys(moves)))


def apply_undo(conn: Connection, customer_id: str, plan: UndoPlan, *, by: str) -> None:
    """Undo (module notes), in the caller's transaction after ``prepare_undo``. O(v) for the v
    versions, plus one statement per family put back."""
    c, back = customer_id, plan.snapshot["versions"]
    conn.execute(_NOT_CURRENT, {"c": c, "ids": plan.order})
    shifted = [_SHIFT + n for n in range(1, len(plan.order) + 1)]
    conn.execute(_NUMBERS, {"c": c, "ids": plan.order, "nos": shifted})
    moved = [id_ for id_ in plan.order if back[id_]["family_id"] != plan.family]
    for family, carried in plan.snapshot["families"].items():
        rows = [
            {
                "id": id_,
                "folder": back[id_]["folder_id"],
                "held": back[id_]["held_for"],
                "owner": back[id_]["owner_id"],
                "label": back[id_]["sensitivity"],
                "by_person": back[id_]["sensitivity_set_by_person"],
            }
            for id_ in plan.order
            if back[id_]["family_id"] == family
        ]
        conn.execute(_BACK, {"c": c, "g": family, "rows": json.dumps(rows)})
        ids = [row["id"] for row in rows]
        keep = [{"id": id_, **share} for id_ in ids for share in back[id_]["shares"]]
        conn.execute(_EXTRA_SHARES, {"c": c, "ids": ids, "keep": json.dumps(keep)})
        conn.execute(_PUT_SHARES, {"c": c, "keep": json.dumps(keep)})
        if carried["stories"]:
            conn.execute(_MOVE_STORIES, {"from": plan.family, "to": family, "users": carried["stories"]})
    conn.execute(
        _NUMBERS, {"c": c, "ids": plan.order, "nos": [back[id_]["version_no"] for id_ in plan.order]}
    )
    conn.execute(_CURRENT, {"c": c, "ids": [id_ for id_ in plan.order if back[id_]["is_current"]]})
    conn.execute(_DROP_TAGS, {"c": c, "ids": moved})
    tagged = [(id_, tag) for id_ in moved for tag in back[id_]["tags"]]
    conn.execute(_PUT_TAGS, {"c": c, "ids": [i for i, _ in tagged], "tags": [t for _, t in tagged]})
    members = [
        {"id": id_, "state": m["state"], "decided_by": m["by"], "decided_at": m["at"]}
        for id_ in plan.order
        if (m := back[id_]["member"]) is not None
    ]
    _lock_members(conn, c, plan.order)
    conn.execute(_MEMBERS_BACK, {"c": c, "members": json.dumps(members)})
    conn.execute(_UNDONE, {"c": c, "l": plan.link, "by": by})
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
