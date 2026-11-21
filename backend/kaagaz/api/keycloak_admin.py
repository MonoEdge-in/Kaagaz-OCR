"""Keycloak's admin REST API for company sign-in accounts (#737).

Kaagaz makes, disables, unlocks and deletes people in the one configured realm with the
``kaagaz-provisioner`` service account (client credentials; it may only manage users). Same
rules as ``keycloak_oidc``: issuer-pinned URLs, no proxy, no redirects, short timeouts, bounded
replies. A token, a secret, an email or a Keycloak reply is never logged or put in an error: every
failure is one ``KeycloakAdminError`` with a plain message for the admin.

An existing person is never taken over: ``find_user_by_email`` lets the caller refuse, and
``create_user`` refuses a taken email (``AlreadyExists``).
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from kaagaz.api.keycloak_oidc import _OPENER, MAX_REPLY_BYTES, TIMEOUT_SECONDS
from kaagaz.settings import Settings

SETUP_ACTIONS = ("UPDATE_PASSWORD", "CONFIGURE_TOTP")
SETUP_LINK_SECONDS = 12 * 60 * 60  # "It works for 12 hours."
NOT_SET_UP = "Company sign-in is not set up on this server. Ask your super admin."
UNREACHABLE = "We could not reach the company sign-in service. Try again in a minute."

logger = logging.getLogger(__name__)

_token: tuple[float, str] | None = None  # (monotonic expiry, access token)


class KeycloakAdminError(Exception):
    """A plain message safe to show; nothing from Keycloak is in it."""

    def __init__(self, message: str = UNREACHABLE) -> None:
        super().__init__(message)
        self.message = message


class AlreadyExists(KeycloakAdminError):
    def __init__(self) -> None:
        super().__init__("This email already has a company sign-in account.")


def configured(settings: Settings) -> bool:
    return bool(
        settings.keycloak_enabled
        and settings.keycloak_issuer
        and settings.keycloak_admin_client_id
        and settings.keycloak_admin_client_secret
    )


def find_user_by_email(settings: Settings, email: str) -> str | None:
    """The Keycloak id of the account with exactly this email, or None."""
    query = urllib.parse.urlencode({"email": email, "exact": "true"})
    found = _call(settings, "GET", f"/users?{query}")
    for entry in found if isinstance(found, list) else []:
        if isinstance(entry, dict) and str(entry.get("email", "")).lower() == email.lower():
            return _id(entry.get("id"))
    return None


def create_user(settings: Settings, *, email: str, name: str) -> str:
    """Make an enabled account that must set a password and an authenticator; its id."""
    first, _, last = name.strip().partition(" ")
    body = {
        "username": email,
        "email": email,
        "firstName": first,
        "lastName": last.strip() or first,
        "enabled": True,
        "emailVerified": True,
        "requiredActions": list(SETUP_ACTIONS),
    }
    location = _call(settings, "POST", "/users", body, want_location=True, conflict=AlreadyExists)
    if not isinstance(location, str):
        raise KeycloakAdminError()
    return _id(location.rstrip("/").rsplit("/", 1)[-1])


def send_setup_email(settings: Settings, user_id: str) -> None:
    """Email the person a link to set a password and an authenticator (works 12 hours)."""
    query = urllib.parse.urlencode(
        {
            "client_id": settings.keycloak_client_id or "",
            "redirect_uri": _login_page(settings),
            "lifespan": str(SETUP_LINK_SECONDS),
        }
    )
    _call(settings, "PUT", f"/users/{_id(user_id)}/execute-actions-email?{query}", list(SETUP_ACTIONS))


def setup_unfinished(settings: Settings, user_id: str) -> bool:
    """True while the person still has a required action (password, authenticator) or no authenticator.

    Resending the setup email to someone who finished would put those actions back on a working
    account, so callers refuse unless this is true."""
    found = _call(settings, "GET", f"/users/{_id(user_id)}")
    if isinstance(found, dict) and found.get("requiredActions"):
        return True
    credentials = _call(settings, "GET", f"/users/{_id(user_id)}/credentials")
    return not any(isinstance(c, dict) and c.get("type") == "otp" for c in credentials or [])


def remove_otp(settings: Settings, user_id: str) -> None:
    """Remove their authenticator; Keycloak asks them to set one up at the next sign-in."""
    path = f"/users/{_id(user_id)}/credentials"
    for credential in _call(settings, "GET", path) or []:
        if isinstance(credential, dict) and credential.get("type") == "otp":
            _call(settings, "DELETE", f"{path}/{_id(credential.get('id'))}", missing_ok=True)


def set_enabled(settings: Settings, user_id: str, enabled: bool) -> None:
    _call(settings, "PUT", f"/users/{_id(user_id)}", {"enabled": enabled})


def delete_user(settings: Settings, user_id: str) -> None:
    _call(settings, "DELETE", f"/users/{_id(user_id)}", missing_ok=True)


def clear_lock(settings: Settings, user_id: str) -> None:
    """End Keycloak's wrong-password lock for this person."""
    _call(settings, "DELETE", f"/attack-detection/brute-force/users/{_id(user_id)}", missing_ok=True)


