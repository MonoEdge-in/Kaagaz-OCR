"""Customer isolation for unpacking a zip (#522 PR 3, kaagaz/jobs/unpack.py). Marked
``isolation`` by conftest.py: the unpacking job makes folders and documents for its own customer
only, under row level security, and no other customer can see or reach them."""

from dataclasses import replace

import pytest
from sqlalchemy import Connection, text

from kaagaz.db import zip_unpacks
from kaagaz.db.engine import Database
from kaagaz.jobs.worker import JobFailed
from tests.db_support import insert_folder, new_customer
from tests.unpack_support import Unpacking
from tests.zip_builder import make_zip

pytestmark = pytest.mark.db
DATA = make_zip([("Top/a.csv", b"a\n1\n"), ("Top/Mid/b.csv", b"b\n2\n")])
_COUNTS = (
    "SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM folders),"
    " (SELECT count(*) FROM folder_uploads), (SELECT count(*) FROM folder_upload_items)"
)


def test_the_unpacking_makes_rows_for_its_own_customer_only(db: Database) -> None:
    a_customer, a_user = new_customer(db)
    b_customer, b_user = new_customer(db)
    a, b = Unpacking(db, a_customer, a_user), Unpacking(db, b_customer, b_user)
    record = a.upload(DATA)
    assert a.unpack(record, DATA) is True

    assert a.rows(_COUNTS) == [(3, 3, 1, 2)]  # the zip and its 2 files; its folder, Top and Mid
    assert b.rows(_COUNTS) == [(0, 0, 0, 0)]  # row level security: none of A's is B's to see
    with db.system() as conn:  # no customer set: the policy shows nothing at all
        assert [tuple(row) for row in conn.execute(text(_COUNTS))] == [(0, 0, 0, 0)]
    assert {job.customer_id for job in a.queue.jobs} == {a_customer}
    with db.customer(b_customer) as conn:
        assert zip_unpacks.unpacks(conn, b_customer, [record.document_id]) == {}


def test_a_job_naming_another_customer_finds_no_zip_and_makes_nothing(db: Database) -> None:
    a_customer, a_user = new_customer(db)
    b_customer, b_user = new_customer(db)
    a, b = Unpacking(db, a_customer, a_user), Unpacking(db, b_customer, b_user)
    record = a.upload(DATA)
    assert a.unpack(replace(record, customer_id=b_customer), DATA, customer_id=b_customer) is False
    assert a.rows(_COUNTS) == [(1, 0, 0, 0)]
    assert b.rows(_COUNTS) == [(0, 0, 0, 0)]


def test_another_customer_s_folder_is_never_a_place_to_unpack_into(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    a_customer, a_user = new_customer(db)
    b_customer, b_user = new_customer(db)
    a, b = Unpacking(db, a_customer, a_user), Unpacking(db, b_customer, b_user)
    with db.customer(b_customer) as conn:
        theirs = insert_folder(conn, b_customer, b_user, "B's folder")
    record = a.upload(DATA)
    real_place = zip_unpacks.place

    def elsewhere(conn: Connection, customer_id: str, document_id: str) -> zip_unpacks.Place | None:
        found = real_place(conn, customer_id, document_id)
        return None if found is None else replace(found, folder_id=theirs)

    monkeypatch.setattr(zip_unpacks, "place", elsewhere)
    with pytest.raises(JobFailed) as failed:
        a.unpack(record, DATA)
    assert failed.value.code == "folder_gone"  # A can't see B's folder: there is no such folder
    assert a.rows(_COUNTS) == [(1, 0, 0, 0)]
    assert b.rows(_COUNTS) == [(0, 1, 0, 0)]
