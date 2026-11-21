"""The operator and one organisation's admins against PostgreSQL (super admin PR 3b,
0017): the list shows that organisation's admins only, an id from another organisation
or a member's gets 404 and changes nothing, and each change has exactly one
``platform_audit`` row and one row in the organisation's own audit log."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from kaagaz.api import totp
from kaagaz.db import keycloak as keycloak_sql
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate
from tests.settings_factory import make_settings
from tests.test_api_admin import ISSUER, KEYCLOAK_ON, _Keycloak
from tests.test_api_support import CSRF, Api, wrong_code
from tests.test_operator_auth_db import _owner
from tests.test_operator_orgs_db import ORGS, _platform, _signed_in

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
CHANGES = ("disable", "enable", "unlock")


def test_0017_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0016")
    at_0016 = m0002._schema(database)
    migrate(url, "head")
    at_head = m0002._schema(database)
    assert m0002._names(at_head, "functions") >= {
        "kaagaz_op_admins",
        "kaagaz_op_create_admin",
        "kaagaz_op_change_admin",
    }
    m0002._downgrade(url, "0016")
    assert m0002._schema(database) == at_0016
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_an_organisations_admins_only_and_nothing_changes_through_another_organisation(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    a, a_admin = api.customer("Org A")
    second, _ = api.add_user(a, "admin")
    member, member_email = api.add_user(a, "member")
    b, b_admin = api.customer("Org B")

    listed = client.get(f"{ORGS}/{a}/admins")
    assert {item["id"] for item in listed.json()["items"]} == {a_admin, second}
    assert member_email not in listed.text and b_admin not in listed.text
    assert client.get(f"{ORGS}/{uuid.uuid4()}/admins").status_code == 404

    before = [dict(api.user_state(b, b_admin)), dict(api.user_state(a, member))]
    for target in (b_admin, member):  # B's admin through A's address, and A's member
        for change in CHANGES:
            assert client.post(f"{ORGS}/{a}/admins/{target}/{change}", headers=CSRF).status_code == 404
        api.clock.advance(totp.STEP_SECONDS)
        reset = {"code": api.code(operator.secret)}
        assert (
            client.post(f"{ORGS}/{a}/admins/{target}/reset-2fa", json=reset, headers=CSRF).status_code == 404
        )
    smuggled = {"email": f"x-{uuid.uuid4().hex[:8]}@example.test", "name": "X", "customer_id": b}
    assert client.post(f"{ORGS}/{a}/admins", json=smuggled, headers=CSRF).status_code == 400
    assert [dict(api.user_state(b, b_admin)), dict(api.user_state(a, member))] == before
    assert len(client.get(f"{ORGS}/{b}/admins").json()["items"]) == 1
    assert _platform(operator) == []


def test_an_operator_manages_one_admin_with_one_row_per_change(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    keycloak = _Keycloak(monkeypatch)
    api = Api(db, settings=make_settings(**KEYCLOAK_ON))
    operator, client = _signed_in(api)
    admin = api.new_admin("Org A")  # two-step on, signed in on the admin screen
    a, uid = admin.customer_id, admin.user_id
    base = f"{ORGS}/{a}/admins"

    email, other = (f"{who}-{uuid.uuid4().hex[:12]}@example.test" for who in ("new", "other"))
    new = {"email": email, "name": "New admin"}
    assert client.post(base, json=new, headers=CSRF).status_code == 400  # no code
    wrong = client.post(base, json={**new, "code": wrong_code(api, operator.secret)}, headers=CSRF)
    assert wrong.json()["error"]["code"] == "step_up_failed"
    code = api.code(operator.secret)
    created = client.post(base, json={**new, "code": code}, headers=CSRF)
    assert created.status_code == 201, created.text
    new_id = created.json()["admin"]["id"]
    assert "temporary_password" not in created.json() and created.json()["setup_email_sent"] is True
    assert keycloak.names() == ["create_user", "send_setup_email"]
    with db.system() as conn:  # the new admin is linked to exactly the account that was made
        assert keycloak_sql.lookup(conn, ISSUER, keycloak.subject()) is not None
    assert client.post(base, json={**new, "email": other, "code": code}, headers=CSRF).status_code == 403
    api.clock.advance(totp.STEP_SECONDS)
    taken = client.post(base, json={**new, "code": api.code(operator.secret)}, headers=CSRF)
    assert taken.json()["error"]["message"] == "That e-mail is already in use."
    assert other not in client.get(base).text  # the reused code made nothing
    keycloak.calls.clear()
    subject = f"subject-{uuid.uuid4().hex}"  # the existing admin joins company sign-in
    with db.user(a, uid) as conn:
        keycloak_sql.link_account(conn, ISSUER, subject, now=api.clock())

    assert client.post(f"{base}/{uid}/disable", headers=CSRF).json()["admin"]["disabled"] is True
    assert admin.client.get("/api/auth/me").status_code == 401
    assert client.post(f"{base}/{uid}/enable", headers=CSRF).json()["admin"]["disabled"] is False
    assert admin.client.get("/api/auth/me").status_code == 401  # every session ended
    with db.customer(a) as conn:
        conn.execute(
            text("UPDATE users SET failed_logins = 5, locked_until = :t WHERE customer_id = :c AND id = :u"),
            {"t": api.clock() + timedelta(minutes=10), "c": a, "u": uid},
        )
    assert [item["locked"] for item in client.get(base).json()["items"] if item["id"] == uid] == [True]
    assert client.post(f"{base}/{uid}/unlock", headers=CSRF).json()["admin"]["locked"] is False
    api.clock.advance(totp.STEP_SECONDS)
    with db.customer(a) as conn:  # the admin had turned it off themselves (30 Oct 2026)
        conn.execute(
            text(
                "UPDATE users SET totp_secret_enc = NULL, totp_enabled_at = NULL, totp_last_step = NULL,"
                " totp_off_at = :t WHERE id = :u"
            ),
            {"t": api.clock(), "u": uid},
        )
    reset = client.post(f"{base}/{uid}/reset-2fa", json={"code": api.code(operator.secret)}, headers=CSRF)
    assert reset.json()["admin"]["two_step"] is False
    state = api.user_state(a, uid)
    assert (state["failed_logins"], state["totp_secret_enc"], state["totp_enabled_at"]) == (0, None, None)
    assert state["totp_off_at"] is None  # a reset means set it up again, not turned off
    assert keycloak.calls == [
        ("set_enabled", (subject, False)),
        ("set_enabled", (subject, True)),
        ("clear_lock", (subject,)),
        ("remove_otp", (subject,)),
    ]  # each change reached the linked account, and only that one

    failed = ("operator.step_up_failed", None, "operator", operator.id)
    assert _platform(operator) == [
        failed,
        ("admin.create", a, "user", new_id),
        failed,
        ("admin.create_refused", a, "user", None),  # no e-mail stored
        *[(f"admin.{change}", a, "user", uid) for change in ("disable", "enable", "unlock", "reset_totp")],
    ]
    by_operator = [row[:3] for row in api.audit_rows(a) if row[3].get("by") == "operator"]
    assert by_operator == [
        ("user.create", "user", new_id),
        ("user.update", "user", uid),
        ("user.update", "user", uid),
        ("user.unlock", "user", uid),
        ("auth.totp_reset", "user", uid),  # Changed on purpose (#457 review, 0063): the spec's name
    ]


def test_nothing_is_made_or_changed_in_an_organisation_being_deleted(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    a, a_admin = api.customer("Org D")
    deleting = "UPDATE customers SET status = :s, disabled_at = :d, delete_after = :d WHERE id = :c"
    _owner(db, deleting, {"s": "deleting", "d": api.clock(), "c": a})  # as PR 4's schedule-delete
    try:
        for change in CHANGES:
            assert client.post(f"{ORGS}/{a}/admins/{a_admin}/{change}", headers=CSRF).status_code == 409
        new = {
            "email": f"d-{uuid.uuid4().hex[:8]}@example.test",
            "name": "D",
            "code": api.code(operator.secret),
        }
        assert client.post(f"{ORGS}/{a}/admins", json=new, headers=CSRF).status_code == 409
        assert len(client.get(f"{ORGS}/{a}/admins").json()["items"]) == 1
        assert _platform(operator) == []
    finally:
        _owner(db, deleting, {"s": "active", "d": None, "c": a})
