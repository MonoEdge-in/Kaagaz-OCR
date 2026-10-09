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

pdfium is not safe to call from two threads at once, and the API takes uploads in
parallel (Q2), so the PDF count holds a lock. O(pages) for a TIFF's frame headers;
the PDF's open and count are pdfium's own cost.
"""

import threading
from typing import IO

import pypdfium2 as pdfium
from PIL import Image

from kaagaz.ingestion import sniff

_PDFIUM = threading.Lock()
_DAMAGED = (OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError)


def count_pages(file: IO[bytes], detected_type: str) -> int | None:
    """The pages of the upload in ``file`` (any position; rewound to the start after),
    or None when they cannot be counted. ``detected_type`` is sniff's type."""
    try:
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
