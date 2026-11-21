"""PRD 7 PR 13 (#605): opening a locked file. The 10-minute check, the password and Google confirms,
the 5-tries block and the page-picture route.

One organisation with nothing walled, so R0 alone decides: L is locked, pictured on two pages and
given to ``given`` and the Admin; ``member`` and Management see every file but L. Another
organisation has its own locked file. Every refusal of a person who may not open L is checked to be
the reply to an id that never was, byte for byte, with or without a live check of their own."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx
import pytest

from kaagaz.api.auth import LOCKED_OPEN_COOKIE, LOCKED_OPEN_SECONDS
from kaagaz.api.routers.locked_open import OPENS_PER_HOUR
from kaagaz.db.engine import Database
from tests.test_api_delete_forever_grant import _confirm, _grant
from tests.test_api_support import CSRF, PASSWORD, Api, Signed, TestClient
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


def _open(signed: Signed) -> httpx.Response:
    """Open the locked files the way the screen does: the company sign-in's proof, then the call."""
    Api.of(signed).prove(signed, "locked_open")
    return signed.client.post(OPEN, headers=CSRF)


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


def test_a_given_person_sees_the_pictures_only_after_the_company_sign_in(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _code(_page(org.given, org.locked)) == (403, "locked_open_required")
    closed = {"open": False, "until": None, "seconds": None, "via": "keycloak"}
    assert org.given.client.get(OPEN).json() == closed
    opened = _open(org.given)
    assert opened.status_code == 200, opened.text
    until = int(api.clock().timestamp()) + LOCKED_OPEN_SECONDS
    assert opened.json()["open"] is True and opened.json()["seconds"] == 600
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
    api.clock.advance(100)
    assert org.given.client.get(OPEN).json()["seconds"] == 500  # by the server's clock


def test_a_picture_asked_for_as_anything_but_an_image_is_not_found(db: Database) -> None:
    """It deters only: opening the picture as a page, saving it or framing it (developer tools and a
    photograph of the screen remain)."""
    api = Api(db)
    org = _org(api)
    assert _open(org.given).status_code == 200
    path = f"/api/documents/{org.locked}/locked-pages/1"
    for where in ("document", "iframe", "empty", "object"):
        reply = org.given.client.get(path, headers={"Sec-Fetch-Dest": where})
        assert _code(reply) == (404, "not_found"), where
    assert org.given.client.get(path, headers={"Sec-Fetch-Dest": "image"}).content == PICTURE


def test_one_view_row_per_person_file_and_ten_minutes(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _open(org.given).status_code == 200
    for page in (1, 2, 1):
        assert _page(org.given, org.locked, page).status_code == 200
    views = [row for row in _actions(api, org.c) if row[0] == "lock.view"]
    assert views == [("lock.view", org.locked, {})]
    earlier = "UPDATE audit_log SET created_at = created_at - interval '11 minutes' WHERE customer_id = :c"
    _owner(db, earlier, {"c": org.c})
    assert _page(org.given, org.locked, 2).status_code == 200
    assert [row[0] for row in _actions(api, org.c)].count("lock.view") == 2
    assert "document.view" not in [row[0] for row in _actions(api, org.c, "document.")]


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
    api.clock.advance(LOCKED_OPEN_SECONDS - 1)
    assert _page(org.given, org.locked).status_code == 200  # used just before the end: not extended
    api.clock.advance(1)
    assert _code(_page(org.given, org.locked)) == (403, "locked_open_required")
    assert org.given.client.get(OPEN).json()["open"] is False


def test_sign_out_ends_it_and_a_new_sign_in_does_not_bring_it_back(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _open(org.given).status_code == 200
    kept = org.given.client.cookies.get(LOCKED_OPEN_COOKIE, path="/api/documents")
    assert kept
    out = org.given.client.post("/api/auth/logout", headers=CSRF)
    assert out.status_code == 204
    cleared = [c for c in out.headers.get_list("set-cookie") if c.startswith("kaagaz_locked_open=")]
    assert (
        len(cleared) == 1
        and "max-age=0" in cleared[0].lower()
        and "path=/api/documents" in cleared[0].lower()
    )
    api.mint_session(org.given.client, org.c, org.given.user_id)  # signed in again, a new session
    org.given.client.cookies.set(LOCKED_OPEN_COOKIE, kept, path="/api/documents")
    assert _code(_page(org.given, org.locked)) == (403, "locked_open_required")  # another browser session


def test_sign_out_everywhere_clears_it(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _open(org.given).status_code == 200
    out = org.given.client.post("/api/auth/logout-everywhere", headers=CSRF)
    assert out.status_code == 204
    cleared = [c for c in out.headers.get_list("set-cookie") if c.startswith("kaagaz_locked_open=")]
    assert len(cleared) == 1 and "max-age=0" in cleared[0].lower()


def test_another_browser_and_signing_out_everywhere_end_it(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _open(org.given).status_code == 200
    other = api.client()  # the same person in a second browser, with this browser's check copied in
    api.mint_session(other, org.c, org.given.user_id)
    kept = org.given.client.cookies.get(LOCKED_OPEN_COOKIE, path="/api/documents")
    assert kept
    other.cookies.set(LOCKED_OPEN_COOKIE, kept, path="/api/documents")
    assert _code(_page(other, org.locked)) == (403, "locked_open_required")  # its sid is this browser's
    assert _page(org.given, org.locked).status_code == 200
    out = org.given.client.post("/api/auth/logout-everywhere", headers=CSRF)
    assert out.status_code == 204
    assert _code(_page(org.given, org.locked)) == (401, "not_signed_in")  # the version went up


def test_the_delete_forever_confirm_never_opens_a_locked_file_nor_the_other_way(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    assert _grant(org.admin, org.given.user_id) == (200, True)
    assert _confirm(org.given) == 204
    assert _code(_page(org.given, org.locked)) == (403, "locked_open_required")
    confirm = org.given.client.cookies.get("kaagaz_confirm", path="/api/documents")
    assert confirm
    org.given.client.cookies.set(LOCKED_OPEN_COOKIE, confirm, path="/api/documents")
    assert _code(_page(org.given, org.locked)) == (403, "locked_open_required")

    reader = api.sign_in(org.c, "member")
    assert _grant(org.admin, reader.user_id) == (200, True)
    trashed = api.add_document(org.c, deleted_at=api.clock())
    assert _open(reader).status_code == 200
    check = reader.client.cookies.get(LOCKED_OPEN_COOKIE, path="/api/documents")
    assert check
    reader.client.cookies.set("kaagaz_confirm", check, path="/api/documents")
    forever = reader.client.delete(f"/api/documents/{trashed}/permanent", headers=CSRF)
    assert _code(forever) == (403, "keycloak_reauth_required")


# --- the company sign-in, the only way to open --------------------------------------------------


def test_opening_needs_a_fresh_company_sign_in_for_this_action_and_no_password_counts(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    for body in ({}, {"password": PASSWORD}):  # a password is not a way in, and is not even read
        refused = org.given.client.post(OPEN, json=body, headers=CSRF)
        assert _code(refused) == (403, "keycloak_reauth_required")
    api.prove(org.given, "locks")  # another action's proof is no proof for this one
    assert _code(org.given.client.post(OPEN, headers=CSRF)) == (403, "keycloak_reauth_required")
    assert _actions(api, org.c) == []
    api.prove(org.given, "locked_open")
    opened = org.given.client.post(OPEN, headers=CSRF)
    assert opened.status_code == 200, opened.text
    assert opened.json()["via"] == "keycloak"
    assert _actions(api, org.c)[-1] == ("lock.open", org.given.user_id, {"via": "keycloak"})
    api.clock.advance(5 * 60 + 1)  # the proof is only good for five minutes
    assert _code(org.given.client.post(OPEN, headers=CSRF)) == (403, "keycloak_reauth_required")


def test_more_than_thirty_opens_an_hour_are_refused(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    api.prove(org.given, "locked_open")
    for _ in range(OPENS_PER_HOUR):
        assert org.given.client.post(OPEN, headers=CSRF).status_code == 200
    assert _code(org.given.client.post(OPEN, headers=CSRF)) == (429, "rate_limited")


def test_a_live_check_never_lets_search_read_a_locked_file(db: Database) -> None:
    """#649: search inside one locked file answers as for an id that never was; the 10-minute
    check opens only the pictures, never the file to search or Ask."""
    api = Api(db)
    org = _org(api)
    assert _open(org.given).status_code == 200
    hidden = org.given.client.get(f"/api/search?q=deed&document_ids={org.locked}", headers=CSRF)
    missing = org.given.client.get(f"/api/search?q=deed&document_ids={uuid.uuid4()}", headers=CSRF)
    assert hidden.status_code == 200 and hidden.json() == missing.json()
