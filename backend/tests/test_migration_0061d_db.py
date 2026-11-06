"""Storage limit per organisation (0061d, #516) on PostgreSQL: up, down (0061c's schema exactly)
and up again with the other rows kept, the limit inside 1 to 10,000 GB, and the app's grant: two
columns, never a write. Customer separation: test_isolation_storage_limits."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import APP_ROLE, insert_document, migrate, new_customer
from tests.test_migration_0004_db import _current, _head
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db
fresh = base.fresh
FUNCTIONS = {"kaagaz_op_storage_limit", "kaagaz_op_set_storage_limit"}
_ROW = "INSERT INTO org_storage_limits (customer_id, storage_gb) VALUES (:c, :g)"


def test_0061d_up_down_up_keeps_every_other_row(fresh: tuple[str, Database]) -> None:
    """Changed on purpose (Testing-Env port, #516): down to 0061c leaves 0061c and 0070 (0071, the
    merge of the two, and all after it go), so the state before is 0061c's and 0070's."""
    url, db = fresh
    migrate(url, "0061c")
    migrate(url, "0070")
    before = base._schema(db)
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, size_bytes=1234)
    _owner(db, "INSERT INTO org_upload_limits (customer_id, max_gb) VALUES (:c, 2)", {"c": customer})
    migrate(url, "head")
    assert _current(url) == _head()
    after = base._schema(db)
    assert "org_storage_limits" in base._names(after, "relations")
    assert base._names(after, "functions") >= FUNCTIONS
    _owner(db, _ROW, {"c": customer, "g": 7})
    _owner(db, "UPDATE platform_ai_settings SET storage_gb = 60", {})
    base._downgrade(url, "0061c")
    assert base._schema(db) == before
    migrate(url, "head")
    assert base._schema(db) == after
    kept = _owner(
        db,
        "SELECT (SELECT size_bytes FROM documents WHERE id = :d),"
        " (SELECT max_gb FROM org_upload_limits WHERE customer_id = :c),"
        " (SELECT count(*) FROM org_storage_limits), (SELECT storage_gb FROM platform_ai_settings)",
        {"d": document, "c": customer},
    )
    assert [tuple(row) for row in kept] == [(1234, 2, 0, None)]  # the limits went with the downgrade


def test_the_limit_stays_between_1_and_10000_gb(db: Database) -> None:
    customer, _ = new_customer(db)
    for wrong in (0, 10_001, -1, None):
        with pytest.raises(DBAPIError):
            _owner(db, _ROW, {"c": customer, "g": wrong})
    for wrong in (0, 10_001):
        with pytest.raises(DBAPIError):
            _owner(db, "UPDATE platform_ai_settings SET storage_gb = :g", {"g": wrong})
    _owner(db, _ROW, {"c": customer, "g": 10_000})
    other, _ = new_customer(db)
    _owner(db, _ROW, {"c": other, "g": 1})
    with pytest.raises(DBAPIError):  # one row an organisation
        _owner(db, _ROW, {"c": other, "g": 2})


def test_the_app_reads_two_columns_of_the_limits_and_writes_none(db: Database) -> None:
    rights = _owner(
        db,
        "SELECT a.attname::text, has_column_privilege(:r, a.attrelid, a.attname, 'SELECT'),"
        " has_column_privilege(:r, a.attrelid, a.attname, 'INSERT'),"
        " has_column_privilege(:r, a.attrelid, a.attname, 'UPDATE')"
        " FROM pg_attribute a WHERE a.attrelid = 'org_storage_limits'::regclass AND a.attnum > 0"
        " AND NOT a.attisdropped ORDER BY a.attnum",
        {"r": APP_ROLE},
    )
    assert [tuple(row) for row in rights] == [
        ("customer_id", True, False, False),
        ("storage_gb", True, False, False),
        ("updated_by", False, False, False),
        ("updated_at", False, False, False),
    ]
    table = _owner(
        db,
        "SELECT has_table_privilege(:r, 'org_storage_limits', 'DELETE'),"
        " has_column_privilege(:r, 'platform_ai_settings', 'storage_gb', 'SELECT'),"
        " has_column_privilege(:r, 'platform_ai_settings', 'storage_gb', 'UPDATE')",
        {"r": APP_ROLE},
    )
    assert tuple(table[0]) == (False, True, False)  # the platform's: read, as every column there


_DESCRIBED = text(
    "SELECT a.attrelid::regclass::text || '.' || a.attname, col_description(a.attrelid, a.attnum)"
    " FROM pg_attribute a WHERE a.attnum > 0 AND NOT a.attisdropped"
    " AND (a.attrelid = 'org_storage_limits'::regclass"
    " OR (a.attrelid = 'platform_ai_settings'::regclass AND a.attname = 'storage_gb'))"
    " UNION ALL SELECT 'org_storage_limits', obj_description('org_storage_limits'::regclass, 'pg_class')"
    " UNION ALL SELECT p.proname::text, obj_description(p.oid, 'pg_proc') FROM pg_proc p"
    " WHERE p.proname = ANY(:f)"
)


def test_every_new_table_column_and_function_has_a_comment(db: Database) -> None:
    with db.system() as conn:
        described = {
            str(name): comment for name, comment in conn.execute(_DESCRIBED, {"f": sorted(FUNCTIONS)})
        }
    assert len(described) == 8  # the table, its four columns, the platform's column, two functions
    assert [name for name, comment in described.items() if not comment] == []
