"""Migration 0002's rules, one per test, each proved by trying what it forbids.

Every refusal is checked by its SQLSTATE and constraint name (the column name
for NOT NULL), so a test can't pass because some other rule happened to fire.
"""

from datetime import UTC, datetime

import psycopg
import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from tests.db_support import insert_document, insert_folder, insert_tag, new_customer, tag_document, violation

pytestmark = pytest.mark.db


def _current(conn: Connection, customer_id: str, family_id: str) -> list[str]:
    rows = conn.execute(
        text("SELECT id FROM documents WHERE customer_id = :c AND family_id = :f AND is_current"),
        {"c": customer_id, "f": family_id},
    )
    return [str(row[0]) for row in rows]


# --- users ---------------------------------------------------------------------


def test_a_new_user_gets_the_sign_in_defaults(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        row = conn.execute(
            text(
                "SELECT failed_logins, locked_until, session_version, must_change_password,"
                " totp_secret_enc, totp_enabled_at, totp_last_step, last_login_at FROM users WHERE id = :u"
            ),
            {"u": user},
        ).one()
    assert tuple(row) == (0, None, 1, False, None, None, None, None)


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("UPDATE users SET failed_logins = -1 WHERE id = :u", "users_failed_logins_check"),
        ("UPDATE users SET session_version = 0 WHERE id = :u", "users_session_version_check"),
    ],
    ids=["failed_logins", "session_version"],
)
def test_user_counters_cannot_go_below_their_floor(db: Database, statement: str, constraint: str) -> None:
    customer, user = new_customer(db)
    with pytest.raises(IntegrityError) as caught, db.customer(customer) as conn:
        conn.execute(text(statement), {"u": user})
    assert violation(caught.value) == ("23514", constraint)


# --- folders -------------------------------------------------------------------


