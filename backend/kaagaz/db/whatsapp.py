"""Kaagaz on WhatsApp's SQL (#603, PRD 7 section 15; migration 0097).

* With no organisation (``db.system``), the owner-rights functions only: ``lookup`` (a number's live
  connection, ids only), ``claim`` (the channel's queue: ids and the lease) and ``sweep``; and (W3)
  ``end_number`` (WhatsApp says the owner changed), ``tell_unknown``, ``block``, ``may_send`` and
  ``phone_code`` (the second way's "Link <code>").
* In the person's own transaction (``db.user``): ``add_message`` (once per WhatsApp message id),
  ``next_message`` (their oldest waiting one, by WhatsApp's time), ``check`` (what every message
  checks again), ``finish``, ``not_sent``, ``end`` (``kaagaz_whatsapp_end_link``: the app has no
  right to end a connection itself), and ``renew`` and ``release``, which touch the lease only
  while it is still the one this process holds (another may have taken it once it ran out).
* Connecting (W3), in the person's own transaction: ``refused`` (why they may not connect now, as
  ``check`` says it for a connection), ``lock_codes`` (one request for codes at a time a person),
  ``codes_this_hour``, ``add_code``, ``newest_code`` (only the newest counts: a newer one replaces
  the older ones), ``code_tried``, ``drop_codes`` (a code used: all the person's go, numbers and
  all), ``mine`` (their live connection), ``add_link`` (``NumberTaken`` when the number is live
  elsewhere) and ``google_proofs_this_hour``.
* The Admin's (W3), in the Admin's transaction: ``org_settings``, ``save_settings`` and
  ``connections`` (the organisation's live connections: who, the last 4 digits, when).

Each is one statement on an index: O(log n).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, RowMapping, text
from sqlalchemy.exc import IntegrityError

from kaagaz.channel.inbound import Inbound
from kaagaz.db.sqlstate import UNIQUE_VIOLATION, sqlstate

CODE_MINUTES = 10


class NumberTaken(Exception):
    """The number is connected to another account (in any organisation)."""


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
class Code:
    """A person's newest connect code: its number (hash and sealed), how it travels, and what is left."""

    id: str
    phone_hash: bytes
    sealed: str | None  # None once expired (the sweep)
    code_hash: bytes
    via: str
    tries: int
    used: bool
    live: bool  # not expired
    confirmed: bool  # phone only: the number sent it


