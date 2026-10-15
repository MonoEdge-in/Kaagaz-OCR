"""Auto-filing (feature 28, council 15 Oct 2026) against PostgreSQL: one test per rule of
the plan (R1 to R18), then its high-risk cases: versions, never twice, races with real
threads in a forced order (``wait_until_blocked``, never timing), the stale flag after a
folder delete, Undo and the admin's switch. Customer separation and the operator's grant:
test_isolation_autofile.py; the migration: test_migration_0025_db.py; the pipeline's call
and its error log: test_pipeline.py's AutoFilingTest.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db import autofile
from kaagaz.db.admin import set_autofile
from kaagaz.db.documents import PgDocuments, add_version
from kaagaz.db.engine import Database
from kaagaz.db.folders import create_folder, delete_folder, move_folder, rename_folder
from kaagaz.db.listing import get_document, move_family
from kaagaz.db.trash import lock_live_family, restore_document, trash_document
from kaagaz.ingestion.ports import DocumentRecord
from tests.db_support import insert_folder, name_session, new_customer, wait_until_blocked
from tests.test_api_support import CSRF, Api
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
_GRANT = "UPDATE customers SET autofile_granted = true, autofile_on = :on WHERE id = :c"
_TYPE = "UPDATE documents SET doc_type = :t WHERE customer_id = :c AND id = :d"
_STATE = (
    "SELECT r.autofile, f.name FROM documents d"
    " JOIN documents r ON r.customer_id = d.customer_id AND r.id = d.family_id"
    " LEFT JOIN folders f ON f.customer_id = d.customer_id AND f.id = d.folder_id"
    " WHERE d.customer_id = :c AND d.id = :d"
)
_FOLDERS = (
    "SELECT id::text, name, parent_id::text, autofile_key, created_by::text"
    " FROM folders WHERE customer_id = :c"
)
_AUDIT = (
    "SELECT action, actor_user_id::text, details FROM audit_log WHERE customer_id = :c"
    " AND action IN ('folder.create', 'document.autofile', 'document.autofile_undo') ORDER BY id"
)


# --- helpers ----------------------------------------------------------------------------


def organisation(db: Database, *, on: bool = True) -> tuple[str, str]:
    """A new customer the operator granted auto-filing, switched on (or off) by its admin,
    and its admin's id. Written as the owner, as the operator's function does."""
    customer, admin = new_customer(db)
    _owner(db, _GRANT, {"c": customer, "on": on})
    return customer, admin


def upload(db: Database, customer: str, user: str | None, *, folder: str | None = None) -> str:
    """A new upload as the upload service stores it (``add_if_absent``)."""
    record = DocumentRecord(
        customer_id=customer,
        document_id=str(uuid.uuid4()),
        sha256=uuid.uuid4().hex * 2,
        size=10,
        detected_type="pdf",
        uploaded_by=user,
        folder_id=folder,
    )
    stored, created = PgDocuments(db).add_if_absent(record)
    assert created
    return stored.document_id


def ready(db: Database, customer: str, document: str, doc_type: str | None = "invoice") -> None:
    """A reading's end before filing: the card's type is saved, then the document is ready."""
    with db.customer(customer) as conn:
        conn.execute(text(_TYPE), {"c": customer, "d": document, "t": doc_type})
    PgDocuments(db).mark_ready(customer, document, 1)


def file(db: Database, customer: str, document: str) -> str | None:
    """Filing in its own transaction, with the code it decided."""
    with db.customer(customer) as conn:
        return autofile.file_document(conn, customer, document)


def read(db: Database, customer: str, document: str, doc_type: str | None = "invoice") -> None:
    """A whole reading's end, as the pipeline: ready, then ``PgDocuments.file_document``."""
    ready(db, customer, document, doc_type)
    PgDocuments(db).file_document(customer, document)


def state(db: Database, customer: str, document: str) -> tuple[str | None, str | None]:
    """The family's auto-filing state and the name of the folder the document is in."""
    with db.customer(customer) as conn:
        row = conn.execute(text(_STATE), {"c": customer, "d": document}).one()
    return row[0], row[1]


