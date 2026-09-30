"""Values from text documents: a fixed list of labels, each followed by its value.

For PDFs and scans (line spans), a line that starts with a known label, for
example "Invoice No: INV-17" or "Grand Total Rs. 1,180.00", gives one value:
the text after the label and its separator (``:``, a dash followed by a
space, or spaces). The region is that line's box. The list is fixed below;
nothing else is recognised, and no field is ever guessed from layout.

* Longer labels are tried first, so "Total Tax" is never read as "Total".
  Once a label fits the start of a line, no shorter one is tried.
* A line can hold several "Label:" pairs ("Bill No: 17   Date: 12/08/2026"):
  it is split before each later label that is followed by a colon, so one
  value never swallows the next.
* An amount field (total_amount, subtotal, tax_amount) is kept only if its
  text reads as an amount, and invoice_date only if it reads as a date
  (kaagaz.extraction.parsing). "Total Items 5" is not a total amount.
  PROVISIONAL: this check is ours, beyond the written rule, so a label on a
  line of prose does not become a wrong value.
* Several matches for one field: the first by (page, line) gets the record
  key ``document``; each other one becomes its own record
  ``page:{p}:line:{n}`` (n counts lines on that page from 1). Which one is
  right is never guessed.

Time O(n * L) for n characters of text and L labels (a fixed list of 26);
memory O(lines).
"""

import re
from collections.abc import Iterable
from itertools import pairwise

from kaagaz.extraction.parsing import parse_amount, parse_date
from kaagaz.extraction.values import ExtractedValue
from kaagaz.reading.spans import BoxLocation, Span

LABELS: dict[str, tuple[str, ...]] = {
    "invoice_number": ("Invoice No", "Invoice Number", "Bill No"),
    "invoice_date": ("Invoice Date", "Bill Date", "Date"),
    "total_amount": ("Grand Total", "Total Amount", "Amount Payable", "Net Amount", "Total"),
    "subtotal": ("Sub Total",),
    "tax_amount": ("CGST", "SGST", "IGST", "Total Tax"),
    "gstin": ("GSTIN",),
    "party_name": ("Seller", "Supplier", "Vendor", "From", "Billed By"),
    "buyer_name": ("Bill To", "Buyer", "Customer", "Billed To"),
}
AMOUNT_FIELDS = frozenset({"total_amount", "subtotal", "tax_amount"})
DATE_FIELDS = frozenset({"invoice_date"})

DOCUMENT_RECORD = "document"


def _words(label: str) -> str:
    return r"\s+".join(re.escape(word) for word in label.split())


_BY_LENGTH = sorted(
    ((label, field) for field, labels in LABELS.items() for label in labels),
    key=lambda pair: -len(pair[0]),
)
_STARTS = [
    (re.compile(rf"\s*{_words(label)}\.?(?![a-z0-9])", re.IGNORECASE), field) for label, field in _BY_LENGTH
]
_SEPARATOR = re.compile(r"\s*:\s*|\s*[-\u2013]\s+|\s+")
_LABEL_THEN_COLON = re.compile(
    rf"(?:^|(?<=\s))(?:{'|'.join(_words(label) for label, _ in _BY_LENGTH)})\.?(?=\s*:)",
    re.IGNORECASE,
)


def values_from_lines(spans: Iterable[Span]) -> list[ExtractedValue]:
    """Labelled values of the box spans (one per line); spans of other kinds are ignored."""
    lines = [(span, span.location) for span in spans if isinstance(span.location, BoxLocation)]
    lines.sort(key=lambda pair: pair[1].page)  # stable: reading order kept within a page
    values: list[ExtractedValue] = []
    seen: set[str] = set()
    line_no, page = 0, 0
    for span, box in lines:
        line_no = line_no + 1 if box.page == page else 1
        page = box.page
        for field, value in _pairs(span.text):
            if not _fits(field, value):
                continue
            record_key = DOCUMENT_RECORD if field not in seen else f"page:{page}:line:{line_no}"
            seen.add(field)
            values.append(
                ExtractedValue(
                    record_key=record_key,
                    field=field,
                    value_text=value,
                    value_raw=value,
                    location=box,
                    confidence=span.confidence,
                    origin=span.origin,
                )
            )
    return values


def value_after_label(field: str, text: str) -> str | None:
    """The text after ``field``'s label in ``text``, unchecked, or None if the label
    is not there. Used to compare a second reading of the same line."""
    for found, value in _pairs(text):
        if found == field:
            return value
    return None


def _pairs(line: str) -> list[tuple[str, str]]:
    """Every (field, value) the line's segments start with."""
    starts = [match.start() for match in _LABEL_THEN_COLON.finditer(line) if match.start() > 0]
    pairs = []
    for begin, end in pairwise([0, *starts, len(line)]):
        pair = _pair(line[begin:end])
        if pair is not None:
            pairs.append(pair)
    return pairs


def _pair(segment: str) -> tuple[str, str] | None:
    for start, field in _STARTS:
        head = start.match(segment)
        if head is None:
            continue
        separator = _SEPARATOR.match(segment, head.end())
        value = "" if separator is None else segment[separator.end() :].strip()
        return (field, value) if value else None  # the longest fitting label decides
    return None


def _fits(field: str, value: str) -> bool:
    if field in AMOUNT_FIELDS:
        return parse_amount(value) is not None
    if field in DATE_FIELDS:
        return parse_date(value) is not None
    return True
