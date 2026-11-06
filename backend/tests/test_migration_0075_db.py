"""Storage limit per organisation (0075, #516) on PostgreSQL: the three ways to it end on one
schema, with the same grants, comments and function bodies, and down across 0075 and up again
keeps the limits. Customer separation: test_isolation_storage_limits; the limited logins:
test_isolation_logins.

0075 merges production's 0061d (its file byte for byte) with 0074 and adds nothing. A fresh
database (0074, then 0061d), the test server (at 0074 as #509 left it, with rows, then 0061d) and
production (0061d with its limits, then 0062 ... 0074) all reach 0075 with the same state."""

from typing import Any

import pytest

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import APP_ROLE, insert_document, migrate, new_customer
from tests.test_migration_0004_db import _current, _head
from tests.test_migration_0071_db import _as_509_left_it, _empty_again, _heads, _state
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db
fresh = base.fresh
FUNCTIONS = {"kaagaz_op_storage_limit", "kaagaz_op_set_storage_limit"}
State = dict[str, list[tuple[Any, ...]]]
# One organisation's document bytes, its own limit and the platform's.
_ROWS = (
    "SELECT (SELECT sum(size_bytes) FROM documents WHERE customer_id = :c),"
    " (SELECT storage_gb FROM org_storage_limits WHERE customer_id = :c),"
    " (SELECT storage_gb FROM platform_ai_settings)"
)
# What the app may do with the new objects: read the two columns, nothing else.
_RIGHTS = (
    "SELECT has_column_privilege(:r, 'org_storage_limits', 'customer_id', 'SELECT'),"
    " has_column_privilege(:r, 'org_storage_limits', 'storage_gb', 'SELECT'),"
    " has_column_privilege(:r, 'org_storage_limits', 'updated_by', 'SELECT'),"
    " has_table_privilege(:r, 'org_storage_limits', 'INSERT, UPDATE, DELETE'),"
    " has_column_privilege(:r, 'platform_ai_settings', 'storage_gb', 'SELECT'),"
    " has_function_privilege(:r, 'kaagaz_op_storage_limit(uuid, uuid)', 'EXECUTE'),"
    " has_function_privilege('kaagaz_operator', 'kaagaz_op_storage_limit(uuid, uuid)', 'EXECUTE'),"
    " has_function_privilege('kaagaz_operator', 'kaagaz_op_set_storage_limit(uuid, uuid, integer, bigint)',"
    " 'EXECUTE')"
)


def _limited_with_rows(db: Database) -> tuple[str, int]:
    """An organisation with a document of 1234 bytes and its own limit of 7 GB, and the platform's
    limit 60 GB, written with 0061d's columns only."""
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        insert_document(conn, customer, size_bytes=1234)
    _owner(db, "INSERT INTO org_storage_limits (customer_id, storage_gb) VALUES (:c, 7)", {"c": customer})
    _owner(db, "UPDATE platform_ai_settings SET storage_gb = 60", {})
    return customer, 1234


def _rows(db: Database, customer: str) -> tuple[Any, ...]:
    return tuple(_owner(db, _ROWS, {"c": customer})[0])


def _normal(url: str, db: Database) -> tuple[State, State, State]:
    """The normal path's states (a fresh database): at 0074, at 0074 and 0061d, and at head;
    then the database empty again."""
    migrate(url, "0074")
    at_0074 = _state(db)
    migrate(url, "0061d")
    parents = _state(db)
    migrate(url, "head")
    at_head = _state(db)
    _empty_again(url, db)
    return at_0074, parents, at_head


def test_0075_is_a_merge_alone_and_down_and_up_again(fresh: tuple[str, Database]) -> None:
    """The normal path: 0074, production's 0061d, then 0075 (the merge, nothing more). Down to
    0074 (0061d stays) and to 0061c (0061d goes, with 0071 ... 0075), and up again."""
    url, db = fresh
    migrate(url, "0074")
    at_0074 = _state(db)
    assert "org_storage_limits" not in base._names(at_0074, "relations")
    migrate(url, "0061d")
    assert _heads(url) == ["0061d", "0074"]
    parents = _state(db)
    assert base._names(parents, "functions") >= FUNCTIONS
    migrate(url, "0075")
    assert _current(url) == "0075"  # Changed on purpose (#502): 0076 is the head now
    after = _state(db)
    assert after == parents  # a merge: nothing added
    customer, size = _limited_with_rows(db)

    base._downgrade(url, "0074")
    assert _heads(url) == ["0061d", "0074"]
    assert _state(db) == parents
    assert _rows(db, customer) == (size, 7, 60)  # 0061d's table and rows stay
    migrate(url, "0075")
    assert _state(db) == after

    base._downgrade(url, "0061c")
    assert _heads(url) == ["0061c", "0070"]
    assert "org_storage_limits" not in base._names(_state(db), "relations")
    migrate(url, "0075")
    assert _current(url) == "0075"
    assert _state(db) == after
    assert _rows(db, customer) == (size, None, None)  # the limits went with 0061d's downgrade


def test_the_test_server_at_0074_migrates_to_the_same_state_and_keeps_its_rows(
    fresh: tuple[str, Database],
) -> None:
    """The test server: 0071 as #509 merged it (0061c's statements straight after 0070), then
    0072 ... 0074, with documents in it. Head runs 0061d, then 0075, and ends on the normal
    path's state: the same schema, grants, comments and function bodies. Down and up keeps it."""
    url, db = fresh
    normal_0074, normal_parents, normal = _normal(url, db)

    _as_509_left_it(url, db)
    migrate(url, "0074")
    assert _heads(url) == ["0074"]
    assert _state(db) == normal_0074
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        insert_document(conn, customer, size_bytes=99)
    migrate(url, "head")
    assert _current(url) == _head()
    assert _state(db) == normal
    assert _rows(db, customer) == (99, None, None)

    base._downgrade(url, "0074")
    assert _state(db) == normal_parents
    migrate(url, "head")
    assert _state(db) == normal


def test_production_at_0061d_migrates_to_the_same_state_and_keeps_its_limits(
    fresh: tuple[str, Database],
) -> None:
    """Production ran 0061d and has limits set. The release runs 0062 ... 0074 with them there,
    then the merge 0075: the normal path's state, every limit kept. Down and up again too."""
    url, db = fresh
    _, normal_parents, normal = _normal(url, db)

    migrate(url, "0061d")
    assert _heads(url) == ["0061d"]
    customer, size = _limited_with_rows(db)
    migrate(url, "0074")
    assert _heads(url) == ["0061d", "0074"]
    assert _state(db) == normal_parents
    migrate(url, "head")
    assert _current(url) == _head()
    assert _state(db) == normal
    assert _rows(db, customer) == (size, 7, 60)

    base._downgrade(url, "0074")
    assert _state(db) == normal_parents
    assert _rows(db, customer) == (size, 7, 60)
    migrate(url, "head")
    assert _state(db) == normal


def test_the_app_reads_two_columns_and_only_the_operator_runs_the_functions(db: Database) -> None:
    """At head, whatever the path (the state is the same): the app's mode reads two columns of
    the limits and the platform's column, writes none and runs neither function; the operator's
    mode runs both. The limited logins reach them only through these two modes."""
    rights = _owner(db, _RIGHTS, {"r": APP_ROLE})
    assert tuple(rights[0]) == (True, True, False, False, True, False, True, True)
