"""Land facts' schema (0049, #245) on a fresh database: up, down and up again. The downgrade
drops only the derived ``value_keys``: it never refuses because keys or land values exist,
it keeps the land values, and it restores 0034's ``kaagaz_op_daily`` body and 0007's
comments exactly."""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, migrate, new_customer

pytestmark = pytest.mark.db
fresh = base.fresh

_DAILY = "pg_get_functiondef('public.kaagaz_op_daily(uuid, date, date, text)'::regprocedure)"
_COMMENTS = (
    "SELECT a.attname::text, col_description(a.attrelid, a.attnum) FROM pg_attribute a"
    " WHERE a.attrelid = 'extracted_values'::regclass AND a.attname IN ('record_key', 'field')"
)


def _state(db: Database) -> dict[str, Any]:
    """The schema, plus what ``base._schema`` leaves out: the daily function's body and comments."""
    with db.system() as conn:
        daily = conn.execute(text(f"SELECT {_DAILY}")).scalar_one()
        comments = sorted(tuple(row) for row in conn.execute(text(_COMMENTS)))
    return {**base._schema(db), "daily": daily, "comments": comments}


def _land_value(db: Database, customer_id: str) -> tuple[str, str]:
    """One document with one land owner value, as a customer. (document id, value id)"""
    with db.customer(customer_id) as conn:
        document_id = insert_document(conn, customer_id)
        value_id = conn.execute(
            text(
                "INSERT INTO extracted_values (customer_id, document_id, record_key, field, value_text,"
                " value_raw, page_no, region, reader, label, label_reason) VALUES (:c, :d, 'land:1:party:1',"
                " 'land_owner_name', 'Krishna Sohoni', 'Krishna Sohoni', 1,"
                ' \'{"page": 1, "x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.2}\', \'gemini\', \'needs_review\','
                " 'model_chosen') RETURNING id"
            ),
            {"c": customer_id, "d": document_id},
        ).scalar_one()
    return document_id, str(value_id)


def test_0049_up_down_up_keeps_land_values_and_restores_0034_and_0007(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0048")
    at_0048 = _state(db)
    migrate(url, "0049")
    at_0049 = _state(db)
    assert "value_keys" in base._names(at_0049, "relations")
    assert "'card', 'invoice', 'land', 'map'" in at_0049["daily"]
    assert at_0049["daily"].replace("'land', ", "", 1) == at_0048["daily"]
    comments = dict(at_0049["comments"])
    assert "land:{p}:party:{o}" in comments["record_key"] and "land_share" in comments["field"]

    customer_id, _user = new_customer(db)
    document_id, value_id = _land_value(db, customer_id)
    with db.customer(customer_id) as conn:
        conn.execute(
            text(
                "INSERT INTO value_keys (customer_id, value_id, document_id, kind, latin, sound)"
                " VALUES (:c, :v, :d, 'person', 'krishna sohoni', 'krisn sohoni')"
            ),
            {"c": customer_id, "v": value_id, "d": document_id},
        )

    base._downgrade(url, "0048")  # keys and land values exist: nothing refuses
    assert _state(db) == at_0048
    with db.customer(customer_id) as conn:
        kept = conn.execute(text("SELECT field FROM extracted_values WHERE id = :v"), {"v": value_id}).all()
    assert [row[0] for row in kept] == ["land_owner_name"]

    migrate(url, "0049")
    assert _state(db) == at_0049
    with db.customer(customer_id) as conn:  # derived: empty until land-facts --keys-only writes it
        assert conn.execute(text("SELECT count(*) FROM value_keys")).scalar_one() == 0
