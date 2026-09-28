"""Build the reader socket from the settings: the one place readers are chosen.

Application code (the pipeline) calls ``build_reader_socket`` and then only
ever talks to the socket, never to a reader (CLAUDE.md "The reader plug
socket"). Adding a reader means one new module and one entry here.

* text -> CsvReader, zip -> XlsxReader, pdf -> PdfReader (text layer, and OCR
  for scanned pages when an engine is set up).
* png, jpeg, tiff -> ImageReader, only with an OCR engine. Without one they
  have no reader, so they are stored and marked needs_reader.
* Second readers: pdf -> PdfiumReader. Nothing else has one yet, so every
  other value stays single_reader.
"""

from kaagaz.ingestion import sniff
from kaagaz.reading.csv_reader import CsvReader
from kaagaz.reading.image_reader import ImageReader
from kaagaz.reading.ocr import OcrEngine
from kaagaz.reading.pdf_reader import PdfReader
from kaagaz.reading.pdf_second_reader import PdfiumReader
from kaagaz.reading.socket import Reader, ReaderSocket
from kaagaz.reading.xlsx_reader import XlsxLimits, XlsxReader
from kaagaz.settings import Settings


def build_reader_socket(settings: Settings, ocr: OcrEngine | None) -> ReaderSocket:
    readers: dict[str, Reader] = {
        sniff.TEXT: CsvReader(max_cells=settings.csv_max_cells),
        sniff.ZIP: XlsxReader(
            XlsxLimits(
                max_entries=settings.xlsx_max_entries,
                max_member_bytes=settings.xlsx_max_member_bytes,
                max_total_bytes=settings.xlsx_max_total_bytes,
                max_cells=settings.xlsx_max_cells,
                max_text_chars=settings.xlsx_max_text_chars,
            )
        ),
        sniff.PDF: PdfReader(
            ocr,
            max_pages=settings.max_pages,
            dpi=settings.page_image_dpi,
            max_image_pixels=settings.max_image_pixels,
        ),
    }
    if ocr is not None:
        images = ImageReader(ocr, max_pages=settings.max_pages, max_image_pixels=settings.max_image_pixels)
        readers.update({sniff.PNG: images, sniff.JPEG: images, sniff.TIFF: images})
    second_readers: dict[str, Reader] = {sniff.PDF: PdfiumReader(max_pages=settings.max_pages)}
    return ReaderSocket(readers, second_readers)
