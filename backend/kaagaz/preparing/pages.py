"""Prepare a document's pages: each page's size, and a picture of it.

The page viewer draws every source box over a picture of its page, so each
PDF page and each image frame is drawn here (pdf at ``dpi``; images
normalised to RGB with their EXIF orientation applied, so what the viewer
shows is what the scan reader read) and handed to ``on_picture`` as soon as it
is drawn. The caller stores it; nothing here keeps it (council A5b). Sizes
are kept in the page's own unit, points or pixels; boxes are fractions of
them (``kaagaz/reading/spans.py``).
Spreadsheets have no picture: ``sheet_pages`` gives one page per sheet.

The file is treated as hostile (it passed the virus scan, but a clean file
can still be built to exhaust memory):

* The page count is checked against ``max_pages`` before any page is drawn.
* The pixel count of every picture is checked against ``max_image_pixels``
  before it is drawn or decoded: from the PDF page size and ``dpi``, or from
  the image header. Pillow's own decompression bomb error maps to the same
  code.
* One page is drawn and decoded at a time, and dropped before the next.
* Errors are short codes (``corrupt_file``, ``too_many_pages``,
  ``image_too_large``, ``encrypted``), raised outside any ``except`` block,
  so no library error (which can quote the file) is chained on.

The PDF opening, page drawing and image frame helpers are shared with the
readers (``pdf_reader``, ``pdf_second_reader``, ``image_reader``), so the
limits hold the same way for every path that decodes a page.

Each page also gets its checks (PRD 2 R3, R4; ``page_checks.PageCheck``): whether
its picture is blank (``blank_stddev`` is BLANK_PAGE_STDDEV), and its effective
DPI, flagged under ``low_res_dpi`` (LOW_RES_DPI): a PDF page's from its largest
image, a photo's from its short side. The pipeline stores them with the page.

``PreparedPages.picture`` draws one page again, with the same DPI, pixel
check and EXIF handling, so the checker's crop (PRD 5 V1) has the pixels preparing drew.
``page_picture`` draws one page the same way with no other page drawn first: Ensemble's
crops after the scan (scan review UX rules, 16 Oct 2026).

Time is O(total pixels) to draw; memory is one page's picture at a time, O(1)
in the page count.
"""

import io
import math
import struct
import zlib
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageOps

from kaagaz.ingestion import sniff
from kaagaz.preparing.page_checks import PageCheck, is_blank, largest_image_dpi, low_resolution, photo_dpi
from kaagaz.reading.spans import CellLocation, PageInfo, ReadError, Span, TooManyPages

_IMAGE_TYPES = frozenset({sniff.PNG, sniff.JPEG, sniff.TIFF})
_IMAGE_FORMATS = ("PNG", "JPEG", "TIFF")  # Pillow never tries any other format
_POINTS_PER_INCH = 72.0

# Errors Pillow raises for a damaged or refused image. Pillow reports a file it
# cannot identify as OSError (UnidentifiedImageError), and some damage as
# ValueError, SyntaxError, EOFError, struct.error or zlib.error, and a truncated
# multi-frame TIFF as TypeError, KeyError or IndexError (from n_frames, council 17 Oct 2026).
_IMAGE_DAMAGED = (
    OSError,
    ValueError,
    SyntaxError,
    EOFError,
    TypeError,
    KeyError,
    IndexError,
    struct.error,
    zlib.error,
)
_IMAGE_BOMB = (Image.DecompressionBombError, Image.DecompressionBombWarning)

# Called with (page number, page count, picture) for each page drawn, in order.
OnPicture = Callable[[int, int, Image.Image], None]


@dataclass(frozen=True, slots=True)
class _Limits:
    dpi: int
    max_pages: int
    max_image_pixels: int
    blank_stddev: float
    low_res_dpi: int


