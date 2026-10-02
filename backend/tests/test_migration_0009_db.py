"""Migration 0009 (story of this PDF): the table, its policies, CHECKs, index and comments.

The upgrade comments the new table and each column and no index, and adds one
index besides the key (no vector or word index yet); the downgrade gives back
0008's schema exactly, and upgrading again the same schema. The CHECKs refuse
every bad row. The fresh database and the schema snapshot come from
test_migration_0002_db (a plain import).
"""

from typing import Any

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from kaagaz.search.hybrid import vector_literal
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer, violation

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
TABLE = "document_stories"
VECTOR = vector_literal([0.5] * 1024)
_STORY = text(
    "INSERT INTO document_stories"
    " (customer_id, user_id, family_id, story, story_latin, embedding, embedding_model)"
    " VALUES (:c, :u, :f, :s, lower(:s), CAST(:v AS halfvec(1024)), :m)"
)
_COMMENTS = text(
    "SELECT c.relkind::text FROM pg_description d JOIN pg_class c ON c.oid = d.objoid"
    " WHERE d.classoid = 'pg_class'::regclass AND c.relnamespace = 'public'::regnamespace"
    " AND c.relname LIKE 'document_stor%'"
)
GOOD = {"s": "papa ne 2018 me daregaon wali jamin ka 7/12 diya", "v": VECTOR, "m": "embed#story1"}


def _row(conn: Connection, customer: str, user: str, **changes: Any) -> None:
    """A story on a new document, with ``changes``."""
    conn.execute(_STORY, {"c": customer, "u": user, "f": insert_document(conn, customer), **GOOD, **changes})


def test_upgrade_downgrade_and_upgrade_again(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0008")
    at_0008 = m0002._schema(database)

    migrate(url, "head")
    at_head = m0002._schema(database)
    policies = {(str(row[0]), str(row[1])) for row in at_head["policies"]}
    assert {(TABLE, "customer_only"), (TABLE, "own_user_only")} <= policies
    assert (TABLE, "document_stories_touch") in {(str(row[0]), str(row[1])) for row in at_head["triggers"]}
    indexes = {str(row[1]) for row in at_head["indexes"] if row[0] == TABLE}
    assert indexes == {"document_stories_pkey", "document_stories_family"}  # no HNSW or GIN in v1
    with database.system() as conn:
        kinds = [str(row[0]) for row in conn.execute(_COMMENTS)]
    columns = [row for row in at_head["columns"] if row[0] == TABLE]
    assert set(kinds) == {"r"}  # the table and its columns only, never an index
    assert len(kinds) == len(columns) + 1 == 11

    customer, user = new_customer(database)
    with database.user(customer, user) as conn:  # a row, so the downgrade has something to drop
        _row(conn, customer, user)
    m0002._downgrade(url, "0008")
    assert m0002._schema(database) == at_0008
    migrate(url, "head")
    assert m0002._schema(database) == at_head


BAD_ROWS = {
    "story_empty": ({"s": ""}, "document_stories_story_check"),
    "story_over_1000": ({"s": "x" * 1001}, "document_stories_story_check"),
    "vector_alone": ({"m": None}, "document_stories_vector_with_model"),
    "model_alone": ({"v": None}, "document_stories_vector_with_model"),
}


@pytest.mark.parametrize(("changes", "constraint"), list(BAD_ROWS.values()), ids=list(BAD_ROWS))
def test_the_checks_refuse_a_bad_row(db: Database, changes: dict[str, Any], constraint: str) -> None:
    customer, user = new_customer(db)
    with db.user(customer, user) as conn:  # good rows first: 1,000 code points, and no vector yet
        _row(conn, customer, user, s="ज" * 1000)
        _row(conn, customer, user, v=None, m=None)
    with pytest.raises(IntegrityError) as refused, db.user(customer, user) as conn:
        _row(conn, customer, user, **changes)
    assert violation(refused.value) == ("23514", constraint)


def test_the_word_index_is_generated_from_both_spellings(db: Database) -> None:
    customer, user = new_customer(db)
    with db.user(customer, user) as conn:
        family = insert_document(conn, customer)
        conn.execute(_STORY, {"c": customer, "u": user, "f": family, **GOOD, "s": "जमीन Loan"})
        conn.execute(text("UPDATE document_stories SET story_latin = 'jamin loan'"))
        tsv = conn.execute(text("SELECT tsv_simple::text FROM document_stories")).scalar_one()
    assert tsv == "'jamin':3 'loan':2,4 'जमीन':1"
