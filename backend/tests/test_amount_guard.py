"""The amount safety layer (#524, Tushar, 6 Nov 2026): a misread amount never silently
reaches a total. "/-" and its variants end an amount and are never digits; Indic digits are
read as ASCII ones for the number only; and an amount the rules doubt goes to review with its
reason (kaagaz/extraction/parsing.py ``amount_problem``, kaagaz/extraction/labels.py
``label_amounts``). The real examples: amount-1.png (11500000/-) and amount-2.png (९४0000/-).

Every expected value is a literal written by hand. The same rules on PostgreSQL (search, Ask's
total, the review queue) are in tests/test_amount_guard_db.py, migration 0077c in
tests/test_migration_0077c_db.py.
"""

import unittest
from dataclasses import replace
from decimal import Decimal

from kaagaz.chunking.script import latin_digits, piece_latin
from kaagaz.extraction.checks import PASSED, CheckResult
from kaagaz.extraction.flags import COVERED_REASONS
from kaagaz.extraction.invoice import _settle
from kaagaz.extraction.labelled import tax_components, values_from_lines
from kaagaz.extraction.labels import (
    AMOUNT_OUTLIER,
    LineMarks,
    label_amounts,
    label_reading_marks,
)
from kaagaz.extraction.land import value_number
from kaagaz.extraction.parsing import AMOUNT_COMMAS, AMOUNT_MARK, MIXED_DIGITS, amount_problem, parse_amount
from kaagaz.extraction.values import CARD_RECORD, ExtractedValue
from kaagaz.reading.spans import BoxLocation, CellLocation, Span
from tests.test_pipeline_invoice import labels as pipeline_labels
from tests.test_pipeline_invoice import lines as pipeline_lines
from tests.test_pipeline_invoice import rig

BOX = BoxLocation(1, 0.1, 0.40, 0.9, 0.42)
OTHER = BoxLocation(1, 0.1, 0.50, 0.9, 0.52)


def amount(
    text: str,
    *,
    field: str = "total_amount",
    record_key: str = "document",
    box: BoxLocation | CellLocation = BOX,
    reason: str = "readers_agree",
) -> ExtractedValue:
    """A value as the pipeline has it before ``label_amounts``: labelled, its number parsed
    (pipeline._with_parsed's ``land.value_number``)."""
    label = "verified" if reason in ("readers_agree", "direct_cell", "learned_correction") else "needs_review"
    return ExtractedValue(
        record_key,
        field,
        text,
        text,
        box,
        label=label,
        label_reason=reason,
        value_number=value_number(field, text),
        origin="ocr",
    )


def guarded(*values: ExtractedValue) -> list[tuple[str, str, str, Decimal | None]]:
    return [(v.value_text, v.label, v.label_reason, v.value_number) for v in label_amounts(values)]


class TheExamplesTest(unittest.TestCase):
    """The brief's cases: the number computed, whether it goes to review, the text as read."""

    def test_each_example_reads_its_number_and_only_the_doubtful_ones_go_to_review(self) -> None:
        cases = [
            ("11500000/-", Decimal(11500000), "verified", "readers_agree"),
            ("९४0000/-", Decimal(940000), "needs_review", MIXED_DIGITS),  # amount-2.png
            ("९४००००/-", Decimal(940000), "verified", "readers_agree"),
            ("३४०००० ", Decimal(340000), "verified", "readers_agree"),
            ("1,15,00,000/-", Decimal(11500000), "verified", "readers_agree"),
            ("Rs. 9,40,000/=", Decimal(940000), "verified", "readers_agree"),
            ("115000001-", None, "needs_review", AMOUNT_MARK),  # "/-" read as "1-"
            ("11500000/1", None, "needs_review", AMOUNT_MARK),
            ("1,15,0000", None, "needs_review", AMOUNT_COMMAS),
            ("11,500,00", None, "needs_review", AMOUNT_COMMAS),
            ("1O,000", None, "needs_review", AMOUNT_MARK),  # a letter O inside the digits
        ]
        for text, number, label, reason in cases:
            with self.subTest(text=text):
                self.assertEqual(guarded(amount(text)), [(text, label, reason, number)])

    def test_the_displayed_text_is_never_changed(self) -> None:
        for text in ("९४0000/-", "३४०००० ", "115000001-", "Rs. 9,40,000/="):
            (value,) = label_amounts([amount(text)])
            self.assertEqual((value.value_text, value.value_raw), (text, text))

    def test_115000001_with_no_mark_left_cannot_be_told_apart_and_is_read_as_printed(self) -> None:
        """Known limit (#524): with its "-" gone too, "11500000/-" read as "115000001" is a clean
        amount; only the sanity bound below can catch it."""
        clean = ("115000001", "verified", "readers_agree", Decimal(115000001))
        self.assertEqual(guarded(amount("115000001")), [clean])


