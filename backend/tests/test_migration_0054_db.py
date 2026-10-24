"""A map value's label text (0054) on a fresh database: up, down and up again. One nullable
column with its comment and a length CHECK of 80; a value saved before it stays NULL, and the
app role writes it through its table grants."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer
from tests.test_migration_0037 import _load
from tests.test_migration_0049_db import _land_value

pytestmark = pytest.mark.db
fresh = base.fresh
M0054 = _load("0054_map_label_text.py")
_COMMENT = text(
    "SELECT col_description(a.attrelid, a.attnum) FROM pg_attribute a"
    " WHERE a.attrelid = 'extracted_values'::regclass AND a.attname = 'label_text'"
)
_LABEL = text("UPDATE extracted_values SET label_text = :label WHERE id = :v RETURNING label_text")


def test_0054_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, M0054.down_revision)
    before = base._schema(db)
    customer_id, _user = new_customer(db)
    _document_id, value_id = _land_value(db, customer_id)  # read before 0054

    migrate(url, "0054")
    at_0054 = base._schema(db)
    added = [row for row in at_0054["columns"] if row[:2] == ("extracted_values", "label_text")]
    assert added == [("extracted_values", "label_text", "text", False, None)]
    with db.system() as conn:
        assert "at most 80 characters" in conn.execute(_COMMENT).scalar_one()
    with db.customer(customer_id) as conn:
        kept = conn.execute(text("SELECT label_text FROM extracted_values WHERE id = :v"), {"v": value_id})
        assert kept.scalar_one() is None  # no row is written
        assert conn.execute(_LABEL, {"v": value_id, "label": "N" * 80}).scalar_one() == "N" * 80
    with pytest.raises(IntegrityError), db.customer(customer_id) as conn:
        conn.execute(_LABEL, {"v": value_id, "label": "N" * 81})

    base._downgrade(url, M0054.down_revision)
    assert base._schema(db) == before
    migrate(url, "0054")
    assert base._schema(db) == at_0054
