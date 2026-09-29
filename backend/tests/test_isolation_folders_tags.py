"""Row level security and the composite keys on folders, tags and document_tags.

Customer B uses customer A's exact ids and must see nothing, change nothing and
write nothing for A. Foreign-key checks run without row level security, so the
(customer_id, id) keys are the second lock: even a row that names its own
customer can't point at another customer's folder, document or tag.
"""

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from kaagaz.db.engine import Database
from tests.db_support import (
    count_rows,
    insert_document,
    insert_folder,
    insert_tag,
    library_rows_seen,
    new_customer,
    new_library,
    new_trashed_document,
    tag_document,
    violation,
)

pytestmark = pytest.mark.db


def _refused_by_policy(error: DBAPIError) -> bool:
    """A WITH CHECK refusal. 42501 alone would also match a missing GRANT."""
    assert isinstance(error.orig, psycopg.Error)
    message = error.orig.diag.message_primary or ""
    return error.orig.sqlstate == "42501" and "row-level security policy" in message


def test_other_customer_sees_none_of_the_rows_even_by_id(db: Database) -> None:
    a = new_library(db)
    b, _ = new_customer(db)

    assert library_rows_seen(db, a["customer"], a) == (1, 1, 1)
    assert library_rows_seen(db, b, a) == (0, 0, 0)
    with db.customer(b) as conn:
        # Naming A in the WHERE changes nothing: the policy still hides every row.
        by_customer = {"a": a["customer"]}
        assert count_rows(conn, "SELECT count(*) FROM folders WHERE customer_id = :a", by_customer) == 0
        assert count_rows(conn, "SELECT count(*) FROM tags WHERE customer_id = :a", by_customer) == 0
        assert count_rows(conn, "SELECT count(*) FROM document_tags WHERE customer_id = :a", by_customer) == 0


def test_other_customer_cannot_insert_rows_naming_a(db: Database) -> None:
    a = new_library(db)
    b, _ = new_customer(db)
    with db.customer(a["customer"]) as conn:
        # A fresh pair of A's, so only the policy (never the primary key) can refuse the link.
        unused_tag = insert_tag(conn, a["customer"], a["user"], "Unused")
    params = {"a": a["customer"], "a_user": a["user"], "a_document": a["document"], "a_tag": unused_tag}
    statements = (
        "INSERT INTO folders (customer_id, name, created_by) VALUES (:a, 'Mine now', :a_user)",
        "INSERT INTO tags (customer_id, name, created_by) VALUES (:a, 'Mine now', :a_user)",
        "INSERT INTO document_tags (customer_id, document_id, tag_id) VALUES (:a, :a_document, :a_tag)",
    )
    for statement in statements:
        with pytest.raises(ProgrammingError) as caught, db.customer(b) as conn:
            conn.execute(text(statement), params)
        assert _refused_by_policy(caught.value), statement

    with db.customer(a["customer"]) as conn:
        assert count_rows(conn, "SELECT count(*) FROM folders WHERE name = 'Mine now'") == 0
        assert (
            count_rows(conn, "SELECT count(*) FROM document_tags WHERE tag_id = :t", {"t": unused_tag}) == 0
        )


def test_other_customer_cannot_move_its_own_rows_to_a(db: Database) -> None:
    a = new_library(db)
    b = new_library(db)
    statements = (
        ("UPDATE folders SET customer_id = :a WHERE id = :id", b["folder"]),
        ("UPDATE tags SET customer_id = :a WHERE id = :id", b["tag"]),
    )
    for statement, row_id in statements:
        with pytest.raises(ProgrammingError) as caught, db.customer(b["customer"]) as conn:
            conn.execute(text(statement), {"a": a["customer"], "id": row_id})
        assert _refused_by_policy(caught.value), statement


