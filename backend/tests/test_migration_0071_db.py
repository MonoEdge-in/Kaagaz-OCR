"""Folder upload (0071, #484) on PostgreSQL: both ways to it, down across it and up again, and
the checks that keep an item's state, failure code and document in step, and the limits inside
their ceilings. Customer separation: test_isolation_folder_uploads.

Changed on purpose (#484, production at 0061c): 0071 merges production's 0061c
(production-with-folder-upload's file, byte for byte) with 0070 and adds nothing; 0072 adds what
Testing-Env has more. A fresh database, production's (0061c, with uploads in it) and the test
server's (0071 as #509 merged it, with uploads) end on the same schema; down across 0071 leaves
0070 and 0061c."""

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import database_url, insert_folder, migrate, new_customer
from tests.folder_upload_support import new_folder_upload, path_key
from tests.test_migration_0004_db import _current
from tests.test_migration_0034 import _load
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db
fresh = base.fresh
# Changed on purpose (#516, 0075): the head merges production's 0061d too, which a downgrade
# across 0071 keeps; these paths stop at 0074, the line's top before that merge
# (test_migration_0075_db goes on from here).
TOP = "0074"
TABLES = {"folder_uploads", "folder_upload_folders", "folder_upload_items", "org_upload_limits"}
_ITEM = (
    "INSERT INTO folder_upload_items (customer_id, folder_upload_id, seq, path_key, name, folder_id,"
    " size_bytes, batch_id, state, failure_code, document_id)"
    " VALUES (:c, :u, :seq, :k, 'a.pdf', :f, 10, :b, :state, :code, :d)"
)
_COMMENT = text("SELECT obj_description('folders'::regclass, 'pg_class')")
# Beyond base._schema: the function bodies, every comment and the column grants.
_MORE = {
    "bodies": "SELECT proname, pg_get_function_identity_arguments(oid), md5(prosrc) FROM pg_proc"
    " WHERE pronamespace = 'public'::regnamespace AND proname LIKE 'kaagaz%'",
    "comments": "SELECT c.relname, coalesce(a.attname::text, ''), d.description FROM pg_description d"
    " JOIN pg_class c ON c.oid = d.objoid AND d.classoid = 'pg_class'::regclass"
    " LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.objsubid AND d.objsubid > 0"
    " WHERE c.relnamespace = 'public'::regnamespace",
    "function_comments": "SELECT p.proname, pg_get_function_identity_arguments(p.oid), d.description"
    " FROM pg_description d JOIN pg_proc p ON p.oid = d.objoid AND d.classoid = 'pg_proc'::regclass"
    " WHERE p.pronamespace = 'public'::regnamespace",
    "policy_comments": "SELECT p.polrelid::regclass::text, p.polname, d.description FROM pg_description d"
    " JOIN pg_policy p ON p.oid = d.objoid AND d.classoid = 'pg_policy'::regclass",
    "column_grants": "SELECT c.relname, a.attname, a.attacl::text FROM pg_attribute a"
    " JOIN pg_class c ON c.oid = a.attrelid"
    " WHERE c.relnamespace = 'public'::regnamespace AND a.attacl IS NOT NULL",
}
# One upload as the owner sees it: its folders, items, registered files and bytes, the two limits.
_ROWS = (
    "SELECT (SELECT count(*) FROM folder_upload_folders WHERE folder_upload_id = :u),"
    " (SELECT count(*) FROM folder_upload_items WHERE folder_upload_id = :u),"
    " (SELECT items_registered FROM folder_uploads WHERE id = :u),"
    " (SELECT bytes_registered FROM folder_uploads WHERE id = :u),"
    " (SELECT max_gb FROM org_upload_limits WHERE customer_id = :c),"
    " (SELECT folder_upload_max_gb FROM platform_ai_settings)"
)
_COUNTED = "SELECT items_counted FROM folder_uploads WHERE id = :u"


