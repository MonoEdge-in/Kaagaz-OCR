"""Tags: a customer's labels, and the set of tags on a document family (PRD 1 F3, WP1.2).

Tags belong to the family: setting them through any version writes the same
set to every version. The concurrency tests force the order with
``wait_until_blocked``, never with sleeps.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import Connection, text

from kaagaz.db.engine import Database
from kaagaz.db.tags import (
    TagError,
    TagNameTaken,
    TagNotFound,
    TagRow,
    create_tag,
    delete_tag,
    list_tags,
    set_document_tags,
    tags_for_documents,
)
from tests.db_support import insert_document, new_customer, wait_until_blocked

pytestmark = pytest.mark.db


def _tags(conn: Connection, customer: str, admin: str, *names: str) -> list[TagRow]:
    return [create_tag(conn, customer, name=name, created_by=admin) for name in names]


def _names(rows: list[TagRow]) -> list[str]:
    return [row.name for row in rows]


def _stored(db: Database, customer: str, document_id: str) -> list[str]:
    """The tag names stored on one document row, sorted."""
    with db.customer(customer) as conn:
        return _names(tags_for_documents(conn, customer, [document_id])[document_id])


def _named(conn: Connection, application_name: str) -> None:
    conn.execute(text("SELECT set_config('application_name', :name, true)"), {"name": application_name})


def _session_name() -> str:
    return f"tags-{uuid.uuid4().hex[:16]}"


def test_create_and_list_tags_by_name(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        gst, bank, rent = _tags(conn, customer, admin, "gst", "Bank", "rent")
        listed = list_tags(conn, customer)

    assert listed == [bank, gst, rent]
    assert gst == TagRow(id=gst.id, name="gst", created_at=gst.created_at)


def test_a_taken_tag_name_is_name_taken(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        _tags(conn, customer, admin, "GST")

    with pytest.raises(TagNameTaken) as taken, db.customer(customer) as conn:
        create_tag(conn, customer, name="gst", created_by=admin)
    assert taken.value.code == "conflict"
    with db.customer(customer) as conn:
        assert _names(list_tags(conn, customer)) == ["GST"]


def test_set_change_and_clear_the_set(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        rent, bank, gst = _tags(conn, customer, admin, "rent", "Bank", "gst")
        document = insert_document(conn, customer)
        other = insert_document(conn, customer)
        set_document_tags(conn, customer, other, [rent.id])

    with db.customer(customer) as conn:
        assert set_document_tags(conn, customer, document, [rent.id, bank.id]) == [bank, rent]
    assert _stored(db, customer, document) == ["Bank", "rent"]

    with db.customer(customer) as conn:
        assert set_document_tags(conn, customer, document, [gst.id, bank.id]) == [bank, gst]
    assert _stored(db, customer, document) == ["Bank", "gst"]

    with db.customer(customer) as conn:
        assert set_document_tags(conn, customer, document, []) == []
        assert tags_for_documents(conn, customer, [document, other]) == {document: [], other: [rent]}


def test_duplicate_ids_are_removed(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        rent, bank = _tags(conn, customer, admin, "rent", "bank")
        document = insert_document(conn, customer)
        result = set_document_tags(conn, customer, document, [rent.id, bank.id, rent.id, rent.id])
    assert result == [bank, rent]
    assert _stored(db, customer, document) == ["bank", "rent"]


def test_an_unknown_or_other_customers_tag_is_not_found_and_keeps_the_old_set(db: Database) -> None:
    customer, admin = new_customer(db)
    other, other_admin = new_customer(db)
    with db.customer(other) as conn:
        (theirs,) = _tags(conn, other, other_admin, "theirs")
    with db.customer(customer) as conn:
        rent, bank = _tags(conn, customer, admin, "rent", "bank")
        document = insert_document(conn, customer)
        set_document_tags(conn, customer, document, [rent.id])

    for stranger in (str(uuid.uuid4()), theirs.id):
        with pytest.raises(TagNotFound) as missing, db.customer(customer) as conn:
            set_document_tags(conn, customer, document, [bank.id, stranger])
        assert missing.value.code == "not_found"
        assert _stored(db, customer, document) == ["rent"]


def test_a_missing_document_is_not_found(db: Database) -> None:
    customer, admin = new_customer(db)
    other, _other_admin = new_customer(db)
    with db.customer(other) as conn:
        theirs = insert_document(conn, other)
    with db.customer(customer) as conn:
        (rent,) = _tags(conn, customer, admin, "rent")

    for document in (str(uuid.uuid4()), theirs):
        with pytest.raises(TagNotFound), db.customer(customer) as conn:
            set_document_tags(conn, customer, document, [rent.id])


def test_the_set_applies_to_every_version_in_a_family(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        rent, bank = _tags(conn, customer, admin, "rent", "bank")
        first = insert_document(conn, customer)
        second = insert_document(conn, customer, family_id=first, version_no=2, is_current=False)
        unrelated = insert_document(conn, customer)
        set_document_tags(conn, customer, unrelated, [bank.id])

        set_document_tags(conn, customer, second, [rent.id, bank.id])
        assert tags_for_documents(conn, customer, [first, second, unrelated]) == {
            first: [bank, rent],
            second: [bank, rent],
            unrelated: [bank],
        }

        set_document_tags(conn, customer, first, [rent.id])
        assert tags_for_documents(conn, customer, [first, second]) == {first: [rent], second: [rent]}


def test_tags_for_documents_with_no_ids_is_empty(db: Database) -> None:
    customer, _admin = new_customer(db)
    with db.customer(customer) as conn:
        assert tags_for_documents(conn, customer, []) == {}


def test_delete_tag_cascades_and_returns_false_when_the_tag_is_missing(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        rent, bank = _tags(conn, customer, admin, "rent", "bank")
        document = insert_document(conn, customer)
        set_document_tags(conn, customer, document, [rent.id, bank.id])

    with db.customer(customer) as conn:
        assert delete_tag(conn, customer, rent.id) is True
    assert _stored(db, customer, document) == ["bank"]
    with db.customer(customer) as conn:
        assert list_tags(conn, customer) == [bank]
        assert delete_tag(conn, customer, rent.id) is False
        assert delete_tag(conn, customer, str(uuid.uuid4())) is False


def test_parallel_sets_on_one_family_end_with_the_second_set(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        rent, bank = _tags(conn, customer, admin, "rent", "bank")
        first = insert_document(conn, customer)
        second = insert_document(conn, customer, family_id=first, version_no=2, is_current=False)
    session = _session_name()

    def set_bank_through_the_other_version() -> list[TagRow]:
        with db.customer(customer) as conn:
            _named(conn, session)
            return set_document_tags(conn, customer, second, [bank.id])

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(customer) as conn:
            set_document_tags(conn, customer, first, [rent.id])
            later = pool.submit(set_bank_through_the_other_version)
            wait_until_blocked(db, application_name=session)
        assert later.result(timeout=10) == [bank]

    assert _stored(db, customer, first) == ["bank"]
    assert _stored(db, customer, second) == ["bank"]


def test_a_tag_deleted_during_a_set_is_not_found(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        rent, bank = _tags(conn, customer, admin, "rent", "bank")
        document = insert_document(conn, customer)
        set_document_tags(conn, customer, document, [bank.id])
    session = _session_name()

    def set_rent() -> list[TagRow]:
        with db.customer(customer) as conn:
            _named(conn, session)
            return set_document_tags(conn, customer, document, [rent.id])

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(customer) as conn:
            assert delete_tag(conn, customer, rent.id) is True
            blocked = pool.submit(set_rent)
            wait_until_blocked(db, application_name=session)
        with pytest.raises(TagNotFound):
            blocked.result(timeout=10)

    assert _stored(db, customer, document) == ["bank"]


def test_every_tag_error_carries_its_code() -> None:
    errors: list[TagError] = [TagNotFound(), TagNameTaken()]
    assert [error.code for error in errors] == ["not_found", "conflict"]
