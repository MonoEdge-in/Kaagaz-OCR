"""The safety plan's migrations as one chain (product QA, 4 Nov 2026): head down to 0065 and up
again, in one alembic run each, with a row in every table and column added after 0065 (the
reading pause's counter, a held document, the two-step rule, the platform's daily limit, a kept
audit row). The schema and every kaagaz function's body are the same each time round, and a held
document stays received on the way down. Every later migration joins the chain on its own.

Changed on purpose (#484, 4 Nov 2026): production's 0061c (folder upload) is a branch off 0061b
that 0071 merges, so a downgrade to 0065 leaves it, its tables and their rows: 0065's state is
taken with 0061c applied, and a folder upload made at head is still there at 0065."""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, insert_folder, migrate, new_customer
from tests.folder_upload_support import new_folder_upload
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
    migrate(url, "0061c")
    at_0065 = _state(db)
    migrate(url, "head")
    at_head = _state(db)
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "A")
    upload = new_folder_upload(db, customer, user, folder_ids=[folder])
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
    as_owner(db, "UPDATE platform_ai_settings SET daily_page_limit = 10, google_signin = false", {})
    as_owner(  # 0074 (#507): the super admin's switch and the org admin's Google rules
        db,
        "UPDATE customers SET google_signin = true, google_only_roles = '{admin}',"
        " google_domains = '{sharma.in}' WHERE id = :c",
        {"c": customer},
    )
    as_owner(
        db,
        "INSERT INTO audit_log_kept (id, customer_id, action, target_type, details, created_at, deleted_at)"
        " VALUES (987654321, gen_random_uuid(), 'x', 'customer', '{}'::jsonb, now(), now())",
        {},
    )

    base._downgrade(url, "0065")
    assert _state(db) == at_0065
    assert as_owner(db, "SELECT status FROM documents WHERE id = :d", {"d": held}) == [("received",)]
    assert as_owner(db, "SELECT count(*) FROM folder_uploads WHERE id = :u", {"u": upload}) == [(1,)]
    migrate(url, "head")
    assert _state(db) == at_head
