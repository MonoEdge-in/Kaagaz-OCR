"""Indian number formatting for answer sentences (kaagaz/answering/format.py)."""

from decimal import Decimal

import pytest

from kaagaz.answering.format import indian_number


@pytest.mark.parametrize(
    ("value", "written"),
    [
        (0, "0"),
        (7, "7"),
        (999, "999"),
        (1000, "1,000"),
        (99999, "99,999"),
        (120000, "1,20,000"),
        (1234567, "12,34,567"),
        (10000000, "1,00,00,000"),
        (12345678901, "12,34,56,78,901"),
        (Decimal("1234567.5"), "12,34,567.50"),
        (Decimal("120000.00"), "1,20,000"),
        (Decimal("0.5"), "0.50"),
        (Decimal("1234.005"), "1,234.01"),
        (Decimal("1234.004"), "1,234.00"),
        (-1500, "-1,500"),
        (Decimal("-120000.25"), "-1,20,000.25"),
        (Decimal("-0.001"), "0.00"),
    ],
)
def test_writes_indian_grouping(value: Decimal | int, written: str) -> None:
    assert indian_number(value) == written


@pytest.mark.parametrize("value", [Decimal("Infinity"), Decimal("NaN")])
def test_refuses_numbers_that_are_not_finite(value: Decimal) -> None:
    with pytest.raises(ValueError, match="finite"):
        indian_number(value)