def test_other_customer_cannot_update_or_delete_a_rows(db: Database) -> None:
    a = new_library(db)
    b, _ = new_customer(db)
    writes = (
        ("UPDATE folders SET name = 'Taken' WHERE id = :f", {"f": a["folder"]}),
        ("UPDATE tags SET name = 'Taken' WHERE id = :t", {"t": a["tag"]}),
        ("UPDATE document_tags SET created_at = created_at WHERE document_id = :d", {"d": a["document"]}),
        ("DELETE FROM document_tags WHERE document_id = :d", {"d": a["document"]}),
        ("DELETE FROM tags WHERE id = :t", {"t": a["tag"]}),
        ("DELETE FROM folders WHERE id = :f", {"f": a["folder"]}),
    )
    with db.customer(b) as conn:
        changed = [conn.execute(text(statement), params).rowcount for statement, params in writes]
    assert changed == [0, 0, 0, 0, 0, 0]

    assert library_rows_seen(db, a["customer"], a) == (1, 1, 1)
    with db.customer(a["customer"]) as conn:
        names = conn.execute(
            text("SELECT f.name, t.name FROM folders f, tags t WHERE f.id = :f AND t.id = :t"),
            {"f": a["folder"], "t": a["tag"]},
        ).one()
    assert tuple(names) == ("GST", "Paid")


def test_a_folder_cannot_sit_under_another_customer_folder(db: Database) -> None:
    a = new_library(db)
    b = new_library(db)
    with pytest.raises(IntegrityError) as caught, db.customer(a["customer"]) as conn:
        insert_folder(conn, a["customer"], a["user"], "Inside B", parent_id=b["folder"])
    assert violation(caught.value) == ("23503", "folders_customer_id_parent_id_fkey")

    with pytest.raises(IntegrityError) as caught, db.customer(a["customer"]) as conn:
        conn.execute(
            text("UPDATE folders SET parent_id = :p WHERE id = :f"), {"p": b["folder"], "f": a["folder"]}
        )
    assert violation(caught.value) == ("23503", "folders_customer_id_parent_id_fkey")


def test_a_document_cannot_be_filed_in_another_customer_folder(db: Database) -> None:
    a = new_library(db)
    b = new_library(db)
    with pytest.raises(IntegrityError) as caught, db.customer(a["customer"]) as conn:
        insert_document(conn, a["customer"], folder_id=b["folder"])
    assert violation(caught.value) == ("23503", "documents_folder_fk")

    with pytest.raises(IntegrityError) as caught, db.customer(a["customer"]) as conn:
        conn.execute(
            text("UPDATE documents SET folder_id = :f WHERE id = :d"), {"f": b["folder"], "d": a["document"]}
        )
    assert violation(caught.value) == ("23503", "documents_folder_fk")


def test_a_document_tag_cannot_point_at_another_customer_document_or_tag(db: Database) -> None:
    a = new_library(db)
    b = new_library(db)
    with db.customer(a["customer"]) as conn:
        spare_tag = insert_tag(conn, a["customer"], a["user"], "Spare")
        spare_document = insert_document(conn, a["customer"])

    with pytest.raises(IntegrityError) as caught, db.customer(a["customer"]) as conn:
        tag_document(conn, a["customer"], b["document"], spare_tag)
    assert violation(caught.value) == ("23503", "document_tags_customer_id_document_id_fkey")

    with pytest.raises(IntegrityError) as caught, db.customer(a["customer"]) as conn:
        tag_document(conn, a["customer"], spare_document, b["tag"])
    assert violation(caught.value) == ("23503", "document_tags_customer_id_tag_id_fkey")


