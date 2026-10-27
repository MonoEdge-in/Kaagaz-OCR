"""Which reader a scanned page needs, decided page by page (#384), from the local reader's own
read of it.

For the reader choice "Save cost" (#398, scan level save, read Automatic:
``db.models.Reading.routes``; PAGE_ROUTING is the platform's permission for it), the Gemini
reader first reads each scanned page with the local reader (RapidOCR, no model call) and
``route_page`` decides from what that read shows:

* ``local``: a clean printed page. The local read is the page's read; no model call.
* ``regions``: a clean page with a few lines the local reader was not sure of (at most
  PAGE_ROUTE_REGION_LINES_MAX lines and PAGE_ROUTE_REGION_SHARE of its lines). Only those
  lines are cut out, zoomed and read by the model in one call (#385,
  ``gemini_ocr.GeminiEngine._local_regions``); the rest of the page keeps the local read.
* ``model``: anything else, the page read whole by the model, as before routing.

The signals are what the local read already holds, plus its boxes: the lines' confidences, the
words, the scripts (RapidOCR's default recogniser has no Devanagari, and its Devanagari is not
trusted, labels.py ``ocr_script``), the text's height in pixels (small text is misread), how
much the lines' heights vary (a sign of handwriting: printed lines are of a few sizes), and how
many lines sit three or more to a row (a table: only the model gives cells their row and
column). Every check sends the page to the model when it is not met: when unsure, the model.

The router can only save model calls: it runs only where the account chose it and its reading
already calls the model for every page (never for AI settings None, which reads locally, and
never for Low, High or Max, which read every page with the model), and it never reads a page
the document would not read. A page asked to be read again (F5) is never routed.

Pure and O(lines log lines): no image, no model, no database.
"""

import statistics
from dataclasses import dataclass
from typing import Literal

from kaagaz.chunking.script import has_devanagari_letters, ocr_script_suspect
from kaagaz.reading.ocr import OcrLine, PageRead

Route = Literal["local", "regions", "model"]
LOCAL: Route = "local"
REGIONS: Route = "regions"
MODEL: Route = "model"
# Lines that sit at least this many to a row are table cells (``table_share``).
ROW_CELLS = 3


@dataclass(frozen=True, slots=True)
class RouteRules:
    """The thresholds (settings PAGE_ROUTE_*; the defaults favour the model):

    * ``clean_confidence``: a line read at least this sure by the local reader is clean.
    * ``min_words``: a page with fewer words found goes to the model (a photo, a stamp,
      handwriting the local reader missed).
    * ``min_line_px``: text whose median line height is below this many pixels goes to the
      model.
    * ``height_spread``: the lines' heights' coefficient of variation above which the page
      looks handwritten.
    * ``table_share``: the share of lines in rows of ROW_CELLS or more above which the page
      holds a table.
    * ``region_lines_max``, ``region_share``: the most unsure lines, and share of its lines,
      a page may have to be read through its regions; past either, the whole page."""

    clean_confidence: float = 0.95
    min_words: int = 8
    min_line_px: float = 16.0
    height_spread: float = 0.45
    table_share: float = 0.3
    region_lines_max: int = 6
    region_share: float = 0.15


@dataclass(frozen=True, slots=True)
class PageRoute:
    """``route_page``'s decision: the route, the reason code (for run_config.reading.routed)
    and, for ``regions``, the indexes of the lines to read again, in page order."""

    route: Route
    reason: str
    lines: tuple[int, ...] = ()


def route_page(read: PageRead, rules: RouteRules) -> PageRoute:
    """The route of a page from its local ``read`` (see the module notes), the first rule that
    is not met naming the reason: no_text, few_words, script, small_text, handwriting, table,
    then clean (local), few_unsure (regions) or unsure (model). O(lines log lines)."""
    lines = read.lines
    if not lines:
        return PageRoute(MODEL, "no_text")
    text = " ".join(line.text for line in lines)
    if len(text.split()) < rules.min_words:
        return PageRoute(MODEL, "few_words")
    if has_devanagari_letters(text) or ocr_script_suspect(text):
        return PageRoute(MODEL, "script")
    heights = [line.y1 - line.y0 for line in lines]
    if statistics.median(heights) < rules.min_line_px:
        return PageRoute(MODEL, "small_text")
    if height_spread(heights) > rules.height_spread:
        return PageRoute(MODEL, "handwriting")
    if table_share(lines) > rules.table_share:
        return PageRoute(MODEL, "table")
    unsure = tuple(
        index
        for index, line in enumerate(lines)
        if line.confidence is None or line.confidence < rules.clean_confidence
    )
    if not unsure:
        return PageRoute(LOCAL, "clean")
    if len(unsure) <= rules.region_lines_max and len(unsure) <= rules.region_share * len(lines):
        return PageRoute(REGIONS, "few_unsure", unsure)
    return PageRoute(MODEL, "unsure")


def height_spread(heights: list[float]) -> float:
    """The heights' coefficient of variation (population standard deviation over the mean);
    0 for one line or a mean of 0. O(n)."""
    mean = statistics.fmean(heights)
    if len(heights) < 2 or mean <= 0:
        return 0.0
    return statistics.pstdev(heights) / mean


def table_share(lines: tuple[OcrLine, ...]) -> float:
    """The share of ``lines`` in a row of at least ROW_CELLS lines: lines whose middles lie
    within each other's height, side by side. O(n log n) to sort, O(n x r) for rows of r."""
    order = sorted(range(len(lines)), key=lambda index: (lines[index].y0 + lines[index].y1) / 2)
    in_rows = 0
    row: list[int] = []
    for index in order:
        line = lines[index]
        middle = (line.y0 + line.y1) / 2
        if row and not all(lines[other].y0 <= middle <= lines[other].y1 for other in row):
            in_rows += len(row) if len(row) >= ROW_CELLS else 0
            row = []
        row.append(index)
    in_rows += len(row) if len(row) >= ROW_CELLS else 0
    return in_rows / len(lines)
