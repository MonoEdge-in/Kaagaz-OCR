"""The invoice checks (PRD 2 K1 to K3): pure functions over extracted values, no database.

The values are one invoice's final values with today's field names (E4): the
``document`` record (invoice_number, invoice_date, gstin, buyer_gstin,
subtotal, tax_amount, total_amount...) and one ``line:{n}`` record per line
(description, amount...). Invoices read with the model off use the
``labelled.py`` values, which have the same field names.

K1, each amount check with an allowance of plus or minus 1.00 (``ALLOWANCE``):

* **Lines.** The line amounts sum to the subtotal. A line whose description
  matches ``NOT_AN_ITEM`` (brought or carried forward, b/f, c/d...; ML
  amendment 6(c)) is not an item; "Sub/Fabric" is. With no subtotal and no
  tax, the lines are compared with the total instead (ML 6(g)). Not run with no
  item lines, or when a line amount is not a number.
* **Total.** Subtotal + tax = total. The tax is the stated tax total, else the
  sum of the tax components (CGST, SGST, UTGST, IGST, cess; ML 6(a)), used for
  this check only. Not run when the subtotal, the tax or the total is missing:
  a tax of 0 would be a guess.
* invoice_number and total_amount are present (not blank).
* invoice_date, when there is one, is a date from 2000-01-01 to tomorrow.
  ``today`` comes from the caller's clock (the worker's, in the configured
  time zone; ML 6(j)).
* Every GSTIN value (fields gstin and buyer_gstin, in any record) passes
  ``gstin_valid``: spaces removed and upper-cased (ASCII only), a full match of
  ``GSTIN``, and the published mod-36 check character.

An amount is the value's ``value_number`` when set, else ``parse_amount`` of
its text; a date is its ``value_date``, else ``parse_date`` of its text.

K2, the status (ML 6(g)): ``failed`` when there is any reason; otherwise
``passed`` when at least one of the two amount checks ran; otherwise
``not_checked``. PROVISIONAL: ML 6(g) defines passed and not_checked by the
amount checks; here a missing field, a bad date or GSTIN, a conflict or a
truncated read also fails the invoice, because each is a K1 check or a K2
reason. The reasons come in ``REASONS`` order.

K3, the values flagged per reason (ML 6(h)), as (record_key, field):
lines_sum_mismatch, the summed line amounts and the subtotal (or the total
they were compared with); total_mismatch, the subtotal, the tax used (the tax
total, or each component) and the total; bad_date, the date; bad_gstin, that
value; conflicting_values, that document field. The missing fields and
truncated flag nothing. ``apply_checks`` puts the flagged values in review.

Time and memory O(v) for v values; one GSTIN check is O(15).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from kaagaz.extraction.labelled import DOCUMENT_RECORD
from kaagaz.extraction.parsing import parse_amount, parse_date
from kaagaz.extraction.values import NEEDS_REVIEW, ExtractedValue

ALLOWANCE = Decimal("1.00")
EARLIEST = date(2000, 1, 1)
GSTIN = re.compile(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]")
REASONS = (
    "lines_sum_mismatch",
    "total_mismatch",
    "missing_invoice_number",
    "missing_total",
    "bad_date",
    "bad_gstin",
    "conflicting_values",
    "truncated",
)  # K2 order
# K1, widened by ML 6(c): the words, or b/f, c/f, b/d, c/d (optionally "wd") standing alone.
NOT_AN_ITEM = re.compile(
    r"\b(?:brought|carried)\s+(?:forward|down|over)\b|(?<![a-z0-9])[bc]\s*/\s*[fd](?:wd)?(?![a-z0-9])",
    re.IGNORECASE,
)
GSTIN_FIELDS = frozenset({"gstin", "buyer_gstin"})

PASSED, FAILED, NOT_CHECKED = "passed", "failed", "not_checked"
_LINE_PREFIX = "line:"
_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: str  # PASSED, FAILED or NOT_CHECKED
    reasons: tuple[str, ...]  # in REASONS order
    flagged: frozenset[tuple[str, str]]  # (record_key, field) of the values involved (K3)


def gstin_check_char(first14: str) -> str:
    """The mod-36 check character for a GSTIN's first 14 characters. Each character's value
    (0 to 9, then A=10 to Z=35) is doubled at the odd positions (counting from 0); each
    product p adds p // 36 + p % 36; the check character's value brings the sum to a multiple
    of 36. Raises ValueError unless ``first14`` is 14 digits or capital letters."""
    if len(first14) != 14 or any(ch not in _GSTIN_CHARS for ch in first14):
        raise ValueError("a GSTIN body is 14 digits or capital letters")
    total = 0
    for i, ch in enumerate(first14):
        product = _GSTIN_CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _GSTIN_CHARS[(36 - total % 36) % 36]


def gstin_valid(text: str) -> bool:
    """K1: spaces removed and upper-cased, the whole text matches GSTIN, and the check character is right."""
    compact = "".join(text.split())
    if not compact.isascii():  # upper() would turn look-alikes such as the dotless i into letters
        return False
    compact = compact.upper()
    return GSTIN.fullmatch(compact) is not None and gstin_check_char(compact[:14]) == compact[14]


def check_invoice(
    values: Sequence[ExtractedValue],
    *,
    today: date,
    conflicts: frozenset[str],
    truncated: bool,
    tax_components: Sequence[ExtractedValue] = (),
) -> CheckResult:
    """K1 and K2 for one invoice (the module docstring). ``conflicts`` holds the document
    fields that had conflicting values; ``tax_components`` the invoice's tax lines (ML 6(a))."""
    document: dict[str, ExtractedValue] = {}
    for value in values:
        if value.record_key == DOCUMENT_RECORD:
            document.setdefault(value.field, value)
    reasons: set[str] = set()
    flagged: set[tuple[str, str]] = set()
    ran = 0

    subtotal = _amount(document.get("subtotal"))
    tax_total = _amount(document.get("tax_amount"))
    total = _amount(document.get("total_amount"))
    components = [(value, amount) for value in tax_components if (amount := _amount(value)) is not None]
    no_tax = tax_total is None and not components

    lines = _item_lines(values)
    amounts = [amount for _, amount in lines if amount is not None]
    target_field = "subtotal" if subtotal is not None else "total_amount"
    target = subtotal if subtotal is not None else (total if no_tax else None)
    if lines and len(amounts) == len(lines) and target is not None:
        ran += 1
        if abs(sum(amounts, Decimal(0)) - target) > ALLOWANCE:
            reasons.add("lines_sum_mismatch")
            flagged |= {(key, "amount") for key, _ in lines} | {(DOCUMENT_RECORD, target_field)}

    if subtotal is not None and total is not None and not no_tax:
        ran += 1
        tax = tax_total if tax_total is not None else sum((amount for _, amount in components), Decimal(0))
        if abs(subtotal + tax - total) > ALLOWANCE:
            reasons.add("total_mismatch")
            flagged |= {(DOCUMENT_RECORD, "subtotal"), (DOCUMENT_RECORD, "total_amount")}
            if tax_total is not None:
                flagged.add((DOCUMENT_RECORD, "tax_amount"))
            else:
                flagged |= {(value.record_key, value.field) for value, _ in components}

    if not _present(document.get("invoice_number")):
        reasons.add("missing_invoice_number")
    if not _present(document.get("total_amount")):
        reasons.add("missing_total")
    invoice_date = document.get("invoice_date")
    if invoice_date is not None and _present(invoice_date) and not _in_range(invoice_date, today):
        reasons.add("bad_date")
        flagged.add((DOCUMENT_RECORD, "invoice_date"))
    for value in values:
        if value.field in GSTIN_FIELDS and not gstin_valid(value.value_text):
            reasons.add("bad_gstin")
            flagged.add((value.record_key, value.field))
    if conflicts:
        reasons.add("conflicting_values")
        flagged |= {(DOCUMENT_RECORD, field) for field in conflicts}
    if truncated:
        reasons.add("truncated")

    ordered = tuple(reason for reason in REASONS if reason in reasons)
    status = FAILED if ordered else (PASSED if ran else NOT_CHECKED)
    return CheckResult(status, ordered, frozenset(flagged))


