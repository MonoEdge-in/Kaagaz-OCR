"""The address Meta's WhatsApp webhook calls (#603, PRD 7 section 15). Every route here is 404 while
WhatsApp is off (``WHATSAPP_ENABLED``, on the router itself, as email's).

* ``GET /whatsapp/webhook``: Meta's setup check. ``hub.mode=subscribe`` with our verify token
  (compared in constant time) gets ``hub.challenge`` back as plain text; anything else 403.
* ``POST /whatsapp/webhook``: Meta's deliveries. No session and no CSRF header (``csrf_guard``
  lets this one path through): the signature is the proof. In order, and nothing is read before
  the signature holds:
  1. the raw body, at most MAX_BODY_BYTES (Meta's own most), else 413;
  2. ``X-Hub-Signature-256`` over those bytes with the app secret, else 403, nothing said;
  3. the messages to our own number only (``channel.inbound``, which drops a message with a bad
     id or time and strips what PostgreSQL can't keep from its text); a body that is not JSON,
     or nested too deep to read, is answered 200 and dropped, as Meta would only send it again:
     a signed body never answers 500;
  4. a message sent more than MAX_AGE (WhatsApp's 24 hours, the lead's decision on #603's
     review) ago is dropped, at WARNING: it can't be answered any more. A replay inside it is
     stopped by the message id kept 30 days (6);
  5. (W3) WhatsApp says the number's owner changed (``inbound``'s ``owner_changed``): every live
     connection of it ends, in any organisation (``kaagaz_whatsapp_end_number``), and nothing is
     stored. "Link <6 digits>" (the second way to connect) confirms the number's newest live phone
     code when right (``kaagaz_whatsapp_phone_code``; wrong: one try more), answered "go back to
     Kaagaz" and stored nowhere. BLOCK puts the number on the never-message list and ends its
     connection (the person's own end);
  6. the number's live connection (``kaagaz_whatsapp_lookup`` on its keyed hash); none: answered
     NOT_CONNECTED at most once a day, never once it said BLOCK (``kaagaz_whatsapp_tell_unknown``),
     never saying whether it once had an account;
  7. stored in the person's own transaction, once per WhatsApp message id (a second delivery
     changes nothing).
  Then 200, at once: the channel process does the work, and a reply here goes after the reply to
  Meta. A lost database is 503, and Meta's own retries keep the message. Its own rate bucket
  (``api/middleware.py``). No body or number is ever logged.
"""

import hmac
import json
import logging
import re
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from kaagaz.adapters.whatsapp import MetaWhatsApp, WhatsAppError
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.channel import inbound
from kaagaz.channel.loop import NOT_CONNECTED
from kaagaz.channel.numbers import code_hash, number_hash
from kaagaz.db import whatsapp as wa
from kaagaz.settings import Settings

_log = logging.getLogger("kaagaz.api.whatsapp")

WEBHOOK = "/whatsapp/webhook"
MAX_BODY_BYTES = 3 * 1024 * 1024  # Meta's webhook payloads are at most 3 MB
MAX_AGE = timedelta(hours=24)
_LINK = re.compile(r"link\s+([0-9]{6})")  # the second way's "Link <code>", trimmed and in lower case
GO_BACK = "Thanks. Now go back to Kaagaz and confirm the number there."


def require_whatsapp(request: Request) -> None:
    if not get_context(request).settings.whatsapp_enabled:
        raise ApiError(404, "not_found")


router = APIRouter(dependencies=[Depends(require_whatsapp)])

Context = Annotated[AppContext, Depends(get_context)]


async def raw_body(request: Request) -> bytes:
    """The body as it came, at most MAX_BODY_BYTES (413 past it, read no further)."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise ApiError(413, "too_large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.get(WEBHOOK, response_class=PlainTextResponse)
def verify(
    context: Context,
    mode: Annotated[str, Query(alias="hub.mode", max_length=32)] = "",
    token: Annotated[str, Query(alias="hub.verify_token", max_length=256)] = "",
    challenge: Annotated[str, Query(alias="hub.challenge", max_length=256)] = "",
) -> str:
    expected = context.settings.whatsapp_verify_token or ""
    if mode != "subscribe" or not expected or not hmac.compare_digest(token.encode(), expected.encode()):
        raise ApiError(403, "forbidden")
    return challenge


@router.post(WEBHOOK)
def receive(
    body: Annotated[bytes, Depends(raw_body)], request: Request, background: BackgroundTasks, context: Context
) -> dict[str, bool]:
    settings = context.settings
    if settings.whatsapp_app_secret is None or not inbound.signed(
        settings.whatsapp_app_secret, body, request.headers.get("x-hub-signature-256")
    ):
        raise ApiError(403, "forbidden")
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        _log.warning("whatsapp webhook not JSON", extra={"code": "not_json"})
        return {"ok": True}
    oldest = context.clock() - MAX_AGE
    for message in inbound.messages(payload, settings.whatsapp_phone_number_id or ""):
        if message.sent_at < oldest:
            _log.warning("whatsapp message dropped", extra={"code": "too_old"})
            continue
        key = settings.whatsapp_number_key or ""
        hashed = number_hash(key, message.number)
        typed = (message.body or "").strip().casefold() if message.kind == "text" else ""
        linking = _LINK.fullmatch(typed)
        with context.db.system() as conn:
            if message.owner_changed:
                wa.end_number(conn, hashed, "number_changed")
                continue
            if linking is not None:
                if wa.phone_code(conn, hashed, code_hash(key, message.number, linking.group(1))):
                    background.add_task(_reply, settings, message.number, GO_BACK)
                continue
            if typed == "block":
                wa.block(conn, hashed)
            linked = wa.lookup(conn, hashed)
            if linked is None:
                if typed != "block" and wa.tell_unknown(conn, hashed):
                    background.add_task(_reply, settings, message.number, NOT_CONNECTED)
                continue
        with context.db.user(linked.customer_id, linked.user_id) as conn:
            if typed == "block":
                wa.end(conn, linked, "person")
            else:
                wa.add_message(conn, linked, message)
    return {"ok": True}


def _reply(settings: Settings, number: str, text: str) -> None:
    """One text to a number that wrote just now, after the reply to Meta; a refusal is logged."""
    try:
        MetaWhatsApp.from_settings(settings).send_text(number, text)
    except WhatsAppError as error:
        _log.warning("whatsapp reply not sent", extra={"code": f"{error.code}:{error.status or '-'}"})
