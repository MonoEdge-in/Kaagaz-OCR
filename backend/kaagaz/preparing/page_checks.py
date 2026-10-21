"""Checks on a page's picture and images: is it blank (R3), and how sharp is it (R4).

PRD 2 PR 3. Pure helpers over a Pillow picture or one open pypdfium2 page.
Nothing here reads a setting: the caller passes BLANK_PAGE_STDDEV and
LOW_RES_DPI. They are shared by the preparer and both OCR readers, so a page
is judged the same way on every path.

Blank (R3, with the council's ML 13 rule)
-----------------------------------------
A page is blank when both hold on a small grey copy of its picture:

* the standard deviation of the grey values is under ``max_stddev``
  (BLANK_PAGE_STDDEV, 3.0); and
* fewer than ``BLANK_DARK_PIXELS`` (20) pixels are darker than the mean
  minus ``BLANK_DARK_OFFSET`` (48).

The small copy is the grey picture shrunk by a whole factor to about
``BLANK_SAMPLE_WIDTH`` (512) pixels wide with a box filter, so a page is
measured alike at 150 and 300 dpi, and scanner specks and JPEG noise are
averaged out. The second rule keeps a page with a little real ink (one short
line, a signature) away from "blank" even when its spread is small.

PROVISIONAL: the sample width (plan §5.2) and the two dark-pixel numbers
(ML 13). Measure them with ``cli measure`` and record any change.

Effective DPI (R4)
------------------
A PDF page's effective DPI comes from its largest image: the image's pixel
width over its placed width in inches. A photo assumes A4: its short side
over 8.27 inches. The answer is None (stored as NULL) unless it is a finite
whole number from 1 up to what the ``integer`` column holds, so a crafted
file never reaches the ``effective_dpi > 0`` CHECK (security A7).

The placed size comes from the image's matrix, not its page box: an image
drawn a quarter turn is still paired with its own width (the plan's
PROVISIONAL note on turned images no longer applies). Only images drawn
directly on the page count: VERIFIED on pypdfium2 5.13, an image inside a
form XObject reports its bounds and matrix in the form's own space, so such
a page gives None rather than a wrong number.
"""

import math
from dataclasses import dataclass
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageStat

BLANK_SAMPLE_WIDTH = 512  # plan §5.2, PROVISIONAL
BLANK_DARK_OFFSET = 48  # ML 13: "darker than (mean - 48)"
BLANK_DARK_PIXELS = 20  # ML 13: "fewer than 20 pixels"
A4_SHORT_SIDE_INCHES = 8.27  # R4: a photo "assumes an A4 width"

_POINTS_PER_INCH = 72.0
_INTEGER_MAX = 2**31 - 1  # pages.effective_dpi is a PostgreSQL integer


@dataclass(frozen=True, slots=True)
class PageCheck:
    """What the preparer found about one page's picture, stored with the page."""

    effective_dpi: int | None  # R4; None when the page has no image to measure
    low_resolution: bool  # R4: effective_dpi under LOW_RES_DPI
    blank: bool  # R3: the stored picture is blank


@dataclass(frozen=True, slots=True)
class BlankMeasure:
    """What the blank rule sees: printed by ``cli measure`` (ML 12)."""

    stddev: float
    dark_pixels: int


