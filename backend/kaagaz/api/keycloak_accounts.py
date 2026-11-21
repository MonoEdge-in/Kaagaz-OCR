"""Company sign-in accounts that Kaagaz makes and keeps in step with Keycloak (#737).

Rules, in plain words:

* Create only. An email that already has a Keycloak account is never linked automatically
  (``AccountExists``); the caller tells the admin in plain words.
* Link only an account this code just made. Admin actions then act on the LINKED account (its
  subject, read through ``kaagaz_keycloak_subjects``), never on whoever has the same email.
* Kaagaz and Keycloak cannot share a transaction, so the order is: check the email, make the
  Keycloak account, write and link the Kaagaz user in one transaction, then send the setup email.
  If the Kaagaz write fails the new Keycloak account is deleted again; if only the email fails,
  the person is linked and "Resend setup email" finishes the job. A retry is always safe.
* Nothing here runs while Keycloak is off or has no provisioner secret: ``require`` says so.
"""

import logging
from collections.abc import Callable, Iterable

from kaagaz.api import keycloak_admin as kc
from kaagaz.api.context import AppContext
from kaagaz.api.errors import ApiError
from kaagaz.db import keycloak as keycloak_sql
from kaagaz.settings import Settings

logger = logging.getLogger(__name__)

NOT_SET_UP = "Company sign-in is not set up on this server."
EMAIL_NOT_SENT = "Added, but the setup email could not be sent. Use Resend setup email."
NOT_LINKED = "This person is not on company sign-in yet."


class AccountExists(Exception):
    """The email already has a Keycloak account: never taken over."""


def enabled(context: AppContext) -> bool:
    return kc.configured(context.settings)


def require(context: AppContext) -> None:
    if not enabled(context):
        raise ApiError(409, "conflict", NOT_SET_UP)


def sent_line(email: str, verb: str = "Added") -> str:
    return (
        f"{verb}. We emailed {email} a link to set their password and authenticator. It works for 12 hours."
    )


def _failed(error: kc.KeycloakAdminError) -> ApiError:
    return ApiError(502, "keycloak_unavailable", error.message)


def ensure_free(context: AppContext, email: str) -> None:
    """AccountExists when Keycloak already has this email; nothing is made."""
    require(context)
    try:
        if kc.find_user_by_email(context.settings, email) is not None:
            raise AccountExists
    except kc.KeycloakAdminError as error:
        raise _failed(error) from None


def make_account(context: AppContext, *, email: str, name: str) -> str:
    """Step 1: refuse a taken email, else make the Keycloak account; its id."""
    ensure_free(context, email)
    try:
        return kc.create_user(context.settings, email=email, name=name)
    except kc.AlreadyExists:
        raise AccountExists from None
    except kc.KeycloakAdminError as error:
        raise _failed(error) from None


def discard(context: AppContext, subject: str) -> None:
    """Undo ``make_account`` after a Kaagaz failure. Best effort: a leftover account has no link,
    so it can sign in nowhere, and the next try finds its email taken and says so."""
    try:
        kc.delete_user(context.settings, subject)
    except kc.KeycloakAdminError:
        logger.error("could not remove an unlinked company sign-in account")


def send_setup(context: AppContext, subject: str) -> bool:
    try:
        kc.send_setup_email(context.settings, subject)
    except kc.KeycloakAdminError:
        logger.warning("the company sign-in setup email was not sent")
        return False
    return True


def link_new(context: AppContext, customer_id: str, user_id: str, *, email: str, name: str) -> str:
    """For a Kaagaz user that exists without a link (Move everyone, a new organisation's first
    admin): make the account, link it, send the setup email. "sent" or "email_failed";
    AccountExists, or ApiError when Keycloak or the link fails (nothing is left half-made)."""
    subject = make_account(context, email=email, name=name)
    try:
        with context.db.user(customer_id, user_id) as conn:
            keycloak_sql.link_account(
                conn, context.settings.keycloak_issuer or "", subject, now=context.clock()
            )
    except Exception:
        discard(context, subject)
        raise
    return "sent" if send_setup(context, subject) else "email_failed"


def subject_of(context: AppContext, customer_id: str, user_id: str) -> str | None:
    with context.db.customer(customer_id) as conn:
        return keycloak_sql.subject_of(conn, user_id, context.settings.keycloak_issuer or "")


def act(
    context: AppContext,
    customer_id: str,
    user_id: str,
    action: Callable[[Settings, str], None],
    *,
    needs_link: bool = True,
) -> bool:
    """Run one Keycloak action on a person's LINKED account. False: no link (nothing done), and
    ``needs_link`` raises 409 instead. A no-op while Keycloak is off unless ``needs_link``."""
    if not needs_link and not enabled(context):
        return False
    require(context)
    subject = subject_of(context, customer_id, user_id)
    if subject is None:
        if needs_link:
            raise ApiError(409, "conflict", NOT_LINKED)
        return False
    try:
        action(context.settings, subject)
    except kc.KeycloakAdminError as error:
        raise _failed(error) from None
    return True


def mirror_enabled(
    context: AppContext, customer_id: str, *, enabled_now: bool, only: Iterable[str] | None = None
) -> int:
    """Switch the linked accounts of some people (``only``) or the whole organisation on or off.

    Switching an organisation on leaves people an admin switched off still off. Returns how many
    were changed; 502 after trying everyone when any failed (the Kaagaz change is kept, a second
    try finishes). Does nothing while Keycloak is off."""
    if not enabled(context):
        return 0
    wanted = None if only is None else set(only)
    with context.db.customer(customer_id) as conn:
        issuer = context.settings.keycloak_issuer or ""
        people = keycloak_sql.people(conn, customer_id, issuer)
        subjects = keycloak_sql.subjects(conn, issuer)
    changed = failed = 0
    for person in people:
        subject = subjects.get(person.user_id)
        if subject is None or (wanted is not None and person.user_id not in wanted):
            continue
        if enabled_now and person.disabled and wanted is None:
            continue
        try:
            kc.set_enabled(context.settings, subject, enabled_now)
            changed += 1
        except kc.KeycloakAdminError:
            failed += 1
    if failed:
        raise ApiError(
            502,
            "keycloak_unavailable",
            f"Saved here, but {failed} sign-in account(s) could not be updated. Try the same action again.",
        )
    return changed
