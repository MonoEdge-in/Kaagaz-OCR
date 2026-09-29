"""Trash: a document family goes to Trash together and comes back together (PRD 1 F5, WP1.2).

Also the fixed visibility fragments every list, search and chat statement
writes into its SQL. Times are always passed in, never read from the
database clock, so these tests use fixed dates instead of waiting.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, text

from kaagaz.db import folders, trash, visibility
from kaagaz.db.engine import Database
from kaagaz.db.trash import (
    customer_ids_with_trash,
    lock_family,
    purge_next_family,
    restore_document,
    trash_document,
)
from tests.db_support import insert_document, name_session, new_customer, wait_until_blocked

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
BEFORE = NOW - timedelta(days=30)

# The fragments written literally, as every filtered statement must (plan 9.7).
_LIVE_IDS = "SELECT d.id FROM documents d WHERE d.customer_id = :c AND d.deleted_at IS NULL"
_LIVE_CURRENT_IDS = (
    "SELECT d.id FROM documents d WHERE d.customer_id = :c"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
)
_TRASHED_IDS = "SELECT d.id FROM documents d WHERE d.customer_id = :c AND d.deleted_at IS NOT NULL"
_LISTED_IDS = (
    "SELECT d.id FROM documents d WHERE d.customer_id = :c AND d.deleted_at IS NULL AND d.is_current"
)
_TRASHED_LISTED_IDS = (
    "SELECT d.id FROM documents d WHERE d.customer_id = :c AND d.deleted_at IS NOT NULL AND d.is_current"
)


def _state(db: Database, customer: str) -> dict[str, tuple[datetime | None, str | None]]:
    """Each document's (deleted_at, deleted_by)."""
    with db.customer(customer) as conn:
        rows = conn.execute(
            text("SELECT id, deleted_at, deleted_by FROM documents WHERE customer_id = :c"), {"c": customer}
        ).all()
    return {str(row[0]): (row[1], str(row[2]) if row[2] else None) for row in rows}


def _family(conn: Connection, customer: str, versions: int, **columns: object) -> list[str]:
    """A family of ``versions`` rows; the first is current. Returns the ids, first version first."""
    first = insert_document(conn, customer, **columns)
    rest = [
        insert_document(conn, customer, family_id=first, version_no=number, is_current=False, **columns)
        for number in range(2, versions + 1)
    ]
    return [first, *rest]


def _ids(conn: Connection, statement: str, customer: str) -> set[str]:
    return {str(row[0]) for row in conn.execute(text(statement), {"c": customer})}


# --- visibility fragments -----------------------------------------------------------


def test_visibility_fragments_have_their_exact_text_and_pick_the_right_rows(db: Database) -> None:
    assert visibility.LIVE == " AND d.deleted_at IS NULL"
    assert visibility.LIVE_CURRENT == " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    assert visibility.TRASHED == " AND d.deleted_at IS NOT NULL"
    assert visibility.LIVE in _LIVE_IDS
    assert visibility.LIVE_CURRENT in _LIVE_CURRENT_IDS
    assert visibility.TRASHED in _TRASHED_IDS
    assert visibility.LIVE not in _TRASHED_IDS

    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        current, older_view = _family(conn, customer, 2)
        reading = insert_document(conn, customer, status="received")
        binned = insert_document(conn, customer, deleted_at=NOW, deleted_by=admin)

        assert _ids(conn, _LIVE_IDS, customer) == {current, older_view, reading}
        assert _ids(conn, _LIVE_CURRENT_IDS, customer) == {current}
        assert _ids(conn, _TRASHED_IDS, customer) == {binned}


def test_the_list_fragments_have_their_exact_text_and_pick_one_row_per_family(db: Database) -> None:
    assert visibility.LISTED == " AND d.deleted_at IS NULL AND d.is_current"
    assert visibility.TRASHED_LISTED == " AND d.deleted_at IS NOT NULL AND d.is_current"
    assert visibility.LISTED in _LISTED_IDS
    assert visibility.TRASHED_LISTED in _TRASHED_LISTED_IDS

    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        current, _newer_reading = _family(conn, customer, 2)
        reading = insert_document(conn, customer, status="received")
        binned, _binned_older = _family(conn, customer, 2, deleted_at=NOW, deleted_by=admin)

        # Every state of the current version, and nothing of a newer one or of Trash.
        assert _ids(conn, _LISTED_IDS, customer) == {current, reading}
        assert _ids(conn, _TRASHED_LISTED_IDS, customer) == {binned}


