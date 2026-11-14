"""Kaagaz on WhatsApp: connecting a number, and the Admin's switches (#603 W3, PRD 7 section 15).
Every route is 404 while WhatsApp is off (``WHATSAPP_ENABLED``), as the webhook's; the Admin's are
the admin session's first (``routers/admin.py``'s rule).

Account, WhatsApp (signed in, any role). The bot messages first:

* ``GET /whatsapp/me``: ``state``: ``off`` (WhatsApp is off for the organisation: its Admin's switch
  or the super admin's), ``not_allowed`` (their role), ``none`` or ``connected``, with the number's
  last 4 digits and when it was connected and last used; ``google``: they sign in with Google only,
  so they confirm with Google; ``totp``: the code is asked too.
* ``POST /whatsapp/prove`` {password, code?}: the password again, plus the code for anyone with
  two-step sign-in on (``routers/auth.py`` ``_prove``: counted like a sign-in, as Google connect's);
  ``POST /whatsapp/prove/google`` {code?}: for one who signs in with Google only: the code first
  (two-step on), then ``{url}`` to Google, whose callback (``routers/oidc.py``) checks it is their
  own connection. Either sets the proof cookie: PROOF_SECONDS, this person at this session version.
* ``POST /whatsapp/codes`` {number, agreed, way}: with the proof (else 403 ``proof_needed``), the
  "I agree" tick (400), a number (400), WhatsApp on for the organisation and the role (403 ``off``,
  ``not_allowed``), at most CODES_AN_HOUR codes for the account (429) and 5 a day for the number in
  every organisation (``kaagaz_whatsapp_may_send``, 429). Nothing says whether the number is
  connected anywhere. ``way``:
  * ``sent``: a 6-digit code, CODE_MINUTES, is sent with Meta's code template; ``{last4, sent}``.
    ``sent`` false when Meta refused it, or the number said BLOCK (nothing is sent, nothing kept):
    the screen offers the second way.
  * ``phone`` (the second way): a code for that number shown here: ``{last4, code, link}``, the
    link opening WhatsApp with "Link <code>" to Kaagaz's number. The webhook confirms it when that
    number sends it (``kaagaz_whatsapp_phone_code``).
* ``POST /whatsapp/codes/verify`` {code}: the newest code only (a newer one replaces the older
  ones), sent this way, unused, unexpired, under 5 wrong tries; the code is hashed with the number
  it was sent to, so only that number connects. Wrong: one try counted (400, how many are left).
* ``GET /whatsapp/codes/phone``: the second way's ``{state: waiting | confirmed | expired}``;
  ``POST /whatsapp/codes/phone/confirm``: "Connect +91 ••••1234?" said yes, once the number sent it.
* Connecting (both ways), in one transaction: WhatsApp still on for them; the person's own live
  connection ends first (a stale one, say after WHATSAPP_NUMBER_KEY changed); then the number's. A
  number connected to another account is 409 ``number_taken`` with nobody named, and only now, after
  the right code. Audited ``whatsapp.connected`` (the person its subject, the last 4 digits), then
  the welcome naming the person and the organisation, [Start] [Not me - disconnect], and the
  security email, after the reply.
* ``POST /whatsapp/disconnect``: their own connection ends (audited by the function).

The Admin (``/admin/whatsapp``):

* ``GET``: the switches (WhatsApp, the roles that may connect, Management, voice notes, uploads),
  ``available`` (the super admin's switches) and who is connected, when, and when last used.
* ``PUT`` {enabled, roles, management, voice, uploads, password?}: all at once. Turning WhatsApp
  on needs the Admin's password (400 without, counted like a sign-in) and the super admin's switch
  (409 ``not_available``); it is emailed to Management and the other Admins. A change is audited
  (``settings.whatsapp_on``, ``_off`` or ``settings.whatsapp``, the values in its details), then the
  sweep ends at once the connections no longer allowed.
* ``POST /admin/whatsapp/{user_id}/disconnect``: ends that person's connection (audited by the
  function) and tells their phone with the "you were disconnected" template.

A message to a phone goes after the reply; one that fails is logged (its code only, never the
number), as the channel's.
"""

import dataclasses
import hmac
import logging
import re
import uuid
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from kaagaz.adapters.whatsapp import MetaWhatsApp, WhatsAppError
from kaagaz.api import emails, oidc
from kaagaz.api.auth import (
    WHATSAPP_PROOF_COOKIE,
    AdminSession,
    CurrentUser,
    User,
    require_admin_session,
    set_flow_cookie,
    set_whatsapp_proof_cookie,
    whatsapp_proved,
)
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.admin import MAX_PASSWORD
from kaagaz.api.routers.auth import (
    MAX_CODE,
    _check_current_password,
    _per_user_limit,
    _prove,
    _prove_code,
    _succeeded,
)
from kaagaz.api.routers.oidc import WHATSAPP_PAGE, _new_flow, connect_limits
from kaagaz.api.routers.whatsapp import require_whatsapp
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
ON_NEEDS_PROOF = "Type your Kaagaz password to turn WhatsApp on."
_REFUSALS = {"off": OFF, "not_allowed": NOT_ALLOWED}


class ProveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    code: str | None = Field(default=None, max_length=MAX_CODE)


class GoogleProveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, max_length=MAX_CODE)


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
    password: str | None = Field(default=None, min_length=1, max_length=MAX_PASSWORD)


# --- Account, WhatsApp ---------------------------------------------------------------------------


@router.get("/whatsapp/me")
def my_whatsapp(user: User, context: Context) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        refused = wa.refused(conn, user.customer_id, user.user_id)
        mine = wa.mine(conn, user.customer_id, user.user_id)
    google = (
        oidc.enabled(context.settings, "google")
        and user.google_on
        and user.google_only
        and user.google_linked
    )
    reply: dict[str, Any] = {"state": refused or "none", "google": google, "totp": user.totp_enabled}
    if mine is not None:  # shown even when it is no longer allowed: Disconnect stays
        reply |= {
            "state": "connected",
            "last4": mine.last4,
            "linked_at": iso(mine.linked_at),
            "last_used_at": None if mine.last_used_at is None else iso(mine.last_used_at),
        }
    return reply


@router.post("/whatsapp/prove")
def prove(body: ProveBody, user: User, context: Context) -> JSONResponse:
    _prove(context, user, body.password, body.code)
    response = JSONResponse({"proved": True})
    set_whatsapp_proof_cookie(response, user, context.settings, now=unix(context.clock()))
    return response


@router.post("/whatsapp/prove/google")
def prove_with_google(body: GoogleProveBody, user: User, context: Context) -> JSONResponse:
    if not oidc.enabled(context.settings, "google"):
        raise ApiError(404, "not_found")
    _per_user_limit(connect_limits, user, "Too many tries. Try again in an hour.")
    _prove_code(context, user, body.code)
    now = unix(context.clock())
    provider = oidc.PROVIDERS["google"]
    flow = dataclasses.replace(_new_flow(provider, WHATSAPP_PAGE, now, user), mode="whatsapp")
    url = oidc.authorize_url(
        provider, context.settings, state=flow.state, nonce=flow.nonce, verifier=flow.verifier
    )
    response = JSONResponse({"url": url})
    set_flow_cookie(response, flow, context.settings, now=now)
    return response


@router.post("/whatsapp/codes")
def ask_for_code(body: CodesBody, user: User, request: Request, context: Context) -> dict[str, Any]:
    settings = context.settings
    key = settings.whatsapp_number_key or ""
    proof = request.cookies.get(WHATSAPP_PROOF_COOKIE) or ""
    if not whatsapp_proved(proof, user, settings.session_secret, unix(context.clock())):
        raise ApiError(403, "proof_needed", PROOF_NEEDED)
    if not body.agreed:
        raise ApiError(400, "bad_request", NOT_AGREED)
    number = e164(_SPACING.sub("", body.number))
    if number is None:
        raise ApiError(400, "bad_request", BAD_NUMBER)
    code, hashed = new_code(), number_hash(key, number)
    with context.db.user(user.customer_id, user.user_id) as conn:
        _refuse(conn, user)
        if wa.codes_this_hour(conn, user.customer_id, user.user_id) >= CODES_AN_HOUR:
            raise ApiError(429, "rate_limited", TOO_MANY_CODES)
        allowed = wa.may_send(conn, hashed)
        if allowed == "number_limit":
            raise ApiError(429, "rate_limited", NUMBER_LIMIT)
        if allowed == "blocked" and body.way == "sent":  # it said BLOCK: never messaged
            return {"last4": number[-4:], "sent": False}
        sealed = seal(key, number, user.customer_id, user.user_id)
        wa.add_code(
            conn, user.customer_id, user.user_id, hashed, sealed, code_hash(key, number, code), body.way
        )
    if body.way == "phone":
        link = f"https://wa.me/{settings.whatsapp_business_number}?text=Link%20{code}"
        return {"last4": number[-4:], "code": code, "link": link}
    try:
        MetaWhatsApp.from_settings(settings).send_code(number, code)
    except WhatsAppError as error:
        _log.warning("whatsapp code not sent", extra={"code": f"{error.code}:{error.status or '-'}"})
        return {"last4": number[-4:], "sent": False}
    return {"last4": number[-4:], "sent": True}


