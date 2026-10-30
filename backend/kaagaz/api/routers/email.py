"""The emailed links (#457 phase 0). Every route here is 404 while email is off
(``EMAIL_ENABLED``, on the router itself, so a route added later cannot forget it).

* ``POST /auth/email/confirm`` {token}: the link from "Confirm your email" proves the
  e-mail (``users.email_verified_at``), audited ``auth.email_confirmed``. ``{"ok": true}``.
* ``POST /auth/email/send-confirm`` (signed in): a new confirm link to the user's own
  e-mail, at most CONFIRMS_PER_HOUR a user (429). Older unused links stop working.
* ``POST /auth/forgot`` {email}: always 202 with the same words, so it never shows who has
  an account. The lookup and the email happen after the reply (a background task), so an
  unknown address takes the same path and time. At most FORGOT_PER_HOUR_ADDRESS a client
  address (429) and FORGOTS_PER_HOUR_EMAIL an e-mail (the rest are answered the same and
  send nothing). Only organisation users are found (``kaagaz_login_lookup``), never an
  operator, and never a disabled user.
* ``POST /auth/reset`` {token, new_password}: the password from a reset link. It clears a
  temporary password, proves the e-mail, raises the session version (every browser is
  signed out), is audited ``auth.password_reset`` and sends a security notice. No session
  starts: the page sends them to sign in, and an admin still types their code.
* ``POST /auth/invite/accept`` {token, new_password}: the same for an invite from an admin
  (``routers/admin.py``), audited ``auth.invite_accepted``.

A link that is unknown, for something else, used or expired: 400 ``link_expired``, the
same words for each. A link is used in the same transaction that acts on it, by one UPDATE
that holds "unused, not expired", so it acts once even when sent twice at once.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from kaagaz.api import emails
from kaagaz.api.auth import User, hash_password
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.emails import email_on, new_link, send, token_hash
from kaagaz.api.errors import ApiError
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers.admin import MAX_PASSWORD, MIN_PASSWORD, valid_email
from kaagaz.db import audit
from kaagaz.db import email_links as links_sql
from kaagaz.db import users as users_sql
from kaagaz.db.email_links import LinkRow


def require_email(request: Request) -> None:
    if not email_on(get_context(request)):
        raise ApiError(404, "not_found")


router = APIRouter(dependencies=[Depends(require_email)])

Context = Annotated[AppContext, Depends(get_context)]

LINK_GONE = "That link has expired or was already used."
FORGOT_SENT = "If that email has an account, we've sent a link."
# PROVISIONAL limits. Behind the Next.js proxy every visitor shares the proxy's address
# (kaagaz/api/middleware.py), so the per-address one is one cap for the whole site for now.
# ponytail: site-wide cap; per-visitor once X-Forwarded-For is trusted (as sign-up's).
CONFIRMS_PER_HOUR = 3
FORGOT_PER_HOUR_ADDRESS = 10
FORGOTS_PER_HOUR_EMAIL = 3
confirm_limits = RateLimiter(CONFIRMS_PER_HOUR, window_seconds=3600)
forgot_ip_limits = RateLimiter(FORGOT_PER_HOUR_ADDRESS, window_seconds=3600)
forgot_email_limits = RateLimiter(FORGOTS_PER_HOUR_EMAIL, window_seconds=3600)
TOO_MANY_LINKS = "We sent a few links already. Try again in an hour."


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128)


class NewPasswordBody(TokenBody):
    new_password: str = Field(min_length=MIN_PASSWORD, max_length=MAX_PASSWORD)


class ForgotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)


@router.post("/auth/email/confirm")
def confirm_email(body: TokenBody, context: Context) -> dict[str, bool]:
    link = _open_link(context, body.token, "confirm")
    now = context.clock()
    with context.db.customer(link.customer_id) as conn:
        _use(conn, link, context)
        users_sql.confirm_email(conn, link.customer_id, link.user_id, now=now)
        _record(conn, link, "auth.email_confirmed")
    return {"ok": True}


@router.post("/auth/email/send-confirm")
def send_confirm(user: User, context: Context) -> dict[str, bool]:
    if not confirm_limits.allow(user.user_id):
        raise ApiError(429, "rate_limited", TOO_MANY_LINKS, headers={"Retry-After": "3600"})
    link = new_link(context, user.customer_id, user.user_id, "confirm")
    send(context, user.email, "confirm", emails.confirm_email(user.display_name, link))
    return {"ok": True}


@router.post("/auth/forgot", status_code=202)
def forgot_password(
    body: ForgotBody, request: Request, background: BackgroundTasks, context: Context
) -> dict[str, str]:
    if not forgot_ip_limits.allow(request.client.host if request.client else "-"):
        raise ApiError(429, "rate_limited", TOO_MANY_LINKS, headers={"Retry-After": "3600"})
    background.add_task(_send_reset, context, valid_email(body.email))
    return {"message": FORGOT_SENT}


@router.post("/auth/reset")
def reset_password(body: NewPasswordBody, context: Context) -> dict[str, bool]:
    link = _set_password(context, body, "reset", "auth.password_reset")
    emails.notify(context, link.customer_id, link.user_id, "password_reset", emails.password_was_reset)
    return {"ok": True}


@router.post("/auth/invite/accept")
def accept_invite(body: NewPasswordBody, context: Context) -> dict[str, bool]:
    _set_password(context, body, "invite", "auth.invite_accepted")
    return {"ok": True}


def _send_reset(context: AppContext, email: str) -> None:
    """After the reply: a reset link to an enabled organisation user with this e-mail."""
    if not forgot_email_limits.allow(email):
        return
    with context.db.system() as conn:
        found = users_sql.login_lookup(conn, email)
    if found is None or found.disabled:
        return
    with context.db.customer(found.customer_id) as conn:
        contact = users_sql.contact(conn, found.customer_id, found.user_id)
    if contact is None:  # removed a moment ago
        return
    link = new_link(context, found.customer_id, found.user_id, "reset")
    send(context, contact[0], "reset", emails.reset_password(contact[1], link))


def _set_password(context: AppContext, body: NewPasswordBody, purpose: str, action: str) -> LinkRow:
    """The user's own new password from a reset or invite link, with the e-mail proven."""
    link = _open_link(context, body.token, purpose)
    password_hash = hash_password(body.new_password)  # before the transaction, never during it
    with context.db.customer(link.customer_id) as conn:
        _use(conn, link, context)
        users_sql.set_password(
            conn, link.customer_id, link.user_id, password_hash=password_hash, must_change=False
        )
        users_sql.confirm_email(conn, link.customer_id, link.user_id, now=context.clock())
        _record(conn, link, action)
    return link


def _open_link(context: AppContext, token: str, purpose: str) -> LinkRow:
    """The unused, unexpired link of this purpose with this token, else the one 400."""
    with context.db.system() as conn:
        link = links_sql.lookup(conn, token_hash(token))
    if (
        link is None
        or link.purpose != purpose
        or link.used_at is not None
        or link.expires_at <= context.clock()
    ):
        raise ApiError(400, "link_expired", LINK_GONE)
    return link


def _use(conn: Connection, link: LinkRow, context: AppContext) -> None:
    """Use the link up in this transaction; a request a moment ago used it: the one 400."""
    if not links_sql.use(conn, link.customer_id, link.link_id, now=context.clock()):
        raise ApiError(400, "link_expired", LINK_GONE)


def _record(conn: Connection, link: LinkRow, action: str) -> None:
    audit.record(
        conn,
        link.customer_id,
        actor_user_id=link.user_id,
        action=action,
        target_type="user",
        target_id=link.user_id,
    )
