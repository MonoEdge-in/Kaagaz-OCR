"""``/auth/keycloak/callback`` with the REAL ``_login`` (routers/keycloak.py) on PostgreSQL.

Only the two calls to the identity provider are faked (``id_token_for`` and ``verified_identity``,
as tests/test_keycloak_api.py does); the flow row, the link lookup, the refusals, the session
record, ``last_login_at`` and the audit row are all real. Every refusal sets no session cookie.
"""

import uuid
from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import Connection, text

from kaagaz.api import keycloak_oidc
from kaagaz.api.auth import SESSION_COOKIE
from kaagaz.db import customers
from kaagaz.db import keycloak as keycloak_sql
from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_support import Api

ISSUER = "https://id.example.test/realms/kaagaz-test"
SETTINGS = {
    "keycloak_enabled": True,
    "keycloak_issuer": ISSUER,
    "keycloak_client_id": "kaagaz-web",
    "keycloak_client_secret": "route-test-confidential-secret",
    "keycloak_redirect_uri": "https://app.example.test/api/auth/keycloak/callback",
    "keycloak_flow_encryption_key": "distinct-route-test-encryption-key-material",
}


def _person(db: Database, *, role: str = "member") -> tuple[Api, str, str, str]:
    """A linked person (the way Move everyone leaves them): (api, customer, user, subject)."""
    api = Api(db, settings=make_settings(**SETTINGS))
    customer_id, _ = api.customer()
    user_id, _ = api.add_user(customer_id, role)
    subject = f"subject-{uuid.uuid4().hex}"
    with db.user(customer_id, user_id) as conn:
        keycloak_sql.link_account(conn, ISSUER, subject, now=api.clock())
    return api, customer_id, user_id, subject


def _callback(api: Api, monkeypatch: pytest.MonkeyPatch, subject: str) -> tuple[int, str, bool]:
    """Start a sign-in and finish it as ``subject``: (status, where to, a session cookie was set)."""
    monkeypatch.setattr(  # no network: the discovery document is not fetched
        keycloak_oidc, "authorize_url", lambda settings, *, state, **kwargs: f"{ISSUER}/auth?state={state}"
    )
    monkeypatch.setattr(keycloak_oidc, "id_token_for", lambda *args: "opaque-test-token")
    monkeypatch.setattr(
        keycloak_oidc,
        "verified_identity",
        lambda *args, **kwargs: keycloak_oidc.Identity(
            issuer=ISSUER,
            subject=subject,
            session_id=f"sid-{uuid.uuid4().hex}",
            auth_time=int(api.clock().timestamp()),
            acr="1",
            authentication_provider="local",
        ),
    )
    client = api.client()
    client.follow_redirects = False
    started = client.get("/api/auth/keycloak/start?next=%2Fdocuments")
    assert started.status_code == 303
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    reply = client.get("/api/auth/keycloak/callback", params={"state": state, "code": "one-use-code"})
    return reply.status_code, reply.headers["location"], SESSION_COOKIE in reply.headers.get("set-cookie", "")


def _owner(db: Database, sql: str, customer_id: str, user_id: str | None = None) -> None:
    with db.engine.begin() as conn:
        conn.execute(text(sql), {"c": customer_id, "u": user_id})


def _stamps(db: Database, customer_id: str, user_id: str) -> tuple[object, int]:
    with db.engine.begin() as conn:
        last = conn.execute(
            text("SELECT last_login_at FROM users WHERE customer_id = :c AND id = :u"),
            {"c": customer_id, "u": user_id},
        ).scalar_one()
        audits = conn.execute(
            text(
                "SELECT count(*) FROM audit_log WHERE customer_id = :c AND actor_user_id = :u"
                " AND action = 'auth.login'"
            ),
            {"c": customer_id, "u": user_id},
        ).scalar_one()
    return last, int(audits)


