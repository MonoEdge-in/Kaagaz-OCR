"""Kaagaz on WhatsApp's uploads (#603 W5, PRD 7 section 15 "Upload a file", "Choosing a folder"),
each in the person's own transaction (``db.user``): the folders through PRD 7's ``visible_folders``,
as the browser's tree reads them, so a folder the person can't see is never listed, counted, named
in a path or saved into. Nothing here reads a folder outside the view, and each statement filters
in its own CTE before it orders, pages or counts (never after).

* ``level``: the folders one level shows, by name: the top (a folder whose parent the person can't
  see is at the top for them, as in the browser), or inside a folder they see. A page of them.
* ``named``: the folders whose name holds some words (Type a folder name).
* ``about``: for folders the person sees, the name, the path (only when every folder in it is one
  they see, else None), the folder above as they see it, and how many folders they see inside.
* ``run``: the person's waiting messages that are files (a photo or a document) sent one after
  another, oldest first, and whether a message of another kind waits after them.
* ``files_this_hour``: the files the person sent in the last hour (the rate).
* ``await_notice``: a message whose file was saved waits again, with its document and the chat's
  language, to send that file's "ready" notice (kaagaz/channel/loop.py). ``last_sent``,
  ``park_notice`` and ``unpark_notices``: a notice past WhatsApp's 24 hours since the person's
  newest message is put aside (``expired``, its document and language kept) until their next one.
* ``recent`` and ``add_recent``: the connection's last 3 folders saved into, newest first.
* ``locked``: which copies the person sees are locked, in Trash or not. ``batch_notices``: the
  waiting notices of one batch, for one "ready" message.

Each is one statement; the folder ones O(F) for the F folders the person sees (``visible_folders``
is read once, as the browser's tree reads it).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, text

UPLOAD_KINDS = ("image", "document")
PATH = " " + chr(0x203A) + " "  # between the folders of a path, as the browser shows it
RUN_MOST = 100  # the waiting messages a run is looked for in

# The folders the person sees, each with the folder above as they see it (NULL: the top, or a folder
# above they can't see), as db/folders.py's _LIST; then one level of them, paged.
_LEVEL = text(
    "WITH placed AS (SELECT f.id, f.name, p.id AS above FROM visible_folders f"
    " LEFT JOIN visible_folders p ON p.customer_id = f.customer_id AND p.id = f.parent_id"
    " WHERE f.customer_id = :c)"
    " SELECT id FROM placed WHERE above IS NOT DISTINCT FROM CAST(:at AS uuid)"
    " AND NOT id = ANY(CAST(:without AS uuid[]))"
    " ORDER BY lower(name), id OFFSET :skip LIMIT :n"
)
_NAMED = text(
    "WITH seen AS (SELECT f.id, f.name FROM visible_folders f WHERE f.customer_id = :c"
    " AND strpos(lower(f.name), lower(:words)) > 0)"
    " SELECT id FROM seen ORDER BY lower(name), id LIMIT :n"
)
# Up from each folder through the folders the person sees only: a path reaching a folder with no
# parent is whole; one stopped by a folder above that they can't see is not (NULL).
_ABOUT = text(
    "WITH RECURSIVE up (start, id, parent_id, name, depth) AS ("
    " SELECT f.id, f.id, f.parent_id, f.name, 1 FROM visible_folders f"
    " WHERE f.customer_id = :c AND f.id = ANY(CAST(:ids AS uuid[]))"
    " UNION ALL"
    " SELECT up.start, f.id, f.parent_id, f.name, up.depth + 1 FROM up"
    " JOIN visible_folders f ON f.customer_id = :c AND f.id = up.parent_id"
    " WHERE up.depth < 101)"
    " SELECT up.start,"
    " min(up.name) FILTER (WHERE up.depth = 1) AS name,"
    " CASE WHEN bool_or(up.parent_id IS NULL) THEN string_agg(up.name, :sep ORDER BY up.depth DESC)"
    " END AS path,"
    " min(up.id::text) FILTER (WHERE up.depth = 2) AS above,"
    " (SELECT count(*) FROM visible_folders k WHERE k.customer_id = :c AND k.parent_id = up.start) AS inside"
    " FROM up GROUP BY up.start"
)
# On whatsapp_messages_waiting; a notice (document_id set) is not a message the person sent.
_RUN = text(
    "SELECT id, kind, media_id, body, sent_at FROM whatsapp_messages"
    " WHERE customer_id = :c AND link_id = :l AND state = 'waiting' AND document_id IS NULL"
    " ORDER BY sent_at, received_at, id LIMIT :n"
)
# On whatsapp_messages_user: the files the person sent in the last hour, and in the 30 days messages
# are kept.
_THIS_HOUR = text(
    "SELECT count(*) FROM whatsapp_messages WHERE customer_id = :c AND user_id = :u"
    " AND kind IN ('image', 'document') AND received_at > now() - interval '1 hour'"
)
_KEPT = text(
    "SELECT count(*) FROM whatsapp_messages WHERE customer_id = :c AND user_id = :u"
    " AND kind IN ('image', 'document')"
)
_AWAIT_NOTICE = text(
    "UPDATE whatsapp_messages SET state = 'waiting', document_id = :d, body = :lang, tries = 0"
    " WHERE customer_id = :c AND id = :m"
)
# A copy the person sees that is locked, in Trash or not (a locked copy is never named on WhatsApp).
_LOCKED = text(
    "SELECT id FROM visible_documents WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
    " AND locked_at IS NOT NULL"
)
# The waiting notices of this connection whose files are in the batch of :d, each a file the person
# may still see on WhatsApp (seen, out of Trash, not locked), filtered before anything is read.
_BATCH_NOTICES = text(
    "WITH mine AS (SELECT b.batch_id FROM visible_documents b WHERE b.customer_id = :c AND b.id = :d)"
    " SELECT m.id, d.id AS document_id, d.status, d.display_name, d.folder_id FROM whatsapp_messages m"
    " JOIN visible_documents d ON d.customer_id = m.customer_id AND d.id = m.document_id"
    " WHERE m.customer_id = :c AND m.link_id = :l AND m.state = 'waiting'"
    " AND d.deleted_at IS NULL AND d.locked_at IS NULL"
    " AND (d.id = :d OR d.batch_id = (SELECT batch_id FROM mine))"
    " ORDER BY m.sent_at, m.received_at, m.id LIMIT :n"
)
# A notice past WhatsApp's 24 hours waits ("expired", keeping its document and language) for the
# person's next message, which puts it back in line; the window is the person's newest message's.
_LAST_SENT = text("SELECT max(sent_at) FROM whatsapp_messages WHERE customer_id = :c AND link_id = :l")
_PARK = text(
    "UPDATE whatsapp_messages SET state = 'expired' WHERE customer_id = :c AND id = :m AND state = 'waiting'"
)
_UNPARK = text(
    "UPDATE whatsapp_messages SET state = 'waiting', tries = 0"
    " WHERE customer_id = :c AND link_id = :l AND state = 'expired' AND document_id IS NOT NULL"
)
_RECENT = text("SELECT recent_folder_ids FROM whatsapp_links WHERE customer_id = :c AND id = :l")
_ADD_RECENT = text(
    "UPDATE whatsapp_links SET recent_folder_ids ="
    " (ARRAY[CAST(:f AS uuid)] || array_remove(recent_folder_ids, CAST(:f AS uuid)))[1:3]"
    " WHERE customer_id = :c AND id = :l"
)


@dataclass(frozen=True, slots=True)
class About:
    name: str
    path: str | None  # None: a folder above it is one the person can't see
    above: str | None  # the folder above, as the person sees the tree (None: the top)
    inside: int  # the folders inside it they see


@dataclass(frozen=True, slots=True)
class Sent:
    """A file the person sent: its message, kind, Meta's id for it, its file name (a document's)."""

    id: str
    kind: str
    media_id: str | None
    name: str | None
    sent_at: datetime


def level(
    conn: Connection, customer_id: str, at: str | None, skip: int, most: int, *, without: Sequence[str] = ()
) -> list[str]:
    """``without``: folders left out (the recent ones the list shows already)."""
    params = {"c": customer_id, "at": at, "skip": skip, "n": most, "without": list(without)}
    return [str(row[0]) for row in conn.execute(_LEVEL, params)]


def named(conn: Connection, customer_id: str, words: str, most: int) -> list[str]:
    return [str(row[0]) for row in conn.execute(_NAMED, {"c": customer_id, "words": words, "n": most})]


def about(conn: Connection, customer_id: str, folder_ids: Sequence[str]) -> dict[str, About]:
    """The folders of ``folder_ids`` the person sees; one they don't (or that is gone) is missing."""
    if not folder_ids:
        return {}
    rows = conn.execute(_ABOUT, {"c": customer_id, "ids": sorted(set(folder_ids)), "sep": PATH}).mappings()
    return {str(r["start"]): About(str(r["name"]), r["path"], r["above"], int(r["inside"])) for r in rows}


