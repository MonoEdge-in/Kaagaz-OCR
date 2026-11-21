"""The admin routes against a real database, called with an admin session
(signed in on the admin screen with a code; tests/test_api_admin_sign_in.py
checks that an app session is refused on every one of them)."""

import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import RowMapping, text

from kaagaz.api import keycloak_admin as kc_admin
from kaagaz.api.routers.admin import month_window
from kaagaz.api.routers.auth import ACCOUNT_MAX_FAILURES
from kaagaz.api.views import audit_entry
from kaagaz.db import keycloak as keycloak_sql
from kaagaz.db.engine import Database
from kaagaz.db.runs import PgRuns
from kaagaz.pipeline import FinishedRun
from tests.settings_factory import make_settings
from tests.test_api_model_choice import add_model, allow
from tests.test_api_support import ANY_ID, CSRF, PASSWORD, Api, MovingClock, Signed, answer_json
from tests.test_api_usage import NOW, THIS_MONTH, add_spend, set_budget
from tests.test_runs_db import totals

# Money in a response: a key or a text that names it, or a currency sign (Tushar, 15 Oct 2026).
MONEY = re.compile(r"usd|price|budget|cost|[$₹]", re.IGNORECASE)

# How a person signs in and the Google tag (#457 phase 1, #507): only in replies that know them.
ADMIN_USER_FIELDS = {
    "id",
    "email",
    "display_name",
    "role",
    "disabled",
    "created_at",
    "locked_until",
    "totp_enabled",
    "two_step_turned_off",
    "must_change_password",
    "can_delete_forever",
    "company_sign_in",  # Changed on purpose (21 Nov 2026): connected, or waiting for setup
    "management",  # Changed on purpose (PRD 7, #601): Management on the person
}
WRONG = "wrong password!!"


ISSUER = "https://id.example.test/realms/kaagaz-test"
KEYCLOAK_ON = {
    "keycloak_enabled": True,
    "keycloak_issuer": ISSUER,
    "keycloak_client_id": "kaagaz-web",
    "keycloak_admin_client_id": "kaagaz-provisioner",
    "keycloak_admin_client_secret": "a-test-provisioner-secret-of-32-chars00",
}


ADMIN_SESSION_REFUSED = {
    "error": {
        "code": "admin_session_required",
        "message": "Only admins can use this. Ask your admin if you need it.",
    }
}


