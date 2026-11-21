"""Google sign-in by organisation and role (#507, 0074), the settings, data and API part, against
PostgreSQL:

* the super admin's switch per organisation and for the platform (step-up, both logs): a change
  of the value in force signs out the people with an approved connection there and keeps their
  connections; a platform change touches only the organisations that follow it;
* the data rights: the app can't write ``customers.google_signin``; row level security keeps the
  rule columns to the organisation itself;
* the org admin's rules (``/admin/settings`` ``google``): the checks, the lock-out guard in both
  forms (audited in its own transaction), and the right people signed out on a save;
* promotion to admin: the connection waits for another admin again, the old session ends;
  approval refuses one's own connection, an organisation that is off, a role not allowed and an
  account outside the domains;
* the people list's ``google_needed`` and ``signs_in_with``, and the invite's Google line.
"""

import uuid
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.adapters.email import MemoryMailer
from kaagaz.api.routers.admin import ONLY_NOT_ALLOWED
from kaagaz.db import identities
from kaagaz.db.engine import Database
from tests.oidc_fake import ISSUER, google_settings
from tests.test_api_oidc_connect import _api
from tests.test_api_support import CSRF, Api, Signed
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

PLATFORM = "/api/operator/google-signin"
ROLES = ["admin", "reviewer", "member"]
NO_LINK = (
    "Connect your own Google account first, in Account, Ways to sign in. Then you can make Google"
    " the only way in for admins."
)
OUTSIDE = (
    "Your own Google account isn't from these domains, so you'd be locked out. Add its domain, or"
    " connect one that is."
)
PENDING = (
    "Your Google account is waiting for another admin to approve it. Then you can make Google the"
    " only way in for admins."
)
UNCHECKED = "Sign in with Google once so Kaagaz can check its domain."


def _google_api(db: Database) -> Api:
    return Api(db, settings=google_settings())


def _link(
    api: Api,
    customer: str,
    user: str,
    *,
    approved: bool = True,
    hosted: str | None = None,
    email: str = "ramesh@gmail.com",
) -> str:
    """A Google connection of ``user``, approved or waiting, with its ``hd``."""
    with api.db.customer(customer) as conn:
        identity = identities.add(
            conn,
            customer,
            user,
            provider="google",
            issuer=ISSUER,
            subject=f"made-up-sub-{uuid.uuid4().hex}",
            email=email,
            approved=approved,
            approved_by=None,
            now=api.clock(),
        )
        conn.execute(
            text("UPDATE user_identities SET hosted_domain = :h WHERE customer_id = :c AND id = :i"),
            {"h": hosted, "c": customer, "i": identity},
        )
    return identity


def _status(api: Api, customer: str, identity: str) -> tuple[str, Any]:
    with api.db.customer(customer) as conn:
        row = conn.execute(
            text("SELECT status, approved_at FROM user_identities WHERE customer_id = :c AND id = :i"),
            {"c": customer, "i": identity},
        ).one()
    return str(row[0]), row[1]


def _in(signed: Signed) -> bool:
    """Still signed in."""
    return signed.client.get("/api/auth/me").status_code == 200


def _rules(admin: Signed, allowed: list[str], only: list[str], domains: list[str]) -> httpx.Response:
    body = {"google": {"allowed_roles": allowed, "only_roles": only, "domains": domains}}
    return admin.client.patch("/api/admin/settings", json=body, headers=CSRF)


def _error(reply: httpx.Response) -> tuple[int, str, str]:
    error = reply.json()["error"]
    return reply.status_code, error["code"], error["message"]


def _org_rows(
    api: Api, customer: str, prefix: str
) -> list[tuple[str, str | None, str | None, dict[str, Any]]]:
    return [row for row in api.audit_rows(customer) if row[0].startswith(prefix)]


# --- the super admin's switch -------------------------------------------------------------------


# --- data rights --------------------------------------------------------------------------------


def test_the_app_cant_write_the_switch_and_writes_only_its_own_rules(db: Database) -> None:
    a_admin, b_admin = _google_api(db).new_admin("Org A rights"), _google_api(db).new_admin("Org B rights")
    a, b = a_admin.customer_id, b_admin.customer_id
    with pytest.raises(DBAPIError), db.customer(a) as conn:
        conn.execute(text("UPDATE customers SET google_signin = false WHERE id = :c"), {"c": a})
    with db.customer(a) as conn:
        other = conn.execute(text("UPDATE customers SET google_roles = '{admin}' WHERE id = :b"), {"b": b})
        own = conn.execute(
            text("UPDATE customers SET google_domains = '{sharma.in}' WHERE id = :a"), {"a": a}
        )
        assert (other.rowcount, own.rowcount) == (0, 1)
    assert _owner(db, "SELECT google_roles, google_domains FROM customers WHERE id = :b", {"b": b}) == [
        (ROLES, [])
    ]
    api = _google_api(db)
    b_identity = _link(api, b, b_admin.user_id)
    with db.customer(a) as conn:
        moved = conn.execute(
            text("UPDATE user_identities SET hosted_domain = 'evil.io' WHERE id = :i"), {"i": b_identity}
        )
        assert moved.rowcount == 0


