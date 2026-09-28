"""The admin routes against a real database."""

import json

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, PASSWORD, Api, answer_json


@pytest.mark.db
def test_admin_lists_and_creates_users_and_the_new_user_can_sign_in(db: Database) -> None:
    api = Api(db)
    admin = api.new_signed("admin")
    email = f"New.Person.{admin.user_id[:8]}@Example.test"

    created = admin.client.post(
        "/api/admin/users",
        json={"email": email, "display_name": " New Person ", "role": "reviewer", "password": PASSWORD},
        headers=CSRF,
    )

    assert created.status_code == 201
    user = created.json()
    assert set(user) == {"id", "email", "display_name", "role", "disabled", "created_at"}
    assert user["email"] == email.lower()
    assert user["display_name"] == "New Person"
    assert user["role"] == "reviewer"
    assert user["disabled"] is False
    assert api.audit_actions(admin.customer_id)[-1] == "user.create"
    listed = admin.client.get("/api/admin/users").json()["items"]
    assert user["id"] in [item["id"] for item in listed]
    login = api.client().post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF)
    assert login.status_code == 200
    with db.customer(admin.customer_id) as conn:
        details: dict[str, object] = conn.execute(
            text("SELECT details FROM audit_log WHERE action = 'user.create'")
        ).scalar_one()
    assert PASSWORD not in json.dumps(details)


@pytest.mark.db
def test_a_taken_email_is_409_even_in_another_customer(db: Database) -> None:
    api = Api(db)
    admin = api.new_signed("admin")
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
    admin = Api(db).new_signed("admin")
    assert admin.client.post("/api/admin/users", json=body, headers=CSRF).status_code == 400


@pytest.mark.db
def test_update_role_disable_and_password(db: Database) -> None:
    api = Api(db)
    admin = api.new_signed("admin")
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
    login = api.client().post(
        "/api/auth/login", json={"email": member.email, "password": new_password}, headers=CSRF
    )
    assert login.status_code == 200

    disabled = admin.client.patch(f"/api/admin/users/{member.user_id}", json={"disabled": True}, headers=CSRF)
    assert disabled.json()["disabled"] is True
    again = api.client().post(
        "/api/auth/login", json={"email": member.email, "password": new_password}, headers=CSRF
    )
    assert again.status_code == 401
    assert api.audit_actions(admin.customer_id).count("user.update") == 3


@pytest.mark.db
@pytest.mark.parametrize("body", [{"disabled": True}, {"role": "member"}, {"role": "reviewer"}])
def test_an_admin_cannot_lock_themselves_out(db: Database, body: dict[str, object]) -> None:
    admin = Api(db).new_signed("admin")
    reply = admin.client.patch(f"/api/admin/users/{admin.user_id}", json=body, headers=CSRF)
    assert reply.status_code == 400
    assert admin.client.get("/api/auth/me").json()["user"]["role"] == "admin"


@pytest.mark.db
def test_an_admin_may_change_their_own_password(db: Database) -> None:
    admin = Api(db).new_signed("admin")
    reply = admin.client.patch(
        f"/api/admin/users/{admin.user_id}",
        json={"password": "my new long password", "role": "admin"},
        headers=CSRF,
    )
    assert reply.status_code == 200


@pytest.mark.db
def test_update_unknown_user_is_404(db: Database) -> None:
    admin = Api(db).new_signed("admin")
    reply = admin.client.patch(
        "/api/admin/users/7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e", json={"role": "member"}, headers=CSRF
    )
    assert reply.status_code == 404


@pytest.mark.db
def test_settings_read_and_change_within_the_platform_limit(db: Database) -> None:
    api = Api(db)  # platform limit: 5 MB
    customer_id, _ = api.customer("Mehta Textiles")
    admin = api.sign_in(customer_id, "admin")

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
    admin = api.new_signed("admin")
    admin.client.patch("/api/admin/settings", json={"max_upload_mb": 2}, headers=CSRF)

    body = admin.client.get("/api/admin/audit?limit=1").json()

    assert body["total"] == 2  # auth.login, settings.update
    entry = body["items"][0]
    assert set(entry) == {"id", "action", "actor_name", "target_type", "target_id", "details", "created_at"}
    assert entry["action"] == "settings.update"
    assert entry["actor_name"] == "Test admin"
    assert entry["details"] == {"max_upload_mb": 2}
    older = admin.client.get("/api/admin/audit?limit=1&offset=1").json()["items"]
    assert [item["action"] for item in older] == ["auth.login"]


@pytest.mark.db
def test_stats_count_this_customers_activity(db: Database) -> None:
    api = Api(db)
    admin = api.new_signed("admin")
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
    }
