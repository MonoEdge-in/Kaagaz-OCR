"""Ensemble after the scan, the parts with no database (kaagaz/jobs/ensemble.py,
kaagaz/db/ensemble.py, kaagaz/api/routers/ensemble.py; scan review UX rules, 16 Oct 2026).

The vote is upload Ensemble's rule (``labels.majority`` gives what ``labels.vote`` gave); a
model's reading is its words lined up with the stored text; a word mark is read on its line,
the crop grown as the checker's; the run's error says the pages in the owner's words; the
queue carries ``run_id`` only when set; the worker sends a run to its handler and marks the
run, never the document; who reads again; and no new string has a banned word.
The job and the API against PostgreSQL: test_ensemble_job_db.py, test_api_ensemble.py.
"""

import re
from typing import Any, cast

import pytest
from PIL import Image
from sqlalchemy import RowMapping

from kaagaz.adapters.queue import _body, _job_from_body
from kaagaz.api import views
from kaagaz.api.routers import documents as documents_router
from kaagaz.api.routers import ensemble as router
from kaagaz.db import ensemble as ensemble_sql
from kaagaz.db import models
from kaagaz.db.models import AiNotAllowed
from kaagaz.extraction.flags import LINE, MARK, READINGS_DIFFER, REVIEW, Flag, span_flag
from kaagaz.extraction.labels import majority, vote
from kaagaz.extraction.values import ExtractedValue
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.ensemble import aligned, reading_of
from kaagaz.pipeline import _check_crop
from kaagaz.reading.spans import BoxLocation, Span, location_to_json
from kaagaz.worker_main import MarkExhausted, RecordAttempt, dispatch
from tests.settings_factory import make_settings
from tests.test_ai_resolve import CUSTOMER, DOCUMENT, FakeConn, document, row
from tests.test_word_review_text import BANNED

LINE_BOX = BoxLocation(1, 0.1, 0.1, 0.9, 0.15)
MARK_BOX = BoxLocation(1, 0.5, 0.1, 0.6, 0.15)
LINE_TEXT = "Total Rs 4,850 paid [?]"
GEMINI = make_settings(ocr_engine="gemini")
LEAD, SECOND, THIRD, FOURTH = "google/lead", "google/second", "google/third", "google/fourth"


def flag(kind: str, box: BoxLocation, words: str) -> Flag:
    return Flag("k" * 64, kind, box.page, location_to_json(box), words, words, REVIEW, READINGS_DIFFER, None)


def target(kind: str = LINE, box: BoxLocation = LINE_BOX, words: str = LINE_TEXT) -> ensemble_sql.Target:
    spot, spot_text, span = ensemble_sql._spot(flag(kind, box, words), words, [_line()])
    return ensemble_sql.Target(flag(kind, box, words), words, True, False, spot, spot_text, span)


def _line() -> RowMapping:
    return cast(RowMapping, {"page_no": 1, "text": LINE_TEXT, "region": location_to_json(LINE_BOX)})


# --- the vote ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reads", "value", "agreed"),
    [
        ([("a", "Rs 4,850"), ("b", "Rs 4,850"), ("c", "Rs 4,350")], "Rs 4,850", 2),
        ([("a", "Rs 4,850"), ("b", "4,350"), ("c", "rs 4,350")], "4,350", 2),  # compared without case or Rs
        ([("a", "Rs 4,850"), ("b", "Rs 4,350"), ("c", None)], None, 1),  # nothing read counts against
        ([("a", "Rs 4,850"), ("b", "₹4,850")], "Rs 4,850", 2),  # 2 of 2, the main reader's spelling
        ([("a", "Rs 4,850"), ("b", "4,350")], None, 1),
    ],
)
def test_majority_is_the_upload_votes_rule(
    reads: list[tuple[str, str | None]], value: str | None, agreed: int
) -> None:
    result = majority(reads)
    assert (result["value"], result["agreed"], result["readers"]) == (value, agreed, len(reads))
    assert result["reads"] == [{"model": model, "text": text} for model, text in reads]


def test_vote_still_gives_what_it_gave() -> None:
    value = ExtractedValue(
        "document",
        "total_amount",
        "Rs 4,850",
        "Rs 4,850",
        MARK_BOX,
        None,
        None,
        "needs_review",
        "readers_disagree",
    )
    other = Span("Rs 4,350", "Rs 4,350", MARK_BOX, None, "paragraph", "ocr", quality="ok", box_source="found")
    same = Span("Rs 4,850", "Rs 4,850", MARK_BOX, None, "paragraph", "ocr", quality="ok", box_source="found")
    assert vote(value, MARK_BOX, ("a", [("b", [other]), ("c", [same]), ("d", None)])) == {
        "value": "Rs 4,850",
        "agreed": 2,
        "readers": 3,
        "reads": [
            {"model": "a", "text": "Rs 4,850"},
            {"model": "b", "text": "Rs 4,350"},
            {"model": "c", "text": "Rs 4,850"},
        ],
    }