class EndMarksTest(unittest.TestCase):
    def test_every_end_mark_ends_the_amount_and_is_never_a_digit(self) -> None:
        for text in ("940000/-", "940000 / -", "940000/=", "940000-/-", "940000/", "940000।", "940000॥"):
            with self.subTest(text=text):
                self.assertEqual(parse_amount(text), Decimal(940000))
                self.assertIsNone(amount_problem(text))

    def test_a_trailing_minus_alone_is_still_no_amount_and_no_doubt(self) -> None:
        self.assertIsNone(parse_amount("500-"))
        self.assertIsNone(amount_problem("500-"))


class DigitsTest(unittest.TestCase):
    def test_indic_digits_become_ascii_one_for_one_and_others_stay(self) -> None:
        self.assertEqual(latin_digits("९४0000/-"), "940000/-")
        self.assertEqual(latin_digits("૯૪ ৯৪ ੯੪ ௯௪"), "94 94 94 94")  # Gujarati, Bengali, Gurmukhi, Tamil
        self.assertEqual(latin_digits("١٢٣ ①"), "١٢٣ ①")  # Arabic-Indic and a circled digit: not Indic
        self.assertIsNone(parse_amount("١٢٣"))

    def test_digits_of_two_indic_scripts_are_mixed_too(self) -> None:
        self.assertEqual(amount_problem("९४૦૦૦૦"), MIXED_DIGITS)  # Devanagari then Gujarati
        self.assertEqual(amount_problem("९४,0000"), MIXED_DIGITS)  # its commas are wrong too: the mix first
        self.assertIsNone(amount_problem("९४0000-"))  # a trailing minus: no amount, so no doubt

    def test_a_piece_with_an_indic_digit_gets_a_latin_spelling_for_search(self) -> None:
        self.assertEqual(piece_latin("Total ९४००००/-"), "total 940000/-")
        self.assertEqual(piece_latin("९४0000"), "940000")
        self.assertEqual(piece_latin("एकूण ९४००००"), "ekun 940000")  # Devanagari, as before
        self.assertIsNone(piece_latin("Total 940000/-"))  # nothing to spell: as before


class NotAnAmountTest(unittest.TestCase):
    def test_text_that_is_no_amount_raises_no_doubt(self) -> None:
        for text in ("12 pcs", "9% 900.00", "2 x 500", "Items 51-", "1,234.00 Cr", "007", "INV-1042", "abc"):
            with self.subTest(text=text):
                self.assertIsNone(amount_problem(text))

    def test_a_date_a_ratio_or_a_phone_number_is_no_amount(self) -> None:
        """#524 review: only a letter inside the digits is a misread mark ("1O,000"); a "/", "-"
        or ":" there means the text is something else, never an amount to doubt."""
        for text in ("12/03/2024", "45/2", "24/7", "3/4", "98765-43210", "2024-11", "12/1", "10:30"):
            with self.subTest(text=text):
                self.assertIsNone(amount_problem(text))
        for text in ("115000001-", "11500000/1", "1O,000"):
            with self.subTest(text=text):
                self.assertEqual(amount_problem(text), AMOUNT_MARK)

    def test_an_identifier_or_a_date_with_no_number_is_left_alone(self) -> None:
        values = [
            amount("INV-1042", field="invoice_number"),
            amount("12/08/2026", field="invoice_date"),
            amount("1,15,0000", field="party_name"),  # not an amount field, and no number
        ]
        self.assertEqual([v.label_reason for v in label_amounts(values)], ["readers_agree"] * 3)

    def test_any_value_with_a_number_is_checked_for_mixed_digits(self) -> None:
        """A sheet's cell or an invoice number: the number is read, so the mix must be seen."""
        cell = amount("९४0000", field="profit", box=CellLocation(1, 2, 2), reason="direct_cell")
        number = amount("१२42", field="invoice_number")
        self.assertEqual(
            guarded(cell, number),
            [
                ("९४0000", "needs_review", MIXED_DIGITS, Decimal(940000)),
                ("१२42", "needs_review", MIXED_DIGITS, Decimal(1242)),
            ],
        )


