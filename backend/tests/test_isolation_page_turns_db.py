"""Page turns saved for everyone (0088, PRD 7 section 14, #600): a turn of one organisation can never
name another organisation's document or person (every key holds the organisation, and the key
checks run without row level security), and one organisation sees none of another's turns."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests.db_support import insert_document, new_customer, violation

pytestmark = pytest.mark.db
_TURN = text(
    "INSERT INTO page_rotations (customer_id, document_id, page_no, turn, set_by) VALUES (:c, :d, 1, 90, :u)"
)
_COUNT = text("SELECT count(*) FROM page_rotations WHERE customer_id = :c")


def _organisation(db: Database) -> dict[str, str]:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user)
    return {"c": customer, "u": user, "d": document}


@pytest.mark.parametrize("theirs", ["d", "u"])
def test_a_turn_naming_another_organisations_document_or_person_is_refused(db: Database, theirs: str) -> None:
    a, b = _organisation(db), _organisation(db)
    with pytest.raises(DBAPIError) as refused, db.customer(a["c"]) as conn:
        conn.execute(_TURN, {**a, theirs: b[theirs]})
    assert violation(refused.value)[0] == "23503"  # foreign_key_violation


def test_one_organisation_sees_none_of_anothers_turns_and_cannot_add_one_for_it(db: Database) -> None:
    a, b = _organisation(db), _organisation(db)
    with db.customer(a["c"]) as conn:
        conn.execute(_TURN, a)
    with db.customer(b["c"]) as conn:
        assert conn.execute(_COUNT, a).scalar_one() == 0  # a's rows, asked for by name
    with pytest.raises(DBAPIError) as refused, db.customer(b["c"]) as conn:
        conn.execute(_TURN, a)  # a's own document and person, written as b
    assert violation(refused.value)[0] == "42501"  # the policy's WITH CHECK
    with db.customer(a["c"]) as conn:
        assert conn.execute(_COUNT, a).scalar_one() == 1
