"""Google sign-in by organisation and role (#507), the sign-in side, against a made-up Google.

* The Google callback's order: locked, disabled, the organisation off (its own or the
  platform's), the role, the domains, a waiting link, ``password_first``; each audited with
  ``reason``, ``via`` and (domains only) the email's domain, nothing more.
* ``password_refused``: a "Google only" role with a passing link, only after a right password,
  with an unknown email's reply and time otherwise. An admin still setting up two-step enrols.
* The limited session: a "Google only" user with no passing link reaches only who they are,
  sign out, and connecting Google; found by walking every route of the app.
* Connect: the organisation, the role (start and callback) and the domains (callback).
* Disconnect and an admin's reset fall back to the limited session; disabled, deleting and
  locked are refused and audited."""

import base64
import hashlib
import urllib.parse
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import text

from kaagaz.api.auth import SESSION_COOKIE, current_user, spend_password_time
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers import oidc as oidc_router
from kaagaz.db import identities
from kaagaz.db.engine import Database
from tests.oidc_fake import ISSUER, FakeGoogle, google_settings, use_google
from tests.test_api_support import (
    ANY_ID,
    CSRF,
    EMAIL_ROUTES,
    PASSWORD,
    Api,
    Signed,
    TestClient,
    call,
    route_calls,
)
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

DOMAIN = "sharmatraders.com"
WAYS = "/settings/sign-in"
LINK_REQUIRED = "google_link_required"


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    for name in ("callback_limits", "connect_limits", "remove_limits"):
        monkeypatch.setattr(oidc_router, name, RateLimiter(1000, window_seconds=3600))
    return use_google(monkeypatch)


@pytest.fixture
def platform(db: Database) -> Iterator[Callable[[bool], None]]:
    """Sets the platform's Google default for one test, then puts it back on."""

    def set_to(on: bool) -> None:
        _owner(db, "UPDATE platform_ai_settings SET google_signin = :on", {"on": on})

    yield set_to
    set_to(True)


def _api(db: Database) -> Api:
    return Api(db, settings=google_settings())


def _now(api: Api) -> int:
    return int(api.clock().timestamp())


def _rules(
    api: Api,
    customer_id: str,
    *,
    roles: list[str] | None = None,
    only: list[str] | None = None,
    domains: list[str] | None = None,
) -> None:
    """The org admin's rules, as the app may write them (RLS, its column grants)."""
    with api.db.customer(customer_id) as conn:
        conn.execute(
            text(
                "UPDATE customers SET google_roles = COALESCE(CAST(:r AS text[]), google_roles),"
                " google_only_roles = COALESCE(CAST(:o AS text[]), google_only_roles),"
                " google_domains = COALESCE(CAST(:d AS text[]), google_domains) WHERE id = :c"
            ),
            {"r": roles, "o": only, "d": domains, "c": customer_id},
        )


def _org(api: Api, customer_id: str, on: bool | None) -> None:
    """The super admin's per-organisation switch: the app may not write it."""
    _owner(api.db, "UPDATE customers SET google_signin = :on WHERE id = :c", {"on": on, "c": customer_id})


def _link(
    api: Api,
    customer_id: str,
    user_id: str,
    *,
    email: str = f"ramesh@{DOMAIN}",
    hd: str | None = DOMAIN,
    approved: bool = True,
) -> str:
    """A Google account connected to this user; its subject."""
    subject = f"made-up-sub-{uuid.uuid4().hex}"
    with api.db.customer(customer_id) as conn:
        identities.add(
            conn,
            customer_id,
            user_id,
            provider="google",
            issuer=ISSUER,
            subject=subject,
            email=email,
            approved=approved,
            approved_by=None,
            now=api.clock(),
            hosted_domain=hd,
        )
    return subject


def _google(
    api: Api, google: FakeGoogle, subject: str, email: str = f"Ramesh@{DOMAIN}", **claims: Any
) -> httpx.Response:
    """Continue with Google from the sign-in page, choosing this account."""
    client = api.client()
    start = client.get("/api/auth/oidc/google/start", params={"next": "/documents"}, follow_redirects=False)
    claims.setdefault("hd", DOMAIN)
    query = google.sign_in(start.headers["location"], subject, email, _now(api), **claims)
    return client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)