# --- the org admin's rules ----------------------------------------------------------------------


def test_the_settings_show_the_rules_and_refuse_bad_ones(db: Database) -> None:
    api = _google_api(db)
    admin = api.new_admin("Org rules")
    google = admin.client.get("/api/admin/settings").json()["google"]
    assert google == {"available": True, "allowed_roles": ROLES, "only_roles": [], "domains": []}
    personal = "Gmail and other personal email domains can't be used. Enter your company's own domain."
    cases = [
        ((["admin", "reviewer"], ["member"], []), ONLY_NOT_ALLOWED),
        ((ROLES, [], ["not a domain"]), "Enter a domain like sharmatraders.com."),
        ((ROLES, [], ["sharma"]), "Enter a domain like sharmatraders.com."),
        ((ROLES, [], ["gmail.com"]), personal),
        ((ROLES, [], ["Yahoo.co.in"]), personal),
        ((ROLES, [], [f"d{n}.in" for n in range(11)]), "Add at most 10 domains."),
    ]  # fmt: skip
    for (allowed, only, domains), message in cases:
        assert _error(_rules(admin, allowed, only, domains)) == (400, "bad_request", message)
    assert _rules(admin, ["owner"], [], []).status_code == 400
    assert (
        admin.client.patch("/api/admin/settings", json={"google": {"domains": []}}, headers=CSRF).status_code
        == 400
    )

    saved = _rules(
        admin, ["reviewer", "member"], ["member"], [" SharmaTraders.com ", "sharmatraders.com", "sharma.in"]
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["google"] == {
        "available": True,
        "allowed_roles": ["reviewer", "member"],
        "only_roles": ["member"],
        "domains": ["sharmatraders.com", "sharma.in"],
    }
    assert _org_rows(api, admin.customer_id, "settings.google") == [
        (
            "settings.google_rules",
            "customer",
            admin.customer_id,
            {
                "allowed_roles": "reviewer,member",
                "only_roles": "member",
                "domains": "sharmatraders.com,sharma.in",
            },
        )
    ]
    assert (
        _rules(admin, ["reviewer", "member"], ["member"], ["sharmatraders.com", "sharma.in"]).status_code
        == 200
    )
    assert len(_org_rows(api, admin.customer_id, "settings.google")) == 1  # unchanged: not audited again


def test_the_rules_cant_be_saved_while_google_is_off(db: Database) -> None:
    off_server = Api(db).new_admin("Org server off")  # GOOGLE_SIGNIN_ENABLED off
    assert "google" not in off_server.client.get("/api/admin/settings").json()  # as auto-approval
    message = "Google sign-in is off for your organisation."
    assert _error(_rules(off_server, ROLES, [], [])) == (409, "google_off", message)
    api = _google_api(db)
    admin = api.new_admin("Org operator off")
    _owner(db, "UPDATE customers SET google_signin = false WHERE id = :c", {"c": admin.customer_id})
    assert admin.client.get("/api/admin/settings").json()["google"]["available"] is False
    assert _error(_rules(admin, ROLES, [], [])) == (409, "google_off", message)
    assert (
        admin.client.patch("/api/admin/settings", json={"max_upload_mb": 5}, headers=CSRF).status_code == 200
    )


def test_google_only_for_admins_needs_the_savers_own_passing_link(db: Database) -> None:
    api = _google_api(db)
    admin = api.new_admin("Org lockout")
    customer = admin.customer_id
    assert _error(_rules(admin, ROLES, ["admin"], [])) == (409, "google_lockout", NO_LINK)
    pending = _link(
        api, customer, admin.user_id, approved=False, hosted="sharmatraders.com", email="me@sharmatraders.com"
    )
    # Waiting is no way in, and says so (#507 review).
    assert _error(_rules(admin, ROLES, ["admin"], [])) == (409, "google_lockout", PENDING)
    with db.customer(customer) as conn:
        identities.remove(conn, customer, pending)
    # A link from before 0074 (no hd yet) inside the domains: not checked yet, never "outside".
    unchecked = _link(api, customer, admin.user_id, email="me@sharmatraders.com")
    assert _error(_rules(admin, ROLES, ["admin"], ["sharmatraders.com"])) == (
        409,
        "google_lockout",
        UNCHECKED,
    )
    with db.customer(customer) as conn:
        identities.remove(conn, customer, unchecked)
    _link(api, customer, admin.user_id, hosted="other.com", email="me@other.com")
    refused = _rules(admin, ROLES, ["admin"], ["sharmatraders.com"])
    assert _error(refused) == (409, "google_lockout", OUTSIDE)
    assert _owner(
        db, "SELECT google_only_roles, google_domains FROM customers WHERE id = :c", {"c": customer}
    ) == [([], [])]  # nothing saved
    assert [row[3]["reason"] for row in _org_rows(api, customer, "settings.google")] == [
        "no_link",
        "link_pending",
        "domain_unchecked",
        "outside_domains",
    ]
    assert _rules(admin, ROLES, ["admin"], ["other.com"]).status_code == 200  # its domain: passes
    assert _in(admin)  # the saver stays signed in


def test_saving_the_rules_signs_out_the_linked_people_of_the_changed_roles(db: Database) -> None:
    api = _google_api(db)
    admin = api.new_admin("Org save")
    customer = admin.customer_id
    linked_member, plain_member = api.sign_in(customer, "member"), api.sign_in(customer, "member")
    linked_reviewer, other_admin = api.sign_in(customer, "reviewer"), api.new_admin_of(customer)
    for signed in (linked_member, linked_reviewer, other_admin):
        _link(api, customer, signed.user_id, hosted="sharma.in", email="x@sharma.in")
    _link(api, customer, admin.user_id, hosted="sharma.in", email="me@sharma.in")

    assert _rules(admin, ROLES, ["member"], []).status_code == 200  # the member role changed
    assert not _in(linked_member)
    assert all(_in(s) for s in (plain_member, linked_reviewer, other_admin, admin))

    assert _rules(admin, ROLES, ["member"], ["sharma.in"]).status_code == 200  # domains: every role
    assert not _in(linked_reviewer) and not _in(other_admin)
    assert _in(admin) and _in(plain_member)


# --- promotion and approval ---------------------------------------------------------------------


def test_promotion_to_admin_needs_another_admins_approval_again(db: Database) -> None:
    mailer = MemoryMailer()
    api = _api(db, mailer)
    admin = api.new_admin("Org promote")
    customer = admin.customer_id
    member = api.sign_in(customer, "member")
    identity = _link(api, customer, member.user_id)
    promoted = admin.client.patch(f"/api/admin/users/{member.user_id}", json={"role": "admin"}, headers=CSRF)
    assert promoted.status_code == 200, promoted.text
    assert _status(api, customer, identity) == ("pending", None)
    assert not _in(member)  # the old session ends
    assert _org_rows(api, customer, "auth.identity_reapproval") == [
        ("auth.identity_reapproval", "user", member.user_id, {"provider": "google", "identity_id": identity})
    ]
    waiting = [m for m in mailer.sent if m.subject == "Waiting for your approval"]
    told = [m.to for m in waiting]
    assert admin.email in told and member.email not in told  # the admins, never the promoted one
    assert len(told) == 2  # this admin and the organisation's first
    pending = admin.client.get("/api/admin/identities").json()["items"]
    assert [item["id"] for item in pending] == [identity]

    own = _link(api, customer, admin.user_id, approved=False)  # an admin's own waiting connection
    refused = admin.client.post(f"/api/admin/identities/{own}/approve", headers=CSRF)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "own_connection")
    other = api.new_admin_of(customer)
    assert other.client.post(f"/api/admin/identities/{own}/approve", headers=CSRF).status_code == 200
    assert other.client.post(f"/api/admin/identities/{identity}/approve", headers=CSRF).status_code == 200
    assert _status(api, customer, identity)[0] == "approved"

    demoted = admin.client.patch(f"/api/admin/users/{member.user_id}", json={"role": "member"}, headers=CSRF)
    assert demoted.status_code == 200
    assert _status(api, customer, identity)[0] == "approved"  # a role change down keeps it


