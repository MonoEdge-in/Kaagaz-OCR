"""Empty folders Kaagaz made go (Vrushit UAT 3, 27 Oct 2026; ``folders.prune_empty``): once a
move, an Undo, a delete for good or a folder move leaves one with no file and no folder in it,
it goes, and so does each parent it leaves empty. A file in Trash keeps its folder, a folder a
person made is never deleted, and a department a moved type folder's key names stays (R10).
``prune-folders`` lists the ones already empty and, with ``--apply``, deletes them."""

import io

import pytest
from sqlalchemy import text

from kaagaz.cli import main
from kaagaz.db.documents import delete_family_forever
from kaagaz.db.engine import Database
from kaagaz.db.trash import trash_document
from tests.db_support import insert_folder
from tests.fakes import MemoryStorage
from tests.test_autofile_db import NOW, folders, organisation, person_moves, read, upload

pytestmark = pytest.mark.db

_DELETES = "SELECT count(*) FROM audit_log WHERE customer_id = :c AND action = 'folder.delete'"


def names(db: Database, customer: str) -> list[str]:
    return [f[1] for f in folders(db, customer)]


def test_a_move_drops_the_folders_kaagaz_made_and_never_a_persons(db: Database) -> None:
    customer, admin = organisation(db)
    document = upload(db, customer, admin)
    read(db, customer, document)
    assert names(db, customer) == ["Accounts and HR", "Invoices"]
    with db.customer(customer) as conn:
        mine = insert_folder(conn, customer, admin, "Mine")

    person_moves(db, customer, document, mine)
    # Changed on purpose (PRD 9 F-02, D2): Invoices emptied goes; its department is the admin's, so it stays.
    assert names(db, customer) == ["Accounts and HR", "Mine"]
    with db.customer(customer) as conn:
        assert conn.execute(text(_DELETES), {"c": customer}).scalar_one() == 1

    person_moves(db, customer, document, None)
    assert names(db, customer) == ["Accounts and HR", "Mine"]  # a person's folder stays, empty


def test_a_file_in_trash_keeps_its_folder_until_it_is_deleted_for_good(db: Database) -> None:
    customer, admin = organisation(db)
    document = upload(db, customer, admin)
    read(db, customer, document)
    with db.customer(customer) as conn:
        trash_document(conn, customer, document, by=admin, now=NOW)
    assert names(db, customer) == ["Accounts and HR", "Invoices"]  # Restore needs it

    with db.customer(customer) as conn:
        assert delete_family_forever(conn, MemoryStorage(), customer, document) == 1
    # Changed on purpose (PRD 9 F-02, D2): the admin's department stays.
    assert names(db, customer) == ["Accounts and HR"]


def test_the_command_counts_the_empty_ones_then_deletes_them(db: Database) -> None:
    customer, admin = organisation(db)
    document = upload(db, customer, admin)
    read(db, customer, document)
    with db.customer(customer) as conn:  # emptied before prune_empty existed
        conn.execute(text("UPDATE documents SET folder_id = NULL WHERE customer_id = :c"), {"c": customer})
        insert_folder(conn, customer, admin, "Mine")

    counted = io.StringIO()
    assert main(["prune-folders", "--customer-id", customer], db=db, environ={}, out=counted) == 0
    assert counted.getvalue().endswith("1 folder empty, not deleted (add --apply)\n")  # Invoices
    assert names(db, customer) == ["Accounts and HR", "Invoices", "Mine"]

    applied = io.StringIO()
    assert main(["prune-folders", "--customer-id", customer, "--apply"], db=db, environ={}, out=applied) == 0
    # Changed on purpose (PRD 9 F-02, D2): only Invoices goes; the department is the admin's.
    assert applied.getvalue() == f"customer {customer}: 1 deleted\n1 folder deleted\n"
    assert names(db, customer) == ["Accounts and HR", "Mine"]
