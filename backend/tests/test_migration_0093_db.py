"""Migration 0093 (PRD 7 PR 12, #605): locked files. Up, down (the schema as at 0092) and up again
on a fresh database; the CHECKs refuse a locked version that is ready or keeps its card, and a
version in the state locked that is not locked; a run may end locked. What the functions do:
test_isolation_locked_files; their rights: test_isolation_schema."""

import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from kaagaz.reading import office
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, migrate, new_customer, violation
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db
fresh = base.fresh
M0093 = _load("0093_locked_files.py")
_FUNCTIONS = (
    "SELECT count(*) FROM pg_proc WHERE proname IN ('kaagaz_lock', 'kaagaz_unlock', 'kaagaz_lock_grant',"
    " 'kaagaz_lock_revoke', 'kaagaz_clear_derived', 'kaagaz_lock_grants_follow',"
    " 'kaagaz_refuse_locked_content')"
)
_TRIGGERS = (
    "SELECT count(*) FROM pg_trigger WHERE tgname LIKE '%locked_refused' OR tgname LIKE '%lock_grants_follow'"
)
_COLUMNS = (
    "SELECT count(*) FROM pg_attribute WHERE attrelid = 'users'::regclass AND attname LIKE 'locked_open%'"
)
_LOCKED = "UPDATE documents SET locked_at = now(), locked_by = :u, status = :s WHERE id = :d"


def test_the_revision_follows_0092() -> None:
    assert (M0093.revision, M0093.down_revision) == ("0093", "0092")


def test_the_kinds_with_page_pictures_take_the_office_kinds_drawn_as_pdf() -> None:
    assert set(M0093.RENDERED) == office.PDF_KINDS
    assert set(M0093.LOCKABLE) == {"pdf", "png", "jpeg", "tiff", "heic"} | office.PDF_KINDS


def _counts(db: Database) -> list[int]:
    with db.system() as conn:
        return [int(conn.execute(text(query)).scalar_one()) for query in (_FUNCTIONS, _TRIGGERS, _COLUMNS)]


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0092")
    at_0092 = _state(db)
    migrate(url, "0093")
    at_0093 = _state(db)
    assert _counts(db) == [7, len(M0093.TRIGGERS), 2]

    base._downgrade(url, "0092")
    assert _state(db) == at_0092
    assert _counts(db) == [0, 0, 0]

    migrate(url, "0093")
    assert _state(db) == at_0093


@pytest.mark.parametrize("status", ["ready", "needs_reader"])
def test_a_locked_version_is_never_ready(db: Database, status: str) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user)
    with pytest.raises(DBAPIError) as refused:
        _owner(db, _LOCKED, {"u": user, "s": status, "d": document})
    assert violation(refused.value) == ("23514", "documents_locked_state")


def test_the_state_locked_is_only_a_locked_versions(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user)
    with pytest.raises(DBAPIError) as refused:
        _owner(db, "UPDATE documents SET status = 'locked' WHERE id = :d", {"d": document})
    assert violation(refused.value) == ("23514", "documents_locked_state")


@pytest.mark.parametrize(
    "column", ["doc_type = 'invoice'", "summary = 'x'", "languages = '{en}'", "document_date = '2026-01-01'"]
)
def test_a_locked_version_keeps_no_card(db: Database, column: str) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user)
    _owner(db, _LOCKED, {"u": user, "s": "locked", "d": document})
    with pytest.raises(DBAPIError) as refused:
        _owner(db, f"UPDATE documents SET {column} WHERE id = :d", {"d": document})  # noqa: S608 - fixed
    assert violation(refused.value) == ("23514", "documents_locked_cleared")


def test_a_run_may_end_locked(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user)
        conn.execute(
            text(
                "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status)"
                " VALUES (:c, :d, now(), now(), 'locked')"
            ),
            {"c": customer, "d": document},
        )


def test_the_downgrade_is_refused_while_a_file_is_locked(fresh: tuple[str, Database]) -> None:
    """Review (15 Nov 2026): downgraded, a locked file would stay locked with nothing to open or
    unlock it. Unlocked again, the downgrade goes ahead."""
    url, db = fresh
    migrate(url, "0093")
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user)
    _owner(db, _LOCKED, {"u": user, "s": "received", "d": document})
    with pytest.raises(subprocess.CalledProcessError) as refused:
        base._downgrade(url, "0092")
    assert b"unlock every file first" in refused.value.stderr
    assert _counts(db) == [7, len(M0093.TRIGGERS), 2]  # nothing dropped
    _owner(db, "UPDATE documents SET locked_at = NULL, locked_by = NULL WHERE id = :d", {"d": document})
    base._downgrade(url, "0092")
    assert _counts(db) == [0, 0, 0]
