"""Correct a line in the clean copy (#527, Tushar, 7 Nov 2026) against a real PostgreSQL: a
reviewer's correction of any line of the main reader goes, through word review's own path
(``word_review.change_word`` with ``line``), into the search pieces and the values only where
the line's text is there exactly once; the vector is cleared, the keyword index follows, the
stored reading is never changed; Undo, a read again, the audit and Home's ready answers follow.
The API: test_api_line_correction.py.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering import ready
from kaagaz.answering.service import AnswerService
from kaagaz.api import views
from kaagaz.chunking.piece import Piece
from kaagaz.db import word_review
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.extraction.flags import word_key
from kaagaz.extraction.values import ExtractedValue
from kaagaz.pipeline import DocumentContent, PageRecord, ReaderOutput
from kaagaz.reading.spans import BoxLocation, CellLocation, PageInfo, Span, location_to_json
from tests import test_word_review_db as word_tests
from tests.db_support import insert_document, new_customer
from tests.settings_factory import make_settings
from tests.test_answering_neighbours_db import DraftModel
from tests.test_search_db import run

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
TOTAL_BOX = BoxLocation(1, 0.1, 0.1, 0.9, 0.15)
AMOUNT_BOX = BoxLocation(1, 0.2, 0.1, 0.5, 0.15)  # the amount, inside the total's line
BILL_BOX = BoxLocation(1, 0.1, 0.3, 0.9, 0.35)
TOTAL, FIXED, BILL = "Total: 150000/-", "Total: 1500000/-", "Bill to Patil Stores"
VECTOR = "[" + ",".join(["0.5"] * 1024) + "]"
VALUES = "SELECT value_text, value_number, label, label_reason FROM extracted_values WHERE document_id = :d"
PIECES = "SELECT text, embedding IS NULL AS cleared FROM pieces WHERE document_id = :d ORDER BY seq"
READING = "SELECT output FROM reader_outputs WHERE document_id = :d"
# 150000 on its own, as written or with commas: never inside 1500000.
OLD_FIGURE = re.compile(r"(?<![\d,])(150000|1,50,000|150,000)(?![\d,])")


def bill(customer_id: str, document_id: str, total: str = TOTAL) -> DocumentContent:
    """A one-page scan read cleanly (no flag): the total's line, with its amount value, and
    the bill's line. Two pieces: both lines, and one naming the total twice."""
    spans = (
        Span(total, total, TOTAL_BOX, 0.97, "paragraph", "ocr", quality="ok", box_source="found"),
        Span(BILL, BILL, BILL_BOX, 0.97, "paragraph", "ocr", quality="ok", box_source="found"),
    )
    pages = [PageRecord(PageInfo(1, 595.0, 842.0, "pt"), True, "ocr")]
    pieces = [
        Piece(customer_id, document_id, f"{total}\n{BILL}", (TOTAL_BOX, BILL_BOX), "verified"),
        Piece(customer_id, document_id, f"Paid {total} of {total}", (TOTAL_BOX,), "verified"),
    ]
    amount = total.removeprefix("Total: ").removesuffix("/-")
    value = ExtractedValue(
        "document", "total_amount", amount, amount, AMOUNT_BOX, 0.97, None, "verified", "readers_agree",
        Decimal(amount),
    )  # fmt: skip
    outputs = [ReaderOutput("first", "google/gemini-3.1-pro-preview", "1", spans)]
    return DocumentContent(customer_id, document_id, pages, outputs, pieces, [value], reader="gemini")


def saved(db: Database) -> tuple[str, str, str]:
    """(customer, its admin, the bill saved, its pieces holding a vector)."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user, detected_type="jpeg")
    PgPipelineStore(db).save(bill(customer, document))
    with db.customer(customer) as conn:
        sql = "UPDATE pieces SET embedding = CAST(:v AS halfvec(1024)), embedding_model = 'm'"
        sql += " WHERE document_id = :d"
        conn.execute(text(sql), {"v": VECTOR, "d": document})
    return customer, user, document


def line_key(location: BoxLocation, as_read: str) -> str:
    """The flags' derivation for a line (kaagaz.extraction.flags.word_key)."""
    return word_key("line", location.page, location_to_json(location), as_read)