@dataclass(frozen=True, slots=True)
class PreparedPages:
    pages: list[PageInfo]
    checks: dict[int, PageCheck] = field(default_factory=dict)  # page number -> its checks
    # What ``picture`` draws from: the file's type, its bytes and the limits.
    source: tuple[str, bytes, _Limits] | None = field(default=None, repr=False, compare=False)

    def picture(self, page_no: int) -> Image.Image:
        """Page ``page_no``'s picture, drawn again exactly as preparing drew it. O(pixels)."""
        if self.source is None:
            raise ValueError("these pages have no pictures")
        detected_type, data, limits = self.source
        if detected_type == sniff.PDF:
            document = open_pdf(data)
            try:
                page = pdf_page(document, page_no - 1)
                try:
                    return render_pdf_page(page, dpi=limits.dpi, max_image_pixels=limits.max_image_pixels)
                finally:
                    page.close()
            finally:
                document.close()
        image = _open_image(data)
        try:
            return _decode_frame(image, page_no - 1, limits.max_image_pixels)  # image_pages' own step
        finally:
            image.close()


def prepare_pages(
    detected_type: str,
    data: bytes,
    on_picture: OnPicture,
    *,
    dpi: int,
    max_pages: int,
    max_image_pixels: int,
    blank_stddev: float,
    low_res_dpi: int,
) -> PreparedPages:
    limits = _Limits(dpi, max_pages, max_image_pixels, blank_stddev, low_res_dpi)
    if detected_type == sniff.PDF:
        return _prepare_pdf(data, limits, on_picture)
    if detected_type in _IMAGE_TYPES:
        prepared = PreparedPages([], {}, (detected_type, data, limits))
        with _open_image(data) as header:  # the header only, for the page count
            count = _frame_count(header)
        # Not enumerate(): its reused tuple would keep the last frame alive into the next decode.
        for image in image_pages(data, max_pages=max_pages, max_image_pixels=max_image_pixels):
            number = len(prepared.pages) + 1
            prepared.pages.append(PageInfo(number, float(image.width), float(image.height), "px"))
            prepared.checks[number] = _check(image, photo_dpi(image.width, image.height), limits)
            on_picture(number, count, image)
            del image  # dropped before the next frame is decoded
        return prepared
    return PreparedPages([])


def page_picture(
    detected_type: str, data: bytes, page_no: int, *, dpi: int, max_image_pixels: int
) -> Image.Image:
    """Page ``page_no`` of a pdf or image file, drawn as ``prepare_pages`` draws it, losslessly
    (``PreparedPages.picture``). O(one page's pixels)."""
    limits = _Limits(dpi, 0, max_image_pixels, 0.0, 0)  # only the DPI and the pixel check are used
    return PreparedPages([], {}, (detected_type, data, limits)).picture(page_no)


def sheet_pages(spans: Iterable[Span]) -> list[PageInfo]:
    """One page per sheet that has cells: width = last column used, height = last row used.

    O(n) for n spans.
    """
    extent: dict[int, tuple[int, int]] = {}
    for span in spans:
        location = span.location
        if isinstance(location, CellLocation):
            columns, rows = extent.get(location.sheet, (0, 0))
            extent[location.sheet] = (max(columns, location.column), max(rows, location.row))
    return [
        PageInfo(sheet, float(columns), float(rows), "cells")
        for sheet, (columns, rows) in sorted(extent.items())
    ]


def _prepare_pdf(data: bytes, limits: _Limits, on_picture: OnPicture) -> PreparedPages:
    prepared = PreparedPages([], {}, (sniff.PDF, data, limits))
    document = open_pdf(data)
    try:
        count = len(document)
        check_page_count(count, limits.max_pages)
        for index in range(count):
            page = pdf_page(document, index)
            try:
                width, height = page.get_size()
                image = render_pdf_page(page, dpi=limits.dpi, max_image_pixels=limits.max_image_pixels)
                dpi = largest_image_dpi(page)
            finally:
                page.close()
            prepared.pages.append(PageInfo(index + 1, float(width), float(height), "pt"))
            prepared.checks[index + 1] = _check(image, dpi, limits)
            on_picture(index + 1, count, image)
            del image  # dropped before the next page is drawn
    finally:
        document.close()
    return prepared


def _check(image: Image.Image, dpi: int | None, limits: _Limits) -> PageCheck:
    return PageCheck(dpi, low_resolution(dpi, limits.low_res_dpi), is_blank(image, limits.blank_stddev))


