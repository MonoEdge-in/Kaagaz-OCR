"""Migration 0088 (PRD 7 section 14, #600, 14 Nov 2026): page turns saved for everyone. Up, down
(the schema as at 0087) and up again on a fresh database; the CHECKs refuse a turn that is not a
quarter and a page before the first. Grants and row level security: test_isolation_schema; another
organisation's rows: test_isolation_page_turns_db; the organisation delete:
test_isolation_customer_purge."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, migrate, new_customer, violation
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0088 = _load("0088_page_rotations.py")
_OURS = "SELECT count(*) FROM pg_class WHERE relname = 'page_rotations'"
_TURN = (
    "INSERT INTO page_rotations (customer_id, document_id, page_no, turn, set_by) VALUES (:c, :d, :p, :t, :u)"
)


def test_the_revision_follows_0087() -> None:
    assert (M0088.revision, M0088.down_revision) == ("0088", "0087")


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0087")
    at_0087 = _state(db)
    migrate(url, "0088")
    at_0088 = _state(db)
    with db.system() as conn:
        assert conn.execute(text(_OURS)).scalar_one() == 1

    base._downgrade(url, "0087")
    assert _state(db) == at_0087
    with db.system() as conn:
        assert conn.execute(text(_OURS)).scalar_one() == 0

    migrate(url, "0088")
    assert _state(db) == at_0088


@pytest.mark.parametrize(
    ("page", "turn", "constraint"),
    [
        (1, 45, "page_rotations_turn"),
        (1, 0, "page_rotations_turn"),  # no row means not turned
        (1, 360, "page_rotations_turn"),
        (0, 90, "page_rotations_page_no"),
    ],
)
def test_only_a_quarter_turn_of_a_real_page_is_stored(
    db: Database, page: int, turn: int, constraint: str
) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user)
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        conn.execute(text(_TURN), {"c": customer, "d": document, "p": page, "t": turn, "u": user})
    assert violation(refused.value) == ("23514", constraint)


def test_a_turn_goes_with_its_version(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user)
        conn.execute(text(_TURN), {"c": customer, "d": document, "p": 2, "t": 90, "u": user})
        conn.execute(
            text("DELETE FROM documents WHERE customer_id = :c AND id = :d"), {"c": customer, "d": document}
        )
        left = conn.execute(
            text("SELECT count(*) FROM page_rotations WHERE customer_id = :c"), {"c": customer}
        )
        assert left.scalar_one() == 0
