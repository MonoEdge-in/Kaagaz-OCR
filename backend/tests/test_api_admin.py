"""The admin routes against a real database, called with an admin session
(signed in on the admin screen with a code; tests/test_api_admin_sign_in.py
checks that an app session is refused on every one of them)."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from kaagaz.api.auth import verify_password
from kaagaz.api.routers.admin import month_window
from kaagaz.db.engine import Database
from kaagaz.db.runs import PgRuns
from kaagaz.pipeline import FinishedRun
from tests.test_api_support import ANY_ID, CSRF, PASSWORD, Api, MovingClock, answer_json
from tests.test_runs_db import totals

ADMIN_USER_FIELDS = {
    "id",
    "email",
    "display_name",
    "role",
    "disabled",
    "created_at",
    "locked_until",
    "totp_enabled",
    "must_change_password",
    "can_delete_forever",
}
WRONG = "wrong password!!"


@pytest.mark.db
def test_admin_lists_and_creates_users_and_the_new_user_can_sign_in(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    email = f"New.Person.{admin.user_id[:8]}@Example.test"

    created = admin.client.post(
        "/api/admin/users",
        json={"email": email, "display_name": " New Person ", "role": "reviewer", "password": PASSWORD},
        headers=CSRF,
    )

    assert created.status_code == 201
    user = created.json()
    assert set(user) == ADMIN_USER_FIELDS
    assert user["email"] == email.lower()
    assert user["display_name"] == "New Person"
    assert user["role"] == "reviewer"
    assert user["disabled"] is False
    assert user["locked_until"] is None
    assert user["totp_enabled"] is False
    assert user["must_change_password"] is True  # the admin chose it, so it is temporary
    assert api.audit_actions(admin.customer_id)[-1] == "user.create"
    listed = admin.client.get("/api/admin/users").json()["items"]
    assert user["id"] in [item["id"] for item in listed]
    login = api.client().post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF)
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True
    with db.customer(admin.customer_id) as conn:
        details: dict[str, object] = conn.execute(
            text("SELECT details FROM audit_log WHERE action = 'user.create'")
        ).scalar_one()
    assert PASSWORD not in json.dumps(details)


@pytest.mark.db
def test_two_users_made_with_the_same_password_get_different_salts(db: Database) -> None:
    """The helpers reuse one cached hash of PASSWORD; the product never does. Two users
    made on the admin screen with PASSWORD each get a new salt (not the helpers' one,
    which the admin has), and each hash still verifies."""
    api = Api(db)
    admin = api.new_admin()
    made = []
    for n in (1, 2):
        body = {"email": f"same-{n}-{admin.user_id[:8]}@example.test", "display_name": f"Same {n}"}
        reply = admin.client.post(
            "/api/admin/users", json={**body, "role": "member", "password": PASSWORD}, headers=CSRF
        )
        assert reply.status_code == 201, reply.text
        made.append(str(api.user_state(admin.customer_id, reply.json()["id"])["password_hash"]))
    helpers = str(api.user_state(admin.customer_id, admin.user_id)["password_hash"])

    assert len({stored.split("$")[4] for stored in [*made, helpers]}) == 3
    assert all(verify_password(PASSWORD, stored) for stored in made)


@pytest.mark.db
def test_a_taken_email_is_409_even_in_another_customer(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    other_customer, _ = api.customer()
    _, taken = api.add_user(other_customer, "member")
    reply = admin.client.post(
        "/api/admin/users",
        json={"email": taken, "display_name": "Copy", "role": "member", "password": PASSWORD},
        headers=CSRF,
    )
    assert reply.status_code == 409
    assert reply.json()["error"]["code"] == "conflict"
    assert taken not in reply.text


@pytest.mark.db
@pytest.mark.parametrize(
    "body",
    [
        {"email": "no-at-sign", "display_name": "X", "role": "member", "password": PASSWORD},
        {"email": "a@b.test", "display_name": "X", "role": "member", "password": "short"},
        {"email": "a@b.test", "display_name": "X", "role": "owner", "password": PASSWORD},
        {"email": "a@b.test", "display_name": "   ", "role": "member", "password": PASSWORD},
    ],
)
def test_bad_new_users_are_400(db: Database, body: dict[str, str]) -> None:
    admin = Api(db).new_admin()
    assert admin.client.post("/api/admin/users", json=body, headers=CSRF).status_code == 400


@pytest.mark.db
def test_update_role_disable_and_password(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")

    promoted = admin.client.patch(
        f"/api/admin/users/{member.user_id}", json={"role": "reviewer"}, headers=CSRF
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "reviewer"
    assert member.client.get("/api/auth/me").status_code == 401  # the old session named the old role

    new_password = "a brand new password"
    changed = admin.client.patch(
        f"/api/admin/users/{member.user_id}", json={"password": new_password}, headers=CSRF
    )
    assert changed.json()["role"] == "reviewer"  # untouched fields stay
    assert changed.json()["must_change_password"] is True
    login = api.client().post(
        "/api/auth/login", json={"email": member.email, "password": new_password}, headers=CSRF
    )
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True

    disabled = admin.client.patch(f"/api/admin/users/{member.user_id}", json={"disabled": True}, headers=CSRF)
    assert disabled.json()["disabled"] is True
    again = api.client().post(
        "/api/auth/login", json={"email": member.email, "password": new_password}, headers=CSRF
    )
    assert again.status_code == 401
    assert api.audit_actions(admin.customer_id).count("user.update") == 3


@pytest.mark.db
def test_a_demoted_and_promoted_again_user_does_not_get_old_sessions_back(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    other = api.sign_in(admin.customer_id, "admin")
    for role in ("member", "admin"):
        reply = admin.client.patch(f"/api/admin/users/{other.user_id}", json={"role": role}, headers=CSRF)
        assert reply.status_code == 200
    assert other.client.get("/api/auth/me").status_code == 401  # the cookie from before the demotion

    same = admin.client.patch(f"/api/admin/users/{admin.user_id}", json={"role": "admin"}, headers=CSRF)
    assert same.status_code == 200  # setting the role it already has
    assert admin.client.get("/api/auth/me").status_code == 200  # ends no session


@pytest.mark.db
def test_an_admin_cannot_reset_their_own_password(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    before = api.user_state(admin.customer_id, admin.user_id)
    reply = admin.client.post(f"/api/admin/users/{admin.user_id}/reset-password", headers=CSRF)
    assert reply.status_code == 400
    assert reply.json()["error"]["code"] == "bad_request"
    assert api.user_state(admin.customer_id, admin.user_id) == before
    assert "user.reset_password" not in api.audit_actions(admin.customer_id)
    assert admin.client.get("/api/auth/me").status_code == 200  # the session did not end


@pytest.mark.db
@pytest.mark.parametrize("body", [{"disabled": True}, {"role": "member"}, {"role": "reviewer"}])
def test_an_admin_cannot_lock_themselves_out(db: Database, body: dict[str, object]) -> None:
    admin = Api(db).new_admin()
    reply = admin.client.patch(f"/api/admin/users/{admin.user_id}", json=body, headers=CSRF)
    assert reply.status_code == 400
    assert admin.client.get("/api/auth/me").json()["user"]["role"] == "admin"


@pytest.mark.db
def test_an_admin_may_change_their_own_password(db: Database) -> None:
    admin = Api(db).new_admin()
    reply = admin.client.patch(
        f"/api/admin/users/{admin.user_id}",
        json={"password": "my new long password", "role": "admin"},
        headers=CSRF,
    )
    assert reply.status_code == 200


@pytest.mark.db
def test_update_unknown_user_is_404(db: Database) -> None:
    admin = Api(db).new_admin()
    reply = admin.client.patch(
        "/api/admin/users/7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e", json={"role": "member"}, headers=CSRF
    )
    assert reply.status_code == 404


@pytest.mark.db
def test_settings_read_and_change_within_the_platform_limit(db: Database) -> None:
    api = Api(db)  # platform limit: 5 MB
    customer_id, _ = api.customer("Mehta Textiles")
    admin = api.admin_sign_in(customer_id)

    assert admin.client.get("/api/admin/settings").json() == {
        "customer_name": "Mehta Textiles",
        "max_upload_mb": None,
        "platform_max_upload_mb": 5,
    }
    changed = admin.client.patch("/api/admin/settings", json={"max_upload_mb": 3}, headers=CSRF)
    assert changed.json()["max_upload_mb"] == 3
    assert (
        admin.client.patch("/api/admin/settings", json={"max_upload_mb": 6}, headers=CSRF).status_code == 400
    )
    assert (
        admin.client.patch("/api/admin/settings", json={"max_upload_mb": 0}, headers=CSRF).status_code == 400
    )
    cleared = admin.client.patch("/api/admin/settings", json={"max_upload_mb": None}, headers=CSRF)
    assert cleared.json()["max_upload_mb"] is None
    assert api.audit_actions(customer_id).count("settings.update") == 2


@pytest.mark.db
def test_audit_log_is_newest_first_with_actor_names_and_paginated(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    admin.client.patch("/api/admin/settings", json={"max_upload_mb": 2}, headers=CSRF)

    body = admin.client.get("/api/admin/audit?limit=1").json()

    assert body["total"] == 2  # auth.admin_login, settings.update
    entry = body["items"][0]
    assert set(entry) == {"id", "action", "actor_name", "target_type", "target_id", "details", "created_at"}
    assert entry["action"] == "settings.update"
    assert entry["actor_name"] == "Test admin"
    assert entry["details"] == {"max_upload_mb": 2}
    older = admin.client.get("/api/admin/audit?limit=1&offset=1").json()["items"]
    assert [item["action"] for item in older] == ["auth.admin_login"]


@pytest.mark.db
def test_stats_count_this_customers_activity(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    ready = api.add_document(admin.customer_id, status="ready")
    api.add_document(admin.customer_id, status="rejected", failure_code="virus_found")
    api.add_page(admin.customer_id, ready)
    api.add_value(admin.customer_id, ready)
    first = admin.client.post("/api/chat", json={"question": "a"}, headers=CSRF).json()
    api.answers.reply = answer_json(status="not_found")
    admin.client.post("/api/chat", json={"question": "b"}, headers=CSRF)
    api.answers.reply = answer_json(label="needs_review")
    admin.client.post("/api/chat", json={"question": "c"}, headers=CSRF)
    admin.client.post(f"/api/messages/{first['answer']['id']}/feedback", json={"rating": "up"}, headers=CSRF)

    assert admin.client.get("/api/admin/stats").json() == {
        "documents_by_status": {"ready": 1, "rejected": 1},
        "questions_total": 3,
        "answered": 2,
        "not_found": 1,
        "verified_answers": 1,
        "needs_review_answers": 1,
        "feedback_up": 1,
        "feedback_down": 0,
        "open_review_items": 1,
        "older_versions": 0,
        "in_trash": 0,
        "pages_this_month": {"cells": 0, "text_layer": 0, "ocr": 0, "blank": 0},  # PRD 2 R6
        "reader_pages": {"gemini": 0, "local": 0, "cached": 0, "read_again": 0},  # PRD 5 U9
        "spend_estimate": {"input_tokens": 0, "output_tokens": 0, "usd": 0.0},  # nothing spent (SP1)
    }


@pytest.mark.db
def test_stats_count_each_family_by_its_current_version_and_older_versions_and_trash_apart(
    db: Database,
) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    first = api.add_document(c, status="ready", is_current=False)
    current = api.add_document(c, status="ready", family_id=first, version_no=2)
    reading = api.add_document(c, status="received", family_id=first, version_no=3, is_current=False)
    for document in (first, current, reading):
        api.add_page(c, document)
        api.add_value(c, document)
    binned = api.add_document(c, status="ready", deleted_at=api.clock(), deleted_by=admin.user_id)
    api.add_document(c, family_id=binned, version_no=2, is_current=False, deleted_at=api.clock())
    api.add_document(c, status="failed", failure_code="corrupt_file")

    stats = admin.client.get("/api/admin/stats").json()

    assert stats["documents_by_status"] == {"ready": 1, "failed": 1}
    assert stats["open_review_items"] == 1  # the current version's value, as the review queue shows it
    assert (stats["older_versions"], stats["in_trash"]) == (2, 1)


# --- pages read this month, by route (PRD 2 R6) ------------------------------------------------


def _run(
    api: Api,
    customer_id: str,
    document_id: str,
    started: datetime,
    *,
    pages_cells: int = 0,
    pages_text_layer: int = 0,
    pages_ocr: int = 0,
    pages_blank: int = 0,
) -> None:
    run = PgRuns(api.db).start(customer_id, document_id, now=started)
    done = totals(
        pages_cells=pages_cells,
        pages_text_layer=pages_text_layer,
        pages_ocr=pages_ocr,
        pages_blank=pages_blank,
    )
    PgRuns(api.db).finish(customer_id, FinishedRun(run, done, started))


def test_the_month_is_worked_out_in_the_customers_timezone() -> None:
    start, end = month_window(datetime(2026, 9, 30, 19, 0, tzinfo=UTC), "Asia/Kolkata")  # 00:30 IST, 1 Oct
    assert (start.isoformat(), end.isoformat()) == ("2026-10-01T00:00:00+05:30", "2026-11-01T00:00:00+05:30")
    start, end = month_window(datetime(2026, 12, 15, tzinfo=UTC), "Asia/Kolkata")
    assert (start.isoformat(), end.isoformat()) == ("2026-12-01T00:00:00+05:30", "2027-01-01T00:00:00+05:30")


@pytest.mark.db
def test_stats_count_the_pages_read_this_month_by_route(db: Database) -> None:
    api = Api(db, clock=MovingClock(datetime(2026, 10, 20, 6, 0, tzinfo=UTC)))
    admin = api.new_admin()
    c = admin.customer_id
    doc = api.add_document(c)
    _run(api, c, doc, datetime(2026, 10, 2, tzinfo=UTC), pages_text_layer=3, pages_ocr=2)
    _run(api, c, doc, datetime(2026, 10, 3, tzinfo=UTC), pages_ocr=1, pages_blank=1)  # a read again
    _run(api, c, doc, datetime(2026, 9, 29, tzinfo=UTC), pages_cells=9)  # last month
    other = api.new_admin()
    _run(
        api,
        other.customer_id,
        api.add_document(other.customer_id),
        datetime(2026, 10, 2, tzinfo=UTC),
        pages_ocr=50,
    )

    stats = admin.client.get("/api/admin/stats").json()

    assert stats["pages_this_month"] == {"cells": 0, "text_layer": 3, "ocr": 3, "blank": 1}


@pytest.mark.db
def test_a_run_at_00_30_ist_on_the_1st_counts_in_the_new_month(db: Database) -> None:
    api = Api(db, clock=MovingClock(datetime(2026, 10, 1, 1, 0, tzinfo=UTC)))  # 06:30 IST, 1 Oct
    admin = api.new_admin()
    c = admin.customer_id
    doc = api.add_document(c)
    _run(api, c, doc, datetime(2026, 9, 30, 19, 0, tzinfo=UTC), pages_ocr=4)  # 00:30 IST, 1 Oct
    _run(api, c, doc, datetime(2026, 9, 30, 18, 0, tzinfo=UTC), pages_ocr=7)  # 23:30 IST, 30 Sep

    assert admin.client.get("/api/admin/stats").json()["pages_this_month"]["ocr"] == 4
    api.clock.now = datetime(2026, 9, 30, 18, 45, tzinfo=UTC)  # 00:15 IST, 1 Oct
    assert admin.client.get("/api/admin/stats").json()["pages_this_month"]["ocr"] == 4


# --- lockout, temporary passwords and 2FA: the admin's three buttons ----------------------------


def _lock(api: Api, email: str) -> None:
    client = api.client()
    for _ in range(api.settings.login_max_failures):
        client.post("/api/auth/login", json={"email": email, "password": WRONG}, headers=CSRF)


@pytest.mark.db
def test_the_user_list_shows_lock_2fa_and_password_status(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member_id, member_email = api.add_user(admin.customer_id, "member")
    _lock(api, member_email)

    items = {item["id"]: item for item in admin.client.get("/api/admin/users").json()["items"]}

    assert all(set(item) == ADMIN_USER_FIELDS for item in items.values())
    assert items[member_id]["locked_until"] == (api.clock() + timedelta(minutes=15)).isoformat()
    assert items[member_id]["totp_enabled"] is False
    assert items[admin.user_id]["totp_enabled"] is True
    assert items[admin.user_id]["must_change_password"] is False
    api.clock.advance(15 * 60 + 1)
    later = {item["id"]: item for item in admin.client.get("/api/admin/users").json()["items"]}
    assert later[member_id]["locked_until"] is None  # a lock that has passed is not shown


@pytest.mark.db
def test_unlock(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member_id, member_email = api.add_user(admin.customer_id, "member")
    _lock(api, member_email)

    reply = admin.client.post(f"/api/admin/users/{member_id}/unlock", headers=CSRF)

    assert reply.status_code == 200, reply.text
    assert set(reply.json()) == ADMIN_USER_FIELDS
    assert reply.json()["locked_until"] is None
    login = api.client().post(
        "/api/auth/login", json={"email": member_email, "password": PASSWORD}, headers=CSRF
    )
    assert login.status_code == 200
    assert api.audit_actions(admin.customer_id)[-2:] == ["user.unlock", "auth.login"]


@pytest.mark.db
def test_reset_password_gives_a_temporary_password_and_ends_sessions(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")

    reply = admin.client.post(f"/api/admin/users/{member.user_id}/reset-password", headers=CSRF)

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert set(body["user"]) == ADMIN_USER_FIELDS
    assert body["user"]["must_change_password"] is True
    assert len(body["temporary_password"]) == 16
    assert member.client.get("/api/auth/me").status_code == 401
    assert api.audit_actions(admin.customer_id)[-1] == "user.reset_password"
    with db.customer(admin.customer_id) as conn:
        details: dict[str, object] = conn.execute(
            text("SELECT details FROM audit_log WHERE action = 'user.reset_password'")
        ).scalar_one()
    assert body["temporary_password"] not in json.dumps(details)


@pytest.mark.db
def test_reset_2fa_clears_the_code_so_the_password_alone_signs_in(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    colleague = api.enrol_admin(admin.customer_id)

    reply = admin.client.post(f"/api/admin/users/{colleague.user_id}/reset-2fa", headers=CSRF)

    assert reply.status_code == 200, reply.text
    assert set(reply.json()) == ADMIN_USER_FIELDS
    assert reply.json()["totp_enabled"] is False
    state = api.user_state(admin.customer_id, colleague.user_id)
    assert (state["totp_secret_enc"], state["totp_enabled_at"], state["totp_last_step"]) == (None, None, None)
    assert api.audit_actions(admin.customer_id)[-1] == "user.reset_totp"
    signed_in = api.admin_login(api.client(), colleague.email)
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["user"]["totp_enabled"] is False
    assert signed_in.json()["session"] == {"scope": "admin"}


@pytest.mark.db
def test_an_admin_cannot_reset_their_own_2fa(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    before = api.user_state(admin.customer_id, admin.user_id)

    reply = admin.client.post(f"/api/admin/users/{admin.user_id}/reset-2fa", headers=CSRF)

    assert reply.status_code == 400
    assert reply.json()["error"]["code"] == "bad_request"
    assert api.user_state(admin.customer_id, admin.user_id) == before
    assert admin.client.get("/api/admin/users").status_code == 200


@pytest.mark.db
@pytest.mark.parametrize("action", ["unlock", "reset-password", "reset-2fa"])
def test_the_three_buttons_on_an_unknown_user_are_404(db: Database, action: str) -> None:
    admin = Api(db).new_admin()
    reply = admin.client.post(f"/api/admin/users/{ANY_ID}/{action}", headers=CSRF)
    assert reply.status_code == 404
    assert reply.json()["error"]["code"] == "not_found"
    malformed = admin.client.post(f"/api/admin/users/not-a-uuid/{action}", headers=CSRF)
    assert malformed.status_code == 400


@pytest.mark.db
def test_disabling_a_user_ends_their_sessions(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    version = api.user_state(admin.customer_id, member.user_id)["session_version"]

    admin.client.patch(f"/api/admin/users/{member.user_id}", json={"disabled": True}, headers=CSRF)

    assert api.user_state(admin.customer_id, member.user_id)["session_version"] > version
    assert member.client.get("/api/auth/me").status_code == 401
