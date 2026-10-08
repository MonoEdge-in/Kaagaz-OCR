"""The fixes for one page (PRD 5 F5): POST /api/documents/{id}/pages/{n}/read-again and
POST /api/documents/{id}/pages/{n}/accept ("Keep as is"), and the page fields and the
document summary counts the screens read (PRD 5 RT2, F1, F4).

Read one page again is a read again of its document (PRD 2 X1) in every rule: a
reviewer or an admin, the same states, one transaction (the page's mark, the document
back to the start, one ``document.reprocess`` audit row naming the page, then the job),
and the same REPROCESS_PER_HOUR and 10-minute cooldown, so each page read again is
counted and a page is read again at most once in 10 minutes (the council's security
finding). Keep as is records who and when; the page stays flagged but leaves the
attention counts. Customer separation is tests/test_isolation_api_page_fixes.py.
"""

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, NOW, Api, Signed

pytestmark = pytest.mark.db

NOT_SCANNED = {
    "error": {
        "code": "not_reprocessable",
        "message": "Only a page read from a scan or photo can be read again.",
    }
}
NOT_REPROCESSABLE = {
    "error": {
        "code": "not_reprocessable",
        "message": "This document can't be read again right now. Only a ready, failed or unreadable document"
        " that is not in Trash can be.",
    }
}
HOURLY = "Too many documents were sent to be read again in the last hour. Please try again later."
COOLDOWN = "This document was sent to be read again less than 10 minutes ago. Please wait and try again."


def read_again(signed: Signed, document_id: str, page_no: int) -> Any:
    return signed.client.post(f"/api/documents/{document_id}/pages/{page_no}/read-again", headers=CSRF)


def accept(signed: Signed, document_id: str, page_no: int) -> Any:
    return signed.client.post(f"/api/documents/{document_id}/pages/{page_no}/accept", headers=CSRF)


_READ_PAGE = text(
    "UPDATE pages SET route = :route, read_by = :read_by, read_quality = :read_quality,"
    " read_issues = :read_issues, unsure_words = :unsure_words, illegible_words = :illegible_words,"
    " unpinned_lines = :unpinned_lines WHERE customer_id = :c AND document_id = :d AND page_no = :n"
)
_GOOD_OCR_PAGE: dict[str, Any] = {
    "route": "ocr",
    "read_by": "gemini-3.1-pro-preview",
    "read_quality": "good",
    "read_issues": [],
    "unsure_words": 0,
    "illegible_words": 0,
    "unpinned_lines": 0,
}


def read_page(api: Api, customer_id: str, document_id: str, page_no: int = 1, **columns: Any) -> None:
    """A page as a run left it: read well by the reading model, unless ``columns`` say otherwise."""
    api.add_page(customer_id, document_id, page_no)
    with api.db.customer(customer_id) as conn:
        conn.execute(
            _READ_PAGE,
            {**_GOOD_OCR_PAGE, **columns, "c": customer_id, "d": document_id, "n": page_no},
        )


def scanned(api: Api, customer_id: str, pages: int = 2, **columns: Any) -> str:
    """A ready scanned document with ``pages`` pages read by OCR."""
    document_id = api.add_document(customer_id, page_count=pages, **columns)
    for page_no in range(1, pages + 1):
        read_page(api, customer_id, document_id, page_no)
    return document_id


def marks(api: Api, customer_id: str, document_id: str) -> dict[int, tuple[object, object, object]]:
    """Each page's (read_again_requested_at, accepted_by, accepted_at)."""
    with api.db.customer(customer_id) as conn:
        rows = conn.execute(
            text(
                "SELECT page_no, read_again_requested_at, accepted_by::text, accepted_at FROM pages"
                " WHERE customer_id = :c AND document_id = :d ORDER BY page_no"
            ),
            {"c": customer_id, "d": document_id},
        ).all()
    return {int(row[0]): (row[1], row[2], row[3]) for row in rows}


def status(api: Api, customer_id: str, document_id: str) -> str:
    with api.db.customer(customer_id) as conn:
        found: str = conn.execute(
            text("SELECT status FROM documents WHERE customer_id = :c AND id = :d"),
            {"c": customer_id, "d": document_id},
        ).scalar_one()
    return found


