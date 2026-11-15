"""Locked files: the lock, unlock and lock grant statements (PRD 7 section 6, PR 12, #605).

Each write is one call of an owner-rights function of migration 0093, which checks its caller
(an Admin of this organisation), takes the tree lock (the organisation's row) and then the family
locks itself, reads every file again under them and filters on the organisation, so an Admin can
lock, unlock and grant a file they can't open (decision 20). ``conn`` comes from
``Database.user(customer_id, user_id)``, the Admin's own transaction, after the route proved their
password. The reads name the organisation too.

Only ``kaagaz/api`` calls these. The worker never locks anything: it only prepares a locked version
(``kaagaz.pipeline``).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, RowMapping, text

# PRD 7 section 6: the kinds Kaagaz draws as page pictures (0093's LOCKABLE; the migration test pins
# them together): PDFs, photos and scans, Word and PowerPoint files.
LOCKABLE = ("pdf", "png", "jpeg", "tiff", "heic", "docx", "dotx", "doc", "pptx", "ppt")
MAX_OPEN_TO = 50  # 0093's

_LOCK = text(
    "SELECT document_id, outcome, version_id, prepare, rendition"
    " FROM kaagaz_lock(CAST(:d AS uuid[]), CAST(:u AS uuid[]))"
)
_UNLOCK = text("SELECT document_id, outcome, version_id, read FROM kaagaz_unlock(CAST(:d AS uuid[]))")
_GRANT = text("SELECT kaagaz_lock_grant(:d, :u)")
_REVOKE = text("SELECT kaagaz_lock_revoke(:d, :u)")
_GRANTS = text(
    "SELECT g.user_id, u.display_name, u.disabled, g.given_at, g.given_by, b.display_name AS given_by_name"
    " FROM lock_grants g JOIN users u ON u.customer_id = g.customer_id AND u.id = g.user_id"
    " LEFT JOIN users b ON b.customer_id = g.customer_id AND b.id = g.given_by"
    " WHERE g.customer_id = :c AND g.document_id = :d ORDER BY lower(u.display_name), u.id"
)


@dataclass(frozen=True, slots=True)
class Version:
    """A version a lock or an unlock changed: ``prepare`` (a lock: its pages have no pictures
    yet, so the worker prepares it) or ``read`` (an unlock: it is read again); ``rendition``, a
    Word or PowerPoint file whose stored PDF the lock deletes."""

    document_id: str  # the file asked for
    version_id: str
    queue: bool
    rendition: bool = False


@dataclass(frozen=True, slots=True)
class Outcome:
    """All or nothing: ``refused`` (file asked for -> why) and nothing written, or the versions
    changed and the files ``already`` as asked (locked already, for a lock)."""

    refused: dict[str, str]
    versions: list[Version]
    already: list[str]


def lock(conn: Connection, document_ids: Sequence[str], open_to: Sequence[str]) -> Outcome:
    """Lock the files (every version) and allow ``open_to`` to open them: refused ``not_found``,
    ``in_trash``, ``kind``, ``no_pictures``; already ``already_locked``. A person in ``open_to`` who
    is not switched on, or not of this organisation, is a check_violation (the route checks first)."""
    rows = conn.execute(_LOCK, {"d": list(document_ids), "u": list(open_to)}).all()
    refused = {str(r[0]): str(r[1]) for r in rows if r[1] not in ("locked", "already_locked")}
    if refused:
        return Outcome(refused, [], [])
    versions = [Version(str(r[0]), str(r[2]), bool(r[3]), bool(r[4])) for r in rows if r[1] == "locked"]
    return Outcome({}, versions, [str(r[0]) for r in rows if r[1] == "already_locked"])


def unlock(conn: Connection, document_ids: Sequence[str]) -> Outcome:
    """Unlock the files (every version, their grants gone): refused ``not_found``, ``in_trash``,
    ``not_locked``. The current version of each is set to be read again, billed to the caller."""
    rows = conn.execute(_UNLOCK, {"d": list(document_ids)}).all()
    refused = {str(r[0]): str(r[1]) for r in rows if r[1] != "unlocked"}
    if refused:
        return Outcome(refused, [], [])
    return Outcome({}, [Version(str(r[0]), str(r[2]), bool(r[3])) for r in rows], [])


def grant(conn: Connection, document_id: str, user_id: str) -> str:
    """Allow the person to open the locked file, on every version: ``granted``, ``already``,
    ``not_locked``, ``not_found`` or ``disabled`` (switched off, or not of this organisation)."""
    return str(conn.execute(_GRANT, {"d": document_id, "u": user_id}).scalar_one())


def revoke(conn: Connection, document_id: str, user_id: str) -> int:
    """The person's grant taken from every version of the file: how many rows went."""
    return int(conn.execute(_REVOKE, {"d": document_id, "u": user_id}).scalar_one())


def grants(conn: Connection, customer_id: str, document_id: str) -> Sequence[RowMapping]:
    """Who may open this locked version, by name, and who allowed them."""
    return conn.execute(_GRANTS, {"c": customer_id, "d": document_id}).mappings().all()
