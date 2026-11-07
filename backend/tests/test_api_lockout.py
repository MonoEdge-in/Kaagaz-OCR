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


def _from(api: Api, address: str) -> TestClient:
    """A browser at this visitor address (uvicorn takes it from Caddy's X-Forwarded-For)."""
    return TestClient(api.app, client=(address, 50000))


def test_five_wrong_passwords_lock_and_the_sixth_right_password_is_refused(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, "member")

    _fail(api, email, 5)
    state = api.user_state(customer_id, user_id)
    assert state["failed_logins"] == 5
    # Changed on purpose (A2, 7 Nov 2026): the lock is this address's, not the account's.
    assert state["locked_until"] is None

    sixth = _login(api.client(), email)
    assert sixth.status_code == 401
    assert sixth.json()["error"]["code"] == "bad_credentials"
    assert "set-cookie" not in sixth.headers
    # The refused sixth attempt writes nothing: the lock was audited when it started.
    assert api.audit_actions(customer_id) == ["auth.login_failed"] * 5 + ["auth.locked"]
    assert _audit_details(db, customer_id, "auth.login_failed") == [{"reason": "wrong_password"}] * 5


def test_a_flood_from_one_address_never_locks_the_person_out_of_their_own(db: Database) -> None:
    """A2 (stability pass, 7 Nov 2026): wrong passwords lock the account for that address only,
    and the refused attempts there are not counted toward the account's own cap."""
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, "member")
    attacker = _from(api, "203.0.113.7")
    for _ in range(5):
        assert _login(attacker, email, WRONG).status_code == 401
    for _ in range(100):  # refused before anything is counted or checked
        assert _login(attacker, email, WRONG).status_code == 401
    assert _login(attacker, email).status_code == 401  # locked here, even the right password

    assert api.user_state(customer_id, user_id)["failed_logins"] == 5
    assert _login(_from(api, "198.51.100.9"), email).status_code == 200  # the person, at home
    assert api.audit_actions(customer_id) == ["auth.login_failed"] * 5 + ["auth.locked", "auth.login"]


def test_fifty_wrong_passwords_from_anywhere_lock_the_account_everywhere(db: Database) -> None:
    """A2: the account's own cap, across every address, against a spread-out guesser."""
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, "member")
    for n in range(10):
        client = _from(api, f"203.0.113.{n}")
        for _ in range(5):
            assert _login(client, email, WRONG).status_code == 401

    state = api.user_state(customer_id, user_id)
    assert (state["failed_logins"], state["locked_until"]) == (50, api.clock() + timedelta(minutes=15))
    assert _login(_from(api, "198.51.100.9"), email).status_code == 401
    api.clock.advance(15 * 60 + 1)
    assert _login(_from(api, "198.51.100.9"), email).status_code == 200


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
    # Changed on purpose (UI quality council, 15 Oct 2026): one clear message, spelt e-mail.
    assert {reply.text for reply in replies.values()} == {
        '{"error":{"code":"bad_credentials","message":'
        '"E-mail or password is wrong, or the account is locked.'
        ' Check them, or wait 15 minutes, then try again."}}'
    }
    assert all("set-cookie" not in reply.headers for reply in replies.values())


def test_an_admins_failed_replies_are_identical_too(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    admin = api.enrol_admin(customer_id)
    locked = api.enrol_admin(customer_id)
    _, no_2fa_email = api.add_user(customer_id, "admin")
    client = api.client()
    for _ in range(5):
        assert api.admin_login(client, locked.email, password=WRONG).status_code == 401

    replies = {
        "locked": api.admin_login(client, locked.email, code=api.code(locked.secret)),
        "unknown": api.admin_login(client, "nobody-at-all@example.test", code="123456"),
        "wrong password": api.admin_login(client, admin.email, password=WRONG, code=api.code(admin.secret)),
        "wrong code": api.admin_login(client, admin.email, code=wrong_code(api, admin.secret)),
        "wrong password, no code": api.admin_login(client, admin.email, password=WRONG),
        "2FA off, wrong password": api.admin_login(client, no_2fa_email, password=WRONG),
    }

    assert {reply.status_code for reply in replies.values()} == {401}
    assert len({reply.text for reply in replies.values()}) == 1
    assert replies["locked"].json()["error"]["code"] == "bad_credentials"
    # Only a right password learns that a code is needed, or must be set up (0035).
    no_code = api.admin_login(client, admin.email)
    assert (no_code.status_code, no_code.json()["error"]["code"]) == (401, "code_required")
    set_up = api.admin_login(client, no_2fa_email)
    assert (set_up.status_code, set_up.json()["error"]["code"]) == (403, "mfa_enrol_required")


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

    # Changed on purpose (A2): the account's count (cap 50) goes on; the address's started again.
    assert api.user_state(customer_id, user_id)["failed_logins"] == 9
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

    # Changed on purpose (A2): the lock is this address's, for the same 7 minutes.
    assert reply.status_code == 401 and api.user_state(customer_id, user_id)["locked_until"] is None
    api.clock.advance(7 * 60 - 1)
    assert _login(api.client(), email).status_code == 401
    api.clock.advance(2)
    assert _login(api.client(), email).status_code == 200
    # Changed on purpose (UI quality council, 15 Oct 2026): one clear message, spelt e-mail.
    assert reply.json()["error"]["message"] == (
        "E-mail or password is wrong, or the account is locked."
        " Check them, or wait 7 minutes, then try again."
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
        "admin: right code": lambda: api.admin_login(api.client(), admin.email, code=api.code(admin.secret)),
        "admin: 2FA off, the set-up offer": lambda: api.admin_login(api.client(), no_2fa_email),
    }
    for name, attempt in attempts.items():
        hashes.clear()
        attempt()
        assert len(hashes) == 1, name