def set_ready(api: Api, customer_id: str, document_id: str) -> None:
    """The queued job ran and the document is ready again."""
    with api.db.customer(customer_id) as conn:
        conn.execute(
            text("UPDATE documents SET status = 'ready' WHERE customer_id = :c AND id = :d"),
            {"c": customer_id, "d": document_id},
        )


# --- read one page again (F5) -------------------------------------------------------------


@pytest.mark.parametrize("role", ["reviewer", "admin"])
def test_f5_a_reviewer_or_an_admin_reads_one_page_again(db: Database, role: str) -> None:
    api = Api(db)
    signed = api.new_signed(role)
    c = signed.customer_id
    doc = scanned(api, c)

    reply = read_again(signed, doc, 2)

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["id"], body["status"], body["attempts"]) == (doc, "received", 0)
    assert marks(api, c, doc) == {1: (None, None, None), 2: (NOW, None, None)}
    assert [(job.customer_id, job.document_id) for job in api.queue.jobs] == [(c, doc)]
    assert api.audit_rows(c)[-1] == ("document.reprocess", "document", doc, {"status": "ready", "page_no": 2})
    page = signed.client.get(f"/api/documents/{doc}").json()["pages"][1]
    assert datetime.fromisoformat(page["read_again_requested_at"]) == NOW


def test_f5_a_member_gets_403_and_nothing_changes(db: Database) -> None:
    api = Api(db)
    member = api.new_signed("member")
    doc = scanned(api, member.customer_id)
    reply = read_again(member, doc, 1)
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "forbidden")
    assert marks(api, member.customer_id, doc)[1] == (None, None, None)
    assert status(api, member.customer_id, doc) == "ready"
    assert api.queue.jobs == []


@pytest.mark.parametrize("page_no", [0, 3, 99])
def test_f5_a_page_the_document_does_not_have_is_404_and_nothing_is_counted(
    db: Database, page_no: int
) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = scanned(api, c)
    reply = read_again(reviewer, doc, page_no)
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    assert status(api, c, doc) == "ready"
    assert api.queue.jobs == []
    assert "document.reprocess" not in api.audit_actions(c)


@pytest.mark.parametrize("route", ["text_layer", "cells", "blank", None])
def test_f5_only_a_page_read_from_a_scan_can_be_read_again(db: Database, route: str | None) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = api.add_document(c)
    read_page(api, c, doc, 1, route=route)
    reply = read_again(reviewer, doc, 1)
    assert (reply.status_code, reply.json()) == (409, NOT_SCANNED)
    assert status(api, c, doc) == "ready"
    assert marks(api, c, doc)[1] == (None, None, None)
    assert "document.reprocess" not in api.audit_actions(c)


@pytest.mark.parametrize("state", ["received", "clean", "rejected", "in_trash"])
def test_f5_a_page_of_a_document_being_read_rejected_or_in_trash_is_409(db: Database, state: str) -> None:
    """PRD 5 section 12: read again on one page while the document is being read is
    refused with 409 until the run ends."""
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    if state == "in_trash":
        doc = scanned(api, c, deleted_at=api.clock(), deleted_by=reviewer.user_id)
    else:
        doc = scanned(api, c, status=state, failure_code="virus_found" if state == "rejected" else None)

    reply = read_again(reviewer, doc, 1)

    assert (reply.status_code, reply.json()) == (409, NOT_REPROCESSABLE)
    assert marks(api, c, doc)[1] == (None, None, None)
    assert api.queue.jobs == []
    assert "document.reprocess" not in api.audit_actions(c)


