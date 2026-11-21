"""Email (#457 phase 0) through the API: confirm your email, forgot password, invites,
Change password with the code, security notices, and sign-ups that wait for an operator.

SES is faked with ``MemoryMailer``; a failing mailer raises botocore's ClientError. Links
are read back from the sent text (``#t=`` in the fragment). The first test needs no
database; the rest (marked ``db``) run against a real PostgreSQL.
"""

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
from kaagaz.api.app import create_app
from kaagaz.api.emails import token_hash
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers import email as email_router
from kaagaz.db import email_links as links_sql
from kaagaz.db.engine import Database, make_engine
from tests.settings_factory import make_settings
from tests.test_api_admin import KEYCLOAK_ON, _Keycloak
from tests.test_api_support import (
    CSRF,
    EMAIL_ROUTES,
    NOW,
    Api,
    ApiFakeQueue,
    ApiFakeStorage,
    FakeAnswers,
    TestClient,
)

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
        "resend_site_limits",
        "resend_email_limits",
        "link_address_limits",
    ):
        limiter = getattr(email_router, name)
        monkeypatch.setattr(email_router, name, RateLimiter(int(limiter._capacity), window_seconds=3600))
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
    api, _ = _email_api(db)
    member = api.new_signed("member")
    client = api.client()
    token = "made-up-other-purpose-token-" + uuid.uuid4().hex
    with db.customer(member.customer_id) as conn:  # a link for something else than confirming
        links_sql.add(
            conn,
            member.customer_id,
            member.user_id,
            "reset",
            token_hash(token),
            now=api.clock(),
            expires_at=api.clock() + timedelta(minutes=30),
        )
    for body in ({"token": "x" * 43}, {"token": token}):  # unknown, and for something else
        reply = _post(client, "/api/auth/email/confirm", body)
        assert (reply.status_code, reply.json()) == (400, LINK_GONE), body
    assert _email_confirmed(db, member.customer_id, member.user_id) is False


# --- "Send the link again" for a sign-up's admin (no session) ------------------------------------


def _pending_sign_up(api: Api) -> str:
    """The email of an unconfirmed admin whose organisation is waiting for an operator."""
    customer_id, _ = api.customer()
    with api.db.engine.begin() as conn:
        conn.execute(text("UPDATE customers SET status = 'pending' WHERE id = :c"), {"c": customer_id})
        return str(
            conn.execute(
                text("SELECT email FROM users WHERE customer_id = :c AND role = 'admin'"), {"c": customer_id}
            ).scalar_one()
        )


@pytest.mark.db
def test_send_again_without_signing_in_sends_only_to_a_pending_unconfirmed_admin(db: Database) -> None:
    """A pending organisation's admin cannot sign in, so "Send the link again" needs no session.
    The reply is the same whoever is asked for, so it never shows who is waiting."""
    api, mailer = _email_api(db)
    pending = _pending_sign_up(api)
    member = api.new_signed("member")  # unconfirmed, but its organisation is active
    confirmed = _pending_sign_up(api)
    with api.db.engine.begin() as conn:
        conn.execute(text("UPDATE users SET email_verified_at = now() WHERE email = :e"), {"e": confirmed})
    client = api.client()

    replies = [
        _post(client, "/api/auth/email/resend-confirm", {"email": address})
        for address in (pending.upper(), member.email, confirmed, "nobody@example.com")
    ]
    assert {(reply.status_code, str(reply.json())) for reply in replies} == {(202, str(RESEND_REPLY))}
    assert [sent.to for sent in mailer.sent] == [pending]
    first = _token(mailer, pending, "/email/confirm")
    assert _post(client, "/api/auth/email/confirm", {"token": first}).json() == {"ok": True, "pending": True}
    assert _post(client, "/api/auth/email/resend-confirm", {"email": pending}).status_code == 202
    assert len(mailer.sent) == 1  # confirmed now: nothing more is sent


