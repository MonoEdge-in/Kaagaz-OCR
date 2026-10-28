"""Organisation users signing in through Keycloak (#435): the routes, end to end against a
made-up realm (tests/keycloak_fake.py) and a real database.

Signed in through Keycloak, a user gets the usual Kaagaz session, marked ``kc``, with no
password-change or two-step gate; anything wrong lands on the sign-in page with a code and
no session. While on, Kaagaz's own password sign-in, sign-up and two-step routes are off;
switching the setting either way ends the sessions of the other kind. Off, the routes are
not there (the rest of off is the existing auth, session and two-step tests, unchanged).
"""

import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.api.auth import KEYCLOAK_COOKIE, OPERATOR_COOKIE, SESSION_COOKIE, OperatorClaims, sign_operator
from kaagaz.db.engine import Database
from tests.keycloak_fake import (
    CLIENT_ID,
    DISCOVERY,
    FakeRealm,
    b64url,
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


def owner_sql(db: Database, statement: str, params: dict[str, str]) -> None:
    """As the table owner (the tests' DATABASE_URL logs in as the owner, like the CLI)."""
    with db.engine.begin() as conn:
        conn.execute(text(statement), params)


def start(client: TestClient, next_path: str | None = None) -> dict[str, str]:
    """GET /start; the query Keycloak's sign-in page was sent."""
    reply = client.get(START, params={} if next_path is None else {"next": next_path}, follow_redirects=False)
    assert reply.status_code == 303, reply.text
    location = reply.headers["location"]
    assert location.startswith(DISCOVERY["authorization_endpoint"] + "?")
    return {name: values[0] for name, values in parse_qs(urlsplit(location).query).items()}


def keycloak_sign_in(
    client: TestClient,
    realm: FakeRealm,
    email: str,
    *,
    next_path: str | None = None,
    **changes: Any,
) -> httpx.Response:
    """The whole sign-in: start, the person signs in at Keycloak (a code for an ID token with
    ``changes``), then the callback. The callback's reply."""
    sent = start(client, next_path)
    token = sign(claims_for(email, sent["nonce"], NOW_UNIX, **changes))
    code = realm.issue(token, sent["code_challenge"])
    return back(client, sent["state"], code)


def back(client: TestClient, state: str, code: str) -> httpx.Response:
    """Keycloak sending the browser back with this state and code."""
    return client.get(CALLBACK, params={"state": state, "code": code}, follow_redirects=False)


def refused(reply: httpx.Response, code: str) -> None:
    """Sent to the sign-in page with ``code``, and no session."""
    assert reply.status_code == 303, reply.text
    assert reply.headers["location"] == f"/login?keycloak_error={code}"
    assert SESSION_COOKIE not in reply.cookies
    assert_state_cleared(reply)


def assert_state_cleared(reply: httpx.Response) -> None:
    cleared = [c for c in reply.headers.get_list("set-cookie") if c.startswith(f"{KEYCLOAK_COOKIE}=")]
    assert len(cleared) == 1 and "Max-Age=0" in cleared[0], cleared


# --- signing in ------------------------------------------------------------------------------


def test_a_member_signs_in_and_gets_the_usual_session(db: Database, realm: FakeRealm) -> None:
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    user_id, email = api.add_user(customer, "member")
    client = api.client()

    reply = keycloak_sign_in(client, realm, email.upper())  # e-mails match in any case

    assert (reply.status_code, reply.headers["location"]) == (303, "/dashboard")
    assert_state_cleared(reply)
    me = client.get("/api/auth/me").json()
    assert (me["user"]["id"], me["customer"]["id"]) == (user_id, customer)
    assert me["session"] == {"scope": "app", "keycloak": True}
    assert client.get("/api/documents").status_code == 200
    assert api.audit_rows(customer)[-1] == ("auth.login", "user", user_id, {"via": "keycloak"})
    # The client secret went to the token endpoint only, with the PKCE verifier.
    form = realm.token_forms[-1]
    assert form["client_id"] == CLIENT_ID and len(form["code_verifier"]) == 64


def test_an_admin_with_no_kaagaz_two_step_or_password_gets_an_admin_session(
    db: Database, realm: FakeRealm
) -> None:
    """Keycloak asked for the password and the code: neither of Kaagaz's gates applies."""
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    admin_id, email = api.add_user(customer, "admin")
    owner_sql(db, "UPDATE users SET must_change_password = true WHERE id = :u", {"u": admin_id})
    member_id, _ = api.add_user(customer, "member")
    client = api.client()

    reply = keycloak_sign_in(client, realm, email, next_path="/admin")

    assert reply.headers["location"] == "/admin"
    assert client.get("/api/auth/me").json()["session"] == {"scope": "admin", "keycloak": True}
    assert client.get("/api/admin/users").status_code == 200
    # Allowing Delete forever needs two-step sign-in, which Keycloak did.
    allowed = client.put(f"/api/admin/users/{member_id}/delete-forever", json={"allowed": True}, headers=CSRF)
    assert allowed.status_code == 200, allowed.text


@pytest.mark.parametrize(
    ("asked", "kept"),
    [
        ("/admin", "/admin"),
        ("/documents/some-id", "/documents/some-id"),
        ("//evil.example.test", "/dashboard"),
        ("/\\evil.example.test", "/dashboard"),
        ("https://evil.example.test/", "/dashboard"),
        ("/admin?x=https://evil.example.test", "/dashboard"),
        ("javascript:alert(1)", "/dashboard"),
        ("", "/dashboard"),
    ],
)
def test_next_is_only_ever_a_path_on_this_site(db: Database, realm: FakeRealm, asked: str, kept: str) -> None:
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    _, email = api.add_user(customer, "reviewer")
    reply = keycloak_sign_in(api.client(), realm, email, next_path=asked)
    assert reply.headers["location"] == kept


# --- the state, the nonce and the code work once -----------------------------------------------


def test_a_wrong_or_missing_state_is_refused(db: Database, realm: FakeRealm) -> None:
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    _, email = api.add_user(customer, "member")
    client = api.client()
    sent = start(client)
    code = realm.issue(sign(claims_for(email, sent["nonce"], NOW_UNIX)), sent["code_challenge"])

    refused(back(client, "made-up", code), "state")
    # The cookie was cleared, so even the right state no longer works.
    refused(back(client, sent["state"], code), "state")
    # Another browser, with no cookie at all.
    refused(back(api.client(), sent["state"], code), "state")
    assert realm.token_forms == []  # no code was ever sent to Keycloak


def test_a_state_cookie_past_its_ten_minutes_is_refused(db: Database, realm: FakeRealm) -> None:
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    _, email = api.add_user(customer, "member")
    client = api.client()
    sent = start(client)
    code = realm.issue(sign(claims_for(email, sent["nonce"], NOW_UNIX)), sent["code_challenge"])
    api.clock.advance(10 * 60)
    refused(back(client, sent["state"], code), "state")


def test_a_used_callback_or_code_never_signs_in_again(db: Database, realm: FakeRealm) -> None:
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    _, email = api.add_user(customer, "member")
    client = api.client()
    sent = start(client)
    code = realm.issue(sign(claims_for(email, sent["nonce"], NOW_UNIX)), sent["code_challenge"])
    params = {"state": sent["state"], "code": code}
    assert client.get(CALLBACK, params=params, follow_redirects=False).headers["location"] == "/dashboard"

    other = api.client()
    refused(other.get(CALLBACK, params=params, follow_redirects=False), "state")  # the same URL again
    again = start(other)  # a new state for this browser, with the old code
    refused(back(other, again["state"], code), "token")


def test_a_code_for_another_browsers_verifier_is_refused(db: Database, realm: FakeRealm) -> None:
    """PKCE: a code stolen on its way back is useless without the verifier in its own cookie."""
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    _, email = api.add_user(customer, "member")
    victim, attacker = api.client(), api.client()
    stolen = start(victim)
    code = realm.issue(sign(claims_for(email, stolen["nonce"], NOW_UNIX)), stolen["code_challenge"])
    own = start(attacker)
    refused(back(attacker, own["state"], code), "token")


def test_a_token_for_another_browsers_nonce_is_refused(db: Database, realm: FakeRealm) -> None:
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    _, email = api.add_user(customer, "member")
    refused(keycloak_sign_in(api.client(), realm, email, nonce="another-browser"), "token")


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"exp": NOW_UNIX - 3600}, "token"),
        ({"aud": "another-client"}, "token"),
        ({"iss": "https://id.example.test/realms/another"}, "token"),
        ({"email_verified": False}, "email_unverified"),
    ],
)
def test_a_wrong_id_token_is_refused(
    db: Database, realm: FakeRealm, changes: dict[str, Any], code: str
) -> None:
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    _, email = api.add_user(customer, "member")
    refused(keycloak_sign_in(api.client(), realm, email, **changes), code)