def test_approval_follows_the_organisations_rules(db: Database) -> None:
    api = _google_api(db)
    admin = api.new_admin("Org approve rules")
    customer = admin.customer_id
    member = api.sign_in(customer, "member")
    identity = _link(api, customer, member.user_id, approved=False)
    path = f"/api/admin/identities/{identity}/approve"

    def refused(code: str) -> None:
        reply = admin.client.post(path, headers=CSRF)
        assert (reply.status_code, reply.json()["error"]["code"]) == (409, code), reply.text
        assert _status(api, customer, identity)[0] == "pending"

    _owner(db, "UPDATE customers SET google_signin = false WHERE id = :c", {"c": customer})
    refused("google_off")
    _owner(
        db,
        "UPDATE customers SET google_signin = NULL, google_roles = '{admin,reviewer}' WHERE id = :c",
        {"c": customer},
    )
    refused("role_refused")
    _owner(
        db,
        "UPDATE customers SET google_roles = DEFAULT, google_domains = '{sharma.in}' WHERE id = :c",
        {"c": customer},
    )
    refused("domain_refused")
    _owner(
        db,
        "UPDATE user_identities SET hosted_domain = 'sharma.in', email_at_link = 'r@sharma.in' WHERE id = :i",
        {"i": identity},
    )
    assert admin.client.post(path, headers=CSRF).status_code == 200