def test_settle_names_agreed_changed_and_unresolved() -> None:
    word = target()
    agreed = ensemble_sql.settle(word, [(LEAD, LINE_TEXT), (SECOND, LINE_TEXT), (THIRD, "other")])
    changed = ensemble_sql.settle(
        word, [(LEAD, LINE_TEXT), (SECOND, "Total Rs 4,350 paid 12"), (THIRD, "Total Rs 4,350 paid 12")]
    )
    unresolved = ensemble_sql.settle(word, [(LEAD, LINE_TEXT), (SECOND, "a"), (THIRD, None)])
    assert (agreed.outcome, agreed.proposal) == ("agreed", None)
    assert (changed.outcome, changed.proposal) == ("changed", "Total Rs 4,350 paid 12")
    assert (unresolved.outcome, unresolved.proposal, len(unresolved.reads)) == ("unresolved", None, 3)


# --- lining up a model's words -------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "read", "words", "found"),
    [
        (LINE_TEXT, "Total Rs 4,350 paid 12", (2, 3), "4,350"),  # a replaced word
        (LINE_TEXT, "Total Rs 4,350 paid 12", (4, 5), "12"),  # [?] read at last
        (LINE_TEXT, "Total Rs 4,850 paid 12", (2, 3), "4,850"),  # the same word
        ("Rs 4,850", "Total Rs 4,850", (0, 2), "Rs 4,850"),  # a word read before is left out
        ("Rs 4,850", "Rs 4 850", (0, 2), "Rs 4 850"),  # a word split in two is taken whole
        ("Rs 4,850 paid", "Rs 4,850 cash paid", (0, 3), "Rs 4,850 cash paid"),  # a word read inside kept
        ("Rs 4,850", "TOTAL rs 4,850.", (1, 2), "4,850."),  # compared without case or end punctuation
        ("Rs 4,850", "", (0, 2), None),
    ],
)
def test_aligned_takes_the_words_that_line_up(
    stored: str, read: str, words: tuple[int, int], found: str | None
) -> None:
    assert aligned(stored, read, words) == found


def test_a_model_unsure_there_has_no_reading() -> None:
    mark = target(MARK, MARK_BOX, "4,850")
    line = Span("Total Rs 4,350 paid 12", "", LINE_BOX, None, "paragraph", "ocr", quality="ok")
    unsure = Span("4,350", "", MARK_BOX, None, origin="ocr", quality="unsure")
    illegible = Span("Total Rs [?] paid 12", "", LINE_BOX, None, "paragraph", "ocr", quality="ok")
    assert reading_of([line], None, mark) == "4,350"
    assert reading_of([line, unsure], None, mark) is None
    assert reading_of([illegible], None, mark) is None
    elsewhere = Span(
        "Total Rs 4,350 paid 12", "", BoxLocation(1, 0.1, 0.5, 0.9, 0.55), None, "paragraph", "ocr"
    )
    assert reading_of([elsewhere], LINE_BOX, mark) is None  # a page read: only lines at its spot
    assert reading_of([line, elsewhere], LINE_BOX, mark) == "4,350"


# --- where a word is read ------------------------------------------------------------------


def test_a_word_mark_is_read_on_its_line_and_found_by_position() -> None:
    mark = flag(MARK, MARK_BOX, "4,850")
    assert ensemble_sql._spot(mark, "4,850", [_line()]) == (location_to_json(LINE_BOX), LINE_TEXT, (2, 3))
    alone = ensemble_sql._spot(mark, "4,850", [])  # no line: its own box, all of it
    assert alone == (location_to_json(MARK_BOX), "4,850", (0, 1))
    assert ensemble_sql.word_at("a [?] b [?]", "[?]", 0.9) == 3  # the nearer of two
    assert ensemble_sql.word_at("a [?] b [?]", "[?]", 0.1) == 1
    assert ensemble_sql.word_at("Total Rs 4,850.", "4,850", 0.6) == 2
    assert ensemble_sql.word_at("Total", "4,850", 0.5) is None


def test_the_crop_is_the_spot_grown_by_the_checkers_margin() -> None:
    page = Image.new("RGB", (1000, 2000), "white")
    crop = _check_crop(page, LINE_BOX)  # 4% of its own width and height on each side
    assert crop.size == (864, 108)
    edge = _check_crop(page, BoxLocation(1, 0.0, 0.0, 1.0, 0.01))  # within the page
    assert edge.size == (1000, 21)


# --- the run's words -----------------------------------------------------------------------


def test_the_error_says_the_pages_in_the_owners_words() -> None:
    assert views.page_ranges([9, 10, 11, 12, 13, 14]) == "9\u201314"
    assert views.page_ranges([1, 3, 5, 6, 7]) == "1, 3 and 5\u20137"
    assert views.error_message("timeout", list(range(9, 15)), list(range(1, 9))) == (
        "Ensemble couldn't finish pages 9\u201314. The connection timed out."
        " Pages 1\u20138 are done and saved."
    )
    assert views.error_message("outage", [3], [1, 2]) == (
        "Ensemble couldn't finish page 3. The reading service is not answering right now."
        " Pages 1\u20132 are done and saved."
    )
    assert views.error_message("refused", [1], []).endswith("Nothing was changed.")


