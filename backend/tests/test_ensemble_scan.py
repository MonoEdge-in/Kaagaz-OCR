"""Ensemble's vote and engine (feature 27, Tushar's rule of 15 Oct 2026): a value is decided
by majority vote (2 of 3) after the checker's text normalising. Ensemble runs only after
the scan since 16 Oct 2026 (tests/test_ensemble_rules.py); a scan never changes a value by
a vote.

* ``vote`` and ``reading_at``: the truth table (split lines agree, a label worded
  differently agrees, (1,180) is not 1,180, 01/02/2026 is not 1/2/2026, a GSTIN's O is not
  0, Devanagari digits are digits, 1,24,560.00 is not 124560 in v1, a reader with no line
  there or an unsure one has no vote, one dissent of three).
* The engine (``gemini_ocr._ensemble``, kept, no scan asks for it now): the other models
  read each page in its page thread, billed as ensemble_scan; one that fails is left out;
  a page the main model could not read is the other model's; pages past
  ENSEMBLE_MAX_PAGES are read by the main model only.

No network and no database (tests/test_dynamic_reading.py's fakes)."""

import unittest
from typing import Any

from kaagaz.ai.replies import ModelOutputError
from kaagaz.extraction.labels import reading_at, vote
from kaagaz.extraction.values import ExtractedValue
from kaagaz.reading.ocr import ENSEMBLE_MAX_PAGES, DocumentPages
from kaagaz.reading.spans import BoxLocation, Span
from tests.test_dynamic_reading import ModelClient, engine, ledger
from tests.test_gemini_ocr import CUSTOMER, DOCUMENT, FakeCache, line, page, reply, value

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


# Changed on purpose (Ensemble after the scan, 16 Oct 2026): settle_votes and its tests are
# deleted (it replaced a value's text by the vote with no one asked, which breaks the scan
# review rules' "never silently replace text"), and so are the pipeline's Ensemble tests: a
# scan is never read by more than one model (tests/test_ai_resolve.py).


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
    def test_c6_the_other_models_read_the_page_with_the_main_one_billed_as_ensemble_scan(self) -> None:
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
        # Changed on purpose (fast scan, council 16 Oct 2026): the three read at the same time.
        self.assertEqual(sorted(model for model, _level in client.calls), sorted([MAIN, SECOND, THIRD]))
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