def test_a_badly_signed_id_token_is_refused(db: Database, realm: FakeRealm) -> None:
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    _, email = api.add_user(customer, "member")
    client = api.client()
    sent = start(client)
    head, body, _ = sign(claims_for(email, sent["nonce"], NOW_UNIX)).split(".")
    code = realm.issue(f"{head}.{body}.{b64url(b'not the signature')}", sent["code_challenge"])
    refused(back(client, sent["state"], code), "token")


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


def test_an_e_mail_with_no_kaagaz_account_is_refused(db: Database, realm: FakeRealm) -> None:
    api = Api(db, settings=keycloak_settings())
    refused(
        keycloak_sign_in(api.client(), realm, f"nobody-{uuid.uuid4().hex[:8]}@example.test"), "no_account"
    )


def test_a_disabled_user_is_refused_and_audited(db: Database, realm: FakeRealm) -> None:
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    user_id, email = api.add_user(customer, "member")
    owner_sql(db, "UPDATE users SET disabled = true WHERE id = :u", {"u": user_id})
    refused(keycloak_sign_in(api.client(), realm, email), "no_account")
    reason = {"reason": "disabled", "via": "keycloak"}
    assert api.audit_rows(customer)[-1] == ("auth.login_failed", "user", user_id, reason)


def test_a_user_of_a_disabled_organisation_is_refused_and_audited(db: Database, realm: FakeRealm) -> None:
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    user_id, email = api.add_user(customer, "admin")
    owner_sql(
        db, "UPDATE customers SET status = 'disabled', disabled_at = now() WHERE id = :c", {"c": customer}
    )
    refused(keycloak_sign_in(api.client(), realm, email), "no_account")
    reason = {"reason": "customer_disabled", "via": "keycloak"}
    assert api.audit_rows(customer)[-1] == ("auth.login_failed", "user", user_id, reason)


