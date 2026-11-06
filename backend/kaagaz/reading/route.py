"""The route each page was read by (PRD 2 R1), and why (the council's ML 12).

PRD 2 PR 3. A route is stored in ``pages.route``:

* ``cells``: a spreadsheet page, read cell by cell;
* ``text_layer``: a PDF page read from the text it carries (reason ``pdfium_text``: a
  Word or PowerPoint file's PDF page read from PDFium's reading of it, #522,
  ``kaagaz/reading/pdf_reader.py``);
* ``ocr``: a page read by the OCR engine (or sent to it, with nothing found);
* ``blank``: no text, and a blank picture (R3).

Each reader says how it read each page, through ``on_page``
(``kaagaz.reading.socket``); the pipeline stores that route
(``pipeline._with_routes``). The reason code says why: ``ok`` for a page that
passed, the R2 check that failed (``kaagaz/reading/text_quality.py``) for a
page sent to OCR, and ``blank`` for a blank one. ``cli measure`` prints it; it
is not stored.
"""

from dataclasses import dataclass
from typing import Literal

Route = Literal["cells", "text_layer", "ocr", "blank"]
Reason = Literal[
    "few_chars",
    "bad_chars",
    "scan_image",
    "hidden_text",
    "legacy_font",
    "map_errors",
    "malformed_devanagari",
    "blank",
    "ok",
    "pdfium_text",
]
PDFIUM_TEXT: Reason = "pdfium_text"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: Route
    reason: Reason