def test_f5_a_queue_that_is_down_is_503_and_the_page_is_not_marked(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = scanned(api, c)
    api.queue.fail_enqueue = True

    reply = read_again(reviewer, doc, 1)

    assert (reply.status_code, reply.json()["error"]["code"]) == (503, "backend_unavailable")
    assert marks(api, c, doc)[1] == (None, None, None)  # the whole transaction rolled back
    assert status(api, c, doc) == "ready"
    assert "document.reprocess" not in api.audit_actions(c)


def test_f5_each_page_read_again_counts_against_reprocess_per_hour(db: Database) -> None:
    """Security (council): one page read again counts like one document read again."""
    api = Api(db, settings=make_settings(reprocess_per_hour=2))
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    first, second, third = scanned(api, c), scanned(api, c), scanned(api, c)

    assert read_again(reviewer, first, 1).status_code == 200
    assert reviewer.client.post(f"/api/documents/{second}/reprocess", headers=CSRF).status_code == 200
    reply = read_again(reviewer, third, 1)

    assert (reply.status_code, reply.json()["error"]) == (429, {"code": "rate_limited", "message": HOURLY})
    assert marks(api, c, third)[1] == (None, None, None)
    assert status(api, c, third) == "ready"
    assert len(api.queue.jobs) == 2


def test_f5_a_page_is_read_again_at_most_once_in_10_minutes(db: Database) -> None:
    """The per-page cooldown (council security finding): the same page, and any other
    page of its document, waits out the document's 10 minutes."""
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = scanned(api, c)
    assert read_again(reviewer, doc, 1).status_code == 200
    set_ready(api, c, doc)

    same, other = read_again(reviewer, doc, 1), read_again(reviewer, doc, 2)

    for reply in (same, other):
        assert (reply.status_code, reply.json()["error"]) == (
            429,
            {"code": "rate_limited", "message": COOLDOWN},
        )
    assert marks(api, c, doc)[2] == (None, None, None)
    assert len(api.queue.jobs) == 1


def test_f5_a_page_read_again_starts_the_cooldown_of_its_whole_document(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc, other = scanned(api, c), scanned(api, c)
    assert read_again(reviewer, doc, 2).status_code == 200
    set_ready(api, c, doc)

    reply = reviewer.client.post(f"/api/documents/{doc}/reprocess", headers=CSRF)

    assert (reply.status_code, reply.json()["error"]["message"]) == (429, COOLDOWN)
    assert read_again(reviewer, other, 2).status_code == 200  # the cooldown is per document


def test_f5_read_again_needs_the_csrf_header(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    doc = scanned(api, reviewer.customer_id)
    reply = reviewer.client.post(f"/api/documents/{doc}/pages/1/read-again")
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "csrf")
    assert api.queue.jobs == []


def test_f5_read_again_role_by_owner_matrix(db: Database) -> None:
    """A member is refused anywhere; a reviewer or an admin may only act on their own
    customer's pages, and another customer's document is simply not found."""
    api = Api(db)
    owner, _ = api.customer()
    stranger, _ = api.customer()
    results = {}
    for role in ("member", "reviewer", "admin"):
        for whose in ("own", "other"):
            signed = api.sign_in(owner if whose == "own" else stranger, role)
            doc = scanned(api, owner)
            results[(role, whose)] = read_again(signed, doc, 1).status_code
    assert results == {
        ("member", "own"): 403,
        ("member", "other"): 403,
        ("reviewer", "own"): 200,
        ("reviewer", "other"): 404,
        ("admin", "own"): 200,
        ("admin", "other"): 404,
    }


# --- Keep as is (F5) ----------------------------------------------------------------------


@pytest.mark.parametrize("role", ["reviewer", "admin"])
def test_f5_keep_as_is_records_who_and_when_and_the_page_stays_flagged(db: Database, role: str) -> None:
    api = Api(db)
    signed = api.new_signed(role)
    c = signed.customer_id
    doc = api.add_document(c, page_count=2)
    read_page(api, c, doc, 1, read_quality="unreadable", read_issues=["blurry"], read_by="rapidocr")
    read_page(api, c, doc, 2, read_quality="uncertain", read_issues=["illegible_words"], illegible_words=3)
    before = signed.client.get(f"/api/documents/{doc}").json()
    assert (before["pages_unreadable"], before["pages_uncertain"]) == (1, 1)

    reply = accept(signed, doc, 1)

    assert reply.status_code == 200, reply.text
    page = reply.json()
    assert (page["page_no"], page["read_quality"], page["read_issues"]) == (1, "unreadable", ["blurry"])
    assert (page["accepted_by"], datetime.fromisoformat(page["accepted_at"])) == (signed.user_id, NOW)
    assert marks(api, c, doc)[1] == (None, signed.user_id, NOW)
    assert api.audit_rows(c)[-1] == (
        "document.page_accept",
        "document",
        doc,
        {"page_no": 1, "read_quality": "unreadable"},
    )
    after = signed.client.get(f"/api/documents/{doc}").json()
    assert (after["pages_unreadable"], after["pages_uncertain"]) == (0, 1)  # left the attention list
    assert after["pages"][0]["read_quality"] == "unreadable"  # still flagged on the page
    assert api.queue.jobs == []  # nothing is read again


def test_f5_keep_as_is_member_403_no_page_404_trash_409(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    member = api.sign_in(c, "member")
    doc = scanned(api, c)
    binned = scanned(api, c, deleted_at=api.clock(), deleted_by=reviewer.user_id)

    refused = accept(member, doc, 1)
    missing = [accept(reviewer, doc, page_no) for page_no in (0, 3)]
    trashed = accept(reviewer, binned, 1)

    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "forbidden")
    assert [(reply.status_code, reply.json()["error"]["code"]) for reply in missing] == [
        (404, "not_found"),
        (404, "not_found"),
    ]
    assert (trashed.status_code, trashed.json()["error"]) == (
        409,
        {"code": "in_trash", "message": "This document is in the Trash. Restore it first."},
    )
    assert marks(api, c, doc)[1] == (None, None, None)
    assert marks(api, c, binned)[1] == (None, None, None)
    assert "document.page_accept" not in api.audit_actions(c)


def test_f5_keep_as_is_needs_the_csrf_header(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    doc = scanned(api, reviewer.customer_id)
    reply = reviewer.client.post(f"/api/documents/{doc}/pages/1/accept")
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "csrf")
    assert marks(api, reviewer.customer_id, doc)[1] == (None, None, None)


# --- the page fields and the summary counts (RT2, F1, F4) ----------------------------------


def test_rt2_f1_each_page_says_who_read_it_and_how_well(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    doc = api.add_document(c, page_count=3)
    read_page(
        api,
        c,
        doc,
        1,
        read_by="gemini-3.1-pro-preview",
        read_quality="uncertain",
        read_issues=["handwriting_hard", "illegible_words", "unpinned"],
        unsure_words=4,
        illegible_words=2,
        unpinned_lines=1,
    )
    read_page(api, c, doc, 2, read_by="rapidocr", read_quality="unreadable", read_issues=["read_locally"])
    read_page(api, c, doc, 3, route="text_layer", read_by="text_layer")

    pages = signed.client.get(f"/api/documents/{doc}").json()["pages"]

    shown = [
        (
            page["read_by"],
            page["read_quality"],
            page["read_issues"],
            page["unsure_words"],
            page["illegible_words"],
            page["unpinned_lines"],
        )
        for page in pages
    ]
    assert shown == [
        ("gemini-3.1-pro-preview", "uncertain", ["handwriting_hard", "illegible_words", "unpinned"], 4, 2, 1),
        ("rapidocr", "unreadable", ["read_locally"], 0, 0, 0),
        ("text_layer", "good", [], 0, 0, 0),
    ]


def test_f4_the_summary_counts_pages_to_attend_to_and_values_to_review(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    doc = api.add_document(c, page_count=4)
    read_page(api, c, doc, 1, read_quality="unreadable", read_issues=["blurry"])
    read_page(api, c, doc, 2, read_quality="unreadable", read_issues=["no_text"])
    read_page(api, c, doc, 3, read_quality="uncertain", read_issues=["unpinned"], unpinned_lines=2)
    read_page(api, c, doc, 4)
    api.add_value(c, doc, page_no=3, reason="box_unanchored")
    api.add_value(c, doc, page_no=3, field_name="gstin", reason="unsure_text")
    api.add_value(c, doc, page_no=4, field_name="date", label="verified", reason="readers_agree")
    clean = api.add_document(c)

    listed = {item["id"]: item for item in signed.client.get("/api/documents").json()["items"]}
    detail = signed.client.get(f"/api/documents/{doc}").json()

    counts = ("pages_unreadable", "pages_uncertain", "values_to_review")
    assert tuple(listed[doc][name] for name in counts) == (2, 1, 2)
    assert tuple(detail[name] for name in counts) == (2, 1, 2)
    assert tuple(listed[clean][name] for name in counts) == (0, 0, 0)
    reasons = sorted(value["label_reason"] for value in detail["values"])
    assert reasons == ["box_unanchored", "readers_agree", "unsure_text"]  # PRD 5 reasons, as stored