# --- the people list and the invite -------------------------------------------------------------


def _listed(admin: Signed) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in admin.client.get("/api/admin/users").json()["items"]}


def test_the_people_list_says_who_signs_in_how_and_who_needs_to_connect(db: Database) -> None:
    api = _google_api(db)
    admin = api.new_admin("Org list")
    customer = admin.customer_id
    needs, connected = api.sign_in(customer, "member"), api.sign_in(customer, "member")
    reviewer, outside = api.sign_in(customer, "reviewer"), api.sign_in(customer, "member")
    unchecked = api.sign_in(customer, "member")
    _link(api, customer, connected.user_id, hosted="sharma.in", email="c@sharma.in")
    _link(api, customer, reviewer.user_id, hosted="sharma.in", email="r@sharma.in")
    _link(api, customer, outside.user_id)  # a Gmail account, outside the domains
    _link(api, customer, unchecked.user_id, email="u@sharma.in")  # from before 0074: no hd yet
    _owner(
        db,
        "UPDATE customers SET google_only_roles = '{member}', google_domains = '{sharma.in}' WHERE id = :c",
        {"c": customer},
    )

    def said(signed: Signed) -> tuple[list[str], bool, bool]:
        item = _listed(admin)[signed.user_id]
        return item["signs_in_with"], item["google_needed"], item["google_unchecked"]

    assert said(needs) == (["password"], True, False)
    assert said(connected) == (["google"], False, False)
    assert said(reviewer) == (["password", "google"], False, False)
    assert said(outside) == (["password"], True, False)
    assert said(unchecked) == (["password"], True, True)  # "Sign in with Google once…"
    assert said(admin) == (["password"], False, False)
    _owner(db, "UPDATE customers SET google_signin = false WHERE id = :c", {"c": customer})
    assert said(connected) == (["password"], False, False)  # off: everyone signs in with their password
    assert said(needs) == (["password"], False, False)


def test_replies_that_change_a_person_carry_the_google_tag(db: Database) -> None:
    """#507 review: the screen merges each reply into its row, so a reply that can change the tag
    carries it (role change, Remove connected accounts, Reset password)."""
    api = _google_api(db)
    admin = api.new_admin("Org tag")
    customer = admin.customer_id
    member = api.sign_in(customer, "member")
    _link(api, customer, member.user_id, hosted="sharma.in", email="m@sharma.in")
    _owner(
        db,
        "UPDATE customers SET google_only_roles = '{member}', google_domains = '{sharma.in}' WHERE id = :c",
        {"c": customer},
    )
    tag = ("signs_in_with", "google_needed", "google_unchecked")

    def change(role: str) -> dict[str, Any]:
        reply = admin.client.patch(f"/api/admin/users/{member.user_id}", json={"role": role}, headers=CSRF)
        assert reply.status_code == 200, reply.text
        changed: dict[str, Any] = reply.json()
        return changed

    assert [change("reviewer")[key] for key in tag] == [["password", "google"], False, False]
    assert [change("member")[key] for key in tag] == [["google"], False, False]
    removed = admin.client.post(f"/api/admin/users/{member.user_id}/remove-connections", headers=CSRF)
    assert removed.status_code == 200, removed.text
    assert [removed.json()[key] for key in tag] == [["password"], True, False]
    reset = admin.client.post(f"/api/admin/users/{member.user_id}/reset-password", headers=CSRF)
    assert reset.status_code == 200, reset.text
    assert [reset.json()["user"][key] for key in tag] == [["password"], True, False]
