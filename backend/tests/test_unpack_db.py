"""A zip upload unpacked into a folder on PostgreSQL (#522 PR 3, kaagaz/jobs/unpack.py): its
structure kept, each file virus checked on its own and stored through the upload service with
the duplicate rule, every unsafe file refused with its reason, the limits refusing the whole zip
before anything is made, and the result line on the zip's own document. Customer separation:
test_isolation_unpack_db."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from kaagaz.adapters.scanner import EICAR
from kaagaz.api.views import unpack_view, zip_message
from kaagaz.db import folder_upload_items as items_sql
from kaagaz.db import zip_unpacks
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.folder_uploads import FolderUploadLimits
from kaagaz.ingestion import unzip
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import StorageRoom
from kaagaz.jobs import unpack as unpack_module
from kaagaz.jobs.worker import JobFailed
from kaagaz.scanning.gate import ScannerError
from tests.db_support import insert_folder, new_customer
from tests.unpack_support import Unpacking
from tests.zip_builder import make_zip, make_zip_with, mark_encrypted, with_link

pytestmark = pytest.mark.db
THREE_LEVELS = [
    ("Top/a.csv", b"a,b\n1,2\n"),
    ("Top/Mid/b.csv", b"c,d\n3,4\n"),
    ("Top/Mid/Low/c.txt", b"hello, world"),
]


@pytest.fixture
def unpacking(db: Database) -> Iterator[Unpacking]:
    customer, user = new_customer(db)
    yield Unpacking(db, customer, user)


def _documents(u: Unpacking) -> list[tuple[Any, ...]]:
    return sorted(u.rows("SELECT display_name, detected_type, status FROM documents WHERE customer_id = :c"))


def test_a_three_level_zip_becomes_a_folder_of_the_same_shape_and_the_zip_is_kept_in_it(
    unpacking: Unpacking,
) -> None:
    data = make_zip(THREE_LEVELS, folders=["Top/Empty/"])
    record = unpacking.upload(data, "Archive 2026.zip")
    assert unpacking.unpack(record, data) is True
    root = unpacking.root_of(record)
    assert unpacking.rows(
        "SELECT name, parent_id FROM folders WHERE customer_id = :c AND id = :r", r=root
    ) == [
        ("Archive 2026", None)  # where it was uploaded: the top level
    ]
    assert unpacking.tree(root) == {
        "": ["Archive 2026.zip"],  # the zip itself, kept as a file in its folder
        "/Top": ["a.csv"],
        "/Top/Empty": [],
        "/Top/Mid": ["b.csv"],
        "/Top/Mid/Low": ["c.txt"],
    }
    # Each file is a document of its own, queued to be read with its own kind, never auto-filed.
    queued = {job.document_id for job in unpacking.queue.jobs}
    inner = unpacking.rows(
        "SELECT id, autofile, uploaded_by, read_requested_by FROM documents"
        " WHERE customer_id = :c AND id <> :z",
        z=record.document_id,
    )
    assert {str(row[0]) for row in inner} == queued and len(queued) == 3
    assert {(row[1], str(row[2]), str(row[3])) for row in inner} == {
        (None, unpacking.user_id, unpacking.user_id)
    }
    # Every file was virus checked on its own.
    assert sorted(unpacking.scanner.scanned) == sorted(data for _, data in THREE_LEVELS)
    found = unpacking.result(record)
    assert found is not None
    assert (found.file_count, found.stored, found.duplicate, found.failed, found.waiting) == (3, 3, 0, 0, 0)
    assert zip_message("ready", None, found) == 'Unpacked: 3 files into folder "Archive 2026".'
    assert unpacking.beats >= 3


def test_one_file_the_scanner_flags_is_refused_and_the_rest_kept(unpacking: Unpacking) -> None:
    data = make_zip([("ok.csv", b"a\n1\n"), ("eicar.txt", EICAR), ("also.csv", b"b\n2\n")])
    record = unpacking.upload(data)
    unpacking.unpack(record, data)
    assert [name for name, *_ in _documents(unpacking)] == ["Archive.zip", "also.csv", "ok.csv"]
    found = unpacking.result(record)
    assert found is not None
    assert (found.stored, found.failed, found.skipped) == (2, 1, [("eicar.txt", "virus_found")])
    assert zip_message("ready", None, found) == (
        'Unpacked: 2 files into folder "Archive", 1 skipped (1 failed the virus check).'
    )
    view = unpack_view(found)
    assert view is not None
    assert view["skipped_files"] == [
        {"name": "eicar.txt", "code": "virus_found", "reason": "It failed the virus check."}
    ]
    assert unpacking.storage.count() == 3  # the zip and the two clean files; never the flagged one


def test_unsafe_files_are_refused_each_with_its_reason_and_never_stored(unpacking: Unpacking) -> None:
    link = with_link("statement.pdf")
    plain = make_zip_with(
        [
            ("../escape.csv", b"x\n1\n"),
            ("/etc/passwd.csv", b"y\n2\n"),
            ("C:/win.csv", b"z\n3\n"),
            link,
            ("locked.csv", b"secret\n1\n"),
            ("inner.zip", make_zip([("deep.csv", b"d\n4\n")])),
            ("Fine/ok.csv", b"ok\n5\n"),
        ]
    )
    data = mark_encrypted(plain, "locked.csv")
    record = unpacking.upload(data)
    unpacking.unpack(record, data)
    found = unpacking.result(record)
    assert found is not None
    assert dict(found.skipped) == {
        "escape.csv": "unsafe_path",
        "passwd.csv": "unsafe_path",
        "win.csv": "unsafe_path",
        "statement.pdf": "link",
        "locked.csv": "encrypted",
    }
    assert (found.stored, found.failed) == (2, 5)
    assert zip_message("ready", None, found) == (
        'Unpacked: 2 files into folder "Archive", 5 skipped (3 unsafe path, 1 password-protected, 1 link).'
    )
    view = unpack_view(found)
    assert view is not None
    reasons = {row["name"]: row["reason"] for row in view["skipped_files"]}
    assert reasons["locked.csv"] == "It has a password. Remove the password, then upload it again."
    # Never a path from the zip: nothing outside the zip's folder, and the nested zip kept whole.
    root = unpacking.root_of(record)
    assert unpacking.tree(root) == {"": ["Archive.zip", "inner.zip"], "/Fine": ["ok.csv"]}


def test_a_nested_zip_is_stored_as_a_file_and_never_unpacked(unpacking: Unpacking) -> None:
    inner = make_zip([("deep.csv", b"d\n4\n")])
    data = make_zip([("inner.zip", inner)])
    record = unpacking.upload(data)
    unpacking.unpack(record, data)
    nested = next(job for job in unpacking.queue.jobs)
    stored = unpacking.rows("SELECT detected_type FROM documents WHERE id = :d", d=nested.document_id)
    assert stored == [("zip",)]
    before = len(unpacking.rows("SELECT id FROM folders WHERE customer_id = :c"))
    # Its own job comes to the unpacker too: kept as a file, nothing made.
    nested_record = PgDocuments(unpacking.db).get(unpacking.customer_id, nested.document_id)
    assert nested_record is not None
    assert unpacking.unpack(nested_record, inner) is False
    assert len(unpacking.rows("SELECT id FROM folders WHERE customer_id = :c")) == before
    assert zip_message("needs_reader", None, None) == (
        "Kept as a file, not unpacked: Kaagaz unpacks a zip only when it is uploaded on its own."
    )


def test_duplicates_in_the_zip_follow_the_duplicate_rule(unpacking: Unpacking) -> None:
    unpacking.upload(b"already,here\n1,2\n", "earlier.csv")
    data = make_zip(
        [("a/one.csv", b"same\n1\n"), ("b/two.csv", b"same\n1\n"), ("c.csv", b"already,here\n1,2\n")]
    )
    record = unpacking.upload(data)
    unpacking.unpack(record, data)
    found = unpacking.result(record)
    assert found is not None
    assert (found.stored, found.duplicate, found.failed) == (1, 2, 0)
    assert zip_message("ready", None, found) == 'Unpacked: 1 file into folder "Archive", 2 already in Kaagaz.'
    assert len(unpacking.queue.jobs) == 1  # nothing new stored for a duplicate


def test_a_second_zip_of_the_same_name_gets_a_folder_of_its_own(unpacking: Unpacking) -> None:
    with unpacking.db.customer(unpacking.customer_id) as conn:
        insert_folder(conn, unpacking.customer_id, unpacking.user_id, "Archive")
    data = make_zip([("a.csv", b"x\n1\n")])
    record = unpacking.upload(data)
    unpacking.unpack(record, data)
    found = unpacking.result(record)
    assert found is not None and found.root_name == "Archive (2)"


def test_a_zip_in_a_folder_is_unpacked_there_and_past_100_levels_its_files_are_refused(
    unpacking: Unpacking,
) -> None:
    parent = None
    with unpacking.db.customer(unpacking.customer_id) as conn:
        for level in range(98):
            parent = insert_folder(conn, unpacking.customer_id, unpacking.user_id, f"L{level}", parent)
    data = make_zip([("a.csv", b"x\n1\n"), ("one/b.csv", b"y\n2\n"), ("one/two/c.csv", b"z\n3\n")])
    record = unpacking.upload(data, folder_id=parent)
    unpacking.unpack(record, data)
    root = unpacking.root_of(record)
    assert parent is not None
    assert unpacking.rows("SELECT parent_id FROM folders WHERE id = :r", r=root) == [(uuid.UUID(parent),)]
    assert unpacking.tree(root) == {"": ["Archive.zip", "a.csv"], "/one": ["b.csv"]}  # depth 99, 100
    found = unpacking.result(record)
    assert found is not None and found.skipped == [("c.csv", "too_deep")]


@pytest.mark.parametrize(
    ("limits", "room", "code"),
    [
        (FolderUploadLimits(max_files=2, max_bytes=10**9, max_folders=100), None, "zip_too_many_files"),
        (FolderUploadLimits(max_files=100, max_bytes=10**9, max_folders=1), None, "zip_too_many_folders"),
        (FolderUploadLimits(max_files=100, max_bytes=10, max_folders=100), None, "zip_too_large"),
        (None, StorageRoom(limit_bytes=10_000, used_bytes=9_990), "storage_full"),
    ],
)
def test_a_zip_past_a_limit_is_refused_whole_before_anything_is_made(
    unpacking: Unpacking,
    monkeypatch: pytest.MonkeyPatch,
    limits: FolderUploadLimits | None,
    room: StorageRoom | None,
    code: str,
) -> None:
    data = make_zip(THREE_LEVELS)
    record = unpacking.upload(data)
    if limits is not None:
        monkeypatch.setattr(unpack_module, "effective_limits", lambda conn, customer: limits)
    if room is not None:
        monkeypatch.setattr(unpack_module, "storage_room", lambda conn, customer: room)
    with pytest.raises(JobFailed) as failed:
        unpacking.unpack(record, data)
    assert (failed.value.code, failed.value.retryable) == (code, False)
    assert unpacking.rows("SELECT id FROM folders WHERE customer_id = :c") == []
    assert unpacking.result(record) is None
    assert len(_documents(unpacking)) == 1 and unpacking.queue.jobs == []


def test_a_zip_bomb_is_refused_whole_by_its_overall_ratio(unpacking: Unpacking) -> None:
    """Each file is at most RATIO_FLOOR, so none is too compressed alone; together they unpack to
    far more than 100 times the zip."""
    data = make_zip([(f"zeros{n}.txt", b"0" * (1024 * 1024)) for n in range(4)])
    assert len(data) * 100 < 4 * 1024 * 1024
    record = unpacking.upload(data)
    with pytest.raises(JobFailed) as failed:
        unpacking.unpack(record, data)
    assert failed.value.code == "zip_too_compressed"
    assert unpacking.rows("SELECT id FROM folders WHERE customer_id = :c") == []


def test_the_50000_entry_limit_and_a_damaged_zip_refuse_it_whole(
    unpacking: Unpacking, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = make_zip(THREE_LEVELS)
    record = unpacking.upload(data)
    monkeypatch.setattr(unzip, "MAX_ENTRIES", 2)
    with pytest.raises(JobFailed) as failed:
        unpacking.unpack(record, data)
    assert failed.value.code == "zip_too_many_files"
    monkeypatch.undo()
    with pytest.raises(JobFailed) as failed:
        unpacking.unpack(record, data[:-30])  # the end record cut off
    assert failed.value.code == "zip_damaged"
    assert unpacking.rows("SELECT id FROM folders WHERE customer_id = :c") == []


def test_a_failure_while_planning_leaves_no_folder_at_all(
    unpacking: Unpacking, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The folders, the upload and its items are one transaction: a refusal after the folders are
    made (here, registering the files) rolls every one of them back."""
    data = make_zip(THREE_LEVELS)
    record = unpacking.upload(data)

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise items_sql.ItemsRefused("too_many_files")

    monkeypatch.setattr(items_sql, "register_items", refuse)
    with pytest.raises(JobFailed) as failed:
        unpacking.unpack(record, data)
    assert failed.value.code == "zip_too_many_files"
    assert unpacking.rows("SELECT id FROM folders WHERE customer_id = :c") == []
    assert unpacking.rows("SELECT id FROM folder_uploads WHERE customer_id = :c") == []


