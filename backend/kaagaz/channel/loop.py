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
4. (W3) STOP, Yes to "Disconnect?", or [Not me] on this connection's own welcome: the connection
   ends first (audited), then DISCONNECTED goes once (never tried again: the number is no longer
   connected). "Disconnect" (typed, or Menu's in W4) asks first, with [Disconnect] [Keep it].
5. Otherwise the chat's reply (W4, ``chat.Chat``: the menu, Ask, "Did you mean", Get a file), one
   message, then the message is done. An error while making it counts a try (below). A reply is
   made once (``made``, by the connection and message): a try to send it again sends the same one,
   with no second search, model call or audit row. Just before each try the connection is checked
   again (refused: as in 3) and so is every document the reply names (``Chat.recheck``: one hidden,
   locked or deleted since gives "couldn't find" instead). Once it was sent, a preview card's
   ``whatsapp.file_link`` is audited with the message's "done", and Ask's memory is written.
6. (W5) While the reply is made, WhatsApp shows the person's message read with its typing
   indicator; a reply not made within SLOW_SECONDS sends LOOKING first ("Looking through your
   files…", always before the reply). Files (a photo or a document) the person sends one after
   another are answered together: their first waits until QUIET_SECONDS have passed since the
   newest of them by WhatsApp's time (or another kind of message came after them), resting the
   connection meanwhile; the reply then finishes them all (``Outgoing.finished``).
7. (W5) A saved file's message waits again for its "ready" notice (``document_id``), behind every
   message the person sent (``wa.next_message``): ``Chat.notice`` while the file is being read
   rests the connection NOTICE_SECONDS (no try counted; it expires with its 24 hours, unsent);
   once read it goes as any reply, checked again just before; a file hidden since sends nothing.

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
import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from kaagaz.adapters.whatsapp import NOT_ME, WhatsAppError
from kaagaz.api.secret_box import SecretBoxError
from kaagaz.channel.numbers import open_number
from kaagaz.channel.outgoing import Outgoing
from kaagaz.channel.texts import LANGUAGES, Language, language, say
from kaagaz.db import audit
from kaagaz.db import whatsapp as wa
from kaagaz.db import whatsapp_upload as wa_up
from kaagaz.db.engine import Database, unavailable
from kaagaz.db.whatsapp import Check, Claim, Linked, Waiting

_log = logging.getLogger("kaagaz.channel")

NOT_CONNECTED = (
    "This WhatsApp isn't connected to Kaagaz. If you use Kaagaz, connect it from Account \u203a WhatsApp."
)
DISCONNECTED = (
    "Disconnected. This chat can no longer reach your Kaagaz files. Messages already here stay on this"
    " phone. Delete them if others use it."
)
ASK_DISCONNECT = "Disconnect this WhatsApp from Kaagaz? This chat will no longer reach your Kaagaz files."
STILL_CONNECTED = "You're still connected."
STOP = "stop"
DISCONNECT = "disconnect"
DISCONNECT_YES = "disconnect_yes"  # the button ids
KEEP = "keep"
DISCONNECT_BUTTONS = ((DISCONNECT_YES, "Disconnect"), (KEEP, "Keep it"))
LEASE_SECONDS = 240  # an answer may take ANSWER_STREAM_MAX_SECONDS (180)
RETRY_SECONDS = 30
RETRY_MAX_SECONDS = 600
MAX_TRIES = 5  # errors while answering one message
UNTIL_IT_EXPIRES = 32767  # a reply not sent is tried until REPLY_WINDOW ends (smallint's most)
REPLY_WINDOW = timedelta(hours=24)
_AUTH = frozenset({401, 403})
MAX_MADE = 1000  # replies held to send again (each one message's, a few KB)
SLOW_SECONDS = 8.0  # W5: a reply not made by then sends "Looking through your files…" first
QUIET_SECONDS = 15  # W5: files sent one after another are answered once none came for this long
# W5: how often a saved file is looked at while Kaagaz reads it. shortcut: the rest holds the whole
# connection, so a message sent meanwhile waits up to this long; a notice time of its own on
# whatsapp_messages (a migration) would let the claim skip it.
NOTICE_SECONDS = 3


class Sender(Protocol):
    def send_text(self, to: str, body: str) -> None: ...

    def send_buttons(self, to: str, body: str, buttons: Sequence[tuple[str, str]]) -> None: ...

    def send_list(self, to: str, body: str, button: str, rows: Sequence[tuple[str, str, str]]) -> None: ...

    def typing(self, message_id: str) -> None: ...


class Replier(Protocol):
    def reply(self, linked: Linked, message: Waiting) -> Outgoing: ...

    def recheck(self, linked: Linked, reply: Outgoing) -> Outgoing: ...

    def notice(self, linked: Linked, message: Waiting) -> Outgoing | None: ...


@dataclass(frozen=True, slots=True)
class Channel:
    db: Database  # in the app's role (db_app_role), never the workers'
    sender: Sender
    number_key: str
    clock: Callable[[], datetime]
    chat: Replier  # W4: the reply to every other message (kaagaz.channel.chat.Chat)
    # W4: the replies made and not yet sent, by (customer, person, message): kept until sent or failed.
    made: dict[tuple[str, str, str], Outgoing] = field(default_factory=dict)
    slow_seconds: float = SLOW_SECONDS

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
                if self._not_sent(linked, message, type(error).__name__, most=MAX_TRIES) is None:
                    self.made.pop(_key(linked, message), None)
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
            return self._refused(linked, message, number, check.refused)
        word = (message.body or "").strip().casefold()
        ending = (
            "person"
            if word in (STOP, DISCONNECT_YES)
            else "not_me"
            if word == NOT_ME + linked.link_id
            else None
        )
        if ending is not None:
            with self.db.user(linked.customer_id, linked.user_id) as conn:
                wa.end(conn, linked, ending)
            sent = self._send(lambda: self.sender.send_text(number, DISCONNECTED)) is None
            return self._finish(linked, message, "done", replied=sent)
        key = _key(linked, message)
        if word == DISCONNECT:
            reply = Outgoing(ASK_DISCONNECT, DISCONNECT_BUTTONS)
        elif word == KEEP:
            reply = Outgoing(STILL_CONNECTED)
        else:
            made = self.made.get(key)
            if made is None:
                if message.document_id is not None:  # W5: a saved file's notice
                    notice = self.chat.notice(linked, message)
                    if notice is None:  # still being read
                        return NOTICE_SECONDS
                    made = notice
                else:
                    rest = self._quiet(linked, message)
                    if rest:  # W5: more files may still come
                        return rest
                    made = self._made(number, message, lambda: self.chat.reply(linked, message))
                if len(self.made) >= MAX_MADE:  # never more than this held: the oldest goes
                    self.made.pop(next(iter(self.made)))
                self.made[key] = made
            with self.db.user(linked.customer_id, linked.user_id) as conn:  # just before it goes
                again = wa.check(conn, linked)
            if again.sealed is None or again.refused is not None:
                self.made.pop(key, None)
                if again.sealed is None:
                    return self._finish(linked, message, "done", replied=False)
                return self._refused(linked, message, number, again.refused or "account")
            reply = self.chat.recheck(linked, made)
            if not reply.body:  # W5: a notice whose file was hidden, locked or deleted since
                self.made.pop(key, None)
                return self._finish(linked, message, "done", replied=False)
        error = self._send(lambda: self._deliver(number, reply))
        if error is None:
            self.made.pop(key, None)
            self._finish(linked, message, "done", replied=True, link=reply.link, also=reply.finished)
            for later in reply.later:  # Ask's memory, once the reply was sent
                try:
                    later()
                except Exception as failed:  # noqa: BLE001 - after the reply, as Starlette's own tasks
                    _log.warning("background task failed", extra={"code": type(failed).__name__})
            return 0
        status = error.status or 0
        for_good = 400 <= status < 500 and status not in _AUTH and status != 429
        code = f"{error.code}:{status or '-'}"
        tries = self._not_sent(linked, message, code, most=UNTIL_IT_EXPIRES, fail=for_good)
        if tries is None:
            self.made.pop(key, None)
        return 0 if tries is None else min(RETRY_SECONDS * 2 ** (tries - 1), RETRY_MAX_SECONDS)

    def _quiet(self, linked: Linked, message: Waiting) -> int:
        """W5: 0 when ``message`` may be answered now; for a file, the seconds until QUIET_SECONDS
        have passed since the newest file sent with it, unless another kind of message came after."""
        if message.kind not in wa_up.UPLOAD_KINDS:
            return 0
        with self.db.user(linked.customer_id, linked.user_id) as conn:
            files, over = wa_up.run(conn, linked.customer_id, linked.link_id)
        if over or not files:
            return 0
        left = (max(each.sent_at for each in files) + timedelta(seconds=QUIET_SECONDS)) - self.clock()
        return max(0, math.ceil(left.total_seconds()))

    def _made(self, number: str, message: Waiting, make: Callable[[], Outgoing]) -> Outgoing:
        """W5: ``make()`` while WhatsApp shows the message read and the typing indicator; a reply
        not made within ``slow_seconds`` sends LOOKING first. The lock keeps LOOKING before the
        reply: once the reply is made, LOOKING never goes."""
        self._send(lambda: self.sender.typing(message.wamid))
        made, lock = threading.Event(), threading.Lock()

        def slow() -> None:
            with lock:
                if not made.is_set():
                    self._send(lambda: self.sender.send_text(number, say("looking", _language_of(message))))

        timer = threading.Timer(self.slow_seconds, slow)
        timer.daemon = True
        timer.start()
        try:
            return make()
        finally:
            timer.cancel()
            with lock:
                made.set()

    def _refused(self, linked: Linked, message: Waiting, number: str, reason: str) -> int:
        """NOT_CONNECTED once to the number; the connection ends regardless (audited)."""
        sent = self._send(lambda: self.sender.send_text(number, NOT_CONNECTED)) is None
        with self.db.user(linked.customer_id, linked.user_id) as conn:
            wa.end(conn, linked, reason)
            wa.finish(conn, linked, message.id, "done", replied=sent)
        return 0

    def _deliver(self, number: str, reply: Outgoing) -> None:
        if reply.rows:
            rows = [(row.id, row.title, row.description) for row in reply.rows]
            self.sender.send_list(number, reply.body, reply.button, rows)
        elif reply.buttons:
            self.sender.send_buttons(number, reply.body, reply.buttons)
        else:
            self.sender.send_text(number, reply.body)

    def _send(self, send: Callable[[], None]) -> WhatsAppError | None:
        try:
            send()
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

    def _finish(
        self,
        linked: Linked,
        message: Waiting,
        state: str,
        *,
        replied: bool,
        link: str | None = None,
        also: Sequence[str] = (),
    ) -> int:
        """The message's end; ``link``: the document whose card was just sent, audited with it;
        ``also`` (W5): the person's other messages the reply answered too."""
        with self.db.user(linked.customer_id, linked.user_id) as conn:
            for each in (message.id, *also):
                wa.finish(conn, linked, each, state, replied=replied)
            if link is not None:
                audit.record(
                    conn,
                    linked.customer_id,
                    actor_user_id=linked.user_id,
                    action="whatsapp.file_link",
                    target_type="document",
                    target_id=link,
                    details={"via": "whatsapp"},
                    subject_user_id=linked.user_id,
                )
        return 0


def _key(linked: Linked, message: Waiting) -> tuple[str, str, str]:
    return (linked.customer_id, linked.user_id, message.id)


def _language_of(message: Waiting) -> Language:
    """A text's language; a tap's, from its id's last part (``ask.hi``, ``up:...:mr``); else English."""
    body = message.body or ""
    if message.kind == "text":
        return language(body) or "en"
    last = body.replace(":", ".").rsplit(".", 1)[-1]
    return next((each for each in LANGUAGES if each == last), "en")