def correct(db: Database, customer: str, document: str, key: str, expected: str, new: str, user: str,
            now: datetime = T0) -> str:  # fmt: skip
    with db.customer(customer) as conn:
        return word_review.change_word(
            conn,
            customer,
            document,
            key,
            expected_text=expected,
            new_text=new,
            user_id=user,
            now=now,
            line=True,
        )


def undo(db: Database, customer: str, document: str, change_id: str, user: str, now: datetime) -> None:
    with db.customer(customer) as conn:
        word_review.undo_change(conn, customer, document, change_id, user_id=user, now=now)


def rows(db: Database, customer: str, sql: str, document: str) -> list[tuple[Any, ...]]:
    with db.customer(customer) as conn:
        return [tuple(row) for row in conn.execute(text(sql), {"d": document}).all()]


def refused(code: str, call: Any, *args: Any) -> None:
    with pytest.raises(word_review.Refused) as caught:
        call(*args)
    assert caught.value.code == code


def test_a_line_has_the_flags_key_and_the_lines_are_the_clean_copys(db: Database) -> None:
    customer, _, document = saved(db)
    with db.customer(customer) as conn:
        lines = word_review.lines(conn, customer, document)
    assert {key: row["text"] for key, row in lines.items()} == {
        line_key(TOTAL_BOX, TOTAL): TOTAL,
        line_key(BILL_BOX, BILL): BILL,
    }


def test_a_correction_goes_into_the_piece_and_the_value_once_and_clears_the_vector(db: Database) -> None:
    customer, user, document = saved(db)
    reading = rows(db, customer, READING, document)

    change_id = correct(db, customer, document, line_key(TOTAL_BOX, TOTAL), TOTAL, FIXED, user)

    assert rows(db, customer, PIECES, document) == [
        (f"{FIXED}\n{BILL}", True),
        (f"Paid {TOTAL} of {TOTAL}", False),  # twice there: never a guess
    ]
    # The amount is a part of the line, and the change falls inside it: the value follows.
    assert rows(db, customer, VALUES, document) == [("1500000", Decimal("1500000"), "verified", "corrected")]
    assert rows(db, customer, READING, document) == reading  # the original reading is kept
    history = "SELECT kind, action, as_read, before_text, after_text FROM word_changes WHERE document_id = :d"
    assert rows(db, customer, history, document) == [("line", "edit", TOTAL, TOTAL, FIXED)]
    audits = "SELECT action, details FROM audit_log WHERE action LIKE 'document.line%' ORDER BY id"
    assert rows(db, customer, audits, document) == [
        (
            "document.line_corrected",
            {"change_id": change_id, "kind": "line", "page_no": 1, "values": 1, "verified": 1, "pieces": 1},
        )
    ]


def test_keyword_search_finds_the_new_text_and_the_old_no_longer_matches_the_piece(db: Database) -> None:
    customer, user, document = saved(db)
    first_piece = "SELECT id::text FROM pieces WHERE document_id = :d AND seq = 0"
    piece = rows(db, customer, first_piece, document)[0][0]
    assert piece in [hit.piece_id for hit in run(db, customer, "150000") if hit.word_score]  # the control

    correct(db, customer, document, line_key(TOTAL_BOX, TOTAL), TOTAL, FIXED, user)

    found = [hit for hit in run(db, customer, "1500000") if hit.word_score]
    assert [(hit.piece_id, hit.document_id) for hit in found] == [(piece, document)]
    assert piece not in [hit.piece_id for hit in run(db, customer, "150000") if hit.word_score]


