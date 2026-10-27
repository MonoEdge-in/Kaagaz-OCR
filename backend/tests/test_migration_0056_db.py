"""The regions of a page asked to be read again (0056, #385) on a fresh database: up, down and
up again. One nullable jsonb column on ``pages`` with its comment and a CHECK of 1 to 10 boxes
in an array; a page saved before it stays NULL, and the app role writes it through its table
grants, its own customer's rows only (row level security)."""

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer
from tests.test_migration_0037 import _load
from tests.test_pipeline_store_db import content, new_document

pytestmark = pytest.mark.db
fresh = base.fresh
M0056 = _load("0056_read_again_regions.py")
_COMMENT = text(
    "SELECT col_description(a.attrelid, a.attnum) FROM pg_attribute a"
    " WHERE a.attrelid = 'pages'::regclass AND a.attname = 'read_again_regions'"
)
_SET = text(
    "UPDATE pages SET read_again_regions = CAST(:r AS jsonb) WHERE document_id = :d AND page_no = 1"
    " RETURNING read_again_regions"
)
BOX = [0.1, 0.2, 0.3, 0.4]


def test_0056_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, M0056.down_revision)
    before = base._schema(db)
    customer_id, _user = new_customer(db)
    other_id, _other = new_customer(db, name="Other customer")
    document_id = new_document(db, customer_id)
    PgPipelineStore(db).save(content(customer_id, document_id))  # pages saved before 0056

    migrate(url, "0056")
    at_0056 = base._schema(db)
    added = [row for row in at_0056["columns"] if row[:2] == ("pages", "read_again_regions")]
    assert added == [("pages", "read_again_regions", "jsonb", False, None)]
    with db.system() as conn:
        assert "page fractions" in conn.execute(_COMMENT).scalar_one()
    with db.customer(customer_id) as conn:
        kept = conn.execute(
            text("SELECT read_again_regions FROM pages WHERE document_id = :d"), {"d": document_id}
        )
        assert {row[0] for row in kept} == {None}  # no row is written
        assert conn.execute(_SET, {"d": document_id, "r": json.dumps([BOX] * 10)}).scalar_one() == [BOX] * 10
    for refused in ([BOX] * 11, [], {"box": BOX}):
        with pytest.raises(IntegrityError), db.customer(customer_id) as conn:
            conn.execute(_SET, {"d": document_id, "r": json.dumps(refused)})
    with db.customer(other_id) as conn:  # row level security: another customer's page is not there
        assert conn.execute(_SET, {"d": document_id, "r": json.dumps([BOX])}).first() is None

    base._downgrade(url, M0056.down_revision)
    assert base._schema(db) == before
    migrate(url, "0056")
    assert base._schema(db) == at_0056
