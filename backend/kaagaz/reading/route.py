"""The route each page was read by (PRD 2 R1), and why (the council's ML 12).

PRD 2 PR 3. A route is stored in ``pages.route``:

* ``cells``: a spreadsheet page, read cell by cell;
* ``text_layer``: a PDF page read from the text it carries;
* ``ocr``: a page read by the OCR engine (or sent to it, with nothing found);
* ``blank``: no text, and a blank picture (R3).

It is worked out after reading, from the origin of each span on the page
(``Span.origin``, PR 2), so the reader protocol does not change. The reason
code says why: ``ok`` for a page that passed, the R2 check that failed
(``kaagaz/reading/text_quality.py``) for a page sent to OCR, and ``blank``
for a blank one. ``cli measure`` prints it; it is not stored.
"""

from collections.abc import Collection
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
]

ROUTES: tuple[Route, ...] = ("cells", "text_layer", "ocr", "blank")  # the pages.route CHECK in 0004
REASONS: tuple[Reason, ...] = (
    "few_chars",
    "bad_chars",
    "scan_image",
    "hidden_text",
    "legacy_font",
    "map_errors",
    "malformed_devanagari",
    "blank",
    "ok",
)

# A page with no text layer at all (a photo, or a scan inside a PDF) has zero
# letters and digits, which is what "few_chars" means.
_NO_TEXT_LAYER: Reason = "few_chars"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: Route
    reason: Reason


def route_for_page(
    *,
    unit: str,
    origins: Collection[str | None],
    blank: bool,
    ocr_on: bool,
    check: RouteDecision | None = None,
) -> RouteDecision | None:
    """The route stored for one page, and its reason, or None if it was not read.

    * ``unit``: the page's ``PageInfo.unit``; ``"cells"`` is a sheet.
    * ``origins``: the ``origin`` of each span the first reader kept on this page.
    * ``blank``: the page's picture is blank (R3, ``PageInfo.blank``).
    * ``ocr_on``: an OCR engine is set.
    * ``check``: what ``text_layer_ok`` said for this page's text layer, when
      known (a PDF page); None for a photo or when it was not kept.

    In order: a sheet page is ``cells``; any OCR span makes it ``ocr``; any
    text-layer span makes it ``text_layer`` (with the failed check's reason if
    it was kept as the fallback); spans with no known origin (stored before
    origins existed) give None; with no spans, a blank picture is ``blank``,
    and with an engine the page went to OCR and nothing was found, so
    ``ocr``; otherwise it was not read (None).

    O(1) for a set of origins; O(spans) for a list.
    """
    if unit == "cells":
        return RouteDecision("cells", "ok")
    if "ocr" in origins:
        return RouteDecision("ocr", _ocr_reason(check))
    if "text_layer" in origins:
        return RouteDecision("text_layer", "ok" if check is None else check.reason)
    if origins:
        return None
    if blank:
        return RouteDecision("blank", "blank")
    if ocr_on:
        return RouteDecision("ocr", _ocr_reason(check))
    return None


def _ocr_reason(check: RouteDecision | None) -> Reason:
    return check.reason if check is not None and check.route == "ocr" else _NO_TEXT_LAYER
