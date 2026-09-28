"""Verified or needs review, and why, for every kind of value."""

import unittest

from kaagaz.extraction.labels import comparable, label_values, overlap
from kaagaz.extraction.learned import LearnedCorrection, apply_learned
from kaagaz.extraction.values import ExtractedValue
from kaagaz.reading.spans import BoxLocation, CellLocation, Span

BOX = BoxLocation(1, 0.1, 0.40, 0.9, 0.42)


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

    def test_a_reading_by_box_wins_over_the_second_reader(self) -> None:
        values = [box_value(confidence=0.5)]
        (labelled,) = label_values(values, [second_line("Total: 1,130.00")], {BOX: "Total: 1,180.00"})
        self.assertEqual(reason(labelled), ("verified", "readers_agree", "1,180.00"))


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


if __name__ == "__main__":
    unittest.main()