def _state(db: Database) -> dict[str, list[tuple[Any, ...]]]:
    state = base._schema(db)
    with db.system() as conn:
        for part, query in _MORE.items():
            state[part] = sorted(tuple(row) for row in conn.execute(text(query)))
    return state


def _heads(url: str) -> list[str]:
    """The database's revisions, as ``alembic current`` lists them."""
    return sorted(line.split()[0] for line in _current(url).splitlines())


def _upload_with_rows(db: Database) -> tuple[str, str]:
    """A row in each of the four tables and a platform limit: an upload of 3 files, 2 of them
    registered (one stored, one failed; 20 bytes), written with 0061c's columns only."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "A")
    upload = new_folder_upload(db, customer, user, folder_ids=[folder], file_count=3, total_bytes=30)
    _item(db, customer, upload, folder, state="stored", d=str(uuid.uuid4()))
    _item(db, customer, upload, folder, seq=1, k=path_key("A/b.pdf"), state="failed", code="too_large")
    registered = "UPDATE folder_uploads SET items_registered = 2, bytes_registered = 20 WHERE id = :u"
    _owner(db, registered, {"u": upload})
    _owner(db, "INSERT INTO org_upload_limits (customer_id, max_gb) VALUES (:c, 2)", {"c": customer})
    _owner(db, "UPDATE platform_ai_settings SET folder_upload_max_gb = 3", {})
    return customer, upload


def _rows(db: Database, customer: str, upload: str) -> tuple[Any, ...]:
    return tuple(_owner(db, _ROWS, {"c": customer, "u": upload})[0])


def _empty_again(url: str, db: Database) -> None:
    """The fresh database dropped and made again, empty."""
    db.engine.dispose()
    base._recreate(database_url(), str(make_url(url).database), create=True)


def _normal(url: str, db: Database) -> tuple[dict[str, list[tuple[Any, ...]]], ...]:
    """The normal path's states (a fresh database): at 0070 and 0061c, at 0071 and at 0074; then
    the database empty again."""
    migrate(url, "0070")
    migrate(url, "0061c")
    parents = _state(db)
    migrate(url, "0071")
    at_0071 = _state(db)
    migrate(url, TOP)
    at_head = _state(db)
    _empty_again(url, db)
    return parents, at_0071, at_head


def _as_509_left_it(url: str, db: Database) -> None:
    """The test server: 0070, then 0071 as #509 merged it (revision "0071", down "0070"). That
    file's statements were 0061c's byte for byte (only its revision ids and its module's name
    differ: ``git show 5518439e:backend/migrations/versions/0071_folder_upload.py``), so they are
    run straight from 0061c's file, in one transaction as alembic did, and the one version row
    says 0071."""
    migrate(url, "0070")
    statements = _load("0061c_folder_upload.py").UPGRADE
    with db.engine.begin() as conn:  # the owner, as the migrations run
        for statement in statements:
            conn.execute(text(statement))
        conn.execute(text("UPDATE alembic_version SET version_num = '0071'"))


def test_0071_is_a_merge_alone_and_0072_up_down_up(fresh: tuple[str, Database]) -> None:
    """The normal path, a fresh database: head runs 0070, production's 0061c, 0071 (the merge,
    nothing more), then 0072's "Add new files only". Down to 0071 and to 0070 and up again."""
    url, db = fresh
    migrate(url, "0070")
    assert base._names(base._schema(db), "relations").isdisjoint(TABLES)
    migrate(url, "0061c")
    parents = _state(db)
    migrate(url, "0071")
    assert _heads(url) == ["0071"]
    assert _state(db) == parents  # a merge: nothing added
    migrate(url, TOP)
    assert _heads(url) == [TOP]
    after = _state(db)
    assert base._names(after, "relations") >= TABLES
    with db.system() as conn:
        assert conn.execute(_COMMENT).scalar_one() == "Folders for documents, nested at most 100 levels."
    customer, upload = _upload_with_rows(db)
    base._downgrade(url, "0071")
    assert _heads(url) == ["0071"]
    assert _state(db) == parents
    assert _rows(db, customer, upload) == (1, 2, 2, 20, 2, 3)
    migrate(url, TOP)
    assert _state(db) == after
    assert _owner(db, _COUNTED, {"u": upload}) == [(2,)]
    base._downgrade(url, "0070")
    assert _heads(url) == ["0061c", "0070"]
    assert _state(db) == parents
    assert _rows(db, customer, upload) == (1, 2, 2, 20, 2, 3)  # 0061c's tables stay
    migrate(url, TOP)
    assert _state(db) == after
    assert _owner(db, _COUNTED, {"u": upload}) == [(2,)]


