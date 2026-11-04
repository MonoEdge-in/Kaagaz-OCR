"""The safety plan's migrations as one chain (product QA, 4 Nov 2026): head down to 0065 and up
again, in one alembic run each, with a row in every table and column added after 0065 (the
reading pause's counter, a held document, the two-step rule, the platform's daily limit, a kept
audit row). The schema and every kaagaz function's body are the same each time round, and a held
document stays received on the way down. Every later migration joins the chain on its own."""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, migrate, new_customer
from tests.test_migration_0023_db import as_owner

pytestmark = pytest.mark.db
fresh = base.fresh
_BODIES = text(
    "SELECT proname, pg_get_function_identity_arguments(oid), md5(prosrc) FROM pg_proc"
    " WHERE pronamespace = 'public'::regnamespace AND proname LIKE 'kaagaz%' ORDER BY 1, 2"
)


def _state(db: Database) -> tuple[dict[str, list[tuple[Any, ...]]], list[tuple[Any, ...]]]:
    with db.system() as conn:
        bodies = [tuple(row) for row in conn.execute(_BODIES)]
    return base._schema(db), bodies


def test_head_down_to_0065_and_up_again_with_rows(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0065")
    at_0065 = _state(db)
    migrate(url, "head")
    at_head = _state(db)
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        held = insert_document(conn, customer, status="received")
        conn.execute(
            text(
                "UPDATE documents SET reading_held = 'budget', reading_admitted = current_date WHERE id = :d"
            ),
            {"d": held},
        )
        conn.execute(
            text(
                "INSERT INTO reading_days (customer_id, day, pages, cost_usd)"
                " VALUES (:c, current_date, 3, 0.5)"
            ),
            {"c": customer},
        )
    as_owner(db, "UPDATE customers SET admin_two_step_required = true WHERE id = :c", {"c": customer})
    as_owner(db, "UPDATE platform_ai_settings SET daily_page_limit = 10", {})
    as_owner(
        db,
        "INSERT INTO audit_log_kept (id, customer_id, action, target_type, details, created_at, deleted_at)"
        " VALUES (987654321, gen_random_uuid(), 'x', 'customer', '{}'::jsonb, now(), now())",
        {},
    )

    base._downgrade(url, "0065")
    assert _state(db) == at_0065
    assert as_owner(db, "SELECT status FROM documents WHERE id = :d", {"d": held}) == [("received",)]
    migrate(url, "head")
    assert _state(db) == at_head
