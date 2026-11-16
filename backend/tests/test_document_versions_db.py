"""A new version joins its family under the family lock (PRD 1 F6, plan 9.3 and 9.4).

``PgDocuments.add_version_if_absent`` and ``add_version`` against a real
PostgreSQL. The concurrency test forces the order with ``wait_until_blocked``:
the second upload waits on the family lock while the first one holds it, so the
result never depends on timing.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db import folders, trash
from kaagaz.db.documents import PgDocuments, add_version
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import DocumentRecord
from tests.db_support import (
    insert_document,
    insert_folder,
    name_session,
    new_customer,
    new_library,
    wait_until_blocked,
)

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _record(customer: str, **fields: Any) -> DocumentRecord:
    """A new upload's record, with new random bytes (a random fingerprint)."""
    sha256 = fields.pop("sha256", uuid.uuid4().hex * 2)
    return DocumentRecord(customer, str(uuid.uuid4()), sha256, 10, "pdf", **fields)


def _versions(db: Database, customer: str) -> list[tuple[str, str, int, bool]]:
    """Every document of the customer: (id, family_id, version_no, is_current), by family and number."""
    with db.customer(customer) as conn:
        rows = conn.execute(
            text("SELECT id, family_id, version_no, is_current FROM documents ORDER BY family_id, version_no")
        ).all()
    return [(str(row[0]), str(row[1]), int(row[2]), bool(row[3])) for row in rows]


def test_a_family_in_trash_or_no_document_of_this_customer_gets_no_version(db: Database) -> None:
    customer, user = new_customer(db)
    other, _ = new_customer(db)
    with db.customer(customer) as conn:
        binned = insert_document(conn, customer, deleted_at=NOW, deleted_by=user)
    with db.customer(other) as conn:
        theirs = insert_document(conn, other)
    documents = PgDocuments(db)
    for target in (binned, theirs, str(uuid.uuid4())):
        assert documents.add_version_if_absent(_record(customer), of_document_id=target) is None, target
    assert _versions(db, customer) == [(binned, binned, 1, True)]


def test_the_same_bytes_as_any_document_give_that_document_and_add_nothing(db: Database) -> None:
    library = new_library(db)
    customer, first = library["customer"], library["document"]
    with db.customer(customer) as conn:
        other = insert_document(conn, customer, sha256="e" * 64)
    result = PgDocuments(db).add_version_if_absent(_record(customer, sha256="e" * 64), of_document_id=first)
    assert result is not None
    existing, created = result
    assert (existing.document_id, existing.family_id, existing.version_no, created) == (
        other,
        other,
        1,
        False,
    )
    assert len(_versions(db, customer)) == 2
    with db.customer(customer) as conn:
        assert conn.execute(text("SELECT count(*) FROM document_tags")).scalar_one() == 1


def test_two_versions_at_once_get_2_and_3_never_the_same_number(db: Database) -> None:
    """The second upload waits on the family lock the first one holds, then sees
    version 2 and takes 3. Without the lock both would pick 2, and the unique
    index documents_family_version would refuse the second."""
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        first = insert_document(conn, customer)
    session = f"versions-{uuid.uuid4().hex[:16]}"

    def second_upload() -> tuple[DocumentRecord, bool] | None:
        with db.customer(customer) as conn:
            name_session(conn, session)
            return add_version(conn, _record(customer), of_document_id=first)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(customer) as conn:
            held = add_version(conn, _record(customer), of_document_id=first)
            waiting = pool.submit(second_upload)
            wait_until_blocked(db, application_name=session)
        waited = waiting.result(timeout=10)

    assert held is not None and waited is not None
    assert [(r.family_id, r.version_no, created) for r, created in (held, waited)] == [
        (first, 2, True),
        (first, 3, True),
    ]
    assert [(v[2], v[3]) for v in _versions(db, customer)] == [(1, True), (2, False), (3, False)]


def test_a_new_version_and_a_folder_made_private_meanwhile_both_finish(db: Database) -> None:
    """#601: a new version takes the tree lock before the family lock, as making a folder private
    does (the Private route takes the tree lock, then the folder, then its files' families). With
    the family lock first, the version would hold the family and wait for the folder (its
    trigger's FOR SHARE) while Private held the folder and waited for the family: a deadlock.
    A third session holds the family first, so both are queued before either can go on."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        bills = insert_folder(conn, customer, user, "Bills")
        inside = insert_folder(conn, customer, user, "2026", bills)
        first = insert_document(conn, customer, folder_id=inside)
    adding, private = (f"{name}-{uuid.uuid4().hex[:12]}" for name in ("version", "private"))
    added: list[tuple[DocumentRecord, bool] | None] = []

    def new_version() -> None:
        with db.customer(customer) as conn:
            name_session(conn, adding)
            added.append(add_version(conn, _record(customer), of_document_id=first))

    def make_private() -> None:
        with db.customer(customer) as conn:
            name_session(conn, private)
            folders._lock_tree(conn, customer)
            conn.execute(text("UPDATE folders SET private = true WHERE id = :f"), {"f": bills})

    with ThreadPoolExecutor(max_workers=2) as pool:
        with db.customer(customer) as conn:
            assert trash.lock_family(conn, customer, first) == first  # as saving a reading
            version = pool.submit(new_version)
            wait_until_blocked(db, application_name=adding)
            switch = pool.submit(make_private)
            wait_until_blocked(db, application_name=private)
        version.result(timeout=20)
        switch.result(timeout=20)

    assert added[0] is not None and added[0][1]
    with db.customer(customer) as conn:
        rows = conn.execute(
            text("SELECT private_folder_id FROM documents WHERE family_id = :f"), {"f": first}
        ).all()
    assert [str(row[0]) for row in rows] == [bills, bills]
