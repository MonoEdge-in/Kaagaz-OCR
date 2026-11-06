"""Amounts, dates and field names as Indian business documents write them.

Every expected value is a literal written by hand, never computed by the code
under test.
"""

import unittest
from datetime import date
from decimal import Decimal

from kaagaz.extraction.parsing import normalise_field, parse_amount, parse_date

MINUS = chr(0x2212)  # the Unicode minus sign


class ParseAmountTest(unittest.TestCase):
    def test_amounts_that_are_read(self) -> None:
        cases = {
            "₹1,23,456.78": "123456.78",
            "₹ 1,23,456.78": "123456.78",
            "Rs. 1,234/-": "1234",
            "Rs.1,234/-": "1234",
            "Rs 1,234.50": "1234.50",
            "rs. 99": "99",
            "RS. 12,00,000": "1200000",
            "INR 1,234": "1234",
            "inr1234": "1234",
            "1,234.00 Dr": "1234.00",
            "1,234.00 Dr.": "1234.00",
            "(1,234)": "-1234",
            "(Rs. 1,234.50)": "-1234.50",
            "-1,234": "-1234",
            "- 1,234": "-1234",
            MINUS + "1,234": "-1234",
            "Rs. -500": "-500",
            "-₹500": "-500",
            "1,234,567.89": "1234567.89",
            "12,345": "12345",
            "1234.5": "1234.5",
            "100": "100",
            "0": "0",
            "0.50": "0.50",
            "  250  ": "250",
            "10,00,00,000.00": "100000000.00",
            "₹ १,२३,४५६.७८": "123456.78",  # Devanagari digits (L4)
            "१२,३४५": "12345",
            # #524: an end mark is never a digit, and Indic digits are read as ASCII ones.
            "11500000/-": "11500000",
            "1,15,00,000/-": "11500000",
            "Rs. 9,40,000/=": "940000",
            "9,40,000 / -": "940000",
            "940000-/-": "940000",
            "940000/": "940000",
            "940000।": "940000",
            "९४००००॥": "940000",
            "३४०००० ": "340000",
            "૯૪૦૦૦૦": "940000",  # Gujarati digits
            "१2,3४5": "12345",  # mixed: read, and the value goes to review (labels.label_amounts)
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_amount(text), Decimal(expected))

    def test_text_that_is_never_guessed_to_be_an_amount(self) -> None:
        cases = [
            "12 pcs",
            "",
            "   ",
            "Rs.",
            "₹",
            "abc",
            "INV-1",
            "1,2,3",
            "1,2345",
            "12,34",
            "1.234,56",
            "1,234.5.6",
            "18%",
            "1e5",
            "--5",
            "-₹-500",
            "(-500)",
            "5-",
            "007",
            "05",
            ",123",
            "1,234.00 Cr",  # PROVISIONAL: a credit's sign is not guessed
            "(1,234) Dr",
            "12/08/2026",
            "Aug 2026",
            "١٢٣",  # Arabic-Indic digits: only ASCII and Indic digits count
            # Changed on purpose (#524, Tushar 6 Nov 2026): ASCII and Devanagari digits mixed
            # are read now, and the value goes to review (test_amount_guard.py).
            "115000001-",  # "/-" read as "1-": never guessed (#524)
            "11500000/1",
            "1" * 70,  # longer than any single amount
            "1,234 only",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(parse_amount(text))


class ParseDateTest(unittest.TestCase):
    def test_dates_that_are_read_day_first(self) -> None:
        cases = {
            "12/08/2026": date(2026, 8, 12),
            "12-08-2026": date(2026, 8, 12),
            "12.08.2026": date(2026, 8, 12),
            "1/8/2026": date(2026, 8, 1),
            "03/04/2026": date(2026, 4, 3),  # 3 April, never 4 March
            "2026-08-12": date(2026, 8, 12),
            "2026-8-1": date(2026, 8, 1),
            "12 Aug 2026": date(2026, 8, 12),
            "12 aug 2026": date(2026, 8, 12),
            "12-Aug-26": date(2026, 8, 12),
            "12-Aug-2026": date(2026, 8, 12),
            "12 August 2026": date(2026, 8, 12),
            "12 August, 2026": date(2026, 8, 12),
            "5 Sept 2026": date(2026, 9, 5),
            "31 Dec 2025": date(2025, 12, 31),
            "August 2026": date(2026, 8, 1),
            "Aug-2026": date(2026, 8, 1),
            "aug 2026": date(2026, 8, 1),
            "March 2026": date(2026, 3, 1),
            "29/02/2028": date(2028, 2, 29),
            " 12/08/2026 ": date(2026, 8, 12),
            "१२/०८/२०२६": date(2026, 8, 12),  # Devanagari digits (L4)
            "१२ Aug २०२६": date(2026, 8, 12),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_date(text), expected)

    def test_text_that_is_never_guessed_to_be_a_date(self) -> None:
        cases = [
            "",
            "12/08/26",  # a two digit year in a numeric date: the century would be a guess
            "Aug-26",  # August 2026, or 26 August?
            "31/02/2026",
            "29/02/2026",
            "12/13/2026",  # no 13th month: never swapped to month first
            "00/08/2026",
            "12/08-2026",  # mixed separators
            "12 Foo 2026",
            "Foo 2026",
            "2026",
            "12/08/1850",
            "1२/08/2026",  # ASCII and Devanagari digits mixed: never guessed (council)
            "12/08/2150",
            "2026-13-01",
            "12 Aug",
            "Aug 12, 2026",  # month first is not how Indian documents write dates
            "12082026",
            "1,234",
            "next Monday",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(parse_date(text))


class NormaliseFieldTest(unittest.TestCase):
    def test_labels_become_snake_case_ascii(self) -> None:
        cases = {
            "Invoice No.": "invoice_no",
            "Invoice Number": "invoice_number",
            "  Amount (₹) ": "amount",
            "Party Name": "party_name",
            "GSTIN": "gstin",
            "S.No": "s_no",
            "Net-Profit": "net_profit",
            "Café": "cafe",
            "total_amount": "total_amount",
            "Qty2": "qty2",
            "a" * 63: "a" * 63,
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(normalise_field(label), expected)

    def test_labels_that_give_no_field(self) -> None:
        for label in ["", "   ", "%", "₹", "2024 Sales", "_", "a" * 64, "नाम"]:
            with self.subTest(label=label):
                self.assertIsNone(normalise_field(label))


if __name__ == "__main__":
    unittest.main()
