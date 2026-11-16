"""Migration 0011 (council 3 Oct 2026, PR 2b-1): the sound key function and the stored
``pieces.tsv_sound`` column.

Up, down and up again on a fresh database (from test_migration_0002_db, a plain
import): a piece written before the upgrade gets its keys, and the downgrade gives
back 0010's schema exactly. The function is IMMUTABLE, STRICT, PARALLEL SAFE, runs
with an empty search_path and standard_conforming_strings on, keeps PUBLIC's EXECUTE (the
worker's inserts run it) and carries its comment; so does the column, and the word
statement reads the stored keys, never the function.
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
_COLUMN = text(
    "SELECT pg_get_expr(d.adbin, d.adrelid), a.attgenerated, col_description(a.attrelid, a.attnum)"
    " FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum"
    " WHERE a.attrelid = 'public.pieces'::regclass AND a.attname = 'tsv_sound'"
)


def _indexes(database: Database) -> dict[str, str]:
    return {str(row[1]): str(row[2]) for row in m0002._schema(database)["indexes"]}


def test_0011_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0010")
    customer, _ = new_customer(database)
    (piece,) = add_pieces(database, customer, add_document(database, customer), [("भोगवटादार गायकवाड", None)])
    at_0010 = m0002._schema(database)

    migrate(url, "head")
    assert _current(url) == _head()
    at_head = m0002._schema(database)
    assert "pieces_sound" not in _indexes(database)  # no index: row level security never uses it
    with database.customer(customer) as conn:
        keys = conn.execute(
            text("SELECT tsv_sound::text FROM pieces WHERE id = :p"), {"p": piece}
        ).scalar_one()
    assert keys == "'bogvtdr':1 'gikvd':2"  # the rewrite keyed the piece written before it
    with database.system() as conn:
        expression, generated, column_comment = conn.execute(_COLUMN).one()
    assert expression == "to_tsvector('simple'::regconfig, kaagaz_sound_key_v1(COALESCE(text_latin, text)))"
    assert generated == "s" and "kaagaz_sound_key_v1" in column_comment
    with database.system() as conn:
        (function,) = conn.execute(_FUNCTION).all()
    volatility, strict, parallel, config, public_execute, comment = function
    assert (volatility, strict, parallel, config, public_execute) == (
        "i",
        True,
        "s",
        '{"search_path=\\"\\"",standard_conforming_strings=on}',
        True,
    )
    assert "kaagaz/chunking/script.py" in comment

    m0002._downgrade(url, "0010")
    assert m0002._schema(database) == at_0010
    with database.system() as conn:
        assert conn.execute(_FUNCTION).all() == []

    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_the_word_statement_reads_the_stored_keys(db: Database) -> None:
    """As the app role, the word statement tests ``tsv_sound`` and never runs the
    function: the keys were stored when the piece was written. Changed on purpose (#619): the
    sound query is the word list's second rung, ``_SOUND``."""
    customer, _ = new_customer(db)
    (wanted, _) = add_pieces(
        db, customer, add_document(db, customer), [("भोगवटादार रामचंद्र", None), ("Rent", None)]
    )
    words = {"c": customer, "q": "zzz", "k": "zzz", "g": "", "sk": hybrid.sound_query("bhogwatdar"), "n": 50}
    words |= {"exact": [], "ranked": hybrid.SOUND_RANKED}
    # Changed on purpose (#617): the sound candidates come from kaagaz_word_candidates (0094).
    words |= {"skc": str(words["sk"]).replace(";", "|"), "docs": None, "cap": hybrid.CANDIDATE_CAP}
    with db.customer(customer) as conn:
        plan = "\n".join(
            conn.execute(text("EXPLAIN VERBOSE " + hybrid._SOUND.text), words | EVERYTHING.params()).scalars()
        )
        found = conn.execute(hybrid._SOUND, words | EVERYTHING.params()).all()

    assert "tsv_sound @@" in plan
    assert "kaagaz_sound_key_v1" not in plan
    assert [str(row[0]) for row in found] == [wanted]