def test_ask_answers_the_corrected_total_and_cites_the_corrected_line(db: Database) -> None:
    """Tushar: "if I fix 150000 to 1500000, will Ask answer 1500000?" The AI is faked."""
    customer, user, document = saved(db)
    correct(db, customer, document, line_key(TOTAL_BOX, TOTAL), TOTAL, FIXED, user)
    settings = make_settings(answer_whole_doc_tokens=0, query_aspects_max=0)

    fixed = AnswerService(db=db, model=None, settings=settings, clock=lambda: T0)
    answer = fixed.answer(customer, "What is the total?").to_json()

    assert (answer["status"], answer["route"]) == ("answered", "fixed_query")
    said = " ".join(sentence["text"] for sentence in answer["sentences"])
    assert "15,00,000" in said or "1500000" in said, said
    [citation] = answer["citations"]
    assert (citation["document_id"], citation["snippet"]) == (document, "total_amount: 1500000")
    assert citation["regions"] == [location_to_json(AMOUNT_BOX)]  # on the corrected line
    assert not OLD_FIGURE.search(json.dumps(answer))

    model = DraftModel("1500000", "The total is Rs 1500000.")
    searched = AnswerService(db=db, model=model, settings=settings, clock=lambda: T0)
    answer = searched.answer(customer, "Which bill was for Patil Stores?").to_json()

    assert answer["status"] == "answered"
    [citation] = answer["citations"]
    assert FIXED in citation["snippet"] and BILL in citation["snippet"]
    assert not OLD_FIGURE.search(json.dumps(answer))
    assert all(not OLD_FIGURE.search(prompt) or "Paid" in prompt for prompt in model.prompts)


def test_a_change_inside_the_value_only_is_written_into_it(db: Database) -> None:
    customer, user, document = saved(db)
    key = line_key(TOTAL_BOX, TOTAL)
    # Added after the amount: no part of it, so the value keeps its text (never a guess).
    correct(db, customer, document, key, TOTAL, f"{TOTAL} only", user)
    assert rows(db, customer, VALUES, document) == [
        ("150000", Decimal("150000"), "verified", "readers_agree")
    ]
    assert word_review.line_part(TOTAL, "Total: 250000/-", "150000") == "250000"
    assert word_review.line_part(TOTAL, "Totals: 150000/-", "150000") is None  # outside it
    assert word_review.line_part("Total: 150000", "Total: 150000 only", "150000") is None  # at its edge
    assert word_review.line_part("Total: 150000", "Total: ", "150000") is None  # nothing left
    assert word_review.line_part("Paid 5 of 5", "Paid 6 of 5", "5") is None  # twice in the line


def test_a_changed_since_unknown_or_unchanged_line_is_refused_and_nothing_is_written(db: Database) -> None:
    customer, user, document = saved(db)
    key = line_key(TOTAL_BOX, TOTAL)
    refused("changed_since", correct, db, customer, document, key, "Total: 15000/-", FIXED, user)
    refused("not_found", correct, db, customer, document, "0" * 64, TOTAL, FIXED, user)
    refused("unchanged", correct, db, customer, document, key, TOTAL, TOTAL, user)
    assert rows(db, customer, "SELECT id FROM word_changes WHERE document_id = :d", document) == []
    assert rows(db, customer, PIECES, document)[0] == (f"{TOTAL}\n{BILL}", False)

    first = correct(db, customer, document, key, TOTAL, FIXED, user)
    refused("changed_since", correct, db, customer, document, key, TOTAL, "Total: 2/-", user)  # seen before
    second = correct(db, customer, document, key, FIXED, "Total: 1600000/-", user, T0 + timedelta(minutes=1))
    assert first != second
    assert rows(db, customer, PIECES, document)[0] == (f"Total: 1600000/-\n{BILL}", True)


