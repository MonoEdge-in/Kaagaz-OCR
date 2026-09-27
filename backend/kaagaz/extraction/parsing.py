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
* an optional "/-" after ("Rs. 1,234/-") and an optional "Dr" (debit).
* negative: a leading minus ("-1,234", "Rs. -1,234") or brackets ("(1,234)").
* "Cr" (credit) is refused. PROVISIONAL: whether a credit counts as negative
  depends on the account, which is a guess, so it is left for Vrushit.
* Only ASCII digits count.

Dates (``parse_date``), day before month as India writes them:

* 12/08/2026, 12-08-2026, 12.08.2026 (one separator throughout) and 2026-08-12.
* 12 Aug 2026, 12-Aug-26, 12 August, 2026: English month names and the usual
  short forms. PROVISIONAL: a two digit year after a month name is 20yy.
* August 2026, Aug-2026: the first of that month (a four digit year only, so
  "Aug-26" is refused: it could be the 26th of August).
* A numeric date with a two digit year (12/08/26) is refused: its century
  would be a guess.

Every function is O(n) in the text length, and the text is capped first.
"""

import re
import unicodedata
from datetime import date
from decimal import Decimal

_MAX_LENGTH = 64  # longer text is not a single amount or date

# U+20B9 is the rupee sign, U+2212 the minus sign (escaped for the linter).
_AMOUNT = re.compile(
    r"(?P<sign>[-\u2212])?\s*"
    r"(?:(?:\u20b9|rs\.?|inr)\s*)?"
    r"(?P<sign2>[-\u2212])?\s*"
    r"(?P<whole>[0-9][0-9,]*)(?P<fraction>\.[0-9]+)?"
    r"\s*(?:/-)?"
    r"\s*(?P<side>dr\.?|cr\.?)?",
    re.IGNORECASE,
)
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
    text = text.strip()
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
    if not (_PLAIN.fullmatch(whole) or _INDIAN.fullmatch(whole) or _WESTERN.fullmatch(whole)):
        return None
    amount = Decimal(whole.replace(",", "") + (match["fraction"] or ""))
    return -amount if signs else amount


def parse_date(text: str) -> date | None:
    """The calendar date ``text`` states (day first), or None if it is not clearly one."""
    text = text.strip()
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