def _error(reply: httpx.Response) -> str | None:
    assert reply.status_code == 303, reply.text
    location = urllib.parse.urlsplit(reply.headers["location"])
    return dict(urllib.parse.parse_qsl(location.query)).get("oidc_error")


def _login(
    client: TestClient, email: str, password: str = PASSWORD, code: str | None = None
) -> httpx.Response:
    body = {"email": email, "password": password} | ({} if code is None else {"code": code})
    return client.post("/api/auth/login", json=body, headers=CSRF)


def _failures(api: Api, customer_id: str) -> list[dict[str, Any]]:
    return [details for action, _, _, details in api.audit_rows(customer_id) if action == "auth.login_failed"]


def _code(reply: httpx.Response) -> tuple[int, str]:
    return reply.status_code, reply.json()["error"]["code"]


# --- the callback's order (plan section 2) -------------------------------------------------------


def test_a_passing_link_signs_in_and_refreshes_the_hosted_domain(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    _rules(api, customer_id, only=["member"], domains=[DOMAIN])
    subject = _link(api, customer_id, member_id, hd=None)  # stored before #507: no domain kept

    reply = _google(api, google, subject)

    assert reply.headers["location"] == "/documents"
    with db.customer(customer_id) as conn:
        stored = conn.execute(
            text("SELECT hosted_domain FROM user_identities WHERE user_id = :u"), {"u": member_id}
        )
        assert stored.scalar() == DOMAIN


# The callback's rules, in the order it checks them.
_ORDER = ["locked", "disabled", "org_off", "platform_off", "role", "domain", "waiting", "password_first"]


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        # Each case also breaks every later rule, so only the earliest can answer.
        ("locked", "locked"),
        ("disabled", "refused"),
        ("org_off", "google_off"),
        ("platform_off", "google_off"),
        ("role", "role_refused"),
        ("domain", "domain_refused"),
        ("waiting", "waiting"),
        ("password_first", "password_first"),
    ],
)
def test_the_callback_answers_the_first_rule_broken(
    db: Database, google: FakeGoogle, platform: Callable[[bool], None], case: str, expected: str
) -> None:
    api = _api(db)
    customer_id, admin_id = api.customer()  # an admin with no code yet: password_first last
    broken = _ORDER[_ORDER.index(case) :]
    if "locked" in broken:
        with db.customer(customer_id) as conn:
            until = api.clock() + timedelta(minutes=10)
            conn.execute(
                text("UPDATE users SET locked_until = :t WHERE id = :u"), {"t": until, "u": admin_id}
            )
    if "disabled" in broken:
        with db.customer(customer_id) as conn:
            conn.execute(text("UPDATE users SET disabled = true WHERE id = :u"), {"u": admin_id})
    if "org_off" in broken:
        _org(api, customer_id, False)
    if "platform_off" in broken and "org_off" not in broken:
        platform(False)  # the organisation follows the platform (NULL)
    if "role" in broken:
        _rules(api, customer_id, roles=["member"])
    if "domain" in broken:
        _rules(api, customer_id, domains=["sharma.in"])
    subject = _link(api, customer_id, admin_id, approved="waiting" not in broken)

    reply = _google(api, google, subject)

    assert _error(reply) == expected
    assert not reply.cookies.get(SESSION_COOKIE)
    details = _failures(api, customer_id)
    if expected == "refused":
        assert details == [{"reason": "disabled"}]  # as before #507
    else:
        wanted = {"reason": expected, "via": "google"} | (
            {"domain": DOMAIN} if expected == "domain_refused" else {}
        )
        assert details == [wanted]
    with db.customer(customer_id) as conn:
        used = conn.execute(
            text("SELECT last_used_at FROM user_identities WHERE user_id = :u"), {"u": admin_id}
        )
        assert used.scalar() is None