@router.post("/whatsapp/codes/verify")
def verify_code(
    body: VerifyBody, user: User, background: BackgroundTasks, context: Context
) -> dict[str, Any]:
    key = context.settings.whatsapp_number_key or ""
    wrong, connected = None, False
    with context.db.user(user.customer_id, user.user_id) as conn:
        code = _live_code(conn, user, "sent")
        if code.tries >= MAX_TRIES:
            raise ApiError(400, "bad_request", TOO_MANY_TRIES)
        number = open_number(key, code.sealed, user.customer_id, user.user_id)
        if hmac.compare_digest(code.code_hash, code_hash(key, number, body.code.strip())):
            connected = _connect(conn, context, user, background, code, number)
        else:
            wa.code_tried(conn, user.customer_id, code.id)
            wrong = MAX_TRIES - code.tries - 1
    if wrong is not None:  # the try is counted (committed) first
        left = f"{wrong} {'try' if wrong == 1 else 'tries'} left." if wrong else "Ask for a new one."
        raise ApiError(400, "wrong_code", f"That code isn't right. {left}")
    return _connected(connected, user, context)


@router.get("/whatsapp/codes/phone")
def phone_code_state(user: User, context: Context) -> dict[str, str]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        code = wa.newest_code(conn, user.customer_id, user.user_id)
    if code is None or code.via != "phone" or code.used or not code.live:
        return {"state": "expired"}
    return {"state": "confirmed" if code.confirmed else "waiting"}


@router.post("/whatsapp/codes/phone/confirm")
def confirm_phone_code(user: User, background: BackgroundTasks, context: Context) -> dict[str, Any]:
    key = context.settings.whatsapp_number_key or ""
    with context.db.user(user.customer_id, user.user_id) as conn:
        code = _live_code(conn, user, "phone")
        if not code.confirmed:
            raise ApiError(409, "not_confirmed", "Send the message from WhatsApp first.")
        number = open_number(key, code.sealed, user.customer_id, user.user_id)
        connected = _connect(conn, context, user, background, code, number)
    return _connected(connected, user, context)


@router.post("/whatsapp/disconnect")
def disconnect(user: User, context: Context) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        mine = wa.mine(conn, user.customer_id, user.user_id)
        if mine is None:
            raise ApiError(404, "not_found", NOT_CONNECTED)
        wa.end(conn, Linked(user.customer_id, user.user_id, mine.id), "person")
    return my_whatsapp(user, context)


def _refuse(conn: Connection, user: CurrentUser) -> None:
    refused = wa.refused(conn, user.customer_id, user.user_id)
    if refused is not None:
        raise ApiError(403, refused, _REFUSALS[refused])


def _live_code(conn: Connection, user: CurrentUser, via: str) -> wa.Code:
    """The person's newest code, sent ``via``, unused and unexpired; else 400."""
    code = wa.newest_code(conn, user.customer_id, user.user_id)
    if code is None or code.via != via or code.used or not code.live:
        raise ApiError(400, "bad_request", NO_CODE)
    return code


def _connected(connected: bool, user: CurrentUser, context: AppContext) -> dict[str, Any]:
    """``/whatsapp/me`` once connected; 409 when the number is another account's (the code spent)."""
    if not connected:
        raise ApiError(409, "number_taken", NUMBER_TAKEN)
    return my_whatsapp(user, context)


def _connect(
    conn: Connection,
    context: AppContext,
    user: CurrentUser,
    background: BackgroundTasks,
    code: wa.Code,
    number: str,
) -> bool:
    """The right code: the code is spent, and the number connects (see the module notes); False when it
    is connected to another account."""
    customer_id, user_id = user.customer_id, user.user_id
    _refuse(conn, user)
    wa.code_used(conn, customer_id, code.id)
    mine = wa.mine(conn, customer_id, user_id)
    try:
        with conn.begin_nested():
            if mine is not None:  # their own, ended first: a stale one, or another number
                wa.end(conn, Linked(customer_id, user_id, mine.id), "person")
            link_id = wa.add_link(
                conn, customer_id, user_id, code.phone_hash, code.sealed, number[-4:], user.session_version
            )
    except wa.NumberTaken:
        return False
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
    name, organisation = user.display_name, user.customer_name
    background.add_task(
        _tell, context, "welcome", lambda bot: bot.send_welcome(number, name, organisation, link_id)
    )
    who = (context, customer_id, user_id)
    emails.after(
        background, "whatsapp_connected", emails.notify, *who, "whatsapp_connected", emails.whatsapp_connected
    )
    return True


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
    if turned_on:
        if not before["available"]:
            raise ApiError(409, "not_available", NOT_AVAILABLE)
        if body.password is None:
            raise ApiError(400, "bad_request", ON_NEEDS_PROOF)
        _succeeded(
            context, _check_current_password(context, user, body.password, context.clock()), signed_in=True
        )
    roles = [role for role in ROLES if role in body.roles]
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
        wa.save_settings(conn, user.customer_id, roles=roles, **wanted)
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
            details={**wanted, "roles": ",".join(roles)},
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
        number = open_number(
            context.settings.whatsapp_number_key or "", mine.sealed, user.customer_id, target
        )
        wa.end(conn, Linked(user.customer_id, target, mine.id), "admin")
    background.add_task(_tell, context, "disconnected", lambda bot: bot.send_disconnected(number))
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
