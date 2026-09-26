"""Masking tests. Every number here is made up (FAKE) and follows the format
only; none belongs to a real person."""

import unittest

from kaagaz.masking.identity import IndianIdMasker

mask = IndianIdMasker().mask


class PanTest(unittest.TestCase):
    def test_pan_is_masked_keeping_the_last_four(self) -> None:
        self.assertEqual(mask("PAN ABCDE1234F"), "PAN XXXXXX234F")  # FAKE

    def test_pan_inside_a_gstin_is_masked(self) -> None:
        self.assertEqual(mask("GSTIN 27ABCDE1234F1Z5"), "GSTIN 27XXXXXX234F1Z5")  # FAKE

    def test_lowercase_pan_is_masked(self) -> None:
        self.assertEqual(mask("abcde1234f"), "XXXXXX234f")  # FAKE

    def test_pan_with_hidden_characters_is_masked(self) -> None:
        self.assertEqual(mask("ABCDE12\u200b34F"), "XXXXXXX\u200b34F")  # FAKE

    def test_pan_written_with_gaps_is_masked(self) -> None:
        self.assertEqual(mask("PAN ABCDE 1234 F"), "PAN XXXXX XX34 F")  # FAKE
        self.assertEqual(mask("ABCDE-1234-F"), "XXXXX-XX34-F")  # FAKE

    def test_ordinary_words_with_gaps_are_not_taken_for_a_pan(self) -> None:
        text = "Sales 2025 Q3 and March 2024 A"
        self.assertEqual(mask(text), text)


class AadhaarTest(unittest.TestCase):
    def test_separator_styles_are_all_masked(self) -> None:
        cases = {
            "2345 6789 0123": "XXXX XXXX 0123",  # FAKE
            "2345-6789-0123": "XXXX-XXXX-0123",  # FAKE
            "234567890123": "XXXXXXXX0123",  # FAKE
            "2345\u00a06789\u00a00123": "XXXX\u00a0XXXX\u00a00123",  # FAKE
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(mask(f"id {raw} end"), f"id {expected} end")

    def test_other_separator_styles_are_masked_too(self) -> None:
        # Changed after the council review: mixed or doubled separators, dots
        # and partial grouping are how real numbers get typed, so they are
        # masked (over-masking is the policy).
        cases = {
            "2345 6789-0123": "XXXX XXXX-0123",  # FAKE, mixed
            "2345  6789  0123": "XXXX  XXXX  0123",  # FAKE, doubled
            "2345.6789.0123": "XXXX.XXXX.0123",  # FAKE, dots
            "2345 67890123": "XXXX XXXX0123",  # FAKE, partial grouping
            "2345\t6789\t0123": "XXXX\tXXXX\t0123",  # FAKE, tabs
            "2345\u200b6789\u200b0123": "XXXX\u200bXXXX\u200b0123",  # FAKE, zero-width
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(mask(raw), expected)

    def test_scientific_notation_from_spreadsheets_is_masked(self) -> None:
        self.assertEqual(mask("2.34567890123E11"), "X.XXXXXXXXXX3E11")  # FAKE

    def test_numbers_starting_with_0_or_1_are_not_aadhaar(self) -> None:
        self.assertEqual(mask("123456789012"), "123456789012")

    def test_longer_digit_runs_are_left_alone(self) -> None:
        self.assertEqual(mask("2345678901234"), "2345678901234")  # 13 digits
        self.assertEqual(mask("1234567890123456"), "1234567890123456")  # card-length


class MaskingPropertiesTest(unittest.TestCase):
    def test_other_text_is_unchanged(self) -> None:
        text = "Invoice INV-1042 dated 12/09/2026, total 45,000.00, phone 9876543210"
        self.assertEqual(mask(text), text)

    def test_length_is_kept_so_positions_stay_valid(self) -> None:
        text = "a ABCDE1234F b 2345 6789 0123 c"  # FAKE
        self.assertEqual(len(mask(text)), len(text))

    def test_masking_twice_changes_nothing(self) -> None:
        once = mask("ABCDE1234F and 2345 6789 0123")  # FAKE
        self.assertEqual(mask(once), once)

    def test_devanagari_digits_are_not_matched(self) -> None:
        text = "२३४५ ६७८९ ०१२३"
        self.assertEqual(mask(text), text)

    def test_empty_text(self) -> None:
        self.assertEqual(mask(""), "")


if __name__ == "__main__":
    unittest.main()
