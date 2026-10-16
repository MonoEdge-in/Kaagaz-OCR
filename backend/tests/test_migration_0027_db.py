"""Migration 0027 (party folders, backlog 28b, final polish 16 Oct 2026): the folders' key
CHECK allows ``party:<GSTIN>`` too and still refuses anything else; the column's comment
says so. Up, down (0026's schema exactly; a party key is cleared, its folder stays) and
up again. The filing rules: test_autofile_party_db.py.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_folder, migrate, new_customer, violation
from tests.test_autofile_party import MEHTA
from tests.test_migration_0004_db import _current, _head
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
_KEY = "UPDATE folders SET autofile_key = :k WHERE customer_id = :c AND id = :f"
_COMMENT = (
    "SELECT col_description(a.attrelid, a.attnum) FROM pg_attribute a"
    " WHERE a.attrelid = 'public.folders'::regclass AND a.attname = 'autofile_key'"
)


def test_0027_up_down_up_with_a_party_folder(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0026")
    at_0026 = m0002._schema(database)

    migrate(url, "head")
    assert _current(url) == _head()
    at_head = m0002._schema(database)
    customer, admin = new_customer(database)
    with database.customer(customer) as conn:
        folder = insert_folder(conn, customer, admin, "Mehta Textiles")
        conn.execute(text(_KEY), {"k": f"party:{MEHTA}", "c": customer, "f": folder})

    m0002._downgrade(url, "0026")
    assert m0002._schema(database) == at_0026
    with database.customer(customer) as conn:
        kept = conn.execute(
            text("SELECT name, autofile_key FROM folders WHERE customer_id = :c AND id = :f"),
            {"c": customer, "f": folder},
        ).one()
    assert tuple(kept) == ("Mehta Textiles", None)  # the folder stays, as a person's folder

    migrate(url, "head")
    assert m0002._schema(database) == at_head


@pytest.mark.parametrize("key", [f"party:{MEHTA}", "type:invoice"])
def test_the_check_allows_a_party_key_and_a_type_key(db: Database, key: str) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, admin, "Mine")
        conn.execute(text(_KEY), {"k": key, "c": customer, "f": folder})


@pytest.mark.parametrize(
    "key", ["party:mehta textiles", f"party:{MEHTA.lower()}", f"party:{MEHTA}0", "Invoices"]
)
def test_the_check_refuses_any_other_key(db: Database, key: str) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, admin, "Mine")
    with pytest.raises(DBAPIError) as refused, db.engine.begin() as conn:  # as the owner: no grant stops it
        conn.execute(text(_KEY), {"k": key, "c": customer, "f": folder})
    assert violation(refused.value) == ("23514", "folders_autofile_key_check")


def test_the_column_comment_names_the_party_key(db: Database) -> None:
    [(comment,)] = _owner(db, _COMMENT, {})
    assert "party" in comment
