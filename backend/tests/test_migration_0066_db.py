"""Migration 0066 (#478, Tushar, 3 Nov 2026): the reading pause. The daily page limit starts NULL
on both AI settings tables (nothing is held on release day) and its CHECK refuses 0 and more than
a million; ``reading_days`` and ``documents.reading_held`` come with it; the operator's four AI
functions are 0065's with the limit, and the downgrade restores 0065 exactly; up, down, up."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, migrate, new_customer, violation
from tests.test_migration_0023_db import as_owner
from tests.test_migration_0065_db import _BODIES as _BODIES_0065

pytestmark = pytest.mark.db
fresh = base.fresh
PREVIOUS = "0065"
_BODIES = text(
    str(_BODIES_0065).replace(
        "'kaagaz_op_mistral_status')",
        "'kaagaz_op_mistral_status', 'kaagaz_op_reading_today', 'kaagaz_customer_ids_held')",
    )
)


def _bodies(db: Database) -> list[tuple[str, ...]]:
    with db.system() as conn:
        return [tuple(row) for row in conn.execute(_BODIES)]


def test_upgrade_starts_every_limit_empty_and_the_checks_refuse_a_bad_one(
    fresh: tuple[str, Database],
) -> None:
    url, db = fresh
    migrate(url, PREVIOUS)
    customer, _ = new_customer(db)
    as_owner(
        db,
        "INSERT INTO customer_ai_settings (customer_id, scan_thinking) VALUES (:c, 'low')",
        {"c": customer},
    )
    migrate(url, "0066")
    assert as_owner(db, "SELECT DISTINCT daily_page_limit FROM customer_ai_settings") == [(None,)]
    assert as_owner(db, "SELECT daily_page_limit FROM platform_ai_settings") == [(None,)]
    assert as_owner(db, "SELECT count(*) FROM documents WHERE reading_held IS NOT NULL") == [(0,)]
    for bad in (0, 1_000_001):
        with pytest.raises(DBAPIError) as refused:
            as_owner(db, "UPDATE platform_ai_settings SET daily_page_limit = :n", {"n": bad})
        assert violation(refused.value)[0] == "23514"
    with db.customer(customer) as conn:
        insert_document(conn, customer, status="received")
    with pytest.raises(DBAPIError) as refused:
        as_owner(db, "UPDATE documents SET reading_held = 'tired' WHERE customer_id = :c", {"c": customer})
    assert violation(refused.value)[0] == "23514"
    with pytest.raises(DBAPIError):
        as_owner(
            db,
            "INSERT INTO reading_days (customer_id, day, pages) VALUES (:c, current_date, -1)",
            {"c": customer},
        )


def test_down_up_restores_0065_exactly(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, PREVIOUS)
    at_0065, bodies_0065 = base._schema(db), _bodies(db)
    migrate(url, "0066")
    at_0066, bodies_0066 = base._schema(db), _bodies(db)
    assert {"kaagaz_op_reading_today", "kaagaz_customer_ids_held"} <= {row[0] for row in bodies_0066}
    for name, body, comment, acl in bodies_0066:  # the house pattern: definer, empty path, one role
        assert "SECURITY DEFINER" in body and "SET search_path TO ''" in body
        role, other = (
            ("kaagaz_app", "kaagaz_operator") if "_op_" not in name else ("kaagaz_operator", "kaagaz_app")
        )
        assert comment and f"{role}=X" in acl and other not in acl, name
    base._downgrade(url, PREVIOUS)
    assert base._schema(db) == at_0065
    assert _bodies(db) == bodies_0065  # restored exactly, comments and grants too
    migrate(url, "0066")
    assert base._schema(db) == at_0066
    assert _bodies(db) == bodies_0066
