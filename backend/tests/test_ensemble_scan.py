"""Ensemble scan (feature 27, Tushar's rule of 15 Oct 2026): the main model reads each page;
every value read by OCR that needs review is checked by the other chosen models; the value
is decided by majority vote (2 of 3) after the checker's text normalising; the vote is
saved with the value; with no majority the value stays Needs review; Verified still needs
the RapidOCR checker. Never a guess.

* ``vote`` and ``reading_at``: the truth table (split lines agree, a label worded
  differently agrees, (1,180) is not 1,180, 01/02/2026 is not 1/2/2026, a GSTIN's O is not
  0, Devanagari digits are digits, 1,24,560.00 is not 124560 in v1, a reader with no line
  there or an unsure one has no vote, one dissent of three).
* ``settle_votes``: the label rules after the vote, and the invariants (marked ``eval``):
  readers that all agree never verify a value the checker disagrees with or could not
  read; a value already Verified is never voted on.
* The engine: the other models read each page in its page thread, billed as
  ensemble_scan; one that fails is left out; a page the main model could not read is the
  other model's; pages past ENSEMBLE_MAX_PAGES are read by the main model only.
* The pipeline: the vote saved with the value, the majority's text checked by the
  checker, and run_config.reading's pages_short.

No network and no database (tests/test_dynamic_reading.py's fakes)."""

import unittest
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import pytest

from kaagaz.ai.replies import ModelOutputError
from kaagaz.db.models import Agent, Reading
from kaagaz.extraction.labels import LineMarks, reading_at, settle_votes, vote
from kaagaz.extraction.values import ExtractedValue
from kaagaz.pipeline import JobReading
from kaagaz.reading.ocr import ENSEMBLE_MAX_PAGES, DocumentPages
from kaagaz.reading.spans import BoxLocation, Location, Span
from tests.pdf_builder import make_pdf
from tests.test_dynamic_reading import FOUND, TOTAL_BOX, ModelClient, engine, ledger
from tests.test_gemini_ocr import CUSTOMER, DOCUMENT, FakeCache, FakeFinder, line, page, reply, value
from tests.test_pipeline_gemini import CUSTOMER as JOB_CUSTOMER
from tests.test_pipeline_gemini import SHARP, FakeChecker, rig

MAIN = "google/gemini-3.8-flash"
SECOND = "anthropic/claude-sonnet-5.5"
THIRD = "google/gemini-3.1-pro-preview"
BOX = BoxLocation(1, 0.10, 0.50, 0.65, 0.53)


def span(text: str, box: BoxLocation = BOX, *, quality: str | None = None) -> Span:
    """A line another model read (``quality`` "unsure": one of its word marks)."""
    return Span(text, text, box, None, None if quality else "paragraph", "ocr", quality=quality)


def ocr_value(
    text: str, field: str = "total_amount", *, label: str = "needs_review", reason: str = "single_reader"
) -> ExtractedValue:
    return ExtractedValue(
        "page:1:line:1", field, text, text, BOX, label=label, label_reason=reason, origin="ocr"
    )


def voted(
    text: str, *others: str | None, field: str = "total_amount", label: str = "Total"
) -> dict[str, Any]:
    """The vote on ``text`` (the main reader's) when each other model read ``label: other``
    at its spot (None: no line there)."""
    reads = [
        (f"m/{n}", None if other is None else [span(f"{label}: {other}")]) for n, other in enumerate(others)
    ]
    page = (MAIN, [(model, [] if spans is None else spans) for model, spans in reads])
    return vote(ocr_value(text, field), BOX, page)


# The truth table.


