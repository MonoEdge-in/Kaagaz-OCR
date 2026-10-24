"""One extracted value, as the ``extracted_values`` table keeps it.

A value always has a location (a cell or a line box), so it can be pointed at
on its page (CLAUDE.md never-do 5): there is no way to build one without.
``value_raw`` is what the reader gave, ``value_text`` the value shown,
which a learned correction may change; both are kept, so a silent change can
be seen later.

A new value starts as needs review (``single_reader``) until
``kaagaz.extraction.labels`` says otherwise: nothing is verified by default.
A value of the document card (``CARD_RECORD``, PRD 2 D3) is never verified,
whatever path it takes (Lead's resolution 1, council fix 4).

The records a run saves with its values live here too (the document's facts,
its invoice row and its link identifiers), so the extraction modules and
kaagaz.pipeline share them without importing each other.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from kaagaz.chunking.piece import NEEDS_REVIEW, VERIFIED
from kaagaz.reading.spans import Location

__all__ = [
    "CARD_RECORD",
    "LINK_KINDS",
    "NEEDS_REVIEW",
    "VERIFIED",
    "DocumentFacts",
    "ExtractedValue",
    "InvoiceLineRow",
    "InvoiceRow",
    "LinkIdentifier",
    "QuoteMatch",
]

CARD_RECORD = "card"  # the record key of the document card's values (PRD 2 D3)
# The identifiers two documents are linked by (D5).
LINK_KINDS = frozenset(
    {"invoice_number", "po_number", "challan_number", "survey_number"}
    | {"khata_number", "account_number", "reference_number"}
)


@dataclass(frozen=True, slots=True)
class QuoteMatch:
    """How the model's quote for a value was found in the reader's text (PRD 2 D2), so
    the labeller can tell a value that is one whole span from one that is not."""

    spans: int  # the reader spans the match covers
    pages: int  # the pages those spans are on
    whole: bool  # the match is exactly one span's whole text
    loose: bool = False  # found only by the loose pass (ML 9(c))
    ambiguous: bool = False  # a later spot fitted the quote too (ML 9(e))


@dataclass(frozen=True, slots=True)
class ExtractedValue:
    record_key: str  # which values belong together: "document", "sheet:1:row:4", "page:2:line:7"
    field: str  # lower snake_case, see parsing.normalise_field
    value_text: str
    value_raw: str
    location: Location
    confidence: float | None = None  # the reading engine's, None for a direct read
    second_reading: str | None = None
    label: str = NEEDS_REVIEW
    label_reason: str = "single_reader"
    value_number: Decimal | None = None
    value_date: date | None = None
    label_location: Location | None = None  # the value's label, when it sits elsewhere (D3)
    origin: str | None = None  # how its span was read: "cell", "text_layer" or "ocr" (spans.ORIGINS)
    quote: QuoteMatch | None = None  # set when a model quote located the value; never stored
    # Ensemble (F27): the readers' vote on the value, plain JSON (labels.vote); None: one reader.
    votes: Mapping[str, Any] | None = None
    label_text: str | None = None  # a map value's label as the reader read it (land.py, 0054)


@dataclass(frozen=True, slots=True)
class DocumentFacts:
    """The documents columns a run writes (D3, L2). Every save replaces all of them,
    so a run without a model clears an older run's card."""

    doc_type: str | None = None
    languages: tuple[str, ...] = ()
    summary: str | None = None
    document_date: date | None = None
    card: Mapping[str, Any] | None = None  # plain JSON


@dataclass(frozen=True, slots=True)
class InvoiceLineRow:
    line_no: int
    description: str | None
    hsn: str | None
    quantity: Decimal | None
    unit: str | None
    rate: Decimal | None
    amount: Decimal | None
    label: str  # "verified" or "needs_review"


@dataclass(frozen=True, slots=True)
class InvoiceRow:
    invoice_number: str | None
    invoice_date: date | None
    seller_name: str | None
    seller_gstin: str | None
    buyer_name: str | None
    buyer_gstin: str | None
    subtotal: Decimal | None
    tax_total: Decimal | None
    total: Decimal | None
    label: str
    check_status: str  # "passed", "failed" or "not_checked"
    check_reasons: tuple[str, ...]
    currency: str = "INR"
    lines: tuple[InvoiceLineRow, ...] = ()


@dataclass(frozen=True, slots=True)
class LinkIdentifier:
    kind: str  # one of LINK_KINDS
    value: str  # whitespace removed, lower case
