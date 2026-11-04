"""Migration 0072 (#507, Google sign-in by organisation and role) on PostgreSQL: up, down (0071's
schema exactly, a connection kept) and up again with rows, defaults that change nothing on
release day, and the CHECKs on the org admin's rules. Rights and customer separation:
test_isolation_google_signin."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import identities
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer
from tests.test_migration_0023_db import as_owner
from tests.test_migration_0037 import _load
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db
fresh = base.fresh
M0072 = _load("0072_google_signin.py")
PREVIOUS = "0071"
NEW_COLUMNS = {
    ("platform_ai_settings", "google_signin", "boolean", True, "true"),
    ("customers", "google_signin", "boolean", False, None),
    ("customers", "google_roles", "text[]", True, "'{admin,reviewer,member}'::text[]"),
    ("customers", "google_only_roles", "text[]", True, "'{}'::text[]"),
    ("customers", "google_domains", "text[]", True, "'{}'::text[]"),
    ("user_identities", "hosted_domain", "text", False, None),
}
_RULES = "SELECT google_signin, google_roles, google_only_roles, google_domains FROM customers WHERE id = :c"


def _connected(db: Database, customer: str, user: str) -> None:
    with db.customer(customer) as conn:
        identities.add(
            conn,
            customer,
            user,
            provider="google",
            issuer="https://accounts.google.com",
            subject="made-up-sub-0072",
            email="ramesh@sharmatraders.com",
            approved=True,
            approved_by=None,
            now=conn.execute(text("SELECT now()")).scalar_one(),
        )


def test_0072_up_down_up_with_rows_and_defaults_that_change_nothing(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    assert M0072.down_revision == PREVIOUS
    migrate(url, PREVIOUS)
    before = base._schema(db)
    customer, user = new_customer(db)  # an organisation and a connection from before 0072
    _connected(db, customer, user)

    migrate(url, M0072.revision)
    after = base._schema(db)
    assert set(after["columns"]) - set(before["columns"]) == NEW_COLUMNS
    assert base._names(after, "functions") - base._names(before, "functions") == {
        "kaagaz_op_google_signin",
        "kaagaz_op_set_google_signin",
    }
    # Release day: Google follows the platform (on), every role may use it, none only it, any domain.
    assert as_owner(db, _RULES, {"c": customer}) == [(None, ["admin", "reviewer", "member"], [], [])]
    assert as_owner(db, "SELECT google_signin FROM platform_ai_settings", {}) == [(True,)]
    assert as_owner(db, "SELECT hosted_domain, status FROM user_identities", {}) == [(None, "approved")]

    _owner(
        db,
        "UPDATE customers SET google_signin = false, google_roles = '{admin}', google_only_roles = '{admin}',"
        " google_domains = '{sharmatraders.com}' WHERE id = :c",
        {"c": customer},
    )
    _owner(db, "UPDATE platform_ai_settings SET google_signin = false", {})
    _owner(db, "UPDATE user_identities SET hosted_domain = 'sharmatraders.com'", {})
    base._downgrade(url, PREVIOUS)
    assert base._schema(db) == before
    assert as_owner(db, "SELECT status FROM user_identities", {}) == [("approved",)]  # the connection stays
    migrate(url, M0072.revision)
    assert base._schema(db) == after
    assert as_owner(db, _RULES, {"c": customer}) == [(None, ["admin", "reviewer", "member"], [], [])]


@pytest.mark.parametrize(
    "change",
    [
        "google_roles = '{admin,owner}'",  # not a role
        "google_only_roles = '{member}', google_roles = '{admin}'",  # only, but not allowed
        "google_only_roles = '{owner}'",
        "google_domains = '{a.in,b.in,c.in,d.in,e.in,f.in,g.in,h.in,i.in,j.in,k.in}'",  # 11
    ],
)
def test_the_rules_checks_hold(db: Database, change: str) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(DBAPIError):
        _owner(db, f"UPDATE customers SET {change} WHERE id = :c", {"c": customer})  # noqa: S608 - fixed cases


def test_the_rules_take_every_good_shape(db: Database) -> None:
    customer, _ = new_customer(db)
    ten = "{" + ",".join(f"d{n}.in" for n in range(10)) + "}"
    _owner(
        db,
        "UPDATE customers SET google_roles = '{}', google_only_roles = '{}', google_domains = :d"
        " WHERE id = :c",
        {"c": customer, "d": ten},
    )
    _owner(
        db,
        "UPDATE customers SET google_roles = '{member,reviewer,admin}',"
        " google_only_roles = '{reviewer,admin}' WHERE id = :c",
        {"c": customer},
    )