class _Keycloak:
    """Stands in for Keycloak's admin API (#737): records every call; no network."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.existing: set[str] = set()
        self.email_fails = False
        self.unfinished = True
        monkeypatch.setattr(kc_admin, "setup_unfinished", lambda settings, subject: self.unfinished)
        for name in ("find_user_by_email", "create_user", "send_setup_email"):
            monkeypatch.setattr(kc_admin, name, getattr(self, f"_{name}"))
        for name in ("remove_otp", "set_enabled", "delete_user", "clear_lock"):
            monkeypatch.setattr(kc_admin, name, self._recorder(name))

    def _find_user_by_email(self, settings: object, email: str) -> str | None:
        return "someone-else" if email in self.existing else None

    def _create_user(self, settings: object, *, email: str, name: str) -> str:
        self.calls.append(("create_user", (email, name)))
        return f"kc-{uuid.uuid4().hex}"

    def _send_setup_email(self, settings: object, subject: str) -> None:
        self.calls.append(("send_setup_email", (subject,)))
        if self.email_fails:
            raise kc_admin.KeycloakAdminError()

    def _recorder(self, name: str) -> Callable[..., None]:
        def record(settings: object, subject: str, *args: object) -> None:
            self.calls.append((name, (subject, *args)))

        return record

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def subject(self) -> str:
        return str(next(args[0] for name, args in self.calls if name == "send_setup_email"))


def _keycloak_api(db: Database, monkeypatch: pytest.MonkeyPatch) -> tuple[Api, _Keycloak]:
    return Api(db, settings=make_settings(**KEYCLOAK_ON)), _Keycloak(monkeypatch)


@pytest.mark.db
def test_admin_adds_a_person_who_is_linked_and_emailed_a_setup_link(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, keycloak = _keycloak_api(db, monkeypatch)
    admin = api.new_admin()
    email = f"New.Person.{admin.user_id[:8]}@Example.test"

    created = admin.client.post(
        "/api/admin/users",
        json={"email": email, "display_name": " New Person ", "role": "reviewer"},
        headers=CSRF,
    )

    assert created.status_code == 201, created.text
    user = created.json()
    assert set(user) == ADMIN_USER_FIELDS | {"setup_email_sent", "message"}
    assert user["email"] == email.lower() and user["display_name"] == "New Person"
    assert user["role"] == "reviewer" and user["disabled"] is False
    assert user["setup_email_sent"] is True
    assert user["message"] == (
        f"Added. We emailed {email.lower()} a link to set their password and authenticator."
        " It works for 12 hours."
    )
    assert keycloak.names() == ["create_user", "send_setup_email"]
    assert keycloak.calls[0][1] == (email.lower(), "New Person")
    assert api.audit_actions(admin.customer_id)[-2:] == ["user.create", "auth.setup_email_sent"]
    listed = admin.client.get("/api/admin/users").json()["items"]
    assert user["id"] in [item["id"] for item in listed]
    with db.system() as conn:  # linked to exactly the account that was made, and on Keycloak
        found = keycloak_sql.lookup(conn, ISSUER, keycloak.subject())
    assert found is not None and found.user_id == user["id"]
    with db.customer(admin.customer_id) as conn:
        details: dict[str, object] = conn.execute(
            text("SELECT details FROM audit_log WHERE action = 'user.create'")
        ).scalar_one()
    assert "password" not in json.dumps(details)


@pytest.mark.db
def test_an_email_keycloak_already_has_is_refused_and_no_person_is_made(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, keycloak = _keycloak_api(db, monkeypatch)
    admin = api.new_admin()
    email = f"taken-{admin.user_id[:8]}@example.test"
    keycloak.existing.add(email)
    reply = admin.client.post(
        "/api/admin/users", json={"email": email, "display_name": "Copy", "role": "member"}, headers=CSRF
    )
    assert reply.status_code == 409
    assert reply.json()["error"]["message"] == (
        "This email already has a company sign-in account. Ask your super admin."
    )
    assert keycloak.calls == []  # not created, not linked
    listed = admin.client.get("/api/admin/users").json()["items"]
    assert email not in [item["email"] for item in listed]


@pytest.mark.db
def test_a_failed_setup_email_leaves_the_person_linked_and_says_to_resend(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, keycloak = _keycloak_api(db, monkeypatch)
    admin = api.new_admin()
    keycloak.email_fails = True
    email = f"mail-{admin.user_id[:8]}@example.test"
    reply = admin.client.post(
        "/api/admin/users", json={"email": email, "display_name": "Mail", "role": "member"}, headers=CSRF
    )
    assert reply.status_code == 201
    assert reply.json()["setup_email_sent"] is False
    assert reply.json()["message"] == "Added, but the setup email could not be sent. Use Resend setup email."
    with db.system() as conn:
        assert keycloak_sql.lookup(conn, ISSUER, keycloak.subject()) is not None  # linked
    keycloak.email_fails = False
    keycloak.calls.clear()
    again = admin.client.post(f"/api/admin/users/{reply.json()['id']}/resend-setup", headers=CSRF)
    assert again.status_code == 200, again.text
    assert again.json()["message"] == "Done. We emailed a new setup link. It works for 12 hours."
    assert keycloak.names() == ["send_setup_email"]


@pytest.mark.db
def test_a_taken_email_is_409_even_in_another_customer_and_the_new_keycloak_account_is_removed(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, keycloak = _keycloak_api(db, monkeypatch)
    admin = api.new_admin()
    other_customer, _ = api.customer()
    _, taken = api.add_user(other_customer, "member")
    reply = admin.client.post(
        "/api/admin/users", json={"email": taken, "display_name": "Copy", "role": "member"}, headers=CSRF
    )
    assert reply.status_code == 409
    assert reply.json()["error"]["code"] == "conflict"
    assert taken not in reply.text
    assert keycloak.names() == ["create_user", "delete_user"]  # nothing half-made is left in Keycloak


@pytest.mark.db
def test_adding_a_person_needs_company_sign_in_to_be_set_up(db: Database) -> None:
    admin = Api(db).new_admin()  # Keycloak is off on this server
    reply = admin.client.post(
        "/api/admin/users",
        json={"email": "a@b.test", "display_name": "A", "role": "member"},
        headers=CSRF,
    )
    assert reply.status_code == 409
    assert (
        reply.json()["error"]["message"]
        == "Company sign-in is not set up on this server. Ask your super admin."
    )


@pytest.mark.db
def test_an_admin_resets_a_members_authenticator_unlocks_and_disables_in_keycloak_too(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, keycloak = _keycloak_api(db, monkeypatch)
    admin = api.new_admin()
    member = admin.client.post(
        "/api/admin/users",
        json={"email": f"m-{admin.user_id[:8]}@example.test", "display_name": "Mira Rao", "role": "member"},
        headers=CSRF,
    ).json()
    subject = keycloak.subject()
    keycloak.calls.clear()

    reset = admin.client.post(f"/api/admin/users/{member['id']}/reset-2fa", headers=CSRF)
    assert reset.status_code == 200, reset.text
    assert reset.json()["message"] == (
        "Done. Mira Rao will set up their authenticator again at their next sign-in."
    )
    assert admin.client.post(f"/api/admin/users/{member['id']}/unlock", headers=CSRF).status_code == 200
    for disabled in (True, False):
        changed = admin.client.patch(
            f"/api/admin/users/{member['id']}", json={"disabled": disabled}, headers=CSRF
        )
        assert changed.status_code == 200, changed.text
    assert keycloak.calls == [
        ("remove_otp", (subject,)),
        ("clear_lock", (subject,)),
        ("set_enabled", (subject, False)),
        ("set_enabled", (subject, True)),
    ]
    other = api.new_admin()  # another organisation's admin cannot reach this person
    assert other.client.post(f"/api/admin/users/{member['id']}/reset-2fa", headers=CSRF).status_code == 404
    assert keycloak.calls[-1][0] == "set_enabled"


@pytest.mark.db
def test_an_admin_cannot_reset_another_admins_authenticator(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, keycloak = _keycloak_api(db, monkeypatch)
    admin = api.new_admin()
    other_id, _ = api.add_user(admin.customer_id, "admin")
    reply = admin.client.post(f"/api/admin/users/{other_id}/reset-2fa", headers=CSRF)
    assert reply.status_code == 403
    assert keycloak.calls == []


@pytest.mark.db
@pytest.mark.parametrize(
    "body",
    [
        {"email": "no-at-sign", "display_name": "X", "role": "member"},
        {"email": "a@b.test", "display_name": "X", "role": "owner"},
        {"email": "a@b.test", "display_name": "   ", "role": "member"},
        {"email": "a@b.test", "display_name": "X", "role": "member", "password": PASSWORD},
    ],
)
def test_bad_new_users_are_400(db: Database, body: dict[str, str]) -> None:
    admin = Api(db).new_admin()
    assert admin.client.post("/api/admin/users", json=body, headers=CSRF).status_code == 400


@pytest.mark.db
def test_update_role_and_disable(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")

    promoted = admin.client.patch(
        f"/api/admin/users/{member.user_id}", json={"role": "reviewer"}, headers=CSRF
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "reviewer"
    assert member.client.get("/api/auth/me").status_code == 401  # the old session named the old role

    disabled = admin.client.patch(f"/api/admin/users/{member.user_id}", json={"disabled": True}, headers=CSRF)
    assert disabled.json()["disabled"] is True
    assert member.client.get("/api/auth/me").status_code == 401  # the old session ends with it
    assert api.audit_actions(admin.customer_id).count("user.update") == 2


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
@pytest.mark.parametrize("body", [{"disabled": True}, {"role": "member"}, {"role": "reviewer"}])
def test_an_admin_cannot_lock_themselves_out(db: Database, body: dict[str, object]) -> None:
    admin = Api(db).new_admin()
    reply = admin.client.patch(f"/api/admin/users/{admin.user_id}", json=body, headers=CSRF)
    assert reply.status_code == 400
    assert admin.client.get("/api/auth/me").json()["user"]["role"] == "admin"


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
        "autofile": {"granted": False, "on": False},  # feature 28: not granted
    }  # Changed on purpose (#457 review): no auto_approve_connections while no provider is on
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

    assert body["total"] == 2  # auth.login, settings.update
    entry = body["items"][0]
    # Changed on purpose (PRD 1, activity/search): A5's entry and the cursor. Changed on purpose
    # (PRD 7, #601): subject_id, whom an entry is about.
    assert set(entry) == {
        "id", "action", "actor_id", "actor_name", "target_type", "target_id", "family_id",
        "document", "folder", "details", "created_at", "subject_id",
    }  # fmt: skip
    assert body["next_cursor"] is not None
    assert entry["action"] == "settings.update"
    assert entry["actor_name"] == "Test admin"
    assert entry["details"] == {"max_upload_mb": 2}
    older = admin.client.get("/api/admin/audit?limit=1&offset=1").json()["items"]
    assert [item["action"] for item in older] == ["auth.login"]


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
        # Changed on purpose (features 25-27, 15 Oct 2026): scan tokens only, no dollars, since org
        # admins never see money (Tushar).
        "scan_tokens": {"input_tokens": 0, "output_tokens": 0},
    }


def money_in(value: object, path: str = "") -> list[str]:
    """Every key, and every text value, in ``value`` that names money or shows a currency."""
    if isinstance(value, dict):
        found = [f"{path}.{key}" for key in value if MONEY.search(str(key))]
        return found + [hit for key, item in value.items() for hit in money_in(item, f"{path}.{key}")]
    if isinstance(value, list):
        return [hit for index, item in enumerate(value) for hit in money_in(item, f"{path}[{index}]")]
    return [f"{path}={value}"] if isinstance(value, str) and MONEY.search(value) else []


def test_an_old_org_models_set_audit_row_shows_no_budget() -> None:
    """Rows 0023 wrote before 15 Oct 2026 hold the operator's budget_usd; the org never sees it."""
    details = {"by": "operator", "models": ["google/a"], "default": "google/a", "budget_usd": 12.5}
    # Changed on purpose (PRD 1, activity/search): the row has the A3 and A5 columns, and
    # the admin's log is the full one.
    row = {
        "id": 7,
        "action": "org.models_set",
        "actor_user_id": None,
        "target_type": "customer",
        "target_id": "c",
        "details": details,
        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
        "folder_id": None,
        "family_id": None,
    }
    entry = audit_entry(cast(RowMapping, row), full=True)
    assert entry["details"] == {"by": "operator", "models": ["google/a"], "default": "google/a"}
    assert money_in(entry) == []
    plain = {**row, "action": "settings.update", "details": None}
    assert audit_entry(cast(RowMapping, plain), full=True)["details"] is None