class VoteTest(unittest.TestCase):
    def test_three_of_three_and_one_dissent_of_three_keep_the_main_readers_text(self) -> None:
        self.assertEqual(voted("1,180.00", "1,180.00", "1,180.00")["value"], "1,180.00")
        one_dissent = voted("1,180.00", "1,180.00", "1,130.00")
        self.assertEqual(
            (one_dissent["value"], one_dissent["agreed"], one_dissent["readers"]), ("1,180.00", 2, 3)
        )

    def test_two_others_against_the_main_reader_decide_the_value(self) -> None:
        votes = voted("27AAKFS88I2L1Z9", "27AAKFS8812L1Z9", "27AAKFS8812L1Z9", field="gstin", label="GSTIN")
        self.assertEqual((votes["value"], votes["agreed"]), ("27AAKFS8812L1Z9", 2))
        self.assertEqual(
            [read["text"] for read in votes["reads"]],
            ["27AAKFS88I2L1Z9", "27AAKFS8812L1Z9", "27AAKFS8812L1Z9"],
        )
        self.assertEqual(votes["reads"][0]["model"], MAIN)

    def test_no_majority_is_none(self) -> None:
        votes = voted("1,180.00", "1,130.00", "1,100.00")
        self.assertEqual((votes["value"], votes["agreed"], votes["readers"]), (None, 1, 3))

    def test_a_reader_with_no_line_there_counts_against_every_text(self) -> None:
        self.assertEqual(voted("1,180.00", None, "1,180.00")["value"], "1,180.00")  # still 2 of 3
        self.assertIsNone(voted("1,180.00", None, "1,130.00")["value"])
        self.assertIsNone(voted("1,180.00", None, None)["value"])

    def test_a_model_that_failed_on_the_page_is_not_counted(self) -> None:
        page = (MAIN, [(SECOND, [span("Total: 1,180.00")]), (THIRD, None)])
        votes = vote(ocr_value("1,180.00"), BOX, page)
        self.assertEqual((votes["value"], votes["agreed"], votes["readers"]), ("1,180.00", 2, 2))
        page = (MAIN, [(SECOND, [span("Total: 1,130.00")]), (THIRD, None)])
        self.assertIsNone(vote(ocr_value("1,180.00"), BOX, page)["value"])  # 1 of 2 is no majority

    def test_the_checkers_normalising_and_nothing_more(self) -> None:
        devanagari = "".join(
            chr(0x0966 + int(c)) if c.isdigit() else c for c in "1,180.00"
        )  # in Devanagari digits
        agree = [("1,180.00", "₹ 1,180.00"), (devanagari, "1,180.00"), ("TOTAL", "total")]
        for main, other in agree:
            with self.subTest(main=main, other=other):
                self.assertEqual(voted(main, other, other)["agreed"], 3)
        differ = [
            ("(1,180)", "1,180"),
            ("01/02/2026", "1/2/2026"),
            ("27AAKFS8812L1Z9", "27AAKFS88l2L1Z9"),
            ("27AAKFSO812L1Z9", "27AAKFS0812L1Z9"),
            ("1,24,560.00", "124560"),  # pinned: v1 compares text, never numbers
        ]
        for main, other in differ:
            with self.subTest(main=main, other=other):
                votes = voted(main, other, other, field="gstin", label="GSTIN")
                self.assertEqual((votes["value"], votes["agreed"]), (other, 2))

    def test_split_lines_agree_and_a_label_worded_differently_agrees(self) -> None:
        left = BoxLocation(1, 0.10, 0.50, 0.40, 0.53)
        right = BoxLocation(1, 0.40, 0.50, 0.65, 0.53)
        self.assertEqual(
            reading_at(ocr_value("1,180.00"), BOX, [span("Total", left), span("1,180.00", right)]), "1,180.00"
        )
        self.assertEqual(
            reading_at(ocr_value("1,180.00"), BOX, [span("Grand Total Rs. 1,180.00")]), "1,180.00"
        )

    def test_a_line_mostly_elsewhere_or_on_another_page_is_not_there(self) -> None:
        wide = BoxLocation(1, 0.10, 0.50, 1.00, 0.53)  # 0.55 of its width inside: still there
        far = BoxLocation(1, 0.10, 0.80, 0.65, 0.83)
        other_page = BoxLocation(2, 0.10, 0.50, 0.65, 0.53)
        self.assertEqual(reading_at(ocr_value("1,180.00"), BOX, [span("Total: 1,180.00", wide)]), "1,180.00")
        self.assertIsNone(reading_at(ocr_value("1,180.00"), BOX, [span("Total: 1,180.00", far)]))
        self.assertIsNone(reading_at(ocr_value("1,180.00"), BOX, [span("Total: 1,180.00", other_page)]))

    def test_a_reader_that_is_not_sure_there_has_no_vote(self) -> None:
        mark = BoxLocation(1, 0.40, 0.50, 0.65, 0.53)
        spans = [span("Total: 1,180.00"), span("1,180.00", mark, quality="unsure")]
        self.assertIsNone(reading_at(ocr_value("1,180.00"), BOX, spans))