def test_a_failure_part_way_leaves_the_tree_with_its_line_and_a_run_again_finishes_it(
    unpacking: Unpacking, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = make_zip(THREE_LEVELS)
    record = unpacking.upload(data)
    put = unpacking.storage.put
    stored: list[str] = []

    def put_once(customer_id: str, document_id: str, stream: Any) -> None:
        if stored:
            raise BackendUnavailable("storage down")
        stored.append(document_id)
        put(customer_id, document_id, stream)

    monkeypatch.setattr(unpacking.storage, "put", put_once)
    with pytest.raises(BackendUnavailable):
        unpacking.unpack(record, data)
    found = unpacking.result(record)
    assert found is not None
    assert (found.stored, found.waiting) == (1, 2)
    assert zip_message("clean", None, found) == 'Unpacking: 1 of 3 files into folder "Archive"…'
    assert zip_message("failed", "retries_exhausted", found) == (
        'Unpacking stopped after 1 file into folder "Archive".'
        " Unpack it again, or try another copy of the zip."
    )
    folders = unpacking.rows("SELECT id FROM folders WHERE customer_id = :c ORDER BY id")

    monkeypatch.undo()
    assert unpacking.unpack(record, data) is True
    again = unpacking.result(record)
    assert again is not None and (again.stored, again.waiting) == (3, 0)
    assert unpacking.rows("SELECT id FROM folders WHERE customer_id = :c ORDER BY id") == folders
    assert unpacking.tree(unpacking.root_of(record))["/Top/Mid/Low"] == ["c.txt"]
    # Run again once done (a Read again): nothing new.
    jobs = len(unpacking.queue.jobs)
    assert unpacking.unpack(record, data) is True
    assert len(unpacking.queue.jobs) == jobs and len(_documents(unpacking)) == 4


def test_the_scanner_down_is_tried_again_and_a_renamed_zip_keeps_its_folder(unpacking: Unpacking) -> None:
    data = make_zip(THREE_LEVELS)
    record = unpacking.upload(data)

    class Down:
        def scan(self, data: bytes, *, name: str | None = None) -> Any:
            raise ScannerError("down")

    scanner = unpacking.scanner
    unpacking.scanner = Down()  # type: ignore[assignment]
    with pytest.raises(JobFailed) as failed:
        unpacking.unpack(record, data)
    assert (failed.value.code, failed.value.retryable) == ("scanner_unavailable", True)
    unpacking.scanner = scanner
    unpacking.execute("UPDATE documents SET display_name = 'Renamed.zip' WHERE id = :d", d=record.document_id)
    assert unpacking.unpack(record, data) is True
    found = unpacking.result(record)
    assert found is not None and (found.root_name, found.stored) == ("Archive", 3)


def test_a_zip_s_unpack_is_not_listed_with_a_person_s_folder_uploads(unpacking: Unpacking) -> None:
    data = make_zip(THREE_LEVELS)
    record = unpacking.upload(data)
    unpacking.unpack(record, data)
    with unpacking.db.customer(unpacking.customer_id) as conn:
        assert items_sql.summaries(conn, unpacking.customer_id, user_id=unpacking.user_id) == []
        upload = zip_unpacks.unpack_id(record.document_id)
        assert items_sql.owned(conn, unpacking.customer_id, upload, user_id=unpacking.user_id)


def test_a_zip_of_at_most_1_mb_is_never_too_compressed(unpacking: Unpacking) -> None:
    """Approved by Tushar (6 Nov 2026): a file, or a whole zip, of at most 1 MB unpacked is never
    held to 100:1; blank pages and small text pack far better, and no bomb is that small."""
    data = make_zip([(f"blank{n}.txt", bytes([65 + n]) * (300 * 1024)) for n in range(3)])
    assert len(data) * 100 < 900 * 1024  # far more than 100:1
    record = unpacking.upload(data)
    assert unpacking.unpack(record, data) is True
    found = unpacking.result(record)
    assert found is not None and (found.stored, found.failed) == (3, 0)
    assert zip_message("failed", "zip_over_limit", None) == (
        "Zip files can be up to 1 GB. Unzip it on your computer and upload the folder instead."
    )
