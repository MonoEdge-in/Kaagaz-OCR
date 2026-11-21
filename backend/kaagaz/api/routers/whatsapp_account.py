"""Kaagaz on WhatsApp: connecting a number, and the Admin's switches (#603 W3, PRD 7 section 15).
Every route is 404 while WhatsApp is off (``WHATSAPP_ENABLED``), as the webhook's; the Admin's are
the admin session's first (``routers/admin.py``'s rule).

Account, WhatsApp (signed in, any role). The bot messages first:

* ``GET /whatsapp/me``: ``state``: ``off`` (WhatsApp is off for the organisation: its Admin's switch
  or the super admin's), ``not_allowed`` (their role), ``none`` or ``connected``, with the number's
  last 4 digits and when it was connected and last used.
* The proof: a fresh Keycloak sign-in for ``whatsapp`` (``routers/keycloak.py`` ``reauth_start``),
  which sets the proof cookie too: 10 minutes, this person at this session version; signing out
  clears it.
* ``POST /whatsapp/codes`` {number, agreed, way}: with the proof (else 403 ``proof_needed``), the
  "I agree" tick (400), a number (400), WhatsApp on for the organisation and the role (403 ``off``,
  ``not_allowed``). Nothing says whether the number is connected anywhere. ``way``:
  * ``sent``: one request a person at a time, at most CODES_AN_HOUR for the account (429) and 5 a
    day for the number in every organisation (``kaagaz_whatsapp_may_send``, 429); a 6-digit code,
    10 minutes, goes with Meta's code template: ``{last4, sent: true}``. When Meta refuses it, or the
    number said BLOCK (then nothing is sent, but the code counts all the same, so BLOCK can't be
    told from a refusal), the same reply starts the second way: ``{last4, sent: false, code, link}``.
  * ``phone`` ("Didn't get it?"): only after a code the bot sent this person for this same number in
    the last 10 minutes, still their newest (else 409 ``send_first``; decision 9): the second way.
  The second way: a code for that number shown here, the link opening WhatsApp with "Link <code>" to
  Kaagaz's number. The webhook confirms it when that number sends it (``kaagaz_whatsapp_phone_code``).
* ``POST /whatsapp/codes/verify`` {code}: with the ten-minute WhatsApp proof still on (not also the
  five-minute check, which only asking for a code needs); the newest code only (a newer one
  replaces the older ones), sent this way, unexpired, under 5 wrong tries; the code is hashed with
  the number it was sent to, so only that number connects. Wrong: one try counted (400, how many are
  left).
* ``GET /whatsapp/codes/phone``: the second way's ``{state: waiting | confirmed | expired}``;
  ``POST /whatsapp/codes/phone/confirm``: "Connect +91 ****1234?" said yes (the proof still on), once
  the number sent it.
* Connecting (both ways), in one transaction: WhatsApp still on for them; every code of the person
  goes (the numbers in them too); the person's own live connection ends first (a stale one, say
  after WHATSAPP_NUMBER_KEY changed); then the number's. A number connected to another account is
  409 ``number_taken`` with nobody named, and only now, after the right code. Audited
  ``whatsapp.connected`` (the person its subject, the last 4 digits). Then the welcome, before the
  reply: the person, their sign-in email masked and the organisation, [Start] [Not me - disconnect]
  (the template; on the second way, whose 24 hours are open, a text with the same buttons). Meta
  refusing it ends the connection at once (``unwelcomed``, audited): 502 ``welcome_not_sent``. Then
  the security email, after the reply.
* ``POST /whatsapp/disconnect``: their own connection ends (audited by the function).

The Admin (``/admin/whatsapp``):

* ``GET``: the switches (WhatsApp, the roles that may connect, Management, voice notes, uploads),
  ``available`` (the super admin's switches) and who is connected, when, and when last used.
* ``PUT`` {enabled, roles, management, voice, uploads, rules_confirmed?}: all at once.
  Turning WhatsApp on needs the super admin's switch (409 ``not_available``) and the Admin's tick
  "our organisation's data rules allow WhatsApp" (400 without; stored with when and who, and in the
  audit row); it is emailed to Management and the other Admins. Turning it on, or ticking Admins or
  Management, needs the Admin's Keycloak proof for ``whatsapp`` (403 ``keycloak_reauth_required``).
  A change is audited
  (``settings.whatsapp_on``, ``_off`` or ``settings.whatsapp``, the values in its details), then the
  sweep ends at once the connections no longer allowed.
* ``POST /admin/whatsapp/{user_id}/disconnect``: ends that person's connection (audited by the
  function) and tells their phone with the "you were disconnected" template; a number that no longer
  opens (WHATSAPP_NUMBER_KEY changed) ends all the same, untold, logged ``number_sealed``.

The "you were disconnected" message goes after the reply; one that fails is logged (its code only,
never the number), as the channel's.
"""