class WhoKeepsTheirLabelTest(unittest.TestCase):
    def test_a_card_value_and_a_persons_fix_keep_their_label(self) -> None:
        card = replace(amount("९४0000/-", reason="model_chosen"), record_key=CARD_RECORD)
        corrected = amount("९४0000/-", reason="corrected")
        learned = amount("९४0000/-", reason="learned_correction")
        self.assertEqual(
            [v.label_reason for v in label_amounts([card, corrected, learned])],
            ["model_chosen", "corrected", "learned_correction"],
        )

    def test_a_reason_the_scan_review_flags_by_is_kept(self) -> None:
        """Changed on purpose (#524 review, 6 Nov 2026): a value already in review for a reason
        the scan review screen flags (flags.COVERED_REASONS) keeps it, so "Readings differ",
        values_disputed and confirming its words still work; it is in review already."""
        disagreeing = replace(amount("९४0000/-", reason="readers_disagree"), second_reading="३४0000/-")
        (value,) = label_amounts([disagreeing])
        self.assertEqual(
            (value.label, value.label_reason, value.second_reading),
            ("needs_review", "readers_disagree", "३४0000/-"),
        )
        for reason in sorted(COVERED_REASONS):
            with self.subTest(reason=reason):
                (kept,) = label_amounts([amount("1,15,0000", reason=reason)])
                self.assertEqual(kept.label_reason, reason)

    def test_another_reason_is_replaced_and_the_second_reading_is_kept(self) -> None:
        single = replace(amount("९४0000/-", reason="single_reader"), second_reading="३४0000/-")
        (value,) = label_amounts([single])
        self.assertEqual((value.label_reason, value.second_reading), (MIXED_DIGITS, "३४0000/-"))


class UnsureDigitTest(unittest.TestCase):
    """(b): a digit the reader marked unsure is an unsure word inside the amount: unsure_text
    (PRD 5 V4, label_reading_marks), which label_amounts keeps when the amount reads cleanly."""

    def test_an_amount_with_an_unsure_word_goes_to_review_and_stays_there(self) -> None:
        value = amount("11500000/-")
        marked = label_reading_marks([value], {BOX: LineMarks(unsure=("11500000/-",))}, ())
        self.assertEqual(guarded(*marked), [("11500000/-", "needs_review", "unsure_text", Decimal(11500000))])

    def test_a_mixed_amount_with_an_unsure_word_keeps_unsure_text(self) -> None:
        """Changed on purpose (#524 review): unsure_text is one of the scan review's reasons, so
        the word stays confirmable there; the amount is in review all the same."""
        value = amount("९४0000/-")
        marked = label_reading_marks([value], {BOX: LineMarks(unsure=("९४0000/-",))}, ())
        self.assertEqual(marked[0].label_reason, "unsure_text")
        self.assertEqual(guarded(*marked), [("९४0000/-", "needs_review", "unsure_text", Decimal(940000))])


