"""Migration 0090b (PRD 7 PR 10c, #601): the move previews' caller check and auto-filing's give.
Up, down (the schema as at 0090) and up again on a fresh database; a private folder sets
walls_set, worked out on upgrade and by the trigger after. What they do:
test_isolation_wall_rules; their rights: test_isolation_schema."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_folder, migrate, new_customer
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db
fresh = base.fresh
M0090B = _load("0090b_wall_rules.py")
_PLACE = "SELECT pg_get_functiondef('public.kaagaz_place_audience(uuid, uuid, boolean)'::regprocedure)"
_GIVE = "SELECT count(*) FROM pg_proc WHERE proname = 'kaagaz_give_uploader'"
_WALLS = "SELECT walls_set FROM customers WHERE id = :c"
_PRIVATE = "UPDATE folders SET private = true WHERE id = :f"


def test_the_revision_follows_0090() -> None:
    assert (M0090B.revision, M0090B.down_revision) == ("0090b", "0090")


def test_the_give_is_the_access_views_rule_and_the_preview_only_its_check_changed() -> None:
    assert M0090B._0090.DOCUMENT_RULE in M0090B.GIVE_UPLOADER
    assert "kaagaz.user_id" not in M0090B.GIVE_UPLOADER and "visible_documents" not in M0090B.GIVE_UPLOADER
    assert M0090B._0090._ADMIN_ONLY not in M0090B.PLACE_AUDIENCE
    assert M0090B.PLACE_AUDIENCE.replace("CREATE OR REPLACE", "CREATE").count(M0090B._0090.PLACE_RULE) == 1


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0090")
    private_only, first = new_customer(db)
    later, user = new_customer(db)
    with db.customer(private_only) as conn:
        vault = insert_folder(conn, private_only, first, "Vault")
    with db.customer(later) as conn:
        safe = insert_folder(conn, later, user, "Safe")
    _owner(db, _PRIVATE, {"f": vault})  # at 0090 a private folder sets nothing
    assert _owner(db, _WALLS, {"c": private_only})[0][0] is False
    at_0090 = _state(db)
    with db.system() as conn:
        before = conn.execute(text(_PLACE)).scalar_one()
    migrate(url, "0090b")
    assert _owner(db, _WALLS, {"c": private_only})[0][0] is True  # worked out once more
    assert _owner(db, _WALLS, {"c": later})[0][0] is False
    _owner(db, _PRIVATE, {"f": safe})  # the trigger, on Private
    assert _owner(db, _WALLS, {"c": later})[0][0] is True
    with db.system() as conn:
        assert conn.execute(text(_GIVE)).scalar_one() == 1
        assert "a.management" in conn.execute(text(_PLACE)).scalar_one()

    base._downgrade(url, "0090")
    assert _state(db) == at_0090
    with db.system() as conn:
        assert conn.execute(text(_GIVE)).scalar_one() == 0
        assert conn.execute(text(_PLACE)).scalar_one() == before

    migrate(url, "0090b")
    with db.system() as conn:
        assert conn.execute(text(_GIVE)).scalar_one() == 1