# The label rules after the vote.


def _settle(
    value_: ExtractedValue,
    others: Sequence[str | None],
    *,
    checker: str | None,
    marks: LineMarks | None = None,
    local: frozenset[int] = frozenset(),
) -> ExtractedValue:
    page = (
        MAIN,
        [(f"m/{n}", None if other is None else [span(f"Total: {other}")]) for n, other in enumerate(others)],
    )
    readings: dict[Location, str] = {} if checker is None else {BOX: f"Total: {checker}"}
    checked: set[Location] = set() if checker is None else {BOX}
    (settled,) = settle_votes(
        [value_], {1: page}, readings, checked=checked, lines={BOX: marks or LineMarks()}, local_pages=local
    )
    return settled


class SettleVotesTest(unittest.TestCase):
    def test_the_majority_the_checker_agrees_with_is_verified_and_the_vote_is_saved(self) -> None:
        disputed = ocr_value("1,130.00", reason="readers_disagree")
        settled = _settle(disputed, ["1,180.00", "1,180.00"], checker="1,180.00")
        self.assertEqual(
            (settled.value_text, settled.label, settled.label_reason),
            ("1,180.00", "verified", "readers_agree"),
        )
        self.assertEqual(settled.value_raw, "1,130.00")  # the main reader's own reading is kept
        assert settled.votes is not None
        self.assertEqual(
            (settled.votes["value"], settled.votes["agreed"], settled.votes["readers"]), ("1,180.00", 2, 3)
        )

    def test_unsure_words_are_settled_by_the_vote_but_illegible_ones_are_not(self) -> None:
        unsure = ocr_value("1,180.00", label="needs_review", reason="unsure_text")
        settled = _settle(
            unsure, ["1,180.00", "1,180.00"], checker="1,180.00", marks=LineMarks(unsure=("1,180.00",))
        )
        self.assertEqual((settled.label, settled.label_reason), ("verified", "readers_agree"))
        unreadable = ocr_value("1,1[?]0.00", reason="unsure_text")
        settled = _settle(unreadable, ["1,1[?]0.00", "1,1[?]0.00"], checker="1,180.00")
        self.assertEqual(settled.label, "needs_review")

    def test_no_majority_stays_needs_review_with_its_reason_and_the_vote(self) -> None:
        disputed = ocr_value("1,130.00", reason="readers_disagree")
        settled = _settle(disputed, ["1,180.00", "1,100.00"], checker="1,180.00")
        self.assertEqual(
            (settled.value_text, settled.label, settled.label_reason),
            ("1,130.00", "needs_review", "readers_disagree"),
        )
        assert settled.votes is not None
        self.assertIsNone(settled.votes["value"])

    def test_a_box_no_found_box_confirmed_stays_needs_review(self) -> None:
        unpinned = ocr_value("1,180.00", reason="box_unanchored")
        settled = _settle(
            unpinned, ["1,180.00", "1,180.00"], checker="1,180.00", marks=LineMarks(unpinned=True)
        )
        self.assertEqual((settled.label, settled.label_reason), ("needs_review", "box_unanchored"))

    def test_not_voted_on_a_verified_value_a_card_value_a_local_page_or_a_page_read_once(self) -> None:
        verified = ocr_value("1,180.00", label="verified", reason="readers_agree")
        card = ocr_value("1,180.00", reason="model_chosen")
        local = ocr_value("1,180.00", reason="local_reader")
        typed = replace(ocr_value("1,180.00"), origin="text_layer")
        for item in (verified, card, local, typed):
            with self.subTest(item.label_reason):
                self.assertEqual(_settle(item, ["1,130.00", "1,130.00"], checker="1,130.00"), item)
        alone = ocr_value("1,180.00")
        (settled,) = settle_votes([alone], {2: (MAIN, [])}, {}, checked=set(), lines={}, local_pages=())
        self.assertEqual(settled, alone)


