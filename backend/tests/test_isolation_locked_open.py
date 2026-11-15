"""PRD 7 PR 13 (#605): opening a locked file. The 10-minute check, the password and Google confirms,
the 5-tries block and the page-picture route.

One organisation with nothing walled, so R0 alone decides: L is locked, pictured on two pages and
given to ``given`` and the Admin; ``member`` and Management see every file but L. Another
organisation has its own locked file. Every refusal of a person who may not open L is checked to be
the reply to an id that never was, byte for byte, with or without a live check of their own."""

import re
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx
import pytest

from kaagaz.api import locked_open
from kaagaz.db.engine import Database
from tests.oidc_fake import google_settings, use_google
from tests.test_api_delete_forever_grant import _confirm, _grant
from tests.test_api_support import CSRF, PASSWORD, Api, Signed, TestClient
from tests.test_isolation_google_signin import _link
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

PICTURE = b"RIFF\x00\x00\x00\x00WEBPVP8 made-up page"
OPEN = "/api/documents/locked-open"
HEADERS = {
    "cache-control": "no-store, private, max-age=0",
    "pragma": "no-cache",
    "content-security-policy": "sandbox",
    "x-content-type-options": "nosniff",
    "cross-origin-resource-policy": "same-origin",
    "referrer-policy": "no-referrer",
}


@dataclass
class Org:
    c: str
    admin: Signed
    given: Signed
    member: Signed
    management: Signed
    locked: str  # L: locked, ready to view, two pictured pages
    plain: str  # not locked


def _lock(api: Api, c: str, document: str, by: str, *, to: tuple[str, ...], status: str = "locked") -> None:
    _owner(
        api.db,
        "UPDATE documents SET locked_at = now(), locked_by = :u, status = :s WHERE id = :d",
        {"u": by, "s": status, "d": document},
    )
    for user in to:
        _owner(
            api.db,
            "INSERT INTO lock_grants (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :by)",
            {"c": c, "d": document, "u": user, "by": by},
        )


def _org(api: Api) -> Org:
    c, first = api.customer()
    admin, given = api.admin_sign_in(c), api.sign_in(c, "member")
    member, management = api.sign_in(c, "member"), api.sign_in(c, "reviewer")
    _owner(api.db, "UPDATE users SET management = true WHERE id = :u", {"u": management.user_id})
    locked = api.add_document(c, name="Sale deed.pdf", uploaded_by=first, page_count=2)
    plain = api.add_document(c, name="plain.pdf", uploaded_by=first)
    for document in (locked, plain):
        for page in (1, 2):
            api.add_page(c, document, page)
            api.storage.page_images[(c, document, page)] = PICTURE
    _lock(api, c, locked, first, to=(given.user_id, admin.user_id))
    return Org(c, admin, given, member, management, locked, plain)


def _page(signed: Signed | TestClient, document: str, page: int = 1) -> httpx.Response:
    client = signed.client if isinstance(signed, Signed) else signed
    return client.get(f"/api/documents/{document}/locked-pages/{page}")


def _open(signed: Signed, password: str = PASSWORD) -> httpx.Response:
    return signed.client.post(OPEN, json={"password": password}, headers=CSRF)


def _code(reply: httpx.Response) -> tuple[int, str | None]:
    if reply.headers.get("content-type", "").startswith("application/json"):
        body = reply.json()
        return reply.status_code, body["error"]["code"] if "error" in body else None
    return reply.status_code, None


def _actions(api: Api, c: str, prefix: str = "lock.") -> list[tuple[str, str | None, dict[str, Any]]]:
    return [
        (action, target, details)
        for action, _type, target, details in api.audit_rows(c)
        if action.startswith(prefix)
    ]


def _same_as_missing(signed: Signed, document: str) -> None:
    """The reply for ``document`` is exactly the reply for an id that never was."""
    missing, reply = _page(signed, str(uuid.uuid4())), _page(signed, document)
    assert missing.status_code == 404
    assert (reply.status_code, reply.content) == (missing.status_code, missing.content)


# --- the page-picture route ------------------------------------------------------------------


