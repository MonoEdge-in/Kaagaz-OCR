"""Organisation users signing in through Keycloak (#435): the routes, end to end against a
made-up realm (tests/keycloak_fake.py) and a real database. The ID token's own checks are
tests/test_keycloak.py's.

Signed in through Keycloak, a user gets the usual Kaagaz session, marked ``kc``, with no
password-change or two-step gate; anything wrong lands on the sign-in page with a code and
no session. While on, Kaagaz's own password sign-in, sign-up, two-step routes and password
retyping for Delete forever are off (Keycloak confirms instead); switching the setting
either way ends the sessions of the other kind. Off, the routes are not there (the rest of
off is the existing auth, session and two-step tests, unchanged).
"""

import dataclasses
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
import pytest
from sqlalchemy import Connection, text

from kaagaz.api.auth import (
    CONFIRM_COOKIE,
    CONFIRM_WITH_KEYCLOAK,
    KEYCLOAK_COOKIE,
    OPERATOR_COOKIE,
    SESSION_COOKIE,
    OperatorClaims,
    sign_operator,
)
from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database
from kaagaz.db.users import LoginRow
from tests.keycloak_fake import (
    CLIENT_ID,
    DISCOVERY,
    FakeRealm,
    claims_for,
    keycloak_settings,
    sign,
    use_realm,
)
from tests.test_api_support import CSRF, NOW, PASSWORD, Api, TestClient

pytestmark = pytest.mark.db

NOW_UNIX = int(NOW.timestamp())
START = "/api/auth/keycloak/start"
CALLBACK = "/api/auth/keycloak/callback"


@pytest.fixture
def realm(monkeypatch: pytest.MonkeyPatch) -> FakeRealm:
    return use_realm(monkeypatch)


@dataclass
class Person:
    """A user of a new organisation, on an app with Keycloak on."""

    api: Api
    customer: str
    user_id: str
    email: str


def person(db: Database, role: str = "member") -> Person:
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    user_id, email = api.add_user(customer, role)
    return Person(api, customer, user_id, email)


@pytest.fixture
def member(db: Database) -> Person:
    return person(db)


def owner_sql(db: Database, statement: str, params: dict[str, str]) -> None:
    """As the table owner (the tests' DATABASE_URL logs in as the owner, like the CLI)."""
    with db.engine.begin() as conn:
        conn.execute(text(statement), params)


def start(client: TestClient, next_path: str | None = None, *, confirm: bool = False) -> dict[str, str]:
    """GET /start; the query Keycloak's sign-in page was sent."""
    params = {**({} if next_path is None else {"next": next_path}), **({"confirm": "1"} if confirm else {})}
    reply = client.get(START, params=params, follow_redirects=False)
    assert reply.status_code == 303, reply.text
    location = reply.headers["location"]
    assert location.startswith(DISCOVERY["authorization_endpoint"] + "?")
    return {name: values[0] for name, values in parse_qs(urlsplit(location).query).items()}


def keycloak_sign_in(
    client: TestClient,
    realm: FakeRealm,
    who: Person,
    *,
    next_path: str | None = None,
    confirm: bool = False,
    **changes: Any,
) -> httpx.Response:
    """The whole sign-in: start, the person signs in at Keycloak (a code for an ID token of
    their e-mail and organisation, with ``changes``), then the callback. The callback's reply."""
    sent = start(client, next_path, confirm=confirm)
    token = sign(claims_for(who.email, sent["nonce"], NOW_UNIX, **{"kaagaz_org": who.customer, **changes}))
    return back(client, sent["state"], realm.issue(token, sent["code_challenge"]))


def back(client: TestClient, state: str, code: str) -> httpx.Response:
    """Keycloak sending the browser back with this state and code."""
    return client.get(CALLBACK, params={"state": state, "code": code}, follow_redirects=False)


def refused(reply: httpx.Response, code: str) -> None:
    """Sent to the sign-in page with ``code``, and no session or confirm cookie."""
    assert reply.status_code == 303, reply.text
    assert reply.headers["location"] == f"/login?keycloak_error={code}"
    assert SESSION_COOKIE not in reply.cookies and CONFIRM_COOKIE not in reply.cookies
    assert_state_cleared(reply)


def assert_state_cleared(reply: httpx.Response) -> None:
    cleared = [c for c in reply.headers.get_list("set-cookie") if c.startswith(f"{KEYCLOAK_COOKIE}=")]
    assert len(cleared) == 1 and "Max-Age=0" in cleared[0], cleared


