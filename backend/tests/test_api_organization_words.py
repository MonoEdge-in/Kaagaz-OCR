"""Members suggest shared words; reviewers approve them for their customer."""

# Changed on purpose (issue 359, 27 Oct 2026): POST /organization/words had no caller since
# #330 and was removed. A suggestion is made as chat's "Did you mean" pick makes one
# (``organization_words.submit``, no document), and one test checks the route stays gone.

from typing import Any

import pytest

from kaagaz.answering.expansion import PgWords
from kaagaz.answering.words_text import check_meaning, check_word
from kaagaz.db import organization_words
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db


def _suggest(db: Database, signed: Signed, word: str, meaning: str) -> Any:
    """An organization-wide suggestion by ``signed``, as chat's clarify pick submits one."""
    checked, checked_meaning = check_word(word).word, check_meaning(meaning)
    with db.user(signed.customer_id, signed.user_id) as conn:
        row = organization_words.submit(
            conn,
            signed.customer_id,
            signed.user_id,
            word=checked.text,
            word_fold=checked.fold,
            meaning=checked_meaning.text,
            meaning_fold=checked_meaning.fold,
        )
    return row if row is None or isinstance(row, str) else {"id": row.id, "status": row.status}


def test_the_suggest_route_is_gone_and_the_list_stays(db: Database) -> None:
    api = Api(db)
    member = api.new_signed()
    reply = member.client.post(
        "/api/organization/words", json={"word": "Jalna", "meaning": "district"}, headers=CSRF
    )
    assert reply.status_code == 405
    assert member.client.get("/api/organization/words").json()["items"] == []


def test_member_suggestion_is_private_until_other_reviewer_approves(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    member = api.sign_in(customer_id)
    reviewer = api.sign_in(customer_id, "reviewer")

    suggestion = _suggest(db, member, "Jalna", "district")
    assert suggestion["status"] == "pending"
    assert _suggest(db, member, "JALNA", "District") is None  # already pending
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
    actions = api.audit_actions(customer_id)  # a list of action names
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
    own = _suggest(db, member, "Jalna", "district")
    forbidden = member.client.post(
        f"/api/organization/words/{own['id']}/decision", json={"decision": "approve"}, headers=CSRF
    )
    assert forbidden.status_code == 403

    reviewer = api.sign_in(customer_id, "reviewer")
    theirs = _suggest(db, reviewer, "Nashik", "district")
    denied = reviewer.client.post(
        f"/api/organization/words/{theirs['id']}/decision", json={"decision": "approve"}, headers=CSRF
    )
    assert denied.status_code == 409


def test_suggestion_ids_are_scoped_to_the_customer(db: Database) -> None:
    api = Api(db)
    owner = api.new_signed()
    suggested = _suggest(db, owner, "Jalna", "district")
    other = api.new_signed("reviewer")
    reply = other.client.post(
        f"/api/organization/words/{suggested['id']}/decision",
        json={"decision": "approve"},
        headers=CSRF,
    )
    assert reply.status_code == 409
