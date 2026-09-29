"""What a reader returns: pieces of text, each with where it came from.

PROVISIONAL until the reader contract (#3) is written.

Every span carries a location, because a value that cannot be pointed at
cannot be verified (CLAUDE.md never-do 5). The raw reader output is kept next
to the cleaned text (CLAUDE.md "Keep the raw reader output").

Two kinds of location (PROVISIONAL answer to PRD question 5, coordinates):

* ``CellLocation``: a spreadsheet cell. Sheet, row and column start at 1.
* ``BoxLocation``: a rectangle on a page, as fractions of the page's width
  and height (0 to 1), origin at the top left. Fractions mean the same box
  fits the page at any zoom, for PDF points and image pixels alike, so the
  page viewer only multiplies by the size it draws the page at.

As JSON (the ``regions`` and ``region`` columns, and the API), a location is:

* ``{"kind": "cell", "page": sheet, "row": r, "column": c}``
* ``{"kind": "box", "page": p, "box": [x0, y0, x1, y1]}``
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True, order=True)
class CellLocation:
    """A spreadsheet cell. All numbers start at 1. A CSV file is sheet 1.

    The sheet is a number, not a name: a sheet name is typed by the customer
    and could hold an identity number.
    """

    sheet: int
    row: int
    column: int

    @property
    def page(self) -> int:
        """Each sheet is shown as one page."""
        return self.sheet


@dataclass(frozen=True, slots=True, order=True)
class BoxLocation:
    """A box on page ``page`` (from 1), as fractions of the page size, top left origin."""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float

    def is_valid(self) -> bool:
        return self.page >= 1 and 0.0 <= self.x0 < self.x1 <= 1.0 and 0.0 <= self.y0 < self.y1 <= 1.0


Location = CellLocation | BoxLocation


@dataclass(frozen=True, slots=True)
class Span:
    text: str  # cleaned: surrounding whitespace removed
    raw: str  # exactly what the file held
    location: Location
    # None for a direct read (a cell, a PDF text layer). A number from 0 to 1 for a
    # reading an engine was unsure about (a scan), so it can be labelled needs review.
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class PageInfo:
    """One page's size in its own unit: PDF points, image pixels, or cells for a sheet
    (width = columns used, height = rows used)."""

    page: int
    width: float
    height: float
    unit: str  # "pt", "px" or "cells"


class ReadError(Exception):
    """The document cannot be read. ``code`` is short and safe to log; the
    message never contains document content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TooManyPages(ReadError):
    """``too_many_pages``, with the real count, so the file list can say
    "This PDF has 180 pages" (the pipeline stores it as the page count)."""

    def __init__(self, pages: int) -> None:
        super().__init__("too_many_pages")
        self.pages = pages


def location_to_json(location: Location) -> dict[str, Any]:
    if isinstance(location, CellLocation):
        return {"kind": "cell", "page": location.sheet, "row": location.row, "column": location.column}
    box = [round(value, 6) for value in (location.x0, location.y0, location.x1, location.y1)]
    return {"kind": "box", "page": location.page, "box": box}


def location_from_json(data: dict[str, Any]) -> Location:
    if data["kind"] == "cell":
        return CellLocation(int(data["page"]), int(data["row"]), int(data["column"]))
    x0, y0, x1, y1 = (float(value) for value in data["box"])
    return BoxLocation(int(data["page"]), x0, y0, x1, y1)
