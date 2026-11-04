"""Folder upload (0071, #484) on PostgreSQL: up, down (0070's schema exactly) and up again,
and the checks that keep an item's state, failure code and document in step, and the limits
inside their ceilings. Customer separation: test_isolation_folder_uploads."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_folder, migrate, new_customer
from tests.folder_upload_support import new_folder_upload, path_key
from tests.test_migration_0004_db import _current, _head
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db
fresh = base.fresh
TABLES = {"folder_uploads", "folder_upload_folders", "folder_upload_items", "org_upload_limits"}
_ITEM = (
    "INSERT INTO folder_upload_items (customer_id, folder_upload_id, seq, path_key, name, folder_id,"
    " size_bytes, batch_id, state, failure_code, document_id)"
    " VALUES (:c, :u, :seq, :k, 'a.pdf', :f, 10, :b, :state, :code, :d)"
)
_COMMENT = text("SELECT obj_description('folders'::regclass, 'pg_class')")


def test_0071_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0070")
    before = base._schema(db)
    migrate(url, "head")
    assert _current(url) == _head()
    after = base._schema(db)
    assert base._names(after, "relations") >= TABLES
    with db.system() as conn:
        assert conn.execute(_COMMENT).scalar_one() == "Folders for documents, nested at most 100 levels."
    customer, user = new_customer(db)  # a row in each of the four tables, and a platform limit
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "A")
    _item(db, customer, new_folder_upload(db, customer, user, folder_ids=[folder]), folder)
    _owner(db, "INSERT INTO org_upload_limits (customer_id, max_gb) VALUES (:c, 2)", {"c": customer})
    _owner(db, "UPDATE platform_ai_settings SET folder_upload_max_gb = 3", {})
    base._downgrade(url, "0070")
    assert base._schema(db) == before
    with db.system() as conn:
        assert conn.execute(_COMMENT).scalar_one() == "Folders for documents, nested at most 5 levels."
    migrate(url, "head")
    assert base._schema(db) == after


def _item(db: Database, customer: str, upload: str, folder: str, **values: object) -> None:
    params = {"c": customer, "u": upload, "seq": 0, "k": path_key("A/a.pdf"), "f": folder}
    params |= {"b": str(uuid.uuid4()), "state": "waiting", "code": None, "d": None, **values}
    with db.customer(customer) as conn:
        conn.execute(text(_ITEM), params)


def test_items_state_failure_code_and_document_must_agree(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "A")
    upload = new_folder_upload(db, customer, user, folder_ids=[folder])
    document = str(uuid.uuid4())
    for wrong in (
        {"state": "failed"},  # failed with no code
        {"code": "too_large"},  # a code while waiting
        {"state": "stored"},  # stored with no document
        {"d": document},  # a document while waiting
        {"state": "failed", "code": "virus"},  # not a failure code
        {"k": "A/a.pdf"},  # a path, not its key
    ):
        with pytest.raises(DBAPIError):
            _item(db, customer, upload, folder, **wrong)
    _item(db, customer, upload, folder, state="failed", code="too_large")
    _item(db, customer, upload, folder, seq=1, k=path_key("A/b.pdf"), state="stored", d=document)
    _item(db, customer, upload, folder, seq=2, k=path_key("A/c\u202e.pdf"), state="failed", code="bad_name")
    with pytest.raises(DBAPIError):  # one item a path in one upload
        _item(db, customer, upload, folder, seq=3)


def test_limits_stay_inside_their_ceilings_and_an_org_row_names_one(db: Database) -> None:
    customer, _ = new_customer(db)
    row = (
        "INSERT INTO org_upload_limits (customer_id, max_files, max_gb, max_folders) VALUES (:c, :f, :g, :d)"
    )
    over = ((200_001, None, None), (None, 21, None), (None, None, 50_001), (0, None, None))
    for files, gb, folders in over:
        with pytest.raises(DBAPIError):
            _owner(db, row, {"c": customer, "f": files, "g": gb, "d": folders})
    with pytest.raises(DBAPIError):  # a row with no limit in it
        _owner(db, row, {"c": customer, "f": None, "g": None, "d": None})
    with pytest.raises(DBAPIError):
        _owner(db, "UPDATE platform_ai_settings SET folder_upload_max_gb = 21", {})
    _owner(db, row, {"c": customer, "f": 200_000, "g": 20, "d": 50_000})
