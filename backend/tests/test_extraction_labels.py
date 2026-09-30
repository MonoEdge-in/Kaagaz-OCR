"""Verified or needs review, and why, for every kind of value."""

import unittest
from dataclasses import dataclass

from kaagaz.extraction.labels import comparable, label_quoted, label_values, overlap
from kaagaz.extraction.learned import LearnedCorrection, apply_learned
from kaagaz.extraction.quotes import find_quote, located_value, reader_text
from kaagaz.extraction.values import ExtractedValue
from kaagaz.reading.spans import BoxLocation, CellLocation, Span

BOX = BoxLocation(1, 0.1, 0.40, 0.9, 0.42)
LINE_BOX = BoxLocation(1, 0.1, 0.50, 0.9, 0.52)


@dataclass(frozen=True, slots=True)
class OcrValue(ExtractedValue):
    """A value with the origin PR 2 adds to ExtractedValue, for these tests only."""

    origin: str | None = None


def ocr_value(text: str, *, field: str = "total_amount", box: BoxLocation = BOX) -> OcrValue:
    return OcrValue("document", field, text, text, box, origin="ocr")


def quoted(text: str, *, field: str = "amount", record_key: str = "line:1") -> ExtractedValue:
    """A value located by a model quote in a text-layer line (PR 6's invoice values)."""
    return ExtractedValue(record_key, field, text, text, LINE_BOX)


def box_value(
    text: str = "1,180.00", *, confidence: float | None = None, box: BoxLocation = BOX
) -> ExtractedValue:
    return ExtractedValue("document", "total_amount", text, text, box, confidence)


def second_line(text: str, box: BoxLocation = BOX) -> Span:
    return Span(text, text, box)


def reason(value: ExtractedValue) -> tuple[str, str, str | None]:
    return value.label, value.label_reason, value.second_reading


class OverlapTest(unittest.TestCase):
    def test_same_box_is_one_and_apart_is_zero(self) -> None:
        self.assertEqual(overlap(BOX, BOX), 1.0)
        self.assertEqual(overlap(BOX, BoxLocation(1, 0.1, 0.5, 0.9, 0.52)), 0.0)

    def test_half_shifted_box(self) -> None:
        # 0.8 wide, heights overlap 0.01 of 0.02: shared 0.008, union 0.024.
        shifted = BoxLocation(1, 0.1, 0.41, 0.9, 0.43)
        self.assertAlmostEqual(overlap(BOX, shifted), 1 / 3)


class ComparableTest(unittest.TestCase):
    def test_case_spaces_and_currency_signs_do_not_matter(self) -> None:
        self.assertEqual(comparable("Rs.  1,180.00"), "1,180.00")
        self.assertEqual(comparable("₹1,180.00"), "1,180.00")
        self.assertEqual(comparable("INR 1,180.00"), "1,180.00")
        self.assertEqual(comparable("  Sharma   TRADERS "), "sharma traders")

    def test_rs_inside_a_word_stays(self) -> None:
        self.assertEqual(comparable("Hours 5"), "hours 5")


