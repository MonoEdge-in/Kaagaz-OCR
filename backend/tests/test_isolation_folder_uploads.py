"""Folder upload tables (0061c, #484): customer B sees none of customer A's uploads, folders,
items or limits, and can write none for A, even with A's exact ids."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests.db_support import insert_folder, new_customer
from tests.folder_upload_support import new_folder_upload, path_key
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_ITEM = (
    "INSERT INTO folder_upload_items (customer_id, folder_upload_id, seq, path_key, name, folder_id,"
    " size_bytes, batch_id) VALUES (:c, :u, :seq, :k, 'a.pdf', :f, 10, :b)"
)


def test_another_customer_sees_and_writes_nothing_of_a_folder_upload(db: Database) -> None:
    a, a_user = new_customer(db)
    b, b_user = new_customer(db)
    with db.customer(a) as conn:
        folder = insert_folder(conn, a, a_user, "A")
    upload = new_folder_upload(db, a, a_user, folder_ids=[folder])
    item = {"u": upload, "seq": 0, "k": path_key("A/a.pdf"), "f": folder, "b": str(uuid.uuid4())}
    with db.customer(a) as conn:
        conn.execute(text(_ITEM), {"c": a, **item})
    _owner(db, "INSERT INTO org_upload_limits (customer_id, max_files) VALUES (:c, 7)", {"c": a})

    for table in ("folder_uploads", "folder_upload_folders", "folder_upload_items", "org_upload_limits"):
        with db.customer(b) as conn:
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0, table  # noqa: S608
        with db.customer(a) as conn:
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 1, table  # noqa: S608

    with pytest.raises(DBAPIError), db.customer(b) as conn:  # an item for A's upload, from B
        conn.execute(text(_ITEM), {"c": a, **item, "seq": 1, "k": path_key("A/b.pdf")})
    with pytest.raises(DBAPIError), db.customer(b) as conn:  # B's own customer, A's upload: the key refuses
        conn.execute(text(_ITEM), {"c": b, **item, "seq": 1, "k": path_key("A/b.pdf")})
    with pytest.raises(DBAPIError), db.customer(b) as conn:
        conn.execute(
            text(
                "INSERT INTO folder_upload_folders (customer_id, folder_upload_id, idx, folder_id)"
                " VALUES (:c, :u, 1, :f)"
            ),
            {"c": a, "u": upload, "f": folder},
        )
    with pytest.raises(DBAPIError), db.customer(b) as conn:  # the same upload id is B's own
        conn.execute(
            text(
                "INSERT INTO folder_uploads (id, customer_id, user_id, target, source_name, root_name,"
                " merge, file_count, total_bytes, skipped_count) VALUES (:u, :c, :usr, 'folder', 'A', 'A',"
                " true, 1, 1, 0)"
            ),
            {"u": upload, "c": a, "usr": b_user},
        )
    with db.customer(b) as conn:  # a reused id is no clash for another customer (PK customer_id, id)
        conn.execute(
            text(
                "INSERT INTO folder_uploads (id, customer_id, user_id, target, source_name, root_name,"
                " merge, file_count, total_bytes, skipped_count) VALUES (:u, :c, :usr, 'folder', 'A', 'A',"
                " true, 1, 1, 0)"
            ),
            {"u": upload, "c": b, "usr": b_user},
        )
        moved = "UPDATE folder_upload_items SET state = 'waiting' WHERE folder_upload_id = :u"
        assert conn.execute(text(moved), {"u": upload}).rowcount == 0
    with pytest.raises(DBAPIError), db.customer(a) as conn:  # only the operator's function writes limits
        conn.execute(text("UPDATE org_upload_limits SET max_files = 8 WHERE customer_id = :c"), {"c": a})
    with pytest.raises(DBAPIError), db.customer(a) as conn:  # never who set them
        conn.execute(text("SELECT updated_by FROM org_upload_limits"))
