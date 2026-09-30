"""One extracted value, as the ``extracted_values`` table keeps it.

A value always has a location (a cell or a line box), so it can be pointed at
on its page (CLAUDE.md never-do 5): there is no way to build one without.
``value_raw`` is what the reader gave, ``value_text`` the value shown,
which a learned correction may change; both are kept, so a silent change can
be seen later.

A new value starts as needs review (``single_reader``) until
``kaagaz.extraction.labels`` says otherwise: nothing is verified by default.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from kaagaz.chunking.piece import NEEDS_REVIEW, VERIFIED
from kaagaz.reading.spans import Location

__all__ = ["NEEDS_REVIEW", "VERIFIED", "ExtractedValue"]


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
