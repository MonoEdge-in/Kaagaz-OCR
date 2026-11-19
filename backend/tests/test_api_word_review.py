"""Word review through the API (kaagaz/api/routers/word_review.py): the review status and
summary bar, the flags, Confirm and Edit by a reviewer or an admin, the history and Undo.
Members read flags without other readings and may change nothing (403). The rules are in
test_word_review_db.py; customer separation in test_isolation_api_word_review.py.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api.routers import word_review as router
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from tests.test_api_support import CSRF, Api, Signed
from tests.test_word_review_db import scanned

pytestmark = pytest.mark.db

FORBIDDEN = {"code": "forbidden", "message": "You don't have permission to do this."}


def scan(api: Api, customer_id: str, quality: str = "illegible", **more: Any) -> str:
    """A ready scan, saved as test_word_review_db.scanned makes it: 3 flags on 2 pages, the two
    word marks review items (#432: with ``quality`` "unsure" the word 4,850 is a check, no review
    item; PRD 9 D5: page 2's unpinned line is a check too). ``more`` goes to ``scanned``."""
    document_id = api.add_document(customer_id, detected_type="jpeg", page_count=2)
    PgPipelineStore(api.db).save(scanned(customer_id, document_id, quality=quality, **more))
    return document_id


def flags(signed: Signed, document_id: str, query: str = "") -> list[dict[str, Any]]:
    reply = signed.client.get(f"/api/documents/{document_id}/flags{query}")
    assert reply.status_code == 200, reply.text
    items: list[dict[str, Any]] = reply.json()["items"]
    return items


def post(signed: Signed, path: str, body: dict[str, Any] | None = None) -> Any:
    return signed.client.post(path, json=body, headers=CSRF)


def word(items: list[dict[str, Any]], as_read: str) -> dict[str, Any]:
    return next(item for item in items if item["as_read"] == as_read)


def test_the_review_status_counts_and_says_the_summary_bar(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document_id = scan(api, reviewer.customer_id)

    reply = reviewer.client.get(f"/api/documents/{document_id}/review-status")

    assert reply.status_code == 200, reply.text
    # Changed on purpose (PRD 9 F-11, D5): page 2's unpinned line is a check, so 2 review items, not 3.
    assert reply.json() == {
        "pages": 2,
        "words": 9,
        "flagged": 2,
        "by_band": {"check": 0, "review": 2},
        "reviewed": 0,
        "resolved": 0,
        "remaining": 2,
        "last_change_at": None,
        "status": "ready",
        "summary": "Scan complete · 2 pages · 9 words · 2 flagged for review",
        "export": {
            "ready": False,
            "message": "2 flagged words not yet reviewed. Export anyway or review first.",
        },
        "can_review": True,
        "ensemble": {"allowed": False, "reason": router.ENSEMBLE_OFF, "run": None},
    }
    member = api.sign_in(reviewer.customer_id, "member")
    seen = member.client.get(f"/api/documents/{document_id}/review-status").json()
    assert (seen["can_review"], seen["ensemble"]["reason"]) == (False, router.ENSEMBLE_ROLE)


def test_a_check_word_is_no_review_item_so_the_list_and_counts_agree(db: Database) -> None:
    # #432 (Tushar, 28 Oct 2026): "Don't take reviews on ?": an unsure word (check band, a
    # second look is wise) is neither listed nor counted; the [?] is.
    # Changed on purpose (PRD 9 F-11, D5): the unpinned line is a check now, neither listed nor counted.
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document_id = scan(api, reviewer.customer_id, quality="unsure")

    listed = flags(reviewer, document_id)
    status = reviewer.client.get(f"/api/documents/{document_id}/review-status").json()

    assert [(item["text"], item["band"]) for item in listed] == [("[?]", "review")]
    assert (status["flagged"], status["by_band"], status["remaining"]) == (1, {"check": 0, "review": 1}, 1)
    assert status["summary"] == "Scan complete · 2 pages · 9 words · 1 flagged for review"
    assert status["remaining"] == len([item for item in listed if item["state"] == "open"])


def test_a_document_still_being_read_has_no_summary_and_takes_no_change(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document_id = scan(api, reviewer.customer_id)
    key = word(flags(reviewer, document_id), "4,850")["word_key"]
    with db.customer(reviewer.customer_id) as conn:
        conn.execute(text("UPDATE documents SET status = 'clean' WHERE id = :d"), {"d": document_id})

    status = reviewer.client.get(f"/api/documents/{document_id}/review-status").json()
    assert (status["summary"], status["ensemble"]["reason"]) == (None, router.ENSEMBLE_WAIT)
    reply = post(reviewer, f"/api/documents/{document_id}/flags/{key}/confirm", {"expected_text": "4,850"})
    assert (reply.status_code, reply.json()["error"]) == (
        409,
        {"code": "not_ready", "message": router.MESSAGES["not_ready"]},
    )


def test_members_see_flags_without_other_readings_and_change_nothing(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document_id = scan(api, reviewer.customer_id)
    member = api.sign_in(reviewer.customer_id, "member")

    seen = flags(member, document_id)
    # Changed on purpose (PRD 9 F-11, D5): two review items; the unpinned line is a check.
    assert [item["alternatives"] for item in seen] == [None, None]
    assert [item["alternatives"] for item in flags(reviewer, document_id)] == [[], []]
    key = seen[0]["word_key"]
    for path, body in (
        (f"flags/{key}/confirm", {"expected_text": "4,850"}),
        (f"flags/{key}/edit", {"text": "4,350", "expected_text": "4,850"}),
        (f"changes/{uuid.uuid4()}/undo", None),
    ):
        reply = post(member, f"/api/documents/{document_id}/{path}", body)
        assert (reply.status_code, reply.json()["error"]) == (403, FORBIDDEN), path
    assert member.client.get(f"/api/documents/{document_id}/changes").json() == {
        "items": [],
        "next_before": None,
    }


@pytest.mark.parametrize("role", ["reviewer", "admin"])
def test_a_reviewer_edits_a_word_and_undoes_it(db: Database, role: str) -> None:
    api = Api(db)
    signed = api.new_signed(role)
    document_id = scan(api, signed.customer_id)
    base = f"/api/documents/{document_id}"
    mark = word(flags(signed, document_id), "4,850")
    assert (
        mark["kind"],
        mark["band"],
        mark["reason"],
        mark["reason_text"],
        mark["score"],
        mark["state"],
    ) == (
        "mark",
        "review",
        "smudged_print",
        "smudged print",
        0.62,
        "open",
    )

    blank = post(signed, f"{base}/flags/{mark['word_key']}/edit", {"text": "  ", "expected_text": "4,850"})
    assert (blank.status_code, blank.json()["error"]["message"]) == (400, "Type the correct text.")
    edited = post(
        signed, f"{base}/flags/{mark['word_key']}/edit", {"text": " 4,350 ", "expected_text": "4,850"}
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert (body["text"], body["state"], body["change"]["action"], body["change"]["by_you"]) == (
        "4,350",
        "reviewed",
        "edit",
        True,
    )
    stale = post(signed, f"{base}/flags/{mark['word_key']}/confirm", {"expected_text": "4,850"})
    assert (stale.status_code, stale.json()["error"]) == (
        409,
        {"code": "changed_since", "message": router.MESSAGES["changed_since"]},
    )
    # Changed on purpose (PRD 9 F-11, D5): page 2's unpinned line is a check, no review item.
    assert len(flags(signed, document_id, "?state=open")) == 1
    assert [item["kind"] for item in flags(signed, document_id, "?page=1")] == ["mark", "mark"]
    assert flags(signed, document_id, "?page=2") == []

    history = signed.client.get(f"{base}/changes").json()
    [item] = history["items"]
    assert (item["before_text"], item["after_text"], item["by"], item["on_page"], item["can_undo"]) == (
        "4,850",
        "4,350",
        "person",
        True,
        True,
    )
    assert signed.client.get(f"{base}/changes?source=ensemble").json()["items"] == []
    status = signed.client.get(f"{base}/review-status").json()
    assert (status["reviewed"], status["remaining"], status["last_change_at"] is not None) == (1, 1, True)

    undone = post(signed, f"{base}/changes/{item['id']}/undo")
    assert undone.status_code == 200, undone.text
    assert (undone.json()["undone_at"] is not None, undone.json()["can_undo"]) == (True, False)
    again = post(signed, f"{base}/changes/{item['id']}/undo")
    assert (again.status_code, again.json()["error"]["code"]) == (409, "not_latest")
    assert word(flags(signed, document_id), "4,850")["state"] == "open"
    assert [
        action for action in api.audit_actions(signed.customer_id) if action.startswith("word_review.")
    ] == [
        "word_review.edit",
        "word_review.undo",
    ]


def test_the_history_is_newest_first_in_pages(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document_id = scan(api, reviewer.customer_id)
    base = f"/api/documents/{document_id}"
    for item in flags(reviewer, document_id):
        assert (
            post(
                reviewer, f"{base}/flags/{item['word_key']}/confirm", {"expected_text": item["text"]}
            ).status_code
            == 200
        )
        api.clock.advance(1)

    # Changed on purpose (PRD 9 F-11, D5): two review items (the unpinned line is a check), so pages of 1.
    first = reviewer.client.get(f"{base}/changes?limit=1").json()
    assert [item["after_text"] for item in first["items"]] == ["[?]"]
    assert [item["can_undo"] for item in first["items"]] == [True]
    rest = reviewer.client.get(f"{base}/changes?limit=1&before={first['next_before']}").json()
    assert ([item["after_text"] for item in rest["items"]], rest["next_before"]) == (["4,850"], None)
    assert reviewer.client.get(f"{base}/changes?before={uuid.uuid4()}").status_code == 404