class LabelValuesTest(unittest.TestCase):
    def test_a_cell_is_a_direct_read_and_verified(self) -> None:
        cell = ExtractedValue("sheet:1:row:2", "amount", "100", "100", CellLocation(1, 2, 2))
        (labelled,) = label_values([cell], None)
        self.assertEqual(reason(labelled), ("verified", "direct_cell", None))

    def test_readers_agree_after_normalising(self) -> None:
        (labelled,) = label_values([box_value("Rs. 1,180.00")], [second_line("Grand Total: ₹1,180.00")])
        self.assertEqual(reason(labelled), ("verified", "readers_agree", "₹1,180.00"))

    def test_readers_disagree_and_the_second_reading_is_kept(self) -> None:
        (labelled,) = label_values([box_value()], [second_line("Total: 1,130.00")])
        self.assertEqual(reason(labelled), ("needs_review", "readers_disagree", "1,130.00"))

    def test_a_second_line_without_the_label_is_compared_whole(self) -> None:
        (agree,) = label_values([box_value()], [second_line("1,180.00")])
        self.assertEqual(reason(agree), ("verified", "readers_agree", "1,180.00"))
        (differ,) = label_values([box_value()], [second_line("Tota1 1,180.00")])
        self.assertEqual(reason(differ), ("needs_review", "readers_disagree", "Tota1 1,180.00"))

    def test_the_second_line_must_overlap_by_half_on_the_same_page(self) -> None:
        barely = second_line("Total: 1,180.00", BoxLocation(1, 0.1, 0.41, 0.9, 0.43))  # 1/3
        other_page = second_line("Total: 1,180.00", BoxLocation(2, 0.1, 0.40, 0.9, 0.42))
        (labelled,) = label_values([box_value()], [barely, other_page])
        self.assertEqual(reason(labelled), ("needs_review", "single_reader", None))

    def test_the_most_overlapping_second_line_is_used(self) -> None:
        close = second_line("Total: 1,180.00", BoxLocation(1, 0.1, 0.401, 0.9, 0.421))
        closer = second_line("Total: 9.00", BOX)
        (labelled,) = label_values([box_value()], [close, closer])
        self.assertEqual(labelled.second_reading, "9.00")

    def test_low_confidence_without_a_second_reading(self) -> None:
        (low,) = label_values([box_value(confidence=0.89)], None)
        self.assertEqual(reason(low), ("needs_review", "low_confidence", None))
        (sure,) = label_values([box_value(confidence=0.90)], None)
        self.assertEqual(reason(sure), ("needs_review", "single_reader", None))

    def test_no_second_reading_is_single_reader(self) -> None:
        (labelled,) = label_values([box_value()], [])
        self.assertEqual(reason(labelled), ("needs_review", "single_reader", None))

    def test_a_card_value_given_to_this_labeller_is_still_model_chosen(self) -> None:
        """Council fix 4: no path verifies a value whose field the model chose."""
        in_a_cell = ExtractedValue("card", "profit", "12000", "12000", CellLocation(1, 2, 2))
        in_a_line = ExtractedValue("card", "total_due", "1,180.00", "1,180.00", BOX)
        labelled = label_values([in_a_cell, in_a_line], [second_line("1,180.00")])
        self.assertEqual([reason(value) for value in labelled], [("needs_review", "model_chosen", None)] * 2)

    def test_a_reading_by_box_wins_over_the_second_reader(self) -> None:
        values = [box_value(confidence=0.5)]
        (labelled,) = label_values(values, [second_line("Total: 1,130.00")], {BOX: "Total: 1,180.00"})
        self.assertEqual(reason(labelled), ("verified", "readers_agree", "1,180.00"))


class LabelValuesOcrTest(unittest.TestCase):
    """ML amendment 1: an OCR value is verified only when its script is sound, it has
    no Devanagari letters, its box is its own, and the vision reading equals it."""

    def test_an_ocr_value_whose_vision_reading_is_equal_agrees(self) -> None:
        (labelled,) = label_values([ocr_value("1,180.00")], None, {BOX: "Total: ₹1,180.00"})
        self.assertEqual(reason(labelled), ("verified", "readers_agree", "₹1,180.00"))

    def test_the_comparison_uses_the_d2_normalising_too(self) -> None:
        (labelled,) = label_values([ocr_value("1,180.50")], None, {BOX: "१,१८०.५०"})
        self.assertEqual(reason(labelled), ("verified", "readers_agree", "१,१८०.५०"))

    def test_a_vision_reading_that_only_contains_the_value_disagrees(self) -> None:
        (labelled,) = label_values([ocr_value("1,180.00")], None, {BOX: "1,180.00 paid in cash"})
        self.assertEqual(reason(labelled), ("needs_review", "readers_disagree", "1,180.00 paid in cash"))

    def test_devanagari_letters_or_an_unsound_script_are_ocr_script(self) -> None:
        cases = [
            "पाटील",  # Devanagari letters (ML 1(b), PROVISIONAL for PRD 2)
            "उता्याची",  # a virama after a vowel sign: malformed Devanagari (ML 1(a))
            "Sharmaئ",  # an Arabic letter inside a Latin word (ML 1(a))
        ]
        for text in cases:
            with self.subTest(text=text):
                (labelled,) = label_values(
                    [ocr_value(text, field="party_name")], None, {BOX: "Seller: " + text}
                )
                self.assertEqual(reason(labelled), ("needs_review", "ocr_script", text))

    def test_devanagari_digits_and_the_danda_are_not_letters(self) -> None:
        (labelled,) = label_values([ocr_value("१,१८०।")], None, {BOX: "१,१८०।"})
        self.assertEqual(reason(labelled), ("verified", "readers_agree", "१,१८०।"))

    def test_two_ocr_values_in_one_box_are_single_reader(self) -> None:
        number = OcrValue("document", "invoice_number", "17", "17", BOX, origin="ocr")
        when = OcrValue("document", "invoice_date", "12/08/2026", "12/08/2026", BOX, origin="ocr")
        labelled = label_values([number, when], None, {BOX: "Bill No: 17 Date: 12/08/2026"})
        self.assertEqual([reason(value) for value in labelled], [("needs_review", "single_reader", None)] * 2)

    def test_two_ocr_values_with_the_same_text_in_one_box_are_single_reader(self) -> None:
        """Council fix 2: the box's vision reading is not either value's own."""
        subtotal = OcrValue("document", "subtotal", "500", "500", BOX, origin="ocr")
        total = OcrValue("document", "total_amount", "500", "500", BOX, origin="ocr")
        labelled = label_values([subtotal, total], None, {BOX: "500"})
        self.assertEqual([reason(value) for value in labelled], [("needs_review", "single_reader", None)] * 2)
        other = ExtractedValue("card", "profit", "12,000", "12,000", BOX)
        (with_card,) = label_values([ocr_value("1,180.00")], None, {BOX: "1,180.00"}, also_located=[other])
        self.assertEqual(reason(with_card), ("needs_review", "single_reader", None))

    def test_an_ocr_value_without_a_reading_is_single_reader(self) -> None:
        (labelled,) = label_values([ocr_value("1,180.00")], None)
        self.assertEqual(reason(labelled), ("needs_review", "single_reader", None))

    def test_an_ocr_value_with_only_a_matching_second_reader_line_is_single_reader(self) -> None:
        """Council fix 1: an OCR value's second reading is the vision reading of its box,
        never a text-layer line that happens to sit there (a hidden text layer)."""
        (labelled,) = label_values([ocr_value("1,180.00")], [second_line("Total: 1,180.00")])
        self.assertEqual(reason(labelled), ("needs_review", "single_reader", None))
        (script,) = label_values([ocr_value("पाटील", field="party_name")], [second_line("Seller: पाटील")])
        self.assertEqual(reason(script), ("needs_review", "ocr_script", None))

    def test_a_text_layer_line_holding_two_values_keeps_todays_rule(self) -> None:
        number = ExtractedValue("document", "invoice_number", "17", "17", BOX)
        when = ExtractedValue("document", "invoice_date", "12/08/2026", "12/08/2026", BOX)
        labelled = label_values([number, when], [second_line("Bill No: 17 Date: 12/08/2026")])
        self.assertEqual(
            [reason(value) for value in labelled],
            [("verified", "readers_agree", "17"), ("verified", "readers_agree", "12/08/2026")],
        )


