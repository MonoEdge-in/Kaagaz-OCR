"""The set-up commands against a real database. Passwords come from the
environment mapping or a fake prompt, never from flags."""

import io
import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy import text

from kaagaz.api.auth import verify_password
from kaagaz.cli import DEMO_CUSTOMERS, main
from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database
from tests.db_support import new_customer

pytestmark = pytest.mark.db

PASSWORD = "a demo password here"  # made up


def _lookup(db: Database, email: str) -> users_sql.LoginRow | None:
    with db.system() as conn:
        return users_sql.login_lookup(conn, email)


def _never_ask(prompt: str) -> str:
    raise AssertionError("must not prompt when the environment has the password")


def test_create_customer_from_the_environment(db: Database) -> None:
    email = f"Owner-{uuid.uuid4().hex[:8]}@Example.test"
    out = io.StringIO()
    code = main(
        ["create-customer", "--name", "Gupta Stores", "--admin-email", email, "--admin-name", "Owner"],
        db=db,
        environ={"KAAGAZ_ADMIN_PASSWORD": PASSWORD},
        out=out,
        ask=_never_ask,
    )
    assert code == 0
    found = _lookup(db, email)
    assert found is not None
    assert found.role == "admin"
    assert verify_password(PASSWORD, found.password_hash)
    assert out.getvalue() == f"customer {found.customer_id}\n"
    assert PASSWORD not in out.getvalue()


def test_create_user_with_the_prompt(db: Database) -> None:
    customer_id, _ = new_customer(db)
    email = f"clerk-{uuid.uuid4().hex[:8]}@example.test"
    answers = iter([PASSWORD, PASSWORD])
    code = main(
        [
            "create-user",
            "--customer-id",
            customer_id,
            "--email",
            email,
            "--name",
            "Clerk",
            "--role",
            "reviewer",
        ],
        db=db,
        environ={},
        out=io.StringIO(),
        ask=lambda prompt: next(answers),
    )
    assert code == 0
    found = _lookup(db, email)
    assert found is not None
    assert (found.customer_id, found.role) == (customer_id, "reviewer")
    with db.customer(customer_id) as conn:
        actions: Sequence[str] = conn.execute(text("SELECT action FROM audit_log")).scalars().all()
    assert actions == ["user.create"]


@pytest.mark.parametrize(
    ("environ", "answers"),
    [({"KAAGAZ_ADMIN_PASSWORD": "short"}, []), ({}, [PASSWORD, PASSWORD + "x"]), ({}, ["", ""])],
)
def test_bad_or_mismatched_passwords_stop_with_code_2(
    db: Database, environ: dict[str, str], answers: list[str]
) -> None:
    email = f"x-{uuid.uuid4().hex[:8]}@example.test"
    replies = iter(answers)
    code = main(
        ["create-customer", "--name", "X", "--admin-email", email, "--admin-name", "X"],
        db=db,
        environ=environ,
        out=io.StringIO(),
        ask=lambda prompt: next(replies),
    )
    assert code == 2
    assert _lookup(db, email) is None


def test_create_user_for_an_unknown_or_malformed_customer(db: Database) -> None:
    for customer_id in (str(uuid.uuid4()), "not-a-uuid"):
        email = f"y-{uuid.uuid4().hex[:8]}@example.test"
        code = main(
            [
                "create-user",
                "--customer-id",
                customer_id,
                "--email",
                email,
                "--name",
                "Y",
                "--role",
                "member",
            ],
            db=db,
            environ={"KAAGAZ_ADMIN_PASSWORD": PASSWORD},
            out=io.StringIO(),
        )
        assert code == 2
        assert _lookup(db, email) is None


def test_taken_email_stops_with_code_2(db: Database) -> None:
    customer_id, _ = new_customer(db)
    email = f"z-{uuid.uuid4().hex[:8]}@example.test"
    args = ["create-user", "--customer-id", customer_id, "--email", email, "--name", "Z", "--role", "member"]
    env = {"KAAGAZ_ADMIN_PASSWORD": PASSWORD}
    assert main(args, db=db, environ=env, out=io.StringIO()) == 0
    assert main(args, db=db, environ=env, out=io.StringIO()) == 2


def test_password_is_not_a_flag() -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "create-customer",
                "--name",
                "X",
                "--admin-email",
                "a@b.c",
                "--admin-name",
                "X",
                "--password",
                "p",
            ]
        )