def every_string() -> list[str]:
    samples = [views.error_message(code, [9, 10], [1]) for code in views.CAUSES] + [
        views.error_message("timeout", [1, 2], [])
    ]
    return [*router.MESSAGES.values(), *views.CAUSES.values(), router.BAD_SCOPE,
            documents_router._ENSEMBLE_RUNNING, *samples]  # fmt: skip


@pytest.mark.parametrize("words", every_string())
def test_no_banned_word_and_no_exclamation_mark(words: str) -> None:
    found = [phrase for phrase in BANNED if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", words.lower())]
    assert found == [], words
    assert "!" not in words


def test_a_line_ensemble_left_unsettled_is_flagged() -> None:
    line: dict[str, Any] = {
        "kind": "paragraph",
        "quality": "ok",
        "box_source": "found",
        "text": "Bill to Patil",
        "confidence": None,
        "region": location_to_json(LINE_BOX),
        "page_no": 1,
        "read_quality": "good",
        "read_issues": [],
    }
    assert span_flag(line) is None
    flagged = span_flag({**line, "ensemble": True})
    assert flagged is not None and (flagged.kind, flagged.band, flagged.reason) == (
        LINE,
        REVIEW,
        READINGS_DIFFER,
    )


# --- the queue and the worker --------------------------------------------------------------


def test_the_queue_carries_run_id_only_when_set() -> None:
    plain = Job("c", "d")
    assert _body(plain) == '{"customer_id": "c", "document_id": "d"}'  # old messages unchanged
    assert _job_from_body(_body(plain)) == (plain, 0)
    run = Job("c", "d", run_id="r")
    assert _job_from_body(_body(run, 2)) == (run, 2)
    assert _job_from_body('{"customer_id": "c", "document_id": "d", "run_id": 5}') is None
    assert _job_from_body('{"customer_id": "c", "document_id": "d", "run_id": ""}') is None


def test_the_worker_sends_a_run_to_its_handler_and_marks_the_run_never_the_document() -> None:
    seen: list[tuple[str, Job]] = []
    handle = dispatch(
        lambda job, beat: seen.append(("pipeline", job)),
        lambda job, beat: seen.append(("ensemble", job)),
        # Changed on purpose (#463 review): the check's handler is required.
        lambda job, beat: seen.append(("accuracy", job)),
    )
    handle(Job("c", "d"), lambda: None)
    handle(Job("c", "d", run_id="r"), lambda: None)
    assert [name for name, _job in seen] == ["pipeline", "ensemble"]

    marked: list[tuple[str, ...]] = []
    exhausted = MarkExhausted(
        lambda c, d: marked.append(("document", c, d)),
        lambda c, r: marked.append(("run", c, r)),
        # Changed on purpose (#463 review): the check's handler is required.
        mark_check=lambda c, k: marked.append(("check", c, k)),
    )
    exhausted(Job("c", "d"))
    exhausted(Job("c", "d", run_id="r"))
    attempt = RecordAttempt(
        lambda c, d, a, m: marked.append(("try", d, str(a))),
        5,
        lambda c, r, a: marked.append(("run try", r, str(a))),
    )
    attempt(Job("c", "d"), 2)
    attempt(Job("c", "d", run_id="r"), 3)
    assert marked == [("document", "c", "d"), ("run", "c", "r"), ("try", "d", "2"), ("run try", "r", "3")]


# --- who reads again -----------------------------------------------------------------------


def ensemble(conn: FakeConn) -> models.Reading:
    return models.resolve_ensemble(conn.conn(), CUSTOMER, DOCUMENT, settings=GEMINI)


def test_the_lead_and_the_first_two_other_usable_models_read() -> None:
    rows = [row(SECOND, default=True), row(LEAD), row(THIRD), row(FOURTH)]
    reading = ensemble(FakeConn(scan=rows, settings={"ensemble": True}, document=document(LEAD)))
    assert reading.lead.model == LEAD
    assert [agent.model for agent in reading.others] == [SECOND, THIRD]


@pytest.mark.parametrize(
    ("rows", "allowed", "settings"),
    [
        ([row(LEAD, default=True), row(SECOND)], False, GEMINI),  # no grant
        ([row(LEAD, default=True)], True, GEMINI),  # no other model
        ([row(LEAD, default=True), row(SECOND)], True, make_settings()),  # scans not read by a model
    ],
)
def test_ensemble_is_refused_without_the_grant_or_another_model(
    rows: list[dict[str, Any]], allowed: bool, settings: Any
) -> None:
    conn = FakeConn(scan=rows, settings={"ensemble": allowed}, document=document())
    with pytest.raises(AiNotAllowed) as refused:
        models.resolve_ensemble(conn.conn(), CUSTOMER, DOCUMENT, settings=settings)
    assert refused.value.code == "ensemble_not_allowed"