def test_undo_puts_the_line_back(db: Database) -> None:
    customer, user, document = saved(db)
    change_id = correct(db, customer, document, line_key(TOTAL_BOX, TOTAL), TOTAL, FIXED, user)

    undo(db, customer, document, change_id, user, T0 + timedelta(minutes=1))

    assert rows(db, customer, PIECES, document) == [
        (f"{TOTAL}\n{BILL}", True),  # made again by the worker
        (f"Paid {TOTAL} of {TOTAL}", False),
    ]
    assert rows(db, customer, VALUES, document) == [
        ("150000", Decimal("150000"), "verified", "readers_agree")
    ]
    audits = "SELECT action, details FROM audit_log WHERE action LIKE 'document.line%' ORDER BY id"
    assert rows(db, customer, audits, document)[1] == (
        "document.line_correction_undone",
        {"change_id": change_id, "values": 1, "pieces": 1},
    )


def test_a_read_again_keeps_the_correction_while_the_line_is_on_the_page(db: Database) -> None:
    customer, user, document = saved(db)
    key = line_key(TOTAL_BOX, TOTAL)
    correct(db, customer, document, key, TOTAL, FIXED, user)

    PgPipelineStore(db).save(bill(customer, document))  # read again: every row replaced

    assert rows(db, customer, PIECES, document) == [
        (f"{FIXED}\n{BILL}", True),
        (f"Paid {TOTAL} of {TOTAL}", True),
    ]
    assert rows(db, customer, VALUES, document) == [("1500000", Decimal("1500000"), "verified", "corrected")]
    skipped = "SELECT applied -> 'skipped' FROM word_changes WHERE document_id = :d"
    assert rows(db, customer, skipped, document) == [({"pieces": 1, "values": 0},)]  # the same counts
    with db.customer(customer) as conn:
        assert key in word_review.on_page(
            conn, customer, document, word_review.load(conn, customer, document)
        )

    PgPipelineStore(db).save(bill(customer, document, "Total: 160000/-"))  # the line reads otherwise now

    assert rows(db, customer, PIECES, document)[0] == (f"Total: 160000/-\n{BILL}", True)
    with db.customer(customer) as conn:
        review = word_review.load(conn, customer, document)
        assert len(review.changes) == 1 and key not in word_review.on_page(conn, customer, document, review)


def test_audit_rows_hold_no_text(db: Database) -> None:
    customer, user, document = saved(db)
    change_id = correct(db, customer, document, line_key(TOTAL_BOX, TOTAL), TOTAL, FIXED, user)
    undo(db, customer, document, change_id, user, T0 + timedelta(minutes=1))
    details = "SELECT details::text FROM audit_log WHERE action LIKE 'document.line%' ORDER BY id"
    written = [row[0] for row in rows(db, customer, details, document)]
    assert len(written) == 2
    assert not any(word in row for row in written for word in ("Total", "150000", "Patil"))


def test_homes_ready_answers_are_made_again_after_a_correction_or_its_undo(db: Database) -> None:
    customer, user, document = saved(db)
    with db.customer(customer) as conn:
        before = ready.stamp(conn, customer)
    # The bill's line is under no value: only a piece changes.
    change_id = correct(db, customer, document, line_key(BILL_BOX, BILL), BILL, "Bill to Patel Stores", user)
    with db.customer(customer) as conn:
        corrected = ready.stamp(conn, customer)
    undo(db, customer, document, change_id, user, T0 + timedelta(minutes=1))
    with db.customer(customer) as conn:
        undone = ready.stamp(conn, customer)
    assert len({before, corrected, undone}) == 3


