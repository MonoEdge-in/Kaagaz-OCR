"""#520 guard rails, sign-in: attacks the sign-in tests did not make yet.

11. The lockout can't be dodged by the e-mail's letter case or spaces, holds exactly
    LOGIN_LOCK_MINUTES, and is per account.
12. The operator's step-up code is single use across different changes (and against the
    sign-in's own code) and is refused once its 30-second window is over. Two-step sign-in
    itself: test_api_two_step, test_api_admin_sign_in, test_isolation_admin_two_step_required.
13. Every cookie the API sets is HttpOnly, SameSite and, with COOKIE_SECURE, Secure, and only
    kaagaz/api/auth.py sets one. Logout ends this browser's session on the server (0076, #502):
    a copied cookie at once, other devices kept, the rows swept, one query; the operator's too.
14. A Google-only account's right password never opens a session, whatever the e-mail's case
    or spaces, and no reply sets a session cookie.
15. The signed-out rate bucket per visitor: test_api_auth (ported from production, 6fa3a7df).
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import Response
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from kaagaz.api import auth
from kaagaz.api.auth import (
    OPERATOR_COOKIE,
    SESSION_COOKIE,
    CurrentUser,
    FlowClaims,
    OperatorClaims,
    PendingClaims,
    SessionClaims,
)
from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database
from kaagaz.db.sqlstate import sqlstate
from kaagaz.jobs.purge import sweep_ended_sessions
from tests import test_api_google_rules as google_rules
from tests.oidc_fake import FakeGoogle
from tests.settings_factory import make_settings
from tests.test_api_google_rules import DOMAIN, _api, _link, _rules
from tests.test_api_support import CSRF, PASSWORD, Api, TestClient
from tests.test_operator_auth_db import _Operator
from tests.test_operator_orgs_db import _platform

pytestmark = pytest.mark.db

WRONG = "not the password!!"
ORGS = "/api/operator/orgs"
google = google_rules.google  # the fixture, by its name


def _login(client: TestClient, email: str, password: str = PASSWORD) -> httpx.Response:
    return client.post("/api/auth/login", json={"email": email, "password": password}, headers=CSRF)


def _variants(email: str) -> list[str]:
    return [email.upper(), f"  {email}  ", f"\t{email.title()}\n", email.swapcase(), email]


# --- 11. the lockout ----------------------------------------------------------------------------


def test_the_lockout_counts_every_spelling_of_the_email_and_lasts_exactly_15_minutes(db: Database) -> None:
    api = Api(db, settings=make_settings(login_max_failures=5, login_lock_minutes=15))
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, "member")
    other_id, other_email = api.add_user(customer_id, "member")
    client = api.client()

    for spelled in _variants(email):  # five wrong passwords, each spelled differently
        assert _login(client, spelled, WRONG).status_code == 401
    state = api.user_state(customer_id, user_id)
    # Changed on purpose (A2, 7 Nov 2026): the lock is this address's, held in memory.
    assert (state["failed_logins"], state["locked_until"]) == (5, None)

    for spelled in _variants(email):  # the right password, any spelling: still locked
        reply = _login(client, spelled)
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "bad_credentials"), spelled
        assert SESSION_COOKIE not in reply.headers.get("set-cookie", "")
    assert _login(api.client(), other_email).status_code == 200  # per account: another is open
    assert api.user_state(customer_id, other_id)["failed_logins"] == 0

    api.clock.advance(15 * 60 - 1)
    assert _login(api.client(), email).status_code == 401  # a second before the end
    api.clock.advance(1)
    assert _login(api.client(), email.upper()).status_code == 200  # 15 minutes on the dot
    assert api.user_state(customer_id, user_id)["failed_logins"] == 0


# --- 12. the operator's step-up code -------------------------------------------------------------


def _limit(client: TestClient, customer_id: str, code: str) -> httpx.Response:
    body = {"storage_gb": 7, "code": code}
    return client.put(f"{ORGS}/{customer_id}/storage-limit", json=body, headers=CSRF)


def _disable(client: TestClient, customer_id: str, code: str) -> httpx.Response:
    return client.post(f"{ORGS}/{customer_id}/disable", json={"code": code}, headers=CSRF)


def _step_up_failed(reply: httpx.Response) -> None:
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "step_up_failed"), reply.text


def test_a_step_up_code_works_once_for_one_change_and_never_after_its_window(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api)
    client = api.client()
    a, b = api.new_admin("Org A"), api.new_admin("Org B")
    signed_in_with = api.code(operator.secret)
    assert operator.login(client, code=signed_in_with).status_code == 200
    _step_up_failed(_disable(client, a.customer_id, signed_in_with))  # the sign-in's code: used

    api.clock.advance(30)
    code = api.code(operator.secret)
    assert _disable(client, a.customer_id, code).status_code == 200
    _step_up_failed(_limit(client, b.customer_id, code))  # the same code, another change
    _step_up_failed(_disable(client, b.customer_id, code))  # or the same change elsewhere

    api.clock.advance(90)  # three steps on: the window is the step before, this one and the next
    for stale in (api.code(operator.secret, -2), api.code(operator.secret, -3)):
        _step_up_failed(_limit(client, b.customer_id, stale))
    assert b.client.get("/api/auth/me").status_code == 200  # B was never disabled
    assert _limit(client, b.customer_id, api.code(operator.secret)).status_code == 200  # the control
    assert [row[0] for row in _platform(operator)] == [
        "operator.step_up_failed",
        "customer.disable",
        "operator.step_up_failed",
        "operator.step_up_failed",
        "operator.step_up_failed",
        "operator.step_up_failed",
        "storage.limit_org",
    ]


# --- 13. cookies --------------------------------------------------------------------------------


def _set_cookies(response: Response) -> list[str]:
    return [value.decode("latin-1") for name, value in response.raw_headers if name == b"set-cookie"]


def test_every_cookie_the_api_sets_or_clears_is_httponly_samesite_and_secure_when_asked() -> None:
    now = 1_800_000_000
    later = now + 600
    user = CurrentUser(
        user_id="u",
        customer_id="c",
        email="e@example.test",
        display_name="E",
        role="member",
        customer_name="C",
        scope="app",
        session_version=1,
        must_change_password=False,
        totp_enabled=False,
        delete_forever_allowed=False,
    )
    flow = FlowClaims(
        provider="google",
        mode="signin",
        state="s",
        nonce="n",
        verifier="v",
        next_path="/",
        user_id=None,
        customer_id=None,
        session_version=None,
        expires_at=later,
    )
    session = SessionClaims(
        user_id="u", customer_id="c", role="member", scope="app", session_version=1, expires_at=later
    )
    for secure in (True, False):
        settings = make_settings(cookie_secure=secure)
        response = Response()
        auth.set_session_cookie(response, session, settings, now=now)
        auth.set_confirm_cookie(response, user, settings, now=now, session_id="s")
        auth.set_operator_cookie(
            response, OperatorClaims(operator_id="o", session_version=1, expires_at=later), settings, now=now
        )
        auth.set_flow_cookie(response, flow, settings, now=now)
        auth.set_pending_cookie(
            response,
            PendingClaims(
                provider="google", user_id="u", customer_id="c", session_version=1, expires_at=later
            ),
            settings,
            now=now,
        )
        auth.set_notice_cookie(response, "e@example.test", settings, now=now)
        for clear in (
            auth.clear_session_cookie,
            auth.clear_operator_cookie,
            auth.clear_flow_cookie,
            auth.clear_pending_cookie,
            auth.clear_notice_cookie,
        ):
            clear(response, settings)
        cookies = _set_cookies(response)
        assert len(cookies) == 11
        for cookie in cookies:
            flags = {part.strip().split("=")[0].lower(): part for part in cookie.split(";")[1:]}
            assert "httponly" in flags, cookie
            assert flags["samesite"].strip().lower() in ("samesite=lax", "samesite=strict"), cookie
            assert ("secure" in flags) is secure, cookie


def test_only_the_auth_module_sets_a_cookie() -> None:
    """A cookie set anywhere else would skip the flags checked above."""
    package = Path(auth.__file__).resolve().parents[1]
    setters = {
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        if re.search(r"\.(set_cookie|delete_cookie)\(", path.read_text(encoding="utf-8"))
    }
    assert setters == {"api/auth.py"}


def _cookie(client: TestClient) -> str:
    value = client.cookies.get(SESSION_COOKIE)
    assert value
    return value


def _with(api: Api, cookie: str) -> TestClient:
    other = api.client()
    other.cookies.set(SESSION_COOKIE, cookie)
    return other


def _ended(db: Database, customer_id: str) -> list[tuple[Any, ...]]:
    with db.customer(customer_id) as conn:
        rows = conn.execute(text("SELECT session_id::text, user_id::text, expires_at FROM ended_sessions"))
        return [tuple(row) for row in rows]


def _refused(reply: httpx.Response) -> None:
    assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), reply.text


def test_logout_ends_this_browsers_session_at_once_and_only_it(db: Database) -> None:
    """#502 item 1 (Tushar, 6 Nov 2026): a copied cookie stops working at once; the user's other
    browsers stay signed in."""
    api = Api(db)
    laptop = api.new_signed()
    phone = api.client()
    assert _login(phone, laptop.email).status_code == 200
    copied = _cookie(laptop.client)
    claims = auth.read_session(copied, api.settings.session_secret, int(api.clock().timestamp()))
    assert claims is not None

    assert laptop.client.post("/api/auth/logout", headers=CSRF).status_code == 204
    thief = _with(api, copied)
    _refused(thief.get("/api/auth/me"))
    _refused(thief.get("/api/documents"))
    _refused(thief.post("/api/auth/logout", headers=CSRF))
    assert phone.get("/api/auth/me").status_code == 200  # another device: still signed in
    assert _ended(db, laptop.customer_id) == [
        (claims.session_id, laptop.user_id, datetime.fromtimestamp(claims.expires_at, UTC))
    ]
    again = api.client()
    assert _login(again, laptop.email).status_code == 200  # a new sign-in is a new session
    assert _cookie(again) != copied
    assert again.get("/api/auth/me").status_code == 200


def test_a_cookie_from_before_0077a_without_a_session_id_is_refused(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    now = int(api.clock().timestamp())
    claims = auth.read_session(_cookie(signed.client), api.settings.session_secret, now)
    assert claims is not None
    old = {
        "uid": claims.user_id,
        "cid": claims.customer_id,
        "role": claims.role,
        "scope": claims.scope,
        "ver": claims.session_version,
        "exp": claims.expires_at,
    }
    token = auth._sign(old, auth._SESSION_PURPOSE, api.settings.session_secret)
    _refused(_with(api, token).get("/api/auth/me"))


def test_an_ended_session_is_swept_once_its_cookie_has_expired(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    copied = _cookie(signed.client)
    assert signed.client.post("/api/auth/logout", headers=CSRF).status_code == 204
    [(_, _, expires_at)] = _ended(db, signed.customer_id)

    sweep_ended_sessions(db, lambda: expires_at - timedelta(seconds=1))
    assert len(_ended(db, signed.customer_id)) == 1  # its cookie is still live: kept
    _refused(_with(api, copied).get("/api/auth/me"))
    assert sweep_ended_sessions(db, lambda: expires_at) >= 1
    assert _ended(db, signed.customer_id) == []
    api.clock.advance((expires_at - api.clock()).total_seconds())
    _refused(_with(api, copied).get("/api/auth/me"))  # swept, and refused by its own expiry


def test_the_operators_logout_ends_a_copied_operator_cookie(db: Database) -> None:
    """The operator's sessions are their own (``session_version``, 0015): logout ends them all."""
    api = Api(db)
    operator = _Operator(api)
    client = api.client()
    assert operator.login(client).status_code == 200
    copied = {"Cookie": f"{OPERATOR_COOKIE}={client.cookies.get(OPERATOR_COOKIE)}"}
    assert api.client().get("/api/operator/me", headers=copied).status_code == 200
    assert client.post("/api/operator/logout", headers=CSRF).status_code == 204
    _refused(api.client().get("/api/operator/me", headers=copied))


