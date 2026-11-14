"""The channel's work (#603, PRD 7 section 15): claim connections with waiting messages, answer each
person's messages one at a time and in order, and sweep.

``Channel.handle`` takes one claimed connection (its lease, ``kaagaz_whatsapp_claim``, keeps every
other process off it) and goes through its waiting messages oldest first, by WhatsApp's time:

1. In the person's own transaction: the message and the checks every message runs again (the
   connection still live; the person enabled and their organisation active; their sign-in
   version unchanged; WhatsApp on for the organisation and for their role).
2. A message older than WhatsApp's 24 hours is expired, with no reply: the reply window is shut.
3. Refused: NOT_CONNECTED once to the connected number, then the connection ends (its number
   goes, audited). One that ended already gets no reply.
4. Otherwise the reply (W2: PLACEHOLDER, so the loop runs end to end; W3 on build the steps), then
   the message is done.

A reply goes outside any transaction, and only to the connection's own number. One that could not
be sent leaves the message waiting and the connection resting RETRY_SECONDS (the order is kept:
nothing after it goes first); after MAX_TRIES it is failed. A crash between a reply and its "done"
sends that one reply again when the lease (LEASE_SECONDS) runs out: the only duplicate left.
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
from kaagaz.db.whatsapp import Check, Linked, Waiting

_log = logging.getLogger("kaagaz.channel")

NOT_CONNECTED = (
    "This WhatsApp isn't connected to Kaagaz. If you use Kaagaz, connect it from Account \u203a WhatsApp."
)
PLACEHOLDER = "Kaagaz got your message. Asking about your files on WhatsApp is coming soon."
LEASE_SECONDS = 240  # an answer may take ANSWER_STREAM_MAX_SECONDS (180)
RETRY_SECONDS = 30
MAX_TRIES = 5
REPLY_WINDOW = timedelta(hours=24)


class Sender(Protocol):
    def send_text(self, to: str, body: str) -> None: ...


@dataclass(frozen=True, slots=True)
class Channel:
    db: Database  # in the app's role (db_app_role), never the workers'
    sender: Sender
    number_key: str
    clock: Callable[[], datetime]

    def claim(self, limit: int) -> list[Linked]:
        """Up to ``limit`` connections, leased; BackendUnavailable when the database is lost."""
        with unavailable(), self.db.system() as conn:
            return wa.claim(conn, limit, LEASE_SECONDS)

    def sweep(self) -> int:
        with unavailable(), self.db.system() as conn:
            return wa.sweep(conn)

    def handle(self, linked: Linked) -> None:
        """Every waiting message of one claimed connection, in order; then its lease is freed."""
        wait = 0
        try:
            while (found := self._next(linked)) is not None:
                if not self._answer(linked, *found):
                    wait = RETRY_SECONDS
                    return
        finally:
            with self.db.user(linked.customer_id, linked.user_id) as conn:
                wa.release(conn, linked, wait)

    def _next(self, linked: Linked) -> tuple[Waiting, Check] | None:
        with self.db.user(linked.customer_id, linked.user_id) as conn:
            message = wa.next_message(conn, linked)
            return None if message is None else (message, wa.check(conn, linked))

    def _answer(self, linked: Linked, message: Waiting, check: Check) -> bool:
        """One message handled: True, or False when its reply could not be sent yet."""
        if message.sent_at < self.clock() - REPLY_WINDOW:
            return self._finish(linked, message, "expired", replied=False)
        if check.sealed is None:  # ended already: nothing goes to a number no longer connected
            return self._finish(linked, message, "done", replied=False)
        try:
            number = open_number(self.number_key, check.sealed, linked.customer_id, linked.user_id)
        except SecretBoxError:  # WHATSAPP_NUMBER_KEY changed: no number to answer
            _log.error(
                "whatsapp number did not open",
                extra={"code": "number_sealed", "customer_id": linked.customer_id},
            )
            return self._finish(linked, message, "failed", replied=False)
        if check.refused is not None:
            sent = self._send(number, NOT_CONNECTED)  # once, as it can be; the connection ends regardless
            with self.db.user(linked.customer_id, linked.user_id) as conn:
                wa.end(conn, linked, check.refused)
                wa.finish(conn, linked, message.id, "done", replied=sent)
            return True
        if self._send(number, PLACEHOLDER):
            return self._finish(linked, message, "done", replied=True)
        with self.db.user(linked.customer_id, linked.user_id) as conn:
            return wa.not_sent(conn, linked, message.id, MAX_TRIES)

    def _send(self, number: str, body: str) -> bool:
        try:
            self.sender.send_text(number, body)
        except WhatsAppError as error:
            _log.warning("whatsapp reply not sent", extra={"code": f"{error.code}:{error.status or '-'}"})
            return False
        return True

    def _finish(self, linked: Linked, message: Waiting, state: str, *, replied: bool) -> bool:
        with self.db.user(linked.customer_id, linked.user_id) as conn:
            wa.finish(conn, linked, message.id, state, replied=replied)
        return True
