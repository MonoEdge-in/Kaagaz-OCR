"""PRD 5's labels for values read by OCR: the checker's agreement (V2, V3) and what the
main reader said about each line (V4 unsure_text, G5 box_unanchored, Q7 local_reader)."""

import unittest

from kaagaz.extraction.labels import LineMarks, label_quoted, label_reading_marks, label_values
from kaagaz.extraction.values import CARD_RECORD, ExtractedValue
from kaagaz.reading.spans import BoxLocation

BOX = BoxLocation(1, 0.1, 0.40, 0.9, 0.42)
OTHER = BoxLocation(1, 0.1, 0.60, 0.9, 0.62)
PAGE_2 = BoxLocation(2, 0.1, 0.40, 0.9, 0.42)


def ocr_value(text: str, *, field: str = "total_amount", box: BoxLocation = BOX) -> ExtractedValue:
    return ExtractedValue("document", field, text, text, box, origin="ocr")


def reason(value: ExtractedValue) -> tuple[str, str, str | None]:
    return value.label, value.label_reason, value.second_reading


class CheckerAgreementTest(unittest.TestCase):
    """V2 and V3: the checker's reading of a value's own box."""

    def test_v2_agreement_ignores_case_spaces_currency_and_devanagari_digits(self) -> None:
        cases = {
            "Rs. 1,180.00": "rs.  1,180.00",
            "INV-17": "inv-17",
            "१,१८०": "1,180",
            "Total  Due": "total due",
        }
        for value, reading in cases.items():
            with self.subTest(value=value):
                (labelled,) = label_values(
                    [ocr_value(value, field="party_name")], None, {BOX: reading}, checked={BOX}
                )
                self.assertEqual(labelled.label_reason, "readers_agree")

    def test_v2_a_different_reading_is_readers_disagree_and_kept_for_the_review_card(self) -> None:
        (labelled,) = label_values([ocr_value("1,180.00")], None, {BOX: "1,130.00"}, checked={BOX})
        self.assertEqual(reason(labelled), ("needs_review", "readers_disagree", "1,130.00"))

    def test_v3_a_devanagari_value_is_verified_only_by_exact_agreement(self) -> None:
        value = ocr_value("रामचंद्र पाटील", field="party_name")
        (agree,) = label_values([value], None, {BOX: "रामचंद्र पाटील"}, checked={BOX})
        self.assertEqual(reason(agree), ("verified", "readers_agree", "रामचंद्र पाटील"))
        (differ,) = label_values([value], None, {BOX: "रामचंद्र पाटिल"}, checked={BOX})
        self.assertEqual(reason(differ), ("needs_review", "readers_disagree", "रामचंद्र पाटिल"))

    def test_v3_when_rapidocr_reads_nothing_the_reason_stays_ocr_script(self) -> None:
        value = ocr_value("पाटील", field="party_name")
        (labelled,) = label_values([value], None, {}, checked={BOX})
        self.assertEqual(reason(labelled), ("needs_review", "ocr_script", None))

    def test_v3_a_vision_reading_never_verifies_devanagari(self) -> None:
        """Only the checker's reading (``checked``) can verify a Devanagari value."""
        (labelled,) = label_values([ocr_value("पाटील", field="party_name")], None, {BOX: "पाटील"})
        self.assertEqual(reason(labelled), ("needs_review", "ocr_script", "पाटील"))

    def test_v3_letters_of_another_script_stay_ocr_script_even_when_the_checker_agrees(self) -> None:
        text = "Sharmaئ"
        (labelled,) = label_values([ocr_value(text, field="party_name")], None, {BOX: text}, checked={BOX})
        self.assertEqual(labelled.label_reason, "ocr_script")

    def test_v3_a_devanagari_value_the_model_located_follows_the_same_rule(self) -> None:
        value = ExtractedValue("line:1", "buyer_name", "पाटील", "पाटील", BOX, origin="ocr")
        (agree,) = label_quoted([value], None, {BOX: "पाटील"}, checked={BOX})
        self.assertEqual(agree.label_reason, "readers_agree")
        (unread,) = label_quoted([value], None, {}, checked={BOX})
        self.assertEqual(unread.label_reason, "ocr_script")


class ReadingMarksTest(unittest.TestCase):
    """V4, G5 and Q7, after the labellers."""

    def agreed(self, text: str = "1,180.00", box: BoxLocation = BOX) -> ExtractedValue:
        (value,) = label_values([ocr_value(text, box=box)], None, {box: text}, checked={box})
        self.assertEqual(value.label_reason, "readers_agree")
        return value

    def test_v4_an_unsure_word_wins_even_when_the_checker_agrees(self) -> None:
        marks = {BOX: LineMarks(unsure=("1,180.00.",))}
        (value,) = label_reading_marks([self.agreed()], marks, ())
        self.assertEqual(reason(value), ("needs_review", "unsure_text", "1,180.00"))

    def test_v4_an_illegible_mark_in_the_value_is_unsure_text(self) -> None:
        (value,) = label_reading_marks([self.agreed("1,1[?]0.00")], {}, ())
        self.assertEqual(value.label_reason, "unsure_text")

    def test_v4_an_unsure_word_elsewhere_on_the_line_leaves_the_value_alone(self) -> None:
        """Edge case: handwritten notes in the margin are unsure; only their values go to review."""
        marks = {BOX: LineMarks(unsure=("Total",))}
        (value,) = label_reading_marks([self.agreed()], marks, ())
        self.assertEqual(reason(value), ("verified", "readers_agree", "1,180.00"))

    def test_g5_a_value_whose_line_is_unpinned_is_box_unanchored(self) -> None:
        marks = {BOX: LineMarks(unpinned=True), OTHER: LineMarks()}
        unpinned, pinned = label_reading_marks([self.agreed(), self.agreed(box=OTHER)], marks, ())
        self.assertEqual(reason(unpinned), ("needs_review", "box_unanchored", "1,180.00"))
        self.assertEqual(pinned.label_reason, "readers_agree")

    def test_q7_every_value_of_a_page_read_locally_is_local_reader(self) -> None:
        one, two = ocr_value("1,180.00"), ocr_value("500", box=PAGE_2)
        labelled = label_reading_marks([one, two], {BOX: LineMarks(unsure=("1,180.00",))}, {1})
        self.assertEqual([value.label_reason for value in labelled], ["local_reader", "single_reader"])
        self.assertEqual(labelled[0].label, "needs_review")

    def test_the_order_is_local_reader_then_unsure_text_then_box_unanchored(self) -> None:
        marks = {BOX: LineMarks(unsure=("1,180.00",), unpinned=True)}
        (local,) = label_reading_marks([self.agreed()], marks, {1})
        (unsure,) = label_reading_marks([self.agreed()], marks, ())
        self.assertEqual((local.label_reason, unsure.label_reason), ("local_reader", "unsure_text"))

    def test_a_card_value_and_values_not_read_by_ocr_keep_their_label(self) -> None:
        card = ExtractedValue(CARD_RECORD, "summary", "1,180.00", "1,180.00", BOX, origin="ocr")
        (card,) = label_values([card], None)
        text = ExtractedValue("document", "total_amount", "1,180.00", "1,180.00", BOX, origin="text_layer")
        marks = {BOX: LineMarks(unsure=("1,180.00",), unpinned=True)}
        labelled = label_reading_marks([card, text], marks, {1})
        self.assertEqual([value.label_reason for value in labelled], ["model_chosen", text.label_reason])


if __name__ == "__main__":
    unittest.main()