def folders(db: Database, customer: str) -> list[tuple[str, str, str | None, str | None, str]]:
    """Every folder: (id, name, parent id, key, created_by), by name."""
    with db.customer(customer) as conn:
        return sorted(
            (tuple(row) for row in conn.execute(text(_FOLDERS), {"c": customer})), key=lambda r: r[1]
        )


def view(db: Database, customer: str, document: str) -> str | None:
    """What the document screens say (``DocumentSummary.autofile``)."""
    with db.customer(customer) as conn:
        row = get_document(conn, customer, document)
    assert row is not None
    filed: str | None = row["autofile"]
    return filed


def audit(db: Database, customer: str) -> list[tuple[str, str | None, dict[str, Any]]]:
    """The auto-filing audit rows: (action, actor, details), oldest first."""
    with db.customer(customer) as conn:
        return [(row[0], row[1], dict(row[2])) for row in conn.execute(text(_AUDIT), {"c": customer})]


def person_moves(db: Database, customer: str, document: str, folder: str | None) -> None:
    """A person's move (PATCH folder_id): the family lock, then ``move_family``."""
    with db.customer(customer) as conn:
        assert lock_live_family(conn, customer, document) is not None
        move_family(conn, customer, document, folder_id=folder)


# --- the rules ---------------------------------------------------------------------------


def test_r2_r9_r13_a_new_upload_goes_into_a_new_invoices_folder_its_uploader_owns(db: Database) -> None:
    customer, admin = organisation(db)
    document = upload(db, customer, admin)
    assert state(db, customer, document) == ("pending", None)

    read(db, customer, document)

    assert state(db, customer, document) == ("filed", "Invoices")
    [(folder, name, parent, key, owner)] = folders(db, customer)
    assert (name, parent, key, owner) == ("Invoices", None, "type:invoice", admin)
    assert view(db, customer, document) == "filed"
    assert audit(db, customer) == [  # no actor: Kaagaz did it; ids only
        ("folder.create", None, {"parent_id": None, "auto": True}),
        ("document.autofile", None, {"folder_id": folder}),
    ]


def test_r9_a_folder_of_that_name_is_adopted_whatever_its_letter_case(db: Database) -> None:
    customer, admin = organisation(db)
    with db.customer(customer) as conn:
        mine = insert_folder(conn, customer, admin, "invoices")
    document = upload(db, customer, admin)
    read(db, customer, document)
    assert state(db, customer, document) == ("filed", "invoices")
    assert [(f[0], f[3]) for f in folders(db, customer)] == [(mine, "type:invoice")]
    assert [row[0] for row in audit(db, customer)] == ["document.autofile"]  # no folder made


def test_r10_a_renamed_and_moved_type_folder_is_still_used(db: Database) -> None:
    customer, admin = organisation(db)
    first = upload(db, customer, admin)
    read(db, customer, first)
    [(invoices, *_)] = folders(db, customer)
    with db.customer(customer) as conn:
        archive = create_folder(conn, customer, name="Archive", parent_id=None, created_by=admin)
    with db.customer(customer) as conn:
        rename_folder(conn, customer, invoices, name="Bills 2026")
        move_folder(conn, customer, invoices, parent_id=archive.id)

    second = upload(db, customer, admin)
    read(db, customer, second)

    assert state(db, customer, second) == ("filed", "Bills 2026")
    assert [f[1] for f in folders(db, customer)] == ["Archive", "Bills 2026"]


def test_r10_a_deleted_type_folder_takes_its_key_and_the_next_file_makes_a_new_one(db: Database) -> None:
    customer, admin = organisation(db)
    first = upload(db, customer, admin)
    read(db, customer, first)
    [(old, *_)] = folders(db, customer)
    person_moves(db, customer, first, None)
    with db.customer(customer) as conn:
        delete_folder(conn, customer, old)

    second = upload(db, customer, admin)
    read(db, customer, second)

    [(new, name, *_)] = folders(db, customer)
    assert (name, new != old) == ("Invoices", True)
    assert state(db, customer, first) == (None, None)  # the person's move ended its state