@pytest.mark.parametrize(
    ("org", "platform_on", "signs_in"),
    [(None, True, True), (None, False, False), (True, False, True), (False, True, False)],
)
def test_the_organisation_follows_the_platform_unless_it_has_its_own(
    db: Database,
    google: FakeGoogle,
    platform: Callable[[bool], None],
    org: bool | None,
    platform_on: bool,
    signs_in: bool,
) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    subject = _link(api, customer_id, member_id)
    _org(api, customer_id, org)
    platform(platform_on)
    reply = _google(api, google, subject)
    assert (reply.headers["location"] == "/documents") is signs_in
    if not signs_in:
        assert _error(reply) == "google_off"


@pytest.mark.parametrize(
    ("email", "claims"),
    [
        (f"ramesh@{DOMAIN}", {"hd": None}),  # no hd: a personal account
        (f"ramesh@{DOMAIN}", {"email_verified": False}),
        ("ramesh@gmail.com", {}),  # hd inside, the email's domain outside
        (f"ramesh@{DOMAIN}", {"hd": "evil.com"}),
    ],
)
def test_the_domain_rule_never_trusts_hd_alone(
    db: Database, google: FakeGoogle, email: str, claims: dict[str, Any]
) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    _rules(api, customer_id, domains=[DOMAIN.upper()])
    subject = _link(api, customer_id, member_id)
    assert _error(_google(api, google, subject, email, **claims)) == "domain_refused"
    [details] = _failures(api, customer_id)
    assert set(details) <= {"reason", "via", "domain"} and details["reason"] == "domain_refused"


