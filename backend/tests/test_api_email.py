"""Email (#457 phase 0) through the API: confirm your email and invites. Forgot password, Change
password, two-step notices and self sign-up went with Kaagaz's own sign-in (21 Nov 2026).

SES is faked with ``MemoryMailer``; a failing mailer raises botocore's ClientError. Links
are read back from the sent text (``#t=`` in the fragment). The first test needs no
database; the rest (marked ``db``) run against a real PostgreSQL.
"""

import logging
import re
import uuid
from collections.abc import Iterator
from typing import Any

import httpx2 as httpx
import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text

from kaagaz.adapters.email import MemoryMailer
from kaagaz.api.app import create_app
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers import email as email_router
from kaagaz.db.engine import Database, make_engine
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


# --- forgot password ----------------------------------------------------------------------------


# --- invites ------------------------------------------------------------------------------------


@pytest.mark.db
def test_an_invite_sets_the_password_and_proves_the_email(db: Database) -> None:
    api, mailer = _email_api(db)
    admin = api.new_admin("Invite Co")
    api.add_document(admin.customer_id, name="secret-plan.pdf", page_count=2)
    email = f"ramesh-{uuid.uuid4().hex[:8]}@example.com"
    body = {"email": email, "display_name": "Ramesh", "role": "member", "password": PASSWORD}

    added = admin.client.post("/api/admin/users", json=body, headers=CSRF)
    assert added.status_code == 201, added.text
    assert added.json()["invited"] is True
    assert "temporary_password" not in added.json()
    [(subject, invite)] = _to(mailer, email)
    assert subject == "You're invited to Kaagaz"
    assert "Invite Co" in invite
    assert not _email_confirmed(db, admin.customer_id, added.json()["id"])

    token = _token(mailer, email, "/invite")
    api.clock.advance(7 * 24 * 3600 - 1)  # 7 days
    accepted = _post(api.client(), "/api/auth/invite/accept", {"token": token, "new_password": NEW_PASSWORD})
    assert accepted.json() == {"ok": True}
    again = _post(api.client(), "/api/auth/invite/accept", {"token": token, "new_password": PASSWORD})
    assert again.json() == LINK_GONE
    assert _email_confirmed(db, admin.customer_id, added.json()["id"])
    actions = api.audit_actions(admin.customer_id)
    assert {"user.create", "auth.invite_sent", "auth.invite_accepted"} <= set(actions)
    for sent in mailer.sent:  # no document, its name or an id in any email
        assert "secret-plan" not in sent.text and not _UUID.search(sent.subject + sent.text)


@pytest.mark.db
def test_an_invite_that_cannot_be_sent_falls_back_to_the_temporary_password(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    api, _ = _email_api(db, FailingMailer())
    admin = api.new_admin()
    email = f"ramesh-{uuid.uuid4().hex[:8]}@example.com"
    body = {"email": email, "display_name": "Ramesh", "role": "member", "password": PASSWORD}

    with caplog.at_level(logging.WARNING, logger="kaagaz.api.emails"):
        added = admin.client.post("/api/admin/users", json=body, headers=CSRF)
    assert added.status_code == 201, added.text
    assert added.json()["invited"] is False
    # Changed on purpose (#457 review): the failure says why (SES's code), at error level.
    assert '{"event":"email_failed","purpose":"invite","error":"MessageRejected"}' in caplog.messages
    assert email not in caplog.text  # never the address
    user_id = added.json()["id"]
    with db.customer(admin.customer_id) as conn:  # the link was made with the user, before the send
        purposes = (
            conn.execute(
                text("SELECT purpose FROM email_links WHERE customer_id = :c AND user_id = :u"),
                {"c": admin.customer_id, "u": user_id},
            )
            .scalars()
            .all()
        )
    assert purposes == ["invite"]
    assert "auth.invite_sent" not in api.audit_actions(admin.customer_id)


# --- Change password and the security notices --------------------------------------------------


# --- an email that breaks never breaks the action -----------------------------------------------