@dataclass(frozen=True, slots=True)
class Mine:
    """A person's live connection."""

    id: str
    last4: str
    sealed: str
    linked_at: datetime
    last_used_at: datetime | None


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
# WhatsApp on for the person's organisation (u): the Admin's switch and the super admin's two (no row
# is on, 0038 and 0039); and their role allowed (Management only with its extra tick). As 0097's sweep.
_OFF = (
    "NOT coalesce(s.enabled, false)"
    " OR NOT coalesce((SELECT f.enabled FROM feature_switches f WHERE f.feature = 'whatsapp'), true)"
    " OR NOT coalesce((SELECT o.enabled FROM org_feature_switches o"
    "  WHERE o.customer_id = u.customer_id AND o.feature = 'whatsapp'), true)"
)
_ROLE_REFUSED = "NOT u.role = ANY (s.roles) OR (u.management AND NOT s.management)"
# The checks every message runs (PRD 7 section 15), each one end reason, as the sweep's (0097).
_CHECK = text(
    "SELECT l.phone_sealed,"  # noqa: S608 - fixed fragments only
    " CASE WHEN l.ended_at IS NOT NULL THEN l.end_reason"
    "  WHEN u.disabled OR c.status <> 'active' THEN 'account'"
    "  WHEN u.session_version <> l.session_version THEN 'session'"
    f"  WHEN {_OFF} OR {_ROLE_REFUSED} THEN 'not_allowed' END"
    " FROM whatsapp_links l"
    " JOIN users u ON u.customer_id = l.customer_id AND u.id = l.user_id"
    " JOIN customers c ON c.id = l.customer_id"
    " LEFT JOIN whatsapp_org_settings s ON s.customer_id = l.customer_id"
    " WHERE l.customer_id = :c AND l.id = :l"
)
# Why a signed-in person may not connect now: off (for the organisation), not_allowed (their role).
_REFUSED = text(
    f"SELECT CASE WHEN {_OFF} THEN 'off' WHEN {_ROLE_REFUSED} THEN 'not_allowed' END"  # noqa: S608 - fixed fragments only
    " FROM users u LEFT JOIN whatsapp_org_settings s ON s.customer_id = u.customer_id"
    " WHERE u.customer_id = :c AND u.id = :u"
)
_END_NUMBER = text("SELECT kaagaz_whatsapp_end_number(:h, :reason, :before)")
_TELL = text("SELECT kaagaz_whatsapp_tell_unknown(:h)")
_BLOCK = text("SELECT kaagaz_whatsapp_block(:h)")
_MAY_SEND = text("SELECT kaagaz_whatsapp_may_send(:h)")
_PHONE_CODE = text("SELECT kaagaz_whatsapp_phone_code(:h, :code)")
# One request for codes at a time a person, to the commit: two at once count each other.
_LOCK_CODES = text("SELECT pg_advisory_xact_lock(hashtextextended('whatsapp codes ' || :u, 0))")
# The codes the bot was asked to send (a phone code is only ever made from one).
_THIS_HOUR = text(
    "SELECT count(*) FROM whatsapp_link_codes"
    " WHERE customer_id = :c AND user_id = :u AND via = 'sent' AND created_at > now() - interval '1 hour'"
)
_ADD_CODE = text(
    "INSERT INTO whatsapp_link_codes (customer_id, user_id, phone_hash, phone_sealed, code_hash, via,"
    " created_at, expires_at) VALUES (:c, :u, :h, :sealed, :code, :via, clock_timestamp(),"
    " clock_timestamp() + make_interval(mins => :minutes))"
)
# On whatsapp_link_codes_person: the newest only (a newer code replaces the older ones).
_NEWEST_CODE = text(
    "SELECT id, phone_hash, phone_sealed, code_hash, via, tries, used_at IS NOT NULL,"
    " expires_at > now(), confirmed_at IS NOT NULL FROM whatsapp_link_codes"
    " WHERE customer_id = :c AND user_id = :u ORDER BY created_at DESC, id LIMIT 1 FOR UPDATE"
)
_CODE_TRIED = text("UPDATE whatsapp_link_codes SET tries = tries + 1 WHERE customer_id = :c AND id = :id")
_DROP_CODES = text("DELETE FROM whatsapp_link_codes WHERE customer_id = :c AND user_id = :u")
# The Google proofs a person started in the last hour (their audit rows, on audit_log_actor).
_GOOGLE_PROOFS = text(
    "SELECT count(*) FROM audit_log WHERE customer_id = :c AND actor_user_id = :u"
    " AND action = 'whatsapp.google_proof' AND created_at > now() - interval '1 hour'"
)
_MINE = text(
    "SELECT id, phone_last4, phone_sealed, linked_at, last_used_at FROM whatsapp_links"
    " WHERE customer_id = :c AND user_id = :u AND ended_at IS NULL"
)
_ADD_LINK = text(
    "INSERT INTO whatsapp_links (customer_id, user_id, phone_hash, phone_sealed, phone_last4,"
    " session_version) VALUES (:c, :u, :h, :sealed, :last4, :version) RETURNING id"
)
_CONNECTIONS = text(
    "SELECT user_id, phone_last4, linked_at, last_used_at FROM whatsapp_links"
    " WHERE customer_id = :c AND ended_at IS NULL ORDER BY linked_at"
)
_ORG_SETTINGS = text(
    "SELECT coalesce(s.enabled, false) AS enabled,"
    " coalesce(s.roles, ARRAY['member', 'reviewer']::text[]) AS roles,"
    " coalesce(s.management, false) AS management, coalesce(s.voice, true) AS voice,"
    " coalesce(s.uploads, false) AS uploads,"
    " coalesce((SELECT f.enabled FROM feature_switches f WHERE f.feature = 'whatsapp'), true)"
    " AND coalesce((SELECT o.enabled FROM org_feature_switches o"
    "  WHERE o.customer_id = c.id AND o.feature = 'whatsapp'), true) AS available"
    " FROM customers c LEFT JOIN whatsapp_org_settings s ON s.customer_id = c.id WHERE c.id = :c"
)
# ``confirmed_by``: the Admin who just ticked "our data rules allow WhatsApp" (turning it on); None
# keeps the last confirmation.
_SAVE_SETTINGS = text(
    "INSERT INTO whatsapp_org_settings AS s (customer_id, enabled, roles, management, voice, uploads,"
    " rules_confirmed_at, rules_confirmed_by)"
    " VALUES (:c, :enabled, :roles, :management, :voice, :uploads,"
    " CASE WHEN CAST(:by AS uuid) IS NOT NULL THEN now() END, CAST(:by AS uuid))"
    " ON CONFLICT (customer_id) DO UPDATE"
    " SET enabled = EXCLUDED.enabled, roles = EXCLUDED.roles, management = EXCLUDED.management,"
    " voice = EXCLUDED.voice, uploads = EXCLUDED.uploads,"
    " rules_confirmed_at = coalesce(EXCLUDED.rules_confirmed_at, s.rules_confirmed_at),"
    " rules_confirmed_by = coalesce(EXCLUDED.rules_confirmed_by, s.rules_confirmed_by)"
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


# --- with no organisation (W3) ---------------------------------------------------------------


def end_number(conn: Connection, phone_hash: bytes, reason: str, before: datetime | None = None) -> int:
    """Every live connection of the number ends (``number_changed`` or ``replaced``), audited; with
    ``before``, only those connected before it. How many."""
    params = {"h": phone_hash, "reason": reason, "before": before}
    return int(conn.execute(_END_NUMBER, params).scalar_one())


def tell_unknown(conn: Connection, phone_hash: bytes) -> bool:
    """Whether a number with no connection may be told so now (once a day; never once blocked)."""
    return bool(conn.execute(_TELL, {"h": phone_hash}).scalar_one())


def block(conn: Connection, phone_hash: bytes) -> None:
    conn.execute(_BLOCK, {"h": phone_hash})


def may_send(conn: Connection, phone_hash: bytes) -> str:
    """``ok``, ``blocked`` or ``number_limit``; the number stays locked to this transaction's end."""
    return str(conn.execute(_MAY_SEND, {"h": phone_hash}).scalar_one())


def phone_code(conn: Connection, phone_hash: bytes, code_hash: bytes) -> bool:
    """The number sent "Link <code>": True when its newest live phone code was this one."""
    return bool(conn.execute(_PHONE_CODE, {"h": phone_hash, "code": code_hash}).scalar_one())


# --- connecting, in the person's own transaction (W3) ------------------------------------------


def refused(conn: Connection, customer_id: str, user_id: str) -> str | None:
    """``off`` (WhatsApp off for the organisation), ``not_allowed`` (their role), or None."""
    row = conn.execute(_REFUSED, {"c": customer_id, "u": user_id}).first()
    return "off" if row is None else row[0]


def lock_codes(conn: Connection, user_id: str) -> None:
    conn.execute(_LOCK_CODES, {"u": user_id})


def codes_this_hour(conn: Connection, customer_id: str, user_id: str) -> int:
    return int(conn.execute(_THIS_HOUR, {"c": customer_id, "u": user_id}).scalar_one())


def add_code(
    conn: Connection,
    customer_id: str,
    user_id: str,
    phone_hash: bytes,
    sealed: str,
    code_hash: bytes,
    via: str,
) -> None:
    """A new code (``sent`` or ``phone``), working CODE_MINUTES from now."""
    params = {
        "c": customer_id,
        "u": user_id,
        "h": phone_hash,
        "sealed": sealed,
        "code": code_hash,
        "via": via,
        "minutes": CODE_MINUTES,
    }
    conn.execute(_ADD_CODE, params)


def newest_code(conn: Connection, customer_id: str, user_id: str) -> Code | None:
    """The person's newest code, locked to this transaction's end, or None."""
    row = conn.execute(_NEWEST_CODE, {"c": customer_id, "u": user_id}).first()
    if row is None:
        return None
    return Code(
        id=str(row[0]),
        phone_hash=bytes(row[1]),
        sealed=row[2],
        code_hash=bytes(row[3]),
        via=str(row[4]),
        tries=int(row[5]),
        used=bool(row[6]),
        live=bool(row[7]),
        confirmed=bool(row[8]),
    )


def code_tried(conn: Connection, customer_id: str, code_id: str) -> None:
    conn.execute(_CODE_TRIED, {"c": customer_id, "id": code_id})


def drop_codes(conn: Connection, customer_id: str, user_id: str) -> None:
    """A code was used: every code of the person goes, with the numbers sealed in them."""
    conn.execute(_DROP_CODES, {"c": customer_id, "u": user_id})


def google_proofs_this_hour(conn: Connection, customer_id: str, user_id: str) -> int:
    return int(conn.execute(_GOOGLE_PROOFS, {"c": customer_id, "u": user_id}).scalar_one())


def mine(conn: Connection, customer_id: str, user_id: str) -> Mine | None:
    row = conn.execute(_MINE, {"c": customer_id, "u": user_id}).first()
    return None if row is None else Mine(str(row[0]), str(row[1]), str(row[2]), row[3], row[4])


def add_link(
    conn: Connection, customer_id: str, user_id: str, phone_hash: bytes, sealed: str, last4: str, version: int
) -> str:
    """A live connection, in a savepoint: its id, or NumberTaken when the number is live elsewhere."""
    params = {
        "c": customer_id,
        "u": user_id,
        "h": phone_hash,
        "sealed": sealed,
        "last4": last4,
        "version": version,
    }
    try:
        with conn.begin_nested():
            return str(conn.execute(_ADD_LINK, params).scalar_one())
    except IntegrityError as error:
        if sqlstate(error) == UNIQUE_VIOLATION and "whatsapp_links_live_number" in str(error.orig):
            raise NumberTaken from None
        raise


# --- the Admin's (W3) --------------------------------------------------------------------------


def connections(conn: Connection, customer_id: str) -> Sequence[RowMapping]:
    """The organisation's live connections: user_id, phone_last4, linked_at, last_used_at."""
    return conn.execute(_CONNECTIONS, {"c": customer_id}).mappings().all()


def org_settings(conn: Connection, customer_id: str) -> RowMapping | None:
    """The Admin's switches (the defaults with no row) and ``available``: the super admin's switches."""
    return conn.execute(_ORG_SETTINGS, {"c": customer_id}).mappings().first()


def save_settings(
    conn: Connection,
    customer_id: str,
    *,
    enabled: bool,
    roles: Sequence[str],
    management: bool,
    voice: bool,
    uploads: bool,
    confirmed_by: str | None = None,
) -> None:
    params = {
        "by": confirmed_by,
        "c": customer_id,
        "enabled": enabled,
        "roles": list(roles),
        "management": management,
        "voice": voice,
        "uploads": uploads,
    }
    conn.execute(_SAVE_SETTINGS, params)
