"""Migration 0007 (PRD 3 N1): the data dictionary inside the database.

Every table and column in ``public`` carries a comment, ``alembic_version``
included (a deliberate choice: then nothing is left out by name), so the
NL-to-SQL agent can read them straight from the catalog. A later migration
that adds a column without its own comment fails the first test.

The schema snapshot of test_migration_0002_db does not read pg_description, so
the up/down/up test counts and compares the comments itself. The fresh
database comes from there too (a plain import).
"""

import re

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate
from tests.test_migration_0004_db import _current, _head

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
# Every table and view (column NULL) and every column in public, with its comment or NULL.
_DICTIONARY = text(
    "WITH r AS (SELECT oid, relname FROM pg_class"
    " WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'p', 'v', 'm', 'f'))"
    " SELECT r.relname, NULL, obj_description(r.oid, 'pg_class') FROM r"
    " UNION ALL"
    " SELECT r.relname, a.attname, col_description(r.oid, a.attnum) FROM r"
    " JOIN pg_attribute a ON a.attrelid = r.oid AND a.attnum > 0 AND NOT a.attisdropped"
)
# Every comment on a table, column, index or sequence in public, whatever it is on.
_COMMENTS = text(
    "SELECT d.description FROM pg_description d JOIN pg_class c ON c.oid = d.objoid"
    " WHERE d.classoid = 'pg_class'::regclass AND c.relnamespace = 'public'::regnamespace"
)
# How many of those are on an index (0020's audit_log_feed): the dictionary has no indexes.
_ON_INDEXES = text(
    "SELECT count(*) FROM pg_description d JOIN pg_class c ON c.oid = d.objoid"
    " WHERE d.classoid = 'pg_class'::regclass AND c.relnamespace = 'public'::regnamespace AND c.relkind = 'i'"
)
# What must never be in a comment: identity numbers (an Aadhaar's 12 digits, in
# ASCII or Devanagari digits, run together or in groups of four, or a PAN in either
# case), e-mail addresses and links.
_DIGIT = r"[0-9\u0966-\u096f]"
_FORBIDDEN = re.compile(
    rf"{_DIGIT}{{4}}[ -]?{_DIGIT}{{4}}[ -]?{_DIGIT}{{4}}|[A-Za-z]{{5}}[0-9]{{4}}[A-Za-z]|@|://"
)
# One made-up sample per pattern, so the scan is shown to work. None starts with
# 2 to 9 or has a PAN's holder letter, so pr.yml's identity warnings stay quiet.
_CAUGHT = (
    "123456789012",
    "1234 5678 9012",
    "\u0967\u0968\u0969\u096a-\u096b\u096c\u096d\u096e-\u096f\u0966\u0967\u0968",
    "abcde1234f",
    "ABCDE1234F",
    "a@b",
    "x://y",
)


def _dictionary(database: Database) -> dict[tuple[str, str | None], str | None]:
    with database.system() as conn:
        return {(str(row[0]), row[1]): row[2] for row in conn.execute(_DICTIONARY)}


def _comments(database: Database) -> list[str]:
    with database.system() as conn:
        return [str(row[0]) for row in conn.execute(_COMMENTS)]


def test_every_table_and_column_has_a_comment(db: Database) -> None:
    dictionary = _dictionary(db)
    assert [name for name, comment in dictionary.items() if not (comment or "").strip()] == []
    assert dictionary[("alembic_version", None)]  # included on purpose, not left out
    assert dictionary[("alembic_version", "version_num")]
    assert len({table for table, _ in dictionary}) >= 21  # 20 product tables and alembic's


def test_no_comment_holds_an_identity_number_an_address_or_a_link(db: Database) -> None:
    comments = _comments(db)
    assert comments
    assert [comment for comment in comments if _FORBIDDEN.search(comment)] == []
    assert [sample for sample in _CAUGHT if not _FORBIDDEN.search(sample)] == []  # the scan works


def test_0007_up_down_up_sets_and_clears_every_comment(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0006")
    at_0006 = m0002._schema(database)
    assert _comments(database) == []

    migrate(url, "head")
    assert _current(url) == _head()
    at_head = _dictionary(database)
    assert None not in at_head.values()
    with database.system() as conn:
        on_indexes = conn.execute(_ON_INDEXES).scalar_one()
    assert len(_comments(database)) == len(at_head) + on_indexes

    m0002._downgrade(url, "0006")
    assert _comments(database) == []
    assert m0002._schema(database) == at_0006

    migrate(url, "head")
    assert _dictionary(database) == at_head