def test_r6_other_is_skipped_and_said_not_filed(db: Database) -> None:
    customer, admin = organisation(db)
    document = upload(db, customer, admin)
    ready(db, customer, document, "other")
    assert file(db, customer, document) == "other"
    assert state(db, customer, document) == ("skipped", None)
    assert view(db, customer, document) == "not_filed"
    assert folders(db, customer) == []


def test_r6_r18_no_type_keeps_it_pending_then_a_read_again_files_it_once(db: Database) -> None:
    """No type: the model is off (no key), down or silent. Nothing is filed, and the
    file says it is not filed; a later successful read files it, once."""
    customer, admin = organisation(db)
    document = upload(db, customer, admin)
    ready(db, customer, document, None)
    assert file(db, customer, document) == "no_type"
    assert (state(db, customer, document), view(db, customer, document)) == (("pending", None), "not_filed")
    assert folders(db, customer) == []

    read(db, customer, document, "invoice")  # Read again, with the model back
    assert file(db, customer, document) is None  # R5: never twice
    read(db, customer, document, "receipt")  # a changed type moves nothing
    assert state(db, customer, document) == ("filed", "Invoices")
    assert [row[0] for row in audit(db, customer)].count("document.autofile") == 1


def test_r2_nothing_is_pending_in_a_chosen_folder_with_the_switch_off_or_for_a_duplicate(
    db: Database,
) -> None:
    customer, admin = organisation(db)
    with db.customer(customer) as conn:
        mine = insert_folder(conn, customer, admin, "Mine")
    chosen = upload(db, customer, admin, folder=mine)
    assert state(db, customer, chosen) == (None, "Mine")
    read(db, customer, chosen)
    assert state(db, customer, chosen) == (None, "Mine")

    off, off_admin = organisation(db, on=False)
    plain = upload(db, off, off_admin)
    read(db, off, plain)
    assert state(db, off, plain) == (None, None)

    first = upload(db, customer, admin)
    with db.customer(customer) as conn:
        sha = str(
            conn.execute(
                text("SELECT sha256 FROM documents WHERE customer_id = :c AND id = :d"),
                {"c": customer, "d": first},
            ).scalar_one()
        )
    again = DocumentRecord(
        customer_id=customer, document_id=str(uuid.uuid4()), sha256=sha, size=10, detected_type="pdf"
    )
    stored, created = PgDocuments(db).add_if_absent(again)
    assert (created, stored.document_id) == (False, first)  # no new row, so nothing new is pending


def test_r14_switched_off_before_it_is_read_it_is_not_filed(db: Database) -> None:
    customer, admin = organisation(db)
    document = upload(db, customer, admin)
    with db.customer(customer) as conn:
        assert set_autofile(conn, customer, on=False)
    ready(db, customer, document)
    assert file(db, customer, document) == "off"
    assert state(db, customer, document) == (None, None)
    assert view(db, customer, document) is None  # quietly: no note


def test_r14_an_organisation_no_longer_active_is_not_filed(db: Database) -> None:
    customer, admin = organisation(db)
    document = upload(db, customer, admin)
    _owner(db, "UPDATE customers SET status = 'disabled', disabled_at = now() WHERE id = :c", {"c": customer})
    ready(db, customer, document)
    assert file(db, customer, document) == "inactive"
    assert state(db, customer, document) == (None, None)


def test_r14_a_family_in_trash_is_not_filed(db: Database) -> None:
    customer, admin = organisation(db)
    document = upload(db, customer, admin)
    with db.customer(customer) as conn:
        trash_document(conn, customer, document, by=admin, now=NOW)
    ready(db, customer, document)
    assert file(db, customer, document) == "trash"
    assert state(db, customer, document) == (None, None)