import hmac
import logging
import re
import uuid
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from kaagaz.adapters.whatsapp import MetaWhatsApp, WhatsAppError, masked_email
from kaagaz.api import emails
from kaagaz.api.auth import (
    WHATSAPP_PROOF_COOKIE,
    AdminSession,
    CurrentUser,
    User,
    require_admin_session,
    whatsapp_proved,
)
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.auth import require_proof
from kaagaz.api.routers.whatsapp import require_whatsapp
from kaagaz.api.secret_box import SecretBoxError
from kaagaz.api.twostep import unix
from kaagaz.api.views import iso
from kaagaz.channel.numbers import code_hash, e164, new_code, number_hash, open_number, seal
from kaagaz.db import audit
from kaagaz.db import users as users_sql
from kaagaz.db import whatsapp as wa
from kaagaz.db.whatsapp import Linked

router = APIRouter(dependencies=[Depends(require_whatsapp)])
admin_router = APIRouter(dependencies=[Depends(require_admin_session), Depends(require_whatsapp)])
_log = logging.getLogger("kaagaz.api.whatsapp")

Context = Annotated[AppContext, Depends(get_context)]

CODES_AN_HOUR = 3  # codes one account may ask for
MAX_TRIES = 5  # wrong codes for one code (whatsapp_link_codes.tries)
_SPACING = re.compile(r"[\s().-]")
ROLES = ("member", "reviewer", "admin")

PROOF_NEEDED = "Confirm it's you first."
NOT_AGREED = "Tick the box to agree before you continue."
BAD_NUMBER = "Enter your WhatsApp number with its country code, like +91 98765 43210."
OFF = "WhatsApp isn't turned on for your organisation."
NOT_ALLOWED = "Your organisation doesn't use WhatsApp for your role."
TOO_MANY_CODES = "Too many codes were asked for. Try again in an hour."
NUMBER_LIMIT = "Too many codes went to this number today. Try again tomorrow."
NO_CODE = "That code has expired. Ask for a new one."
TOO_MANY_TRIES = "Too many wrong codes. Ask for a new one."
NUMBER_TAKEN = "This WhatsApp is already connected to another Kaagaz account."
NOT_CONNECTED = "No WhatsApp number is connected."
NOT_AVAILABLE = "WhatsApp isn't available for your organisation yet."
RULES_NOT_CONFIRMED = "Confirm that your organisation's data rules allow WhatsApp."
SEND_FIRST = "Ask for a code on WhatsApp first."
WELCOME_NOT_SENT = "Kaagaz couldn't message this number on WhatsApp, so it isn't connected. Try again later."
_REFUSALS = {"off": OFF, "not_allowed": NOT_ALLOWED}


class CodesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: str = Field(min_length=1, max_length=32)
    agreed: bool
    way: Literal["sent", "phone"] = "sent"


class VerifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^\s*\d{6}\s*$")


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    roles: list[Literal["member", "reviewer", "admin"]] = Field(max_length=3)
    management: bool
    voice: bool
    uploads: bool
    rules_confirmed: bool = False  # "our organisation's data rules allow WhatsApp", to turn it on


# --- Account, WhatsApp ---------------------------------------------------------------------------


@router.get("/whatsapp/me")
def my_whatsapp(user: User, context: Context) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        refused = wa.refused(conn, user.customer_id, user.user_id)
        mine = wa.mine(conn, user.customer_id, user.user_id)
    reply: dict[str, Any] = {"state": refused or "none"}
    if mine is not None:  # shown even when it is no longer allowed: Disconnect stays
        reply |= {
            "state": "connected",
            "last4": mine.last4,
            "linked_at": iso(mine.linked_at),
            "last_used_at": None if mine.last_used_at is None else iso(mine.last_used_at),
        }
    return reply


