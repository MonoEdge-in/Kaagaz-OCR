"""Migration 0008 (council 3 Oct 2026, PR 2b-1): the sound key function and its index.

Up, down and up again on a fresh database (from test_migration_0002_db, a plain
import): the downgrade gives back 0007's schema exactly. The function is IMMUTABLE,
STRICT, PARALLEL SAFE, runs with an empty search_path, keeps PUBLIC's EXECUTE (the
worker's inserts run it) and carries its comment; the index is partial, built on the
function, and the production word statement uses it.
"""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.search import hybrid
from kaagaz.search.scope import EVERYTHING
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate, new_customer
from tests.test_migration_0004_db import _current, _head
from tests.test_search_db import add_document, add_pieces

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
_FUNCTION = text(
    "SELECT provolatile, proisstrict, proparallel, proconfig::text, proacl IS NULL,"
    " obj_description(oid, 'pg_proc') FROM pg_proc"
    " WHERE pronamespace = 'public'::regnamespace AND proname = 'kaagaz_sound_key_v1'"
)
_INDEX = (
    "CREATE INDEX pieces_sound ON public.pieces USING gin (to_tsvector('simple'::regconfig,"
    " kaagaz_sound_key_v1(COALESCE(text_latin, text)))) WHERE (COALESCE(text_latin, text) ~ '[A-Za-z]'::text)"
)


def _indexes(database: Database) -> dict[str, str]:
    return {str(row[1]): str(row[2]) for row in m0002._schema(database)["indexes"]}


def test_0008_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0007")
    at_0007 = m0002._schema(database)
    assert "pieces_sound" not in _indexes(database)

    migrate(url, "head")
    assert _current(url) == _head()
    at_head = m0002._schema(database)
    assert _indexes(database)["pieces_sound"] == _INDEX
    with database.system() as conn:
        (function,) = conn.execute(_FUNCTION).all()
    volatility, strict, parallel, config, public_execute, comment = function
    assert (volatility, strict, parallel, config, public_execute) == (
        "i",
        True,
        "s",
        '{"search_path=\\"\\""}',
        True,
    )
    assert "kaagaz/chunking/script.py" in comment

    m0002._downgrade(url, "0007")
    assert m0002._schema(database) == at_0007
    with database.system() as conn:
        assert conn.execute(_FUNCTION).all() == []

    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_the_word_statement_can_use_the_sound_index(fresh: tuple[str, Database]) -> None:
    """2,000 pieces of one customer, analysed, so the customer filter alone does not pick
    the plan: the sound arm's index condition matches pieces_sound, next to the other
    arms' GIN indexes.

    The plan is the owner's, with the customer set: under row level security PostgreSQL
    never uses a GIN index for ``@@`` (it is not LEAKPROOF, so it may not run before the
    policy), the same for pieces_tsv and pieces_tsv_simple. EXPLAIN reads no rows."""
    url, database = fresh
    migrate(url, "head")
    customer, _ = new_customer(database)
    document = add_document(database, customer)
    pieces = [(f"Rent paid for August, receipt {n}", None) for n in range(2000)]
    (wanted, *_) = add_pieces(database, customer, document, [("भोगवटादार रामचंद्र", None), *pieces])
    # Only the table owner may ANALYZE (the app role may not); it reads no rows into the test.
    with database.engine.begin() as conn:
        conn.execute(text("ANALYZE pieces, documents"))
    words = {"c": customer, "q": "zzz", "k": "zzz", "g": "", "sk": hybrid.sound_query("bhogwatdar"), "n": 50}
    with database.engine.begin() as conn:
        conn.execute(text("SELECT set_config('kaagaz.customer_id', :c, true)"), {"c": customer})
        plan = "\n".join(
            conn.execute(text("EXPLAIN " + hybrid._WORD.text), words | EVERYTHING.params()).scalars()
        )
    with database.customer(customer) as conn:
        found = conn.execute(hybrid._WORD, words | EVERYTHING.params()).all()

    assert "Bitmap Index Scan on pieces_sound" in plan
    assert [str(row[0]) for row in found] == [wanted]  # the control: the app role finds the piece
