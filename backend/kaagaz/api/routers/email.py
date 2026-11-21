"""The emailed links (#457 phase 0). Every route here is 404 while email is off
(``EMAIL_ENABLED``, on the router itself, so a route added later cannot forget it).

* ``GET /auth/email``: ``{"enabled": true}`` (404, like every route here, while email is off).
* ``POST /auth/email/confirm`` {token}: the link from "Confirm your email" proves the
  e-mail (``users.email_verified_at``), audited ``auth.email_confirmed``. ``{"ok": true,
  "pending": bool}``: ``pending`` while the organisation is a sign-up waiting for approval.
* ``POST /auth/email/send-confirm`` (signed in): a new confirm link to the user's own
  e-mail, at most CONFIRMS_PER_HOUR a user (429). Older unused links stop working.
  ``{"ok": true, "sent": bool}``: ``sent`` is false when the email failed.
* ``POST /auth/email/resend-confirm`` {email} (no session): "Send the link again" for a
  sign-up's admin, who cannot sign in while their organisation is pending. Always 202 with
  the same words, and the work happens after the reply (a background task, so an unknown
  address takes the same path and time). A new confirm link goes only to an enabled user whose
  e-mail is not confirmed and whose organisation is ``pending``.

A link that is unknown, for something else, used or expired: 400 ``link_expired``, the
same words for each. A link is used in the same transaction that acts on it, by one UPDATE
that holds "unused, not expired", so it acts once even when sent twice at once.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from kaagaz.api import emails
from kaagaz.api.auth import User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.emails import email_on, new_link, send, token_hash
from kaagaz.api.errors import ApiError
from kaagaz.api.middleware import RateLimiter, client_address
from kaagaz.api.routers.admin import valid_email
from kaagaz.api.routers.auth import _per_user_limit
from kaagaz.db import audit
from kaagaz.db import customers as customers_sql
from kaagaz.db import email_links as links_sql
from kaagaz.db import users as users_sql
from kaagaz.db.email_links import LinkRow


def require_email(request: Request) -> None:
    if not email_on(get_context(request)):
        raise ApiError(404, "not_found")


router = APIRouter(dependencies=[Depends(require_email)])

Context = Annotated[AppContext, Depends(get_context)]

LINK_GONE = "That link has expired or was already used."
RESEND_SENT = "If that email is waiting to be confirmed, we've sent a new link."
# PROVISIONAL limits. Each visitor has their own address (uvicorn takes it from Caddy's
# X-Forwarded-For, backend/Dockerfile), so the per-address cap does the work (A1, 7 Nov 2026) and
# the site-wide one, 5x what it was, only stops a spread-out flood.
CONFIRMS_PER_HOUR = 3
LINKS_PER_HOUR_ADDRESS = 10  # resend-confirm
LINKS_PER_HOUR_SITE = 1500
FORGOTS_PER_HOUR_EMAIL = 3
SITE = "site"  # the one key of the site-wide limits
confirm_limits = RateLimiter(CONFIRMS_PER_HOUR, window_seconds=3600)
link_address_limits = RateLimiter(LINKS_PER_HOUR_ADDRESS, window_seconds=3600)
resend_site_limits = RateLimiter(LINKS_PER_HOUR_SITE, window_seconds=3600)
resend_email_limits = RateLimiter(FORGOTS_PER_HOUR_EMAIL, window_seconds=3600)
TOO_MANY_LINKS = "We sent a few links already. Try again in an hour."


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128)


class ForgotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)


@router.get("/auth/email")
def email_open() -> dict[str, bool]:
    return {"enabled": True}  # off: the router's 404


@router.post("/auth/email/confirm")
def confirm_email(body: TokenBody, context: Context) -> dict[str, bool]:
    link = _open_link(context, body.token, "confirm")
    now = context.clock()
    with context.db.customer(link.customer_id) as conn:
        _use(conn, link, context)
        users_sql.confirm_email(conn, link.customer_id, link.user_id, now=now)
        _record(conn, link, "auth.email_confirmed")
        pending = customers_sql.is_pending(conn, link.customer_id)
    return {"ok": True, "pending": pending}


@router.post("/auth/email/send-confirm")
def send_confirm(user: User, context: Context) -> dict[str, bool]:
    _per_user_limit(confirm_limits, user, TOO_MANY_LINKS)
    with context.db.user(user.customer_id, user.user_id) as conn:
        link = emails.add_link(conn, context, user.customer_id, user.user_id, "confirm")
    return {"ok": True, "sent": send(context, user.email, "confirm", emails.confirm_email(link))}


@router.post("/auth/email/resend-confirm", status_code=202)
def resend_confirm(
    body: ForgotBody, request: Request, background: BackgroundTasks, context: Context
) -> dict[str, str]:
    address = client_address(request)
    _link_later(background, context, body.email, address, "confirm", resend_site_limits, resend_email_limits)
    return {"message": RESEND_SENT}


def _link_later(
    background: BackgroundTasks,
    context: AppContext,
    email: str,
    address: str,
    purpose: str,
    site_limits: RateLimiter,
    email_limits: RateLimiter,
) -> None:
    """ "Send the link again"'s core: the visitor's and the site-wide limits (429), then the lookup and
    the email after the reply (``_send_link``), so every e-mail takes the same path and time."""
    if not link_address_limits.allow(address) or not site_limits.allow(SITE):
        raise ApiError(429, "rate_limited", TOO_MANY_LINKS, headers={"Retry-After": "3600"})
    email = valid_email(email)
    emails.after(background, purpose, _send_link, context, email, purpose, email_limits, f"{email}|{address}")


def _send_link(context: AppContext, email: str, purpose: str, email_limits: RateLimiter, key: str) -> None:
    """After the reply, within ``email_limits`` for ``key`` (the e-mail and the visitor's address):
    a new confirm link to an enabled, unconfirmed admin of a pending organisation."""
    if not email_limits.allow(key):
        return
    with context.db.system() as conn:
        found = users_sql.login_lookup(conn, email)
    if found is None or found.disabled:
        return
    with context.db.customer(found.customer_id) as conn:
        contact = users_sql.awaiting_confirm(conn, found.customer_id, found.user_id)
    if contact is None:  # removed a moment ago, or not waiting to confirm
        return
    link = new_link(context, found.customer_id, found.user_id, purpose)
    send(context, contact[0], purpose, emails.confirm_email(link))


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


def _record(
    conn: Connection, link: LinkRow, action: str, details: dict[str, audit.DetailValue] | None = None
) -> None:
    audit.record(
        conn,
        link.customer_id,
        actor_user_id=link.user_id,
        action=action,
        target_type="user",
        target_id=link.user_id,
        details=details,
    )