@pytest.mark.eval
class InvariantsTest(unittest.TestCase):
    """C13 invariant 2 and the never-cut rule: readers that agree never stand in for the
    checker."""

    def test_readers_that_all_agree_never_verify_a_value_the_checker_disagrees_with(self) -> None:
        disputed = ocr_value("1,130.00", reason="readers_disagree")
        settled = _settle(disputed, ["1,130.00", "1,130.00"], checker="1,180.00")
        self.assertEqual((settled.label, settled.label_reason), ("needs_review", "readers_disagree"))

    def test_readers_that_all_agree_never_verify_a_value_the_checker_could_not_read(self) -> None:
        settled = _settle(ocr_value("1,180.00"), ["1,180.00", "1,180.00"], checker=None)
        self.assertEqual((settled.label, settled.label_reason), ("needs_review", "single_reader"))

    def test_the_vote_never_turns_a_value_needs_review_and_never_touches_a_verified_one(self) -> None:
        for others in (["1,180.00", "1,180.00"], ["1,130.00", "1,130.00"], [None, None], ["1,1", "x"]):
            verified = ocr_value("1,180.00", label="verified", reason="readers_agree")
            with self.subTest(others=others):
                self.assertEqual(_settle(verified, others, checker="1,180.00"), verified)


# The engine.


def ensemble_pages(cache: FakeCache | None = None) -> DocumentPages:
    return DocumentPages(
        customer_id=CUSTOMER,
        document_id=DOCUMENT,
        cache=cache or FakeCache(),
        model=MAIN,
        provider="google-vertex",
        others=((SECOND, "google-vertex"), (THIRD, "google-vertex")),
    )


class EngineTest(unittest.TestCase):
    def test_c6_the_other_models_read_the_page_after_the_main_one_billed_as_ensemble_scan(self) -> None:
        client = ModelClient(
            {
                MAIN: reply(value(line("Total 1,180.00"))),
                SECOND: reply(value(line("Total 1,180.00"))),
                THIRD: reply(value(line("Total 1,130.00"))),
            }
        )
        reader = engine(client)
        with ledger() as kept, reader.document(ensemble_pages()):
            read = reader.read_page(page(), page_no=1)
        self.assertEqual([model for model, _level in client.calls], [MAIN, SECOND, THIRD])
        self.assertEqual(read.read_by, MAIN)
        self.assertEqual(
            [(other.model, [item.text for item in other.spans or ()]) for other in read.others],
            [(SECOND, ["Total 1,180.00"]), (THIRD, ["Total 1,130.00"])],
        )
        self.assertEqual((read.input_tokens, read.output_tokens), (3 * 1700, 3 * 4000))
        kinds = sorted((row.model, row.kind) for row in kept.rows)
        self.assertEqual(kinds, [(SECOND, "ensemble_scan"), (THIRD, "ensemble_scan"), (MAIN, "scan")])

    def test_c6_a_model_that_fails_on_the_page_is_left_out(self) -> None:
        client = ModelClient(
            {
                MAIN: reply(value(line("Total"))),
                SECOND: ModelOutputError("invalid_output"),
                THIRD: reply(value(line("Total"))),
            }
        )
        reader = engine(client)
        with reader.document(ensemble_pages()):
            read = reader.read_page(page(), page_no=1)
        self.assertEqual(
            [(other.model, other.spans is None) for other in read.others], [(SECOND, True), (THIRD, False)]
        )

    def test_m5_a_page_the_main_model_could_not_read_is_another_models(self) -> None:
        client = ModelClient(
            {
                MAIN: ModelOutputError("invalid_output"),
                SECOND: reply(value(line("Total 1,180.00"))),
                THIRD: reply(value(line("Total 1,180.00"))),
            }
        )
        reader = engine(client)
        with reader.document(ensemble_pages()):
            read = reader.read_page(page(), page_no=1)
        self.assertEqual(read.read_by, SECOND)
        self.assertNotIn("reader_refused", read.issues)
        self.assertEqual(
            [(other.model, other.spans is None) for other in read.others], [(MAIN, True), (THIRD, False)]
        )

    def test_c6_pages_past_the_cap_are_read_by_the_main_model_only(self) -> None:
        client = ModelClient({MAIN: reply(value(line("Total")))})
        reader = engine(client)
        with reader.document(ensemble_pages()):
            read = reader.read_page(page(), page_no=ENSEMBLE_MAX_PAGES + 1)
        self.assertEqual(([model for model, _level in client.calls], read.others), ([MAIN], ()))


