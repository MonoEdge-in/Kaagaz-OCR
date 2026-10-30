"""Every email Kaagaz sends (#457 phase 0), and the one way each is sent.

The texts: one function per email, returning ``(subject, text)``, plain text only. Each is
told a person's name, their organisation's name or a link, never a document, its name or
its content, and never an id (tests/test_email.py checks every one).

* A link email (confirm, invite, reset) carries a link of 32 random bytes, stored only as
  its SHA-256 hash (``email_links``, 0061), with a purpose, an expiry and a used time. The
  token travels in the URL fragment (``#t=``), so it never reaches a server or proxy log;
  the page reads it and posts it. A new link of one purpose for one user ends the older ones.
* A security notice (password changed or reset, two-step on or reset) has no link.

``send`` never breaks the action that asked for the email: a failure is logged as
``{"event": "email_failed", "purpose": ...}``, never with the address, and it returns False.
"""

import hashlib
import json
import logging
import secrets
from datetime import timedelta

from botocore.exceptions import BotoCoreError, ClientError

from kaagaz.api.context import AppContext
from kaagaz.db import email_links as links_sql

log = logging.getLogger("kaagaz.api.emails")

# How long each link works, and the page it opens.
LIFETIMES = {"confirm": timedelta(hours=24), "invite": timedelta(days=7), "reset": timedelta(minutes=30)}
PAGES = {"confirm": "/email/confirm", "invite": "/invite", "reset": "/reset-password"}
TOKEN_BYTES = 32

Message = tuple[str, str]
_SIGN_OFF = "\n\nKaagaz"


def email_on(context: AppContext) -> bool:
    """EMAIL_ENABLED, and the mailer ``create_app`` built for it."""
    return context.settings.email_enabled and context.mailer is not None


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def new_link(context: AppContext, customer_id: str, user_id: str, purpose: str) -> str:
    """A new link of this purpose for this user (the older unused ones end), as the page's
    address with the token in its fragment. The token itself is never stored or logged."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = context.clock()
    with context.db.customer(customer_id) as conn:
        links_sql.add(
            conn,
            customer_id,
            user_id,
            purpose,
            token_hash(token),
            now=now,
            expires_at=now + LIFETIMES[purpose],
        )
    return f"{context.settings.app_url}{PAGES[purpose]}#t={token}"


def send(context: AppContext, to: str, purpose: str, message: Message) -> bool:
    """Send one email; False (logged by purpose only) when email is off or sending failed."""
    mailer = context.mailer
    if mailer is None or not context.settings.email_enabled:
        return False
    subject, text = message
    try:
        mailer.send(to=to, subject=subject, text=text)
    except (BotoCoreError, ClientError):
        log.warning(json.dumps({"event": "email_failed", "purpose": purpose}, separators=(",", ":")))
        return False
    return True


# --- the texts ------------------------------------------------------------------------------


def confirm_email(name: str, link: str) -> Message:
    return (
        "Confirm your email for Kaagaz",
        f"Hello {name},\n\nConfirm your email address for Kaagaz by opening this link:\n\n{link}\n\n"
        "The link works once, for 24 hours. If you did not sign up for Kaagaz, you can ignore"
        " this email." + _SIGN_OFF,
    )


def invite(name: str, organisation: str, link: str) -> Message:
    return (
        "You're invited to Kaagaz",
        f"Hello {name},\n\nYou have been added to {organisation} on Kaagaz. Choose your password"
        f" here:\n\n{link}\n\nThe link works once, for 7 days. If you did not expect this, you can"
        " ignore this email." + _SIGN_OFF,
    )


def reset_password(name: str, link: str) -> Message:
    return (
        "Reset your Kaagaz password",
        f"Hello {name},\n\nSomeone asked to reset the password of your Kaagaz account. To choose a"
        f" new one, open this link:\n\n{link}\n\nThe link works once, for 30 minutes, and signs"
        " you out on every browser. If you did not ask for this, you can ignore this email: your"
        " password stays as it is." + _SIGN_OFF,
    )


def password_changed(name: str) -> Message:
    return (
        "Your Kaagaz password was changed",
        f"Hello {name},\n\nThe password of your Kaagaz account was just changed. If this was you,"
        " there is nothing to do.\n\nIf it was not you, ask your organisation's admin to reset"
        " your password now." + _SIGN_OFF,
    )


def password_was_reset(name: str) -> Message:
    return (
        "Your Kaagaz password was reset",
        f"Hello {name},\n\nThe password of your Kaagaz account was just reset with a link sent to"
        " this email address, and every browser was signed out. If this was you, there is nothing"
        " to do.\n\nIf it was not you, ask your organisation's admin to reset your password now." + _SIGN_OFF,
    )


def two_step_on(name: str) -> Message:
    return (
        "Two-step sign-in is on",
        f"Hello {name},\n\nTwo-step sign-in was just turned on for your Kaagaz account. From now"
        " on, signing in asks for the 6-digit code from your authenticator app.\n\nIf this was not"
        " you, tell your organisation's admin now." + _SIGN_OFF,
    )


def two_step_reset(name: str) -> Message:
    return (
        "Your Kaagaz two-step sign-in was reset",
        f"Hello {name},\n\nThe two-step code of your Kaagaz account was reset, and every browser"
        " was signed out. You can sign in with your password and set up two-step sign-in"
        " again.\n\nIf you did not ask for this, tell your organisation's admin now." + _SIGN_OFF,
    )