class LabelQuotedTest(unittest.TestCase):
    """Values the model chose (the card, and PR 6's invoice values): Lead's resolution 1,
    security amendment A2 and ML amendment 1."""

    def test_a_card_value_is_always_needs_review_model_chosen(self) -> None:
        in_a_line = ExtractedValue("card", "party_name", "Sharma Traders", "Sharma Traders", LINE_BOX)
        in_a_cell = ExtractedValue("card", "profit", "12000", "12000", CellLocation(1, 2, 2))
        labelled = label_quoted([in_a_line, in_a_cell], [second_line("Sharma Traders", LINE_BOX)])
        self.assertEqual([reason(value) for value in labelled], [("needs_review", "model_chosen", None)] * 2)

    def test_a_cell_is_a_direct_read(self) -> None:
        cell = ExtractedValue("line:1", "amount", "1,000.00", "1,000.00", CellLocation(1, 4, 6))
        (labelled,) = label_quoted([cell], None)
        self.assertEqual(reason(labelled), ("verified", "direct_cell", None))

    def test_a_quote_across_two_cells_is_not_direct_cell(self) -> None:
        """Council fix 5: only one whole cell is a direct read of the value."""
        reader = reader_text(
            [Span("Patil", "Patil", CellLocation(1, 2, 1)), Span("500 kg", "500 kg", CellLocation(1, 2, 2))]
        )
        values = []
        for quote, field in (("Patil | 500 kg", "description"), ("500", "quantity"), ("Patil", "buyer_name")):
            found = find_quote(reader, quote)
            assert found is not None
            values.append(located_value(found, record_key="line:1", field=field))
        self.assertEqual(
            [reason(value) for value in label_quoted(values, None)],
            [
                ("needs_review", "model_chosen", None),  # across two cells
                ("needs_review", "model_chosen", None),  # part of a cell
                ("verified", "direct_cell", None),  # one whole cell
            ],
        )

    def test_a_loose_match_on_a_text_layer_line_is_not_verified(self) -> None:
        """Council fix 6: the loose pass runs on text-layer Devanagari too (कूल found for कुल),
        so the second reader agreeing with the text found proves nothing about the quote."""
        span = Span("कूल", "कूल", LINE_BOX)
        found = find_quote(reader_text([span]), "कुल")
        assert found is not None and found.loose
        value = located_value(found, record_key="document", field="description")
        (labelled,) = label_quoted([value], [second_line("कूल", LINE_BOX)])
        self.assertEqual(reason(labelled), ("needs_review", "model_chosen", None))

    def test_an_ambiguous_match_is_not_verified(self) -> None:
        """Council fix 6: the quote fits a later spot too, so the first spot is a guess."""
        total, paid = Span("Total 500", "Total 500", LINE_BOX), Span("Paid 500", "Paid 500", BOX)
        found = find_quote(reader_text([total, paid]), "500")
        assert found is not None and found.ambiguous
        value = located_value(found, record_key="line:1", field="amount")
        (labelled,) = label_quoted([value], [second_line("Total 500", LINE_BOX)])
        self.assertEqual(reason(labelled), ("needs_review", "model_chosen", None))

    def test_a_quote_across_two_pages_is_needs_review(self) -> None:
        """Council fix 5: the spot is only page 1's part, so no reading of it can confirm it."""
        spans = [
            Span("Rs.", "Rs.", BoxLocation(1, 0.8, 0.90, 0.9, 0.92)),
            Span("1,000.00", "1,000.00", BoxLocation(2, 0.1, 0.05, 0.9, 0.07)),
        ]
        found = find_quote(reader_text(spans), "Rs. 1,000.00")
        assert found is not None
        value = located_value(found, record_key="line:1", field="amount")
        (labelled,) = label_quoted([value], spans, {found.location: "Rs. 1,000.00"})
        self.assertEqual(reason(labelled), ("needs_review", "model_chosen", None))

    def test_a_whole_token_run_inside_the_second_line_agrees_and_keeps_the_matched_part(self) -> None:
        (labelled,) = label_quoted(
            [quoted("1,000.00")], [second_line("Cotton cloth 5208 10 m 100.00 1,000.00", LINE_BOX)]
        )
        self.assertEqual(reason(labelled), ("verified", "readers_agree", "1,000.00"))

    def test_currency_and_case_do_not_matter_inside_the_line(self) -> None:
        (labelled,) = label_quoted(
            [quoted("Rs. 1,000.00", field="total_amount", record_key="document")],
            [second_line("GRAND TOTAL ₹1,000.00", LINE_BOX)],
        )
        self.assertEqual(reason(labelled), ("verified", "readers_agree", "1,000.00"))

    def test_part_of_a_longer_number_is_not_inside_the_line(self) -> None:
        line = "Total 1,000.50"
        (labelled,) = label_quoted([quoted("1,000")], [second_line(line, LINE_BOX)])
        self.assertEqual(reason(labelled), ("needs_review", "readers_disagree", line))

    def test_the_second_line_must_overlap_by_half(self) -> None:
        barely = second_line("1,000.00", BoxLocation(1, 0.1, 0.51, 0.9, 0.53))  # 1/3
        (labelled,) = label_quoted([quoted("1,000.00")], [barely])
        self.assertEqual(reason(labelled), ("needs_review", "single_reader", None))

    def test_a_vision_reading_must_equal_the_value_never_just_contain_it(self) -> None:
        (contains,) = label_quoted([quoted("1,000.00")], None, {LINE_BOX: "Amount 1,000.00 only"})
        self.assertEqual(reason(contains), ("needs_review", "readers_disagree", "Amount 1,000.00 only"))
        (equal,) = label_quoted([quoted("1,000.00")], None, {LINE_BOX: "₹ 1,000.00"})
        self.assertEqual(reason(equal), ("verified", "readers_agree", "₹ 1,000.00"))

    def test_a_box_shared_by_two_located_values_is_single_reader(self) -> None:
        rate, amount = quoted("100.00", field="rate"), quoted("1,000.00")
        labelled = label_quoted([rate, amount], [second_line("Cotton 10 m 100.00 1,000.00", LINE_BOX)])
        self.assertEqual([reason(value) for value in labelled], [("needs_review", "single_reader", None)] * 2)

    def test_two_values_with_the_same_text_in_one_box_are_single_reader(self) -> None:
        """Council fix 2: "Subtotal 500 Total 500" holds two values, whose texts are the same."""
        subtotal = quoted("500", field="subtotal", record_key="document")
        total = quoted("500", field="total_amount", record_key="document")
        labelled = label_quoted([subtotal, total], [second_line("Subtotal 500 Total 500", LINE_BOX)])
        self.assertEqual([reason(value) for value in labelled], [("needs_review", "single_reader", None)] * 2)

    def test_the_same_value_located_twice_is_one_value(self) -> None:
        twice = [quoted("1,000.00"), quoted("1,000.00")]
        labelled = label_quoted(twice, [second_line("Cotton 1,000.00", LINE_BOX)])
        self.assertEqual(
            [reason(value) for value in labelled], [("verified", "readers_agree", "1,000.00")] * 2
        )

    def test_a_value_located_elsewhere_in_the_same_box_counts_too(self) -> None:
        """Council fix 2: rule values and quoted values on one page are counted together."""
        rate = ExtractedValue("page:1:line:4", "rate", "100.00", "100.00", LINE_BOX)
        line = [second_line("Cotton 10 m 100.00 1,000.00", LINE_BOX)]
        (alone,) = label_quoted([quoted("1,000.00")], line)
        self.assertEqual(reason(alone), ("verified", "readers_agree", "1,000.00"))
        (shared,) = label_quoted([quoted("1,000.00")], line, also_located=[rate])
        self.assertEqual(reason(shared), ("needs_review", "single_reader", None))

    def test_an_ocr_value_ignores_the_second_line_and_needs_an_equal_vision_reading(self) -> None:
        """Council fix 1 (was ..._is_compared_by_equality_even_with_a_second_line, which
        pinned the bug): a text-layer line is never an OCR value's second reading."""
        value = OcrValue("line:1", "amount", "1,000.00", "1,000.00", LINE_BOX, origin="ocr")
        (labelled,) = label_quoted([value], [second_line("Cotton 1,000.00", LINE_BOX)])
        self.assertEqual(reason(labelled), ("needs_review", "single_reader", None))
        (agree,) = label_quoted([value], None, {LINE_BOX: "1,000.00"})
        self.assertEqual(reason(agree), ("verified", "readers_agree", "1,000.00"))

    def test_an_ocr_value_with_only_a_matching_second_reader_line_is_single_reader(self) -> None:
        value = OcrValue("line:1", "amount", "1,000.00", "1,000.00", LINE_BOX, origin="ocr")
        (labelled,) = label_quoted([value], [second_line("1,000.00", LINE_BOX)])
        self.assertEqual(reason(labelled), ("needs_review", "single_reader", None))
        script = OcrValue("document", "buyer_name", "पाटील", "पाटील", LINE_BOX, origin="ocr")
        (unsound,) = label_quoted([script], [second_line("पाटील", LINE_BOX)])
        self.assertEqual(reason(unsound), ("needs_review", "ocr_script", None))

    def test_an_ocr_value_in_devanagari_is_ocr_script(self) -> None:
        value = OcrValue("document", "buyer_name", "पाटील", "पाटील", LINE_BOX, origin="ocr")
        (labelled,) = label_quoted([value], None, {LINE_BOX: "पाटील"})
        self.assertEqual(reason(labelled), ("needs_review", "ocr_script", "पाटील"))

    def test_no_second_reading_is_single_reader_or_low_confidence(self) -> None:
        sure = quoted("1,000.00")
        unsure = ExtractedValue("line:1", "amount", "1,000.00", "1,000.00", BOX, 0.6)
        labelled = label_quoted([sure, unsure], [])
        self.assertEqual(
            [reason(value) for value in labelled],
            [("needs_review", "single_reader", None), ("needs_review", "low_confidence", None)],
        )


