"""Migration 0102 (PRD 9 F-03, #680): ``kaagaz_duplicate`` skips a failed or never-read copy in Trash
the person sees, as ``db/documents.py``'s ``REFUSED_ALONE``. Up, down (0091's body and comment
exactly) and up again on a fresh database; the answer before and after. The upload itself:
test_api_upload_refused_again; who sees what: test_isolation_trash_reupload."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, migrate, new_customer
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0102 = _load("0102_trash_reupload.py")
_NOW = datetime.now(UTC)
_SHA = {"failed": "a" * 64, "ready": "b" * 64, "live": "c" * 64}  # live: failed, not in Trash
_ANSWER = "SELECT answer FROM kaagaz_duplicate(ARRAY[:h])"
_COMMENT = "SELECT obj_description('public.kaagaz_duplicate(text[], bigint[])'::regprocedure, 'pg_proc')"


def test_the_revision_follows_0101() -> None:
    assert (M0102.revision, M0102.down_revision) == ("0102", "0101")


def _answers(db: Database, customer: str, user: str) -> dict[str, str]:
    with db.user(customer, user) as conn:
        return {s: conn.execute(text(_ANSWER), {"h": h}).scalar_one() for s, h in _SHA.items()}


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0101")
    at_0101 = _state(db)
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        for name, sha in _SHA.items():
            live = name == "live"  # the others in Trash
            trash = {} if live else {"deleted_at": _NOW, "deleted_by": user}
            insert_document(
                conn, customer, sha256=sha, status="failed" if live else name, uploaded_by=user, **trash
            )
    assert _answers(db, customer, user) == {"failed": "visible", "ready": "visible", "live": "visible"}

    migrate(url, "0102")
    at_0102 = _state(db)
    assert _answers(db, customer, user) == {"failed": "none", "ready": "visible", "live": "visible"}
    with db.system() as conn:
        assert "PRD 9 F-03" in conn.execute(text(_COMMENT)).scalar_one()

    base._downgrade(url, "0101")
    assert _state(db) == at_0101  # every function body and comment as 0091 left them
    assert _answers(db, customer, user) == {"failed": "visible", "ready": "visible", "live": "visible"}

    migrate(url, "0102")
    assert _state(db) == at_0102
