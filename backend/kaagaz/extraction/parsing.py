"""Read amounts, dates and field names the way Indian business documents write them.

Each parser returns ``None`` when the text is not clearly one thing, and never
guesses (CLAUDE.md never-do 6): "12 pcs", "1,2,3" or "12/08/26" give None,
not a number or a date. A None only means the value has no number or date
attached; its text is still kept.

Amounts (``parse_amount``):

* digits grouped the Indian way (1,23,456.78), the Western way (1,234,567) or
  not at all (1234.5); any other comma pattern is refused, because a comma
  could be a decimal mark in some files.
* an optional currency in front: ₹, Rs, Rs. or INR, any case.
* an optional end mark after, never a digit (#524): "/-", "/ -", "/=", "-/-", a
  lone "/", or a danda (। ॥); then an optional "Dr" (debit).
* negative: a leading minus ("-1,234", "Rs. -1,234") or brackets ("(1,234)").
* "Cr" (credit) is refused. PROVISIONAL: whether a credit counts as negative
  depends on the account, which is a guess, so it is left for Vrushit.
* ASCII digits and the digits of the Indic scripts (Devanagari ०१२३४५६७८९, PRD 2
  L4, and the others, ``latin_digits``), mixed or not (#524: ९४0000/- is 940000);
  no other digits. The text itself is never changed, so ``value_text`` keeps what
  is printed.

Why an amount needs a person's eyes (``amount_problem``, #524), never a guess at
the number: its digits are of two scripts (``MIXED_DIGITS``); its commas fit
neither grouping, "1,15,0000" or "11,500,00" (``AMOUNT_COMMAS``); or a mark sits
inside its digits ("1O,000"), or it ends in "1-" or "/1", where "/-" may have been
read as a 1 ("115000001-": 11500000/- or 115000001?), ``AMOUNT_MARK``. Text that is
not one amount ("12 pcs", "9% 900.00": a space inside) has no problem: it is no amount.

Dates (``parse_date``), day before month as India writes them:

* 12/08/2026, 12-08-2026, 12.08.2026 (one separator throughout) and 2026-08-12.
* 12 Aug 2026, 12-Aug-26, 12 August, 2026: English month names and the usual
  short forms. PROVISIONAL: a two digit year after a month name is 20yy.
* August 2026, Aug-2026: the first of that month (a four digit year only, so
  "Aug-26" is refused: it could be the 26th of August).
* A numeric date with a two digit year (12/08/26) is refused: its century
  would be a guess.
* Devanagari digits count here too (१२/०८/२०२६).

Every function is O(n) in the text length, and the text is capped first.
"""

import re
import unicodedata
from datetime import date
from decimal import Decimal

from kaagaz.chunking.script import fold_digits, latin_digits

_MAX_LENGTH = 64  # longer text is not a single amount or date
_ASCII_DIGIT = re.compile("[0-9]")
_DEVANAGARI_DIGIT = re.compile("[\u0966-\u096f]")

MIXED_DIGITS, AMOUNT_COMMAS, AMOUNT_MARK = "mixed_digits", "amount_commas", "amount_mark"
# #524: what may end an amount, never a digit: "-/-", "/-", "/ -", "/=", a lone "/", a danda.
_END_MARK = r"(?:-/-|/\s*[-=]|/|[\u0964\u0965])"
# U+20B9 is the rupee sign, U+2212 the minus sign (escaped for the linter).
_AMOUNT = re.compile(
    r"(?P<sign>[-\u2212])?\s*"
    r"(?:(?:\u20b9|rs\.?|inr)\s*)?"
    r"(?P<sign2>[-\u2212])?\s*"
    r"(?P<whole>[0-9][0-9,]*)(?P<fraction>\.[0-9]+)?"
    rf"\s*{_END_MARK}?"
    r"\s*(?P<side>dr\.?|cr\.?)?",
    re.IGNORECASE,
)
# ``amount_problem``: _AMOUNT's frame around a ``core`` from the first digit to the last, with no
# space inside ("9% 900.00" and "2 x 500" are no amount).
_FRAME = re.compile(
    r"[-\u2212]?\s*(?:(?:\u20b9|rs\.?|inr)\s*)?[-\u2212]?\s*"
    r"(?P<core>[0-9](?:\S*[0-9])?)"
    rf"\s*{_END_MARK}?\s*(?:dr\.?|cr\.?)?",
    re.IGNORECASE,
)
# An end mark read with a 1 in it: "115000001-" (from "11500000/-"), "11500000/1".
_ONE_FOR_MARK = re.compile(r"[0-9](?:1\s*[-=]|\s*/\s*1\s*[-=]?)$")
_GROUPED = re.compile(r"(?P<whole>[0-9][0-9,]*)(?:\.[0-9]+)?")
_MAX_STRAYS = 2  # more marks than this inside the digits, and the text is not one amount
_PLAIN = re.compile(r"0|[1-9][0-9]*")
_INDIAN = re.compile(r"[1-9][0-9]?(?:,[0-9]{2})*,[0-9]{3}")
_WESTERN = re.compile(r"[1-9][0-9]{0,2}(?:,[0-9]{3})+")