@pytest.mark.db
def test_send_again_without_signing_in_replies_first_and_is_limited_by_email(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, mailer = _email_api(db)
    pending = _pending_sign_up(api)
    client = api.client()
    for _ in range(4):  # the fourth is answered the same, and sends nothing
        assert _post(client, "/api/auth/email/resend-confirm", {"email": pending}).status_code == 202
    assert len(_to(mailer, pending)) == 3

    later: list[str] = []
    monkeypatch.setattr(email_router, "_send_link", lambda context, email, *_: later.append(email))
    assert _post(client, "/api/auth/email/resend-confirm", {"email": pending}).json() == RESEND_REPLY
    assert later == [pending]  # the lookup happens after the reply


@pytest.mark.db
def test_send_again_is_ten_an_hour_a_visitor_and_fifteen_hundred_for_the_site() -> None:
    """Changed on purpose (A1, 7 Nov 2026): each visitor has their own address now (uvicorn takes
    it from Caddy's X-Forwarded-For), so the per-visitor cap does the work and the site's is 5x."""
    assert email_router.resend_site_limits._capacity == email_router.LINKS_PER_HOUR_SITE == 1500
    assert email_router.link_address_limits._capacity == email_router.LINKS_PER_HOUR_ADDRESS == 10


@pytest.mark.db
def test_send_again_is_limited_per_visitor(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """A1: one visitor address; another visitor has their own allowance."""
    api, _ = _email_api(db)
    monkeypatch.setattr(email_router, "link_address_limits", RateLimiter(2, window_seconds=3600))
    visitor, other = (
        TestClient(api.app, client=(address, 50000)) for address in ("203.0.113.7", "198.51.100.9")
    )
    replies = [
        _post(visitor, "/api/auth/email/resend-confirm", {"email": f"n-{uuid.uuid4().hex[:8]}@example.com"})
        for _ in range(3)
    ]
    assert [reply.status_code for reply in replies] == [202, 202, 429]
    assert replies[-1].json()["error"]["code"] == "rate_limited"
    assert _post(other, "/api/auth/email/resend-confirm", {"email": "n@example.com"}).status_code == 202


@pytest.mark.db
def test_one_visitor_cannot_use_up_someone_elses_links(db: Database) -> None:
    """A1: the per-e-mail cap is per e-mail and address, so a stranger asking for a person's
    link again and again never stops that person getting their own."""
    api, mailer = _email_api(db)
    pending = _pending_sign_up(api)
    stranger = TestClient(api.app, client=("203.0.113.7", 50000))
    for _ in range(4):
        assert _post(stranger, "/api/auth/email/resend-confirm", {"email": pending}).status_code == 202
    assert len(_to(mailer, pending)) == 3
    elsewhere = TestClient(api.app, client=("198.51.100.9", 50000))
    _post(elsewhere, "/api/auth/email/resend-confirm", {"email": pending})
    assert len(_to(mailer, pending)) == 4


@pytest.mark.db
def test_send_again_is_limited_for_the_whole_site(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    api, _ = _email_api(db)
    monkeypatch.setattr(email_router, "resend_site_limits", RateLimiter(2, window_seconds=3600))
    replies = [
        _post(
            api.client(), "/api/auth/email/resend-confirm", {"email": f"n-{uuid.uuid4().hex[:8]}@example.com"}
        )
        for _ in range(3)
    ]
    assert [reply.status_code for reply in replies] == [202, 202, 429]
    assert replies[-1].json()["error"]["code"] == "rate_limited"


@pytest.mark.db
def test_send_again_logs_a_task_that_breaks_after_the_reply(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    working, _ = _email_api(db)
    email = _pending_sign_up(working)
    api, _ = _email_api(db, BrokenMailer())
    caplog.set_level(logging.ERROR, logger="kaagaz.api.emails")
    assert _post(api.client(), "/api/auth/email/resend-confirm", {"email": email}).status_code == 202
    assert _task_failed("confirm") in caplog.messages


@pytest.mark.db
def test_a_link_that_could_not_be_sent_says_so(db: Database) -> None:
    """Send link says ``sent: false`` when the email failed, so the screen offers to send it again."""
    api, _ = _email_api(db, FailingMailer())
    member = api.new_signed("member")
    assert _post(member.client, "/api/auth/email/send-confirm").json() == {"ok": True, "sent": False}


# --- an email that breaks never breaks the action -----------------------------------------------


@pytest.mark.db
def test_a_notice_that_breaks_never_breaks_the_finished_action(
    db: Database, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The authenticator-reset notice is sent after the reply; a mailer failure there is logged
    (its type only, never the address) and the finished reset keeps its success."""
    settings = make_settings(**KEYCLOAK_ON, email_enabled=True, email_from=SENDER, app_url=SITE)
    api = Api(db, settings=settings, mailer=BrokenMailer())
    _Keycloak(monkeypatch)
    admin = api.new_admin()
    address = f"m-{admin.user_id[:8]}@example.test"
    member = admin.client.post(
        "/api/admin/users",
        json={"email": address, "display_name": "Mira Rao", "role": "member"},
        headers=CSRF,
    ).json()
    caplog.set_level(logging.ERROR, logger="kaagaz.api.emails")

    reset = _post(admin.client, f"/api/admin/users/{member['id']}/reset-2fa")

    assert reset.status_code == 200, reset.text
    assert _task_failed("two_step_reset") in caplog.messages
    assert address not in caplog.text


# --- forgot password ----------------------------------------------------------------------------


# --- Change password and the security notices --------------------------------------------------


# --- an email that breaks never breaks the action -----------------------------------------------
