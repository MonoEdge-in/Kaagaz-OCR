"""Migration 0006 (PRD 3 V1, W1, W2): half-precision vectors on a new HNSW index, the
simple-words column and the Latin trigram index, and the two new message columns.

A vector survives the upgrade and the downgrade (cast both ways, PROVISIONAL
plan item 3), the downgrade gives back 0005's schema exactly and refuses while a
message holds what 0005 cannot (item 6), the message CHECKs refuse bad rows
(item 5), and ``kaagaz_customer_ids()`` lists ids only. The fresh database and the schema snapshot come from
test_migration_0002_db (a plain import). Rows are written through the app role
with the customer set.
"""

import json
import subprocess

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer, violation
from tests.test_migration_0004_db import _current, _head

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
_PIECE = text(
    "INSERT INTO pieces (customer_id, document_id, seq, page_from, page_to, text, regions, label,"
    " embedding, embedding_model) VALUES (:c, :d, 0, 1, 1, 'Invoice total', CAST(:r AS jsonb), 'verified',"
    " CAST(:v AS vector), 'qwen3-embedding:0.6b')"
)
VECTOR = "[" + ",".join(["0.5"] * 1024) + "]"
_BOX = json.dumps([{"kind": "box", "page": 1, "box": [0.1, 0.4, 0.9, 0.42]}])
_MESSAGE = text(
    "INSERT INTO messages (customer_id, conversation_id, role, text, rewritten_question, scope)"
    " VALUES (:c, :conversation, 'user', 'And the expenses?', :rewritten, CAST(:scope AS jsonb))"
)


def _conversation(conn: Connection, customer_id: str, user_id: str) -> str:
    return str(
        conn.execute(
            text(
                "INSERT INTO conversations (customer_id, user_id, title) VALUES (:c, :u, 'Chat') RETURNING id"
            ),
            {"c": customer_id, "u": user_id},
        ).scalar_one()
    )


def _vector(database: Database, customer_id: str) -> tuple[str, str]:
    with database.customer(customer_id) as conn:
        row = conn.execute(text("SELECT embedding::text, embedding_model FROM pieces")).one()
    return str(row[0]), str(row[1])


def test_upgrade_casts_the_vectors_and_downgrade_gives_back_0005(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0005")
    at_0005 = m0002._schema(database)
    customer, _ = new_customer(database)
    with database.customer(customer) as conn:
        document = insert_document(conn, customer)
        conn.execute(_PIECE, {"c": customer, "d": document, "r": _BOX, "v": VECTOR})
    stored = (VECTOR, "qwen3-embedding:0.6b")  # 0.5 is exact in half precision
    assert _vector(database, customer) == stored

    migrate(url, "head")

    at_head = m0002._schema(database)
    columns = {(str(row[0]), str(row[1])): row[2] for row in at_head["columns"]}
    assert columns[("pieces", "embedding")] == "halfvec(1024)"
    assert columns[("pieces", "tsv_simple")] == "tsvector"
    assert (columns[("messages", "rewritten_question")], columns[("messages", "scope")]) == ("text", "jsonb")
    indexes = {str(row[1]): str(row[2]) for row in at_head["indexes"]}
    assert indexes["pieces_embedding"].endswith(
        "USING hnsw (embedding halfvec_cosine_ops) WITH (m='16', ef_construction='64')"
    )
    assert indexes["pieces_tsv_simple"].endswith("USING gin (tsv_simple)")
    assert indexes["pieces_latin_trgm"].endswith("USING gin (text_latin gin_trgm_ops)")
    functions = {str(row[0]): row for row in at_head["functions"]}
    assert functions["kaagaz_customer_ids"][2:4] == (True, '{"search_path=\\"\\""}')
    assert _vector(database, customer) == stored

    m0002._downgrade(url, "0005")
    assert m0002._schema(database) == at_0005
    assert _vector(database, customer) == stored
    migrate(url, "head")
    assert m0002._schema(database) == at_head


@pytest.mark.parametrize(
    ("rewritten", "scope"), [("What were the expenses in September?", None), (None, '{"folder_id": null}')]
)
def test_downgrade_refuses_while_a_message_has_a_rewrite_or_a_scope(
    fresh: tuple[str, Database], rewritten: str | None, scope: str | None
) -> None:
    url, database = fresh
    migrate(url, "head")
    customer, user = new_customer(database)
    with database.customer(customer) as conn:
        conversation = _conversation(conn, customer, user)
        conn.execute(
            _MESSAGE, {"c": customer, "conversation": conversation, "rewritten": rewritten, "scope": scope}
        )
    at_head = m0002._schema(database)

    with pytest.raises(subprocess.CalledProcessError) as refused:
        m0002._downgrade(url, "0005")

    assert b"messages have a rewritten question or a scope" in refused.value.stderr
    assert _current(url) == _head()  # the whole downgrade is one transaction
    assert m0002._schema(database) == at_head


@pytest.mark.parametrize(
    ("rewritten", "scope", "constraint"),
    [
        ("x" * 301, None, "messages_rewritten_question_check"),
        (None, "[]", "messages_scope_check"),
        (None, json.dumps({"document_ids": ["d" * 36] * 110}), "messages_scope_check"),
    ],
    ids=["rewrite_over_300", "scope_not_an_object", "scope_over_4_kib"],
)
def test_the_message_checks_refuse_a_bad_row(
    db: Database, rewritten: str | None, scope: str | None, constraint: str
) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        conversation = _conversation(conn, customer, user)
        conn.execute(
            _MESSAGE,
            {
                "c": customer,
                "conversation": conversation,
                "rewritten": "x" * 300,
                "scope": '{"folder_id": null}',
            },
        )
    with pytest.raises(IntegrityError) as refused, db.customer(customer) as conn:
        conn.execute(
            _MESSAGE, {"c": customer, "conversation": conversation, "rewritten": rewritten, "scope": scope}
        )
    assert violation(refused.value) == ("23514", constraint)


def test_every_customer_is_listed_for_the_system_transaction(db: Database) -> None:
    """kaagaz_customer_ids() (for reembed) gives ids only, past the row level
    security that hides every customer from Database.system."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    with db.system() as conn:
        listed = [str(row[0]) for row in conn.execute(text("SELECT * FROM kaagaz_customer_ids()"))]
        assert conn.execute(text("SELECT count(*) FROM customers")).scalar_one() == 0
    assert {a, b} <= set(listed)