def test_a_given_person_sees_the_pictures_only_after_their_password(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _code(_page(org.given, org.locked)) == (403, "locked_open_required")
    assert org.given.client.get(OPEN).json() == {"open": False, "until": None, "via": "password"}
    opened = _open(org.given)
    assert opened.status_code == 200, opened.text
    until = int(api.clock().timestamp()) + locked_open.LOCKED_OPEN_SECONDS
    assert opened.json()["open"] is True
    assert opened.json() == org.given.client.get(OPEN).json()
    cookie = opened.headers["set-cookie"].lower()
    assert "kaagaz_locked_open=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert "path=/api/documents" in cookie and "max-age=600" in cookie
    assert opened.json()["until"] == datetime.fromtimestamp(until, UTC).isoformat()
    for page in (1, 2):
        reply = _page(org.given, org.locked, page)
        assert (reply.status_code, reply.content) == (200, PICTURE)
        assert reply.headers["content-type"] == "image/webp"
        assert {name: reply.headers.get(name) for name in HEADERS} == HEADERS
    for page in (0, 3, -1):  # outside 1..page_count
        assert _code(_page(org.given, org.locked, page)) == (404, "not_found")
    _owner(db, "UPDATE pages SET has_image = false WHERE document_id = :d AND page_no = 2", {"d": org.locked})
    assert _code(_page(org.given, org.locked, 2)) == (404, "not_found")


def test_one_view_row_per_person_file_and_ten_minutes(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _open(org.given).status_code == 200
    for page in (1, 2, 1):
        assert _page(org.given, org.locked, page).status_code == 200
    views = [row for row in _actions(api, org.c) if row[0] == "lock.view"]
    assert views == [("lock.view", org.locked, {})]
    earlier = (
        "UPDATE audit_log SET created_at = created_at - interval '11 minutes' WHERE action = 'lock.view'"
    )
    _owner(db, earlier, {})
    assert _page(org.given, org.locked, 2).status_code == 200
    assert [row[0] for row in _actions(api, org.c)].count("lock.view") == 2
    assert _owner(db, "SELECT count(*) FROM audit_log WHERE action = 'document.view'", {}) == [(0,)]


def test_a_person_who_may_not_open_it_gets_an_id_that_never_was(db: Database) -> None:
    """Visibility first: their own live check changes nothing, and nothing is written."""
    api = Api(db)
    org, other = _org(api), _org(api)
    for signed in (org.member, org.management):
        _same_as_missing(signed, org.locked)
        assert _open(signed).status_code == 200  # a check of their own: it opens nothing of L
        _same_as_missing(signed, org.locked)
    _same_as_missing(other.given, org.locked)  # another organisation's given person, with a live check
    assert _open(other.given).status_code == 200
    _same_as_missing(other.given, org.locked)
    assert _code(_page(org.given, other.locked)) == (404, "not_found")
    assert [row[0] for row in _actions(api, org.c)].count("lock.view") == 0


def test_an_unlocked_file_has_no_pictures_here(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _open(org.given).status_code == 200
    assert _code(_page(org.given, org.plain)) == (404, "not_found")
    _same_as_missing(org.given, org.plain)


def test_still_being_prepared_and_in_trash(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _open(org.given).status_code == 200
    for status in ("received", "clean"):
        _owner(db, "UPDATE documents SET status = :s WHERE id = :d", {"s": status, "d": org.locked})
        assert _code(_page(org.given, org.locked)) == (409, "not_ready")
    _owner(db, "UPDATE documents SET status = 'locked', deleted_at = now() WHERE id = :d", {"d": org.locked})
    assert _code(_page(org.given, org.locked)) == (409, "in_trash")
    # The not-ready and Trash replies come before the check: still only to a person who may open it.
    _same_as_missing(org.member, org.locked)


# --- the 10-minute check -----------------------------------------------------------------------


def test_the_check_ends_after_ten_minutes_whatever_the_person_does(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _open(org.given).status_code == 200
    api.clock.advance(locked_open.LOCKED_OPEN_SECONDS - 1)
    assert _page(org.given, org.locked).status_code == 200  # used just before the end: not extended
    api.clock.advance(1)
    assert _code(_page(org.given, org.locked)) == (403, "locked_open_required")
    assert org.given.client.get(OPEN).json()["open"] is False


def test_sign_out_ends_it_and_a_new_sign_in_does_not_bring_it_back(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _open(org.given).status_code == 200
    kept = org.given.client.cookies.get(locked_open.LOCKED_OPEN_COOKIE, path="/api/documents")
    assert kept
    out = org.given.client.post("/api/auth/logout", headers=CSRF)
    assert out.status_code == 204
    cleared = [c for c in out.headers.get_list("set-cookie") if c.startswith("kaagaz_locked_open=")]
    assert (
        len(cleared) == 1
        and "max-age=0" in cleared[0].lower()
        and "path=/api/documents" in cleared[0].lower()
    )
    back = org.given.client.post(
        "/api/auth/login", json={"email": org.given.email, "password": PASSWORD}, headers=CSRF
    )
    assert back.status_code == 200
    org.given.client.cookies.set(locked_open.LOCKED_OPEN_COOKIE, kept, path="/api/documents")
    assert _code(_page(org.given, org.locked)) == (403, "locked_open_required")  # another browser session


def test_sign_out_everywhere_and_a_new_password_end_it(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _open(org.given).status_code == 200
    other = api.client()  # the same person in a second browser, with this browser's check copied in
    assert other.post("/api/auth/login", json={"email": org.given.email, "password": PASSWORD}, headers=CSRF)
    kept = org.given.client.cookies.get(locked_open.LOCKED_OPEN_COOKIE, path="/api/documents")
    other.cookies.set(locked_open.LOCKED_OPEN_COOKIE, kept, path="/api/documents")
    assert _code(_page(other, org.locked)) == (403, "locked_open_required")  # its sid is this browser's
    assert _page(org.given, org.locked).status_code == 200
    changed = org.given.client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "a-new-long-password-2026"},
        headers=CSRF,
    )
    assert changed.status_code == 200, changed.text
    assert _code(_page(org.given, org.locked)) == (403, "locked_open_required")  # the version went up


def test_the_delete_forever_confirm_never_opens_a_locked_file_nor_the_other_way(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _grant(org.admin, org.given.user_id) == (200, True)
    assert _confirm(org.given) == 204
    assert _code(_page(org.given, org.locked)) == (403, "locked_open_required")
    confirm = org.given.client.cookies.get("kaagaz_confirm", path="/api/documents")
    assert confirm
    org.given.client.cookies.set(locked_open.LOCKED_OPEN_COOKIE, confirm, path="/api/documents")
    assert _code(_page(org.given, org.locked)) == (403, "locked_open_required")

    reader = api.sign_in(org.c, "member")
    assert _grant(org.admin, reader.user_id) == (200, True)
    trashed = api.add_document(org.c, deleted_at=api.clock())
    assert _open(reader).status_code == 200
    check = reader.client.cookies.get(locked_open.LOCKED_OPEN_COOKIE, path="/api/documents")
    reader.client.cookies.set("kaagaz_confirm", check, path="/api/documents")
    forever = reader.client.delete(f"/api/documents/{trashed}/permanent", headers=CSRF)
    assert _code(forever) == (403, "password_confirm_required")


# --- the password and its block ----------------------------------------------------------------


def test_two_step_is_never_asked_for(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    enrolled = api.enrol_user(org.c, "member")
    _lock(api, org.c, org.locked, org.admin.user_id, to=(enrolled.user_id,), status="locked")
    client = api.client()
    signed_in = api.admin_login(client, enrolled.email, code=api.code(enrolled.secret))
    assert signed_in.status_code == 200, signed_in.text
    signed = Signed(client, org.c, enrolled.user_id, enrolled.email)
    assert _open(signed).status_code == 200  # the password alone
    assert _page(signed, org.locked).status_code == 200
    assert org.admin.client.post(OPEN, json={"password": PASSWORD}, headers=CSRF).status_code == 200
    assert _page(org.admin, org.locked).status_code == 200  # an Admin with two-step on: the password alone


def test_five_wrong_tries_block_for_fifteen_minutes_and_count_on_the_sign_in_lock(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    for tries in range(1, 5):
        assert _code(_open(org.given, "not-the-password")) == (400, "wrong_password")
        assert api.user_state(org.c, org.given.user_id)["failed_logins"] == tries
    assert (
        _actions(api, org.c)
        == [("lock.open_failed", org.given.user_id, {"via": "password", "reason": "wrong_password"})] * 4
    )
    assert _code(_open(org.given, "not-the-password")) == (400, "wrong_password")
    assert _actions(api, org.c)[-1] == ("lock.open_blocked", org.given.user_id, {"minutes": 15})
    assert [row[0] for row in _actions(api, org.c, "auth.")].count("auth.login_failed") == 5
    blocked = _open(org.given)  # the right password, during the block
    assert _code(blocked) == (429, "open_blocked")
    assert blocked.headers["retry-after"] == str(15 * 60)
    assert len(_actions(api, org.c)) == 6  # a try during the block writes no row
    assert _code(_page(org.given, org.locked)) == (403, "locked_open_required")
    api.clock.advance(15 * 60)
    assert _open(org.given).status_code == 200
    state = _owner(
        db,
        "SELECT locked_open_failures, locked_open_until FROM users WHERE id = :u",
        {"u": org.given.user_id},
    )
    assert state == [(0, None)]
    assert api.user_state(org.c, org.given.user_id)["failed_logins"] == 0
    assert _actions(api, org.c)[-1] == ("lock.open", org.given.user_id, {"via": "password"})


def test_a_right_password_starts_the_count_again(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    for _ in range(4):
        assert _code(_open(org.given, "wrong")) == (400, "wrong_password")
    assert _open(org.given).status_code == 200
    for _ in range(4):
        assert _code(_open(org.given, "wrong")) == (400, "wrong_password")
    assert _code(_open(org.given, "wrong")) == (400, "wrong_password")  # the fifth in a row
    assert _code(_open(org.given)) == (429, "open_blocked")


def test_while_the_sign_in_lock_is_on_no_password_is_checked(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    _owner(
        db,
        "UPDATE users SET locked_until = :t WHERE id = :u",
        {"t": api.clock().replace(year=2027), "u": org.given.user_id},
    )
    assert _code(_open(org.given)) == (400, "password_locked")
    assert _actions(api, org.c) == []


# --- Google, for a person who signs in with Google only ----------------------------------------


def _google_only(api: Api, org: Org, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str]:
    google = use_google(monkeypatch)
    subject = f"made-up-sub-{uuid.uuid4().hex}"
    identity = _link(api, org.c, org.given.user_id, email="given@sharma.in", hosted="sharma.in")
    _owner(api.db, "UPDATE user_identities SET subject = :s WHERE id = :i", {"s": subject, "i": identity})
    _owner(
        api.db,
        "UPDATE customers SET google_only_roles = '{member}', google_domains = '{sharma.in}' WHERE id = :c",
        {"c": org.c},
    )
    return google, subject


def _confirm_google(signed: Signed, google: Any, subject: str, now: int, **changes: Any) -> httpx.Response:
    started = signed.client.post(
        "/api/auth/oidc/google/confirm", json={"next": "/documents/abc"}, headers=CSRF
    )
    assert started.status_code == 200, started.text
    url = started.json()["url"]
    assert dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["max_age"] == "0"
    query = google.sign_in(url, subject, "given@sharma.in", now, **{"hd": "sharma.in", **changes})
    return signed.client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)


def test_a_google_only_person_confirms_with_a_fresh_google_sign_in(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db, settings=google_settings())
    org = _org(api)
    google, subject = _google_only(api, org, monkeypatch)
    now = int(api.clock().timestamp())
    assert org.given.client.get(OPEN).json()["via"] == "google"
    assert _code(_open(org.given)) == (409, "google_confirm_required")
    assert api.user_state(org.c, org.given.user_id)["failed_logins"] == 0  # nothing counted
    back = _confirm_google(org.given, google, subject, now, auth_time=now - 20)
    assert (back.status_code, back.headers["location"]) == (303, "/documents/abc")
    assert _page(org.given, org.locked).status_code == 200
    assert _actions(api, org.c) == [
        ("lock.open", org.given.user_id, {"via": "google"}),
        ("lock.view", org.locked, {}),
    ]
    reply = org.management.client.post("/api/auth/oidc/google/confirm", json={"next": "/"}, headers=CSRF)
    assert _code(reply) == (409, "use_password")  # a reviewer signs in with a password: types it


@pytest.mark.parametrize(
    ("case", "code"),
    [("old", "not_fresh"), ("missing", "not_fresh"), ("text", "not_fresh"), ("another", "not_yours")],
)
def test_an_old_or_missing_google_sign_in_or_another_account_is_refused(
    db: Database, monkeypatch: pytest.MonkeyPatch, case: str, code: str
) -> None:
    """Older than 5 minutes; missing (refused, never ``iat``); not a number; another person's
    approved Google account."""
    api = Api(db, settings=google_settings())
    org = _org(api)
    google, subject = _google_only(api, org, monkeypatch)
    now = int(api.clock().timestamp())
    times: dict[str, Any] = {"old": now - 301, "missing": None, "text": str(now)}
    given: dict[str, Any] = {"auth_time": times.get(case, now - 10)}
    if case == "another":
        other = api.sign_in(org.c, "member")
        theirs = _link(api, org.c, other.user_id, email="other@sharma.in", hosted="sharma.in")
        subject = str(_owner(db, "SELECT subject FROM user_identities WHERE id = :i", {"i": theirs})[0][0])
    back = _confirm_google(org.given, google, subject, now, **given)
    assert (back.status_code, back.headers["location"]) == (303, f"/documents/abc?open_error={code}")
    assert locked_open.LOCKED_OPEN_COOKIE not in back.headers.get("set-cookie", "")
    assert _code(_page(org.given, org.locked)) == (403, "locked_open_required")
    assert _actions(api, org.c) == [
        ("lock.open_failed", org.given.user_id, {"via": "google", "reason": code})
    ]


def test_the_google_confirm_needs_the_session_that_started_it(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db, settings=google_settings())
    org = _org(api)
    google, subject = _google_only(api, org, monkeypatch)
    now = int(api.clock().timestamp())
    started = org.given.client.post("/api/auth/oidc/google/confirm", json={"next": "/x"}, headers=CSRF)
    query = google.sign_in(
        started.json()["url"], subject, "given@sharma.in", now, auth_time=now, hd="sharma.in"
    )
    assert org.given.client.post("/api/auth/logout", headers=CSRF).status_code == 204
    back = org.given.client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
    assert (back.status_code, back.headers["location"]) == (303, "/x?open_error=signed_out")
    assert re.search(r"kaagaz_locked_open=[^;\"]", back.headers.get("set-cookie", "")) is None
