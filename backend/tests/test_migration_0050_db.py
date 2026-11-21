"""Migration 0050 (PRD 6 F1): feedback's reasons, notice and handled mark, up and down, and
a downgrade that refuses while what people said would be lost."""

import subprocess

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import clear_keycloak_state, migrate
from tests.test_api_support import CSRF, Api

pytestmark = pytest.mark.db
fresh = base.fresh
BEFORE = "0049"


def test_0050_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, BEFORE)
    before = base._schema(db)
    migrate(url, "head")
    after = base._schema(db)
    assert {"feedback_not_useful"} <= base._names(after, "indexes", 1)
    base._downgrade(url, BEFORE)
    assert base._schema(db) == before
    migrate(url, "head")
    assert base._schema(db) == after


def test_the_downgrade_refuses_while_a_reason_the_notice_or_a_handled_mark_is_kept(
    fresh: tuple[str, Database],
) -> None:
    url, db = fresh
    migrate(url, "head")
    signed = Api(db).new_signed()
    answer = signed.client.post("/api/chat", json={"question": "q"}, headers=CSRF).json()["answer"]["id"]
    body = {"rating": "down", "reason_codes": ["wrong_figure"], "notice_shown": True}
    assert signed.client.post(f"/api/messages/{answer}/feedback", json=body, headers=CSRF).status_code == 204

    # Changed on purpose (#737, 21 Nov 2026): sign-in marks people Keycloak, which 0103 won't downgrade;
    # clear it so the refusal below is the kept row's own.
    clear_keycloak_state(db, signed.customer_id)
    with pytest.raises(subprocess.CalledProcessError) as refused:
        base._downgrade(url, BEFORE)
    assert b"downgrading would lose them" in refused.value.stderr
    with db.customer(signed.customer_id) as conn:
        assert conn.execute(text("SELECT reason_codes FROM feedback")).scalar_one() == ["wrong_figure"]
        conn.execute(text("UPDATE feedback SET reason_codes = '{}', notice_shown = false"))
    base._downgrade(url, BEFORE)
    with db.customer(signed.customer_id) as conn:
        assert conn.execute(text("SELECT rating FROM feedback")).scalar_one() == "down"
