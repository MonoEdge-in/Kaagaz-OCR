"""The invoice checks (PRD 2 K1 to K3, ML amendment 6, test-engineer amendment 7).

Every expected value is a literal written by hand. The GSTINs are made up, never
a real company's number; their check characters come from a separate Luhn mod 36
below, written the way it is usually published (from the right), and one is
worked by hand.
"""

import random
import unittest
from datetime import date
from decimal import Decimal

from kaagaz.extraction.checks import (
    ALLOWANCE,
    EARLIEST,
    NOT_AN_ITEM,
    REASONS,
    CheckResult,
    apply_checks,
    check_invoice,
    gstin_check_char,
    gstin_valid,
)
from kaagaz.extraction.values import NEEDS_REVIEW, VERIFIED, ExtractedValue
from kaagaz.reading.spans import BoxLocation

TODAY = date(2026, 9, 30)
BOX = BoxLocation(1, 0.1, 0.1, 0.9, 0.12)
CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Worked by hand for 29ABCDE1234F1Z. Values: 0-9 are themselves, A=10 ... Z=35.
# Characters at odd positions (1, 3, ... 13, from 0) are doubled; each product p
# adds p // 36 + p % 36:
#   2 -> 2;  9x2=18 -> 18;  A=10;  Bx2=22;  C=12;  Dx2=26;  E=14;
#   1x2=2;  2;  3x2=6;  4;  Fx2=30;  1;  Zx2=70 -> 1 + 34 = 35.
# The sum is 184; 184 % 36 = 4; (36 - 4) % 36 = 32, which is W.
HAND_WORKED = "29ABCDE1234F1ZW"
# From the reference below. Also by hand (council 9), each sum as above, then (36 - sum % 36) % 36:
#   27AAPFU0939F1Z (the published example) sums to 221: 221 % 36 = 5, 36 - 5 = 31, V;
#   29ABCDE1234F1Z sums to 184: W (worked above);
#   10AAAAA0000A1Z sums to 127: 127 % 36 = 19, 36 - 19 = 17, H.
KNOWN = [
    "29ABCDE1234F1ZW",
    "27ABCDE1234F1Z0",
    "07PQRST5678K2Z3",
    "33ZZZZZ9999Z9Z7",
    "24MNOPQ4321R1Z1",
    "10AAAAA0000A1ZH",
]


def reference_check_char(body: str) -> str:
    """Luhn mod 36 as usually published: from the right, the rightmost doubled."""
    total, factor = 0, 2
    for ch in reversed(body):
        product = factor * CHARS.index(ch)
        total += product // 36 + product % 36
        factor = 1 if factor == 2 else 2
    return CHARS[(36 - total % 36) % 36]


def doc(field: str, text: str, *, number: Decimal | None = None) -> ExtractedValue:
    return ExtractedValue("document", field, text, text, BOX, value_number=number)


def item(n: int, description: str, amount: str) -> list[ExtractedValue]:
    return [
        ExtractedValue(f"line:{n}", "description", description, description, BOX),
        ExtractedValue(f"line:{n}", "amount", amount, amount, BOX),
    ]


def invoice(*extra: ExtractedValue, total: str = "11800.00") -> list[ExtractedValue]:
    """A complete invoice that passes: 3 lines, subtotal 10000, tax 1800, total 11800."""
    return [
        doc("invoice_number", "INV-17"),
        doc("invoice_date", "12/08/2026"),
        doc("gstin", HAND_WORKED),
        doc("subtotal", "10,000.00"),
        doc("tax_amount", "1,800.00"),
        doc("total_amount", total),
        *item(1, "Cotton fabric", "3,000.00"),
        *item(2, "Sub/Fabric lining", "2,000.00"),
        *item(3, "Silk", "5,000.00"),
        *extra,
    ]


NO_CONFLICTS: frozenset[str] = frozenset()


def check(
    values: list[ExtractedValue],
    *,
    today: date = TODAY,
    conflicts: frozenset[str] = NO_CONFLICTS,
    truncated: bool = False,
    tax_components: tuple[ExtractedValue, ...] = (),
) -> CheckResult:
    return check_invoice(
        values, today=today, conflicts=conflicts, truncated=truncated, tax_components=tax_components
    )


def replaced(values: list[ExtractedValue], record_key: str, field: str, text: str) -> list[ExtractedValue]:
    return [
        ExtractedValue(v.record_key, v.field, text, text, v.location)
        if (v.record_key, v.field) == (record_key, field)
        else v
        for v in values
    ]


def without(values: list[ExtractedValue], *fields: str) -> list[ExtractedValue]:
    return [v for v in values if not (v.record_key == "document" and v.field in fields)]