def test_a_spreadsheet_cell_is_corrected_in_its_piece_and_value(db: Database) -> None:
    """A cell has no box: the piece and the value at the same cell hold it."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user, detected_type="xlsx")
    cell = CellLocation(1, 2, 1)
    spans = (Span("150000", "150000", cell, None, None, "cell"),)
    value = ExtractedValue(
        "sheet:1:row:2", "total_amount", "150000", "150000", cell, None, None, "verified", "direct_cell",
        Decimal(150000),
    )  # fmt: skip
    content = DocumentContent(
        customer, document, [PageRecord(PageInfo(1, 1.0, 2.0, "cells"), False, "cells")],
        [ReaderOutput("first", "cells", "1", spans)],
        [Piece(customer, document, "A: 150000", (cell,), "verified")], [value], reader="cells",
    )  # fmt: skip
    PgPipelineStore(db).save(content)
    key = word_key("line", 1, location_to_json(cell), "150000")

    correct(db, customer, document, key, "150000", "1500000", user)

    assert rows(db, customer, PIECES, document) == [("A: 1500000", True)]
    assert rows(db, customer, VALUES, document) == [("1500000", Decimal("1500000"), "verified", "corrected")]


def test_a_correction_after_a_word_edit_in_the_line_goes_into_the_piece(db: Database) -> None:
    """#527 review (B1): the piece holds the line as the word edit left it, as the clean copy shows."""
    customer, user, document = word_tests.saved(db)
    as_read, edited = "Total Rs 4,850 paid [?]", "Total Rs 4,350 paid [?]"
    word = word_tests.key(word_tests.load(db, customer, document), "mark", "4,850")
    word_tests.change(db, customer, document, word, "4,850", "4,350", user)
    key = line_key(word_tests.LINE, as_read)
    with db.customer(customer) as conn:
        row = word_review.lines(conn, customer, document)[key]
        assert views.clear_line(row, word_review.in_force(conn, customer, document))["text"] == edited

    fixed = "Total Rs 4,350 paid in full"
    change_id = correct(db, customer, document, key, edited, fixed, user, T0 + timedelta(minutes=1))

    assert [piece for piece, _ in rows(db, customer, PIECES, document)] == [
        f"{fixed}\nBill to Patil Stores",
        "Paid 4,850 of 4,850",  # the line is not in it
    ]
    history = "SELECT before_text, applied -> 'skipped' FROM word_changes WHERE id = :d"
    # The second piece is over the line, and so is the total, which the change falls outside of.
    assert rows(db, customer, history, change_id) == [(edited, {"pieces": 1, "values": 1})]


def test_a_correction_leaves_a_value_on_the_next_line_as_it_is(db: Database) -> None:
    """#527 review (B2): the plot number's box reaches a few pixels into the survey line; only a
    value inside the line (or its centre) takes a part of the line's change."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user, detected_type="jpeg")
    PgPipelineStore(db).save(word_tests.survey(customer, document))

    correct(db, customer, document, line_key(word_tests.SURVEY_BOX, "Survey No. 12"), "Survey No. 12",
            "Survey No. 13", user)  # fmt: skip

    assert rows(db, customer, "SELECT value_text FROM extracted_values WHERE document_id = :d", document) == [
        ("12",)
    ]
    assert rows(db, customer, PIECES, document) == [("Survey No. 13\nPlot 12", True)]


def test_homes_ready_answers_stay_after_a_change_that_changes_no_text(db: Database) -> None:
    """#527 review (B5): a Confirm or Ensemble's resolve writes nothing, so it costs no model call."""
    customer, user, document = saved(db)
    with db.customer(customer) as conn:
        before = ready.stamp(conn, customer)
        sql = (
            "INSERT INTO word_changes (customer_id, document_id, word_key, kind, page_no, region,"
            " as_read, action, before_text, after_text, user_id) VALUES (:c, :d, :k, 'line', 1,"
            " CAST(:r AS jsonb), :t, 'confirm', :t, :t, :u)"
        )
        region = json.dumps(location_to_json(TOTAL_BOX))
        params = {
            "c": customer,
            "d": document,
            "k": line_key(TOTAL_BOX, TOTAL),
            "r": region,
            "t": TOTAL,
            "u": user,
        }
        conn.execute(text(sql), params)
        assert ready.stamp(conn, customer) == before