class SanityBoundTest(unittest.TestCase):
    """(d), conservative: a total or subtotal more than 10 times the largest other one on its page."""

    def test_a_total_ten_times_its_subtotal_and_more_goes_to_review(self) -> None:
        values = [amount("1,15,00,000", field="subtotal", box=OTHER), amount("115000001")]
        self.assertEqual(
            [(v.field, v.label_reason) for v in label_amounts(values)],
            [("subtotal", "readers_agree"), ("total_amount", AMOUNT_OUTLIER)],
        )

    def test_every_copy_of_the_top_amount_goes_to_review(self) -> None:
        """#524 review: the top amount printed twice is compared with the largest smaller one,
        never with its own copy."""
        third = BoxLocation(1, 0.1, 0.60, 0.9, 0.62)
        values = [
            amount("97,45,763", field="subtotal", box=OTHER),  # Sub Total
            amount("115000001"),  # Total
            amount("115000001", record_key="page:1:line:3", box=third),  # Amount Payable
        ]
        self.assertEqual(
            [v.label_reason for v in label_amounts(values)], ["readers_agree", AMOUNT_OUTLIER, AMOUNT_OUTLIER]
        )

    def test_exactly_ten_times_or_less_is_not_flagged(self) -> None:
        values = [amount("1,000", field="subtotal", box=OTHER), amount("10,000")]
        self.assertEqual([v.label_reason for v in label_amounts(values)], ["readers_agree"] * 2)

    def test_a_lone_total_a_small_tax_a_line_or_another_page_is_never_compared(self) -> None:
        values = [
            amount("1,00,000"),
            amount("2,500", field="tax_amount", box=OTHER),  # a 5% GST is rightly 40 times less
            amount("50", field="amount", record_key="line:1", box=OTHER),
            amount("100", field="subtotal", box=BoxLocation(2, 0.1, 0.1, 0.9, 0.12)),  # another invoice
        ]
        self.assertEqual([v.label_reason for v in label_amounts(values)], ["readers_agree"] * 4)

    def test_a_card_value_is_never_the_other_amount(self) -> None:
        card = replace(amount("100", field="subtotal", reason="model_chosen"), record_key=CARD_RECORD)
        reasons = [v.label_reason for v in label_amounts([card, amount("1,00,000")])]
        self.assertEqual(reasons, ["model_chosen", "readers_agree"])

    def test_a_text_doubt_comes_before_the_bound(self) -> None:
        values = [amount("100", field="subtotal", box=OTHER), amount("९४0000")]
        self.assertEqual(label_amounts(values)[1].label_reason, MIXED_DIGITS)


class LabelledLinesTest(unittest.TestCase):
    """labelled.py keeps an amount the rules doubt, for review, and still drops what is no amount."""

    def test_doubtful_totals_are_kept_and_text_that_is_no_amount_is_dropped(self) -> None:
        spans = [
            Span(text, text, BoxLocation(1, 0.1, 0.1 * k, 0.9, 0.1 * k + 0.05), origin="ocr")
            for k, text in enumerate(
                ["Grand Total: ९४0000/-", "Sub Total: 1,15,0000", "CGST 9% 900.00", "Total Items 5"], start=1
            )
        ]
        found = [(v.field, v.value_text) for v in values_from_lines(spans)]
        self.assertEqual(found, [("total_amount", "९४0000/-"), ("subtotal", "1,15,0000")])
        # "9% 900.00" is no labelled tax amount, as before; the tax component is its 900.00.
        self.assertEqual([v.value_text for v in tax_components(spans)], ["900.00"])

    def test_a_date_after_total_never_takes_the_documents_total(self) -> None:
        """#524 review: "Total: 12/03/2024" is no amount, so the real grand total stays the
        document's total and no page record is made for it."""
        spans = [
            Span(text, text, BoxLocation(1, 0.1, 0.1 * k, 0.9, 0.1 * k + 0.05), origin="ocr")
            for k, text in enumerate(["Total: 12/03/2024", "Grand Total: 1,18,000"], start=1)
        ]
        found = [(v.record_key, v.field, v.value_text) for v in values_from_lines(spans)]
        self.assertEqual(found, [("document", "total_amount", "1,18,000")])

    def test_the_invoice_stage_keeps_the_amount_reason_over_model_chosen(self) -> None:
        value = label_amounts([amount("९४0000/-")])[0]
        check = CheckResult(PASSED, (), frozenset())
        settled = _settle(value, {("document", "total_amount")}, [], check)
        self.assertEqual((settled.label, settled.label_reason), ("needs_review", MIXED_DIGITS))


class PipelineTest(unittest.TestCase):
    """The layer runs in the pipeline after every reader's values are labelled and parsed, and
    before the invoice row reads the labels (pipeline._content), so the row is not verified."""

    def test_a_mixed_total_is_saved_as_read_with_its_number_and_in_review(self) -> None:
        spans = pipeline_lines("Invoice No: INV-17", "Sub Total: 9,40,000.00", "Grand Total: ९४0000/-")
        _, content = rig(spans).read()

        (total,) = [v for v in content.values if v.field == "total_amount"]
        self.assertEqual(
            (total.value_text, total.value_raw, total.value_number, total.label, total.label_reason),
            ("९४0000/-", "९४0000/-", Decimal(940000), "needs_review", MIXED_DIGITS),
        )
        self.assertEqual(pipeline_labels(content)[("document", "subtotal")], ("verified", "readers_agree"))
        invoice = content.invoice
        assert invoice is not None
        self.assertEqual((invoice.total, invoice.label), (Decimal(940000), "needs_review"))


if __name__ == "__main__":
    unittest.main()