class GstinTest(unittest.TestCase):
    def test_the_hand_worked_vector(self) -> None:
        self.assertEqual(gstin_check_char(HAND_WORKED[:14]), "W")

    def test_known_answer_vectors(self) -> None:
        for gstin in KNOWN:
            with self.subTest(gstin=gstin):
                self.assertEqual(reference_check_char(gstin[:14]), gstin[14])
                self.assertEqual(gstin_check_char(gstin[:14]), gstin[14])
                self.assertTrue(gstin_valid(gstin))

    def test_it_agrees_with_the_separate_implementation(self) -> None:
        rng = random.Random(20260930)  # noqa: S311 - a fixed seed makes test numbers, not a secret
        for _ in range(2000):
            body = "".join(rng.choices(CHARS, k=14))
            self.assertEqual(gstin_check_char(body), reference_check_char(body), body)

    def test_any_single_substitution_changes_the_check_character(self) -> None:
        rng = random.Random(20261001)  # noqa: S311 - a fixed seed makes test numbers, not a secret
        for _ in range(2000):
            body = "".join(rng.choices(CHARS, k=14))
            at = rng.randrange(14)
            changed = body[:at] + rng.choice(CHARS.replace(body[at], "")) + body[at + 1 :]
            self.assertNotEqual(gstin_check_char(changed), gstin_check_char(body), (body, changed))

    def test_lower_case_and_spaces_are_accepted(self) -> None:
        for text in ("29abcde1234f1zw", "29 ABCDE 1234 F1ZW", " 29ABCDE1234F1ZW\t", "10aaaaa0000a1zh"):
            with self.subTest(text=text):
                self.assertTrue(gstin_valid(text))

    def test_junk_and_wrong_shapes_are_refused(self) -> None:
        cases = [
            "29ABCDE1234F1ZX",  # a wrong check character
            "29ABCDE1234F1ZWX",  # 16 characters
            "29ABCDE1234F1Z",  # 14 characters
            "GSTIN:29ABCDE1234F1ZW",  # leading junk
            "29ABCDE1234F1ZW.",  # trailing junk
            "29-ABCDE-1234-F1ZW",  # other separators
            "",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertFalse(gstin_valid(text))

    def test_the_pattern_refuses_a_right_check_character_in_the_wrong_shape(self) -> None:
        # Digits where letters belong; a 0 in position 12; no Z in position 13; a letter first.
        for body in ("2912345123451Z", "29ABCDE1234F0Z", "29ABCDE1234F1Y", "A9ABCDE1234F1Z"):
            text = body + reference_check_char(body)
            with self.subTest(text=text):
                self.assertFalse(gstin_valid(text))

    def test_non_ascii_look_alikes_are_refused(self) -> None:
        body = "27ABCDI1234F1Z"
        good = body + reference_check_char(body)
        self.assertTrue(gstin_valid(good))
        dotless = good.replace("I", "\N{LATIN SMALL LETTER DOTLESS I}")  # upper() would make it I
        self.assertFalse(gstin_valid(dotless))
        self.assertFalse(gstin_valid("\N{FULLWIDTH DIGIT TWO}\N{FULLWIDTH DIGIT NINE}" + HAND_WORKED[2:]))
        self.assertFalse(gstin_valid("२९" + HAND_WORKED[2:]))

    def test_the_check_character_needs_14_digits_or_capitals(self) -> None:
        for body in ("29ABCDE1234F1", "29ABCDE1234F1ZW", "29abcde1234f1z", "29ABCDE1234F1!"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                gstin_check_char(body)


class NotAnItemTest(unittest.TestCase):
    def test_brought_and_carried_forward_are_not_items(self) -> None:
        cases = [
            "Brought forward",
            "BROUGHT  FORWARD",
            "Carried forward",
            "carried down",
            "Carried over",
            "b/f",
            "B/F",
            "c/f",
            "C / F",
            "b/fwd",
            "C/Fwd",
            "Balance b/d",
            "Total c/d 12,000",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNotNone(NOT_AN_ITEM.search(text))

    def test_real_items_are_items(self) -> None:
        cases = [
            "Sub/Fabric",
            "Sub/Fabric lining",
            "Cotton fabric",
            "Carrier bag",
            "B/W print",
            "C/o Patil",
            "ABC/F-12",
            "Bought forward goods",
            "Forwarding charges",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(NOT_AN_ITEM.search(text))


class LinesCheckTest(unittest.TestCase):
    def test_the_lines_sum_to_the_subtotal_within_the_allowance(self) -> None:
        self.assertEqual(ALLOWANCE, Decimal("1.00"))
        cases = [
            ("10,001.00", "11,801.00", "passed"),
            ("9,999.00", "11,799.00", "passed"),
            ("10,001.01", "11,801.01", "failed"),
            ("9,998.99", "11,798.99", "failed"),
        ]
        for subtotal, total, status in cases:
            with self.subTest(subtotal=subtotal):
                result = check(replaced(invoice(total=total), "document", "subtotal", subtotal))
                self.assertEqual(result.status, status)
                self.assertEqual(result.reasons, () if status == "passed" else ("lines_sum_mismatch",))

    def test_brought_and_carried_forward_lines_are_not_summed(self) -> None:
        for description in ("Brought forward", "Carried over", "B/F"):
            with self.subTest(description=description):
                result = check(invoice(*item(4, description, "5,000.00")))
                self.assertEqual((result.status, result.reasons), ("passed", ()))

    def test_sub_fabric_is_an_item(self) -> None:
        values = [v for v in invoice() if v.record_key != "line:2"]  # the "Sub/Fabric lining" line
        self.assertEqual(check(invoice()).status, "passed")
        self.assertEqual(check(values).reasons, ("lines_sum_mismatch",))

    def test_with_no_subtotal_and_no_tax_the_lines_are_compared_with_the_total(self) -> None:
        for total, status in (("10,001.00", "passed"), ("10,001.01", "failed")):
            with self.subTest(total=total):
                self.assertEqual(
                    check(without(invoice(total=total), "subtotal", "tax_amount")).status, status
                )
        failed = check(without(invoice(total="10,001.01"), "subtotal", "tax_amount"))
        self.assertEqual(failed.reasons, ("lines_sum_mismatch",))
        self.assertIn(("document", "total_amount"), failed.flagged)

    def test_with_a_tax_but_no_subtotal_the_lines_are_not_checked(self) -> None:
        # The total includes the tax, so the lines (10,000) are never compared with it (11,800).
        self.assertEqual(check(without(invoice(), "subtotal")), CheckResult("not_checked", (), frozenset()))

    def test_a_line_amount_that_is_not_a_number_stops_the_lines_check(self) -> None:
        values = replaced(invoice(), "line:2", "amount", "2,000 pcs")
        values = replaced(values, "document", "subtotal", "99.00")  # would fail if the lines were summed
        result = check(replaced(values, "document", "total_amount", "1,899.00"))
        self.assertEqual((result.status, result.reasons), ("passed", ()))

    def test_with_no_lines_the_total_check_alone_decides(self) -> None:
        values = [v for v in invoice() if v.record_key == "document"]
        self.assertEqual(check(values).status, "passed")
        self.assertEqual(check([*values[:-1], doc("total_amount", "12,000.00")]).status, "failed")


class TotalCheckTest(unittest.TestCase):
    def test_subtotal_and_tax_make_the_total_within_the_allowance(self) -> None:
        cases = [
            ("11,801.00", "passed"),
            ("11,799.00", "passed"),
            ("11,801.01", "failed"),
            ("11,798.99", "failed"),
        ]
        for total, status in cases:
            with self.subTest(total=total):
                result = check(invoice(total=total))
                self.assertEqual(result.status, status)
                self.assertEqual(result.reasons, () if status == "passed" else ("total_mismatch",))

    def test_with_no_tax_total_the_tax_components_are_summed(self) -> None:
        cgst = ExtractedValue("document", "cgst", "900.00", "900.00", BOX)
        sgst = ExtractedValue("page:1:line:9", "tax_amount", "900.00", "900.00", BOX)
        values = without(invoice(), "tax_amount")
        self.assertEqual(check(values, tax_components=(cgst, sgst)).status, "passed")
        half = check(values, tax_components=(cgst,))
        self.assertEqual(half.reasons, ("total_mismatch",))
        self.assertIn(("document", "cgst"), half.flagged)

    def test_a_stated_tax_total_wins_over_the_components(self) -> None:
        cgst = ExtractedValue("document", "cgst", "900.00", "900.00", BOX)
        self.assertEqual(check(invoice(), tax_components=(cgst,)).status, "passed")

    def test_with_no_tax_at_all_the_total_is_not_checked(self) -> None:
        # The subtotal (10,000) is not the total (11,800), but a tax of 0 would be a guess.
        result = check(without(invoice(), "tax_amount"))
        self.assertEqual((result.status, result.reasons), ("passed", ()))

    def test_a_parsed_number_on_the_value_is_used(self) -> None:
        values = [v for v in invoice() if (v.record_key, v.field) != ("document", "total_amount")]
        values.append(doc("total_amount", "Rupees eleven thousand eight hundred", number=Decimal("11800.00")))
        self.assertEqual(check(values).status, "passed")


class StatusTest(unittest.TestCase):
    def test_passed_when_a_check_ran_and_nothing_failed(self) -> None:
        self.assertEqual(check(invoice()), CheckResult("passed", (), frozenset()))

    def test_not_checked_when_no_arithmetic_check_can_run(self) -> None:
        values = [doc("invoice_number", "INV-17"), doc("total_amount", "11,800.00")]
        self.assertEqual(check(values), CheckResult("not_checked", (), frozenset()))

    def test_any_reason_fails_the_invoice(self) -> None:
        result = check(without(invoice(), "invoice_number"))
        self.assertEqual(result, CheckResult("failed", ("missing_invoice_number",), frozenset()))


class RequiredFieldsTest(unittest.TestCase):
    def test_a_missing_or_blank_invoice_number_or_total(self) -> None:
        self.assertEqual(check(without(invoice(), "invoice_number")).reasons, ("missing_invoice_number",))
        self.assertEqual(check(without(invoice(), "total_amount")).reasons, ("missing_total",))
        blank = replaced(invoice(), "document", "invoice_number", "   ")
        self.assertEqual(check(blank).reasons, ("missing_invoice_number",))


class DateCheckTest(unittest.TestCase):
    def test_the_date_is_between_2000_and_tomorrow(self) -> None:
        self.assertEqual(EARLIEST, date(2000, 1, 1))
        cases = [
            ("31/12/1999", True),
            ("01/01/2000", False),
            ("30/09/2026", False),
            ("01/10/2026", False),  # tomorrow
            ("02/10/2026", True),  # the day after
            ("32/13/2026", True),  # not a date
        ]
        for text, bad in cases:
            with self.subTest(text=text):
                result = check(replaced(invoice(), "document", "invoice_date", text))
                self.assertEqual(result.reasons, ("bad_date",) if bad else ())
                self.assertEqual(("document", "invoice_date") in result.flagged, bad)

    def test_today_comes_from_the_caller(self) -> None:
        values = replaced(invoice(), "document", "invoice_date", "02/10/2026")
        self.assertEqual(check(values).reasons, ("bad_date",))
        self.assertEqual(check(values, today=date(2026, 10, 1)).reasons, ())

    def test_no_date_is_not_a_bad_date(self) -> None:
        self.assertEqual(check(without(invoice(), "invoice_date")).reasons, ())


class GstinCheckTest(unittest.TestCase):
    def test_every_gstin_value_is_checked_and_a_bad_one_is_flagged(self) -> None:
        values = invoice(
            doc("buyer_gstin", "27ABCDE1234F1Z1"),
            ExtractedValue("page:2:line:4", "gstin", "24MNOPQ4321R1Z1", "24MNOPQ4321R1Z1", BOX),
        )
        result = check(values)
        self.assertEqual(result.reasons, ("bad_gstin",))
        self.assertEqual(result.flagged, frozenset({("document", "buyer_gstin")}))
        seller = check(replaced(invoice(), "document", "gstin", "29ABCDE1234F1ZX"))
        self.assertEqual(seller.flagged, frozenset({("document", "gstin")}))

    def test_the_same_gstin_for_seller_and_buyer_is_bad_and_flags_both(self) -> None:
        """Council 8: after spaces and case, a buyer GSTIN equal to the seller's is a misread.
        PROVISIONAL: the state codes are not compared (the place of supply can differ)."""
        result = check(invoice(doc("buyer_gstin", "29abcde 1234f1zw")))
        self.assertEqual(result.reasons, ("bad_gstin",))
        self.assertEqual(result.flagged, frozenset({("document", "gstin"), ("document", "buyer_gstin")}))
        other_state = check(invoice(doc("buyer_gstin", "27ABCDE1234F1Z0")))
        self.assertEqual(other_state.reasons, ())


class AmountRangeTest(unittest.TestCase):
    """Council 6 and security A7: an amount that does not fit numeric(14,2) after rounding to
    0.01 cannot be read, so a check never runs on it (Decimal's 28 digits would lose the 3)."""

    def test_lines_of_10_to_the_30_and_3_against_that_subtotal_do_not_pass(self) -> None:
        huge = "1" + "0" * 30
        values = [
            doc("invoice_number", "INV-17"),
            doc("subtotal", huge),
            doc("total_amount", huge),
            *item(1, "Cotton fabric", huge),
            *item(2, "Silk", "3"),
        ]
        self.assertNotEqual(check(values).status, "passed")

    def test_the_largest_amount_that_fits_is_read_and_one_cent_more_is_not(self) -> None:
        values = [
            doc("invoice_number", "INV-17"),
            doc("subtotal", "999999999999.99"),
            doc("total_amount", "999999999999.99"),
            *item(1, "Cotton fabric", "999999999999.99"),
        ]
        self.assertEqual(check(values).status, "passed")
        over = replaced(values, "line:1", "amount", "999999999999.995")  # rounds to 10^12
        self.assertEqual(check(over).status, "not_checked")


class ConflictsAndTruncatedTest(unittest.TestCase):
    def test_conflicting_values_flag_that_field(self) -> None:
        result = check(invoice(), conflicts=frozenset({"total_amount"}))
        expected = CheckResult("failed", ("conflicting_values",), frozenset({("document", "total_amount")}))
        self.assertEqual(result, expected)

    def test_truncated_is_a_reason_that_flags_nothing(self) -> None:
        self.assertEqual(check(invoice(), truncated=True), CheckResult("failed", ("truncated",), frozenset()))


class ReasonsTest(unittest.TestCase):
    def test_the_k2_order(self) -> None:
        expected = (
            "lines_sum_mismatch",
            "total_mismatch",
            "missing_invoice_number",
            "missing_total",
            "bad_date",
            "bad_gstin",
            "conflicting_values",
            "truncated",
        )
        self.assertEqual(REASONS, expected)

    def test_reasons_come_in_k2_order(self) -> None:
        values = without(invoice(total="99.00"), "invoice_number")
        values = replaced(values, "document", "invoice_date", "01/01/1999")
        values = replaced(values, "document", "gstin", "29ABCDE1234F1ZX")
        values = replaced(values, "line:1", "amount", "1.00")
        result = check(values, conflicts=frozenset({"party_name"}), truncated=True)
        expected = (
            "lines_sum_mismatch",
            "total_mismatch",
            "missing_invoice_number",
            "bad_date",
            "bad_gstin",
            "conflicting_values",
            "truncated",
        )
        self.assertEqual(result.reasons, expected)
        missing = check([doc("invoice_date", "01/01/1999")], truncated=True)
        self.assertEqual(
            missing.reasons, ("missing_invoice_number", "missing_total", "bad_date", "truncated")
        )


class FlaggedValuesTest(unittest.TestCase):
    """ML 6(h): the values each reason puts in review."""

    def test_lines_sum_mismatch_flags_the_summed_line_amounts_and_the_subtotal(self) -> None:
        values = invoice(*item(4, "Brought forward", "5,000.00"), total="11,801.01")
        result = check(replaced(values, "document", "subtotal", "10,001.01"))
        self.assertEqual(result.reasons, ("lines_sum_mismatch",))
        expected = {
            ("line:1", "amount"),
            ("line:2", "amount"),
            ("line:3", "amount"),
            ("document", "subtotal"),
        }
        self.assertEqual(result.flagged, frozenset(expected))

    def test_total_mismatch_flags_the_subtotal_the_tax_and_the_total(self) -> None:
        result = check(invoice(total="12,000.00"))
        expected = {("document", "subtotal"), ("document", "tax_amount"), ("document", "total_amount")}
        self.assertEqual(result.flagged, frozenset(expected))


class ApplyChecksTest(unittest.TestCase):
    def test_flagged_values_go_to_review_even_when_the_readers_agreed(self) -> None:
        agreed = [
            ExtractedValue(
                v.record_key,
                v.field,
                v.value_text,
                v.value_raw,
                v.location,
                label=VERIFIED,
                label_reason="readers_agree",
            )
            for v in invoice(total="12,000.00")
        ]
        labelled = apply_checks(agreed, check(agreed))
        self.assertEqual(len(labelled), len(agreed))
        by_key = {(v.record_key, v.field): (v.label, v.label_reason) for v in labelled}
        self.assertEqual(by_key[("document", "total_amount")], (NEEDS_REVIEW, "check_failed"))
        self.assertEqual(by_key[("document", "subtotal")], (NEEDS_REVIEW, "check_failed"))
        self.assertEqual(by_key[("document", "invoice_number")], (VERIFIED, "readers_agree"))
        self.assertEqual(by_key[("line:1", "amount")], (VERIFIED, "readers_agree"))
        self.assertEqual([v.value_text for v in labelled], [v.value_text for v in agreed])

    def test_a_passed_check_changes_nothing(self) -> None:
        values = invoice()
        self.assertEqual(apply_checks(values, check(values)), values)


if __name__ == "__main__":
    unittest.main()