def test_an_operators_e_mail_is_refused_with_no_session_of_any_kind(db: Database, realm: FakeRealm) -> None:
    """Operators are not users: the lookup reads users only, so the operator console's
    sign-in is never reachable through Keycloak."""
    api = Api(db, settings=keycloak_settings())
    email = f"op-{uuid.uuid4().hex[:12]}@example.test"
    owner_sql(
        db,
        "INSERT INTO operators (email, display_name, password_hash) VALUES (:e, 'Ops', 'not-a-real-hash')",
        {"e": email},
    )
    client = api.client()
    reply = keycloak_sign_in(client, realm, email)
    refused(reply, "no_account")
    assert not [c for c in reply.headers.get_list("set-cookie") if c.startswith("kaagaz_operator=")]
    assert client.get("/api/operator/me").status_code == 401


# --- signing out ------------------------------------------------------------------------------


def test_sign_out_ends_the_session_then_keycloaks(db: Database, realm: FakeRealm) -> None:
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    _, email = api.add_user(customer, "member")
    client = api.client()
    keycloak_sign_in(client, realm, email)

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
    db: Database, realm: FakeRealm
) -> None:
    api = Api(db, settings=keycloak_settings())
    customer, _ = api.customer()
    _, email = api.add_user(customer, "member")
    target_id, _ = api.add_user(customer, "admin")
    anyone = api.client()

    login = anyone.post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF)
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
    _, admin_email = api.add_user(customer, "admin")
    keycloak_sign_in(admin, realm, admin_email)
    for path, body in [
        ("/api/auth/admin/2fa/start", {"password": PASSWORD}),
        ("/api/auth/admin/2fa/off", {}),
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


def test_switching_keycloak_on_or_off_ends_the_other_kinds_sessions(db: Database, realm: FakeRealm) -> None:
    """A password session never outlives the switch to Keycloak, nor a Keycloak one the switch back."""
    off = Api(db)
    on = Api(db, settings=keycloak_settings())
    customer, _ = off.customer()
    password_session = off.sign_in(customer, "member")
    _, email = on.add_user(customer, "member")
    keycloak_client = on.client()
    keycloak_sign_in(keycloak_client, realm, email)

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