def failed_audit(who: Person, reason: str) -> tuple[str, str, str, dict[str, str]]:
    return ("auth.login_failed", "user", who.user_id, {"reason": reason, "via": "keycloak"})


# --- signing in ------------------------------------------------------------------------------


def test_a_member_signs_in_and_gets_the_usual_session(member: Person, realm: FakeRealm) -> None:
    client = member.api.client()
    reply = keycloak_sign_in(client, realm, member, email=member.email.upper())  # any case

    # signed_in=1: the page tells the other tabs who is signed in now (lib/session.tsx).
    assert (reply.status_code, reply.headers["location"]) == (303, "/dashboard?signed_in=1")
    assert_state_cleared(reply)
    me = client.get("/api/auth/me").json()
    assert (me["user"]["id"], me["customer"]["id"]) == (member.user_id, member.customer)
    assert me["session"] == {"scope": "app", "keycloak": True}
    assert client.get("/api/documents").status_code == 200
    assert member.api.audit_rows(member.customer)[-1] == (
        "auth.login",
        "user",
        member.user_id,
        {"via": "keycloak"},
    )
    # The client secret went to the token endpoint only, with the PKCE verifier.
    form = realm.token_forms[-1]
    assert form["client_id"] == CLIENT_ID and len(form["code_verifier"]) == 64


def test_an_admin_with_no_kaagaz_two_step_or_password_gets_an_admin_session(
    db: Database, realm: FakeRealm
) -> None:
    admin = person(db, "admin")
    owner_sql(db, "UPDATE users SET must_change_password = true WHERE id = :u", {"u": admin.user_id})
    member_id, _ = admin.api.add_user(admin.customer, "member")
    client = admin.api.client()

    reply = keycloak_sign_in(client, realm, admin, next_path="/admin")

    assert reply.headers["location"] == "/admin?signed_in=1"
    assert client.get("/api/auth/me").json()["session"] == {"scope": "admin", "keycloak": True}
    assert client.get("/api/admin/users").status_code == 200
    # Allowing Delete forever needs two-step sign-in; Keycloak's counts.
    allowed = client.put(f"/api/admin/users/{member_id}/delete-forever", json={"allowed": True}, headers=CSRF)
    assert allowed.status_code == 200, allowed.text


@pytest.mark.parametrize(
    ("asked", "kept"),
    [
        ("/admin", "/admin?signed_in=1"),
        ("/documents?trash=1", "/documents?trash=1&signed_in=1"),
        ("//evil.example.test", "/dashboard?signed_in=1"),
        ("/\\evil.example.test", "/dashboard?signed_in=1"),
        ("https://evil.example.test/", "/dashboard?signed_in=1"),
        ("/admin?x=https://evil.example.test", "/dashboard?signed_in=1"),
        ("/admin?x=%2F%2Fevil", "/dashboard?signed_in=1"),
        ("/admin#x", "/dashboard?signed_in=1"),
        ("javascript:alert(1)", "/dashboard?signed_in=1"),
        ("", "/dashboard?signed_in=1"),
    ],
)
def test_next_is_only_ever_a_path_on_this_site(
    member: Person, realm: FakeRealm, asked: str, kept: str
) -> None:
    reply = keycloak_sign_in(member.api.client(), realm, member, next_path=asked)
    assert reply.headers["location"] == kept


# --- the state, the nonce and the code work once -----------------------------------------------


def test_a_wrong_or_missing_state_is_refused(member: Person, realm: FakeRealm) -> None:
    client = member.api.client()
    sent = start(client)
    code = realm.issue(sign(claims_for(member.email, sent["nonce"], NOW_UNIX)), sent["code_challenge"])

    refused(back(client, "made-up", code), "state")
    # The cookie was cleared, so even the right state no longer works.
    refused(back(client, sent["state"], code), "state")
    # Another browser, with no cookie at all.
    refused(back(member.api.client(), sent["state"], code), "state")
    assert realm.token_forms == []  # no code was ever sent to Keycloak


def test_a_state_cookie_past_its_ten_minutes_is_refused(member: Person, realm: FakeRealm) -> None:
    client = member.api.client()
    sent = start(client)
    code = realm.issue(sign(claims_for(member.email, sent["nonce"], NOW_UNIX)), sent["code_challenge"])
    member.api.clock.advance(10 * 60)
    refused(back(client, sent["state"], code), "state")


