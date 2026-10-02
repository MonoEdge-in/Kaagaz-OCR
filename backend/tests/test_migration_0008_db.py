"""Migration 0008 (my words): the two tables, their policies, CHECKs and comments.

The upgrade comments each new table and column and no index; the downgrade
gives back 0007's schema exactly, and upgrading again the same schema. The
CHECKs and the UNIQUE on the folds refuse every bad row. The fresh database and
the schema snapshot come from test_migration_0002_db (a plain import).
"""

import json
from typing import Any

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer, violation

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
NEW_TABLES = {"user_words", "user_word_spots"}
_WORD = text(
    "INSERT INTO user_words (customer_id, user_id, word, word_fold, meaning, meaning_fold, as_read, source)"
    " VALUES (:c, :u, :w, lower(:w), :m, lower(:m), :as_read, :source) RETURNING id"
)
_SPOT = text(
    "INSERT INTO user_word_spots (customer_id, user_id, word_id, document_id, page_no, region)"
    " VALUES (:c, :u, :word, :d, :p, CAST(:r AS jsonb))"
)
# Every comment on a new table, its columns or anything else of its (an index).
_NEW_COMMENTS = text(
    "SELECT c.relkind::text FROM pg_description d JOIN pg_class c ON c.oid = d.objoid"
    " WHERE d.classoid = 'pg_class'::regclass AND c.relnamespace = 'public'::regnamespace"
    " AND c.relname LIKE 'user_word%'"
)
BOX = json.dumps({"kind": "box", "page": 2, "box": [0.1, 0.2, 0.3, 0.25]})
GOOD = {"w": "malak", "m": "owner", "as_read": None, "source": "manual", "p": 2, "r": BOX}


def _rows(conn: Connection, customer: str, user: str, **changes: Any) -> None:
    """A word, and a spot of it on a new document, with ``changes`` to either."""
    values = {"c": customer, "u": user, **GOOD, **changes}
    values["word"] = str(conn.execute(_WORD, values).scalar_one())
    values["d"] = insert_document(conn, customer)
    conn.execute(_SPOT, values)


def test_upgrade_downgrade_and_upgrade_again(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0007")
    at_0007 = m0002._schema(database)

    migrate(url, "head")
    at_head = m0002._schema(database)
    policies = {(str(row[0]), str(row[1])) for row in at_head["policies"]}
    assert {(table, name) for table in NEW_TABLES for name in ("customer_only", "own_user_only")} <= policies
    assert ("user_words", "user_words_touch") in {(str(row[0]), str(row[1])) for row in at_head["triggers"]}
    with database.system() as conn:
        kinds = [str(row[0]) for row in conn.execute(_NEW_COMMENTS)]
    columns = [row for row in at_head["columns"] if row[0] in NEW_TABLES]
    assert set(kinds) == {"r"}  # tables and columns only, never an index
    assert len(kinds) == len(columns) + len(NEW_TABLES) == 21

    customer, user = new_customer(database)
    with database.user(customer, user) as conn:  # rows, so the downgrade has something to drop
        _rows(conn, customer, user)
    m0002._downgrade(url, "0007")
    assert m0002._schema(database) == at_0007
    migrate(url, "head")
    assert m0002._schema(database) == at_head


BAD_ROWS = {
    "word_over_40": ({"w": "x" * 41}, "user_words_word_check"),
    "word_empty": ({"w": ""}, "user_words_word_check"),
    "meaning_over_80": ({"m": "x" * 81}, "user_words_meaning_check"),
    "as_read_over_200": ({"as_read": "x" * 201}, "user_words_as_read_check"),
    "another_source": ({"source": "chat"}, "user_words_source_check"),
    "page_alone": ({"r": None}, "user_word_spots_page_with_region"),
    "region_alone": ({"p": None}, "user_word_spots_page_with_region"),
    "another_page": ({"p": 3}, "user_word_spots_region_page"),
    "page_0": ({"p": 0, "r": BOX.replace('"page": 2', '"page": 0')}, "user_word_spots_page_no_check"),
    "not_an_object": ({"r": f"[{BOX}]"}, "user_word_spots_region_check"),
    "over_256_bytes": (
        {"r": BOX.replace("}", ', "note": "' + "x" * 300 + '"}')},
        "user_word_spots_region_check",
    ),
    "same_folds": ({"w": "GOOD", "m": "Owner"}, "user_words_same_meaning"),
}


@pytest.mark.parametrize(("changes", "constraint"), list(BAD_ROWS.values()), ids=list(BAD_ROWS))
def test_the_checks_refuse_a_bad_row(db: Database, changes: dict[str, Any], constraint: str) -> None:
    customer, user = new_customer(db)
    with db.user(customer, user) as conn:
        _rows(conn, customer, user, w="good")  # a good row first, and a whole-document link
        _rows(conn, customer, user, w="also good", p=None, r=None)
    with pytest.raises(IntegrityError) as refused, db.user(customer, user) as conn:
        _rows(conn, customer, user, **changes)
    assert violation(refused.value) == ("23505" if constraint.endswith("meaning") else "23514", constraint)
