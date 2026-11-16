"""Kaagaz on WhatsApp's SQL (#603, PRD 7 section 15; migration 0097).

* With no organisation (``db.system``), the owner-rights functions only: ``lookup`` (a number's live
  connection, ids only), ``claim`` (the channel's queue: ids and the lease) and ``sweep``.
* In the person's own transaction (``db.user``): ``add_message`` (once per WhatsApp message id),
  ``next_message`` (their oldest waiting one, by WhatsApp's time), ``check`` (what every message
  checks again), ``finish``, ``not_sent``, ``end`` (``kaagaz_whatsapp_end_link``: the app has no
  right to end a connection itself), and ``renew`` and ``release``, which touch the lease only
  while it is still the one this process holds (another may have taken it once it ran out).

Each is one statement on an index: O(log n).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, text

from kaagaz.channel.inbound import Inbound


@dataclass(frozen=True, slots=True)
class Linked:
    """A live connection's ids: who a message is from, or whose messages a claim took."""

    customer_id: str
    user_id: str
    link_id: str


@dataclass(frozen=True, slots=True)
class Claim:
    """A connection a claim took, and the end of the lease this process holds on it."""

    linked: Linked
    until: datetime


@dataclass(frozen=True, slots=True)
class Waiting:
    id: str
    kind: str
    body: str | None
    sent_at: datetime


@dataclass(frozen=True, slots=True)
class Check:
    """``sealed``: the connection's number while it is live, else None. ``refused``: why it may no
    longer be used (an end reason of 0097), or None."""

    sealed: str | None
    refused: str | None


_LOOKUP = text("SELECT customer_id, user_id, link_id FROM kaagaz_whatsapp_lookup(:h)")
_CLAIM = text("SELECT customer_id, user_id, link_id, busy_until FROM kaagaz_whatsapp_claim(:n, :lease)")
_SWEEP = text("SELECT kaagaz_whatsapp_sweep()")
_ADD = text(
    "INSERT INTO whatsapp_messages (customer_id, link_id, user_id, wamid, kind, body, media_id, forwarded,"
    " sent_at) VALUES (:c, :l, :u, :wamid, :kind, :body, :media, :forwarded, :sent)"
    " ON CONFLICT (customer_id, wamid) DO NOTHING RETURNING id"
)
# On whatsapp_messages_waiting: WhatsApp's time first (its deliveries come in any order), then ours.
_NEXT = text(
    "SELECT id, kind, body, sent_at FROM whatsapp_messages"
    " WHERE customer_id = :c AND link_id = :l AND state = 'waiting'"
    " ORDER BY sent_at, received_at, id LIMIT 1"
)
# The checks every message runs (PRD 7 section 15), each one end reason, as the sweep's (0097).
_CHECK = text(
    "SELECT l.phone_sealed,"
    " CASE WHEN l.ended_at IS NOT NULL THEN l.end_reason"
    "  WHEN u.disabled OR c.status <> 'active' THEN 'account'"
    "  WHEN u.session_version <> l.session_version THEN 'session'"
    "  WHEN NOT coalesce(s.enabled, false) OR NOT u.role = ANY (s.roles)"
    "   OR (u.management AND NOT s.management) THEN 'not_allowed' END"
    " FROM whatsapp_links l"
    " JOIN users u ON u.customer_id = l.customer_id AND u.id = l.user_id"
    " JOIN customers c ON c.id = l.customer_id"
    " LEFT JOIN whatsapp_org_settings s ON s.customer_id = l.customer_id"
    " WHERE l.customer_id = :c AND l.id = :l"
)
_FINISH = text(
    "UPDATE whatsapp_messages SET state = :state, body = NULL,"
    " reply_sent_at = CASE WHEN :replied THEN now() END"
    " WHERE customer_id = :c AND id = :m AND state = 'waiting'"
)
_USED = text("UPDATE whatsapp_links SET last_used_at = now() WHERE customer_id = :c AND id = :l")
_NOT_SENT = text(
    "UPDATE whatsapp_messages SET tries = tries + 1,"
    " state = CASE WHEN :fail OR tries + 1 >= :most THEN 'failed' ELSE state END,"
    " body = CASE WHEN :fail OR tries + 1 >= :most THEN NULL ELSE body END"
    " WHERE customer_id = :c AND id = :m AND state = 'waiting' RETURNING state, tries"
)
_END = text("SELECT kaagaz_whatsapp_end_link(:l, :reason)")
# Only while the lease is the one this process holds (its exact end, as the claim or the last renewal
# set it): once it ran out another process may hold the connection, and its lease is left alone.
_RENEW = text(
    "UPDATE whatsapp_links SET busy_until = now() + make_interval(secs => :lease)"
    " WHERE customer_id = :c AND id = :l AND busy_until = :until RETURNING busy_until"
)
_RELEASE = text(
    "UPDATE whatsapp_links SET busy_until = CASE WHEN :wait > 0 THEN now() + make_interval(secs => :wait) END"
    " WHERE customer_id = :c AND id = :l AND busy_until = :until"
)


