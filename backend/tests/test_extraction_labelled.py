"""Values from a fixed list of labels on text lines."""

import unittest

from kaagaz.extraction.labelled import value_after_label, values_from_lines
from kaagaz.reading.spans import BoxLocation, CellLocation, Span


def line(text: str, top: float = 0.1, *, page: int = 1, confidence: float | None = None) -> Span:
    return Span(text, text, BoxLocation(page, 0.1, top, 0.9, top + 0.02), confidence)


def pairs(*texts: str) -> list[tuple[str, str]]:
    spans = [line(t, 0.05 * (i + 1)) for i, t in enumerate(texts)]
    return [(v.field, v.value_text) for v in values_from_lines(spans)]


class LabelListTest(unittest.TestCase):
    def test_every_label_on_the_list_gives_its_field(self) -> None:
        cases = {
            "Invoice No: INV-17": ("invoice_number", "INV-17"),
            "Invoice Number: 45": ("invoice_number", "45"),
            "Bill No 99": ("invoice_number", "99"),
            "Invoice Date: 12/08/2026": ("invoice_date", "12/08/2026"),
            "Bill Date 12-Aug-26": ("invoice_date", "12-Aug-26"),
            "Date: 12.08.2026": ("invoice_date", "12.08.2026"),
            "Grand Total: Rs. 1,180.00": ("total_amount", "Rs. 1,180.00"),
            "Total Amount ₹ 1,180": ("total_amount", "₹ 1,180"),
            "Amount Payable - 1,180.00": ("total_amount", "1,180.00"),
            "Net Amount: 1180": ("total_amount", "1180"),
            "Total 1,180.00": ("total_amount", "1,180.00"),
            "Sub Total: 1,000.00": ("subtotal", "1,000.00"),
            "CGST: 90.00": ("tax_amount", "90.00"),
            "SGST 90.00": ("tax_amount", "90.00"),
            "IGST: 180": ("tax_amount", "180"),
            "Total Tax: 180.00": ("tax_amount", "180.00"),
            "GSTIN: 27XXXXXX234F1Z5": ("gstin", "27XXXXXX234F1Z5"),
            "Seller: Sharma Traders": ("party_name", "Sharma Traders"),
            "Supplier: Sharma Traders": ("party_name", "Sharma Traders"),
            "Vendor - Sharma Traders": ("party_name", "Sharma Traders"),
            "From: Sharma Traders": ("party_name", "Sharma Traders"),
            "Billed By: Sharma Traders": ("party_name", "Sharma Traders"),
            "Bill To: Mehta Textiles": ("buyer_name", "Mehta Textiles"),
            "Buyer: Mehta Textiles": ("buyer_name", "Mehta Textiles"),
            "Customer: Mehta Textiles": ("buyer_name", "Mehta Textiles"),
            "Billed To: Mehta Textiles": ("buyer_name", "Mehta Textiles"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(pairs(text), [expected])

    def test_labels_match_in_any_case_with_a_dot_and_spaced_colons(self) -> None:
        self.assertEqual(pairs("INVOICE NO. : INV-17"), [("invoice_number", "INV-17")])
        self.assertEqual(pairs("grand   total:1,180"), [("total_amount", "1,180")])

    def test_a_longer_label_wins_and_a_shorter_one_is_never_tried_after(self) -> None:
        self.assertEqual(pairs("Total Tax: 180"), [("tax_amount", "180")])
        self.assertEqual(pairs("Sub Total 500"), [("subtotal", "500")])
        self.assertEqual(pairs("Total Tax"), [])  # no value: never read as "Total" = "Tax"

    def test_a_label_inside_a_longer_word_is_not_a_label(self) -> None:
        for text in ("Totalling 500", "Dated 12/08/2026", "Customers: many", "Fromage: 3"):
            with self.subTest(text=text):
                self.assertEqual(pairs(text), [])

    def test_a_label_with_nothing_after_it_gives_nothing(self) -> None:
        for text in ("Invoice No:", "Total", "Seller :   ", "Date -"):
            with self.subTest(text=text):
                self.assertEqual(pairs(text), [])

    def test_amount_and_date_fields_are_kept_only_when_they_read_as_one(self) -> None:
        for text in ("Total Items 5", "Total: see attached", "Date of Supply: 12/08/2026", "CGST 9% 90.00"):
            with self.subTest(text=text):
                self.assertEqual(pairs(text), [])

    def test_a_dash_right_before_a_number_is_its_sign(self) -> None:
        self.assertEqual(pairs("Net Amount -1,180"), [("total_amount", "-1,180")])

    def test_several_labels_on_one_line_are_split_before_each_colon_label(self) -> None:
        self.assertEqual(
            pairs("Bill No: 17   Date: 12/08/2026"),
            [("invoice_number", "17"), ("invoice_date", "12/08/2026")],
        )
        self.assertEqual(
            pairs("Bill To: Mehta Textiles GSTIN: 24XXXXXX678K1Z2"),
            [("buyer_name", "Mehta Textiles"), ("gstin", "24XXXXXX678K1Z2")],
        )
        # "Date" inside "Invoice Date:" is not a second label.
        self.assertEqual(pairs("Invoice Date: 12/08/2026"), [("invoice_date", "12/08/2026")])

    def test_lines_without_a_label_give_nothing(self) -> None:
        self.assertEqual(pairs("Sharma Traders", "Thank you for your business", "1,180.00"), [])


class RecordKeyTest(unittest.TestCase):
    def test_the_first_match_is_the_document_and_the_rest_are_their_own_records(self) -> None:
        spans = [
            line("Seller: Sharma Traders", 0.05, page=2),
            line("CGST: 90.00", 0.10),
            line("SGST: 90.00", 0.15),
            line("Total: 1,180.00", 0.20),
            line("CGST: 45.00", 0.10, page=2),
        ]
        values = values_from_lines(spans)
        self.assertEqual(
            [(v.record_key, v.field, v.value_text) for v in values],
            [
                ("document", "tax_amount", "90.00"),
                ("page:1:line:2", "tax_amount", "90.00"),
                ("document", "total_amount", "1,180.00"),
                ("document", "party_name", "Sharma Traders"),
                ("page:2:line:2", "tax_amount", "45.00"),
            ],
        )

    def test_each_value_points_at_its_line_and_keeps_its_confidence(self) -> None:
        span = line("Total: 1,180.00", 0.4, confidence=0.93)
        (value,) = values_from_lines([span])
        self.assertEqual(value.location, span.location)
        self.assertEqual(value.confidence, 0.93)
        self.assertEqual((value.value_text, value.value_raw), ("1,180.00", "1,180.00"))

    def test_cells_are_ignored(self) -> None:
        self.assertEqual(values_from_lines([Span("Total: 5", "Total: 5", CellLocation(1, 1, 1))]), [])


class ValueAfterLabelTest(unittest.TestCase):
    def test_finds_the_same_field_in_another_reading_unchecked(self) -> None:
        self.assertEqual(value_after_label("total_amount", "Grand Total: Rs. 1,13O.00"), "Rs. 1,13O.00")
        self.assertEqual(value_after_label("invoice_date", "Bill No: 17 Date: 12/08/2026"), "12/08/2026")

    def test_none_when_the_label_is_not_there(self) -> None:
        self.assertIsNone(value_after_label("total_amount", "1,180.00"))
        self.assertIsNone(value_after_label("gstin", "Total: 1,180.00"))


if __name__ == "__main__":
    unittest.main()