class LearnedTest(unittest.TestCase):
    def test_an_exact_field_and_raw_match_is_corrected_and_verified(self) -> None:
        value = box_value("1,13O.00")
        (fixed,) = apply_learned([value], [LearnedCorrection("total_amount", "1,13O.00", "1,130.00")])
        self.assertEqual((fixed.value_text, fixed.value_raw), ("1,130.00", "1,13O.00"))
        self.assertEqual((fixed.label, fixed.label_reason), ("verified", "learned_correction"))

    def test_anything_but_an_exact_match_is_left_alone(self) -> None:
        value = box_value("1,13O.00")
        learned = [
            LearnedCorrection("subtotal", "1,13O.00", "x"),  # another field
            LearnedCorrection("total_amount", "1,13o.00", "x"),  # another case
            LearnedCorrection("total_amount", "1,13O.00 ", "x"),  # a trailing space
        ]
        self.assertEqual(apply_learned([value], learned), [value])

    def test_a_card_value_is_never_corrected_into_verified(self) -> None:
        """Council fix 4: a remembered fix is for values the rules found; a card value keeps
        its model_chosen label, and its text stays the reader's."""
        card = ExtractedValue(
            "card",
            "total_amount",
            "1,13O.00",
            "1,13O.00",
            BOX,
            label="needs_review",
            label_reason="model_chosen",
        )
        fixes = [LearnedCorrection("total_amount", "1,13O.00", "1,130.00")]
        self.assertEqual(apply_learned([card], fixes), [card])


if __name__ == "__main__":
    unittest.main()
