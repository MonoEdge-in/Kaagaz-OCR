"""Members suggest shared words; reviewers approve them for their customer."""

import pytest

from kaagaz.answering.expansion import PgWords
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api

pytestmark = pytest.mark.db


def test_member_suggestion_is_private_until_other_reviewer_approves(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    member = api.sign_in(customer_id)
    reviewer = api.sign_in(customer_id, "reviewer")

    submitted = member.client.post(
        "/api/organization/words", json={"word": "Jalna", "meaning": "district"}, headers=CSRF
    )
    assert submitted.status_code == 201, submitted.text
    suggestion = submitted.json()["item"]
    assert suggestion["status"] == "pending"
    duplicate = member.client.post(
        "/api/organization/words", json={"word": "JALNA", "meaning": "District"}, headers=CSRF
    )
    assert duplicate.status_code == 409
    member_items = member.client.get("/api/organization/words").json()["items"]
    assert [item["status"] for item in member_items] == ["pending"]

    # Pending submissions are not exposed to ordinary colleagues.
    colleague = api.sign_in(customer_id)
    assert colleague.client.get("/api/organization/words").json()["items"] == []

    queue = reviewer.client.get("/api/organization/words").json()["items"]
    assert [item["id"] for item in queue] == [suggestion["id"]]
    decided = reviewer.client.post(
        f"/api/organization/words/{suggestion['id']}/decision",
        json={"decision": "approve"},
        headers=CSRF,
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["item"]["status"] == "approved"
    approved = member.client.get("/api/organization/words").json()["items"]
    assert approved[0]["status"] == "approved"
    expanded = [
        (word.word, word.meaning, word.origin) for word in PgWords(db).words(customer_id, member.user_id)
    ]
    assert expanded == [("Jalna", "district", "organization")]
    actions = [row[0] for row in api.audit_actions(customer_id)]
    assert "organization_word.suggested" in actions
    assert "organization_word.approved" in actions


def test_viewer_correction_reaches_review_and_approval_is_document_scoped(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    member = api.sign_in(customer_id)
    reviewer = api.sign_in(customer_id, "reviewer")
    colleague = api.sign_in(customer_id)
    document = api.add_document(customer_id, page_count=2)
    other_document = api.add_document(customer_id, page_count=2)

    saved = member.client.post(
        "/api/me/words",
        json={
            "word": "Jalna",
            "meaning": "district",
            "spot": {
                "document_id": document,
                "page_no": 2,
                "region": {"kind": "box", "page": 2, "box": [0.1, 0.2, 0.3, 0.4]},
            },
        },
        headers=CSRF,
    )
    assert saved.status_code == 201, saved.text
    pending = reviewer.client.get("/api/organization/words").json()["items"]
    assert [(item["word"], item["document_id"], item["page_no"], item["status"]) for item in pending] == [
        ("Jalna", document, 2, "pending")
    ]

    suggestion_id = pending[0]["id"]
    approved = reviewer.client.post(
        f"/api/organization/words/{suggestion_id}/decision", json={"decision": "approve"}, headers=CSRF
    )
    assert approved.status_code == 200, approved.text
    assert [(word.word, word.meaning) for word in PgWords(db).words(customer_id, colleague.user_id)] == []
    assert [
        (word.word, word.meaning)
        for word in PgWords(db).words(customer_id, colleague.user_id, document_id=document)
    ] == [("Jalna", "district")]
    assert PgWords(db).words(customer_id, colleague.user_id, document_id=other_document) == []


def test_members_cannot_decide_and_submitters_cannot_approve_their_own_words(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    member = api.sign_in(customer_id)
    own = member.client.post(
        "/api/organization/words", json={"word": "Jalna", "meaning": "district"}, headers=CSRF
    ).json()["item"]
    forbidden = member.client.post(
        f"/api/organization/words/{own['id']}/decision", json={"decision": "approve"}, headers=CSRF
    )
    assert forbidden.status_code == 403

    reviewer = api.sign_in(customer_id, "reviewer")
    theirs = reviewer.client.post(
        "/api/organization/words", json={"word": "Nashik", "meaning": "district"}, headers=CSRF
    ).json()["item"]
    denied = reviewer.client.post(
        f"/api/organization/words/{theirs['id']}/decision", json={"decision": "approve"}, headers=CSRF
    )
    assert denied.status_code == 409


def test_suggestion_ids_are_scoped_to_the_customer(db: Database) -> None:
    api = Api(db)
    owner = api.new_signed()
    suggested = owner.client.post(
        "/api/organization/words", json={"word": "Jalna", "meaning": "district"}, headers=CSRF
    ).json()["item"]
    other = api.new_signed("reviewer")
    reply = other.client.post(
        f"/api/organization/words/{suggested['id']}/decision",
        json={"decision": "approve"},
        headers=CSRF,
    )
    assert reply.status_code == 409