@pytest.mark.db
def test_an_org_admin_sees_no_money_in_stats_models_usage_or_audit(db: Database) -> None:
    """Tushar, 15 Oct 2026: org admins never see money, prices, budgets or costs. A budget,
    priced models, spend and an old budget audit row are all there; no response names them."""
    # Changed on purpose (#239, 27 Oct 2026): a made-up key. The app builds its model client
    # at start (#212), and Settings.from_env refuses MODEL_ENABLED or gemini without a key.
    settings = make_settings(ocr_engine="gemini", model_enabled=True, openrouter_api_key="test-key")
    api = Api(db, settings=settings, clock=MovingClock(NOW))
    admin = api.new_admin()
    c = admin.customer_id
    model = add_model(db, name="Default model")
    allow(db, c, [model], default=model)
    set_budget(db, c, 10.0)
    add_spend(db, c, at=THIS_MONTH, cost=2.5)
    with db.engine.begin() as conn:  # the owner, as 0023's kaagaz_op_set_org_models wrote it
        conn.execute(
            text(
                "INSERT INTO audit_log (customer_id, actor_user_id, action, target_type, target_id, details)"
                # Fixed (#239, 27 Oct 2026): one parameter cannot be both the uuid and the text.
                " VALUES (:c, NULL, 'org.models_set', 'customer', :t,"
                " jsonb_build_object('by', 'operator', 'budget_usd', 10.0))"
            ),
            {"c": c, "t": c},
        )

    for url in ("/api/admin/stats", "/api/admin/models", "/api/admin/usage", "/api/admin/audit"):
        reply = admin.client.get(url)
        assert reply.status_code == 200, url
        assert money_in(reply.json()) == [], url
    assert admin.client.get("/api/admin/models").json() == {
        "scan": [{"name": "Default model", "default": True}],
        "chat": [{"name": settings.model_chat, "default": True}],
    }
    csv_text = admin.client.get("/api/admin/usage.csv").text
    assert csv_text.splitlines()[0] == "day,user,model,kind,calls,tokens"
    assert not MONEY.search(csv_text)


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
    """The account is locked for an hour, as the operator's sign-in lock leaves it (there is no
    other sign-in with a lock any more)."""
    with api.db.engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET failed_logins = :n, locked_until = :until WHERE email = :e"),
            {"n": ACCOUNT_MAX_FAILURES, "until": api.clock() + timedelta(minutes=60), "e": email.lower()},
        )


