"""Google sign-in by organisation and role (#507, 0072), the settings, data and API part, against
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
from kaagaz.api import emails, totp
from kaagaz.api.routers.admin import ONLY_NOT_ALLOWED
from kaagaz.db import identities
from kaagaz.db.engine import Database
from tests.oidc_fake import ISSUER, google_settings
from tests.test_api_oidc_connect import _api
from tests.test_api_support import CSRF, Api, Signed, TestClient, wrong_code
from tests.test_operator_auth_db import _Operator, _owner
from tests.test_operator_orgs_db import ORGS, _platform, _signed_in

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


def _code(api: Api, operator: _Operator) -> str:
    api.clock.advance(totp.STEP_SECONDS)
    return api.code(operator.secret)


def _set_org(
    api: Api, operator: _Operator, client: TestClient, customer: str, on: bool | None
) -> httpx.Response:
    body = {"on": on, "code": _code(api, operator)}
    return client.put(f"{ORGS}/{customer}/google-signin", json=body, headers=CSRF)


def _set_platform(api: Api, operator: _Operator, client: TestClient, on: bool) -> httpx.Response:
    return client.put(PLATFORM, json={"on": on, "code": _code(api, operator)}, headers=CSRF)


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


def test_the_super_admin_turns_one_organisation_off_signing_out_its_linked_people_only(db: Database) -> None:
    api = _google_api(db)
    operator, client = _signed_in(api)
    a_admin, b_admin = api.new_admin("Org A google"), api.new_admin("Org B google")
    a, b = a_admin.customer_id, b_admin.customer_id
    linked, plain = api.sign_in(a, "member"), api.sign_in(a, "member")
    identity = _link(api, a, linked.user_id)
    b_linked = api.sign_in(b, "member")
    _link(api, b, b_linked.user_id)
    path = f"{ORGS}/{a}/google-signin"
    shape: dict[str, Any] = {
        "on": None,
        "platform_on": True,
        "effective": True,
        "google_only_roles": [],
        "domains": [],
    }
    assert client.get(path).json() == shape  # follows the platform, nothing changes on release day

    bad = client.put(path, json={"on": False, "code": wrong_code(api, operator.secret)}, headers=CSRF)
    assert (bad.status_code, bad.json()["error"]["code"]) == (403, "step_up_failed")
    assert client.put(path, json={"on": False}, headers=CSRF).status_code == 400  # no code
    assert _in(linked)

    off = _set_org(api, operator, client, a, False)
    assert (off.status_code, off.json()) == (200, {**shape, "on": False, "effective": False})
    assert not _in(linked)  # signed out, the connection kept
    assert _status(api, a, identity)[0] == "approved"
    assert _in(plain) and _in(a_admin) and _in(b_linked)  # no connection, or another organisation
    assert _set_org(api, operator, client, a, False).status_code == 200  # unchanged: no new rows

    again = api.sign_in(a, "member")
    _link(api, a, again.user_id)
    on = _set_org(api, operator, client, a, None)  # back to the platform's: on again
    assert on.json() == shape
    assert not _in(again)

    _owner(
        db,
        "UPDATE customers SET google_only_roles = '{member}', google_domains = '{sharma.in}' WHERE id = :c",
        {"c": a},
    )
    assert client.get(path).json() == {**shape, "google_only_roles": ["member"], "domains": ["sharma.in"]}

    row = ("customer", a)
    assert [entry for entry in _platform(operator) if entry[0] == "org.google_signin_set"] == [
        ("org.google_signin_set", a, *row),
        ("org.google_signin_set", a, *row),
    ]
    assert _org_rows(api, a, "org.google") == [
        (
            "org.google_signin_set",
            "customer",
            a,
            {"old": None, "new": False, "effective_old": True, "effective_new": False, "by": "operator"},
        ),
        (
            "org.google_signin_set",
            "customer",
            a,
            {"old": False, "new": None, "effective_old": False, "effective_new": True, "by": "operator"},
        ),
    ]
    assert _org_rows(api, b, "org.google") == []


def test_on_while_following_an_on_platform_signs_nobody_out_and_unknown_or_deleting_orgs_refuse(
    db: Database,
) -> None:
    api = _google_api(db)
    operator, client = _signed_in(api)
    admin = api.new_admin("Org own on")
    linked = api.sign_in(admin.customer_id, "member")
    _link(api, admin.customer_id, linked.user_id)
    reply = _set_org(api, operator, client, admin.customer_id, True)
    assert reply.json()["on"] is True
    assert _in(linked)  # the value in force stayed on
    assert _org_rows(api, admin.customer_id, "org.google")[0][3]["effective_new"] is True

    missing = "00000000-0000-4000-8000-000000000000"
    assert _set_org(api, operator, client, missing, False).status_code == 404
    assert client.get(f"{ORGS}/{missing}/google-signin").status_code == 404
    gone, _ = api.customer("Org deleting")
    _owner(
        db,
        "UPDATE customers SET status = 'deleting', disabled_at = now(),"
        " delete_after = now() + interval '7 days' WHERE id = :c",
        {"c": gone},
    )
    refused = _set_org(api, operator, client, gone, False)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "conflict")


def test_the_platform_switch_touches_only_the_organisations_that_follow_it(db: Database) -> None:
    api = _google_api(db)
    operator, client = _signed_in(api)
    following, own = api.new_admin("Org follows"), api.new_admin("Org own")
    f_linked, o_linked = api.sign_in(following.customer_id, "member"), api.sign_in(own.customer_id, "member")
    f_identity = _link(api, following.customer_id, f_linked.user_id)
    _link(api, own.customer_id, o_linked.user_id)
    assert _set_org(api, operator, client, own.customer_id, True).status_code == 200
    assert client.get(PLATFORM).json() == {"on": True}
    try:
        bad = client.put(PLATFORM, json={"on": False, "code": wrong_code(api, operator.secret)}, headers=CSRF)
        assert bad.status_code == 403
        assert client.put(PLATFORM, json={"on": None, "code": "123456"}, headers=CSRF).status_code == 400
        off = _set_platform(api, operator, client, False)
        assert (off.status_code, off.json()) == (200, {"on": False})
        assert not _in(f_linked)  # follows the platform: signed out, connection kept
        assert _status(api, following.customer_id, f_identity)[0] == "approved"
        assert _in(o_linked)  # its own value: untouched
        assert client.get(f"{ORGS}/{following.customer_id}/google-signin").json()["effective"] is False
        assert client.get(f"{ORGS}/{own.customer_id}/google-signin").json()["effective"] is True
        assert _set_platform(api, operator, client, False).json() == {"on": False}  # unchanged
        assert [entry[0] for entry in _platform(operator) if entry[0] == "google.platform_set"] == [
            "google.platform_set"
        ]
        details = operator.sql(
            "SELECT details FROM platform_audit WHERE operator_id = :o AND action = 'google.platform_set'"
        )
        assert details == [
            ({"old": True, "new": False, "effective_old": True, "effective_new": False, "by": "operator"},)
        ]
    finally:
        _owner(db, "UPDATE platform_ai_settings SET google_signin = true", {})


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
    assert google == {
        "available": True,
        "allowed_roles": ROLES,
        "only_roles": [],
        "domains": [],
        "my_link_ok": False,
    }
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
        "my_link_ok": False,
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
    assert _error(_rules(admin, ROLES, ["admin"], [])) == (
        409,
        "google_lockout",
        NO_LINK,
    )  # waiting is no way in
    with db.customer(customer) as conn:
        identities.remove(conn, customer, pending)
    _link(api, customer, admin.user_id, hosted="other.com", email="me@other.com")
    refused = _rules(admin, ROLES, ["admin"], ["sharmatraders.com"])
    assert _error(refused) == (409, "google_lockout", OUTSIDE)
    assert _owner(
        db, "SELECT google_only_roles, google_domains FROM customers WHERE id = :c", {"c": customer}
    ) == [([], [])]  # nothing saved
    assert _org_rows(api, customer, "settings.google") == [
        ("settings.google_rules_refused", "customer", customer, {"reason": "no_link"}),
        ("settings.google_rules_refused", "customer", customer, {"reason": "no_link"}),
        ("settings.google_rules_refused", "customer", customer, {"reason": "outside_domains"}),
    ]
    assert _rules(admin, ROLES, ["admin"], ["other.com"]).status_code == 200  # its domain: passes
    assert admin.client.get("/api/admin/settings").json()["google"]["my_link_ok"] is True
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
    _link(api, customer, connected.user_id, hosted="sharma.in", email="c@sharma.in")
    _link(api, customer, reviewer.user_id, hosted="sharma.in", email="r@sharma.in")
    _link(api, customer, outside.user_id)  # a Gmail account, outside the domains
    _owner(
        db,
        "UPDATE customers SET google_only_roles = '{member}', google_domains = '{sharma.in}' WHERE id = :c",
        {"c": customer},
    )

    def said(signed: Signed) -> tuple[list[str], bool]:
        item = _listed(admin)[signed.user_id]
        return item["signs_in_with"], item["google_needed"]

    assert said(needs) == (["password"], True)
    assert said(connected) == (["google"], False)
    assert said(reviewer) == (["password", "google"], False)
    assert said(outside) == (["password"], True)
    assert said(admin) == (["password"], False)
    _owner(db, "UPDATE customers SET google_signin = false WHERE id = :c", {"c": customer})
    assert said(connected) == (["password"], False)  # off: everyone signs in with their password
    assert said(needs) == (["password"], False)


def test_the_invite_says_to_connect_google_while_it_is_on(db: Database) -> None:
    mailer = MemoryMailer()
    api = _api(db, mailer)
    admin = api.new_admin("Org invite")

    def invite(role: str) -> str:
        body = {"email": f"new-{uuid.uuid4().hex[:8]}@example.test", "display_name": "New", "role": role}
        reply = admin.client.post(
            "/api/admin/users", json={**body, "password": "a long password 1"}, headers=CSRF
        )
        assert reply.status_code == 201, reply.text
        return mailer.sent[-1].text

    assert emails.GOOGLE_INVITE_LINE in invite("member")
    _owner(db, "UPDATE customers SET google_roles = '{admin}' WHERE id = :c", {"c": admin.customer_id})
    assert emails.GOOGLE_INVITE_LINE not in invite("member")  # the role doesn't use Google
    assert emails.GOOGLE_INVITE_LINE in invite("admin")
    _owner(db, "UPDATE customers SET google_signin = false WHERE id = :c", {"c": admin.customer_id})
    assert emails.GOOGLE_INVITE_LINE not in invite("admin")