def test_a_used_callback_or_code_never_signs_in_again(member: Person, realm: FakeRealm) -> None:
    client = member.api.client()
    sent = start(client)
    token = sign(claims_for(member.email, sent["nonce"], NOW_UNIX, kaagaz_org=member.customer))
    code = realm.issue(token, sent["code_challenge"])
    assert back(client, sent["state"], code).headers["location"] == "/dashboard?signed_in=1"

    other = member.api.client()
    refused(back(other, sent["state"], code), "state")  # the same URL again
    again = start(other)  # a new state for this browser, with the old code
    refused(back(other, again["state"], code), "token")


def test_a_code_for_another_browsers_verifier_is_refused(member: Person, realm: FakeRealm) -> None:
    """PKCE: a code stolen on its way back is useless without the verifier in its own cookie."""
    victim, attacker = member.api.client(), member.api.client()
    stolen = start(victim)
    code = realm.issue(sign(claims_for(member.email, stolen["nonce"], NOW_UNIX)), stolen["code_challenge"])
    own = start(attacker)
    refused(back(attacker, own["state"], code), "token")


def test_a_token_for_another_browsers_nonce_is_refused(member: Person, realm: FakeRealm) -> None:
    refused(keycloak_sign_in(member.api.client(), realm, member, nonce="another-browser"), "token")


def test_a_sign_in_made_before_this_one_was_asked_for_is_refused(member: Person, realm: FakeRealm) -> None:
    """max_age=0: an old Keycloak sign-in (auth_time) never stands in for a new one."""
    reply = keycloak_sign_in(member.api.client(), realm, member, auth_time=NOW_UNIX - 3600)
    refused(reply, "token")


def test_cancelled_at_keycloak_is_refused(db: Database, realm: FakeRealm) -> None:
    client = Api(db, settings=keycloak_settings()).client()
    sent = start(client)
    params = {"state": sent["state"], "error": "access_denied", "code": "made-up"}
    refused(client.get(CALLBACK, params=params, follow_redirects=False), "cancelled")
    assert realm.token_forms == []  # nothing was sent to Keycloak


def test_keycloak_down_sends_back_to_the_sign_in_page(db: Database, realm: FakeRealm) -> None:
    realm.down = True
    reply = Api(db, settings=keycloak_settings()).client().get(START, follow_redirects=False)
    assert (reply.status_code, reply.headers["location"]) == (303, "/login?keycloak_error=unreachable")
    assert KEYCLOAK_COOKIE not in reply.cookies


# --- who may sign in -------------------------------------------------------------------------


def test_an_e_mail_with_no_kaagaz_account_is_refused(member: Person, realm: FakeRealm) -> None:
    nobody = Person(member.api, member.customer, "", f"nobody-{uuid.uuid4().hex[:8]}@example.test")
    refused(keycloak_sign_in(member.api.client(), realm, nobody), "no_account")


@pytest.mark.parametrize("claimed", ["another", None])
def test_an_e_mail_added_to_another_organisation_is_refused_and_audited(
    db: Database, realm: FakeRealm, claimed: str | None
) -> None:
    """Org A's admin adds someone else's e-mail to A: that person's Keycloak sign-in names
    their own organisation (kaagaz_org), never A, so it opens nothing in A."""
    squatted = person(db)
    other_org, _ = squatted.api.customer()
    org = other_org if claimed == "another" else None
    refused(keycloak_sign_in(squatted.api.client(), realm, squatted, kaagaz_org=org), "wrong_org")
    assert squatted.api.audit_rows(squatted.customer)[-1] == failed_audit(squatted, "org_mismatch")


def test_a_disabled_user_is_refused_and_audited(member: Person, realm: FakeRealm, db: Database) -> None:
    owner_sql(db, "UPDATE users SET disabled = true WHERE id = :u", {"u": member.user_id})
    refused(keycloak_sign_in(member.api.client(), realm, member), "no_account")
    assert member.api.audit_rows(member.customer)[-1] == failed_audit(member, "disabled")