def measure_blank(image: Image.Image) -> BlankMeasure:
    """The grey spread and the dark pixel count of the picture's small copy.

    O(P) for P pixels (grey conversion and the box shrink, both in C), then
    O(P / k^2) for the histogram of the copy shrunk k times, and O(256).
    """
    if image.width < 1 or image.height < 1:
        return BlankMeasure(0.0, 0)
    grey = image.convert("L")
    factor = max(1, grey.width // BLANK_SAMPLE_WIDTH)
    small = grey.reduce(factor) if factor > 1 else grey
    histogram = small.histogram()
    stat = ImageStat.Stat(histogram)
    cut = stat.mean[0] - BLANK_DARK_OFFSET
    dark = sum(count for value, count in enumerate(histogram) if value < cut)
    return BlankMeasure(float(stat.stddev[0]), dark)


def is_blank(image: Image.Image, max_stddev: float, *, dark_pixels: int = BLANK_DARK_PIXELS) -> bool:
    """R3: True when the picture's spread is under ``max_stddev`` and fewer than
    ``dark_pixels`` of its small copy's pixels are dark. A blank page is never
    sent to OCR. ``max_stddev`` 0 means no page is ever blank. O(P), see
    ``measure_blank``.
    """
    measure = measure_blank(image)
    return measure.stddev < max_stddev and measure.dark_pixels < dark_pixels


def effective_dpi(image_px: tuple[int, int], placed_width_pt: float, placed_height_pt: float) -> int | None:
    """R4: the image's pixel width over its placed width in inches, rounded.

    ``image_px`` is (width, height) in pixels; the placed size is in points.
    None for an image with no pixels, a placed width or height that is zero,
    negative (unsorted or flipped bounds) or not finite, and any answer under
    1 dpi (a tiny image stretched over the page) or past the integer column.
    The height is only checked: a placement with no height draws nothing. O(1).
    """
    width_px, height_px = image_px
    if width_px < 1 or height_px < 1:
        return None
    if not (math.isfinite(placed_width_pt) and math.isfinite(placed_height_pt)):
        return None
    if placed_width_pt <= 0 or placed_height_pt <= 0:
        return None
    return _whole_dpi(width_px * _POINTS_PER_INCH / placed_width_pt)


def photo_dpi(width_px: int, height_px: int) -> int | None:
    """R4 for a photo: its short side over A4's short side (8.27 inches). O(1)."""
    if width_px < 1 or height_px < 1:
        return None
    return _whole_dpi(min(width_px, height_px) / A4_SHORT_SIDE_INCHES)


def low_resolution(dpi: int | None, low_res_dpi: int) -> bool:
    """R4: flagged, never refused, when the dpi is known and under LOW_RES_DPI. O(1)."""
    return dpi is not None and dpi < low_res_dpi


def placed_size(a: float, b: float, c: float, d: float) -> tuple[float, float]:
    """How wide and high an image is drawn, in points, from its matrix [a b c d e f].

    A PDF image fills the unit square, and its matrix maps that square onto the
    page: the image's own x axis becomes (a, b) and its y axis (c, d). Their
    lengths are the placed width and height through any mirror or turn. O(1).
    """
    return math.hypot(a, b), math.hypot(c, d)


def largest_image_dpi(page: Any) -> int | None:
    """R4 for one open pypdfium2 page: ``effective_dpi`` of its largest image
    by placed area, or None when the page has no image drawn on it directly.

    An image whose pixel size or matrix PDFium cannot read, or whose area is
    not finite, is passed over; a page whose objects PDFium cannot list has no
    DPI (None: flagged nothing, refused nothing). O(objects on the page) time,
    O(1) space.
    """
    best: tuple[float, tuple[int, int], float, float] | None = None
    try:
        for image in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=1):
            try:
                image_px = image.get_px_size()
                a, b, c, d, _e, _f = image.get_matrix().get()
            except pdfium.PdfiumError:
                continue
            if min(image_px) < 1:
                continue
            area = abs(a * d - b * c)
            if not math.isfinite(area):
                continue
            if best is None or area > best[0]:
                best = (area, image_px, *placed_size(a, b, c, d))
    except pdfium.PdfiumError:
        return None
    if best is None:
        return None
    _area, image_px, width, height = best
    return effective_dpi(image_px, width, height)


def _whole_dpi(dpi: float) -> int | None:
    if not math.isfinite(dpi) or dpi < 1:
        return None
    whole = round(dpi)
    return whole if whole <= _INTEGER_MAX else None
