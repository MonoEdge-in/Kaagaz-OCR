"""Migration 0080 (#522 PR 3, Tushar, 6 Nov 2026): a folder upload may be a zip's
(``folder_uploads.zip_document_id``, one upload a zip) and an item may fail with why a file
inside a zip was refused. Up, down (the zips' uploads deleted, 0061c's codes back) and up again:
the schema, comments and grants as before each time."""

import uuid

import pytest

from kaagaz.db.engine import Database
from kaagaz.ingestion import unzip
from tests import test_migration_0002_db as base
from tests.db_support import insert_folder, migrate, new_customer
from tests.folder_upload_support import new_folder_upload, path_key
from tests.test_migration_0023_db import as_owner
from tests.test_migration_0037 import _load
from tests.test_migration_0070_db import _refused
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0080 = _load("0080_zip_unpack.py")
_ITEM = (
    "INSERT INTO folder_upload_items (customer_id, folder_upload_id, seq, path_key, name, folder_id,"
    " size_bytes, batch_id, state, failure_code)"
    " VALUES (:c, :u, :seq, :k, 'a.csv', :f, 1, :b, 'failed', :code)"
)
_TIE = "UPDATE folder_uploads SET zip_document_id = :d WHERE id = :u"


def test_the_revision_follows_0078_and_names_every_code_a_zip_s_file_fails_with() -> None:
    assert (M0080.revision, M0080.down_revision) == ("0080", "0078")  # no 0079 when written
    assert set(unzip.REASONS) == set(M0080.FAILURES)  # every code has its plain words
    assert set(M0080.FAILURES_0061C) < set(M0080.FAILURES)


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0078")
    at_0078 = _state(db)
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "A")
    person = new_folder_upload(db, customer, user, folder_ids=[folder])
    zipped = new_folder_upload(db, customer, user, folder_ids=[folder])
    item = {"c": customer, "f": folder, "b": str(uuid.uuid4())}
    assert (
        _refused(db, _ITEM, {**item, "u": person, "seq": 0, "k": path_key("x"), "code": "encrypted"})
        == "23514"
    )

    migrate(url, "0080")
    at_0080 = _state(db)
    zip_document = str(uuid.uuid4())
    as_owner(db, _TIE, {"d": zip_document, "u": zipped})
    for seq, code in enumerate(M0080.FAILURES):
        as_owner(db, _ITEM, {**item, "u": zipped, "seq": seq, "k": path_key(code), "code": code})
    assert _refused(db, _ITEM, {**item, "u": person, "seq": 0, "k": path_key("y"), "code": "nope"}) == "23514"
    assert _refused(db, _TIE, {"d": zip_document, "u": person}) == "23505"  # one upload a zip

    base._downgrade(url, "0078")
    assert _state(db) == at_0078
    assert as_owner(db, "SELECT id FROM folder_uploads ORDER BY id") == [(uuid.UUID(person),)]
    assert as_owner(db, "SELECT count(*) FROM folder_upload_items") == [(0,)]

    migrate(url, "0080")
    assert _state(db) == at_0080
