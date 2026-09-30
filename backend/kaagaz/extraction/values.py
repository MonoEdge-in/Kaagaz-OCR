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
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from kaagaz.chunking.piece import NEEDS_REVIEW, VERIFIED
from kaagaz.reading.spans import Location

__all__ = ["CARD_RECORD", "NEEDS_REVIEW", "VERIFIED", "ExtractedValue", "QuoteMatch"]

CARD_RECORD = "card"  # the record key of the document card's values (PRD 2 D3)


@dataclass(frozen=True, slots=True)
class QuoteMatch:
    """How the model's quote for a value was found in the reader's text (PRD 2 D2), so
    the labeller can tell a value that is one whole span from one that is not."""

    spans: int  # the reader spans the match covers
    pages: int  # the pages those spans are on
    whole: bool  # the match is exactly one span's whole text


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
