"""Who can see what: the access API's statements (PRD 7 PR 10a, #601).

Every function takes a connection from ``Database.user(customer_id, user_id)`` (the caller,
checked by the route first) and also filters on ``customer_id``. Folders are read through
``visible_folders`` (an Admin sees every folder's name, 0086), files through the owner-rights
functions of 0090 (an Admin's screens show true names, decision 20), and the access tables
directly: they hold ids only. Every write comes after the route's check and the tree lock
(``folders._lock_tree``), and each is one statement whose result says whether it changed
anything, so a repeat is a no-op (``ON CONFLICT``).

Only ``kaagaz/api`` calls these: they read the views, which the worker must never do.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, RowMapping, text

from kaagaz.db import folders
from kaagaz.db.audit import _FOLDER_PATHS  # a folder's path through visible_folders, as the feeds show it


@dataclass(frozen=True, slots=True)
class Place:
    """A live folder as the access rules see it."""

    id: str
    name: str
    parent_id: str | None
    kind: str
    private: bool
    department_only: bool
    department_id: str | None
    private_folder_id: str | None


@dataclass(frozen=True, slots=True)
class AdminDocument:
    """``kaagaz_admin_document``'s row."""

    id: str
    family_id: str
    in_trash: bool


# PRD 7 (#601): in_department, a member of a live department (11c's "Ask your admin for access").
_ME = text(
    "SELECT a.management, a.heads, EXISTS (SELECT 1 FROM visible_folders f"
    " WHERE f.customer_id = kaagaz_current_customer() AND f.id = ANY (a.departments)"
    " AND f.parent_id IS NULL AND f.kind = 'department')"
    " FROM kaagaz_access() a"
)
_PERSON = text(
    "SELECT disabled, management, display_name, role FROM users WHERE customer_id = :c AND id = :u"
)
_PLACE = text(
    "SELECT id, name, parent_id, kind, private, department_only, department_id, private_folder_id"
    " FROM visible_folders WHERE customer_id = :c AND id = :f"
)
_HOLDS_PRIVATE = text(
    "SELECT EXISTS (SELECT 1 FROM visible_folders WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
    " AND private AND id <> :f)"
)
_DEPARTMENTS = text(
    "SELECT m.user_id, m.department_id, f.name, m.is_head, m.added_at, m.added_by FROM department_members m"
    " JOIN visible_folders f ON f.customer_id = m.customer_id AND f.id = m.department_id"
    " WHERE m.customer_id = :c AND (CAST(:u AS uuid) IS NULL OR m.user_id = CAST(:u AS uuid))"
    " ORDER BY lower(f.name), f.id"
)
_NAMED_ON = text(
    "SELECT f.id, f.name FROM private_folder_members p"
    " JOIN visible_folders f ON f.customer_id = p.customer_id AND f.id = p.folder_id AND f.private"
    " WHERE p.customer_id = :c AND p.user_id = :u ORDER BY lower(f.name), f.id"
)
# The files given to :u, one per family (its current version's name and folder): an Admin's screen.
# PRD 7 (#606): by their true names, the files the Admin can't see too (decision 20): the
# owner-rights kaagaz_admin_given_to (0091) checks the caller; ``:c`` keeps another customer's id at none.
_GIVEN_TO = text(
    "SELECT * FROM kaagaz_admin_given_to(CAST(:u AS uuid)) WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
_PEOPLE_ON = text(
    "SELECT u.id, u.display_name FROM private_folder_members p"
    " JOIN users u ON u.customer_id = p.customer_id AND u.id = p.user_id"
    " WHERE p.customer_id = :c AND p.folder_id = :f AND (NOT :active OR NOT u.disabled)"
    " ORDER BY lower(u.display_name), u.id"
)
_HEADS_OF = text(
    "SELECT u.id, u.display_name FROM department_members m"
    " JOIN users u ON u.customer_id = m.customer_id AND u.id = m.user_id"
    " WHERE m.customer_id = :c AND m.department_id = :d AND m.is_head ORDER BY lower(u.display_name), u.id"
)
_MEMBERS = text("SELECT count(*) FROM department_members WHERE customer_id = :c AND department_id = :d")
_IS_MEMBER = text(
    "SELECT is_head FROM department_members WHERE customer_id = :c AND department_id = :d AND user_id = :u"
)
# Added, or the Head tick changed: one row, ``added`` true for a new member; none for a no-op. A
# Head tick left out (NULL) is false for a new member and keeps an existing member's.
_JOIN = text(
    "INSERT INTO department_members AS m (customer_id, department_id, user_id, is_head, added_by)"
    " VALUES (:c, :d, :u, coalesce(CAST(:head AS boolean), false), :by)"
    " ON CONFLICT (customer_id, department_id, user_id) DO UPDATE SET is_head = EXCLUDED.is_head"
    " WHERE CAST(:head AS boolean) IS NOT NULL AND m.is_head <> EXCLUDED.is_head"
    " RETURNING m.xmax = 0 AS added"
)
_LEAVE = text(
    "DELETE FROM department_members WHERE customer_id = :c AND department_id = :d AND user_id = :u"
    " RETURNING is_head"
)
_DEPARTMENT_ONLY = text("UPDATE folders SET department_only = :on WHERE customer_id = :c AND id = :d")
_PRIVATE = text("UPDATE folders SET private = :on WHERE customer_id = :c AND id = :f")
_NAME = text(
    "INSERT INTO private_folder_members (customer_id, folder_id, user_id, added_by) VALUES (:c, :f, :u, :by)"
    " ON CONFLICT DO NOTHING RETURNING user_id"
)
_UNNAME = text(
    "DELETE FROM private_folder_members WHERE customer_id = :c AND folder_id = :f AND user_id = :u"
    " RETURNING user_id"
)
_PLACE_AUDIENCE = text("SELECT user_id, sees FROM kaagaz_place_audience(:d, :p, :only)")
_ADMIN_DOCUMENT = text("SELECT id, family_id, in_trash FROM kaagaz_admin_document(:d)")
_SHARES = text(
    "SELECT s.user_id, u.display_name, s.given_at, s.given_by, g.display_name AS given_by_name"
    " FROM document_shares s JOIN users u ON u.customer_id = s.customer_id AND u.id = s.user_id"
    " LEFT JOIN users g ON g.customer_id = s.customer_id AND g.id = s.given_by"
    " WHERE s.customer_id = :c AND s.document_id = :d ORDER BY lower(u.display_name), u.id"
)
_GIVE = text("SELECT user_id, versions, added FROM kaagaz_give_file(:d, CAST(:users AS uuid[]))")
_TAKE = text("SELECT kaagaz_take_file(:d, :u)")

# PR 10b (#601): the reads.
_AUDIENCE = text(
    "SELECT a.user_id, u.display_name, a.management, a.head, a.member, a.named, a.given, a.held, a.everyone,"
    " a.may_open_locked, a.sees FROM kaagaz_audience(:k, :i, :u) a"
    " JOIN users u ON u.customer_id = :c AND u.id = a.user_id ORDER BY lower(u.display_name), u.id"
)
# The place of a file already found (the view, or kaagaz_admin_document for an Admin): ids only.
# PRD 7 (#606): through kaagaz_document_place (0091), so an Admin's "Who can see this" reads a file
# they can't see too (the database rule hides it from the table).
_DOCUMENT_PLACE = text(
    "SELECT department_id, private_folder_id FROM kaagaz_document_place(:d)"
    " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
_DEPARTMENT_LIST = text(
    "SELECT f.id, f.name, f.department_only,"
    " (SELECT count(*) FROM department_members m"
    " WHERE m.customer_id = f.customer_id AND m.department_id = f.id)"
    " AS members,"
    " (SELECT coalesce(json_agg(json_build_object('id', u.id, 'name', u.display_name)"
    " ORDER BY lower(u.display_name), u.id), '[]') FROM department_members m"
    " JOIN users u ON u.customer_id = m.customer_id AND u.id = m.user_id"
    " WHERE m.customer_id = f.customer_id AND m.department_id = f.id AND m.is_head) AS heads"
    " FROM visible_folders f WHERE f.customer_id = :c AND f.parent_id IS NULL AND f.kind = 'department'"
    " ORDER BY f.sort_order, lower(f.name), f.id"
)
_KEEPS = text(
    "SELECT EXISTS (SELECT 1 FROM visible_folders WHERE customer_id = :c AND (department_only OR private))"
)
_MEMBERS_OF = text(
    "SELECT u.id, u.display_name, m.is_head, m.added_at FROM department_members m"
    " JOIN users u ON u.customer_id = m.customer_id AND u.id = m.user_id"
    " WHERE m.customer_id = :c AND m.department_id = :d ORDER BY lower(u.display_name), u.id"
)
# The live files whose audience a wall changes (an Admin's preview: true totals, decision 20).
# PRD 7 (#606): an Admin's preview counts every file (kaagaz_admin_files, 0091), the hidden ones too.
_DEPARTMENT_FILES = text(
    "SELECT kaagaz_admin_files(CAST(:d AS uuid), NULL) WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
_FOLDER_FILES = text(
    "SELECT kaagaz_admin_files(NULL, CAST(:f AS uuid[])) WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
# PR 10c (#601): a file's place with whom it is held for, for a move; who sees a held file.
_DOCUMENT_HOLD = text(
    "SELECT department_id, private_folder_id, held_for FROM kaagaz_document_place(:d)"
    " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
_MANAGEMENT = text("SELECT id FROM users WHERE customer_id = :c AND management AND NOT disabled")
_CLOSED = text("SELECT department_only FROM folders WHERE customer_id = :c AND id = :d")
_NAMES = text(
    "SELECT id, display_name FROM users WHERE customer_id = :c AND id = ANY(CAST(:u AS uuid[]))"
    " ORDER BY lower(display_name), id"
)
_KEPT_FROM_ME = text(
    "SELECT id, display_name, detected_type, uploader, created_at, version_no, locked"
    " FROM kaagaz_kept_from_me(:f) LIMIT :n"
)
_KEPT_COUNT = text("SELECT count(*) FROM kaagaz_kept_from_me(:f)")
# PRD 7 section 3 (#606): the names list's Send to Trash, the file's family seen or not (0092).
_ADMIN_TRASH = text(
    "SELECT document_id FROM kaagaz_admin_trash_documents(ARRAY[CAST(:d AS uuid)], :now)"
    " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)


def me(conn: Connection) -> tuple[bool, list[str], bool]:
    """The caller's Management, the departments they head (``/auth/me``, the Head checks) and
    whether they are a member of a live department."""
    management, heads, in_department = conn.execute(_ME).one()
    return bool(management), [str(each) for each in heads], bool(in_department)


def rights(conn: Connection, admin: bool) -> folders.Rights:
    """The caller's rights to move what changes who can see it (PRD 7 decision 18): read under the
    tree lock, so a Head tick or Management taken away meanwhile counts."""
    management, heads, _ = me(conn)
    return folders.Rights(wide=admin or management, heads=frozenset(heads))


@dataclass(frozen=True, slots=True)
class Person:
    disabled: bool
    management: bool
    name: str
    admin: bool


def person(conn: Connection, customer_id: str, user_id: str) -> Person | None:
    """A user of this organisation, or None."""
    row = conn.execute(_PERSON, {"c": customer_id, "u": user_id}).first()
    if row is None:
        return None
    return Person(disabled=bool(row[0]), management=bool(row[1]), name=str(row[2]), admin=row[3] == "admin")


def place(conn: Connection, customer_id: str, folder_id: str) -> Place | None:
    """A live folder the caller may see, or None."""
    row = conn.execute(_PLACE, {"c": customer_id, "f": folder_id}).first()
    if row is None:
        return None
    return Place(
        id=str(row[0]),
        name=str(row[1]),
        parent_id=None if row[2] is None else str(row[2]),
        kind=str(row[3]),
        private=bool(row[4]),
        department_only=bool(row[5]),
        department_id=None if row[6] is None else str(row[6]),
        private_folder_id=None if row[7] is None else str(row[7]),
    )


def holds_private(conn: Connection, customer_id: str, folder_id: str, subtree: Sequence[str]) -> bool:
    """Whether a folder of ``subtree`` other than ``folder_id`` is private."""
    return bool(
        conn.execute(_HOLDS_PRIVATE, {"c": customer_id, "f": folder_id, "ids": list(subtree)}).scalar()
    )


def departments(conn: Connection, customer_id: str, user_id: str | None = None) -> Sequence[RowMapping]:
    """Memberships of live departments (every person's, or ``user_id``'s), by department name."""
    return conn.execute(_DEPARTMENTS, {"c": customer_id, "u": user_id}).mappings().all()


def named_on(conn: Connection, customer_id: str, user_id: str) -> Sequence[RowMapping]:
    """The live folders ``user_id`` is named on, while they are private."""
    return conn.execute(_NAMED_ON, {"c": customer_id, "u": user_id}).mappings().all()


def given_to(conn: Connection, customer_id: str, user_id: str) -> Sequence[RowMapping]:
    """The live files given to ``user_id``, one per family."""
    return conn.execute(_GIVEN_TO, {"c": customer_id, "u": user_id}).mappings().all()


def paths(conn: Connection, customer_id: str, folder_ids: Sequence[str]) -> dict[str, str]:
    """Each folder's path from the top, through the folders the caller may see."""
    if not folder_ids:
        return {}
    rows = conn.execute(_FOLDER_PATHS, {"c": customer_id, "ids": sorted(set(folder_ids))})
    return {str(row[0]): str(row[1]) for row in rows}


def people_on(
    conn: Connection, customer_id: str, folder_id: str, *, active: bool = False
) -> list[dict[str, str]]:
    """The people named on the folder (kept while it is open); ``active``: those switched on only."""
    params = {"c": customer_id, "f": folder_id, "active": active}
    return [{"id": str(i), "name": str(n)} for i, n in conn.execute(_PEOPLE_ON, params)]


def heads_of(conn: Connection, customer_id: str, department_id: str | None) -> list[dict[str, str]]:
    if department_id is None:
        return []
    rows = conn.execute(_HEADS_OF, {"c": customer_id, "d": department_id})
    return [{"id": str(i), "name": str(n)} for i, n in rows]


def members(conn: Connection, customer_id: str, department_id: str) -> int:
    return int(conn.execute(_MEMBERS, {"c": customer_id, "d": department_id}).scalar_one())


def membership(conn: Connection, customer_id: str, department_id: str, user_id: str) -> bool | None:
    """Whether ``user_id`` heads the department; None when not a member."""
    head = conn.execute(_IS_MEMBER, {"c": customer_id, "d": department_id, "u": user_id}).scalar()
    return None if head is None else bool(head)


def join(
    conn: Connection, customer_id: str, department_id: str, user_id: str, *, head: bool | None, by: str
) -> bool | None:
    """Add the member, or set their Head tick (None: keep it): True added, False the tick changed,
    None no-op."""
    params = {"c": customer_id, "d": department_id, "u": user_id, "head": head, "by": by}
    added = conn.execute(_JOIN, params).scalar()
    return None if added is None else bool(added)


def leave(conn: Connection, customer_id: str, department_id: str, user_id: str) -> bool | None:
    """Remove the member; whether they were its Head, None when not a member."""
    was_head = conn.execute(_LEAVE, {"c": customer_id, "d": department_id, "u": user_id}).scalar()
    return None if was_head is None else bool(was_head)


def set_department_only(conn: Connection, customer_id: str, department_id: str, *, on: bool) -> None:
    conn.execute(_DEPARTMENT_ONLY, {"c": customer_id, "d": department_id, "on": on})


def set_private(conn: Connection, customer_id: str, folder_id: str, *, on: bool) -> None:
    conn.execute(_PRIVATE, {"c": customer_id, "f": folder_id, "on": on})


def name(conn: Connection, customer_id: str, folder_id: str, user_id: str, *, by: str) -> bool:
    """Name the person on the private folder; False when they already were."""
    params = {"c": customer_id, "f": folder_id, "u": user_id, "by": by}
    return conn.execute(_NAME, params).first() is not None


def unname(conn: Connection, customer_id: str, folder_id: str, user_id: str) -> bool:
    return conn.execute(_UNNAME, {"c": customer_id, "f": folder_id, "u": user_id}).first() is not None


def place_audience(
    conn: Connection, department_id: str | None, private_folder_id: str | None, *, department_only: bool
) -> set[str]:
    """The active people who see the files of such a place (``kaagaz_place_audience``): an Admin,
    Management, or the Head of ``department_id``."""
    params = {"d": department_id, "p": private_folder_id, "only": department_only}
    return {str(user) for user, sees in conn.execute(_PLACE_AUDIENCE, params) if sees}


def admin_document(conn: Connection, document_id: str) -> AdminDocument | None:
    """A file for an Admin, seen or not (``kaagaz_admin_document``)."""
    row = conn.execute(_ADMIN_DOCUMENT, {"d": document_id}).first()
    return (
        None if row is None else AdminDocument(id=str(row[0]), family_id=str(row[1]), in_trash=bool(row[2]))
    )


def shares(conn: Connection, customer_id: str, document_id: str) -> Sequence[RowMapping]:
    """Who this file version is given to, and by whom."""
    return conn.execute(_SHARES, {"c": customer_id, "d": document_id}).mappings().all()


def give(conn: Connection, document_id: str, user_ids: Sequence[str]) -> tuple[int, dict[str, int]]:
    """(live versions, the versions newly given to each person) in one family lock and one insert;
    0 versions for a file in Trash or gone. Admins; every person switched on (else an error)."""
    rows = conn.execute(_GIVE, {"d": document_id, "users": list(user_ids)}).all()
    return (int(rows[0][1]) if rows else 0), {str(user): int(added) for user, _, added in rows}


def take(conn: Connection, document_id: str, user_id: str) -> int:
    """How many version rows went (every version, Trash too). Admins."""
    return int(conn.execute(_TAKE, {"d": document_id, "u": user_id}).scalar_one())


def audience(
    conn: Connection, customer_id: str, kind: str, target_id: str, user_id: str | None
) -> Sequence[RowMapping]:
    """``kaagaz_audience``'s rows with each person's name, by name: every active person (or only
    ``user_id``, none when not of this organisation). An Admin, or the Head of its department."""
    params = {"c": customer_id, "k": kind, "i": target_id, "u": user_id}
    return conn.execute(_AUDIENCE, params).mappings().all()


def document_place(
    conn: Connection, customer_id: str, document_id: str
) -> tuple[str | None, str | None] | None:
    """(department, private folder) ids of a file the route has already found; None once gone."""
    row = conn.execute(_DOCUMENT_PLACE, {"c": customer_id, "d": document_id}).first()
    if row is None:
        return None
    return (None if row[0] is None else str(row[0])), (None if row[1] is None else str(row[1]))


def department_list(conn: Connection, customer_id: str) -> Sequence[RowMapping]:
    """Every live department with Department only, its member count and its Heads, in tree order."""
    return conn.execute(_DEPARTMENT_LIST, {"c": customer_id}).mappings().all()


def keeps_files(conn: Connection, customer_id: str) -> bool:
    """Whether a department is Department only or a folder private (the one-time card)."""
    return bool(conn.execute(_KEEPS, {"c": customer_id}).scalar_one())


def members_of(conn: Connection, customer_id: str, department_id: str) -> Sequence[RowMapping]:
    return conn.execute(_MEMBERS_OF, {"c": customer_id, "d": department_id}).mappings().all()


def department_files(conn: Connection, customer_id: str, department_id: str) -> int:
    """The live files Department only decides for: the department's, outside private folders."""
    return int(conn.execute(_DEPARTMENT_FILES, {"c": customer_id, "d": department_id}).scalar_one())


def folder_files(conn: Connection, customer_id: str, folder_ids: Sequence[str]) -> int:
    """The live files in these folders (a folder's subtree, for Private)."""
    return int(conn.execute(_FOLDER_FILES, {"c": customer_id, "f": list(folder_ids)}).scalar_one())


def names(conn: Connection, customer_id: str, user_ids: set[str]) -> list[dict[str, str]]:
    """``[{id, name}]`` of these people, by name."""
    if not user_ids:
        return []
    rows = conn.execute(_NAMES, {"c": customer_id, "u": sorted(user_ids)})
    return [{"id": str(i), "name": str(n)} for i, n in rows]


def admin_trash(conn: Connection, customer_id: str, document_id: str, *, now: datetime) -> None:
    """The file's whole family to Trash at ``now``, by the calling Admin, whether they can see it or
    not (``kaagaz_admin_trash_documents``: the tree lock, then the family's); one already there keeps
    its first time. The caller found it with ``admin_document``."""
    conn.execute(_ADMIN_TRASH, {"c": customer_id, "d": document_id, "now": now}).all()


def kept_from_me(conn: Connection, folder_id: str, limit: int) -> tuple[int, Sequence[RowMapping]]:
    """How many files in the folder the calling Admin can't see, and the first ``limit`` of their
    names, by name (``kaagaz_kept_from_me``)."""
    count = int(conn.execute(_KEPT_COUNT, {"f": folder_id}).scalar_one())
    return count, conn.execute(_KEPT_FROM_ME, {"f": folder_id, "n": limit}).mappings().all()


def document_hold(
    conn: Connection, customer_id: str, document_id: str
) -> tuple[folders.Place, str | None] | None:
    """A file already found: its (department, private folder) and whom it is held for; None once gone."""
    row = conn.execute(_DOCUMENT_HOLD, {"c": customer_id, "d": document_id}).first()
    if row is None:
        return None
    department, private, held = (None if v is None else str(v) for v in row)
    return (department, private), held


def _sees(conn: Connection, customer_id: str, place: folders.Place, held: str | None = None) -> set[str]:
    """The active people who see what is in ``place``; a file held for ``held``: them and Management."""
    if held is not None:
        return {str(u) for u in conn.execute(_MANAGEMENT, {"c": customer_id}).scalars()} | {held}
    closed = place[0] is not None and bool(conn.execute(_CLOSED, {"c": customer_id, "d": place[0]}).scalar())
    return place_audience(conn, place[0], place[1], department_only=closed)


def moved(
    conn: Connection, customer_id: str, move: folders.Move, held: str | None = None
) -> tuple[set[str], set[str]]:
    """Who loses and who gains what a move carries (PRD 7 section 5, "6 people won't see this"): the
    files of its place (``own``; a file held for ``held``: from its holder and Management) and of each
    private folder it carries, whose Head is the department's. Admins, Management and a Head
    (``kaagaz_place_audience``)."""
    pairs = (
        [(_sees(conn, customer_id, move.before, held), _sees(conn, customer_id, move.after))]
        if move.own
        else []
    )
    pairs += [
        (_sees(conn, customer_id, (move.before[0], p)), _sees(conn, customer_id, (move.after[0], p)))
        for p in move.private
    ]
    losing: set[str] = set().union(*(before - after for before, after in pairs))
    gaining: set[str] = set().union(*(after - before for before, after in pairs))
    return losing, gaining