def test_folder_name_is_1_to_100_characters(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        insert_folder(conn, customer, user, "x")
        insert_folder(conn, customer, user, "y" * 100)
    for name in ("", "z" * 101):
        with pytest.raises(IntegrityError) as caught, db.customer(customer) as conn:
            insert_folder(conn, customer, user, name)
        assert violation(caught.value) == ("23514", "folders_name_check"), len(name)


def test_a_folder_cannot_be_its_own_parent(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "Bills")
    with pytest.raises(IntegrityError) as caught, db.customer(customer) as conn:
        conn.execute(text("UPDATE folders SET parent_id = id WHERE id = :f"), {"f": folder})
    assert violation(caught.value) == ("23514", "folders_check")


def test_sibling_folder_names_are_unique_ignoring_case(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        parent = insert_folder(conn, customer, user, "2026")
        insert_folder(conn, customer, user, "Bills", parent_id=parent)
    with pytest.raises(IntegrityError) as caught, db.customer(customer) as conn:
        insert_folder(conn, customer, user, "BILLS", parent_id=parent)
    assert violation(caught.value) == ("23505", "folders_sibling_name")


def test_two_top_level_folders_with_the_same_name_collide(db: Database) -> None:
    """NULLS NOT DISTINCT: without it, two NULL parents would never be equal."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        insert_folder(conn, customer, user, "GST")
    with pytest.raises(IntegrityError) as caught, db.customer(customer) as conn:
        insert_folder(conn, customer, user, "gst")
    assert violation(caught.value) == ("23505", "folders_sibling_name")


def test_the_same_name_is_allowed_under_different_parents_and_customers(db: Database) -> None:
    customer, user = new_customer(db)
    other, other_user = new_customer(db)
    with db.customer(customer) as conn:
        first = insert_folder(conn, customer, user, "2025")
        second = insert_folder(conn, customer, user, "2026")
        insert_folder(conn, customer, user, "Bills")
        insert_folder(conn, customer, user, "Bills", parent_id=first)
        insert_folder(conn, customer, user, "Bills", parent_id=second)
    with db.customer(other) as conn:
        insert_folder(conn, other, other_user, "Bills")
    with db.customer(customer) as conn:
        assert conn.execute(text("SELECT count(*) FROM folders WHERE name = 'Bills'")).scalar_one() == 3


def test_a_folder_with_a_child_cannot_be_deleted(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        parent = insert_folder(conn, customer, user, "2026")
        insert_folder(conn, customer, user, "Bills", parent_id=parent)
    with pytest.raises(IntegrityError) as caught, db.customer(customer) as conn:
        conn.execute(text("DELETE FROM folders WHERE id = :f"), {"f": parent})
    assert violation(caught.value) == ("23503", "folders_customer_id_parent_id_fkey")


def test_a_folder_with_a_document_cannot_be_deleted_even_a_trashed_one(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        live = insert_folder(conn, customer, user, "Live")
        trashed = insert_folder(conn, customer, user, "Trashed")
        insert_document(conn, customer, folder_id=live)
        insert_document(conn, customer, folder_id=trashed, deleted_at=datetime(2026, 9, 1, tzinfo=UTC))
    for folder in (live, trashed):
        with pytest.raises(IntegrityError) as caught, db.customer(customer) as conn:
            conn.execute(text("DELETE FROM folders WHERE id = :f"), {"f": folder})
        assert violation(caught.value) == ("23503", "documents_folder_fk")


def test_the_folder_keys_are_no_action(db: Database) -> None:
    """PROVISIONAL 7.9: NO ACTION ("a"), not the PRD's RESTRICT ("r")."""
    with db.system() as conn:
        actions = conn.execute(
            text(
                "SELECT conname, confdeltype FROM pg_constraint WHERE conname IN"
                " ('folders_customer_id_parent_id_fkey', 'documents_folder_fk') ORDER BY conname"
            )
        ).all()
    assert [tuple(row) for row in actions] == [
        ("documents_folder_fk", "a"),
        ("folders_customer_id_parent_id_fkey", "a"),
    ]


def test_a_whole_folder_tree_can_go_in_one_statement(db: Database) -> None:
    """The keys are checked at the end of the statement, as when a customer's rows cascade."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        top = insert_folder(conn, customer, user, "2026")
        middle = insert_folder(conn, customer, user, "Q1", parent_id=top)
        insert_folder(conn, customer, user, "April", parent_id=middle)
    with db.customer(customer) as conn:
        gone = conn.execute(text("DELETE FROM folders WHERE customer_id = :c"), {"c": customer}).rowcount
    assert gone == 3


# --- tags ----------------------------------------------------------------------


def test_tag_name_is_1_to_40_characters(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        insert_tag(conn, customer, user, "x")
        insert_tag(conn, customer, user, "y" * 40)
    for name in ("", "z" * 41):
        with pytest.raises(IntegrityError) as caught, db.customer(customer) as conn:
            insert_tag(conn, customer, user, name)
        assert violation(caught.value) == ("23514", "tags_name_check"), len(name)


def test_tag_names_are_unique_per_customer_ignoring_case(db: Database) -> None:
    customer, user = new_customer(db)
    other, other_user = new_customer(db)
    with db.customer(customer) as conn:
        insert_tag(conn, customer, user, "GST")
    with pytest.raises(IntegrityError) as caught, db.customer(customer) as conn:
        insert_tag(conn, customer, user, "gst")
    assert violation(caught.value) == ("23505", "tags_name")

    # Another customer may have its own "GST".
    with db.customer(other) as conn:
        insert_tag(conn, other, other_user, "GST")
        assert conn.execute(text("SELECT count(*) FROM tags WHERE name = 'GST'")).scalar_one() == 1


def test_document_tags_go_when_the_document_or_the_tag_goes(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        paid, gst = insert_tag(conn, customer, user, "Paid"), insert_tag(conn, customer, user, "GST")
        bill, receipt = insert_document(conn, customer), insert_document(conn, customer)
        for document, tag in ((bill, paid), (bill, gst), (receipt, paid), (receipt, gst)):
            tag_document(conn, customer, document, tag)

    with db.customer(customer) as conn:
        conn.execute(text("DELETE FROM documents WHERE id = :d"), {"d": bill})
        conn.execute(text("DELETE FROM tags WHERE id = :t"), {"t": paid})
        left = conn.execute(
            text("SELECT document_id, tag_id FROM document_tags WHERE document_id = ANY(:d)"),
            {"d": [bill, receipt]},
        ).all()
    assert [(str(document), str(tag)) for document, tag in left] == [(receipt, gst)]


# --- documents: versions -------------------------------------------------------


def test_version_no_starts_at_1(db: Database) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(IntegrityError) as caught, db.customer(customer) as conn:
        insert_document(conn, customer, version_no=0)
    assert violation(caught.value) == ("23514", "documents_version_no_check")


def test_family_id_is_required(db: Database) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(IntegrityError) as caught, db.customer(customer) as conn:
        insert_document(conn, customer, family_id=None)
    assert isinstance(caught.value.orig, psycopg.Error)
    assert (caught.value.orig.sqlstate, caught.value.orig.diag.column_name) == ("23502", "family_id")


def test_a_family_has_at_most_one_current_row(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        first = insert_document(conn, customer)
    with pytest.raises(IntegrityError) as caught, db.customer(customer) as conn:
        insert_document(conn, customer, family_id=first, version_no=2, is_current=True)
    assert violation(caught.value) == ("23505", "documents_current")

    # A new version that is not current yet is fine.
    with db.customer(customer) as conn:
        insert_document(conn, customer, family_id=first, version_no=2, is_current=False)
        assert _current(conn, customer, first) == [first]


def test_switching_current_in_two_statements_works_in_either_direction(db: Database) -> None:
    """``documents_current`` is checked row by row, not at the end of the statement.

    So a one-statement switch (``SET is_current = (id = :d)``) works or fails
    depending on which row it happens to visit first. Clearing the old current
    row first, then setting the new one, never has two current rows, so it works
    whatever the physical row order is. Going forward and back covers both
    orders: the target row after the current one, and before it.
    """
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        first = insert_document(conn, customer)
        second = insert_document(conn, customer, family_id=first, version_no=2, is_current=False)
        third = insert_document(conn, customer, family_id=first, version_no=3, is_current=False)

    for target in (second, third, first, third, second):
        with db.customer(customer) as conn:
            conn.execute(
                text(
                    "UPDATE documents SET is_current = false"
                    " WHERE customer_id = :c AND family_id = :f AND is_current AND id <> :d"
                ),
                {"c": customer, "f": first, "d": target},
            )
            conn.execute(
                text("UPDATE documents SET is_current = true WHERE customer_id = :c AND id = :d"),
                {"c": customer, "d": target},
            )
            assert _current(conn, customer, first) == [target]


def test_a_version_number_is_used_once_per_family(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        first = insert_document(conn, customer)
        other_family = insert_document(conn, customer)
    with pytest.raises(IntegrityError) as caught, db.customer(customer) as conn:
        insert_document(conn, customer, family_id=first, version_no=1, is_current=False)
    assert violation(caught.value) == ("23505", "documents_family_version")

    # Version 2 is free in each family, so each may use it once.
    with db.customer(customer) as conn:
        insert_document(conn, customer, family_id=first, version_no=2, is_current=False)
        insert_document(conn, customer, family_id=other_family, version_no=2, is_current=False)