# -- shared with the readers --------------------------------------------------


def open_pdf(data: bytes) -> Any:
    """A pypdfium2 document, or ReadError ``encrypted`` / ``corrupt_file``.

    ``encrypted`` means the file needs a password to open. A file with only
    an owner password (printing or copying restricted) opens and is read.
    """
    locked = False
    try:
        return pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        locked = error.err_code in (pdfium_c.FPDF_ERR_PASSWORD, pdfium_c.FPDF_ERR_SECURITY)
    raise ReadError("encrypted" if locked else "corrupt_file")  # outside the except block: nothing chained


def pdf_page(document: Any, index: int) -> Any:
    """Page ``index`` (from 0) of an open pypdfium2 document, or ReadError ``corrupt_file``
    when PDFium cannot load it (a damaged page tree: PdfiumError, council 17 Oct 2026)."""
    try:
        return document[index]
    except pdfium.PdfiumError:
        pass
    raise ReadError("corrupt_file")  # outside the except block: nothing chained


def check_page_count(count: int, max_pages: int) -> None:
    if count > max_pages:
        raise TooManyPages(count)


def render_pdf_page(page: Any, *, dpi: int, max_image_pixels: int, smooth_text: bool = True) -> Image.Image:
    """Draw one pypdfium2 page at ``dpi`` as an RGB picture, after the pixel check.
    ``smooth_text=False`` draws text without anti-aliasing: each glyph pixel is the text's own colour."""
    scale = dpi / _POINTS_PER_INCH
    width, height = page.get_size()  # points, with the page's rotation applied
    if math.ceil(width * scale) * math.ceil(height * scale) > max_image_pixels:
        raise ReadError("image_too_large")
    try:
        bitmap = page.render(scale=scale, no_smoothtext=not smooth_text)
        try:
            return _to_rgb(bitmap.to_pil())
        finally:
            bitmap.close()
    except (pdfium.PdfiumError, ValueError):  # ValueError: a page with no area
        pass
    raise ReadError("corrupt_file")


def image_pages(data: bytes, *, max_pages: int, max_image_pixels: int) -> Iterator[Image.Image]:
    """Each page of a png, jpeg or tiff as an upright RGB picture, one at a time.

    Every frame of a TIFF is a page. Only the first frame of any other
    format is (a phone's JPEG can carry a second, smaller preview picture).
    """
    image = _open_image(data)
    try:
        frames = _frame_count(image)
        check_page_count(frames, max_pages)
        for index in range(frames):
            yield _decode_frame(image, index, max_image_pixels)
    finally:
        image.close()


def _open_image(data: bytes) -> Image.Image:
    code = "corrupt_file"
    try:
        return Image.open(io.BytesIO(data), formats=_IMAGE_FORMATS)  # reads the header only
    except _IMAGE_BOMB:
        code = "image_too_large"
    except _IMAGE_DAMAGED:
        pass
    raise ReadError(code)


def _frame_count(image: Image.Image) -> int:
    if image.format != "TIFF":
        return 1
    try:
        return int(getattr(image, "n_frames", 1))
    except _IMAGE_DAMAGED:
        pass
    raise ReadError("corrupt_file")


def _decode_frame(image: Image.Image, index: int, max_image_pixels: int) -> Image.Image:
    code = "corrupt_file"
    try:
        image.seek(index)  # reads this frame's header, not its pixels
        width, height = image.size
        if width * height <= max_image_pixels:
            return _to_rgb(ImageOps.exif_transpose(image))  # decodes this frame only
        code = "image_too_large"
    except _IMAGE_BOMB:
        code = "image_too_large"
    except _IMAGE_DAMAGED:
        pass
    raise ReadError(code)


def _to_rgb(image: Image.Image) -> Image.Image:
    """RGB, with any transparent parts on white (as a page is), not on black."""
    has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
    if not has_alpha:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    page = Image.new("RGB", rgba.size, "white")
    page.paste(rgba, mask=rgba.getchannel("A"))
    return page