_NUMERIC_DATE = re.compile(r"(?P<d>[0-9]{1,2})(?P<sep>[/.\-])(?P<m>[0-9]{1,2})(?P=sep)(?P<y>[0-9]{4})")
_ISO_DATE = re.compile(r"(?P<y>[0-9]{4})-(?P<m>[0-9]{1,2})-(?P<d>[0-9]{1,2})")
_NAMED_DATE = re.compile(
    r"(?P<d>[0-9]{1,2})[ \-/.]+(?P<mon>[a-z]+)[ \-/.,]+(?P<y>[0-9]{4}|[0-9]{2})", re.IGNORECASE
)
_MONTH_YEAR = re.compile(r"(?P<mon>[a-z]+)[ \-/.,]+(?P<y>[0-9]{4})", re.IGNORECASE)

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
_FIRST_YEAR, _LAST_YEAR = 1900, 2099

_FIELD = re.compile(r"[a-z][a-z0-9_]{0,62}")
_NOT_FIELD_CHARS = re.compile(r"[^a-z0-9]+")


def parse_amount(text: str) -> Decimal | None:
    """The amount ``text`` states, or None if it is not clearly one amount."""
    text = latin_digits(text).strip()
    if not text or len(text) > _MAX_LENGTH:
        return None
    bracketed = text.startswith("(") and text.endswith(")")
    if bracketed:
        text = text[1:-1].strip()
    match = _AMOUNT.fullmatch(text)
    if match is None:
        return None
    signs = (match["sign"] is not None) + (match["sign2"] is not None) + bracketed
    side = (match["side"] or "").lower()
    if signs > 1 or side.startswith("cr") or (bracketed and side):
        return None
    whole = match["whole"]
    if not _grouped(whole):
        return None
    amount = Decimal(whole.replace(",", "") + (match["fraction"] or ""))
    return -amount if signs else amount


def amount_problem(text: str) -> str | None:
    """Why ``text`` needs a person's eyes as an amount (the module docstring, #524): MIXED_DIGITS,
    AMOUNT_COMMAS or AMOUNT_MARK. None for an amount that reads cleanly, and for text that is not
    one amount at all. O(n) in the text, capped first."""
    if len(text) > _MAX_LENGTH:
        return None
    problem = None
    if parse_amount(text) is None:
        problem = _shape_problem(latin_digits(text).strip())
        if problem is None:
            return None  # no amount at all
    return MIXED_DIGITS if _digit_scripts(text) > 1 else problem


def _shape_problem(text: str) -> str | None:
    """AMOUNT_MARK or AMOUNT_COMMAS for text (ASCII digits) ``parse_amount`` refused, else None."""
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    one = _ONE_FOR_MARK.search(text)
    if one is not None and _FRAME.fullmatch(text[: one.start() + 1]):
        return AMOUNT_MARK
    frame = _FRAME.fullmatch(text)
    if frame is None:
        return None
    core = frame["core"]
    strays = sum(ch not in "0123456789,." for ch in core)
    if 0 < strays <= _MAX_STRAYS:
        return AMOUNT_MARK
    if strays or ("," not in core and core.count(".") < 2):
        return None  # too many marks to be one amount, or refused for another reason (Cr, 007)
    grouped = _GROUPED.fullmatch(core)
    return None if grouped is not None and _grouped(grouped["whole"]) else AMOUNT_COMMAS


def _grouped(whole: str) -> bool:
    """Digits grouped the Indian or the Western way, or not at all."""
    return bool(_PLAIN.fullmatch(whole) or _INDIAN.fullmatch(whole) or _WESTERN.fullmatch(whole))


def _digit_scripts(text: str) -> int:
    """How many scripts the decimal digits of ``text`` are in. A script's ten digits are
    consecutive code points, so a digit's script is its code point less its value."""
    return len({ord(ch) - unicodedata.digit(ch) for ch in text if ch.isdecimal()})


def parse_date(text: str) -> date | None:
    """The calendar date ``text`` states (day first), or None if it is not clearly one."""
    if _mixed_digits(text):
        return None
    text = fold_digits(text).strip()
    if not text or len(text) > _MAX_LENGTH:
        return None
    for pattern in (_NUMERIC_DATE, _ISO_DATE):
        match = pattern.fullmatch(text)
        if match is not None:
            return _date(int(match["y"]), int(match["m"]), int(match["d"]))
    match = _NAMED_DATE.fullmatch(text)
    if match is not None:
        month = _MONTHS.get(match["mon"].lower())
        year = int(match["y"])
        year = 2000 + year if len(match["y"]) == 2 else year
        return None if month is None else _date(year, month, int(match["d"]))
    match = _MONTH_YEAR.fullmatch(text)
    if match is not None:
        month = _MONTHS.get(match["mon"].lower())
        return None if month is None else _date(int(match["y"]), month, 1)
    return None


def _mixed_digits(text: str) -> bool:
    """ASCII and Devanagari digits in one text: which was meant is never guessed (council)."""
    return _ASCII_DIGIT.search(text) is not None and _DEVANAGARI_DIGIT.search(text) is not None


def _date(year: int, month: int, day: int) -> date | None:
    if not _FIRST_YEAR <= year <= _LAST_YEAR:
        return None
    try:
        return date(year, month, day)
    except ValueError:  # 31/02, month 13: not a date
        return None


def normalise_field(label: str) -> str | None:
    """A header or label as a field name: lower snake_case ASCII, for example
    "Invoice No." -> "invoice_no". None when nothing usable is left, when it
    starts with a digit, or when it is longer than 63 characters (never cut)."""
    folded = unicodedata.normalize("NFKD", label.casefold()).encode("ascii", "ignore").decode("ascii")
    field = _NOT_FIELD_CHARS.sub("_", folded).strip("_")
    return field if _FIELD.fullmatch(field) else None
