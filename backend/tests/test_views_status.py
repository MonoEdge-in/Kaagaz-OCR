"""Waiting is explained (PRD 5 Q6) and a drop is summed up (Q1, U1), without a database:
the two waiting texts in the PRD's words, a wait for the reader that shows no step
count, and ``batch_view``'s counts, pages and time left from the uploader's own rows."""

import uuid
from datetime import timedelta
from typing import Any, cast

import pytest
from sqlalchemy import RowMapping

from kaagaz.api.auth import CurrentUser
from kaagaz.api.views import (
    READER_WAITING,
    batch_view,
    document_summary,
    document_time_left,
    files_ahead_message,
    in_progress,
    newer_version,
    progress_message,
    status_message,
)
from kaagaz.db.tags import TagRow
from kaagaz.eta import time_left
from tests.test_api_support import NOW

WAITING = {"step": "waiting"}


def _message(status: str = "received", **kwargs: Any) -> str:
    return status_message(status, None, detected_type="pdf", max_pages=100, **kwargs)


def test_q6_the_waiting_texts_are_the_prds_words() -> None:
    assert files_ahead_message(12) == "Waiting: 12 files ahead in your batch"
    assert files_ahead_message(1) == "Waiting: 1 file ahead in your batch"
    assert READER_WAITING == "Waiting for the reader to come back. This continues on its own."


@pytest.mark.parametrize("status", ["received", "clean"])
def test_q6_a_document_waiting_for_the_reader_says_so_and_shows_no_step(status: str) -> None:
    assert _message(status, progress=WAITING) == READER_WAITING
    assert _message(status, progress=WAITING, batch_ahead=4) == READER_WAITING  # the reader comes first
    assert _message(status, progress=WAITING, attempts=2, max_attempts=5) == READER_WAITING + " Try 2 of 5."
    assert in_progress(status, WAITING) is None


def test_q6_files_ahead_in_the_batch_show_while_it_waits_its_turn() -> None:
    assert _message(batch_ahead=12) == "Waiting: 12 files ahead in your batch"
    assert _message("clean", batch_ahead=1) == "Waiting: 1 file ahead in your batch"
    assert _message(batch_ahead=0) == "Waiting for the virus check."  # its turn: as before
    reading = {"step": "reading", "done": 2, "total": 5}
    assert _message("clean", progress=reading, batch_ahead=3) == "Reading page 3 of 5…"  # being read now
    assert _message("ready", batch_ahead=3) == "Ready. You can ask about it."  # only while in progress


# --- batch_view (Q1, U1) -------------------------------------------------------------

USER = CurrentUser(
    user_id="22222222-2222-4222-8222-222222222222",
    customer_id="11111111-1111-4111-8111-111111111111",
    email="member@example.test",
    display_name="Member",
    role="member",
    customer_name="Sharma Traders",
    scope="app",
    session_version=1,
    must_change_password=False,
    totp_enabled=False,
    delete_forever_allowed=False,
)


def _row(
    status: str, *, progress: dict[str, Any] | None = None, pages: int | None = 2, **counts: int
) -> RowMapping:
    document_id = str(uuid.uuid4())
    row = {
        "id": document_id,
        "display_name": "bill.pdf",
        "detected_type": "pdf",
        "size_bytes": 10,
        "status": status,
        "failure_code": None,
        "page_count": pages,
        "created_at": NOW,
        "uploaded_by": USER.user_id,
        "folder_id": None,
        "deleted_at": None,
        "family_id": document_id,
        "version_no": 1,
        "attempts": 0,
        "max_attempts": None,
        "progress": progress,
        "doc_type": None,
        "languages": [],
        "summary": None,
        "uploaded_by_name": "Member",
        "pages_unreadable": counts.get("unreadable", 0),
        "pages_uncertain": 0,
        "values_to_review": counts.get("to_review", 0),
        "words_unsure": 0,
        "autofile": None,
        "batch_ahead": counts.get("ahead", 0),
        "family_uploaders": [USER.user_id],
        "newer": None,
        "first_unreadable": None,
        "values_found": counts.get("values", 0),
    }
    return cast(RowMapping, row)


def _estimate(seconds: float) -> dict[str, Any]:
    """A stored ``reading`` progress whose estimate finishes ``seconds`` from NOW."""
    return {
        "step": "reading",
        "done": 1,
        "total": 4,
        "moved_at": NOW.isoformat(),
        "finish_at": (NOW + timedelta(seconds=seconds)).isoformat(),
        "started_at": (NOW - timedelta(seconds=10)).isoformat(),
        "item_s": 5.0,
        "after_s": 10.0,
        "slow_after_s": 20.0,
        "confidence": "low",
    }


