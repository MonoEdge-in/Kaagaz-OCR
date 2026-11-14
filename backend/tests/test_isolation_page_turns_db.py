"""Page turns saved for everyone (0088, PRD 7 section 14, #600): a turn of one organisation can never
name another organisation's document or person (every key holds the organisation, and the key
checks run without row level security), and one organisation sees none of another's turns. Inside
one organisation, a file the person cannot see (PRD 7's walls, test_isolation_access_views) answers
both writes exactly as a random id, its turns are left as they were, and its detail is 404."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests.db_support import insert_document, new_customer, violation
from tests.test_api_support import CSRF, Api
from tests.test_isolation_access_views import _walled
from tests.test_operator_auth_db import _owner

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


def test_a_hidden_file_answers_as_a_random_id_and_keeps_its_turns(db: Database) -> None:
    """Fin (Department only) and a Salaries file in Trash: the plain reviewer sees neither. Not
    409 in_trash, not 400 for a page past the end: the reply for an id that never was."""
    org = _walled(Api(db))
    plain = org.people["plain"]
    saved = "SELECT document_id::text, page_no, turn FROM page_rotations WHERE customer_id = :c ORDER BY 1, 2"
    for name in ("fin", "trashed"):
        _owner(db, _TURN.text, {"c": org.c, "d": org.files[name], "u": plain.user_id})
    before = _owner(db, saved, {"c": org.c})
    for body in ({"changes": [{"page_no": 1, "turn": 180}]}, {"changes": [{"page_no": 99, "turn": 180}]}):
        replies = [
            plain.client.patch(f"/api/documents/{document}/page-turns", json=body, headers=CSRF)
            for document in (org.files["fin"], org.files["trashed"], str(uuid.uuid4()))
        ]
        assert [r.status_code for r in replies] == [404, 404, 404]
        assert replies[0].json() == replies[1].json() == replies[2].json()
    for document in (org.files["fin"], org.files["trashed"]):
        reset = plain.client.delete(f"/api/documents/{document}/page-turns", headers=CSRF)
        assert (reset.status_code, reset.json()["error"]["code"]) == (404, "not_found")
        assert plain.client.get(f"/api/documents/{document}").status_code == 404
    assert _owner(db, saved, {"c": org.c}) == before
    member = org.people["member"].client.get(f"/api/documents/{org.files['fin']}").json()
    assert member["page_turns"] == [{"page_no": 1, "turn": 90}]  # who can see it, still does
