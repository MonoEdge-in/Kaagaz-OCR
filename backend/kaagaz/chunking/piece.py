"""The piece: a stretch of a document's text that search finds and answers cite.

Every piece remembers where each part of its text came from (a cell or a line
box), in text order, so a cited piece can always be highlighted on its page
(CLAUDE.md never-do 5). Its label says whether the text can be trusted as
read: ``verified`` only when every part came from a direct read (a cell, a PDF
text layer), ``needs_review`` as soon as one part came from an engine that was
unsure (a scan), so nothing read by guesswork is shown as fact.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from kaagaz.reading.spans import Location, Span, location_to_json

VERIFIED = "verified"
NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True, slots=True)
class Piece:
    customer_id: str
    document_id: str
    text: str
    locations: tuple[Location, ...]  # one per cell or line of ``text``, same order; never empty
    label: str  # VERIFIED or NEEDS_REVIEW

    @property
    def page_from(self) -> int:
        return min(location.page for location in self.locations)

    @property
    def page_to(self) -> int:
        return max(location.page for location in self.locations)

    def regions(self) -> list[dict[str, Any]]:
        """The ``regions`` column: every location as JSON, in text order."""
        return [location_to_json(location) for location in self.locations]


def piece_label(spans: Iterable[Span]) -> str:
    """Verified only if no span carries an engine's confidence (a direct read)."""
    return VERIFIED if all(span.confidence is None for span in spans) else NEEDS_REVIEW