def lookup(conn: Connection, phone_hash: bytes) -> Linked | None:
    row = conn.execute(_LOOKUP, {"h": phone_hash}).first()
    return None if row is None else Linked(str(row[0]), str(row[1]), str(row[2]))


def claim(conn: Connection, limit: int, lease_seconds: int) -> list[Claim]:
    rows = conn.execute(_CLAIM, {"n": limit, "lease": lease_seconds}).all()
    return [Claim(Linked(str(row[0]), str(row[1]), str(row[2])), row[3]) for row in rows]


def sweep(conn: Connection) -> int:
    return int(conn.execute(_SWEEP).scalar_one())


def add_message(conn: Connection, linked: Linked, message: Inbound) -> bool:
    """Store ``message`` once: False when its WhatsApp id was stored already."""
    params = {
        "c": linked.customer_id,
        "l": linked.link_id,
        "u": linked.user_id,
        "wamid": message.wamid,
        "kind": message.kind,
        "body": message.body,
        "media": message.media_id,
        "forwarded": message.forwarded,
        "sent": message.sent_at,
    }
    return conn.execute(_ADD, params).first() is not None


def next_message(conn: Connection, linked: Linked) -> Waiting | None:
    row = conn.execute(_NEXT, {"c": linked.customer_id, "l": linked.link_id}).first()
    return None if row is None else Waiting(str(row[0]), str(row[1]), row[2], row[3])


def check(conn: Connection, linked: Linked) -> Check:
    row = conn.execute(_CHECK, {"c": linked.customer_id, "l": linked.link_id}).first()
    if row is None:  # not this person's organisation's: nothing to answer
        return Check(None, "account")
    return Check(row[0], row[1])


def finish(conn: Connection, linked: Linked, message_id: str, state: str, *, replied: bool) -> None:
    """The message is done, expired or failed: its text goes; ``replied`` stamps its reply."""
    params = {"c": linked.customer_id, "m": message_id, "state": state, "replied": replied}
    conn.execute(_FINISH, params)
    if state == "done":
        conn.execute(_USED, {"c": linked.customer_id, "l": linked.link_id})


def not_sent(
    conn: Connection, linked: Linked, message_id: str, *, most: int, fail: bool = False
) -> tuple[bool, int]:
    """Its reply could not be sent, or its handling failed: one more try counted, and failed for good
    at ``most`` tries or at once with ``fail``. (failed for good, tries); (True, 0) for a message no
    longer waiting."""
    params = {"c": linked.customer_id, "m": message_id, "most": most, "fail": fail}
    row = conn.execute(_NOT_SENT, params).first()
    return (True, 0) if row is None else (row[0] == "failed", int(row[1]))


def end(conn: Connection, linked: Linked, reason: str) -> bool:
    """End the connection, its number gone at once, audited (``kaagaz_whatsapp_end_link``)."""
    return bool(conn.execute(_END, {"l": uuid.UUID(linked.link_id), "reason": reason}).scalar_one())


def renew(conn: Connection, linked: Linked, until: datetime, lease_seconds: int) -> datetime | None:
    """The lease ending ``until`` lengthened to ``lease_seconds`` from now: its new end, or None when
    this process no longer holds it."""
    params = {"c": linked.customer_id, "l": linked.link_id, "until": until, "lease": lease_seconds}
    row = conn.execute(_RENEW, params).first()
    return None if row is None else row[0]


def release(conn: Connection, linked: Linked, until: datetime, wait_seconds: int) -> None:
    """Free the lease ending ``until``, or keep it ``wait_seconds`` from now (a reply to try again
    later); nothing when this process no longer holds it."""
    params = {"c": linked.customer_id, "l": linked.link_id, "until": until, "wait": wait_seconds}
    conn.execute(_RELEASE, params)