def apply_checks(values: Sequence[ExtractedValue], check: CheckResult) -> list[ExtractedValue]:
    """K3: flagged values become needs review, reason check_failed, even when the readers agreed."""
    return [
        replace(value, label=NEEDS_REVIEW, label_reason="check_failed")
        if (value.record_key, value.field) in check.flagged
        else value
        for value in values
    ]


def _item_lines(values: Sequence[ExtractedValue]) -> list[tuple[str, Decimal | None]]:
    """(record_key, amount) of every line that is an item, in the order the lines first appear."""
    descriptions: dict[str, str] = {}
    amounts: dict[str, ExtractedValue] = {}
    keys: dict[str, None] = {}
    for value in values:
        if value.record_key.startswith(_LINE_PREFIX):
            keys.setdefault(value.record_key)
            if value.field == "description":
                descriptions.setdefault(value.record_key, value.value_text)
            elif value.field == "amount":
                amounts.setdefault(value.record_key, value)
    return [
        (key, _amount(amounts.get(key)))
        for key in keys
        if NOT_AN_ITEM.search(descriptions.get(key, "")) is None
    ]


def _amount(value: ExtractedValue | None) -> Decimal | None:
    if value is None:
        return None
    return value.value_number if value.value_number is not None else parse_amount(value.value_text)


def _present(value: ExtractedValue | None) -> bool:
    return value is not None and bool(value.value_text.strip())


def _in_range(value: ExtractedValue, today: date) -> bool:
    day = value.value_date if value.value_date is not None else parse_date(value.value_text)
    return day is not None and EARLIEST <= day <= today + timedelta(days=1)
