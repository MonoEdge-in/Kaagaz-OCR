"""Every email Kaagaz sends (#457 phase 0), and the one way each is sent.

The texts: one function per email, returning ``(subject, text)``, plain text only. Each is
told a person's name, their organisation's name or a link, never a document, its name or
its content, and never an id (tests/test_email.py checks every one). A link email goes to an
address nobody has proven yet, so it is never told a name (anyone can type one at sign-up).

* A link email (confirm, invite, reset) carries a link of 32 random bytes, stored only as
  its SHA-256 hash (``email_links``, 0061), with a purpose, an expiry and a used time. The
  token travels in the URL fragment (``#t=``), so it never reaches a server or proxy log;
  the page reads it and posts it. A new link of one purpose for one user ends the older ones.
* A security notice (password changed or reset, two-step on, off or reset; a Google account
  connected, waiting, approved, declined or disconnected) has no link.
* "Waiting for your approval" goes to each admin when a member's Google account waits, with
  the link to the approval list (Admin, people) and nobody's name or email but their own.

``send`` never breaks the action that asked for the email: a failure is logged as
``{"event": "email_failed", "purpose": ...}``, never with the address, and it returns False.
"""

import hashlib
import json
import logging
import secrets
from collections.abc import Callable
from datetime import timedelta

from botocore.exceptions import BotoCoreError, ClientError

from kaagaz.api.context import AppContext
from kaagaz.db import email_links as links_sql
from kaagaz.db import users as users_sql

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


def notify(
    context: AppContext, customer_id: str, user_id: str, purpose: str, notice: Callable[[str], Message]
) -> None:
    """A security notice to the user's own e-mail, while email is on. One read, one send."""
    if not email_on(context):
        return
    with context.db.customer(customer_id) as conn:
        found = users_sql.contact(conn, customer_id, user_id)
    if found is not None:
        email, name = found
        send(context, email, purpose, notice(name))


# --- the texts ------------------------------------------------------------------------------


def confirm_email(link: str) -> Message:
    return (
        "Confirm your email for Kaagaz",
        f"Hello,\n\nConfirm your email address for Kaagaz by opening this link:\n\n{link}\n\n"
        "The link works once, for 24 hours. If you did not sign up for Kaagaz, you can ignore"
        " this email." + _SIGN_OFF,
    )


def invite(organisation: str, link: str) -> Message:
    return (
        "You're invited to Kaagaz",
        f"Hello,\n\nYou have been added to {organisation} on Kaagaz. Choose your password"
        f" here:\n\n{link}\n\nThe link works once, for 7 days. If you did not expect this, you can"
        " ignore this email." + _SIGN_OFF,
    )


def reset_password(link: str) -> Message:
    return (
        "Reset your Kaagaz password",
        f"Hello,\n\nSomeone asked to reset the password of your Kaagaz account. To choose a"
        f" new one, open this link:\n\n{link}\n\nThe link works once, for 30 minutes, and signs"
        " you out on every browser. If you did not ask for this, you can ignore this email: your"
        " password stays as it is." + _SIGN_OFF,
    )


def password_changed(name: str) -> Message:
    return (
        "Your Kaagaz password was changed",
        f"Hello {name},\n\nThe password of your Kaagaz account was just changed. If this was you,"
        " there is nothing to do.\n\nIf you didn't do this, or don't recognise a connected account,"
        " open Ways to sign in. If you can't sign in, ask your organisation's admin to reset your"
        " password now." + _SIGN_OFF,
    )


def password_was_reset(name: str) -> Message:
    return (
        "Your Kaagaz password was reset",
        f"Hello {name},\n\nThe password of your Kaagaz account was just reset with a link sent to"
        " this email address, and every browser was signed out. Connected sign-ins, such as a"
        " Google account, were removed too. If this was you, there is nothing to do; you can"
        " connect them again under Ways to sign in.\n\nIf it was not you, ask your organisation's"
        " admin to reset your password now." + _SIGN_OFF,
    )


def two_step_on(name: str) -> Message:
    return (
        "Two-step sign-in is on",
        f"Hello {name},\n\nTwo-step sign-in was just turned on for your Kaagaz account. From now"
        " on, signing in asks for the 6-digit code from your authenticator app.\n\nIf this was not"
        " you, tell your organisation's admin now." + _SIGN_OFF,
    )


def two_step_off(name: str) -> Message:
    return (
        "Two-step sign-in is off",
        f"Hello {name},\n\nTwo-step sign-in was just turned off for your Kaagaz account, and every"
        " other browser was signed out. From now on, your password alone signs you in.\n\nIf this"
        " was not you, tell your organisation's admin now." + _SIGN_OFF,
    )


def two_step_reset(name: str) -> Message:
    return (
        "Your Kaagaz two-step sign-in was reset",
        f"Hello {name},\n\nThe two-step code of your Kaagaz account was reset, and every browser"
        " was signed out. You can sign in with your password and set up two-step sign-in"
        " again.\n\nIf you did not ask for this, tell your organisation's admin now." + _SIGN_OFF,
    )


# --- Ways to sign in (#457 phase 1): a connected Google account ------------------------------


def google_connected(name: str) -> Message:
    return (
        "A Google account was connected",
        f"Hello {name},\n\nA Google account was just connected to your Kaagaz account. You can now"
        " sign in with it as well as with your password.\n\nIf this was not you, disconnect it"
        " under Account, Ways to sign in, change your password and tell your organisation's admin"
        " now." + _SIGN_OFF,
    )


def google_waiting(name: str) -> Message:
    return (
        "A Google account is waiting for approval",
        f"Hello {name},\n\nA Google account was just connected to your Kaagaz account. It can sign"
        " in once your admin approves it.\n\nIf this was not you, cancel it under Account, Ways to"
        " sign in, change your password and tell your organisation's admin now." + _SIGN_OFF,
    )


def google_approved(name: str) -> Message:
    return (
        "Your Google account was approved",
        f"Hello {name},\n\nYour admin approved the Google account connected to your Kaagaz account."
        " You can now sign in with it as well as with your password.\n\nIf you did not ask for"
        " this, tell your organisation's admin now." + _SIGN_OFF,
    )


def google_declined(name: str) -> Message:
    return (
        "Your Google account was declined",
        f"Hello {name},\n\nYour admin declined the Google account you asked to connect to your"
        " Kaagaz account, so it cannot sign in. Your password works as before." + _SIGN_OFF,
    )


def google_disconnected(name: str) -> Message:
    return (
        "A Google account was disconnected",
        f"Hello {name},\n\nThe Google account connected to your Kaagaz account was disconnected, so"
        " it can no longer sign in. Your password works as before.\n\nIf you did not expect this,"
        " tell your organisation's admin." + _SIGN_OFF,
    )


def connection_waiting(name: str, organisation: str, link: str) -> Message:
    """To each admin: someone asked to sign in with a Google account; who is not said here."""
    return (
        "Waiting for your approval",
        f"Hello {name},\n\nSomeone in {organisation} asked to sign in to Kaagaz with a Google"
        " account. It works only once an admin approves it. Open the approval list:\n\n"
        f"{link}\n\nIf you do not recognise the request, decline it." + _SIGN_OFF,
    )