def _view(rows: list[RowMapping], max_active: int = 2) -> dict[str, Any]:
    tags: dict[str, list[TagRow]] = {str(row["id"]): [] for row in rows}
    return batch_view("b1", rows, max_pages=100, tags=tags, user=USER, now=NOW, max_active=max_active)


def test_a_batch_counts_each_file_once_and_its_bar_is_pages_read_over_total() -> None:
    rows = [
        _row("ready"),  # ready: 2 of 2
        _row("ready", to_review=3),  # attention: 2 of 2
        _row("failed"),  # failed: read whole, 2 of 2
        _row("needs_reader", pages=None),  # failed: 1 of 1 (no page count)
        _row("clean", progress=_estimate(30.0), pages=4),  # reading: 1 of 4
        _row("received", pages=3, ahead=4),  # waiting: 0 of 3
        _row("clean", progress=WAITING),  # waiting for the reader: 0 of 2
    ]

    view = _view(rows)

    assert view["id"] == "b1"
    assert view["counts"] == {"total": 7, "ready": 1, "reading": 1, "waiting": 2, "attention": 1, "failed": 2}
    assert (view["pages_read"], view["pages_total"]) == (8, 16)
    assert [item["id"] for item in view["documents"]] == [str(row["id"]) for row in rows]  # upload order
    assert view["documents"][5]["status_message"] == "Waiting: 4 files ahead in your batch"
    assert view["documents"][6]["status_message"] == READER_WAITING
    assert view["documents"][6]["progress"] is None
    # The file being read: 30 s; the 5 pages waiting at its 5 s a page, 2 at a time.
    assert (view["time_left"]["seconds"], view["time_left"]["items_left"]) == (30.0 + 5 * 5.0 / 2, 8)


def test_a_batch_with_nothing_being_read_has_no_time_left() -> None:
    view = _view([_row("ready"), _row("received")])
    assert view["time_left"] is None
    assert view["counts"]["waiting"] == 1


@pytest.mark.parametrize("status", ["received", "clean"])
def test_q6_every_reader_of_the_progress_takes_a_wait_for_the_reader(status: str) -> None:
    """``{"step": "waiting"}`` has no done or total: each reader of a stored progress
    (the list, detail and upload replies, the newer version, the time left, the batch)
    shows the reader's wait with no step count and no time left, and none raises."""
    row = _row(status, progress=WAITING)
    summary = document_summary(row, max_pages=100, tags=[], user=USER, now=NOW)
    assert (summary["status_message"], summary["progress"], summary["time_left"]) == (
        READER_WAITING,
        None,
        None,
    )
    newer = newer_version({**row, "version_no": 2}, max_pages=100)
    assert newer is not None and newer["status_message"] == READER_WAITING
    assert (progress_message(WAITING), time_left(WAITING, now=NOW), document_time_left(row, now=NOW)) == (
        None,
        None,
        None,
    )
    view = _view([row])
    assert (view["counts"]["waiting"], view["documents"][0]["progress"], view["time_left"]) == (1, None, None)


def test_u1_a_batch_file_names_its_first_unreadable_page_with_the_reason_and_the_fix() -> None:
    page = {
        "page_no": 2,
        "read_issues": ["glare"],
        "illegible_words": 0,
        "unpinned_lines": 0,
        "effective_dpi": 300,
    }
    unsayable = {**page, "page_no": 1, "read_issues": ["illegible_words"]}  # its count is 0 now
    rows = [_row("ready", unreadable=1), _row("ready", unreadable=1), _row("ready")]
    rows[0] = cast(RowMapping, {**rows[0], "first_unreadable": page})
    rows[1] = cast(RowMapping, {**rows[1], "first_unreadable": unsayable})
    documents = _view(rows)["documents"]
    assert [item["first_unreadable"] for item in documents] == [
        {
            "page_no": 2,
            "says": "Glare or a shadow covers part of the page.",
            "fix": "Take the photo without flash, away from a window, with the page flat.",
        },
        {"page_no": 1, "says": None, "fix": None},  # never a made-up sentence
        None,
    ]


def test_u1_a_batch_file_says_how_many_values_were_found() -> None:
    """The tray says "All values verified" only for a file that has values; one with none
    (a letter, a photo of a page) is just Ready."""
    documents = _view([_row("ready", values=4), _row("ready")])["documents"]
    assert [item["values_found"] for item in documents] == [4, 0]
