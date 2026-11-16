"""Who can see what's tables (0084, PRD 7, #590): an access row of one organisation can never name
another organisation's person or document (every key holds the organisation, and the key checks
run without row level security), and one organisation sees none of another's rows."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests.db_support import insert_document, insert_folder, new_customer, violation

pytestmark = pytest.mark.db
_ROWS = {
    "department_members": "INSERT INTO department_members (customer_id, department_id, user_id, added_by)"
    " VALUES (:c, :f, :u, :by)",
    "private_folder_members": "INSERT INTO private_folder_members (customer_id, folder_id, user_id, added_by)"
    " VALUES (:c, :f, :u, :by)",
    "document_shares": "INSERT INTO document_shares (customer_id, document_id, user_id, given_by)"
    " VALUES (:c, :d, :u, :by)",
    "lock_grants": "INSERT INTO lock_grants (customer_id, document_id, user_id, given_by)"
    " VALUES (:c, :d, :u, :by)",
}
_COUNT = text(
    "SELECT (SELECT count(*) FROM department_members WHERE customer_id = :c)"
    " + (SELECT count(*) FROM private_folder_members WHERE customer_id = :c)"
    " + (SELECT count(*) FROM document_shares WHERE customer_id = :c)"
    " + (SELECT count(*) FROM lock_grants WHERE customer_id = :c)"
)


def _organisation(db: Database) -> dict[str, str]:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "Finance")
        document = insert_document(conn, customer, folder_id=folder)
    return {"c": customer, "u": user, "by": user, "f": folder, "d": document}


# Each key of each table: the person, who added or gave it, and the document version.
_KEYS = [(table, theirs) for table in sorted(_ROWS) for theirs in ("u", "by")] + [
    ("document_shares", "d"),
    ("lock_grants", "d"),
]


@pytest.mark.parametrize(("table", "theirs"), _KEYS)
def test_a_row_naming_another_organisations_person_or_document_is_refused(
    db: Database, table: str, theirs: str
) -> None:
    a, b = _organisation(db), _organisation(db)
    with pytest.raises(DBAPIError) as refused, db.customer(a["c"]) as conn:
        conn.execute(text(_ROWS[table]), {**a, theirs: b[theirs]})
    assert violation(refused.value)[0] == "23503"  # foreign_key_violation


def test_one_organisation_sees_none_of_anothers_access_rows(db: Database) -> None:
    a, b = _organisation(db), _organisation(db)
    with db.customer(a["c"]) as conn:
        for statement in _ROWS.values():
            conn.execute(text(statement), a)
    with db.customer(b["c"]) as conn:
        assert conn.execute(_COUNT, {"c": a["c"]}).scalar_one() == 0  # a's rows, asked for by name
    with db.customer(a["c"]) as conn:
        assert conn.execute(_COUNT, {"c": a["c"]}).scalar_one() == len(_ROWS)
