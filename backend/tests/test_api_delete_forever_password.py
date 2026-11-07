"""Delete forever for an admin without two-step sign-in (#538, Tushar, 7 Nov 2026), through the API.

An admin who turned two-step off (0061b) deletes forever in their admin session by typing their
password again: the confirm token grant holders use (/auth/confirm-password, CONFIRM_SECONDS,
bound to the user and session version). Without one: 403 ``password_confirm_required``. The
audit row says ``by_password``. An admin with two-step on needs no token, as before. Deleting a
tag still needs two-step on. While the organisation requires two-step for its admins (0068,
#481), such an admin is refused before any of this (``mfa_enrol_required``).
"""

import pytest

from kaagaz.api.auth import CONFIRM_COOKIE
from kaagaz.db.engine import Database
from tests.test_api_admin_sign_in import _turned_off
from tests.test_api_delete_forever_grant import _confirm, _forever, _grant
from tests.test_api_support import CSRF, PASSWORD, Api, Signed
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db


def _binned(api: Api, signed: Signed) -> str:
    return api.add_document(signed.customer_id, deleted_at=api.clock())


def test_an_admin_without_two_step_deletes_forever_after_typing_their_password(db: Database) -> None:
    api = Api(db)
    admin = _turned_off(api)
    document = _binned(api, admin)
    assert _forever(admin, document) == (403, "password_confirm_required")
    assert api.storage.has(admin.customer_id, document)

    assert _confirm(admin, "not the password") == 400
    assert api.user_state(admin.customer_id, admin.user_id)["failed_logins"] == 1
    assert _confirm(admin) == 204
    assert api.user_state(admin.customer_id, admin.user_id)["failed_logins"] == 0

    assert _forever(admin, document) == (204, None)
    assert not api.storage.has(admin.customer_id, document)
    assert admin.client.get(f"/api/documents/{document}").status_code == 404
    rows = api.audit_rows(admin.customer_id)
    assert rows[-1] == ("document.delete", "document", document, {"versions": 1, "by_password": True})


def test_only_a_fresh_token_of_this_admin_and_session_counts(db: Database) -> None:
    api = Api(db)
    admin = _turned_off(api)
    document = _binned(api, admin)
    granter = api.new_admin_of(admin.customer_id)
    member = api.sign_in(admin.customer_id)
    assert _grant(granter, member.user_id) == (200, True)
    assert _confirm(member) == 204
    admin.client.cookies.set(CONFIRM_COOKIE, member.client.cookies[CONFIRM_COOKIE])  # another user's
    assert _forever(admin, document) == (403, "password_confirm_required")
    admin.client.cookies.delete(CONFIRM_COOKIE)

    assert _confirm(admin) == 204
    api.clock.advance(300)  # 5 minutes later
    assert _forever(admin, document) == (403, "password_confirm_required")

    assert _confirm(admin) == 204
    passwords = {"current_password": PASSWORD, "new_password": "a brand new password"}
    changed = admin.client.post("/api/auth/password", json=passwords, headers=CSRF)
    assert changed.status_code == 200, changed.text  # this browser stays in, on a new session version
    assert _forever(admin, document) == (403, "password_confirm_required")
    assert api.storage.has(admin.customer_id, document)


def test_an_admin_with_two_step_needs_no_token_and_gets_none(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    document = _binned(api, admin)
    assert _confirm(admin) == 403  # nothing to confirm: two-step already counts
    assert _forever(admin, document) == (204, None)
    assert api.audit_rows(admin.customer_id)[-1] == ("document.delete", "document", document, {"versions": 1})


def test_deleting_a_tag_still_needs_two_step_even_with_a_typed_password(db: Database) -> None:
    api = Api(db)
    admin = _turned_off(api)
    tag = api.add_tag(admin.customer_id, admin.user_id, "Paid")
    assert _confirm(admin) == 204
    untag = admin.client.delete(f"/api/tags/{tag}", headers=CSRF)
    assert (untag.status_code, untag.json()["error"]["code"]) == (403, "two_step_required")


def test_while_the_organisation_requires_two_step_a_typed_password_never_counts(db: Database) -> None:
    """Turning the requirement on signs such an admin out (0069); here a session that outlives
    it, the column set as the table owner, still holding a fresh confirm token."""
    api = Api(db)
    admin = _turned_off(api)
    document = _binned(api, admin)
    assert _confirm(admin) == 204
    _owner(db, "UPDATE customers SET admin_two_step_required = true WHERE id = :c", {"c": admin.customer_id})

    assert _forever(admin, document) == (403, "mfa_enrol_required")
    assert _confirm(admin) == 403
    assert api.storage.has(admin.customer_id, document)
