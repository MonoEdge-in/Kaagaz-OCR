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
_SHA = {
    "failed": "a" * 64,
    "ready": "b" * 64,
    "live": "c" * 64,
    "locked": "d" * 64,
    "run": "e" * 64,  # failed in Trash, after a good read (a ready run)
    "paged": "f" * 64,  # failed in Trash, with a page saved
}
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
        # In Trash: failed (also after a good read, or with a page), ready, and locked before it was read;
        # live: failed, not in Trash.
        for name, sha in _SHA.items():
            trash = {} if name == "live" else {"deleted_at": _NOW, "deleted_by": user}
            status = {"ready": "ready", "locked": "received"}.get(name, "failed")
            insert_document(conn, customer, sha256=sha, status=status, uploaded_by=user, **trash)
    with db.engine.begin() as conn:  # as the owner: the lock's columns
        conn.execute(
            text("UPDATE documents SET locked_at = now(), locked_by = :u WHERE sha256 = :h"),
            {"u": user, "h": _SHA["locked"]},
        )
        conn.execute(  # and they may open it, so they see it: the lock alone keeps it
            text(
                "INSERT INTO lock_grants (customer_id, document_id, user_id, given_by)"
                " SELECT customer_id, id, :u, :u FROM documents WHERE sha256 = :h"
            ),
            {"u": user, "h": _SHA["locked"]},
        )
        conn.execute(
            text(
                "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status)"
                " SELECT customer_id, id, now(), now(), 'ready' FROM documents WHERE sha256 = :h"
            ),
            {"h": _SHA["run"]},
        )
        conn.execute(
            text(
                "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit)"
                " SELECT customer_id, id, 1, 1, 1, 'pt' FROM documents WHERE sha256 = :h"
            ),
            {"h": _SHA["paged"]},
        )
    before = _answers(db, customer, user)
    assert [before[name] for name in ("failed", "run", "paged", "locked")] == [
        *["visible"] * 3,
        "locked_open",
    ]

    migrate(url, "0102")
    at_0102 = _state(db)
    assert _answers(db, customer, user) == {**before, "failed": "none"}  # only the failed copy in Trash
    with db.system() as conn:
        assert "PRD 9 F-03" in conn.execute(text(_COMMENT)).scalar_one()

    base._downgrade(url, "0101")
    assert _state(db) == at_0101  # every function body and comment as 0091 left them
    assert _answers(db, customer, user) == before

    migrate(url, "0102")
    assert _state(db) == at_0102