@router.post("/whatsapp/codes")
def ask_for_code(body: CodesBody, user: User, request: Request, context: Context) -> dict[str, Any]:
    settings = context.settings
    key = settings.whatsapp_number_key or ""
    _require_proof(request, user, context)
    if not body.agreed:
        raise ApiError(400, "bad_request", NOT_AGREED)
    number = e164(_SPACING.sub("", body.number))
    if number is None:
        raise ApiError(400, "bad_request", BAD_NUMBER)
    code, hashed = new_code(), number_hash(key, number)
    sealed = seal(key, number, user.customer_id, user.user_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        _refuse(conn, user)
        if body.way == "phone":  # "Didn't get it?": only after the bot's code to this same number
            newest = wa.newest_code(conn, user.customer_id, user.user_id)
            if newest is None or newest.via != "sent" or not newest.live or newest.phone_hash != hashed:
                raise ApiError(409, "send_first", SEND_FIRST)
            return _second_way(conn, context, user, number, hashed, sealed)
        wa.lock_codes(conn, user.user_id)
        if wa.codes_this_hour(conn, user.customer_id, user.user_id) >= CODES_AN_HOUR:
            raise ApiError(429, "rate_limited", TOO_MANY_CODES)
        allowed = wa.may_send(conn, hashed)
        if allowed == "number_limit":
            raise ApiError(429, "rate_limited", NUMBER_LIMIT)
        wa.add_code(
            conn, user.customer_id, user.user_id, hashed, sealed, code_hash(key, number, code), "sent"
        )
        if allowed == "blocked":  # it said BLOCK: never messaged; counted, and answered as a refusal
            return _second_way(conn, context, user, number, hashed, sealed)
    try:
        MetaWhatsApp.from_settings(settings).send_code(number, code)
    except WhatsAppError as error:
        _log.warning("whatsapp code not sent", extra={"code": f"{error.code}:{error.status or '-'}"})
        with context.db.user(user.customer_id, user.user_id) as conn:
            return _second_way(conn, context, user, number, hashed, sealed)
    return {"last4": number[-4:], "sent": True}


def _second_way(
    conn: Connection, context: AppContext, user: CurrentUser, number: str, hashed: bytes, sealed: str
) -> dict[str, Any]:
    """A phone code for the number, shown with the link that sends "Link <code>" from WhatsApp."""
    code = new_code()
    key = context.settings.whatsapp_number_key or ""
    wa.add_code(conn, user.customer_id, user.user_id, hashed, sealed, code_hash(key, number, code), "phone")
    link = f"https://wa.me/{context.settings.whatsapp_business_number}?text=Link%20{code}"
    return {"last4": number[-4:], "sent": False, "code": code, "link": link}


@router.post("/whatsapp/codes/verify")
def verify_code(
    body: VerifyBody, user: User, request: Request, background: BackgroundTasks, context: Context
) -> dict[str, Any]:
    _require_proof(request, user, context, fresh=False)
    key = context.settings.whatsapp_number_key or ""
    wrong, link_id = None, None
    with context.db.user(user.customer_id, user.user_id) as conn:
        code, number = _live_code(conn, context, user, "sent")
        if code.tries >= MAX_TRIES:
            raise ApiError(400, "bad_request", TOO_MANY_TRIES)
        if hmac.compare_digest(code.code_hash, code_hash(key, number, body.code.strip())):
            link_id = _connect(conn, context, user, code, number)
        else:
            wa.code_tried(conn, user.customer_id, code.id)
            wrong = MAX_TRIES - code.tries - 1
    if wrong is not None:  # the try is counted (committed) first
        left = f"{wrong} {'try' if wrong == 1 else 'tries'} left." if wrong else "Ask for a new one."
        raise ApiError(400, "wrong_code", f"That code isn't right. {left}")
    return _welcomed(context, user, background, number, link_id, window_open=False)


@router.get("/whatsapp/codes/phone")
def phone_code_state(user: User, context: Context) -> dict[str, str]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        code = wa.newest_code(conn, user.customer_id, user.user_id)
    if code is None or code.via != "phone" or code.used or not code.live:
        return {"state": "expired"}
    return {"state": "confirmed" if code.confirmed else "waiting"}


@router.post("/whatsapp/codes/phone/confirm")
def confirm_phone_code(
    user: User, request: Request, background: BackgroundTasks, context: Context
) -> dict[str, Any]:
    _require_proof(request, user, context, fresh=False)
    with context.db.user(user.customer_id, user.user_id) as conn:
        code, number = _live_code(conn, context, user, "phone")
        if not code.confirmed:
            raise ApiError(409, "not_confirmed", "Send the message from WhatsApp first.")
        link_id = _connect(conn, context, user, code, number)
    return _welcomed(context, user, background, number, link_id, window_open=True)


@router.post("/whatsapp/disconnect")
def disconnect(user: User, context: Context) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        mine = wa.mine(conn, user.customer_id, user.user_id)
        if mine is None:
            raise ApiError(404, "not_found", NOT_CONNECTED)
        wa.end(conn, Linked(user.customer_id, user.user_id, mine.id), "person")
    return my_whatsapp(user, context)


def _require_proof(request: Request, user: CurrentUser, context: AppContext, *, fresh: bool = True) -> None:
    """Asking for a code needs the company sign-in just now (the five-minute proof) and its ten-minute
    WhatsApp cookie; the steps after it (the code typed, the phone way confirmed) only the ten-minute
    cookie, so a slow typist is not sent back to the sign-in in the middle of connecting."""
    proof = request.cookies.get(WHATSAPP_PROOF_COOKIE) or ""
    secret, now = context.settings.session_secret, unix(context.clock())
    if (fresh and user.reauth_action != "whatsapp") or not whatsapp_proved(proof, user, secret, now):
        raise ApiError(403, "proof_needed", PROOF_NEEDED)


def _refuse(conn: Connection, user: CurrentUser) -> None:
    refused = wa.refused(conn, user.customer_id, user.user_id)
    if refused is not None:
        raise ApiError(403, refused, _REFUSALS[refused])


def _live_code(conn: Connection, context: AppContext, user: CurrentUser, via: str) -> tuple[wa.Code, str]:
    """The person's newest code, sent ``via``, unexpired, and its number; else 400."""
    code = wa.newest_code(conn, user.customer_id, user.user_id)
    if code is None or code.via != via or code.used or not code.live or code.sealed is None:
        raise ApiError(400, "bad_request", NO_CODE)
    return code, open_number(
        context.settings.whatsapp_number_key or "", code.sealed, user.customer_id, user.user_id
    )


def _welcomed(
    context: AppContext,
    user: CurrentUser,
    background: BackgroundTasks,
    number: str,
    link_id: str | None,
    *,
    window_open: bool,
) -> dict[str, Any]:
    """The welcome, before the reply (see the module notes); ``/whatsapp/me`` once it went."""
    if link_id is None:
        raise ApiError(409, "number_taken", NUMBER_TAKEN)
    names = (user.display_name, masked_email(user.email), user.customer_name, link_id)
    bot = MetaWhatsApp.from_settings(context.settings)
    try:
        (bot.welcome_buttons if window_open else bot.send_welcome)(number, *names)
    except WhatsAppError as error:
        _log.warning("whatsapp welcome not sent", extra={"code": f"{error.code}:{error.status or '-'}"})
        with context.db.user(user.customer_id, user.user_id) as conn:
            wa.end(conn, Linked(user.customer_id, user.user_id, link_id), "unwelcomed")
        raise ApiError(502, "welcome_not_sent", WELCOME_NOT_SENT) from None
    who = (context, user.customer_id, user.user_id)
    emails.after(
        background, "whatsapp_connected", emails.notify, *who, "whatsapp_connected", emails.whatsapp_connected
    )
    return my_whatsapp(user, context)


def _connect(
    conn: Connection, context: AppContext, user: CurrentUser, code: wa.Code, number: str
) -> str | None:
    """The right code: every code of the person goes, and the number connects (see the module notes):
    its connection's id, or None when it is connected to another account."""
    customer_id, user_id = user.customer_id, user.user_id
    _refuse(conn, user)
    sealed = seal(context.settings.whatsapp_number_key or "", number, customer_id, user_id)
    wa.drop_codes(conn, customer_id, user_id)
    mine = wa.mine(conn, customer_id, user_id)
    try:
        with conn.begin_nested():
            if mine is not None:  # their own, ended first: a stale one, or another number
                wa.end(conn, Linked(customer_id, user_id, mine.id), "person")
            link_id = wa.add_link(
                conn, customer_id, user_id, code.phone_hash, sealed, number[-4:], user.session_version
            )
    except wa.NumberTaken:
        return None
    audit.record(
        conn,
        customer_id,
        actor_user_id=user_id,
        action="whatsapp.connected",
        target_type="user",
        target_id=user_id,
        details={"link_id": link_id, "number_last4": number[-4:]},
        subject_user_id=user_id,
    )
    return link_id


def _tell(context: AppContext, what: str, send: Callable[[MetaWhatsApp], None]) -> None:
    """One template to the phone, after the reply; a refusal is logged (``what``), never the number."""
    try:
        send(MetaWhatsApp.from_settings(context.settings))
    except WhatsAppError as error:
        _log.warning(
            "whatsapp template not sent", extra={"code": f"{what}:{error.code}:{error.status or '-'}"}
        )


# --- the Admin's ---------------------------------------------------------------------------------


@admin_router.get("/admin/whatsapp")
def admin_whatsapp(user: AdminSession, context: Context) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        row = wa.org_settings(conn, user.customer_id)
        people = wa.connections(conn, user.customer_id)
    if row is None:
        raise ApiError(404, "not_found")
    return {
        **{name: row[name] for name in ("enabled", "management", "voice", "uploads", "available")},
        "roles": [role for role in ROLES if role in row["roles"]],
        "people": [
            {
                "user_id": str(person["user_id"]),
                "last4": person["phone_last4"],
                "linked_at": iso(person["linked_at"]),
                "last_used_at": None if person["last_used_at"] is None else iso(person["last_used_at"]),
            }
            for person in people
        ],
    }


@admin_router.put("/admin/whatsapp")
def save_whatsapp(
    body: SettingsBody, user: AdminSession, background: BackgroundTasks, context: Context
) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        before = wa.org_settings(conn, user.customer_id)
    if before is None:
        raise ApiError(404, "not_found")
    turned_on = body.enabled and not before["enabled"]
    roles = [role for role in ROLES if role in body.roles]
    ticked = ("admin" in roles and "admin" not in before["roles"]) or (
        body.management and not before["management"]
    )
    if turned_on:
        if not before["available"]:
            raise ApiError(409, "not_available", NOT_AVAILABLE)
        if not body.rules_confirmed:
            raise ApiError(400, "bad_request", RULES_NOT_CONFIRMED)
    if turned_on or ticked:
        require_proof(user, "whatsapp")
    wanted = {
        "enabled": body.enabled,
        "management": body.management,
        "voice": body.voice,
        "uploads": body.uploads,
    }
    was = {name: before[name] for name in wanted}
    if (wanted, roles) == (was, [role for role in ROLES if role in before["roles"]]):
        return admin_whatsapp(user, context)
    with context.db.user(user.customer_id, user.user_id) as conn:
        confirmed_by = user.user_id if turned_on else None
        wa.save_settings(conn, user.customer_id, roles=roles, confirmed_by=confirmed_by, **wanted)
        action = "settings.whatsapp"
        if body.enabled != before["enabled"]:
            action = "settings.whatsapp_on" if body.enabled else "settings.whatsapp_off"
        audit.record(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action=action,
            target_type="customer",
            target_id=user.customer_id,
            details={**wanted, "roles": ",".join(roles), **({"rules_confirmed": True} if turned_on else {})},
        )
    with context.db.system() as conn:
        wa.sweep(conn)  # the connections no longer allowed end now, not at the next hourly sweep
    if turned_on:
        emails.after(background, "whatsapp_on", _tell_leaders, context, user)
    return admin_whatsapp(user, context)


@admin_router.post("/admin/whatsapp/{user_id}/disconnect")
def admin_disconnect(
    user_id: uuid.UUID, user: AdminSession, background: BackgroundTasks, context: Context
) -> dict[str, Any]:
    target = str(user_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        mine = wa.mine(conn, user.customer_id, target)
        if mine is None:
            raise ApiError(404, "not_found", NOT_CONNECTED)
        try:
            number: str | None = open_number(
                context.settings.whatsapp_number_key or "", mine.sealed, user.customer_id, target
            )
        except SecretBoxError:  # WHATSAPP_NUMBER_KEY changed: it ends all the same, untold
            _log.warning("whatsapp number not opened", extra={"code": "number_sealed"})
            number = None
        wa.end(conn, Linked(user.customer_id, target, mine.id), "admin")
    if number is not None:
        told = number
        background.add_task(_tell, context, "disconnected", lambda bot: bot.send_disconnected(told))
    return admin_whatsapp(user, context)


def _tell_leaders(context: AppContext, admin: CurrentUser) -> None:
    """ "WhatsApp was turned on" to Management and every other Admin, while email is on."""
    if not emails.email_on(context):
        return
    with context.db.customer(admin.customer_id) as conn:
        leaders = users_sql.admin_contacts(conn, admin.customer_id, management=True)
    for address, name in leaders:
        if address != admin.email:
            emails.send(context, address, "whatsapp_on", emails.whatsapp_turned_on(name, admin.customer_name))