def test_seed_demo_makes_two_customers_with_three_roles_and_is_safe_to_rerun(db: Database) -> None:
    env = {"KAAGAZ_DEMO_PASSWORD": PASSWORD}
    first = io.StringIO()
    assert main(["seed-demo"], db=db, environ=env, out=first) == 0

    customers: set[str] = set()
    for _, domain in DEMO_CUSTOMERS:
        found = {role: _lookup(db, f"{role}@{domain}") for role in ("admin", "reviewer", "member")}
        assert all(row is not None for row in found.values())
        assert {role: row.role for role, row in found.items() if row} == {
            "admin": "admin",
            "reviewer": "reviewer",
            "member": "member",
        }
        assert len({row.customer_id for row in found.values() if row}) == 1
        customers.update(row.customer_id for row in found.values() if row)
    assert len(customers) == 2
    assert PASSWORD not in first.getvalue()

    again = io.StringIO()
    assert main(["seed-demo"], db=db, environ=env, out=again) == 0
    assert again.getvalue().count("skipped") == 2


def test_seed_demo_needs_the_password_variable(db: Database) -> None:
    assert main(["seed-demo"], db=db, environ={}, out=io.StringIO(), ask=_never_ask) == 2


# --- reset-admin-2fa: the way back in for the last admin (PRD 1 A6) -------------------------


def _enrolled_admin(db: Database) -> tuple[str, str, str]:
    """A customer whose first admin has a two-step code set up. Returns (customer, user, e-mail)."""
    customer_id, user_id = new_customer(db)
    with db.customer(customer_id) as conn:
        email = str(
            conn.execute(
                text(
                    "UPDATE users SET totp_secret_enc = 'v1:not-a-real-sealed-secret',"
                    " totp_enabled_at = now(), totp_last_step = 100"
                    " WHERE id = :u RETURNING email"
                ),
                {"u": user_id},
            ).scalar_one()
        )
    return customer_id, user_id, email


def _two_step(db: Database, customer_id: str, user_id: str) -> tuple[object, ...]:
    with db.customer(customer_id) as conn:
        row = conn.execute(
            text(
                "SELECT totp_secret_enc, totp_enabled_at, totp_last_step, session_version"
                " FROM users WHERE id = :u"
            ),
            {"u": user_id},
        ).one()
    return tuple(row)


def test_reset_admin_2fa_clears_the_code_and_ends_the_sessions(db: Database) -> None:
    customer_id, user_id, email = _enrolled_admin(db)
    out = io.StringIO()

    code = main(["reset-admin-2fa", "--email", email.upper()], db=db, environ={}, out=out, ask=_never_ask)

    assert code == 0
    assert _two_step(db, customer_id, user_id) == (None, None, None, 2)
    assert out.getvalue() == f"user {user_id}: 2FA is off; they can turn it on again in Admin, Settings\n"
    with db.customer(customer_id) as conn:
        rows = conn.execute(text("SELECT action, actor_user_id, target_id, details FROM audit_log")).all()
    assert [tuple(row) for row in rows] == [("user.reset_totp", None, user_id, {"by": "cli"})]


@pytest.mark.parametrize("role", ["member", "reviewer", None])
def test_reset_admin_2fa_needs_an_admins_email(db: Database, role: str | None) -> None:
    customer_id, _ = new_customer(db)
    email = f"nobody-{uuid.uuid4().hex[:8]}@example.test"
    if role is not None:
        main(
            ["create-user", "--customer-id", customer_id, "--email", email, "--name", "X", "--role", role],
            db=db,
            environ={"KAAGAZ_ADMIN_PASSWORD": PASSWORD},
            out=io.StringIO(),
        )
    before = _audit_count(db, customer_id)

    assert main(["reset-admin-2fa", "--email", email], db=db, environ={}, out=io.StringIO()) == 2
    assert _audit_count(db, customer_id) == before


def test_reset_admin_2fa_needs_the_email_flag() -> None:
    with pytest.raises(SystemExit):
        main(["reset-admin-2fa"])


def _audit_count(db: Database, customer_id: str) -> int:
    with db.customer(customer_id) as conn:
        return int(conn.execute(text("SELECT count(*) FROM audit_log")).scalar_one())