def test_a_user_disabled_during_the_sign_in_is_refused_with_the_true_reason(
    member: Person, realm: FakeRealm, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disabled after the e-mail was looked up: the session's own check refuses it, and the
    audit says "disabled", not a disabled organisation."""
    lookup = users_sql.login_lookup

    def enabled_at_lookup(conn: Connection, email: str) -> LoginRow | None:
        found = lookup(conn, email)
        return None if found is None else dataclasses.replace(found, disabled=False)

    monkeypatch.setattr(users_sql, "login_lookup", enabled_at_lookup)
    owner_sql(db, "UPDATE users SET disabled = true WHERE id = :u", {"u": member.user_id})
    refused(keycloak_sign_in(member.api.client(), realm, member), "no_account")
    assert member.api.audit_rows(member.customer)[-1] == failed_audit(member, "disabled")


def test_a_user_of_a_disabled_organisation_is_refused_and_audited(db: Database, realm: FakeRealm) -> None:
    admin = person(db, "admin")
    statement = "UPDATE customers SET status = 'disabled', disabled_at = now() WHERE id = :c"
    owner_sql(db, statement, {"c": admin.customer})
    refused(keycloak_sign_in(admin.api.client(), realm, admin), "no_account")
    assert admin.api.audit_rows(admin.customer)[-1] == failed_audit(admin, "customer_disabled")


def test_an_operators_e_mail_is_refused_with_no_session_of_any_kind(db: Database, realm: FakeRealm) -> None:
    """Operators are not users: the lookup reads users only, so the operator console's
    sign-in is never reachable through Keycloak."""
    api = Api(db, settings=keycloak_settings())
    operator = Person(api, str(uuid.uuid4()), "", f"op-{uuid.uuid4().hex[:12]}@example.test")
    statement = (
        "INSERT INTO operators (email, display_name, password_hash) VALUES (:e, 'Ops', 'not-a-real-hash')"
    )
    owner_sql(db, statement, {"e": operator.email})
    client = api.client()
    reply = keycloak_sign_in(client, realm, operator)
    refused(reply, "no_account")
    assert not [c for c in reply.headers.get_list("set-cookie") if c.startswith("kaagaz_operator=")]
    assert client.get("/api/operator/me").status_code == 401


# --- confirming Delete forever through Keycloak ----------------------------------------------------


def granted(db: Database) -> tuple[Person, TestClient]:
    """A member an admin allowed to delete forever, signed in through Keycloak."""
    member = person(db)
    statement = "UPDATE users SET delete_forever_allowed_at = now() WHERE id = :u"
    owner_sql(db, statement, {"u": member.user_id})
    return member, member.api.client()


def test_a_granted_member_confirms_delete_forever_through_keycloak(db: Database, realm: FakeRealm) -> None:
    member, client = granted(db)
    keycloak_sign_in(client, realm, member)
    document = member.api.add_document(member.customer, uploaded_by=member.user_id, deleted_at=NOW)
    refused_delete = client.delete(f"/api/documents/{document}/permanent", headers=CSRF)
    expected = {"code": "password_confirm_required", "message": CONFIRM_WITH_KEYCLOAK}
    assert refused_delete.json()["error"] == expected
    typed = client.post("/api/auth/confirm-password", json={"password": PASSWORD}, headers=CSRF)
    assert typed.status_code == 404  # no Kaagaz password to retype while Keycloak is on

    reply = keycloak_sign_in(client, realm, member, next_path="/documents?trash=1", confirm=True)

    assert (reply.status_code, reply.headers["location"]) == (303, "/documents?trash=1")
    assert CONFIRM_COOKIE in reply.cookies and SESSION_COOKIE not in reply.cookies
    assert client.delete(f"/api/documents/{document}/permanent", headers=CSRF).status_code == 204


@pytest.mark.parametrize("who", ["another_user", "no_session", "not_granted"])
def test_a_keycloak_confirm_for_anyone_else_is_refused(db: Database, realm: FakeRealm, who: str) -> None:
    member, client = granted(db)
    keycloak_sign_in(client, realm, member)
    signer = member
    if who == "another_user":  # Keycloak signed in someone else in this browser
        other_id, other_email = member.api.add_user(member.customer, "member")
        signer = Person(member.api, member.customer, other_id, other_email)
    elif who == "no_session":
        client = member.api.client()
    else:
        owner_sql(
            db, "UPDATE users SET delete_forever_allowed_at = NULL WHERE id = :u", {"u": member.user_id}
        )
    refused(keycloak_sign_in(client, realm, signer, confirm=True), "confirm")


# --- signing out ------------------------------------------------------------------------------


def test_sign_out_ends_the_session_then_keycloaks(member: Person, realm: FakeRealm) -> None:
    client = member.api.client()
    keycloak_sign_in(client, realm, member)

    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    reply = client.get("/api/auth/keycloak/logout", follow_redirects=False)
    assert reply.status_code == 303
    location = urlsplit(reply.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == DISCOVERY["end_session_endpoint"]
    assert parse_qs(location.query) == {
        "client_id": [CLIENT_ID],
        "post_logout_redirect_uri": ["https://kaagaz.example.test/login?signed_out=1"],
    }


# --- while on, Kaagaz's own sign-in and two-step are off ------------------------------------------


def test_while_on_the_password_sign_in_sign_up_and_two_step_routes_are_off(
    member: Person, realm: FakeRealm, db: Database
) -> None:
    api, customer = member.api, member.customer
    target_id, _ = api.add_user(customer, "admin")
    anyone = api.client()

    login = anyone.post("/api/auth/login", json={"email": member.email, "password": PASSWORD}, headers=CSRF)
    assert (login.status_code, login.json()["error"]["code"]) == (403, "keycloak_sign_in")
    assert anyone.get("/api/auth/session").json() == {"signed_in": False, "keycloak": True}
    assert anyone.get("/api/auth/signup").json() == {"enabled": False}
    signup = {
        "organisation_name": "Mehta Textiles",
        "name": "Asha",
        "email": "new@example.test",
        "password": PASSWORD,
    }
    assert anyone.post("/api/auth/signup", json=signup, headers=CSRF).status_code == 404

    enrol = anyone.post("/api/auth/admin/enrol", json={"enrol_token": "x", "code": "123456"}, headers=CSRF)
    assert enrol.status_code == 404
    admin = api.client()
    admin_id, admin_email = api.add_user(customer, "admin")
    keycloak_sign_in(admin, realm, Person(api, customer, admin_id, admin_email))
    for path, body in [
        ("/api/auth/admin/2fa/start", {"password": PASSWORD}),
        (f"/api/admin/users/{target_id}/reset-2fa", {}),
    ]:
        reply = admin.post(path, json=body, headers=CSRF)
        assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found"), path

    # The operator's reset of an admin's two-step, with an operator session.
    operator_id = str(uuid.uuid4())
    owner_sql(
        db,
        "INSERT INTO operators (id, email, display_name, password_hash, totp_secret_enc, totp_enabled_at)"
        " VALUES (:o, :e, 'Ops', 'not-a-real-hash', 'not-a-real-secret', now())",
        {"o": operator_id, "e": f"op-{operator_id[:8]}@example.test"},
    )
    operator = api.client()
    claims = OperatorClaims(operator_id=operator_id, session_version=1, expires_at=NOW_UNIX + 600)
    operator.cookies.set(OPERATOR_COOKIE, sign_operator(claims, api.settings.session_secret))
    assert operator.get("/api/operator/me").status_code == 200
    path = f"/api/operator/orgs/{customer}/admins/{target_id}/reset-2fa"
    reply = operator.post(path, json={"code": "123456"}, headers=CSRF)
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")


def test_switching_keycloak_on_or_off_ends_the_other_kinds_sessions(member: Person, realm: FakeRealm) -> None:
    """A password session never outlives the switch to Keycloak, nor a Keycloak one the switch back."""
    on = member.api
    off = Api(on.db)
    password_session = off.sign_in(member.customer, "member")
    keycloak_client = on.client()
    keycloak_sign_in(keycloak_client, realm, member)

    password_cookie = password_session.client.cookies[SESSION_COOKIE]
    keycloak_cookie = keycloak_client.cookies[SESSION_COOKIE]
    for api, cookie, status in [
        (on, password_cookie, 401),
        (off, keycloak_cookie, 401),
        (on, keycloak_cookie, 200),
        (off, password_cookie, 200),
    ]:
        client = api.client()
        client.cookies.set(SESSION_COOKIE, cookie)
        assert client.get("/api/auth/me").status_code == status


def test_while_off_the_keycloak_routes_are_not_there(db: Database) -> None:
    client = Api(db).client()
    for path in (START, CALLBACK, "/api/auth/keycloak/logout"):
        reply = client.get(path, follow_redirects=False)
        assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found"), path
    assert client.get("/api/auth/session").json() == {"signed_in": False}