def test_r3_a_person_moving_it_while_it_is_read_wins_even_to_not_in_a_folder(db: Database) -> None:
    customer, admin = organisation(db)
    with db.customer(customer) as conn:
        mine = insert_folder(conn, customer, admin, "Mine")
    into_mine, to_top = upload(db, customer, admin), upload(db, customer, admin)
    person_moves(db, customer, into_mine, mine)
    person_moves(db, customer, to_top, None)

    read(db, customer, into_mine)
    read(db, customer, to_top)

    assert state(db, customer, into_mine) == (None, "Mine")
    assert state(db, customer, to_top) == (None, None)
    assert [f[1] for f in folders(db, customer)] == ["Mine"]


def test_r12_a_folder_of_that_name_filing_another_type_is_a_clash(db: Database) -> None:
    customer, admin = organisation(db)
    receipt = upload(db, customer, admin)
    read(db, customer, receipt, "receipt")
    [(receipts, *_)] = folders(db, customer)
    with db.customer(customer) as conn:
        rename_folder(conn, customer, receipts, name="Invoices")  # still files receipts
    invoice = upload(db, customer, admin)
    ready(db, customer, invoice)
    assert file(db, customer, invoice) == "clash"
    assert (state(db, customer, invoice), view(db, customer, invoice)) == (("skipped", None), "not_filed")


def test_r13_with_no_uploader_a_folder_is_used_or_adopted_but_never_made(db: Database) -> None:
    customer, admin = organisation(db)
    nobody = upload(db, customer, None)
    ready(db, customer, nobody)
    assert file(db, customer, nobody) == "no_owner"
    assert (state(db, customer, nobody), folders(db, customer)) == (("skipped", None), [])

    with db.customer(customer) as conn:
        insert_folder(conn, customer, admin, "Invoices")
    adopted = upload(db, customer, None)
    read(db, customer, adopted)
    assert state(db, customer, adopted) == ("filed", "Invoices")


# --- versions ------------------------------------------------------------------------------


def _version(db: Database, customer: str, user: str, of: str) -> str:
    record = DocumentRecord(
        customer_id=customer,
        document_id=str(uuid.uuid4()),
        sha256=uuid.uuid4().hex * 2,
        size=10,
        detected_type="pdf",
        uploaded_by=user,
    )
    with db.customer(customer) as conn:
        added = add_version(conn, record, of_document_id=of)
    assert added is not None and added[1]
    return added[0].document_id


def test_r15_a_version_read_after_a_newer_one_keeps_pending_and_the_current_one_files_once(
    db: Database,
) -> None:
    customer, admin = organisation(db)
    first = upload(db, customer, admin)
    second = _version(db, customer, admin, first)
    assert state(db, customer, second) == ("pending", None)  # the family's state, on the first
    ready(db, customer, second)  # v2 is current
    ready(db, customer, first)  # v1 is read after it: not current

    assert file(db, customer, first) == "not_current"
    assert state(db, customer, first) == ("pending", None)
    assert file(db, customer, second) == "filed"
    assert file(db, customer, second) is None
    assert (state(db, customer, first), state(db, customer, second)) == (("filed", "Invoices"),) * 2
    assert [row[0] for row in audit(db, customer)].count("document.autofile") == 1


def test_a_new_version_of_a_filed_family_is_not_pending_and_says_filed(db: Database) -> None:
    customer, admin = organisation(db)
    first = upload(db, customer, admin)
    read(db, customer, first)
    second = _version(db, customer, admin, first)
    with db.customer(customer) as conn:
        own = conn.execute(
            text("SELECT autofile FROM documents WHERE customer_id = :c AND id = :d"),
            {"c": customer, "d": second},
        ).scalar()
    assert own is None  # its own row holds nothing: the state is the family's
    read(db, customer, second, "receipt")
    assert (state(db, customer, second), view(db, customer, second)) == (("filed", "Invoices"), "filed")


# --- races: real threads, the order forced -------------------------------------------------


def _session() -> str:
    return f"autofile-{uuid.uuid4().hex[:12]}"


def _file_as(db: Database, customer: str, document: str, session: str) -> str | None:
    with db.customer(customer) as conn:
        name_session(conn, session)
        return autofile.file_document(conn, customer, document)


