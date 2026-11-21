"""Migration 0051 (PRD 6 F3): the learning index, ``learning_removals`` and the operator's
``kaagaz_op_answer_weeks``, up and down, and a downgrade that refuses while a Remove is kept."""

import subprocess

import pytest

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import clear_keycloak_state, migrate
from tests.test_api_support import CSRF, Api
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db
fresh = base.fresh
BEFORE = "0050"


def test_0051_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, BEFORE)
    before = base._schema(db)
    migrate(url, "head")
    after = base._schema(db)
    assert "learning_removals" in base._names(after, "relations")
    assert {"feedback_learning", "learning_removals_document"} <= base._names(after, "indexes", 1)
    assert "kaagaz_op_answer_weeks" in base._names(after, "functions")
    base._downgrade(url, BEFORE)
    assert base._schema(db) == before
    migrate(url, "head")
    assert base._schema(db) == after


def test_the_downgrade_refuses_while_a_remove_is_kept(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "head")
    api = Api(db)
    admin = api.new_admin()
    document = api.add_document(admin.customer_id, name="march-bill.pdf")
    assert admin.client.delete(f"/api/admin/learned/{document}", headers=CSRF).status_code == 204

    # Changed on purpose (#737, 21 Nov 2026): sign-in marks people Keycloak, which 0103 won't downgrade;
    # clear it so the refusal below is the kept row's own.
    clear_keycloak_state(db, admin.customer_id)
    with pytest.raises(subprocess.CalledProcessError) as refused:
        base._downgrade(url, BEFORE)
    assert b"an admin removed what Kaagaz learned" in refused.value.stderr
    # As the table owner: the app may never delete a Remove.
    assert _owner(db, "SELECT count(*) FROM learning_removals", {}) == [(1,)]
    _owner(db, "DELETE FROM learning_removals", {})
    base._downgrade(url, BEFORE)
    assert _owner(db, "SELECT to_regclass('learning_removals')", {}) == [(None,)]
