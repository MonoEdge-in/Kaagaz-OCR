"""Migration 0087 (PRD 7, #599, 14 Nov 2026): the access view of trashed folders. It holds every
column of ``trashed_folders`` with its comment, reads as its reader, and its rule is 0086's own
text; up, down (the schema as at 0086) and up again on a fresh database."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0086, M0087 = _load("0086_access_views.py"), _load("0087_trashed_folders_view.py")
_COLUMNS = (
    "SELECT attname, format_type(atttypid, atttypmod), col_description(attrelid, attnum) FROM pg_attribute"
    " WHERE attrelid = CAST(:r AS regclass) AND attnum > 0 AND NOT attisdropped ORDER BY attnum"
)
_OURS = "SELECT count(*) FROM pg_class WHERE relname = 'visible_trashed_folders'"


def test_the_revision_follows_0086() -> None:
    assert (M0087.revision, M0087.down_revision) == ("0087", "0086")


def test_the_rule_is_0086s_with_one_change_fail_closed() -> None:
    """One rule text: each view is 0086's with an open department read as ``NOT a.walled`` (the
    organisation keeps nothing to some people, as on day one) or ``a.open`` (a department still
    there and open), and the trashed folders' view is the folders' view on another table."""
    changed = {"d": "d.department_id", "f": "f.department_id", "t": "t.department_id"}
    for name, view in M0086.VIEWS.items():
        alias = changed["d" if name == "visible_documents" else "f"]
        expected = view.replace(f"{alias} <> ALL (a.closed)", f"NOT a.walled OR {alias} = ANY (a.open)")
        assert expected.replace("CREATE VIEW", "CREATE OR REPLACE VIEW") == M0087.VIEWS[name]
    folders = M0086.VIEWS["visible_folders"].replace("visible_folders", "visible_trashed_folders")
    trashed = folders.replace("public.folders f", "public.trashed_folders t").replace("f.", "t.")
    expected = trashed.replace(
        f"{changed['t']} <> ALL (a.closed)", f"NOT a.walled OR {changed['t']} = ANY (a.open)"
    )
    assert expected == M0087.VIEW


def test_the_view_holds_every_column_of_its_table_with_its_comment(db: Database) -> None:
    with db.system() as conn:
        view = conn.execute(text(_COLUMNS), {"r": "visible_trashed_folders"}).all()
        table = conn.execute(text(_COLUMNS), {"r": "trashed_folders"}).all()
        options = conn.execute(
            text("SELECT reloptions FROM pg_class WHERE relname = 'visible_trashed_folders'")
        ).scalar_one()
    assert view == table
    assert options == ["security_invoker=true"]


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0086")
    at_0086 = _state(db)
    migrate(url, "0087")
    at_0087 = _state(db)
    with db.system() as conn:
        assert conn.execute(text(_OURS)).scalar_one() == 1

    base._downgrade(url, "0086")
    assert _state(db) == at_0086
    with db.system() as conn:
        assert conn.execute(text(_OURS)).scalar_one() == 0

    migrate(url, "0087")
    assert _state(db) == at_0087
