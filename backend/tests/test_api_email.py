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
from typing import Any

import httpx2 as httpx
import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text

from kaagaz.adapters.email import MemoryMailer
from kaagaz.api import totp
from kaagaz.api.app import create_app
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers import auth as auth_router
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


@pytest.fixture(autouse=True)
def _fresh_limits(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts with a full allowance: the limiters are one a process."""
    for name in (
        "confirm_limits",
        "forgot_site_limits",
        "forgot_email_limits",
        "resend_site_limits",
        "resend_email_limits",
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
    assert (signed_up.status_code, signed_up.json()) == (201, {"status": "confirm_email"})
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
    assert _post(api.client(), "/api/auth/email/confirm", {"token": token}).json() == {"ok": True}
    [org] = [item for item in ops.get("/api/operator/orgs").json()["items"] if item["id"] == org["id"]]
    assert org["admin_email_confirmed"] is True
    approved = _post(ops, approve)
    assert approved.json() == {"organisation": {"id": org["id"], "status": "active", "changed": True}}
    assert _post(ops, approve).status_code == 409  # once

    # Signed in now: the admin sets up the two-step code first, as every admin (0035).
    reply = api.admin_login(api.client(), email)
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "mfa_enrol_required")
    actions = api.audit_actions(org["id"])
    assert actions[:4] == [
        "org.self_signup",
        "auth.login_failed",
        "auth.login_failed",
        "auth.email_confirmed",
    ]
    assert "org.approved" in actions
    reasons = [details.get("reason") for action, _, _, details in api.audit_rows(org["id"])]
    assert "customer_pending" in reasons


# --- confirm your email ------------------------------------------------------------------------


@pytest.mark.db
def test_a_confirm_link_works_once_only_the_newest_works_and_it_expires(db: Database) -> None:
    api, mailer = _email_api(db)
    customer_id, _ = api.customer()
    member = api.sign_in(customer_id, "member")
    assert member.client.get("/api/auth/me").json()["user"]["email_confirmed"] is False  # the banner
    assert api.client().get("/api/auth/email").json() == {"enabled": True}  # "Forgot password?" shows

    assert _post(member.client, "/api/auth/email/send-confirm").json() == {"ok": True}
    first = _token(mailer, member.email, "/email/confirm")
    _post(member.client, "/api/auth/email/send-confirm")
    newest = _token(mailer, member.email, "/email/confirm")
    assert _post(api.client(), "/api/auth/email/confirm", {"token": first}).json() == LINK_GONE  # replaced
    assert _post(api.client(), "/api/auth/email/confirm", {"token": newest}).json() == {"ok": True}
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
    assert _post(client, "/api/auth/email/confirm", {"token": first}).json() == {"ok": True}
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
    monkeypatch.setattr(email_router, "_resend_confirm", lambda context, email: later.append(email))
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
        ("/api/auth/invite/accept", {"token": reset, "new_password": NEW_PASSWORD}),
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
    monkeypatch.setattr(email_router, "_send_reset", lambda context, email: later.append(email))

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


def test_the_site_wide_limits_on_forgot_and_send_again_are_three_hundred_an_hour() -> None:
    """Behind the proxy every visitor shares one address, so the limit is for the whole site."""
    for name in ("forgot_site_limits", "resend_site_limits"):
        assert getattr(email_router, name)._capacity == email_router.LINKS_PER_HOUR_SITE == 300


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
    assert api.admin_login(api.client(), email).status_code == 401  # the typed password is not kept

    token = _token(mailer, email, "/invite")
    api.clock.advance(7 * 24 * 3600 - 1)  # 7 days
    accepted = _post(api.client(), "/api/auth/invite/accept", {"token": token, "new_password": NEW_PASSWORD})
    assert accepted.json() == {"ok": True}
    again = _post(api.client(), "/api/auth/invite/accept", {"token": token, "new_password": PASSWORD})
    assert again.json() == LINK_GONE
    signed = api.admin_login(api.client(), email, password=NEW_PASSWORD)
    assert signed.status_code == 200, signed.text
    assert (signed.json()["user"]["email_confirmed"], signed.json()["user"]["must_change_password"]) == (
        True,
        False,
    )
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
    assert '{"event":"email_failed","purpose":"invite"}' in caplog.messages
    assert email not in caplog.text  # never the address
    signed = api.admin_login(api.client(), email)
    assert (signed.status_code, signed.json()["user"]["must_change_password"]) == (200, True)


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
def test_turning_two_step_on_and_an_admins_reset_send_notices(db: Database) -> None:
    api, mailer = _email_api(db)
    admin = api.new_admin()
    customer_id = admin.customer_id
    new_admin_id, new_admin_email = api.add_user(customer_id, "admin")
    offer = api.admin_login(api.client(), new_admin_email).json()["error"]["details"]
    secret = base64.b32decode(offer["secret"].replace(" ", ""))
    enrol = {"enrol_token": offer["enrol_token"], "code": api.code(secret)}
    enrolled = _post(api.client(), "/api/auth/admin/enrol", enrol)
    assert enrolled.status_code == 200, enrolled.text
    assert [subject for subject, _ in _to(mailer, new_admin_email)] == ["Two-step sign-in is on"]

    reset = _post(admin.client, f"/api/admin/users/{new_admin_id}/reset-2fa")
    assert reset.status_code == 200, reset.text
    assert [subject for subject, _ in _to(mailer, new_admin_email)][
        -1
    ] == "Your Kaagaz two-step sign-in was reset"


@pytest.mark.db
def test_with_email_off_nothing_is_sent_and_me_asks_for_no_confirm(db: Database) -> None:
    mailer = MemoryMailer()
    api = Api(db, mailer=mailer)  # email_enabled is off by default
    member = api.new_signed("member")
    assert member.client.get("/api/auth/me").json()["user"]["email_confirmed"] is True  # no banner
    body = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert _post(member.client, "/api/auth/password", body).status_code == 200
    assert mailer.sent == []