def test_audit_details_hold_no_personal_data(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, member_email = api.add_user(customer_id, "member")
    _rules(api, customer_id, domains=["sharma.in"])
    subject = _link(api, customer_id, member_id)
    _google(api, google, subject)
    written = repr([details for _, _, _, details in api.audit_rows(customer_id)]).lower()
    for private in (subject, "ramesh", member_email, "made-up-sub"):
        assert private.lower() not in written


# --- the password sign-in ------------------------------------------------------------------------


def test_password_refused_only_after_a_right_password(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, email = api.add_user(customer_id, "member")
    _rules(api, customer_id, only=["member"], domains=[DOMAIN])
    _link(api, customer_id, member_id)
    client = api.client()

    wrong = _login(client, email, "not the password at all")
    unknown = _login(client, f"nobody-{uuid.uuid4().hex}@example.test")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert api.user_state(customer_id, member_id)["failed_logins"] == 1

    refused = _login(client, email)
    assert _code(refused) == (403, "password_refused")
    assert (
        refused.json()["error"]["message"]
        == "Your organisation asks you to sign in with Google. Use Continue with Google."
    )
    assert not client.cookies.get(SESSION_COOKIE)
    assert api.user_state(customer_id, member_id)["failed_logins"] == 0  # the password was right
    assert _failures(api, customer_id)[-1] == {"reason": "password_refused"}


def test_every_password_path_spends_one_hash(
    db: Database, google: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, email = api.add_user(customer_id, "member")
    _rules(api, customer_id, only=["member"])
    _link(api, customer_id, member_id)
    spend_password_time("warm up the dummy hash")
    hashes: list[int] = []
    real = hashlib.scrypt

    def counting(*args: Any, **kwargs: Any) -> bytes:
        hashes.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(hashlib, "scrypt", counting)
    attempts: list[tuple[str, str, int]] = [
        (f"nobody-{uuid.uuid4().hex}@example.test", PASSWORD, 401),
        (email, "not the password at all", 401),
        (email, PASSWORD, 403),  # password_refused
    ]
    for who, password, status in attempts:
        hashes.clear()
        assert _login(api.client(), who, password).status_code == status
        assert len(hashes) == 1, who


@pytest.mark.parametrize("why", ["not_only", "link_outside_domains", "waiting_link", "org_off", "env_off"])
def test_the_password_signs_in_when_the_rule_does_not_hold(
    db: Database, google: FakeGoogle, why: str
) -> None:
    api = Api(db, settings=google_settings(google_signin_enabled=why != "env_off"))
    customer_id, _ = api.customer()
    member_id, email = api.add_user(customer_id, "member")
    _rules(api, customer_id, only=[] if why == "not_only" else ["member"])
    if why == "link_outside_domains":
        _rules(api, customer_id, domains=["sharma.in"])
    if why == "org_off":
        _org(api, customer_id, False)
    _link(api, customer_id, member_id, approved=why != "waiting_link")
    reply = _login(api.client(), email)
    assert reply.status_code == 200, reply.text
    limited = why in ("link_outside_domains", "waiting_link")
    assert reply.json()["user"]["google_link_required"] is limited
    assert reply.json()["user"]["google_unchecked"] is False  # its hd is known: checked


def _hosted_domain(api: Api, customer_id: str, user_id: str) -> str | None:
    return _stored(api, customer_id, user_id)[0]


def _stored(api: Api, customer_id: str, user_id: str) -> tuple[str | None, str | None]:
    """The link's stored ``hd`` and email."""
    with api.db.customer(customer_id) as conn:
        row = conn.execute(
            text("SELECT hosted_domain, email_at_link FROM user_identities WHERE user_id = :u"),
            {"u": user_id},
        ).one()
    return row[0], row[1]


def _connect(api: Api, google: FakeGoogle, signed: Signed, subject: str, **claims: Any) -> str:
    """Connect a Google account from Ways to sign in; where the callback lands."""
    started = signed.client.post("/api/auth/oidc/google/connect", json={"password": PASSWORD}, headers=CSRF)
    assert started.status_code == 200, started.text
    query = google.sign_in(started.json()["url"], subject, f"ramesh@{DOMAIN}", _now(api), **claims)
    landed = signed.client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
    return str(landed.headers["location"])


def test_domains_set_after_linking_leave_the_password_until_a_link_passes(
    db: Database, google: FakeGoogle
) -> None:
    """The stored link and Google agree (the reported gap): an ``hd`` is kept only with a
    verified email, so a link made before the domains, with an email Google didn't verify,
    passes neither. The password opens the limited session (never ``password_refused``), Google
    says ``domain_refused``, and a verified account connected from there ends it. No dead end."""
    api = _api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    unverified = f"made-up-sub-{uuid.uuid4().hex}"
    landed = _connect(api, google, member, unverified, hd=DOMAIN, email_verified=False)
    assert landed == f"{WAYS}?waiting=google"
    [waiting] = admin.client.get("/api/admin/identities").json()["items"]
    approved = admin.client.post(f"/api/admin/identities/{waiting['id']}/approve", headers=CSRF)
    assert approved.status_code == 200, approved.text  # no domains yet: any account
    assert _hosted_domain(api, admin.customer_id, member.user_id) is None
    signed_in = _google(api, google, unverified, f"ramesh@{DOMAIN}", email_verified=False)
    assert signed_in.headers["location"] == "/documents"
    assert _hosted_domain(api, admin.customer_id, member.user_id) is None  # a sign-in keeps the rule

    _rules(api, admin.customer_id, only=["member"], domains=[DOMAIN])

    client = api.client()
    password = _login(client, member.email)
    assert password.status_code == 200, password.text  # not password_refused
    assert password.json()["user"]["google_link_required"] is True
    assert _code(client.get("/api/documents")) == (403, LINK_REQUIRED)
    refused = _google(api, google, unverified, f"ramesh@{DOMAIN}", email_verified=False)
    assert _error(refused) == "domain_refused"

    # The way out, from the limited session: disconnect, connect the verified work account.
    limited = Signed(client, admin.customer_id, member.user_id, member.email)
    [identity] = client.get("/api/auth/identities").json()["identities"]
    removed = client.post(
        f"/api/auth/identities/{identity['id']}/remove", json={"password": PASSWORD}, headers=CSRF
    )
    assert removed.status_code == 200, removed.text
    verified = f"made-up-sub-{uuid.uuid4().hex}"
    assert _connect(api, google, limited, verified, hd=DOMAIN) == f"{WAYS}?waiting=google"
    [waiting] = admin.client.get("/api/admin/identities").json()["items"]
    approved = admin.client.post(f"/api/admin/identities/{waiting['id']}/approve", headers=CSRF)
    assert approved.status_code == 200, approved.text
    assert _hosted_domain(api, admin.customer_id, member.user_id) == DOMAIN
    assert _code(_login(api.client(), member.email)) == (403, "password_refused")
    assert _google(api, google, verified).headers["location"] == "/documents"


def test_an_admin_still_setting_up_two_step_can_enrol(db: Database, google: FakeGoogle) -> None:
    """Else ``password_first`` at Google and ``password_refused`` here would be a dead end."""
    api = _api(db)
    customer_id, _ = api.customer()
    admin_id, email = api.add_user(customer_id, "admin")
    _rules(api, customer_id, only=["admin"])
    _link(api, customer_id, admin_id)
    client = api.client()

    offer = _login(client, email)
    assert _code(offer) == (403, "mfa_enrol_required")
    details = offer.json()["error"]["details"]
    secret = base64.b32decode(details["secret"].replace(" ", ""))
    body = {"enrol_token": details["enrol_token"], "code": api.code(secret)}
    enrolled = client.post("/api/auth/admin/enrol", json=body, headers=CSRF)
    assert enrolled.status_code == 200, enrolled.text
    assert enrolled.json()["user"]["google_link_required"] is False
    api.clock.advance(30)
    again = _login(api.client(), email, code=api.code(secret))
    assert _code(again) == (403, "password_refused")  # enrolled: Google from now on


UNCHECKED = "Sign in with Google once so Kaagaz can check its domain."


def test_a_link_from_before_0072_is_checked_at_the_next_google_sign_in(
    db: Database, google: FakeGoogle
) -> None:
    """#507 review: a link made before 0072 has no ``hd`` yet ("not checked yet"). No dead end:
    the password opens the limited session, which says to sign in with Google once (connecting
    again would be ``has_provider``); Google's sign-in passes the domains and keeps ``hd``."""
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, email = api.add_user(customer_id, "member")
    _rules(api, customer_id, only=["member"], domains=[DOMAIN])
    subject = _link(api, customer_id, member_id, hd=None)

    password = _login(api.client(), email)
    assert password.status_code == 200, password.text
    user = password.json()["user"]
    assert (user["google_link_required"], user["google_unchecked"]) == (True, True)

    assert _google(api, google, subject).headers["location"] == "/documents"
    assert _hosted_domain(api, customer_id, member_id) == DOMAIN
    assert _code(_login(api.client(), email)) == (403, "password_refused")


def test_a_waiting_link_from_before_0072_is_checked_at_google_then_approved(
    db: Database, google: FakeGoogle
) -> None:
    """Approval says ``domain_unchecked``; the person's Google sign-in (still ``waiting``) keeps
    Google's ``hd``, so the approval then goes through."""
    api = _api(db)
    admin = api.new_admin()
    member_id, _ = api.add_user(admin.customer_id, "member")
    _rules(api, admin.customer_id, domains=[DOMAIN])
    subject = _link(api, admin.customer_id, member_id, hd=None, approved=False)
    [waiting] = admin.client.get("/api/admin/identities").json()["items"]
    path = f"/api/admin/identities/{waiting['id']}/approve"

    refused = admin.client.post(path, headers=CSRF)
    assert _code(refused) == (409, "domain_unchecked")
    assert refused.json()["error"]["message"] == UNCHECKED

    assert _error(_google(api, google, subject)) == "waiting"
    assert _hosted_domain(api, admin.customer_id, member_id) == DOMAIN
    assert admin.client.post(path, headers=CSRF).status_code == 200


def test_a_refused_google_sign_in_keeps_googles_answer_for_the_password(
    db: Database, google: FakeGoogle
) -> None:
    """#507 review: the stored link and Google disagree (the work account moved to a domain not
    listed). ``domain_refused`` keeps Google's ``hd`` and email, only when Google verified the
    email, so the password then opens the limited session instead of ``password_refused``."""
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, email = api.add_user(customer_id, "member")
    _rules(api, customer_id, only=["member"], domains=[DOMAIN])
    subject = _link(api, customer_id, member_id)
    assert _code(_login(api.client(), email)) == (403, "password_refused")

    unverified = _google(api, google, subject, "ramesh@moved.in", hd="moved.in", email_verified=False)
    assert _error(unverified) == "domain_refused"
    assert _stored(api, customer_id, member_id) == (DOMAIN, f"ramesh@{DOMAIN}")  # not verified: as it was

    assert _error(_google(api, google, subject, "Ramesh@Moved.in", hd="moved.in")) == "domain_refused"
    assert _stored(api, customer_id, member_id) == ("moved.in", "ramesh@moved.in")
    password = _login(api.client(), email)
    assert password.status_code == 200, password.text
    assert password.json()["user"]["google_link_required"] is True


# --- the limited session (plan section 3) -------------------------------------------------------


def _limited(api: Api, role: str = "member") -> Signed:
    """A "Google only" user with no passing link, signed in with the password."""
    customer_id, _ = api.customer()
    _rules(api, customer_id, only=[role], domains=["example.test"])
    signed = api.sign_in(customer_id, role)
    me = signed.client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["user"]["google_link_required"] is True
    return signed


def _uses(dependant: Dependant, wanted: Callable[..., Any]) -> bool:
    return dependant.call is wanted or any(_uses(sub, wanted) for sub in dependant.dependencies)


def _routes_on_current_user(app: FastAPI) -> set[tuple[str, str]]:
    """Every (method, path) whose route's dependencies hold ``current_user``, its path filled in
    as ``route_calls`` fills it (the provider: google)."""
    found: set[tuple[str, str]] = set()
    for context in iter_route_contexts(app.routes):
        route, path = context.original_route, context.path
        if not isinstance(route, APIRoute) or path is None or not path.startswith("/api"):
            continue
        if not _uses(route.dependant, current_user):
            continue
        for param in route.dependant.path_params:
            path = path.replace("{" + param.name + "}", "1" if param.field_info.annotation is int else ANY_ID)
        found |= {(method, path.replace("{provider}", "google")) for method in context.methods or ()}
    return found


def test_the_limited_session_blocks_every_data_route(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    limited = _limited(api)
    allowed = {
        ("GET", "/api/auth/me"),
        ("GET", "/api/auth/session"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/auth/password"),  # refused below: only a temporary password may be changed
        ("POST", "/api/auth/oidc/google/connect"),
        ("GET", "/api/auth/identities"),
        ("POST", f"/api/auth/identities/{ANY_ID}/remove"),
    }
    # Every route of the app (the two sign-outs last), and those whose dependencies hold current_user.
    on_current_user = _routes_on_current_user(api.app) - set(EMAIL_ROUTES)  # 404 while email is off
    sign_outs = {("POST", "/api/auth/logout"), ("POST", "/api/auth/logout-everywhere")}
    calls = sorted(
        {
            (method, path.replace("{provider}", "google"))
            for method, path in route_calls(api.app)
            if (method, path) not in EMAIL_ROUTES and (method, path) not in sign_outs
        }
    )
    gated = []
    for method, path in calls:
        reply = call(limited.client, method, path)
        if (method, path) in allowed:
            if path == "/api/auth/password":  # a well-formed body, so the route itself answers
                body = {"current_password": PASSWORD, "new_password": "a brand new made-up password"}
                reply = limited.client.post(path, json=body, headers=CSRF)
                assert _code(reply) == (403, LINK_REQUIRED)
            else:
                assert reply.status_code in (200, 400, 404), (method, path, reply.text)  # past the gate
            continue
        if reply.status_code == 403 and reply.json()["error"]["code"] == LINK_REQUIRED:
            gated.append((method, path))
            continue
        # Not gated: then it must not see the session at all, so it answers as signed out.
        signed_out = call(api.client(), method, path)
        assert reply.status_code == signed_out.status_code, (method, path, reply.text)
    # Exactly the routes that use current_user, and no other, answer the limited session's 403.
    assert set(gated) == on_current_user and len(gated) > 50
    assert ("GET", "/api/documents") in gated and ("GET", "/api/admin/users") in gated
    assert ("POST", "/api/auth/2fa/start") in gated
    assert ("POST", "/api/auth/logout-everywhere") not in on_current_user  # #507 review: allowed

    assert limited.client.get("/api/auth/session").json()["me"]["user"]["google_link_required"] is True
    assert limited.client.post("/api/auth/logout", headers=CSRF).status_code == 204
    assert _login(limited.client, limited.email).status_code == 200
    elsewhere = Signed(api.client(), limited.customer_id, limited.user_id, limited.email)
    assert _login(elsewhere.client, limited.email).status_code == 200
    assert limited.client.post("/api/auth/logout-everywhere", headers=CSRF).status_code == 204
    assert elsewhere.client.get("/api/auth/me").status_code == 401  # every browser is signed out
    assert limited.client.get("/api/auth/me").status_code == 401


def test_the_limited_session_connects_google_and_the_gate_lifts(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    limited = _limited(api)
    listed = limited.client.get("/api/auth/identities")
    assert listed.status_code == 200
    assert listed.json()["rules"] == {"google_only": True, "domains": ["example.test"]}
    started = limited.client.post("/api/auth/oidc/google/connect", json={"password": PASSWORD}, headers=CSRF)
    assert started.status_code == 200, started.text
    query = google.sign_in(
        started.json()["url"], f"made-up-sub-{uuid.uuid4().hex}", limited.email, _now(api), hd="example.test"
    )
    landed = limited.client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
    assert landed.headers["location"] == f"{WAYS}?connected=google"
    assert limited.client.get("/api/documents").status_code == 200
    assert limited.client.get("/api/auth/me").json()["user"]["google_link_required"] is False
    linked = [
        d for action, _, _, d in api.audit_rows(limited.customer_id) if action == "auth.identity_linked"
    ]
    assert linked[-1]["domain"] == "example.test"
    with db.customer(limited.customer_id) as conn:
        stored = text("SELECT hosted_domain FROM user_identities WHERE user_id = :u")
        assert conn.execute(stored, {"u": limited.user_id}).scalar() == "example.test"


def test_a_temporary_password_may_still_be_changed_then_the_session_is_limited(
    db: Database, google: FakeGoogle
) -> None:
    """Reset by an admin (links removed, #457): the new password first, then connect Google."""
    api = _api(db)
    admin = api.new_admin()
    member_id, email = api.add_user(admin.customer_id, "member")
    _rules(api, admin.customer_id, only=["member"])
    _link(api, admin.customer_id, member_id)
    assert _code(_login(api.client(), email)) == (403, "password_refused")

    reset = admin.client.post(f"/api/admin/users/{member_id}/reset-password", headers=CSRF)
    assert reset.status_code == 200, reset.text
    client = api.client()
    assert _login(client, email, reset.json()["temporary_password"]).status_code == 200
    body = {
        "current_password": reset.json()["temporary_password"],
        "new_password": "a brand new made-up password",
    }
    changed = client.post("/api/auth/password", json=body, headers=CSRF)
    assert changed.status_code == 200, changed.text
    assert changed.json()["user"]["google_link_required"] is True
    assert _code(client.get("/api/documents")) == (403, LINK_REQUIRED)


def test_disconnecting_your_own_link_falls_back_to_the_limited_session(
    db: Database, google: FakeGoogle
) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, email = api.add_user(customer_id, "member")
    _rules(api, customer_id, only=["member"])
    subject = _link(api, customer_id, member_id)
    client = api.client()
    start = client.get("/api/auth/oidc/google/start", follow_redirects=False)
    query = google.sign_in(start.headers["location"], subject, email, _now(api), hd=DOMAIN)
    client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
    assert client.get("/api/documents").status_code == 200

    [identity] = client.get("/api/auth/identities").json()["identities"]
    removed = client.post(
        f"/api/auth/identities/{identity['id']}/remove", json={"password": PASSWORD}, headers=CSRF
    )
    assert removed.status_code == 200, removed.text
    assert _code(client.get("/api/documents")) == (403, LINK_REQUIRED)
    assert _login(api.client(), email).json()["user"]["google_link_required"] is True


# --- connect (plan section 2) ---------------------------------------------------------------------


@pytest.mark.parametrize(("change", "code"), [("org_off", "google_off"), ("role", "role_refused")])
def test_connect_start_checks_the_organisation_and_the_role(
    db: Database, google: FakeGoogle, change: str, code: str
) -> None:
    api = _api(db)
    member = api.new_signed("member")
    if change == "org_off":
        _org(api, member.customer_id, False)
    else:
        _rules(api, member.customer_id, roles=["admin"])
    reply = member.client.post("/api/auth/oidc/google/connect", json={"password": PASSWORD}, headers=CSRF)
    assert _code(reply) == (403, code)
    assert api.user_state(member.customer_id, member.user_id)["failed_logins"] == 0  # nothing counted


@pytest.mark.parametrize(
    ("change", "claims", "code"),
    [
        ("org_off", {}, "google_off"),
        ("role", {}, "role_refused"),
        ("domains", {"hd": "evil.com"}, "domain_refused"),
        ("domains", {"hd": None}, "domain_refused"),
    ],
)
def test_connect_callback_checks_again_and_audits_the_refusal(
    db: Database, google: FakeGoogle, change: str, claims: dict[str, Any], code: str
) -> None:
    api = _api(db)
    member = api.new_signed("member")
    started = member.client.post("/api/auth/oidc/google/connect", json={"password": PASSWORD}, headers=CSRF)
    assert started.status_code == 200, started.text
    if change == "org_off":
        _org(api, member.customer_id, False)
    elif change == "role":
        _rules(api, member.customer_id, roles=["admin"])
    else:
        _rules(api, member.customer_id, domains=[DOMAIN])
    query = google.sign_in(
        started.json()["url"], f"made-up-sub-{uuid.uuid4().hex}", f"ramesh@{DOMAIN}", _now(api), **claims
    )
    landed = member.client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
    assert landed.headers["location"] == f"{WAYS}?error={code}"
    with db.customer(member.customer_id) as conn:
        assert identities.for_user(conn, member.customer_id, member.user_id) == []
    refused = [d for a, _, _, d in api.audit_rows(member.customer_id) if a == "auth.identity_link_refused"]
    wanted = {"provider": "google", "reason": code} | ({"domain": DOMAIN} if code == "domain_refused" else {})
    assert refused == [wanted]


# --- disabled, deleting, locked (plan section 5) ------------------------------------------------


@pytest.mark.parametrize(
    ("why", "error", "details"),
    [
        ("disabled", "refused", {"reason": "disabled"}),
        ("deleting", "refused", {"reason": "customer_disabled"}),
        ("locked", "locked", {"reason": "locked", "via": "google"}),
    ],
)
def test_disabled_deleting_and_locked_are_refused_and_audited(
    db: Database, google: FakeGoogle, why: str, error: str, details: dict[str, str]
) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    _rules(api, customer_id, only=["member"])
    subject = _link(api, customer_id, member_id)
    if why == "disabled":
        with db.customer(customer_id) as conn:
            conn.execute(text("UPDATE users SET disabled = true WHERE id = :u"), {"u": member_id})
    elif why == "deleting":
        deleting = (
            "UPDATE customers SET status = 'deleting', disabled_at = now(),"
            " delete_after = now() + interval '7 days' WHERE id = :c"
        )
        _owner(db, deleting, {"c": customer_id})
    else:
        with db.customer(customer_id) as conn:
            until = api.clock() + timedelta(minutes=10)
            conn.execute(
                text("UPDATE users SET locked_until = :t WHERE id = :u"), {"t": until, "u": member_id}
            )
    assert _error(_google(api, google, subject)) == error
    assert _failures(api, customer_id) == [details]