def test_the_test_server_at_509s_0071_migrates_to_the_same_schema_and_keeps_its_uploads(
    fresh: tuple[str, Database],
) -> None:
    """The test server ran 0071 as #509 merged it (the folder tables straight after 0070) and
    has uploads. That is 0071's state of the other paths, so head runs 0072 (and on) alone and
    ends on the normal path's schema, ``items_counted`` filled. Down and up again keeps the rows."""
    url, db = fresh
    _, normal_0071, normal = _normal(url, db)

    _as_509_left_it(url, db)
    assert _heads(url) == ["0071"]
    assert _state(db) == normal_0071
    customer, upload = _upload_with_rows(db)
    migrate(url, TOP)
    assert _heads(url) == [TOP]
    assert _state(db) == normal
    assert _rows(db, customer, upload) == (1, 2, 2, 20, 2, 3)
    assert _owner(db, _COUNTED, {"u": upload}) == [(2,)]

    base._downgrade(url, "0071")
    assert _heads(url) == ["0071"]
    assert _state(db) == normal_0071
    migrate(url, TOP)
    assert _state(db) == normal
    assert _owner(db, _COUNTED, {"u": upload}) == [(2,)]


def test_production_at_0061c_migrates_to_the_same_schema_and_keeps_its_uploads(
    fresh: tuple[str, Database],
) -> None:
    """Production ran production-with-folder-upload (0061c) and has uploads. The release runs
    0061 ... 0070 with the folder tables there, the merge 0071, then 0072 adds only
    ``items_counted`` (the files registered, all counted against the limits there): the schema
    and every function body as the normal path's. Down and up again across 0071 keeps the rows."""
    url, db = fresh
    normal_parents, normal_0071, normal = _normal(url, db)

    migrate(url, "0061c")
    assert _heads(url) == ["0061c"]
    customer, upload = _upload_with_rows(db)
    migrate(url, "0070")
    assert _heads(url) == ["0061c", "0070"]
    assert _state(db) == normal_parents
    migrate(url, "0071")
    assert _state(db) == normal_0071
    migrate(url, TOP)
    assert _heads(url) == [TOP]
    assert _state(db) == normal
    assert _rows(db, customer, upload) == (1, 2, 2, 20, 2, 3)
    assert _owner(db, _COUNTED, {"u": upload}) == [(2,)]

    base._downgrade(url, "0070")
    assert _heads(url) == ["0061c", "0070"]
    assert _state(db) == normal_parents
    assert _rows(db, customer, upload) == (1, 2, 2, 20, 2, 3)
    migrate(url, TOP)
    assert _state(db) == normal
    assert _owner(db, _COUNTED, {"u": upload}) == [(2,)]


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


def test_an_uploads_counted_files_start_at_0_and_are_never_negative(db: Database) -> None:
    """ "Add new files only" (0072): ``items_counted``, the files registered that
    weren't already in Kaagaz."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "A")
    upload = new_folder_upload(db, customer, user, folder_ids=[folder])
    counted = text("SELECT items_counted FROM folder_uploads WHERE id = :u")
    with db.customer(customer) as conn:
        assert conn.execute(counted, {"u": upload}).scalar_one() == 0
    with pytest.raises(DBAPIError), db.customer(customer) as conn:
        conn.execute(text("UPDATE folder_uploads SET items_counted = -1 WHERE id = :u"), {"u": upload})