@pytest.mark.db
def test_the_user_list_shows_lock_2fa_and_password_status(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member_id, member_email = api.add_user(admin.customer_id, "member")
    _lock(api, member_email)

    items = {item["id"]: item for item in admin.client.get("/api/admin/users").json()["items"]}

    # Changed on purpose (#457 phase 1): the list says how each person signs in.
    # Changed on purpose (#507): and whether their role needs a Google account connected, or
    # (#507 review) its domain checked.
    # Changed on purpose (PRD 7, #601): and the departments each is in.
    assert all(set(item) == ADMIN_USER_FIELDS | {"departments"} for item in items.values())
    # Changed on purpose (A2 review): the account's own lock lasts ACCOUNT_LOCK_MINUTES.
    assert items[member_id]["locked_until"] == (api.clock() + timedelta(minutes=60)).isoformat()
    assert items[member_id]["totp_enabled"] is False
    assert items[admin.user_id]["totp_enabled"] is True
    assert items[admin.user_id]["must_change_password"] is False
    api.clock.advance(60 * 60 + 1)
    again = api.new_admin_of(admin.customer_id)  # an admin session lasts an hour too
    later = {item["id"]: item for item in again.client.get("/api/admin/users").json()["items"]}
    assert later[member_id]["locked_until"] is None  # a lock that has passed is not shown


@pytest.mark.db
def test_company_sign_in_is_waiting_until_linked_and_signed_in_once(db: Database) -> None:
    """The People list's `company_sign_in` drives Resend: waiting until the person is linked to
    Keycloak AND has signed in once, then connected."""
    api = Api(db)
    admin = api.new_admin()
    member_id, _ = api.add_user(admin.customer_id, "member")  # made, never linked

    def state() -> str:
        items = {item["id"]: item for item in admin.client.get("/api/admin/users").json()["items"]}
        return str(items[member_id]["company_sign_in"])

    assert state() == "waiting"
    with db.engine.begin() as conn:  # linked, but never signed in
        conn.execute(
            text("UPDATE users SET auth_authority = 'keycloak' WHERE customer_id = :c AND id = :u"),
            {"c": admin.customer_id, "u": member_id},
        )
    assert state() == "waiting"
    api.mint_session(api.client(), admin.customer_id, member_id)  # their first sign-in
    assert state() == "connected"


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
    assert api.audit_actions(admin.customer_id)[-1] == "user.unlock"


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
@pytest.mark.parametrize("action", ["unlock", "reset-2fa"])
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


def _member_in_keycloak(api: Api, keycloak: _Keycloak, admin: Signed, role: str = "member") -> dict[str, Any]:
    person: dict[str, Any] = admin.client.post(
        "/api/admin/users",
        json={
            "email": f"{role}-{uuid.uuid4().hex[:10]}@example.test",
            "display_name": "Mira Rao",
            "role": role,
        },
        headers=CSRF,
    ).json()
    keycloak.calls.clear()
    return person


@pytest.mark.db
def test_resend_setup_is_refused_once_the_person_finished_setting_up_and_for_an_admin(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, keycloak = _keycloak_api(db, monkeypatch)
    admin = api.new_admin()
    member = _member_in_keycloak(api, keycloak, admin)
    keycloak.unfinished = False
    refused = admin.client.post(f"/api/admin/users/{member['id']}/resend-setup", headers=CSRF)
    assert refused.status_code == 409
    assert refused.json()["error"]["message"] == (
        "They have already set up their account. Ask them to use Forgot password,"
        " or reset their authenticator."
    )
    assert keycloak.calls == []
    other_admin_id, _ = api.add_user(admin.customer_id, "admin")
    keycloak.unfinished = True
    for path in ("resend-setup", "reset-2fa"):
        reply = admin.client.post(f"/api/admin/users/{other_admin_id}/{path}", headers=CSRF)
        assert reply.status_code == 403
        assert reply.json()["error"]["message"] == (
            "Only your super admin can reset another admin's authenticator."
        )
    assert keycloak.calls == []


@pytest.mark.db
def test_a_person_not_on_company_sign_in_gets_a_next_step(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, keycloak = _keycloak_api(db, monkeypatch)
    admin = api.new_admin()
    legacy_id, _ = api.add_user(admin.customer_id, "member")  # never moved
    for path in ("resend-setup", "reset-2fa"):
        reply = admin.client.post(f"/api/admin/users/{legacy_id}/{path}", headers=CSRF)
        assert reply.status_code == 409
        assert reply.json()["error"]["message"].endswith(
            "Ask your super admin to move your organisation to company sign-in."
        )
    assert keycloak.calls == []


@pytest.mark.db
def test_resetting_an_authenticator_signs_the_person_out_everywhere(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, keycloak = _keycloak_api(db, monkeypatch)
    admin = api.new_admin()
    member = _member_in_keycloak(api, keycloak, admin)
    before = int(api.user_state(admin.customer_id, member["id"])["session_version"])
    reply = admin.client.post(f"/api/admin/users/{member['id']}/reset-2fa", headers=CSRF)
    assert reply.status_code == 200, reply.text
    assert int(api.user_state(admin.customer_id, member["id"])["session_version"]) == before + 1


@pytest.mark.db
@pytest.mark.parametrize("action", ["resend-setup", "reset-2fa", "unlock", "disable"])
def test_an_admin_of_another_organisation_reaches_nobody_and_keycloak_is_never_called(
    db: Database, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    api, keycloak = _keycloak_api(db, monkeypatch)
    mine, theirs = api.new_admin(), api.new_admin()
    person = _member_in_keycloak(api, keycloak, theirs)
    if action == "disable":
        reply = mine.client.patch(f"/api/admin/users/{person['id']}", json={"disabled": True}, headers=CSRF)
    else:
        reply = mine.client.post(f"/api/admin/users/{person['id']}/{action}", headers=CSRF)
    assert reply.status_code in (404, 409), reply.text
    assert keycloak.calls == []
    assert api.user_state(theirs.customer_id, person["id"])["disabled"] is False


@pytest.mark.db
@pytest.mark.parametrize("role", ["member", "reviewer"])
@pytest.mark.parametrize("action", ["resend-setup", "reset-2fa"])
def test_a_member_or_reviewer_cannot_use_the_new_admin_routes(db: Database, role: str, action: str) -> None:
    api = Api(db)
    signed = api.new_signed(role)
    reply = signed.client.post(f"/api/admin/users/{ANY_ID}/{action}", headers=CSRF)
    assert reply.status_code == 403
    assert reply.json() == ADMIN_SESSION_REFUSED
