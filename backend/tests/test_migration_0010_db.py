"""Migration 0010 (story vector sweep): one narrow owner-rights function that gives ids only.

The upgrade adds ``kaagaz_stories_to_embed``: SECURITY DEFINER, an empty search
path, three uuid columns out and nothing else, runnable by the app role only,
with a comment. The downgrade gives back 0009's schema exactly, and upgrading again the same
schema. The fresh database and the schema snapshot come from test_migration_0002_db.
"""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
_FUNCTION = text(
    "SELECT p.prosecdef, p.proconfig, p.proretset, pg_get_function_result(p.oid),"
    " pg_get_userbyid(p.proowner) = pg_get_userbyid(c.relowner),"
    " has_function_privilege('public', p.oid, 'EXECUTE'),"
    " has_function_privilege('kaagaz_app', p.oid, 'EXECUTE'), obj_description(p.oid, 'pg_proc')"
    " FROM pg_proc p, pg_class c"
    " WHERE p.oid = 'kaagaz_stories_to_embed(text, uuid, integer, uuid, uuid, uuid)'::regprocedure"
    " AND c.oid = 'document_stories'::regclass"
)


def test_upgrade_downgrade_and_upgrade_again(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0009")
    at_0009 = m0002._schema(database)

    migrate(url, "head")
    at_head = m0002._schema(database)
    with database.system() as conn:
        row = conn.execute(_FUNCTION).one()
    definer, config, returns_set, result, same_owner, public_may_run, app_may_run, comment = row
    assert definer
    assert config == ['search_path=""']
    # Ids only: no story, no vector, no tag ever leaves the function.
    assert (returns_set, result) == (True, "TABLE(customer_id uuid, user_id uuid, family_id uuid)")
    assert same_owner  # it runs as the table's owner, past row level security
    assert not public_may_run
    assert app_may_run
    assert comment.startswith("Story of this PDF: (customer, user, family) of at most p_limit stories")
    assert "Ids only, never the text." in comment

    m0002._downgrade(url, "0009")
    assert m0002._schema(database) == at_0009
    migrate(url, "head")
    assert m0002._schema(database) == at_head
