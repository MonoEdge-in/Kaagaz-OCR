"""POST /api/auth/signup (16 Oct 2026): a new organisation and its first admin, who sets up
two-step sign-in before the first session (0035).

The first half needs no database: the switch, the body rules and the per-address limit
are checked before it is reached. The second half (marked ``db``) signs up for real.
"""

import base64
import uuid
from collections.abc import Iterator
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.api import totp
from kaagaz.api.app import create_app
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.names import ORG_NAME_PROBLEM
from kaagaz.api.routers import auth as auth_router
from kaagaz.db.engine import Database, make_engine
from tests.settings_factory import make_settings
from tests.test_api_support import (
    CSRF,
    NOW,
    PASSWORD,
    Api,
    ApiFakeQueue,
    ApiFakeStorage,
    FakeAnswers,
    TestClient,
)

URL = "/api/auth/signup"


def _body(email: str = "owner@example.test", **changes: str) -> dict[str, str]:
    return {
        "organisation_name": "Sharma Traders",
        "name": "Asha Sharma",
        "email": email,
        "password": PASSWORD,
        **changes,
    }


@pytest.fixture(autouse=True)
def _fresh_limit(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts with a full allowance: the limiter is one a process."""
    monkeypatch.setattr(
        auth_router, "signup_limits", RateLimiter(auth_router.SIGNUPS_PER_HOUR, window_seconds=3600)
    )
    yield


def _offline_client(**settings: object) -> TestClient:
    """An app whose database is never reached (the URL points nowhere)."""
    db = Database(make_engine("postgresql://unused@localhost:1/unused"), "kaagaz_app")
    app = create_app(
        make_settings(**settings),
        db=db,
        storage=ApiFakeStorage(),
        queue=ApiFakeQueue(),
        answers=FakeAnswers(),
        clock=lambda: NOW,
    )
    return TestClient(app)


# --- refused before the database ---------------------------------------------------


def test_the_switch_says_whether_sign_up_is_open() -> None:
    # Changed on purpose (0038): on also asks the super admin's switch, so "open" is a db test
    # (test_ai_settings_db); off by the setting never reaches the database.
    assert _offline_client(self_signup_enabled=False).get(URL).json() == {"enabled": False}


def test_switched_off_it_is_404() -> None:
    reply = _offline_client(self_signup_enabled=False).post(URL, json=_body(), headers=CSRF)
    assert reply.status_code == 404
    assert reply.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "changes",
    [
        {"password": "short pass"},  # 10 characters: under MIN_PASSWORD
        {"organisation_name": "   "},
        {"name": ""},
        {"email": "no-at-sign"},
    ],
)
def test_a_weak_password_a_blank_name_or_a_bad_email_is_400(changes: dict[str, str]) -> None:
    reply = _offline_client().post(URL, json=_body(**changes), headers=CSRF)
    assert reply.status_code == 400
    assert reply.json()["error"]["code"] == "bad_request"
    assert PASSWORD not in reply.text


def test_a_placeholder_organisation_name_is_400_in_plain_words() -> None:
    reply = _offline_client().post(URL, json=_body(organisation_name="asd"), headers=CSRF)
    assert reply.status_code == 400
    assert reply.json()["error"] == {"code": "bad_request", "message": ORG_NAME_PROBLEM}


def test_it_needs_the_csrf_header() -> None:
    assert _offline_client().post(URL, json=_body()).status_code == 403


def test_at_most_five_sign_ups_an_hour_from_one_address() -> None:
    client = _offline_client()
    # A bad e-mail is refused after it is counted, so no database is needed.
    for _ in range(auth_router.SIGNUPS_PER_HOUR):
        assert client.post(URL, json=_body("no-at-sign"), headers=CSRF).status_code == 400

    reply = client.post(URL, json=_body(), headers=CSRF)

    assert reply.status_code == 429
    assert reply.headers["Retry-After"] == "3600"
    assert reply.json() == {
        "error": {"code": "rate_limited", "message": "Too many sign-ups from here. Try again in an hour."}
    }


def test_the_limiter_window_refills_over_an_hour() -> None:
    now = [0.0]
    limiter = RateLimiter(5, window_seconds=3600, clock=lambda: now[0])
    assert all(limiter.allow("ip") for _ in range(5))
    assert not limiter.allow("ip")
    now[0] += 3600 / 5 - 1
    assert not limiter.allow("ip")
    now[0] += 1
    assert limiter.allow("ip")


# --- against the database ------------------------------------------------------------


def _enrolled(api: Api, client: TestClient, reply: httpx.Response) -> tuple[dict[str, Any], bytes]:
    """A sign-up's reply is the two-step set-up offer (0035): enrolled with its first code,
    the Me of the new admin session, and the secret."""
    offer = reply.json()
    secret = base64.b32decode(offer["secret"].replace(" ", ""))
    enrolled = client.post(
        "/api/auth/admin/enrol",
        json={"enrol_token": offer["enrol_token"], "code": api.code(secret)},
        headers=CSRF,
    )
    assert enrolled.status_code == 200, enrolled.text
    return enrolled.json(), secret


@pytest.mark.db
def test_sign_up_makes_the_organisation_and_its_admin_who_sets_up_two_step(db: Database) -> None:
    api = Api(db)
    client = api.client()
    # Fixed (#239, 27 Oct 2026): an e-mail of its own, so a database used before cannot have it.
    token = uuid.uuid4().hex[:12]

    reply = client.post(URL, json=_body(f"  Owner.Signup.{token}@Example.TEST "), headers=CSRF)

    assert reply.status_code == 201, reply.text
    assert set(reply.json()) == {"enrol_token", "secret", "otpauth_uri"}
    assert "set-cookie" not in reply.headers  # no session before the first code
    assert client.get("/api/auth/me").status_code == 401
    me, secret = _enrolled(api, client, reply)
    customer_id, user_id = me["customer"]["id"], me["user"]["id"]
    assert me["customer"]["name"] == "Sharma Traders"
    assert me["user"]["email"] == f"owner.signup.{token}@example.test"
    assert me["user"]["display_name"] == "Asha Sharma"
    assert me["user"]["role"] == "admin"
    assert me["user"]["must_change_password"] is False  # their own password
    assert me["user"]["totp_enabled"] is True
    assert me["session"] == {"scope": "admin"}
    assert client.get("/api/admin/users").status_code == 200  # an admin session
    with db.customer(customer_id) as conn:
        rows = conn.execute(
            text("SELECT action, actor_user_id, target_id FROM audit_log WHERE customer_id = :c ORDER BY id"),
            {"c": customer_id},
        ).all()
    assert (rows[0][0], str(rows[0][1]), rows[0][2]) == ("org.self_signup", user_id, user_id)
    # The password they chose and the next code sign them in again.
    api.clock.advance(totp.STEP_SECONDS)
    again = api.admin_login(api.client(), f"owner.signup.{token}@example.test", code=api.code(secret))
    assert again.status_code == 200, again.text


@pytest.mark.db
def test_a_taken_email_is_the_admin_screens_409_and_makes_nothing(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer("Existing Co")
    _, email = api.add_user(customer_id, "member")

    reply = api.client().post(URL, json=_body(email.upper(), organisation_name="Copycat Co"), headers=CSRF)

    assert reply.status_code == 409
    assert reply.json() == {"error": {"code": "conflict", "message": "That e-mail is already in use."}}
    assert "set-cookie" not in reply.headers
    with db.system() as conn:  # no half-made organisation: the statement rolled back
        made = conn.execute(text("SELECT kaagaz_login_lookup(:e)"), {"e": email}).all()
    assert len(made) == 1


@pytest.mark.db
@pytest.mark.isolation
def test_the_new_admin_sees_only_their_own_organisation(db: Database) -> None:
    api = Api(db)
    other_id, _ = api.customer("Other Co")
    other_doc = api.add_document(other_id, name="other-invoice.pdf")
    api.add_user(other_id, "member")
    client = api.client()
    # Fixed (#239, 27 Oct 2026): CI runs this twice on one database; a fixed e-mail is taken then.
    email = f"isolated-{uuid.uuid4().hex[:12]}@example.test"

    reply = client.post(URL, json=_body(email), headers=CSRF)
    assert reply.status_code == 201, reply.text
    own_id = _enrolled(api, client, reply)[0]["customer"]["id"]
    assert own_id != other_id

    assert client.get(f"/api/documents/{other_doc}").status_code == 404
    assert client.get("/api/documents").json()["items"] == []
    users = client.get("/api/admin/users").json()["items"]
    assert [user["email"] for user in users] == [email]
