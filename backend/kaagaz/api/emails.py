"""Every email Kaagaz sends (#457 phase 0), and the one way each is sent.

The texts: one function per email, returning ``(subject, text)``, plain text only. Each is
told a person's name, their organisation's name or a link, never a document, its name or
its content, and never an id (tests/test_email.py checks every one). A link email goes to an
address nobody has proven yet, so it is never told a name (anyone can type one at sign-up).

* A link email (confirm, invite, reset) carries a link of 32 random bytes, stored only as
  its SHA-256 hash (``email_links``, 0061), with a purpose, an expiry and a used time. The
  token travels in the URL fragment (``#t=``), so it never reaches a server or proxy log;
  the page reads it and posts it. A new link of one purpose for one user ends the older ones.
* A security notice (password changed or reset, two-step on, off or reset; a WhatsApp number
  connected; a Google account connected, waiting, approved, declined or disconnected) has no link.
* "Waiting for your approval" goes to each admin when a member's Google account waits, with
  the link to the approval list (Admin, people) and nobody's name or email but their own.
* "WhatsApp was turned on" (#603) goes to Management and every other admin, with no link.

``send`` never breaks the action that asked for the email: a failure is logged as
``{"event": "email_failed", "purpose": ..., "error": SES's code or the type}``, never with the
address (an invite's at error level), and it returns False.
A security notice, the admins' email and the forgot-password lookup run after the reply
(``after``), so nothing there, the database's failure included, can break a finished action.
"""

import hashlib
import json
import logging
import secrets
from collections.abc import Callable
from datetime import timedelta

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import BackgroundTasks
from sqlalchemy import Connection

from kaagaz.api.context import AppContext
from kaagaz.db import email_links as links_sql
from kaagaz.db import users as users_sql

log = logging.getLogger("kaagaz.api.emails")

# How long each link works, and the page it opens.
LIFETIMES = {"confirm": timedelta(hours=24), "reset": timedelta(minutes=30)}
PAGES = {"confirm": "/email/confirm", "reset": "/reset-password"}
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
    with context.db.customer(customer_id) as conn:
        return add_link(conn, context, customer_id, user_id, purpose)


def add_link(conn: Connection, context: AppContext, customer_id: str, user_id: str, purpose: str) -> str:
    """``new_link`` in the caller's transaction (``conn`` from ``Database.customer``, or
    ``Database.user`` for the signed-in user's own link)."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = context.clock()
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
    if mailer is None or not email_on(context):
        return False
    subject, text = message
    try:
        mailer.send(to=to, subject=subject, text=text)
    except (BotoCoreError, ClientError) as error:
        # Why: SES's own error code, else the exception's type; an invite's failure is an error
        # (the person waits for it), the rest warnings.
        why = error.response.get("Error", {}).get("Code") if isinstance(error, ClientError) else None
        failed = {"event": "email_failed", "purpose": purpose, "error": why or type(error).__name__}
        log.log(
            logging.ERROR if purpose == "invite" else logging.WARNING,
            json.dumps(failed, separators=(",", ":")),
        )
        return False
    return True


def after(background: BackgroundTasks, purpose: str, task: Callable[..., object], *args: object) -> None:
    """Run ``task(*args)`` once the reply has gone. The action it follows is finished, so a
    failure there is logged as ``email_task_failed`` {purpose, error: its type}, never raised."""
    background.add_task(_logged, purpose, task, *args)


def _logged(purpose: str, task: Callable[..., object], *args: object) -> None:
    try:
        task(*args)
    except Exception as error:  # noqa: BLE001 - the reply has gone: logged (its type only), never raised
        failed = {"event": "email_task_failed", "purpose": purpose, "error": type(error).__name__}
        log.error(json.dumps(failed, separators=(",", ":")))


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


def two_step_reset(name: str) -> Message:
    return (
        "Your Kaagaz two-step sign-in was reset",
        f"Hello {name},\n\nThe two-step code of your Kaagaz account was reset, and every browser"
        " was signed out. You can sign in with your password and set up two-step sign-in"
        " again.\n\nIf you did not ask for this, tell your organisation's admin now." + _SIGN_OFF,
    )


# --- Ways to sign in (#457 phase 1): a connected Google account ------------------------------


def whatsapp_connected(name: str) -> Message:
    """#603: a security notice, as Google's: a WhatsApp number was connected to the account."""
    return (
        "WhatsApp was connected to your Kaagaz account",
        f"Hello {name},\n\nA WhatsApp number was connected to your Kaagaz account. From it you can ask"
        " about your files and get links to them. Anyone who can open WhatsApp on that phone can do the"
        " same, so keep the phone locked.\n\nIf you did not do this, disconnect it in Kaagaz, Account,"
        " WhatsApp, change your password and tell your organisation's admin." + _SIGN_OFF,
    )


def whatsapp_turned_on(name: str, organisation: str) -> Message:
    """#603: to Management and the other admins when an admin turns WhatsApp on; who did is in the log."""
    return (
        "WhatsApp was turned on for your organisation",
        f"Hello {name},\n\nAn admin turned on Kaagaz on WhatsApp for {organisation}. People whose role"
        " is allowed can now connect their WhatsApp number, ask about their files and get links to them."
        " Files never leave Kaagaz: each link needs a Kaagaz sign-in. Answers already sent stay on the"
        " person's phone, even after their access is removed.\n\nAdmins can turn it off in Admin,"
        " Settings." + _SIGN_OFF,
    )


# --- who can see what (PRD 7, #601) -----------------------------------------------------------

# The kind of access given, in words: never a file, a folder or a department by name.
ACCESS_KINDS = {
    "management": "Management, which sees every file but locked ones",
    "member": "a place in a department",
    "head": "the Head tick of a department",
    "named": "a place on a private folder's list",
    "file": "a file, given directly",
    "wall": "access to files by opening them to more people",
    "lock": "a locked file to open",  # PRD 7 (#605): never which one
}


def access_given(name: str, organisation: str, admin: str, person: str | None, kind: str) -> Message:
    """To each Management person and each other admin: ``admin`` gave ``person`` (None:
    themselves) a kind of access (``ACCESS_KINDS``)."""
    who = "themselves" if person is None else person
    return (
        "Access was given in Kaagaz",
        f"Hello {name},\n\n{admin} gave {who} {ACCESS_KINDS[kind]} in {organisation} on Kaagaz."
        "\n\nThe Activity tab shows who has access. If you do not expect this, ask your"
        " organisation's admins." + _SIGN_OFF,
    )
