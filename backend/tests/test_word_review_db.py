"""Word review against a real PostgreSQL (kaagaz.db.word_review, migration 0030).

The flags come from the stored reading; an edit goes into a value and a search piece only
where the old text is there exactly once, and a value is Verified, through the correction
screen's path, only once every flag in its box is reviewed by a person; Undo walks back
the latest change only and only over rows still as it left them; a save (a read again)
writes the changes in force again; a reviewed mark is no longer hard to read in answers.
The API and customer separation: test_api_word_review.py, test_isolation_api_word_review.py.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering.hard_to_read import PgReadingQuality
from kaagaz.chunking.piece import Piece
from kaagaz.db import word_review
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.extraction.values import ExtractedValue
from kaagaz.pipeline import DocumentContent, PageRecord, ReaderOutput
from kaagaz.reading.spans import BoxLocation, PageInfo, Span
from tests.db_support import insert_document, new_customer, new_trashed_document

pytestmark = pytest.mark.db

T0 = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
LINE = BoxLocation(1, 0.1, 0.1, 0.9, 0.15)
MARK = BoxLocation(1, 0.5, 0.1, 0.6, 0.15)
ILLEGIBLE = BoxLocation(1, 0.7, 0.1, 0.75, 0.15)
VALUE_BOX = BoxLocation(1, 0.45, 0.1, 0.65, 0.15)
UNPINNED = BoxLocation(2, 0.1, 0.5, 0.9, 0.55)
VECTOR = "[" + ",".join(["0.5"] * 1024) + "]"
VALUES = "SELECT value_text, value_number, label, label_reason FROM extracted_values WHERE document_id = :d"
PIECES = "SELECT text, embedding IS NULL AS cleared FROM pieces WHERE document_id = :d ORDER BY seq"
NO_MAJORITY = {
    "value": None,
    "agreed": 1,
    "readers": 3,
    "reads": [
        {"model": "a", "text": "Rs 4,850"},
        {"model": "b", "text": "Rs 4,350"},
        {"model": "c", "text": None},
    ],
}


def scanned(
    customer_id: str,
    document_id: str,
    *,
    mark: str = "4,850",
    reason: str = "unsure_text",
    votes: Any = None,
    quality: str = "unsure",
) -> DocumentContent:
    """A two-page scan: page 1's line holds the unsure word ``mark`` and a [?], with a total
    in the word's box; page 2's line is unpinned. Two pieces: the lines, and a line naming
    the word twice (so an edit never goes there). ``quality`` "illegible" makes the word a
    review item too (#432: an unsure word is a check, listed and counted nowhere)."""
    line = f"Total Rs {mark} paid [?]"
    spans = (
        Span(line, line, LINE, None, "paragraph", "ocr", quality="ok", box_source="found"),
        Span(mark, mark, MARK, 0.62, origin="ocr", quality=quality, box_source="found"),
        Span("[?]", "[?]", ILLEGIBLE, None, origin="ocr", quality="illegible", box_source="found"),
        Span("Bill to Patil Stores", "Bill to Patil Stores", UNPINNED, None, "paragraph", "ocr", quality="ok",
             box_source="model"),
    )  # fmt: skip
    pages = [
        PageRecord(PageInfo(1, 595.0, 842.0, "pt"), True, "ocr", read_quality="uncertain",
                   read_issues=("blurry", "illegible_words"), unsure_words=1, illegible_words=1),
        PageRecord(PageInfo(2, 595.0, 842.0, "pt"), True, "ocr", read_quality="uncertain",
                   read_issues=("unpinned",), unpinned_lines=1),
    ]  # fmt: skip
    pieces = [
        Piece(customer_id, document_id, f"{line}\nBill to Patil Stores", (LINE, UNPINNED), "needs_review"),
        Piece(customer_id, document_id, f"Paid {mark} of {mark}", (LINE,), "needs_review"),
    ]
    total = ExtractedValue(
        "document", "total_amount", f"Rs {mark}", f"Rs {mark}", VALUE_BOX, None, "Rs 4,350", "needs_review",
        reason, Decimal(mark.replace(",", "")), votes=votes,
    )  # fmt: skip
    outputs = [ReaderOutput("first", "google/gemini-3.1-pro-preview", "1", spans)]
    return DocumentContent(customer_id, document_id, pages, outputs, pieces, [total], reader="gemini")


def saved(db: Database, **kwargs: Any) -> tuple[str, str, str]:
    """(customer, its admin, a ready scan saved as ``scanned`` makes it)."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user, detected_type="jpeg")
    PgPipelineStore(db).save(scanned(customer, document, **kwargs))
    return customer, user, document


def load(db: Database, customer: str, document: str) -> word_review.Review:
    with db.customer(customer) as conn:
        return word_review.load(conn, customer, document)


def key(review: word_review.Review, kind: str, words: str) -> str:
    return next(flag.key for flag in review.flags if flag.kind == kind and flag.as_read == words)


def change(db: Database, customer: str, document: str, word: str, expected: str, new: str | None, user: str,
           now: datetime = T0) -> str:  # fmt: skip
    with db.customer(customer) as conn:
        return word_review.change_word(
            conn, customer, document, word, expected_text=expected, new_text=new, user_id=user, now=now
        )


def undo(db: Database, customer: str, document: str, change_id: str, user: str, now: datetime = T0) -> None:
    with db.customer(customer) as conn:
        word_review.undo_change(conn, customer, document, change_id, user_id=user, now=now)


def rows(db: Database, customer: str, sql: str, document: str) -> list[tuple[Any, ...]]:
    with db.customer(customer) as conn:
        return [tuple(row) for row in conn.execute(text(sql), {"d": document}).all()]


def refused(code: str, call: Any, *args: Any) -> None:
    with pytest.raises(word_review.Refused) as caught:
        call(*args)
    assert caught.value.code == code


def test_the_flags_come_from_the_stored_reading(db: Database) -> None:
    customer, _, document = saved(db)
    review = load(db, customer, document)
    assert [(f.kind, f.page_no, f.band, f.reason, f.text, f.score) for f in review.flags] == [
        ("mark", 1, "check", "smudged_print", "4,850", 0.62),
        ("mark", 1, "review", "smudged_print", "[?]", None),
        ("line", 2, "review", "spot_unconfirmed", "Bill to Patil Stores", None),
    ]
    # Changed on purpose (#432, Tushar, 28 Oct 2026): the unsure word (check band) is still a
    # flag, so a change to it is kept, but it is no review item: listed and counted nowhere.
    assert [flag.text for flag in review.listed] == ["[?]", "Bill to Patil Stores"]
    with db.customer(customer) as conn:
        numbers = word_review.counts(conn, customer, document, review)
    assert numbers == {
        "pages": 2,
        "words": 9,
        "flagged": 2,
        "by_band": {"check": 0, "review": 2},
        "reviewed": 0,
        "resolved": 0,
        "remaining": 2,
        "last_change_at": None,
    }


def test_an_edit_goes_where_the_text_is_there_once_and_verifies_the_value(db: Database) -> None:
    customer, user, document = saved(db)
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "UPDATE pieces SET embedding = CAST(:v AS halfvec(1024)), embedding_model = 'm'"
                " WHERE document_id = :d"
            ),
            {"v": VECTOR, "d": document},
        )
    word = key(load(db, customer, document), "mark", "4,850")

    change_id = change(db, customer, document, word, "4,850", "4,350", user)

    assert rows(db, customer, VALUES, document) == [("Rs 4,350", Decimal("4350"), "verified", "corrected")]
    assert rows(db, customer, PIECES, document) == [
        ("Total Rs 4,350 paid [?]\nBill to Patil Stores", True),
        ("Paid 4,850 of 4,850", False),  # twice there: never a guess
    ]
    corrections = (
        "SELECT choice, old_value, new_value, corrected_by::text FROM corrections WHERE document_id = :d"
    )
    assert rows(db, customer, corrections, document) == [("second", "Rs 4,850", "Rs 4,350", user)]
    audits = "SELECT action, target_type, details FROM audit_log WHERE action LIKE '%review%' ORDER BY id"
    assert [(action, target) for action, target, _ in rows(db, customer, audits, document)] == [
        ("review.resolve", "extracted_value"),
        ("word_review.edit", "document"),
    ]
    assert rows(db, customer, audits, document)[1][2] == {
        "change_id": change_id, "kind": "mark", "page_no": 1, "values": 1, "verified": 1, "pieces": 1,
    }  # fmt: skip
    review = load(db, customer, document)
    flag = review.flag(word)
    assert flag is not None
    assert (word_review.state(flag, review.latest()), word_review.current_text(flag, review.latest())) == (
        "reviewed",
        "4,350",
    )


def test_a_word_changed_since_or_no_longer_flagged_is_refused_and_nothing_is_written(db: Database) -> None:
    customer, user, document = saved(db)
    word = key(load(db, customer, document), "mark", "4,850")
    refused("changed_since", change, db, customer, document, word, "4,851", "4,350", user)
    refused("not_found", change, db, customer, document, "0" * 64, "4,850", None, user)
    assert rows(db, customer, "SELECT id FROM word_changes WHERE document_id = :d", document) == []
    assert rows(db, customer, VALUES, document) == [
        ("Rs 4,850", Decimal("4850"), "needs_review", "unsure_text")
    ]


def test_only_a_ready_current_document_out_of_trash_takes_a_change(db: Database) -> None:
    customer, user, document = saved(db)
    with db.customer(customer) as conn:
        being_read = insert_document(conn, customer, status="clean")
    binned = new_trashed_document(db, customer, deleted_at=T0)
    other, other_user = new_customer(db)
    refused("not_ready", change, db, customer, being_read, "0" * 64, "x", None, user)
    refused("in_trash", change, db, customer, binned, "0" * 64, "x", None, user)
    refused("not_found", change, db, other, document, "0" * 64, "x", None, other_user)


def test_undo_walks_back_the_latest_change_only(db: Database) -> None:
    customer, user, document = saved(db)
    review = load(db, customer, document)
    first = change(db, customer, document, key(review, "mark", "4,850"), "4,850", "4,350", user)
    second = change(db, customer, document, key(review, "mark", "[?]"), "[?]", "cash", user)
    assert rows(db, customer, PIECES, document)[0] == ("Total Rs 4,350 paid cash\nBill to Patil Stores", True)

    refused("not_latest", undo, db, customer, document, first, user)
    undo(db, customer, document, second, user, T0 + timedelta(minutes=1))
    assert rows(db, customer, PIECES, document)[0] == ("Total Rs 4,350 paid [?]\nBill to Patil Stores", True)
    assert rows(db, customer, VALUES, document) == [("Rs 4,350", Decimal("4350"), "verified", "corrected")]
    undo(db, customer, document, first, user, T0 + timedelta(minutes=2))
    assert rows(db, customer, VALUES, document) == [
        ("Rs 4,850", Decimal("4850"), "needs_review", "unsure_text")
    ]
    assert rows(db, customer, PIECES, document)[0] == ("Total Rs 4,850 paid [?]\nBill to Patil Stores", True)
    refused("not_latest", undo, db, customer, document, first, user)

    undone = "SELECT undone_at, undone_by::text FROM word_changes WHERE document_id = :d ORDER BY created_at"
    assert rows(db, customer, undone, document) == [
        (T0 + timedelta(minutes=2), user),
        (T0 + timedelta(minutes=1), user),
    ]
    review = load(db, customer, document)
    assert {word_review.state(flag, review.latest()) for flag in review.flags} == {"open"}


def test_undo_puts_back_nothing_when_a_row_changed_since(db: Database) -> None:
    customer, user, document = saved(db)
    first = change(
        db, customer, document, key(load(db, customer, document), "mark", "4,850"), "4,850", "4,350", user
    )
    with db.customer(customer) as conn:
        conn.execute(
            text("UPDATE extracted_values SET value_text = 'Rs 9' WHERE document_id = :d"), {"d": document}
        )
    refused("changed_since", undo, db, customer, document, first, user)
    assert rows(db, customer, PIECES, document)[0][0] == "Total Rs 4,350 paid [?]\nBill to Patil Stores"
    assert rows(db, customer, "SELECT undone_at FROM word_changes WHERE document_id = :d", document) == [
        (None,)
    ]


def test_a_save_writes_the_changes_again_and_keeps_words_no_longer_on_the_page(db: Database) -> None:
    customer, user, document = saved(db)
    review = load(db, customer, document)
    mark = key(review, "mark", "4,850")
    change(db, customer, document, mark, "4,850", "4,350", user)
    line = key(review, "line", "Bill to Patil Stores")
    change(db, customer, document, line, "Bill to Patil Stores", None, user)

    PgPipelineStore(db).save(scanned(customer, document))  # read again: every row replaced

    assert rows(db, customer, VALUES, document) == [("Rs 4,350", Decimal("4350"), "verified", "corrected")]
    assert rows(db, customer, PIECES, document)[0] == ("Total Rs 4,350 paid [?]\nBill to Patil Stores", True)
    review = load(db, customer, document)
    states = {flag.key: word_review.state(flag, review.latest()) for flag in review.flags}
    assert (states[mark], states[line], states[key(review, "mark", "[?]")]) == (
        "reviewed",
        "reviewed",
        "open",
    )

    PgPipelineStore(db).save(scanned(customer, document, mark="4,880"))  # the word reads otherwise now

    assert rows(db, customer, VALUES, document) == [
        ("Rs 4,880", Decimal("4880"), "needs_review", "unsure_text")
    ]
    review = load(db, customer, document)
    assert mark not in {flag.key for flag in review.flags} and len(review.changes) == 2
    flag = review.flag(line)
    assert flag is not None and word_review.state(flag, review.latest()) == "reviewed"


def test_a_reviewed_mark_is_no_longer_hard_to_read(db: Database) -> None:
    customer, user, document = saved(db)
    quality = PgReadingQuality(db)
    assert len(quality.word_marks(customer, [document])) == 2
    first = change(
        db, customer, document, key(load(db, customer, document), "mark", "4,850"), "4,850", None, user
    )
    marks = quality.word_marks(customer, [document])
    assert [(mark.location.x0, mark.location.x1) for mark in marks] == [(ILLEGIBLE.x0, ILLEGIBLE.x1)]
    undo(db, customer, document, first, user)
    assert len(quality.word_marks(customer, [document])) == 2


def test_a_value_the_readers_read_differently_is_verified_only_with_every_flag_in_its_box(
    db: Database,
) -> None:
    customer, user, document = saved(db, reason="readers_disagree", votes=NO_MAJORITY)
    review = load(db, customer, document)
    value = next(flag for flag in review.flags if flag.kind == "value")
    assert (value.band, value.reason, value.score, value.alternatives) == (
        "review",
        "readings_differ",
        0.3333,
        ("Rs 4,350",),
    )
    change(db, customer, document, key(review, "mark", "4,850"), "4,850", None, user)
    assert rows(db, customer, VALUES, document)[0][2:] == ("needs_review", "readers_disagree")

    change(db, customer, document, value.key, "Rs 4,850", "Rs 4,350", user)

    assert rows(db, customer, VALUES, document) == [("Rs 4,350", Decimal("4350"), "verified", "corrected")]
    assert rows(db, customer, PIECES, document)[0][0] == "Total Rs 4,350 paid [?]\nBill to Patil Stores"
    review = load(db, customer, document)
    kept = review.flag(value.key)  # still listed, reviewed, with the band and reason it had
    assert kept is not None and (kept.band, kept.reason, kept.text) == (
        "review",
        "readings_differ",
        "Rs 4,350",
    )
    with db.customer(customer) as conn:
        numbers = word_review.counts(conn, customer, document, review)
    # Changed on purpose (#432): the unsure word confirmed above is a check, counted nowhere.
    assert (numbers["flagged"], numbers["reviewed"], numbers["remaining"]) == (3, 1, 2)
