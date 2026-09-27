"""Numbers written the Indian way, for answer sentences.

Customers read amounts in lakhs and crores: 120000 is "1,20,000" (the last
three digits, then groups of two), never "120,000". Whole numbers get no
decimals; anything else gets exactly two, rounded half up like a bill.

Time is O(d) in the number of digits.
"""

from decimal import ROUND_HALF_UP, Decimal

_CENTS = Decimal("0.01")


def indian_number(value: Decimal | int) -> str:
    """120000 -> "1,20,000"; Decimal("1234567.5") -> "12,34,567.50"; -1500 -> "-1,500"."""
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("only finite numbers can be written")
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number == number.to_integral_value():
        whole, fraction = str(int(number)), ""
    else:
        cents = number.quantize(_CENTS, rounding=ROUND_HALF_UP)
        whole, fraction = f"{cents:f}".split(".")
        fraction = "." + fraction
        if cents == 0:
            sign = ""  # -0.001 is written "0.00", never "-0.00"
    return sign + _group(whole) + fraction


def _group(digits: str) -> str:
    """Commas for a run of digits: the last three, then pairs."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    pairs: list[str] = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head, *pairs, tail])
