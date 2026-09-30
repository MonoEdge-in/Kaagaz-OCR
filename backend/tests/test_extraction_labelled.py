"""Values from a fixed list of labels on text lines."""

import unittest

from kaagaz.extraction.labelled import tax_components, value_after_label, values_from_lines
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


class TaxComponentsTest(unittest.TestCase):
    """ML amendment 6(a): CGST, SGST, UTGST, IGST and cess lines, for the invoice total check."""

    def components(self, *texts: str) -> list[tuple[str, str]]:
        spans = [line(t, 0.05 * (i + 1)) for i, t in enumerate(texts)]
        return [(v.record_key, v.value_text) for v in tax_components(spans)]

    def test_each_component_with_or_without_its_rate(self) -> None:
        found = self.components(
            "CGST: 900.00",
            "SGST @ 9%: 900.00",
            "UTGST 9% 450.00",
            "IGST (18%) 1,800.00",
            "Cess: 100.00",
            "Add: CGST @ 2.5% Rs. 25.00",
        )
        self.assertEqual(
            [text for _, text in found], ["900.00", "900.00", "450.00", "1,800.00", "100.00", "Rs. 25.00"]
        )

    def test_every_component_is_a_tax_amount_at_its_line(self) -> None:
        span = line("IGST: 1,800.00", 0.3, confidence=0.91)
        (value,) = tax_components([span])
        self.assertEqual((value.field, value.location, value.confidence), ("tax_amount", span.location, 0.91))

    def test_two_components_on_one_line_are_both_kept(self) -> None:
        found = self.components("CGST 9% 900.00 SGST 9% 900.00")
        self.assertEqual([text for _, text in found], ["900.00", "900.00"])

    def test_the_same_component_at_the_same_spot_counts_once(self) -> None:
        span = line("CGST: 900.00", 0.3)
        self.assertEqual(len(tax_components([span, span])), 1)

    def test_record_keys_are_those_of_the_stored_tax_values(self) -> None:
        # The CGST and SGST lines are also labelled.py tax_amount values; the IGST line with a rate is not.
        self.assertEqual(
            self.components("CGST: 900.00", "SGST: 900.00", "IGST @ 18% 1,800.00"),
            [("document", "900.00"), ("page:1:line:2", "900.00"), ("page:1:line:3", "1,800.00")],
        )

    def test_lines_that_are_not_a_component_amount_give_nothing(self) -> None:
        self.assertEqual(
            self.components(
                "Carried forward CGST: 450.00",
                "Brought forward SGST 450.00",
                "CGST Rate 9%",
                "HSN Taxable CGST SGST",
                "5208 10,000.00 9% 900.00 9% 900.00",
                "Access fee: 50.00",
                "Total Tax: 1,800.00",
            ),
            [],
        )

    def test_cells_are_ignored(self) -> None:
        self.assertEqual(tax_components([Span("CGST: 9", "CGST: 9", CellLocation(1, 1, 1))]), [])


if __name__ == "__main__":
    unittest.main()