def test_trash_statements_use_the_fragments() -> None:
    assert visibility.LIVE in trash._TRASH
    assert visibility.TRASHED in trash._RESTORE
    assert visibility.TRASHED in trash._PURGE_PICK
    assert visibility.TRASHED in trash._PURGE_LOCK
    assert visibility.TRASHED in folders._DETACH_TRASHED


# --- trash and restore ----------------------------------------------------------------


def test_trash_and_restore_act_on_the_whole_family(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        first, second, third = _family(conn, customer, 3)
        unrelated = insert_document(conn, customer)

    with db.customer(customer) as conn:
        assert trash_document(conn, customer, second, by=admin, now=NOW) is True
    assert _state(db, customer) == {
        first: (NOW, admin),
        second: (NOW, admin),
        third: (NOW, admin),
        unrelated: (None, None),
    }

    with db.customer(customer) as conn:
        assert restore_document(conn, customer, third) is True
    assert _state(db, customer) == dict.fromkeys((first, second, third, unrelated), (None, None))


def test_trash_keeps_the_first_time_and_restore_of_a_live_family_changes_nothing(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        first, second = _family(conn, customer, 2)
        trash_document(conn, customer, first, by=admin, now=NOW)
        assert trash_document(conn, customer, second, by=admin, now=NOW + timedelta(days=3)) is True
    assert _state(db, customer) == {first: (NOW, admin), second: (NOW, admin)}

    with db.customer(customer) as conn:
        restore_document(conn, customer, first)
        assert restore_document(conn, customer, second) is True
    assert _state(db, customer) == {first: (None, None), second: (None, None)}


def test_trash_and_restore_of_a_missing_or_other_customers_document_are_false(db: Database) -> None:
    customer, admin = new_customer(db)
    other, other_admin = new_customer(db)
    with db.customer(other) as conn:
        theirs = insert_document(conn, other)
        binned = insert_document(conn, other, deleted_at=NOW, deleted_by=other_admin)

    with db.customer(customer) as conn:
        for document in (str(uuid.uuid4()), theirs):
            assert trash_document(conn, customer, document, by=admin, now=NOW) is False
        for document in (str(uuid.uuid4()), binned):
            assert restore_document(conn, customer, document) is False
    assert _state(db, other) == {theirs: (None, None), binned: (NOW, other_admin)}


def test_lock_family_returns_the_family_id_or_none(db: Database) -> None:
    customer, _admin = new_customer(db)
    other, _other_admin = new_customer(db)
    with db.customer(other) as conn:
        theirs = insert_document(conn, other)
    with db.customer(customer) as conn:
        first, second = _family(conn, customer, 2)

        assert lock_family(conn, customer, second) == first
        assert lock_family(conn, customer, first) == first
        assert lock_family(conn, customer, theirs) is None
        assert lock_family(conn, customer, str(uuid.uuid4())) is None


def test_a_restore_waits_for_a_trash_of_the_same_family(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        first, second = _family(conn, customer, 2)
    session = f"trash-{uuid.uuid4().hex[:16]}"

    def restore_second() -> bool:
        with db.customer(customer) as conn:
            name_session(conn, session)
            return restore_document(conn, customer, second)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(customer) as conn:
            assert trash_document(conn, customer, first, by=admin, now=NOW) is True
            restoring = pool.submit(restore_second)
            wait_until_blocked(db, application_name=session)
        assert restoring.result(timeout=10) is True

    assert _state(db, customer) == {first: (None, None), second: (None, None)}


def test_a_family_that_grows_while_a_trash_waits_still_queues(db: Database) -> None:
    """Version 2 arrives, with an id below version 1's, while a trash waits for the family.

    The trash (paused between its two statements) locked only what its snapshot saw.
    A restore that starts later sees both rows. Unless both take the first version
    first, the restore holds version 2 while the trash needs it: a deadlock.
    """
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        first = insert_document(conn, customer, id="ffffffff-ffff-4fff-bfff-" + uuid.uuid4().hex[:12])
    smaller = "00000000-0000-4000-8000-" + uuid.uuid4().hex[:12]
    trasher, restorer = f"trash-{uuid.uuid4().hex[:16]}", f"trash-{uuid.uuid4().hex[:16]}"
    locked, go = threading.Event(), threading.Event()

    def trash_in_two_steps() -> None:
        with db.customer(customer) as conn:
            name_session(conn, trasher)
            lock_family(conn, customer, first)
            locked.set()
            go.wait(timeout=10)
            conn.execute(text(trash._TRASH), {"c": customer, "f": first, "by": admin, "now": NOW})

    def restore() -> bool:
        with db.customer(customer) as conn:
            name_session(conn, restorer)
            return restore_document(conn, customer, first)

    with ThreadPoolExecutor(max_workers=2) as pool:
        with db.customer(customer) as conn:  # the version insert, under the family lock
            lock_family(conn, customer, first)
            insert_document(conn, customer, id=smaller, family_id=first, version_no=2, is_current=False)
            trashing = pool.submit(trash_in_two_steps)
            wait_until_blocked(db, application_name=trasher)
        assert locked.wait(timeout=10)
        restoring = pool.submit(restore)
        wait_until_blocked(db, application_name=restorer)
        go.set()
        trashing.result(timeout=10)
        assert restoring.result(timeout=10) is True

    assert _state(db, customer) == {first: (None, None), smaller: (None, None)}


# --- purge ------------------------------------------------------------------------------


def test_customer_ids_with_trash_lists_only_customers_with_old_trash(db: Database) -> None:
    old, old_admin = new_customer(db)
    recent, recent_admin = new_customer(db)
    clean, _clean_admin = new_customer(db)
    with db.customer(old) as conn:
        insert_document(conn, old, deleted_at=BEFORE - timedelta(seconds=1), deleted_by=old_admin)
        insert_document(conn, old)
    with db.customer(recent) as conn:
        insert_document(conn, recent, deleted_at=BEFORE, deleted_by=recent_admin)
    with db.customer(clean) as conn:
        insert_document(conn, clean)

    with db.system() as conn:
        found = customer_ids_with_trash(conn, before=BEFORE)

    assert old in found
    assert recent not in found
    assert clean not in found
    assert len(found) == len(set(found))


def test_purge_next_family_returns_the_oldest_old_family_whole(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        oldest = _family(conn, customer, 2, deleted_at=BEFORE - timedelta(days=10), deleted_by=admin)
        older = _family(conn, customer, 1, deleted_at=BEFORE - timedelta(days=5), deleted_by=admin)
        _family(conn, customer, 2, deleted_at=BEFORE + timedelta(days=1), deleted_by=admin)
        _family(conn, customer, 2)

    for expected in (oldest, older):
        with db.customer(customer) as conn:
            picked = purge_next_family(conn, customer, before=BEFORE)
            assert sorted(picked) == sorted(expected)
            # The caller deletes the picked rows in the same transaction.
            conn.execute(
                text("DELETE FROM documents WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"),
                {"c": customer, "ids": picked},
            )
    with db.customer(customer) as conn:
        assert purge_next_family(conn, customer, before=BEFORE) == []
    assert len(_state(db, customer)) == 4


def test_purge_next_family_skips_a_family_locked_by_another_transaction(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        oldest = _family(conn, customer, 2, deleted_at=BEFORE - timedelta(days=10), deleted_by=admin)
        older = _family(conn, customer, 1, deleted_at=BEFORE - timedelta(days=5), deleted_by=admin)

    with db.customer(customer) as holder:
        assert lock_family(holder, customer, oldest[1]) == oldest[0]
        with db.customer(customer) as conn:
            # Without SKIP LOCKED this would wait on the holder; fail fast instead of hanging.
            conn.execute(text("SET LOCAL lock_timeout = '2s'"))
            assert purge_next_family(conn, customer, before=BEFORE) == older

    with db.customer(customer) as conn:
        assert sorted(purge_next_family(conn, customer, before=BEFORE)) == sorted(oldest)