# The pipeline.


def _ensemble_rig(answers: dict[str, Any], checker: str) -> Any:
    reader = engine(ModelClient(answers), finder=FakeFinder(FOUND))
    reading = Reading(
        "ensemble",
        Agent(MAIN, "google-vertex", "Main"),
        others=(Agent(SECOND, "google-vertex", "Second"), Agent(THIRD, "google-vertex", "Third")),
        thinking="low",
    )
    return rig(
        reader,
        checker=FakeChecker(f"Total: {checker}"),
        ocr_model=MAIN,
        job_reading=lambda customer, document: JobReading(None, MAIN, "google-vertex", reading=reading),
    )


def _total(text: str) -> Any:
    return reply(value(line(f"Total: {text}", TOTAL_BOX)))


class PipelineTest(unittest.TestCase):
    def test_the_majority_decides_the_checker_verifies_and_the_vote_is_saved(self) -> None:
        system = _ensemble_rig(
            {MAIN: _total("1,130.00"), SECOND: _total("1,180.00"), THIRD: _total("1,180.00")}, "1,180.00"
        )
        job = system.run(make_pdf([SHARP]))
        (saved,) = system.store.values_for(JOB_CUSTOMER, job.document_id)
        self.assertEqual(
            (saved.value_text, saved.label, saved.label_reason), ("1,180.00", "verified", "readers_agree")
        )
        assert saved.votes is not None
        self.assertEqual(
            [(read["model"], read["text"]) for read in saved.votes["reads"]],
            [(MAIN, "1,130.00"), (SECOND, "1,180.00"), (THIRD, "1,180.00")],
        )
        config = system.totals(job).config["reading"]
        self.assertEqual((config["mode"], config["pages_short"], config["pages_single"]), ("ensemble", [], 0))

    @pytest.mark.eval
    def test_readers_that_agree_with_each_other_but_not_the_checker_stay_needs_review(self) -> None:
        system = _ensemble_rig(
            {MAIN: _total("1,130.00"), SECOND: _total("1,130.00"), THIRD: _total("1,130.00")}, "1,180.00"
        )
        job = system.run(make_pdf([SHARP]))
        (saved,) = system.store.values_for(JOB_CUSTOMER, job.document_id)
        self.assertEqual(
            (saved.value_text, saved.label, saved.label_reason),
            ("1,130.00", "needs_review", "readers_disagree"),
        )

    def test_a_model_that_could_not_read_the_page_is_noted_and_left_out(self) -> None:
        answers = {
            MAIN: _total("1,130.00"),
            SECOND: _total("1,180.00"),
            THIRD: ModelOutputError("invalid_output"),
        }
        system = _ensemble_rig(answers, "1,180.00")
        job = system.run(make_pdf([SHARP]))
        (saved,) = system.store.values_for(JOB_CUSTOMER, job.document_id)
        self.assertEqual((saved.value_text, saved.label), ("1,130.00", "needs_review"))  # 1 of 2: no majority
        assert saved.votes is not None
        self.assertEqual((saved.votes["value"], saved.votes["readers"]), (None, 2))
        self.assertEqual(system.totals(job).config["reading"]["pages_short"], [1])
