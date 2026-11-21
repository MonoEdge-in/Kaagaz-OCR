"""Email (#457 phase 0) through the API: confirm your email, forgot password, invites,
Change password with the code, security notices, and sign-ups that wait for an operator.

SES is faked with ``MemoryMailer``; a failing mailer raises botocore's ClientError. Links
are read back from the sent text (``#t=`` in the fragment). The first test needs no
database; the rest (marked ``db``) run against a real PostgreSQL.
"""

import base64
import logging
import re
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx2 as httpx
import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text

from kaagaz.adapters.email import MemoryMailer
from kaagaz.api import totp
from kaagaz.api.app import create_app
from kaagaz.api.emails import token_hash
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers import auth as auth_router
from kaagaz.api.routers import email as email_router
from kaagaz.db import email_links as links_sql
from kaagaz.db import identities
from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database, make_engine
from tests.oidc_fake import ISSUER, google_settings
from tests.settings_factory import make_settings
from tests.test_api_support import (
    CSRF,
    EMAIL_ROUTES,
    NOW,
    PASSWORD,
    Api,
    ApiFakeQueue,
    ApiFakeStorage,
    FakeAnswers,
    TestClient,
    wrong_code,
)
from tests.test_operator_auth_db import _Operator

SITE = "https://kaagaz.example.com"
SENDER = "no-reply@example.com"
NEW_PASSWORD = "a brand new made-up password"
LINK_GONE = {"error": {"code": "link_expired", "message": "That link has expired or was already used."}}
FORGOT_REPLY = {"message": "If that email has an account, we've sent a link."}
RESEND_REPLY = {"message": "If that email is waiting to be confirmed, we've sent a new link."}
_TOKEN = re.compile(r"#t=([A-Za-z0-9_-]+)")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class FailingMailer(MemoryMailer):
    def send(self, *, to: str, subject: str, text: str) -> None:
        raise ClientError({"Error": {"Code": "MessageRejected", "Message": "no"}}, "SendEmail")


class BrokenMailer(MemoryMailer):
    """Breaks in a way ``send`` does not expect: never SES's own errors."""

    def send(self, *, to: str, subject: str, text: str) -> None:
        raise RuntimeError("the mailer broke")


def _task_failed(purpose: str) -> str:
    return f'{{"event":"email_task_failed","purpose":"{purpose}","error":"RuntimeError"}}'


