"""``clear-legacy-credentials --customer-id`` (21 Nov 2026): once everyone in an organisation is on
Keycloak, their old password hashes and two-step secrets go. It refuses, changing nothing, while an
active person is not linked yet; a disabled person who never linked is left alone."""

import io
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api.auth import verify_password
from kaagaz.cli import NO_PASSWORD, main
from kaagaz.db.engine import Database
from tests.test_api_support import Api

pytestmark = pytest.mark.db


def _clear(db: Database, customer_id: str) -> tuple[int, str]:
    out = io.StringIO()
    code = main(["clear-legacy-credentials", "--customer-id", customer_id], db=db, environ={}, out=out)
    return code, out.getvalue()


def _rows(db: Database, customer_id: str) -> dict[str, dict[str, Any]]:
    with db.engine.begin() as conn:
        found = conn.execute(
            text(
                "SELECT id::text AS id, password_hash, totp_secret_enc, totp_enabled_at, auth_authority"
                " FROM users WHERE customer_id = :c"
            ),
            {"c": customer_id},
        ).mappings()
        return {row["id"]: dict(row) for row in found}


def test_it_clears_linked_people_and_prints_the_counts(db: Database) -> None:
    api = Api(db)
    customer, _ = api.customer()
    plain = api.sign_in(customer)  # signing in makes them Keycloak's (Api.mint_session)
    enrolled = api.enrol_user(customer, "member")
    api.mint_session(api.client(), customer, enrolled.user_id)
    first_admin = next(
        user for user, row in _rows(db, customer).items() if user not in {plain.user_id, enrolled.user_id}
    )
    api.mint_session(api.client(), customer, first_admin)
    before = _rows(db, customer)
    assert verify_password("correct horse battery", before[plain.user_id]["password_hash"])

    code, said = _clear(db, customer)

    assert (code, said) == (
        0,
        f"customer {customer}: old credentials blanked for 3 people (1 had a two-step code)\n",
    )
    for user, row in _rows(db, customer).items():
        assert row["password_hash"] == NO_PASSWORD, user
        assert (row["totp_secret_enc"], row["totp_enabled_at"]) == (None, None)
        assert not verify_password("correct horse battery", row["password_hash"])
    assert _clear(db, customer) == (
        0,
        f"customer {customer}: old credentials blanked for 0 people (0 had a two-step code)\n",
    )
    with db.engine.begin() as conn:
        actions = conn.execute(
            text("SELECT action FROM platform_audit WHERE subject_customer_id = :c ORDER BY id"),
            {"c": customer},
        ).scalars()
        assert list(actions) == ["customer.clear_legacy_credentials"] * 2


def test_it_refuses_while_an_active_person_is_not_linked_and_changes_nothing(db: Database) -> None:
    api = Api(db)
    customer, _ = api.customer()
    linked = api.sign_in(customer)
    waiting_id, _ = api.add_user(customer, "member")  # not linked: still the old sign-in
    before = _rows(db, customer)

    code, said = _clear(db, customer)

    assert code == 2 and said == ""
    assert _rows(db, customer) == before
    assert before[linked.user_id]["auth_authority"] == "keycloak"
    assert before[waiting_id]["auth_authority"] == "legacy"


def test_a_disabled_person_who_never_linked_does_not_stop_it(db: Database) -> None:
    api = Api(db)
    customer, admin_id = api.customer()
    api.mint_session(api.client(), customer, admin_id)
    gone_id, _ = api.add_user(customer, "member")
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE users SET disabled = true WHERE id = :u"), {"u": gone_id})
    before = _rows(db, customer)[gone_id]

    code, said = _clear(db, customer)

    assert code == 0 and "for 1 people" in said
    assert _rows(db, customer)[gone_id] == before  # not linked, so not touched


def test_it_needs_a_valid_customer(db: Database) -> None:
    assert _clear(db, "not-an-id")[0] == 2
    assert _clear(db, "7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e")[0] == 2
