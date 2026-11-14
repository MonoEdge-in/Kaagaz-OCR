"""The channel's work (#603, PRD 7 section 15): claim connections with waiting messages, answer each
person's messages one at a time and in order, and sweep.

``Channel.handle`` takes one claimed connection (its lease, ``kaagaz_whatsapp_claim``, keeps every
other process off it) and goes through its waiting messages oldest first, by WhatsApp's time:

1. In the person's own transaction: the lease renewed for LEASE_SECONDS (only while this process
   still holds it: one that lost it stops at once), the message, and the checks every message runs
   again (the connection still live; the person enabled and their organisation active; their
   sign-in version unchanged; WhatsApp on for the organisation and for their role).
2. A message older than WhatsApp's 24 hours is expired, with no reply: the reply window is shut.
3. Refused: NOT_CONNECTED once to the connected number, then the connection ends (its number
   goes, audited). One that ended already gets no reply.
4. Otherwise the reply (W2: PLACEHOLDER, so the loop runs end to end; W3 on build the steps), then
   the message is done.

A reply goes outside any transaction, and only to the connection's own number. One that could not
be sent leaves the message waiting and the connection resting (the order is kept: nothing after it
goes first): RETRY_SECONDS, doubling each try to RETRY_MAX_SECONDS, until the message's 24 hours end
(then it expires). Meta refusing it for good (a 4xx but 401, 403 and 429) fails it at once; 401 and
403 (the token or app set up wrong) are logged at ERROR, ``whatsapp_auth``, and tried again. An
error while answering counts a try too (MAX_TRIES of them fail the message) and keeps the lease,
so nothing is sent again until it runs out. A failed message is logged at ERROR with its last code.
A crash between a reply and its "done" sends that one reply again once the lease runs out: the only
duplicate left.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from kaagaz.adapters.whatsapp import WhatsAppError
from kaagaz.api.secret_box import SecretBoxError
from kaagaz.channel.numbers import open_number
from kaagaz.db import whatsapp as wa
from kaagaz.db.engine import Database, unavailable
from kaagaz.db.whatsapp import Check, Claim, Linked, Waiting

_log = logging.getLogger("kaagaz.channel")

NOT_CONNECTED = (
    "This WhatsApp isn't connected to Kaagaz. If you use Kaagaz, connect it from Account \u203a WhatsApp."
)
PLACEHOLDER = "Kaagaz got your message. Asking about your files on WhatsApp is coming soon."
LEASE_SECONDS = 240  # an answer may take ANSWER_STREAM_MAX_SECONDS (180)
RETRY_SECONDS = 30
RETRY_MAX_SECONDS = 600
MAX_TRIES = 5  # errors while answering one message
UNTIL_IT_EXPIRES = 32767  # a reply not sent is tried until REPLY_WINDOW ends (smallint's most)
REPLY_WINDOW = timedelta(hours=24)
_AUTH = frozenset({401, 403})


class Sender(Protocol):
    def send_text(self, to: str, body: str) -> None: ...


@dataclass(frozen=True, slots=True)
class Channel:
    db: Database  # in the app's role (db_app_role), never the workers'
    sender: Sender
    number_key: str
    clock: Callable[[], datetime]

    def claim(self, limit: int) -> list[Claim]:
        """Up to ``limit`` connections, leased; BackendUnavailable when the database is lost."""
        with unavailable(), self.db.system() as conn:
            return wa.claim(conn, limit, LEASE_SECONDS)

    def sweep(self) -> int:
        with unavailable(), self.db.system() as conn:
            return wa.sweep(conn)

    def handle(self, claim: Claim) -> None:
        """Every waiting message of one claimed connection, in order; then its lease is freed, or kept
        a while for a reply to try again. An error keeps the lease (see the module notes)."""
        linked, until = claim.linked, claim.until
        wait = 0
        while not wait:
            with self.db.user(linked.customer_id, linked.user_id) as conn:
                renewed = wa.renew(conn, linked, until, LEASE_SECONDS)
                if renewed is None:  # it ran out and another process holds it now
                    return
                until = renewed
                message = wa.next_message(conn, linked)
                check = None if message is None else wa.check(conn, linked)
            if message is None or check is None:
                break
            try:
                wait = self._answer(linked, message, check)
            except Exception as error:
                self._not_sent(linked, message, type(error).__name__, most=MAX_TRIES)
                raise
        with self.db.user(linked.customer_id, linked.user_id) as conn:
            wa.release(conn, linked, until, wait)

    def _answer(self, linked: Linked, message: Waiting, check: Check) -> int:
        """One message handled: 0, or the seconds to rest before its reply is tried again."""
        if message.sent_at < self.clock() - REPLY_WINDOW:
            return self._finish(linked, message, "expired", replied=False)
        if check.sealed is None:  # ended already: nothing goes to a number no longer connected
            return self._finish(linked, message, "done", replied=False)
        try:
            number = open_number(self.number_key, check.sealed, linked.customer_id, linked.user_id)
        except SecretBoxError:  # WHATSAPP_NUMBER_KEY changed: no number to answer
            sealed = {"code": "number_sealed", "customer_id": linked.customer_id}
            _log.error("whatsapp message failed", extra=sealed)
            return self._finish(linked, message, "failed", replied=False)
        if check.refused is not None:
            sent = self._send(number, NOT_CONNECTED) is None  # once, as it can be; it ends regardless
            with self.db.user(linked.customer_id, linked.user_id) as conn:
                wa.end(conn, linked, check.refused)
                wa.finish(conn, linked, message.id, "done", replied=sent)
            return 0
        error = self._send(number, PLACEHOLDER)
        if error is None:
            return self._finish(linked, message, "done", replied=True)
        status = error.status or 0
        for_good = 400 <= status < 500 and status not in _AUTH and status != 429
        code = f"{error.code}:{status or '-'}"
        tries = self._not_sent(linked, message, code, most=UNTIL_IT_EXPIRES, fail=for_good)
        return 0 if tries is None else min(RETRY_SECONDS * 2 ** (tries - 1), RETRY_MAX_SECONDS)

    def _send(self, number: str, body: str) -> WhatsAppError | None:
        try:
            self.sender.send_text(number, body)
        except WhatsAppError as error:
            if error.status in _AUTH:
                _log.error("whatsapp refused the access token", extra={"code": "whatsapp_auth"})
            else:
                _log.warning("whatsapp reply not sent", extra={"code": f"{error.code}:{error.status or '-'}"})
            return error
        return None

    def _not_sent(
        self, linked: Linked, message: Waiting, code: str, *, most: int, fail: bool = False
    ) -> int | None:
        """One more try counted: the count, or None once the message failed for good (logged)."""
        with self.db.user(linked.customer_id, linked.user_id) as conn:
            failed, tries = wa.not_sent(conn, linked, message.id, most=most, fail=fail)
        if not failed:
            return tries
        _log.error("whatsapp message failed", extra={"code": code, "customer_id": linked.customer_id})
        return None

    def _finish(self, linked: Linked, message: Waiting, state: str, *, replied: bool) -> int:
        with self.db.user(linked.customer_id, linked.user_id) as conn:
            wa.finish(conn, linked, message.id, state, replied=replied)
        return 0