@pytest.mark.db
def test_the_happy_path_signs_in_stamps_last_login_and_audits(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, customer_id, user_id, subject = _person(db)
    assert _stamps(db, customer_id, user_id) == (None, 0)  # linking cleared any old sign-in

    status, where, cookie = _callback(api, monkeypatch, subject)

    assert (status, where, cookie) == (303, "/documents", True)
    last, audits = _stamps(db, customer_id, user_id)
    assert last is not None and audits == 1
    assert api.audit_rows(customer_id)[-1] == ("auth.login", "user", user_id, {"via": "keycloak"})


@pytest.mark.db
def test_a_subject_nobody_is_linked_to_is_not_connected(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, customer_id, user_id, _ = _person(db)
    status, where, cookie = _callback(api, monkeypatch, f"subject-{uuid.uuid4().hex}")
    assert (status, cookie) == (303, False)
    assert "keycloak_error=not_connected" in where
    assert _stamps(db, customer_id, user_id) == (None, 0)


@pytest.mark.db
@pytest.mark.parametrize(
    "change",
    [
        "UPDATE users SET disabled = true WHERE customer_id = :c AND id = :u",
        "UPDATE customers SET status = 'disabled', disabled_at = now() WHERE id = :c",
        "UPDATE customers SET status = 'pending' WHERE id = :c",
        "UPDATE users SET auth_authority = 'legacy' WHERE customer_id = :c AND id = :u",
    ],
    ids=["disabled user", "disabled organisation", "pending organisation", "legacy authority"],
)
def test_a_refused_person_gets_no_session_cookie_no_stamp_and_no_audit(
    db: Database, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    api, customer_id, user_id, subject = _person(db)
    _owner(db, change, customer_id, user_id)

    status, where, cookie = _callback(api, monkeypatch, subject)

    assert (status, cookie) == (303, False)
    assert where in ("/login?keycloak_error=refused", "/login?keycloak_error=not_connected"), where
    assert _stamps(db, customer_id, user_id) == (None, 0)


@pytest.mark.db
def test_a_moved_person_with_an_old_last_login_is_cleared_by_the_link_then_stamped_by_the_callback(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db, settings=make_settings(**SETTINGS))
    customer_id, _ = api.customer()
    user_id, _ = api.add_user(customer_id, "member")
    old = api.clock() - timedelta(days=30)
    with db.engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET last_login_at = :t WHERE customer_id = :c AND id = :u"),
            {"t": old, "c": customer_id, "u": user_id},
        )
    subject = f"subject-{uuid.uuid4().hex}"
    with db.user(customer_id, user_id) as conn:
        keycloak_sql.link_account(conn, ISSUER, subject, now=api.clock())
    assert _stamps(db, customer_id, user_id)[0] is None

    assert _callback(api, monkeypatch, subject)[2] is True
    assert _stamps(db, customer_id, user_id)[0] is not None


@pytest.mark.db
def test_a_session_version_that_moved_on_between_the_lookup_and_the_login_is_refused(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, customer_id, user_id, subject = _person(db)
    real = keycloak_sql.lookup

    def stale(conn: Connection, issuer: str, subject: str) -> keycloak_sql.IdentityRow | None:
        found = real(conn, issuer, subject)
        return replace(found, session_version=found.session_version + 1) if found else None

    monkeypatch.setattr(keycloak_sql, "lookup", stale)

    status, where, cookie = _callback(api, monkeypatch, subject)

    assert (status, where, cookie) == (303, "/login?keycloak_error=refused", False)
    assert _stamps(db, customer_id, user_id) == (None, 0)


@pytest.mark.db
def test_an_organisation_that_is_disabled_and_enabled_again_signs_its_people_in_again(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, customer_id, user_id, subject = _person(db)
    with db.engine.begin() as conn:
        customers.set_status(conn, customer_id, customers.DISABLED, now=api.clock())
    assert _callback(api, monkeypatch, subject)[2] is False
    with db.engine.begin() as conn:
        customers.set_status(conn, customer_id, customers.ACTIVE, now=api.clock())
    assert _callback(api, monkeypatch, subject)[2] is True
    assert _stamps(db, customer_id, user_id)[1] == 1
