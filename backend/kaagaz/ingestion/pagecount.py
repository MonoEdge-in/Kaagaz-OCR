"""How many pages an upload has, so the server alone picks its lane (PRD 5 Q4, V2).

A document of at most EXPRESS_MAX_PAGES pages goes to the express queue, so a single
invoice is never stuck behind someone's 60-page scans. The count is the server's own;
a page count from the browser is never asked for or trusted.

* A PDF is counted with pypdfium2: the file is opened and its page count read. No page
  is drawn or decoded, so this runs before the virus scan (V2) and costs about as much
  as reading the file's cross-reference table.
* A PNG or JPEG is 1 page. A TIFF is its number of frames (Pillow's ``n_frames``),
  read from the frame headers, never the pixels.
* A spreadsheet or text file is 1: it is read whole.
* Anything that fails (a damaged or encrypted PDF, a broken TIFF) is None, and None
  goes to the main lane: an unknown count is never express.
* A file over COUNT_MAX_BYTES (5 MB), of any type, is None without being opened: the
  count runs on unscanned bytes in the API process, so how much it parses is bounded.
  A short invoice, a phone photo or a small sheet is well under it; a bigger file is
  rarely a 3-page document, and the main lane reads it as well, only later.

pdfium is not safe to call from two threads at once, and the API takes uploads in
parallel (Q2), so the PDF count holds a lock. The size cap bounds how long one upload
holds it. O(pages) for a TIFF's frame headers; the PDF's open and count are pdfium's
own cost, on at most COUNT_MAX_BYTES.
"""

import io
import struct
import threading
import zlib
from typing import IO

import pypdfium2 as pdfium
from PIL import Image

from kaagaz.ingestion import sniff

# The largest file counted (bytes); anything bigger goes to the main lane uncounted.
COUNT_MAX_BYTES = 5 * 1024 * 1024

_PDFIUM = threading.Lock()
# What Pillow raises for a damaged TIFF, as kaagaz/preparing/pages.py lists it (a
# truncated multi-frame TIFF raises TypeError, KeyError or IndexError from n_frames, council 17 Oct 2026).
_DAMAGED = (
    OSError,
    ValueError,
    SyntaxError,
    EOFError,
    TypeError,
    KeyError,
    IndexError,
    struct.error,
    zlib.error,
    Image.DecompressionBombError,
    Image.DecompressionBombWarning,
)


def count_pages(file: IO[bytes], detected_type: str) -> int | None:
    """The pages of the upload in ``file`` (any position; rewound to the start after),
    or None when they cannot be counted or the file is over COUNT_MAX_BYTES.
    ``detected_type`` is sniff's type."""
    try:
        if file.seek(0, io.SEEK_END) > COUNT_MAX_BYTES:
            return None
        if detected_type == sniff.PDF:
            return _pdf_pages(file)
        if detected_type == sniff.TIFF:
            return _tiff_frames(file)
        return 1
    finally:
        file.seek(0)


def goes_express(page_count: int | None, express_max_pages: int) -> bool:
    """Whether a document of ``page_count`` pages goes to the express lane (Q4): a
    known count of 1 to ``express_max_pages``. Upload and "read again" (with the
    page count stored by the last read) both ask this."""
    return page_count is not None and 1 <= page_count <= express_max_pages


def _pdf_pages(file: IO[bytes]) -> int | None:
    file.seek(0)
    with _PDFIUM:
        try:
            document = pdfium.PdfDocument(file)
        except pdfium.PdfiumError:
            return None
        try:
            return len(document)
        finally:
            document.close()


def _tiff_frames(file: IO[bytes]) -> int | None:
    file.seek(0)
    try:
        with Image.open(file, formats=["TIFF"]) as image:
            return int(getattr(image, "n_frames", 1))
    except _DAMAGED:
        return None