def _id(value: object) -> str:
    text = value if isinstance(value, str) else ""
    if not 0 < len(text) <= 255 or not all(char.isalnum() or char == "-" for char in text):
        raise KeycloakAdminError()
    return text


def _base(settings: Settings) -> str:
    issuer = settings.keycloak_issuer or ""
    head, marker, realm = issuer.partition("/realms/")
    if not configured(settings) or not marker or not realm or "/" in realm:
        raise KeycloakAdminError(NOT_SET_UP)
    if settings.keycloak_admin_url:
        return f"{settings.keycloak_admin_url}/admin/realms/{realm}"
    return f"{head}/admin/realms/{realm}"


def _token_url(settings: Settings) -> str:
    """The issuer's token endpoint, or the same realm's at KEYCLOAK_ADMIN_URL (the server's Docker
    address). The realm is checked by ``_base``, which every call makes first."""
    if settings.keycloak_admin_url:
        realm = (settings.keycloak_issuer or "").rpartition("/realms/")[2]
        return f"{settings.keycloak_admin_url}/realms/{realm}/protocol/openid-connect/token"
    return f"{settings.keycloak_issuer}/protocol/openid-connect/token"


def _access_token(settings: Settings) -> str:
    global _token
    if _token is not None and time.monotonic() < _token[0]:
        return _token[1]
    url = _token_url(settings)
    form = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": settings.keycloak_admin_client_id or "",
            "client_secret": settings.keycloak_admin_client_secret or "",
        }
    ).encode("ascii")
    request = urllib.request.Request(  # noqa: S310 - issuer-pinned by settings
        url, form, {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    )
    try:
        reply = _send(request)[0]
    except _Refused as refused:
        logger.warning("keycloak token request refused: status %s", refused.status)
        raise KeycloakAdminError() from None
    token = reply.get("access_token") if isinstance(reply, dict) else None
    lifetime = reply.get("expires_in") if isinstance(reply, dict) else None
    if not isinstance(token, str) or not token or type(lifetime) is not int:
        raise KeycloakAdminError()
    _token = (time.monotonic() + max(lifetime - 30, 0), token)
    return token


def _call(
    settings: Settings,
    method: str,
    path: str,
    body: object = None,
    *,
    want_location: bool = False,
    missing_ok: bool = False,
    conflict: type[KeycloakAdminError] | None = None,
) -> Any:
    global _token
    url = _base(settings) + path
    for attempt in (1, 2):
        headers = {"Accept": "application/json", "Authorization": f"Bearer {_access_token(settings)}"}
        data = None if body is None else json.dumps(body).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data, headers, method=method)  # noqa: S310
        try:
            reply, location = _send(request)
        except _Refused as refused:
            logger.warning("keycloak admin call refused: %s status %s", method, refused.status)
            if refused.status == 401 and attempt == 1:
                _token = None  # an expired token: ask once more
                continue
            if refused.status == 404 and missing_ok:
                return None
            if refused.status == 409 and conflict is not None:
                raise conflict from None
            raise KeycloakAdminError() from None
        return location if want_location else reply
    raise KeycloakAdminError()


class _Refused(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def _send(request: urllib.request.Request) -> tuple[Any, str | None]:
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_REPLY_BYTES + 1)
            location = response.headers.get("Location")
    except urllib.error.HTTPError as error:
        error.close()
        raise _Refused(error.code) from None
    except (OSError, ValueError):
        raise KeycloakAdminError() from None
    if len(body) > MAX_REPLY_BYTES:
        raise KeycloakAdminError()
    if not body:
        return None, location
    try:
        return json.loads(body), location
    except (ValueError, UnicodeError):
        raise KeycloakAdminError() from None


def _login_page(settings: Settings) -> str:
    """Where "back to application" leads after setup: the site's sign-in page, exactly the origin of
    the configured callback plus /login (the realm's client must allow it)."""
    parts = urllib.parse.urlsplit(settings.keycloak_redirect_uri or "")
    return f"{parts.scheme}://{parts.netloc}/login"