def run(conn: Connection, customer_id: str, link_id: str) -> tuple[list[Sent], bool]:
    """The files the person sent one after another, oldest first, waiting; and whether a message of
    another kind waits after them (the run is then over)."""
    rows = conn.execute(_RUN, {"c": customer_id, "l": link_id, "n": RUN_MOST}).all()
    files: list[Sent] = []
    for row in rows:
        if row[1] not in UPLOAD_KINDS:
            return files, True
        files.append(Sent(str(row[0]), str(row[1]), row[2], row[3], row[4]))
    return files, False


def files_this_hour(conn: Connection, customer_id: str, user_id: str) -> int:
    return int(conn.execute(_THIS_HOUR, {"c": customer_id, "u": user_id}).scalar_one())


def files_kept(conn: Connection, customer_id: str, user_id: str) -> int:
    """The files the person sent on WhatsApp in the 30 days messages are kept (the tip's "first")."""
    return int(conn.execute(_KEPT, {"c": customer_id, "u": user_id}).scalar_one())


def await_notice(conn: Connection, customer_id: str, message_id: str, document_id: str, lang: str) -> None:
    params = {"c": customer_id, "m": message_id, "d": document_id, "lang": lang}
    conn.execute(_AWAIT_NOTICE, params)


@dataclass(frozen=True, slots=True)
class Notice:
    """A waiting "ready" notice: its message, and its file as the person sees it."""

    message_id: str
    document_id: str
    status: str
    name: str
    folder_id: str | None