def test_race_two_invoices_filed_at_once_make_one_invoices_folder(db: Database) -> None:
    customer, admin = organisation(db)
    a, b = upload(db, customer, admin), upload(db, customer, admin)
    ready(db, customer, a)
    ready(db, customer, b)
    session = _session()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(customer) as conn:
            assert autofile.file_document(conn, customer, a) == "filed"
            second = pool.submit(_file_as, db, customer, b, session)
            wait_until_blocked(db, application_name=session)  # on the tree lock
        assert second.result(timeout=10) == "filed"
    assert [f[1] for f in folders(db, customer)] == ["Invoices"]
    assert state(db, customer, b) == ("filed", "Invoices")


def test_race_a_persons_move_while_filing_waits_wins(db: Database) -> None:
    customer, admin = organisation(db)
    with db.customer(customer) as conn:
        mine = insert_folder(conn, customer, admin, "Mine")
    document = upload(db, customer, admin)
    ready(db, customer, document)
    session = _session()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(customer) as conn:
            assert lock_live_family(conn, customer, document) is not None
            move_family(conn, customer, document, folder_id=mine)
            second = pool.submit(_file_as, db, customer, document, session)
            wait_until_blocked(db, application_name=session)  # on the family lock
        assert second.result(timeout=10) is None
    assert state(db, customer, document) == (None, "Mine")


def test_race_filing_waits_for_a_folder_move_and_a_folder_delete_without_a_deadlock(db: Database) -> None:
    customer, admin = organisation(db)
    first = upload(db, customer, admin)
    read(db, customer, first)
    [(invoices, *_)] = folders(db, customer)
    with db.customer(customer) as conn:
        archive = create_folder(conn, customer, name="Archive", parent_id=None, created_by=admin)
    moved, after_delete = upload(db, customer, admin), upload(db, customer, admin)
    ready(db, customer, moved)
    ready(db, customer, after_delete)

    session = _session()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(customer) as conn:
            move_folder(conn, customer, invoices, parent_id=archive.id)
            second = pool.submit(_file_as, db, customer, moved, session)
            wait_until_blocked(db, application_name=session)
        assert second.result(timeout=10) == "filed"
    assert state(db, customer, moved) == ("filed", "Invoices")

    person_moves(db, customer, first, None)
    person_moves(db, customer, moved, None)
    session = _session()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(customer) as conn:
            delete_folder(conn, customer, invoices)
            third = pool.submit(_file_as, db, customer, after_delete, session)
            wait_until_blocked(db, application_name=session)
        assert third.result(timeout=10) == "filed"
    assert [(f[1], f[2]) for f in folders(db, customer)] == [("Archive", None), ("Invoices", None)]


def test_race_a_trash_while_filing_waits_leaves_it_unfiled(db: Database) -> None:
    customer, admin = organisation(db)
    document = upload(db, customer, admin)
    ready(db, customer, document)
    session = _session()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(customer) as conn:
            trash_document(conn, customer, document, by=admin, now=NOW)
            second = pool.submit(_file_as, db, customer, document, session)
            wait_until_blocked(db, application_name=session)
        assert second.result(timeout=10) == "trash"
    assert state(db, customer, document) == (None, None)


# --- the stale flag and Undo ---------------------------------------------------------------


def _undo(client: Any, document: str) -> Any:
    return client.post(f"/api/documents/{document}/autofile/undo", headers=CSRF)


def test_r16_undo_moves_the_family_back_keeps_the_folder_and_names_who(db: Database) -> None:
    api = Api(db)
    customer, admin = organisation(db)
    member = api.sign_in(customer)  # any user of the organisation, not only its uploader
    document = upload(db, customer, admin)
    read(db, customer, document)
    [(invoices, *_)] = folders(db, customer)

    reply = _undo(member.client, document)

    assert reply.status_code == 200, reply.text
    assert (reply.json()["folder_id"], reply.json()["autofile"]) == (None, None)
    assert state(db, customer, document) == (None, None)
    assert [f[0] for f in folders(db, customer)] == [invoices]  # the folder stays
    assert audit(db, customer)[-1] == ("document.autofile_undo", member.user_id, {"folder_id": invoices})
    again = _undo(member.client, document)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "already_moved")


