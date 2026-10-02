"""Sound keys: Marathi and Hindi words found however they are typed in English letters
(council 3 Oct 2026, PR 2b-1, PROVISIONAL).

1. ``kaagaz_sound_key_v1(text)``: the sound key (kaagaz/chunking/script.py,
   ``sound_key``) of every run of ASCII letters in a text, joined by single
   spaces. A plain SQL function, IMMUTABLE STRICT PARALLEL SAFE, with an empty
   search_path and every call ``pg_catalog``-qualified, so nothing a role puts
   on its path can change it. Literal ``replace`` for letter pairs, and only
   regular expressions with one way to match, so PostgreSQL's engine and
   Python's agree (tests/test_sound_key_db.py runs both over the same words).
   Non-letters become spaces first, then ``translate`` lower-cases (it ignores
   the collation): ``lower`` would turn the Kelvin sign into k. ``\\m`` and
   ``\\M`` are the start and end of a word (``\\b`` is a backspace there).
   EXECUTE stays with PUBLIC: the column below runs it on every insert into
   pieces, by the worker's role too.
2. ``pieces.tsv_sound``: a STORED generated column,
   ``to_tsvector('simple', kaagaz_sound_key_v1(coalesce(text_latin, text)))``
   (empty for a text with no ASCII letter), and ``pieces_sound``, a GIN index on
   it. ``coalesce``: a piece that is mostly English has no ``text_latin``, yet
   its Marathi names in Latin letters are keyed too.

Why a stored column, not an expression index (PROVISIONAL, reverses the
council's call; Vrushit decides): under row level security PostgreSQL never
uses a GIN index for ``@@`` (it is not LEAKPROOF, so it may not run before the
policy), so an expression arm ran the function on every piece of the customer on
every search: 1,122 ms for 2,001 pieces against 7.8 ms with the stored column.
The cost is one rewrite of ``pieces`` here, under an ACCESS EXCLUSIVE lock
(0004's and 0006's pattern; env.py's lock_timeout makes a busy table fail
fast): 2.1 s for 2,001 pieces on the laptop, and it grows with the table.
Offline: stop the api and the worker first.

Any change to the rules is a NEW function, ``kaagaz_sound_key_v2``, with a new
column in a new migration, never ``CREATE OR REPLACE`` of this one: a stored
column is not recomputed when its function changes, so its keys would go stale,
and no test could see it.

The function and the column are commented (the data dictionary's rule); the
index is not, as the completeness test of tests/test_migration_0007_db.py counts
the comments of tables, columns and indexes. The downgrade drops the index, the
column, then the function; no data is lost (the column is computed).

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# script.py's sound_key, step for step, on a text of single-space-separated words.
# Each "{}" is the text so far. No colon in them (SQLAlchemy reads one as a parameter).
_STEPS = (
    "pg_catalog.btrim(pg_catalog.regexp_replace({}, '[^A-Za-z]+', ' ', 'g'))",
    f"pg_catalog.translate({{}}, '{_UPPER}', '{_UPPER.lower()}')",
    r"pg_catalog.regexp_replace({}, '\mdny', 'jn', 'g')",
    r"pg_catalog.regexp_replace({}, '\mgny', 'jn', 'g')",
    "pg_catalog.replace(pg_catalog.replace(pg_catalog.replace({}, 'ph', 'f'), 'x', 'ks'), 'ck', 'k')",
    "pg_catalog.regexp_replace({}, 'c(?!h)', 'k', 'g')",
    "pg_catalog.replace(pg_catalog.translate({}, 'wzq', 'vjk'), 'ay', 'ai')",
    r"pg_catalog.regexp_replace({}, '([bcdfgjklmnpqrstv])h+', '\1', 'g')",
    r"pg_catalog.regexp_replace({}, '([bcdfgjklmnpqrstv]r)u', '\1i', 'g')",
    "pg_catalog.replace(pg_catalog.replace(pg_catalog.replace({}, 'ee', 'i'), 'oo', 'u'), 'au', 'o')",
    r"pg_catalog.regexp_replace({}, '([a-z])\1+', '\1', 'g')",
    r"pg_catalog.regexp_replace({}, 'aon\M', 'av', 'g')",
    r"pg_catalog.regexp_replace({}, 'ao\M', 'av', 'g')",
    r"pg_catalog.regexp_replace({}, 'anv\M', 'av', 'g')",
    r"pg_catalog.regexp_replace({}, 'ey\M', 'e', 'g')",
    "pg_catalog.regexp_replace({}, '(?<=[a-z])a', '', 'g')",
)


def _key_expression() -> str:
    expression = "words"
    for step in _STEPS:
        expression = step.format(expression)
    return expression


SOUND_KEY = f"""
    CREATE FUNCTION kaagaz_sound_key_v1(words text)
    RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path = '' AS
    $$ SELECT {_key_expression()} $$
    """
COMMENT = (
    "The sound key of every run of ASCII letters in a text, joined by spaces, so spellings of one"
    " Marathi or Hindi word typed the way it sounds compare equal (bhogwatdar, bhogavatadar)."
    " The rules of sound_key in kaagaz/chunking/script.py, version 1. Stored in pieces.tsv_sound."
)

COLUMN_COMMENT = (
    "Generated. Word index of the sound keys of coalesce(text_latin, text) (kaagaz_sound_key_v1),"
    " so a Marathi or Hindi word typed the way it sounds is found."
)
LOCK = "LOCK TABLE pieces IN ACCESS EXCLUSIVE MODE"

UPGRADE = [
    LOCK,
    SOUND_KEY,
    f"COMMENT ON FUNCTION kaagaz_sound_key_v1(text) IS '{COMMENT}'",
    """
    ALTER TABLE pieces ADD COLUMN tsv_sound tsvector GENERATED ALWAYS AS
        (to_tsvector('simple', kaagaz_sound_key_v1(coalesce(text_latin, text)))) STORED
    """,
    f"COMMENT ON COLUMN pieces.tsv_sound IS '{COLUMN_COMMENT}'",
    "CREATE INDEX pieces_sound ON pieces USING gin (tsv_sound)",
]

DOWNGRADE = [
    LOCK,
    "DROP INDEX IF EXISTS pieces_sound",
    "ALTER TABLE pieces DROP COLUMN IF EXISTS tsv_sound",
    "DROP FUNCTION IF EXISTS kaagaz_sound_key_v1(text)",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
