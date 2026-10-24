"""File management (Phase 2 PRD 1) stage 3: two customers. B's ids, used by A, reach none
of B's rows through any new query: the document and folder timelines, the admin log's
filters (section 10: A filters with B's folder id and gets 0 rows) and its export, the
dashboard's activity, search's new filters and My words, and the questions.
"""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_support import Api
from tests.test_file_management_activity_db import ask, every
from tests.test_search_db import add_pieces
from tests.test_user_words_db import added

pytestmark = pytest.mark.db


def test_bs_ids_reach_none_of_bs_activity_through_a(db: Database) -> None:
    api = Api(db)
    a, b = api.new_admin(), api.new_admin()
    b_folder = api.add_folder(b.customer_id, b.user_id, "Land & Legal")
    b_document = api.add_document(
        b.customer_id, uploaded_by=b.user_id, folder_id=b_folder, name="b-secret.pdf"
    )
    a_document = api.add_document(a.customer_id, uploaded_by=a.user_id, name="a-own.pdf")
    b.client.get(f"/api/documents/{b_document}")
    a.client.get(f"/api/documents/{a_document}")

    assert a.client.get(f"/api/documents/{b_document}/activity").status_code == 404
    assert a.client.get(f"/api/folders/{b_folder}/activity").status_code == 404
    assert a.client.get(f"/api/documents/{b_document}/questions").status_code == 404
    seen = {item["target_id"] for item in every(a, "/api/admin/audit")}
    assert b_document not in seen and a_document in seen  # the control
    export = a.client.get("/api/admin/audit/export").text  # before A's exports name B's ids
    assert "a-own.pdf" in export and "b-secret.pdf" not in export and b_document not in export
    for params in ({"folder_id": b_folder}, {"document_id": b_document}, {"user_id": b.user_id}):
        body = a.client.get("/api/admin/audit", params=params).json()
        assert (body["items"], body["total"]) == ([], 0), params
        assert a.client.get("/api/admin/audit/export", params=params).text.splitlines()[1:] == [], params
    assert b_document not in {item["target_id"] for item in a.client.get("/api/dashboard").json()["activity"]}
    assert [item["target_id"] for item in every(b, f"/api/folders/{b_folder}/activity")] == [b_document]


def test_search_filters_and_my_words_stay_with_their_customer(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    b_folder = api.add_folder(b.customer_id, b.user_id, "Land & Legal")
    for signed, folder in ((a, None), (b, b_folder)):
        document = api.add_document(signed.customer_id, uploaded_by=signed.user_id, folder_id=folder)
        add_pieces(db, signed.customer_id, document, [("Warehouses at Bhiwandi", None)])
    added(db, b.customer_id, b.user_id, "khilwa", "warehouse")

    def found(params: dict[str, Any]) -> list[str]:
        reply = a.client.get("/api/search", params={"q": "Bhiwandi", **params})
        assert reply.status_code == 200, reply.text
        return [document["id"] for document in reply.json()["documents"]]

    assert len(found({})) == 1  # the control: A's own
    assert found({"department_id": b_folder}) == []
    assert found({"owner_id": b.user_id}) == []
    body = a.client.get("/api/search", params={"q": "khilwa"}).json()
    assert (body["documents"], body["understood_words"]) == ([], [])  # B's word is B's


def test_questions_stay_with_their_customer(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    b_document = api.add_document(b.customer_id)
    a_document = api.add_document(a.customer_id)
    ask(db, b, "B's question?", scope=[b_document], cited=[a_document])
    ask(db, a, "A's question?", scope=[a_document], cited=[])
    items = a.client.get(f"/api/documents/{a_document}/questions").json()["items"]
    assert [item["question"] for item in items] == ["A's question?"]


def test_people_lists_only_this_customers_active_users_by_name(db: Database) -> None:
    api = Api(db)
    # Changed on purpose (24 Oct 2026): each customer also has the first user made with it
    # ("Admin", new_customer), so it is listed too; and A disables the user, since with no
    # current customer row level security hides the row and the UPDATE changed nothing.
    (a_customer, a_first), (b_customer, b_first) = api.customer(), api.customer()
    a, b = api.admin_sign_in(a_customer), api.admin_sign_in(b_customer)
    member = api.sign_in(a.customer_id)
    gone = api.sign_in(a.customer_id)
    with db.customer(a.customer_id) as conn:
        conn.execute(text("UPDATE users SET disabled = true WHERE id = :u"), {"u": gone.user_id})
    reply = member.client.get("/api/people")
    assert reply.status_code == 200, reply.text
    items = reply.json()["items"]
    expected = {a_first, a.user_id, member.user_id}
    assert {item["id"] for item in items} == expected  # not B's, not the disabled one
    assert all(set(item) == {"id", "name"} for item in items)  # names only: no email or role
    b_items = b.client.get("/api/people").json()["items"]
    assert [item["id"] for item in b_items] == [b_first, b.user_id]  # by name: "Admin", "Test admin"
