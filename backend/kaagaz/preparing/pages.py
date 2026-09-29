"""Prepare a document's pages: each page's size, and a picture of it.

The page viewer draws every source box over a picture of its page, so each
PDF page and each image frame gets one PNG here (pdf at ``dpi``; images
normalised to RGB with their EXIF orientation applied, so what the viewer
shows is what the scan reader read). Sizes are kept in the page's own unit,
points or pixels; boxes are fractions of them (``kaagaz/reading/spans.py``).
Spreadsheets have no picture: ``sheet_pages`` gives one page per sheet.

The file is treated as hostile (it passed the virus scan, but a clean file
can still be built to exhaust memory):

* The page count is checked against ``max_pages`` before any page is drawn.
* The pixel count of every picture is checked against ``max_image_pixels``
  before it is drawn or decoded: from the PDF page size and ``dpi``, or from
  the image header. Pillow's own decompression bomb error maps to the same
  code.
* One page is drawn and decoded at a time; only its PNG is kept.
* Errors are short codes (``corrupt_file``, ``too_many_pages``,
  ``image_too_large``, ``encrypted``), raised outside any ``except`` block,
  so no library error (which can quote the file) is chained on.

The PDF opening, page drawing and image frame helpers are shared with the
readers (``pdf_reader``, ``pdf_second_reader``, ``image_reader``), so the
limits hold the same way for every path that decodes a page.

Time is O(total pixels) to draw and encode; memory is one page's picture at
a time plus the PNGs kept (their total is what the caller stores anyway).
"""

import io
import math
import struct
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageOps

from kaagaz.ingestion import sniff
from kaagaz.reading.spans import CellLocation, PageInfo, ReadError, Span, TooManyPages

_IMAGE_TYPES = frozenset({sniff.PNG, sniff.JPEG, sniff.TIFF})
_IMAGE_FORMATS = ("PNG", "JPEG", "TIFF")  # Pillow never tries any other format
_POINTS_PER_INCH = 72.0

# Errors Pillow raises for a damaged or refused image. Pillow reports a file it
# cannot identify as OSError (UnidentifiedImageError), and some damage as
# ValueError, SyntaxError, EOFError, struct.error or zlib.error.
_IMAGE_DAMAGED = (OSError, ValueError, SyntaxError, EOFError, struct.error, zlib.error)
_IMAGE_BOMB = (Image.DecompressionBombError, Image.DecompressionBombWarning)


@dataclass(frozen=True, slots=True)
class PreparedPages:
    pages: list[PageInfo]
    images: dict[int, bytes]  # page number -> PNG


def prepare_pages(
    detected_type: str, data: bytes, *, dpi: int, max_pages: int, max_image_pixels: int
) -> PreparedPages:
    if detected_type == sniff.PDF:
        return _prepare_pdf(data, dpi=dpi, max_pages=max_pages, max_image_pixels=max_image_pixels)
    if detected_type in _IMAGE_TYPES:
        pages: list[PageInfo] = []
        images: dict[int, bytes] = {}
        frames = image_pages(data, max_pages=max_pages, max_image_pixels=max_image_pixels)
        for number, image in enumerate(frames, start=1):
            pages.append(PageInfo(number, float(image.width), float(image.height), "px"))
            images[number] = png_bytes(image)
        return PreparedPages(pages, images)
    return PreparedPages([], {})


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


def _prepare_pdf(data: bytes, *, dpi: int, max_pages: int, max_image_pixels: int) -> PreparedPages:
    pages: list[PageInfo] = []
    images: dict[int, bytes] = {}
    document = open_pdf(data)
    try:
        check_page_count(len(document), max_pages)
        for index in range(len(document)):
            page = document[index]
            try:
                width, height = page.get_size()
                image = render_pdf_page(page, dpi=dpi, max_image_pixels=max_image_pixels)
            finally:
                page.close()
            pages.append(PageInfo(index + 1, float(width), float(height), "pt"))
            images[index + 1] = png_bytes(image)
    finally:
        document.close()
    return PreparedPages(pages, images)


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


def check_page_count(count: int, max_pages: int) -> None:
    if count > max_pages:
        raise TooManyPages(count)


def render_pdf_page(page: Any, *, dpi: int, max_image_pixels: int) -> Image.Image:
    """Draw one pypdfium2 page at ``dpi`` as an RGB picture, after the pixel check."""
    scale = dpi / _POINTS_PER_INCH
    width, height = page.get_size()  # points, with the page's rotation applied
    if math.ceil(width * scale) * math.ceil(height * scale) > max_image_pixels:
        raise ReadError("image_too_large")
    try:
        bitmap = page.render(scale=scale)
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


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


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
