"""Correct a line in the clean copy (#527) through the API: the clean copy's lines carry their
key and the correction in force; a reviewer or an admin corrects a line
(``POST /documents/{id}/lines/{key}/edit``) and Undoes it through the word history's Undo; a
member gets 403, another organisation 404, bad text 400, and the word edit's 409s. The rules
are in test_line_correction_db.py.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api.routers.word_review import MAX_TEXT
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from tests.test_api_support import CSRF, Api, Signed
from tests.test_line_correction_db import BILL, BILL_BOX, FIXED, TOTAL, TOTAL_BOX, bill, line_key

pytestmark = pytest.mark.db


def scan(api: Api, customer_id: str, **columns: Any) -> str:
    document_id = api.add_document(customer_id, detected_type="jpeg", **columns)
    PgPipelineStore(api.db).save(bill(customer_id, document_id))
    return document_id


def clean_copy(signed: Signed, document_id: str) -> list[dict[str, Any]]:
    reply = signed.client.get(f"/api/documents/{document_id}/clear-copy")
    assert reply.status_code == 200, reply.text
    lines: list[dict[str, Any]] = reply.json()["pages"][0]["lines"]
    return lines


def edit(signed: Signed, document_id: str, key: str, body: dict[str, Any]) -> Any:
    return signed.client.post(f"/api/documents/{document_id}/lines/{key}/edit", json=body, headers=CSRF)


def test_the_clean_copys_lines_carry_their_key_and_no_correction(db: Database) -> None:
    api = Api(db)
    member = api.new_signed("member")
    document_id = scan(api, member.customer_id)
    lines = clean_copy(member, document_id)
    assert [(line["text"], line["key"], line["correction"]) for line in lines] == [
        (TOTAL, line_key(TOTAL_BOX, TOTAL), None),
        (BILL, line_key(BILL_BOX, BILL), None),
    ]
    assert clean_copy(member, document_id) == lines  # stable across requests


@pytest.mark.parametrize("role", ["reviewer", "admin"])
def test_a_reviewer_corrects_a_line_and_undoes_it(db: Database, role: str) -> None:
    api = Api(db)
    reviewer = api.new_signed(role)
    document_id = scan(api, reviewer.customer_id)
    key = line_key(TOTAL_BOX, TOTAL)

    reply = edit(reviewer, document_id, key, {"text": f"  {FIXED} ", "expected_text": TOTAL})

    assert reply.status_code == 200, reply.text
    body = reply.json()
    change_id = body["change_id"]
    correction = dict(body["line"]["correction"])
    assert datetime.fromisoformat(correction.pop("at")).tzinfo is not None
    assert correction == {"before_text": TOTAL, "by": f"Test {role}", "change_id": change_id}
    assert (body["line"]["text"], body["line"]["key"]) == (FIXED, key)
    assert set(body) == {"change_id", "line"}
    shown = clean_copy(reviewer, document_id)[0]
    assert shown == body["line"]
    history = reviewer.client.get(f"/api/documents/{document_id}/changes").json()["items"]
    assert [(item["id"], item["on_page"], item["can_undo"]) for item in history] == [(change_id, True, True)]

    undone = reviewer.client.post(f"/api/documents/{document_id}/changes/{change_id}/undo", headers=CSRF)

    assert undone.status_code == 200, undone.text
    assert (undone.json()["undone_at"] is not None, undone.json()["on_page"]) == (True, True)
    line = clean_copy(reviewer, document_id)[0]
    assert (line["text"], line["correction"]) == (TOTAL, None)


def test_a_member_gets_403_and_nothing_changes(db: Database) -> None:
    api = Api(db)
    member = api.new_signed("member")
    document_id = scan(api, member.customer_id)
    reply = edit(member, document_id, line_key(TOTAL_BOX, TOTAL), {"text": FIXED, "expected_text": TOTAL})
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "forbidden")
    assert clean_copy(member, document_id)[0]["text"] == TOTAL


def test_another_organisations_document_or_an_unknown_line_is_404(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed("reviewer"), api.new_signed("reviewer")
    b_document = scan(api, b.customer_id)
    a_document = scan(api, a.customer_id)
    key = line_key(TOTAL_BOX, TOTAL)
    replies = [
        edit(a, b_document, key, {"text": FIXED, "expected_text": TOTAL}),
        edit(a, a_document, "0" * 64, {"text": FIXED, "expected_text": TOTAL}),
    ]
    assert [(reply.status_code, reply.json()["error"]["code"]) for reply in replies] == [
        (404, "not_found")
    ] * 2
    assert TOTAL not in replies[0].text
    assert clean_copy(b, b_document)[0]["text"] == TOTAL


@pytest.mark.parametrize(
    "typed",
    ["   ", "Total: 1500000/-\nmore", "Total: 1500000‮/-", "Total: \x00", "x" * (MAX_TEXT + 1)],
)
def test_bad_text_is_400(db: Database, typed: str) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document_id = scan(api, reviewer.customer_id)
    reply = edit(reviewer, document_id, line_key(TOTAL_BOX, TOTAL), {"text": typed, "expected_text": TOTAL})
    assert (reply.status_code, reply.json()["error"]["code"]) == (400, "bad_request")
    assert clean_copy(reviewer, document_id)[0]["correction"] is None


def test_the_word_edits_409s(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document_id = scan(api, reviewer.customer_id)
    key = line_key(TOTAL_BOX, TOTAL)
    stale = edit(reviewer, document_id, key, {"text": FIXED, "expected_text": "Total: 15000/-"})
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "changed_since")
    with db.customer(reviewer.customer_id) as conn:
        conn.execute(text("UPDATE documents SET status = 'clean' WHERE id = :d"), {"d": document_id})
    reading = edit(reviewer, document_id, key, {"text": FIXED, "expected_text": TOTAL})
    assert (reading.status_code, reading.json()["error"]["code"]) == (409, "not_ready")
    with db.customer(reviewer.customer_id) as conn:
        sql = "UPDATE documents SET status = 'ready', deleted_at = :at WHERE id = :d"
        conn.execute(text(sql), {"d": document_id, "at": datetime(2026, 11, 7, tzinfo=UTC)})
    binned = edit(reviewer, document_id, key, {"text": FIXED, "expected_text": TOTAL})
    assert (binned.status_code, binned.json()["error"]["code"]) == (409, "in_trash")