def test_a_folder_tag_or_trash_mark_cannot_name_another_customer_user(db: Database) -> None:
    """created_by and deleted_by use (customer_id, user) keys too: the user must be the row's customer's."""
    a = new_library(db)
    b = new_library(db)
    trashed = {"at": datetime(2026, 9, 29, tzinfo=UTC), "d": a["document"]}
    attempts: tuple[tuple[str, dict[str, object], str], ...] = (
        (
            "INSERT INTO folders (customer_id, name, created_by) VALUES (:a, 'Theirs', :b_user)",
            {},
            "folders_customer_id_created_by_fkey",
        ),
        (
            "INSERT INTO tags (customer_id, name, created_by) VALUES (:a, 'Theirs', :b_user)",
            {},
            "tags_customer_id_created_by_fkey",
        ),
        (
            "UPDATE documents SET deleted_at = :at, deleted_by = :b_user WHERE id = :d",
            trashed,
            "documents_deleted_by_fk",
        ),
    )
    for statement, params, constraint in attempts:
        with pytest.raises(IntegrityError) as caught, db.customer(a["customer"]) as conn:
            conn.execute(text(statement), {"a": a["customer"], "b_user": b["user"], **params})
        assert violation(caught.value) == ("23503", constraint), statement

    with db.customer(a["customer"]) as conn:
        assert count_rows(conn, "SELECT count(*) FROM folders WHERE name = 'Theirs'") == 0
        assert count_rows(conn, "SELECT count(*) FROM tags WHERE name = 'Theirs'") == 0
        assert count_rows(conn, "SELECT count(*) FROM documents WHERE deleted_at IS NOT NULL") == 0


def test_customer_ids_with_trash_returns_only_ids_of_customers_with_old_trash(db: Database) -> None:
    now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    old, _ = new_customer(db)
    recent, _ = new_customer(db)
    live, _ = new_customer(db)
    new_trashed_document(db, old, deleted_at=now - timedelta(days=31))
    new_trashed_document(db, old, deleted_at=now - timedelta(days=40))
    new_trashed_document(db, recent, deleted_at=now - timedelta(days=1))
    with db.customer(live) as conn:
        insert_document(conn, live)

    with db.system() as conn:
        before = {"before": now - timedelta(days=30)}
        result = conn.execute(text("SELECT * FROM kaagaz_customer_ids_with_trash(:before)"), before)
        columns = list(result.keys())
        rows = result.all()

    # One uuid column: nothing else about the documents leaves the function.
    assert columns == ["kaagaz_customer_ids_with_trash"]
    assert all(len(row) == 1 and isinstance(row[0], uuid.UUID) for row in rows)
    ids = [str(row[0]) for row in rows]
    assert ids.count(old) == 1  # two old documents, one row (DISTINCT)
    assert recent not in ids
    assert live not in ids


def test_customer_ids_with_trash_is_a_narrow_owner_rights_function(db: Database) -> None:
    with db.system() as conn:
        row = conn.execute(
            text(
                "SELECT p.prosecdef, p.proconfig, p.proretset, p.prorettype::regtype::text,"
                " p.proallargtypes IS NULL,"
                " has_function_privilege('public', p.oid, 'EXECUTE'),"
                " has_function_privilege('kaagaz_app', p.oid, 'EXECUTE')"
                " FROM pg_proc p WHERE p.oid = 'kaagaz_customer_ids_with_trash(timestamptz)'::regprocedure"
            )
        ).one()
    security_definer, config, returns_set, return_type, no_out_columns, public_may_run, app_may_run = row
    assert security_definer
    assert config == ['search_path=""']
    assert (returns_set, return_type, no_out_columns) == (True, "uuid", True)
    assert not public_may_run
    assert app_may_run


def test_a_system_transaction_sees_no_customer_rows(db: Database) -> None:
    """Why the trash function exists: with no current customer, every table looks empty."""
    a = new_library(db)
    new_trashed_document(db, a["customer"], deleted_at=datetime(2026, 1, 1, tzinfo=UTC))
    with db.system() as conn:
        assert count_rows(conn, "SELECT count(*) FROM customers") == 0
        assert count_rows(conn, "SELECT count(*) FROM documents WHERE deleted_at IS NOT NULL") == 0
        assert count_rows(conn, "SELECT count(*) FROM folders") == 0
        assert count_rows(conn, "SELECT count(*) FROM tags") == 0
        assert count_rows(conn, "SELECT count(*) FROM document_tags") == 0
        through_function = conn.execute(
            text("SELECT count(*) FROM kaagaz_customer_ids_with_trash(:before) AS c WHERE c = :a"),
            {"before": datetime(2026, 1, 2, tzinfo=UTC), "a": a["customer"]},
        )
        assert through_function.scalar_one() == 1
