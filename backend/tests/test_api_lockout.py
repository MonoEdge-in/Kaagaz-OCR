"""Lockout (PRD 1 A3): five wrong passwords in a row lock an account for 15 minutes.

What the council asked for, each with a test here: the attempt is counted
before the password is checked (so parallel guesses cannot slip past the
count), every sign-in path spends exactly one password hash (so the time says
nothing), and a locked account, an unknown e-mail and a wrong password get the
very same reply. The clock is the app's own, moved forward by the test.
"""

import hashlib
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.api.auth import spend_password_time
from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, PASSWORD, Api, TestClient, wrong_code

pytestmark = pytest.mark.db

WRONG = "not the password!!"
NEW_PASSWORD = "a brand new password"


def _login(client: TestClient, email: str, password: str = PASSWORD) -> httpx.Response:
    return client.post("/api/auth/login", json={"email": email, "password": password}, headers=CSRF)


def _fail(api: Api, email: str, times: int) -> None:
    client = api.client()
    for _ in range(times):
        assert _login(client, email, WRONG).status_code == 401


def _audit_details(db: Database, customer_id: str, action: str) -> list[Any]:
    with db.customer(customer_id) as conn:
        return list(
            conn.execute(
                text("SELECT details FROM audit_log WHERE action = :a ORDER BY id"), {"a": action}
            ).scalars()
        )


def test_five_wrong_passwords_lock_and_the_sixth_right_password_is_refused(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, "member")

    _fail(api, email, 5)
    state = api.user_state(customer_id, user_id)
    assert state["failed_logins"] == 5
    assert state["locked_until"] == api.clock() + timedelta(minutes=15)

    sixth = _login(api.client(), email)
    assert sixth.status_code == 401
    assert sixth.json()["error"]["code"] == "bad_credentials"
    assert "set-cookie" not in sixth.headers
    assert api.audit_actions(customer_id) == ["auth.login_failed"] * 5 + ["auth.locked", "auth.login_failed"]
    assert _audit_details(db, customer_id, "auth.login_failed") == [{"reason": "wrong_password"}] * 5 + [
        {"reason": "locked"}
    ]