@pytest.fixture(autouse=True)
def _fresh_limits(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts with a full allowance: the limiters are one a process."""
    for name in (
        "confirm_limits",
        "forgot_site_limits",
        "forgot_email_limits",
        "resend_site_limits",
        "resend_email_limits",
        "link_address_limits",
    ):
        limiter = getattr(email_router, name)
        monkeypatch.setattr(email_router, name, RateLimiter(int(limiter._capacity), window_seconds=3600))
    monkeypatch.setattr(
        auth_router, "signup_limits", RateLimiter(auth_router.SIGNUPS_PER_HOUR, window_seconds=3600)
    )
    yield


def _email_api(db: Database, mailer: MemoryMailer | None = None) -> tuple[Api, MemoryMailer]:
    mailer = MemoryMailer() if mailer is None else mailer
    settings = make_settings(email_enabled=True, email_from=SENDER, app_url=SITE)
    return Api(db, settings=settings, mailer=mailer), mailer


def _to(mailer: MemoryMailer, address: str) -> list[tuple[str, str]]:
    return [(sent.subject, sent.text) for sent in mailer.sent if sent.to == address]


def _token(mailer: MemoryMailer, address: str, page: str) -> str:
    """The token of the last link to ``address``, which must open ``page`` on APP_URL."""
    _, body = _to(mailer, address)[-1]
    assert f"{SITE}{page}#t=" in body, body
    match = _TOKEN.search(body)
    assert match is not None
    return match.group(1)


def _post(client: TestClient, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
    return client.post(path, json=body or {}, headers=CSRF)


def _email_confirmed(db: Database, customer_id: str, user_id: str) -> bool:
    with db.customer(customer_id) as conn:
        verified = conn.execute(
            text("SELECT email_verified_at FROM users WHERE customer_id = :c AND id = :u"),
            {"c": customer_id, "u": user_id},
        ).scalar()
    return verified is not None


# --- off ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), EMAIL_ROUTES)
def test_every_email_route_is_404_while_email_is_off(method: str, path: str) -> None:
    db = Database(make_engine("postgresql://unused@localhost:1/unused"), "kaagaz_app")
    app = create_app(
        make_settings(),
        db=db,
        storage=ApiFakeStorage(),
        queue=ApiFakeQueue(),
        answers=FakeAnswers(),
        clock=lambda: NOW,
    )
    body = {"token": "x" * 43, "new_password": NEW_PASSWORD, "email": "ramesh@example.com"}
    reply = TestClient(app).request(method, path, json=None if method == "GET" else body, headers=CSRF)
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")


# --- sign-up waits for its email and an operator --------------------------------------------


@pytest.mark.db
def test_a_sign_up_confirms_its_email_then_waits_for_an_operators_approval(db: Database) -> None:
    api, mailer = _email_api(db)
    token = uuid.uuid4().hex[:12]
    email, org_name = f"owner-{token}@example.com", f"Sharma Traders {token}"
    body = {
        "organisation_name": org_name,
        "name": "Asha Sharma",
        "email": email,
        "password": PASSWORD,
    }

    signed_up = _post(api.client(), "/api/auth/signup", body)
    # Changed on purpose (#457 review): the reply says whether the link went.
    assert (signed_up.status_code, signed_up.json()) == (201, {"status": "confirm_email", "sent": True})
    assert "set-cookie" not in signed_up.headers  # no session, no two-step offer
    [(subject, _)] = _to(mailer, email)
    assert subject == "Confirm your email for Kaagaz"

    waiting = api.admin_login(api.client(), email)
    assert waiting.status_code == 403
    assert waiting.json()["error"] == {
        "code": "org_pending",
        "message": "Your organisation is waiting for approval.",
    }
    assert "set-cookie" not in waiting.headers
    wrong = api.admin_login(api.client(), email, password="not the right password")
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (401, "bad_credentials")  # says nothing

    operator, ops = _Operator(api), api.client()
    assert operator.login(ops).status_code == 200
    api.clock.advance(totp.STEP_SECONDS)
    [org] = [item for item in ops.get("/api/operator/orgs").json()["items"] if item["name"] == org_name]
    assert (org["status"], org["admin_email_confirmed"]) == ("pending", False)
    approve = f"/api/operator/orgs/{org['id']}/approve"
    refused = _post(ops, approve)
    assert refused.status_code == 409
    assert refused.json()["error"]["message"] == "The organisation's admin has not confirmed their email yet."
    enabled = _post(ops, f"/api/operator/orgs/{org['id']}/enable")  # no way round Approve
    assert (enabled.status_code, enabled.json()["error"]["code"]) == (409, "conflict")

    token = _token(mailer, email, "/email/confirm")
    # Changed on purpose (#457 review): ``pending``, so the page says the sign-up waits for approval.
    confirmed = _post(api.client(), "/api/auth/email/confirm", {"token": token})
    assert confirmed.json() == {"ok": True, "pending": True}
    [org] = [item for item in ops.get("/api/operator/orgs").json()["items"] if item["id"] == org["id"]]
    assert org["admin_email_confirmed"] is True
    approved = _post(ops, approve)
    assert approved.json() == {"organisation": {"id": org["id"], "status": "active", "changed": True}}
    assert _post(ops, approve).status_code == 409  # once

    # Signed in now: the admin sets up the two-step code first, as every admin (0035).
    reply = api.admin_login(api.client(), email)
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "mfa_enrol_required")
    actions = api.audit_actions(org["id"])
    # Changed on purpose (#457 review): the right password while pending is not a failure, so
    # only the wrong one is audited (and counted).
    assert actions[:3] == ["org.self_signup", "auth.login_failed", "auth.email_confirmed"]
    assert "org.approved" in actions


# --- confirm your email ------------------------------------------------------------------------


@pytest.mark.db
def test_a_confirm_link_works_once_only_the_newest_works_and_it_expires(db: Database) -> None:
    api, mailer = _email_api(db)
    customer_id, _ = api.customer()
    member = api.sign_in(customer_id, "member")
    assert member.client.get("/api/auth/me").json()["user"]["email_confirmed"] is False  # the banner
    assert api.client().get("/api/auth/email").json() == {"enabled": True}  # "Forgot password?" shows

    # Changed on purpose (#457 review): the reply says whether the link went.
    assert _post(member.client, "/api/auth/email/send-confirm").json() == {"ok": True, "sent": True}
    first = _token(mailer, member.email, "/email/confirm")
    _post(member.client, "/api/auth/email/send-confirm")
    newest = _token(mailer, member.email, "/email/confirm")
    assert _post(api.client(), "/api/auth/email/confirm", {"token": first}).json() == LINK_GONE  # replaced
    confirmed = _post(api.client(), "/api/auth/email/confirm", {"token": newest}).json()
    assert confirmed == {"ok": True, "pending": False}  # Changed on purpose: an active organisation
    reused = _post(api.client(), "/api/auth/email/confirm", {"token": newest})
    assert (reused.status_code, reused.json()) == (400, LINK_GONE)
    assert member.client.get("/api/auth/me").json()["user"]["email_confirmed"] is True
    assert api.audit_actions(customer_id)[-1] == "auth.email_confirmed"

    other = api.sign_in(customer_id, "reviewer")
    _post(other.client, "/api/auth/email/send-confirm")
    late = _token(mailer, other.email, "/email/confirm")
    api.clock.advance(24 * 3600)  # 24 hours: no longer
    assert _post(api.client(), "/api/auth/email/confirm", {"token": late}).json() == LINK_GONE
    assert _email_confirmed(db, customer_id, other.user_id) is False


def _pending_sign_up(api: Api) -> str:
    """A new sign-up's admin e-mail: its organisation pending, its e-mail not confirmed."""
    token = uuid.uuid4().hex[:12]
    email = f"owner-{token}@example.com"
    body = {"organisation_name": f"Mehta Textiles {token}", "name": "Ramesh", "email": email}
    signed_up = _post(api.client(), "/api/auth/signup", {**body, "password": PASSWORD})
    assert signed_up.status_code == 201, signed_up.text
    return email


def _age(db: Database, email: str, hours: int) -> str:
    """The sign-up of ``email`` made ``hours`` hours ago (as the owner); its customer id."""
    with db.engine.begin() as conn:
        return str(
            conn.execute(
                text(
                    "UPDATE customers SET created_at = now() - make_interval(hours => :h)"
                    " WHERE id = (SELECT customer_id FROM users WHERE email = :e) RETURNING id"
                ),
                {"h": hours, "e": email},
            ).scalar_one()
        )


def _customer_exists(db: Database, customer_id: str) -> bool:
    with db.engine.begin() as conn:
        return (
            conn.execute(text("SELECT count(*) FROM customers WHERE id = :c"), {"c": customer_id}).scalar()
            == 1
        )


@pytest.mark.db
def test_a_stale_unconfirmed_sign_up_no_longer_holds_its_email(db: Database) -> None:
    """A sign-up never confirmed within the link's 24 hours gives the e-mail back to sign-up,
    an admin's Add person and the operator's new organisation."""
    api, mailer = _email_api(db)
    held = _pending_sign_up(api)
    fresh = _pending_sign_up(api)
    _age(db, fresh, 23)
    old = _age(db, held, 25)
    body = {
        "organisation_name": f"Sharma Traders {uuid.uuid4().hex[:6]}",
        "name": "Asha",
        "password": PASSWORD,
    }
    again = _post(api.client(), "/api/auth/signup", {**body, "email": held})
    assert (again.status_code, again.json()) == (201, {"status": "confirm_email", "sent": True})
    assert not _customer_exists(db, old)
    assert len(_to(mailer, held)) == 2  # the old sign-up's link, then the new one's
    assert len(_to(mailer, fresh)) == 1
    _post(api.client(), "/api/auth/signup", {**body, "email": fresh})  # still held: nothing sent
    assert len(_to(mailer, fresh)) == 1


@pytest.mark.db
def test_a_sign_up_with_a_taken_email_answers_like_a_new_one_and_sends_nothing(db: Database) -> None:
    """With email on, sign-up never shows who has an account: the same 201, and no email."""
    api, mailer = _email_api(db)
    member = api.new_signed("member")
    body = {
        "organisation_name": f"Sharma Traders {uuid.uuid4().hex[:6]}",
        "name": "Asha",
        "password": PASSWORD,
    }
    reply = _post(api.client(), "/api/auth/signup", {**body, "email": member.email.upper()})
    assert (reply.status_code, reply.json()) == (201, {"status": "confirm_email", "sent": True})
    assert mailer.sent == []


@pytest.mark.db
def test_send_again_without_signing_in_sends_only_to_a_pending_unconfirmed_admin(db: Database) -> None:
    """A pending organisation's admin cannot sign in, so "Send the link again" needs no session.
    The reply is the same whoever is asked for, so it never shows who is waiting."""
    api, mailer = _email_api(db)
    pending = _pending_sign_up(api)
    member = api.new_signed("member")  # unconfirmed, but its organisation is active
    confirmed = _pending_sign_up(api)
    _post(api.client(), "/api/auth/email/confirm", {"token": _token(mailer, confirmed, "/email/confirm")})
    sent_before = len(mailer.sent)
    client = api.client()

    replies = [
        _post(client, "/api/auth/email/resend-confirm", {"email": address})
        for address in (pending.upper(), member.email, confirmed, "nobody@example.com")
    ]
    assert {(reply.status_code, str(reply.json())) for reply in replies} == {(202, str(RESEND_REPLY))}
    assert [sent.to for sent in mailer.sent[sent_before:]] == [pending]
    first = _token(mailer, pending, "/email/confirm")
    assert _post(client, "/api/auth/email/confirm", {"token": first}).json() == {"ok": True, "pending": True}
    assert _post(client, "/api/auth/email/resend-confirm", {"email": pending}).status_code == 202
    assert len(mailer.sent) == sent_before + 1  # confirmed now: nothing more is sent


@pytest.mark.db
def test_send_again_without_signing_in_replies_first_and_is_limited_by_email(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, mailer = _email_api(db)
    pending = _pending_sign_up(api)
    client = api.client()
    for _ in range(4):  # the fourth is answered the same, and sends nothing
        assert _post(client, "/api/auth/email/resend-confirm", {"email": pending}).status_code == 202
    assert len(_to(mailer, pending)) == 1 + 3  # the sign-up's own link, then three

    later: list[str] = []
    monkeypatch.setattr(email_router, "_send_link", lambda context, email, *_: later.append(email))
    assert _post(client, "/api/auth/email/resend-confirm", {"email": pending}).json() == RESEND_REPLY
    assert later == [pending]  # the lookup happens after the reply


@pytest.mark.db
def test_send_again_is_three_an_hour(db: Database) -> None:
    api, mailer = _email_api(db)
    member = api.new_signed("member")
    for _ in range(3):
        assert _post(member.client, "/api/auth/email/send-confirm").status_code == 200
    refused = _post(member.client, "/api/auth/email/send-confirm")
    assert (refused.status_code, refused.json()["error"]["code"]) == (429, "rate_limited")
    assert len(_to(mailer, member.email)) == 3


@pytest.mark.db
def test_a_wrong_link_or_a_link_for_something_else_says_so(db: Database) -> None:
    api, mailer = _email_api(db)
    member = api.new_signed("member")
    _post(api.client(), "/api/auth/forgot", {"email": member.email})
    reset = _token(mailer, member.email, "/reset-password")
    client = api.client()

    for path, body in (
        ("/api/auth/email/confirm", {"token": "x" * 43}),
        ("/api/auth/email/confirm", {"token": reset}),  # a reset link does not confirm
    ):
        reply = _post(client, path, body)
        assert (reply.status_code, reply.json()) == (400, LINK_GONE), path
    assert _post(client, "/api/auth/reset", {"token": reset, "new_password": NEW_PASSWORD}).json() == {
        "ok": True
    }


# --- forgot password ----------------------------------------------------------------------------


@pytest.mark.db
def test_forgot_password_answers_the_same_whoever_is_asked_for(db: Database) -> None:
    api, mailer = _email_api(db)
    member = api.new_signed("member")
    operator = _Operator(api)
    client = api.client()

    replies = [
        _post(client, "/api/auth/forgot", {"email": address})
        for address in (member.email.upper(), f"nobody-{uuid.uuid4().hex[:8]}@example.com", operator.email)
    ]
    assert {(reply.status_code, str(reply.json())) for reply in replies} == {(202, str(FORGOT_REPLY))}
    assert [sent.to for sent in mailer.sent] == [member.email]  # operators are never found
    [(subject, _)] = _to(mailer, member.email)
    assert subject == "Reset your Kaagaz password"


@pytest.mark.db
def test_forgot_password_looks_nothing_up_before_it_replies(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same path for every address: the reply goes first, the lookup after it."""
    api, _ = _email_api(db)
    member = api.new_signed("member")
    later: list[str] = []
    monkeypatch.setattr(email_router, "_send_link", lambda context, email, *_: later.append(email))

    for address in (member.email, "nobody@example.com"):
        assert _post(api.client(), "/api/auth/forgot", {"email": address}).json() == FORGOT_REPLY
    assert later == [member.email, "nobody@example.com"]


@pytest.mark.db
def test_forgot_password_is_limited_by_email(db: Database) -> None:
    api, mailer = _email_api(db)
    member = api.new_signed("member")
    client = api.client()
    for _ in range(4):  # the fourth is answered the same, and sends nothing
        assert _post(client, "/api/auth/forgot", {"email": member.email}).status_code == 202
    assert len(_to(mailer, member.email)) == 3


def test_forgot_and_send_again_are_ten_an_hour_a_visitor_and_fifteen_hundred_for_the_site() -> None:
    """Changed on purpose (A1, 7 Nov 2026): each visitor has their own address now (uvicorn takes
    it from Caddy's X-Forwarded-For), so the per-visitor cap does the work and the site's is 5x."""
    for name in ("forgot_site_limits", "resend_site_limits"):
        assert getattr(email_router, name)._capacity == email_router.LINKS_PER_HOUR_SITE == 1500
    assert email_router.link_address_limits._capacity == email_router.LINKS_PER_HOUR_ADDRESS == 10


@pytest.mark.db
def test_forgot_and_send_again_are_limited_per_visitor(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """A1: one visitor address, both routes together; another visitor has their own allowance."""
    api, _ = _email_api(db)
    monkeypatch.setattr(email_router, "link_address_limits", RateLimiter(2, window_seconds=3600))
    visitor, other = (
        TestClient(api.app, client=(address, 50000)) for address in ("203.0.113.7", "198.51.100.9")
    )
    replies = [
        _post(visitor, path, {"email": f"n-{uuid.uuid4().hex[:8]}@example.com"})
        for path in ("/api/auth/forgot", "/api/auth/email/resend-confirm", "/api/auth/forgot")
    ]
    assert [reply.status_code for reply in replies] == [202, 202, 429]
    assert replies[-1].json()["error"]["code"] == "rate_limited"
    assert _post(other, "/api/auth/forgot", {"email": "n@example.com"}).status_code == 202


@pytest.mark.db
def test_one_visitor_cannot_use_up_someone_elses_reset_links(db: Database) -> None:
    """A1: the per-e-mail cap is per e-mail and address, so a stranger asking for a person's
    links again and again never stops that person getting their own."""
    api, mailer = _email_api(db)
    member = api.new_signed("member")
    stranger = TestClient(api.app, client=("203.0.113.7", 50000))
    for _ in range(4):
        assert _post(stranger, "/api/auth/forgot", {"email": member.email}).status_code == 202
    assert len(_to(mailer, member.email)) == 3
    _post(TestClient(api.app, client=("198.51.100.9", 50000)), "/api/auth/forgot", {"email": member.email})
    assert len(_to(mailer, member.email)) == 4


@pytest.mark.db
@pytest.mark.parametrize("path", ["/api/auth/forgot", "/api/auth/email/resend-confirm"])
def test_forgot_and_send_again_are_limited_for_the_whole_site(
    db: Database, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    api, _ = _email_api(db)
    for name in ("forgot_site_limits", "resend_site_limits"):
        monkeypatch.setattr(email_router, name, RateLimiter(2, window_seconds=3600))
    replies = [
        _post(api.client(), path, {"email": f"n-{uuid.uuid4().hex[:8]}@example.com"}) for _ in range(3)
    ]
    assert [reply.status_code for reply in replies] == [202, 202, 429]
    assert replies[-1].json()["error"]["code"] == "rate_limited"


@pytest.mark.db
def test_a_reset_link_sets_the_password_once_signs_out_everywhere_and_proves_the_email(db: Database) -> None:
    api, mailer = _email_api(db)
    customer_id, _ = api.customer("Reset Co")
    member = api.sign_in(customer_id, "member")
    with db.customer(customer_id) as conn:  # a temporary password is cleared too
        conn.execute(
            text("UPDATE users SET must_change_password = true WHERE id = :u"), {"u": member.user_id}
        )
    _post(api.client(), "/api/auth/forgot", {"email": member.email})
    token = _token(mailer, member.email, "/reset-password")

    short = _post(api.client(), "/api/auth/reset", {"token": token, "new_password": "too short"})
    assert short.status_code == 400
    reply = _post(api.client(), "/api/auth/reset", {"token": token, "new_password": NEW_PASSWORD})
    assert reply.json() == {"ok": True}
    assert "set-cookie" not in reply.headers  # no session: the page sends them to sign in
    assert (
        _post(api.client(), "/api/auth/reset", {"token": token, "new_password": PASSWORD}).json() == LINK_GONE
    )

    assert member.client.get("/api/auth/me").status_code == 401  # every browser signed out
    assert api.admin_login(api.client(), member.email).status_code == 401
    signed = api.admin_login(api.client(), member.email, password=NEW_PASSWORD)
    assert signed.status_code == 200, signed.text
    assert signed.json()["user"]["must_change_password"] is False
    assert signed.json()["user"]["email_confirmed"] is True
    assert "auth.password_reset" in api.audit_actions(customer_id)
    assert [subject for subject, _ in _to(mailer, member.email)] == [
        "Reset your Kaagaz password",
        "Your Kaagaz password was reset",
    ]


def _connections(db: Database, customer_id: str, user_id: str) -> int:
    with db.customer(customer_id) as conn:
        return len(identities.for_user(conn, customer_id, user_id))


@pytest.mark.db
def test_a_reset_link_removes_connected_accounts_and_change_password_keeps_them(db: Database) -> None:
    """A reset by email link means someone may have taken the account: its connections go, as an
    admin's reset. Change password needs the old one, so they stay, and the notice says where to look."""
    mailer = MemoryMailer()
    settings = google_settings(email_enabled=True, email_from=SENDER, app_url=SITE)
    api = Api(db, settings=settings, mailer=mailer)
    member = api.new_signed("member")
    with db.customer(member.customer_id) as conn:
        identities.add(
            conn,
            member.customer_id,
            member.user_id,
            provider="google",
            issuer=ISSUER,
            subject=f"made-up-sub-{uuid.uuid4().hex}",
            email="ramesh.k@gmail.com",
            approved=True,
            approved_by=None,
            now=api.clock(),
        )

    body = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert _post(member.client, "/api/auth/password", body).status_code == 200
    assert _connections(db, member.customer_id, member.user_id) == 1
    [(_, changed)] = _to(mailer, member.email)
    assert "If you didn't do this, or don't recognise a connected account, open Ways to sign in." in changed

    _post(api.client(), "/api/auth/forgot", {"email": member.email})
    token = _token(mailer, member.email, "/reset-password")
    assert _post(api.client(), "/api/auth/reset", {"token": token, "new_password": PASSWORD}).json() == {
        "ok": True
    }
    assert _connections(db, member.customer_id, member.user_id) == 0
    unlinked = ("auth.identity_unlinked", "user", member.user_id, {"count": 1})
    assert unlinked in api.audit_rows(member.customer_id)
    subject, notice = _to(mailer, member.email)[-1]
    assert subject == "Your Kaagaz password was reset"
    assert "Connected sign-ins, such as a Google account, were removed too." in notice


@pytest.mark.db
@pytest.mark.parametrize("action", ["change", "reset", "disable", "admin_reset"])
def test_setting_a_password_or_an_admins_disable_or_reset_ends_every_open_link(
    db: Database, action: str
) -> None:
    api, mailer = _email_api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    email, user_id = member.email, member.user_id
    _post(member.client, "/api/auth/email/send-confirm")
    confirm = _token(mailer, email, "/email/confirm")
    _post(api.client(), "/api/auth/forgot", {"email": email})
    reset = _token(mailer, email, "/reset-password")
    new = {"new_password": NEW_PASSWORD}

    if action == "change":
        assert (
            _post(member.client, "/api/auth/password", {"current_password": PASSWORD, **new}).status_code
            == 200
        )
    elif action == "reset":
        assert _post(api.client(), "/api/auth/reset", {"token": reset, **new}).json() == {"ok": True}
    elif action == "disable":
        changed = admin.client.patch(f"/api/admin/users/{user_id}", json={"disabled": True}, headers=CSRF)
        assert changed.status_code == 200, changed.text
    else:
        assert _post(admin.client, f"/api/admin/users/{user_id}/reset-password").status_code == 200

    for path, body in [
        ("/api/auth/reset", {"token": reset, **new}),
        ("/api/auth/email/confirm", {"token": confirm}),
    ]:
        assert _post(api.client(), path, body).json() == LINK_GONE, (action, path)


@pytest.mark.db
def test_a_reset_link_lasts_thirty_minutes(db: Database) -> None:
    api, mailer = _email_api(db)
    member = api.new_signed("member")
    _post(api.client(), "/api/auth/forgot", {"email": member.email})
    token = _token(mailer, member.email, "/reset-password")
    api.clock.advance(30 * 60)
    assert (
        _post(api.client(), "/api/auth/reset", {"token": token, "new_password": NEW_PASSWORD}).json()
        == LINK_GONE
    )


# --- Change password and the security notices --------------------------------------------------


@pytest.mark.db
def test_change_password_asks_for_the_code_when_two_step_is_on_and_sends_a_notice(db: Database) -> None:
    api, mailer = _email_api(db)
    admin = api.new_admin()
    assert admin.totp_secret is not None
    body = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    api.clock.advance(totp.STEP_SECONDS)  # the sign-in used this step's code

    missing = _post(admin.client, "/api/auth/password", body)
    assert missing.json()["error"] == {
        "code": "bad_request",
        "message": "Enter the 6-digit code from your authenticator app.",
    }
    wrong = _post(admin.client, "/api/auth/password", {**body, "code": wrong_code(api, admin.totp_secret)})
    assert wrong.json()["error"]["message"] == "That code is not right. Wait for the next code and try again."
    assert mailer.sent == []
    changed = _post(admin.client, "/api/auth/password", {**body, "code": api.code(admin.totp_secret)})
    assert changed.status_code == 200, changed.text
    assert [subject for subject, _ in _to(mailer, admin.email)] == ["Your Kaagaz password was changed"]

    member = api.new_signed("member")  # no code, no code asked
    assert _post(member.client, "/api/auth/password", body).status_code == 200
    assert [subject for subject, _ in _to(mailer, member.email)] == ["Your Kaagaz password was changed"]


@pytest.mark.db
def test_turning_two_step_on_sends_a_notice(db: Database) -> None:
    api, mailer = _email_api(db)
    admin = api.new_admin()
    customer_id = admin.customer_id
    _, new_admin_email = api.add_user(customer_id, "admin")
    offer = api.admin_login(api.client(), new_admin_email).json()["error"]["details"]
    secret = base64.b32decode(offer["secret"].replace(" ", ""))
    enrol = {"enrol_token": offer["enrol_token"], "code": api.code(secret)}
    enrolled = _post(api.client(), "/api/auth/admin/enrol", enrol)
    assert enrolled.status_code == 200, enrolled.text
    assert [subject for subject, _ in _to(mailer, new_admin_email)] == ["Two-step sign-in is on"]


@pytest.mark.db
def test_with_email_off_nothing_is_sent_and_me_asks_for_no_confirm(db: Database) -> None:
    mailer = MemoryMailer()
    api = Api(db, mailer=mailer)  # email_enabled is off by default
    member = api.new_signed("member")
    assert member.client.get("/api/auth/me").json()["user"]["email_confirmed"] is True  # no banner
    body = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert _post(member.client, "/api/auth/password", body).status_code == 200
    assert mailer.sent == []


# --- an email that breaks never breaks the action -----------------------------------------------


@pytest.mark.db
def test_a_notice_that_breaks_never_breaks_the_finished_action(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    """Each security notice is sent after the reply; a mailer or database failure there is
    logged and the finished action keeps its success and its re-signed cookie."""
    api, _ = _email_api(db, BrokenMailer())
    member = api.new_signed("member")
    caplog.set_level(logging.ERROR, logger="kaagaz.api.emails")

    body = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    changed = _post(member.client, "/api/auth/password", body)
    assert changed.status_code == 200, changed.text
    assert "set-cookie" in changed.headers
    assert member.client.get("/api/auth/me").status_code == 200
    assert _task_failed("password_changed") in caplog.messages

    token = "made-up-reset-token-" + uuid.uuid4().hex
    with db.customer(member.customer_id) as conn:
        links_sql.add(
            conn,
            member.customer_id,
            member.user_id,
            "reset",
            token_hash(token),
            now=api.clock(),
            expires_at=api.clock() + timedelta(minutes=30),
        )
    reset = _post(api.client(), "/api/auth/reset", {"token": token, "new_password": PASSWORD})
    assert reset.json() == {"ok": True}
    assert _task_failed("password_reset") in caplog.messages

    _, new_admin_email = api.add_user(member.customer_id, "admin")
    offer = api.admin_login(api.client(), new_admin_email).json()["error"]["details"]
    secret = base64.b32decode(offer["secret"].replace(" ", ""))
    enrolled = _post(
        api.client(), "/api/auth/admin/enrol", {"enrol_token": offer["enrol_token"], "code": api.code(secret)}
    )
    assert enrolled.status_code == 200, enrolled.text
    assert "set-cookie" in enrolled.headers
    assert _task_failed("two_step_on") in caplog.messages
    assert member.email not in caplog.text and new_admin_email not in caplog.text


@pytest.mark.db
@pytest.mark.parametrize(
    ("path", "purpose"), [("/api/auth/forgot", "reset"), ("/api/auth/email/resend-confirm", "confirm")]
)
def test_forgot_and_send_again_log_a_task_that_breaks_after_the_reply(
    db: Database, caplog: pytest.LogCaptureFixture, path: str, purpose: str
) -> None:
    working, _ = _email_api(db)  # the sign-up's own link goes first
    email = working.new_signed("member").email if purpose == "reset" else _pending_sign_up(working)
    api, _ = _email_api(db, BrokenMailer())
    caplog.set_level(logging.ERROR, logger="kaagaz.api.emails")
    assert _post(api.client(), path, {"email": email}).status_code == 202
    assert _task_failed(purpose) in caplog.messages


@pytest.mark.db
def test_a_link_that_could_not_be_sent_says_so(db: Database) -> None:
    """Sign-up and "Send link" say ``sent: false`` when the email failed, so the screen offers
    to send it again."""
    api, _ = _email_api(db, FailingMailer())
    body = {
        "organisation_name": f"Sharma Traders {uuid.uuid4().hex[:6]}",
        "name": "Asha",
        "password": PASSWORD,
    }
    email = f"owner-{uuid.uuid4().hex[:12]}@example.com"
    signed_up = _post(api.client(), "/api/auth/signup", {**body, "email": email})
    assert (signed_up.status_code, signed_up.json()) == (201, {"status": "confirm_email", "sent": False})
    member = api.new_signed("member")
    assert _post(member.client, "/api/auth/email/send-confirm").json() == {"ok": True, "sent": False}


@pytest.mark.db
def test_a_link_whose_user_is_gone_is_not_used_up_and_nothing_is_audited(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, mailer = _email_api(db)
    member = api.new_signed("member")
    _post(api.client(), "/api/auth/forgot", {"email": member.email})
    token = _token(mailer, member.email, "/reset-password")
    before = api.audit_actions(member.customer_id)
    with monkeypatch.context() as patched:
        patched.setattr(users_sql, "set_password", lambda *args, **kwargs: None)
        gone = _post(api.client(), "/api/auth/reset", {"token": token, "new_password": NEW_PASSWORD})
    assert (gone.status_code, gone.json()) == (400, LINK_GONE)
    assert api.audit_actions(member.customer_id) == before
    assert _post(api.client(), "/api/auth/reset", {"token": token, "new_password": NEW_PASSWORD}).json() == {
        "ok": True
    }


def _pending_org(api: Api, ops: TestClient, email: str) -> str:
    [org] = [
        item
        for item in ops.get("/api/operator/orgs").json()["items"]
        if item["status"] == "pending" and _org_admin(api, item["id"]) == email
    ]
    return str(org["id"])


def _org_admin(api: Api, customer_id: str) -> str | None:
    with api.db.customer(customer_id) as conn:
        found = conn.execute(
            text("SELECT email FROM users WHERE customer_id = :c"), {"c": customer_id}
        ).scalar()
    return None if found is None else str(found)


@pytest.mark.db
def test_a_waiting_sign_up_can_not_be_disabled_then_enabled_round_approve(db: Database) -> None:
    api, _ = _email_api(db)
    email = _pending_sign_up(api)
    operator, ops = _Operator(api), api.client()
    assert operator.login(ops).status_code == 200
    org_id = _pending_org(api, ops, email)
    api.clock.advance(totp.STEP_SECONDS)  # the sign-in used this step's code
    disabled = _post(ops, f"/api/operator/orgs/{org_id}/disable", {"code": api.code(operator.secret)})
    assert (disabled.status_code, disabled.json()["error"]["message"]) == (
        409,
        "This organisation is waiting for approval. Use Approve.",
    )
    assert _post(ops, f"/api/operator/orgs/{org_id}/enable").status_code == 409
    with db.engine.begin() as conn:
        status = conn.execute(text("SELECT status FROM customers WHERE id = :c"), {"c": org_id}).scalar()
    assert status == "pending"


@pytest.mark.db
def test_a_waiting_sign_ups_admin_never_locks_themselves_out_and_approve_clears_any_lock(
    db: Database,
) -> None:
    api, mailer = _email_api(db)
    email = _pending_sign_up(api)
    for _ in range(api.settings.login_max_failures + 1):  # a right password while pending: not a failure
        assert api.admin_login(api.client(), email).json()["error"]["code"] == "org_pending"
    with db.system() as conn:
        found = users_sql.login_lookup(conn, email)
    assert found is not None
    assert api.user_state(found.customer_id, found.user_id)["failed_logins"] == 0
    assert "auth.login_failed" not in api.audit_actions(found.customer_id)

    _post(api.client(), "/api/auth/email/confirm", {"token": _token(mailer, email, "/email/confirm")})
    with db.customer(found.customer_id) as conn:  # wrong passwords meanwhile locked it
        conn.execute(
            text("UPDATE users SET failed_logins = 9, locked_until = :t WHERE id = :u"),
            {"t": api.clock() + timedelta(hours=1), "u": found.user_id},
        )
    operator, ops = _Operator(api), api.client()
    assert operator.login(ops).status_code == 200
    assert _post(ops, f"/api/operator/orgs/{found.customer_id}/approve").status_code == 200
    state = api.user_state(found.customer_id, found.user_id)
    assert (state["failed_logins"], state["locked_until"]) == (0, None)


@pytest.mark.db
def test_a_password_set_from_a_reset_link_clears_the_lock(db: Database) -> None:
    api, mailer = _email_api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    email, user_id = member.email, member.user_id
    _post(api.client(), "/api/auth/forgot", {"email": email})
    token = _token(mailer, email, "/reset-password")
    with db.customer(admin.customer_id) as conn:  # someone kept guessing
        conn.execute(
            text("UPDATE users SET failed_logins = 9, locked_until = :t WHERE id = :u"),
            {"t": api.clock() + timedelta(hours=1), "u": user_id},
        )

    reset = _post(api.client(), "/api/auth/reset", {"token": token, "new_password": NEW_PASSWORD})
    assert reset.json() == {"ok": True}
    state = api.user_state(admin.customer_id, user_id)
    assert (state["failed_logins"], state["locked_until"]) == (0, None)
    assert api.admin_login(api.client(), email, password=NEW_PASSWORD).status_code == 200