_FOREIGN_ROW = (
    "INSERT INTO ended_sessions (customer_id, session_id, user_id, expires_at) VALUES (:c, :s, :u, :e)"
)


def test_one_customer_never_sees_or_ends_another_customers_session(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    b_laptop = api.client()
    assert _login(b_laptop, b.email).status_code == 200
    assert b.client.post("/api/auth/logout", headers=CSRF).status_code == 204
    now = int(api.clock().timestamp())
    b_live = auth.read_session(_cookie(b_laptop), api.settings.session_secret, now)
    assert b_live is not None
    assert _ended(db, a.customer_id) == []  # B's ended session is not A's to see
    later = api.clock() + timedelta(hours=1)
    row = {"c": b.customer_id, "s": b_live.session_id, "u": b.user_id, "e": later}
    with pytest.raises(DBAPIError) as written, db.customer(a.customer_id) as conn:
        conn.execute(text(_FOREIGN_ROW), row)  # an ended row in B's name, from A
    assert sqlstate(written.value) == "42501"
    with pytest.raises(DBAPIError) as keyed, db.customer(a.customer_id) as conn:  # A's, naming B's user
        users_sql.end_session(conn, a.customer_id, b.user_id, b_live.session_id, expires_at=later)
    assert sqlstate(keyed.value) == "23503"
    with db.customer(a.customer_id) as conn:  # A's own user and B's session id: A's row only
        users_sql.end_session(conn, a.customer_id, a.user_id, b_live.session_id, expires_at=later)
    assert b_laptop.get("/api/auth/me").status_code == 200  # B's session is untouched
    assert a.client.get("/api/auth/me").status_code == 200


def test_the_ended_check_costs_no_query_of_its_own(db: Database) -> None:
    """One request reads the user once, and that one statement probes ended_sessions too."""
    api = Api(db)
    signed = api.new_signed()
    statements: list[str] = []

    def seen(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", seen)
    try:
        assert signed.client.get("/api/auth/me").status_code == 200
    finally:
        event.remove(db.engine, "before_cursor_execute", seen)
    probing = [statement for statement in statements if "ended_sessions" in statement]
    reading = [statement for statement in statements if "FROM users u JOIN customers" in statement]
    assert len(probing) == len(reading) == 1
    assert probing == reading


# --- 14. Google only --------------------------------------------------------------------------


def test_a_google_only_accounts_right_password_never_opens_a_session(
    db: Database, google: FakeGoogle
) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, email = api.add_user(customer_id, "member")
    _rules(api, customer_id, only=["member"], domains=[DOMAIN])
    _link(api, customer_id, member_id)
    client = api.client()
    for spelled in _variants(email):
        reply = _login(client, spelled)
        assert (reply.status_code, reply.json()["error"]["code"]) == (403, "password_refused"), spelled
        assert "set-cookie" not in reply.headers, spelled
    gone: Any = client.post(
        "/api/auth/admin/login", json={"email": email, "password": PASSWORD}, headers=CSRF
    )
    assert gone.status_code in (404, 410)
    assert "set-cookie" not in gone.headers
    assert client.get("/api/auth/me").status_code == 401