def test_locked_unknown_wrong_and_disabled_replies_are_identical(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    _, email = api.add_user(customer_id, "member")
    _, locked_email = api.add_user(customer_id, "member")
    disabled_id, disabled_email = api.add_user(customer_id, "member")
    _fail(api, locked_email, 5)
    with db.customer(customer_id) as conn:
        conn.execute(text("UPDATE users SET disabled = true WHERE id = :u"), {"u": disabled_id})
    client = api.client()

    replies = {
        "locked": _login(client, locked_email),
        "unknown": _login(client, "nobody-at-all@example.test"),
        "wrong": _login(client, email, WRONG),
        "disabled": _login(client, disabled_email),
    }

    assert {reply.status_code for reply in replies.values()} == {401}
    assert {reply.text for reply in replies.values()} == {
        '{"error":{"code":"bad_credentials","message":'
        '"Email or password is wrong, or the account is locked. Try again in 15 minutes."}}'
    }
    assert all("set-cookie" not in reply.headers for reply in replies.values())


def test_the_admin_screen_replies_are_identical_too(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    admin = api.enrol_admin(customer_id)
    locked = api.enrol_admin(customer_id)
    _, member_email = api.add_user(customer_id, "member")
    _, no_2fa_email = api.add_user(customer_id, "admin")
    client = api.client()
    for _ in range(5):
        assert api.admin_login(client, locked.email, password=WRONG).status_code == 401

    replies = {
        "locked": api.admin_login(client, locked.email, code=api.code(locked.secret)),
        "unknown": api.admin_login(client, "nobody-at-all@example.test", code="123456"),
        "wrong password": api.admin_login(client, admin.email, password=WRONG, code=api.code(admin.secret)),
        "wrong code": api.admin_login(client, admin.email, code=wrong_code(api, admin.secret)),
        "no code": api.admin_login(client, admin.email),
        "not an admin": api.admin_login(client, member_email),
        "2FA off, wrong password": api.admin_login(client, no_2fa_email, password=WRONG),
    }

    assert {reply.status_code for reply in replies.values()} == {401}
    assert len({reply.text for reply in replies.values()}) == 1
    assert replies["locked"].json()["error"]["code"] == "bad_credentials"


def test_an_expired_lock_lets_the_right_password_in_and_resets(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, "member")
    _fail(api, email, 5)

    api.clock.advance(15 * 60 - 1)
    assert _login(api.client(), email).status_code == 401  # still locked
    api.clock.advance(2)
    reply = _login(api.client(), email)

    assert reply.status_code == 200, reply.text
    state = api.user_state(customer_id, user_id)
    assert state["failed_logins"] == 0
    assert state["locked_until"] is None


def test_after_an_expired_lock_the_count_starts_again(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, "member")
    _fail(api, email, 5)
    api.clock.advance(15 * 60 + 1)

    _fail(api, email, 4)

    assert api.user_state(customer_id, user_id)["failed_logins"] == 4
    assert _login(api.client(), email).status_code == 200  # 4 in a row do not lock


def test_a_right_password_resets_the_count(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, "member")

    _fail(api, email, 4)
    assert _login(api.client(), email).status_code == 200
    assert api.user_state(customer_id, user_id)["failed_logins"] == 0
    _fail(api, email, 4)
    assert _login(api.client(), email).status_code == 200


def test_unlock_clears_the_counter(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    user_id, email = api.add_user(admin.customer_id, "member")
    _fail(api, email, 5)

    reply = admin.client.post(f"/api/admin/users/{user_id}/unlock", headers=CSRF)

    assert reply.status_code == 200, reply.text
    assert reply.json()["id"] == user_id
    assert reply.json()["locked_until"] is None
    state = api.user_state(admin.customer_id, user_id)
    assert state["failed_logins"] == 0
    assert state["locked_until"] is None
    _fail(api, email, 4)  # counted from zero again, so 4 more do not lock
    assert _login(api.client(), email).status_code == 200
    assert "user.unlock" in api.audit_actions(admin.customer_id)


def test_a_wrong_current_password_counts_toward_the_lockout(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()

    for _ in range(5):
        reply = signed.client.post(
            "/api/auth/password",
            json={"current_password": WRONG, "new_password": NEW_PASSWORD},
            headers=CSRF,
        )
        assert reply.status_code == 400
        assert reply.json()["error"]["code"] == "bad_request"

    assert api.user_state(signed.customer_id, signed.user_id)["failed_logins"] == 5
    assert _login(api.client(), signed.email).status_code == 401
    while_locked = signed.client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=CSRF,
    )
    assert while_locked.status_code == 400
    api.clock.advance(15 * 60 + 1)
    assert _login(api.client(), signed.email).status_code == 200  # the password did not change


def test_the_limits_come_from_the_settings(db: Database) -> None:
    api = Api(db, settings=make_settings(login_max_failures=2, login_lock_minutes=7))
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, "member")

    _fail(api, email, 2)
    reply = _login(api.client(), email)

    assert api.user_state(customer_id, user_id)["locked_until"] == api.clock() + timedelta(minutes=7)
    assert reply.json()["error"]["message"] == (
        "Email or password is wrong, or the account is locked. Try again in 7 minutes."
    )


def test_every_attempt_spends_exactly_one_hash(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    _, email = api.add_user(customer_id, "member")
    disabled_id, disabled_email = api.add_user(customer_id, "member")
    _, locked_email = api.add_user(customer_id, "member")
    admin = api.enrol_admin(customer_id)
    locked_admin = api.enrol_admin(customer_id)
    _, no_2fa_email = api.add_user(customer_id, "admin")
    with db.customer(customer_id) as conn:
        conn.execute(text("UPDATE users SET disabled = true WHERE id = :u"), {"u": disabled_id})
    _fail(api, locked_email, 5)
    for _ in range(5):
        api.admin_login(api.client(), locked_admin.email, password=WRONG)
    spend_password_time("warm up the dummy hash")
    client = api.client()

    hashes: list[int] = []
    real_scrypt = hashlib.scrypt

    def counting_scrypt(*args: Any, **kwargs: Any) -> bytes:
        hashes.append(1)
        return real_scrypt(*args, **kwargs)

    monkeypatch.setattr(hashlib, "scrypt", counting_scrypt)

    attempts: dict[str, Callable[[], httpx.Response]] = {
        "unknown e-mail": lambda: _login(client, "nobody-at-all@example.test"),
        "wrong password": lambda: _login(client, email, WRONG),
        "disabled": lambda: _login(client, disabled_email),
        "locked": lambda: _login(client, locked_email),
        "right password": lambda: _login(api.client(), email),
        "admin: unknown e-mail": lambda: api.admin_login(client, "nobody-at-all@example.test"),
        "admin: wrong password": lambda: api.admin_login(client, admin.email, password=WRONG),
        "admin: wrong code": lambda: api.admin_login(client, admin.email, code=wrong_code(api, admin.secret)),
        "admin: locked": lambda: api.admin_login(
            client, locked_admin.email, code=api.code(locked_admin.secret)
        ),
        "admin: not an admin": lambda: api.admin_login(client, email),
        "admin: right code": lambda: api.admin_login(api.client(), admin.email, code=api.code(admin.secret)),
        "admin: 2FA off, password alone": lambda: api.admin_login(api.client(), no_2fa_email),
    }
    for name, attempt in attempts.items():
        hashes.clear()
        attempt()
        assert len(hashes) == 1, name