def test_r16_undo_after_a_later_move_or_in_trash_is_refused(db: Database) -> None:
    api = Api(db)
    customer, admin = organisation(db)
    member = api.sign_in(customer)
    moved, trashed = upload(db, customer, admin), upload(db, customer, admin)
    read(db, customer, moved)
    read(db, customer, trashed)
    with db.customer(customer) as conn:
        mine = insert_folder(conn, customer, admin, "Mine")
    patched = member.client.patch(f"/api/documents/{moved}", json={"folder_id": mine}, headers=CSRF)
    assert patched.status_code == 200 and patched.json()["autofile"] is None
    with db.customer(customer) as conn:
        trash_document(conn, customer, trashed, by=admin, now=NOW)

    late = _undo(member.client, moved)
    in_trash = _undo(member.client, trashed)

    assert (late.status_code, late.json()["error"]["code"]) == (409, "already_moved")
    assert (in_trash.status_code, in_trash.json()["error"]["code"]) == (409, "in_trash")
    assert state(db, customer, moved) == (None, "Mine")
    assert _undo(member.client, str(uuid.uuid4())).status_code == 404


def test_stale_flag_a_deleted_folder_of_a_filed_family_in_trash_is_not_filed_any_more(db: Database) -> None:
    api = Api(db)
    customer, admin = organisation(db)
    member = api.sign_in(customer)
    document = upload(db, customer, admin)
    read(db, customer, document)
    [(invoices, *_)] = folders(db, customer)
    with db.customer(customer) as conn:
        trash_document(conn, customer, document, by=admin, now=NOW)
    with db.customer(customer) as conn:
        delete_folder(conn, customer, invoices)  # the family goes to the top level

    assert state(db, customer, document) == (None, None)
    assert view(db, customer, document) is None
    assert _undo(member.client, document).json()["error"]["code"] == "in_trash"
    with db.customer(customer) as conn:
        assert restore_document(conn, customer, document)
    assert _undo(member.client, document).json()["error"]["code"] == "already_moved"


# --- the admin's switch (R1) ---------------------------------------------------------------


def test_r1_the_admin_switch_works_only_while_granted_and_a_patch_keeps_what_it_leaves_out(
    db: Database,
) -> None:
    api = Api(db)  # platform limit: 5 MB
    customer, _ = api.customer("Mehta Textiles")
    admin = api.admin_sign_in(customer)
    settings = "/api/admin/settings"
    assert admin.client.get(settings).json()["autofile"] == {"granted": False, "on": False}

    refused = admin.client.patch(settings, json={"max_upload_mb": 3, "autofile_on": True}, headers=CSRF)
    error = refused.json()["error"]
    assert (refused.status_code, error["code"]) == (409, "not_granted")
    assert error["message"] == "Auto-filing isn't available for your organisation yet."
    assert admin.client.get(settings).json()["max_upload_mb"] is None  # the whole PATCH was refused

    _owner(db, "UPDATE customers SET autofile_granted = true WHERE id = :c", {"c": customer})
    assert admin.client.patch(settings, json={"max_upload_mb": 3}, headers=CSRF).status_code == 200
    on = admin.client.patch(settings, json={"autofile_on": True}, headers=CSRF).json()
    assert (on["autofile"], on["max_upload_mb"]) == ({"granted": True, "on": True}, 3)
    admin.client.patch(settings, json={"autofile_on": True}, headers=CSRF)  # no change, no row
    off = admin.client.patch(settings, json={"autofile_on": False}, headers=CSRF).json()
    assert off["autofile"] == {"granted": True, "on": False}
    assert [a for a in api.audit_actions(customer) if a.startswith("settings.")] == [
        "settings.update",
        "settings.autofile_on",
        "settings.autofile_off",
    ]
    assert admin.client.patch(settings, json={"autofile_granted": True}, headers=CSRF).status_code == 400
