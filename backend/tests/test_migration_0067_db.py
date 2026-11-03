"""Migration 0067 (#479, Tushar, 3 Nov 2026): the two limited logins, no temporary tables for
the public, and 0001's two owner-rights functions on the empty search path. Up, down and up
again restores the schema exactly; the logins stay on a downgrade (roles belong to the whole
server, as 0001's ``kaagaz_app``). What the logins may do: tests/test_isolation_logins.py."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer
from tests.test_migration_0037 import _load

pytestmark = pytest.mark.db
fresh = base.fresh
PREVIOUS = "0065"  # the lead re-chains it when merging (0067 after 0066)
M0067 = _load("0067_limited_logins.py")
_TEMP = text("SELECT has_database_privilege('kaagaz_app', current_database(), 'TEMPORARY')")
_PATHS = text(
    "SELECT proname, proconfig::text, md5(prosrc) FROM pg_proc WHERE pronamespace = 'public'::regnamespace"
    " AND proname IN ('kaagaz_login_lookup', 'kaagaz_create_customer') ORDER BY proname"
)


def _state(db: Database) -> tuple[bool, list[tuple[str, str]], list[str]]:
    """Whether PUBLIC may make temporary tables, each function's search path, and its text."""
    with db.engine.begin() as conn:  # the catalog, as the owner
        rows = conn.execute(_PATHS).all()
        temp = bool(conn.execute(_TEMP).scalar_one())
    return temp, [(str(name), str(path)) for name, path, _ in rows], [str(body) for *_, body in rows]


def test_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, PREVIOUS)
    before, state_before = base._schema(db), _state(db)
    names = sorted(M0067.OWNER_FUNCTIONS)
    assert state_before[:2] == (True, [(name, '{"search_path=public, pg_temp"}') for name in names])

    migrate(url, "0067")
    after = base._schema(db)
    assert _state(db)[:2] == (False, [(name, '{"search_path=\\"\\""}') for name in names])
    customer, _ = new_customer(db)  # both functions still work: kaagaz_create_customer here,
    with db.system() as conn:  # kaagaz_login_lookup below, with every name schema-qualified
        found = conn.execute(text("SELECT customer_id FROM kaagaz_login_lookup('nobody@example.test')")).all()
    assert found == [] and customer

    base._downgrade(url, PREVIOUS)
    assert base._schema(db) == before
    assert _state(db) == state_before  # 0001's text exactly
    migrate(url, "0067")  # the logins already exist: made again idempotently
    assert base._schema(db) == after


def test_single_head() -> None:
    assert (M0067.revision, M0067.down_revision) == ("0067", PREVIOUS)