def locked(conn: Connection, customer_id: str, document_ids: Sequence[str]) -> set[str]:
    rows = conn.execute(_LOCKED, {"c": customer_id, "ids": sorted(set(document_ids))})
    return {str(row[0]) for row in rows}


def batch_notices(conn: Connection, customer_id: str, link_id: str, document_id: str) -> list[Notice]:
    """The waiting notices of ``document_id``'s batch on this connection (it among them, while the
    person may see it on WhatsApp), oldest first; at most RUN_MOST."""
    params = {"c": customer_id, "l": link_id, "d": document_id, "n": RUN_MOST}
    return [
        Notice(str(r[0]), str(r[1]), str(r[2]), str(r[3]), None if r[4] is None else str(r[4]))
        for r in conn.execute(_BATCH_NOTICES, params)
    ]


def last_sent(conn: Connection, customer_id: str, link_id: str) -> datetime | None:
    """When the person last sent anything on this connection, by WhatsApp's clock (the window)."""
    found: datetime | None = conn.execute(_LAST_SENT, {"c": customer_id, "l": link_id}).scalar()
    return found


def park_notice(conn: Connection, customer_id: str, message_id: str) -> None:
    conn.execute(_PARK, {"c": customer_id, "m": message_id})


def unpark_notices(conn: Connection, customer_id: str, link_id: str) -> None:
    conn.execute(_UNPARK, {"c": customer_id, "l": link_id})


def recent(conn: Connection, customer_id: str, link_id: str) -> list[str]:
    found = conn.execute(_RECENT, {"c": customer_id, "l": link_id}).scalar()
    return [str(each) for each in found or []]


def add_recent(conn: Connection, customer_id: str, link_id: str, folder_id: str) -> None:
    conn.execute(_ADD_RECENT, {"c": customer_id, "l": link_id, "f": folder_id})
