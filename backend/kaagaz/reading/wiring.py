"""Build the reader socket from the settings: the one place readers are chosen.

Application code (the pipeline) calls ``build_reader_socket`` and then only
ever talks to the socket, never to a reader (CLAUDE.md "The reader plug
socket"). Adding a reader means one new module and one entry here.

* text -> CsvReader, zip -> XlsxReader, pdf -> PdfReader (text layer, and OCR
  for pages whose text layer fails the route check when an engine is set up,
  drawn at OCR_DPI; PRD 2 R2 and R3 with the TEXT_LAYER_*, SCAN_IMAGE_COVERAGE
  and BLANK_PAGE_STDDEV settings).
* png, jpeg, tiff -> ImageReader, only with an OCR engine. Without one they
  have no reader, so they are stored and marked needs_reader.
* Second readers: pdf -> PdfiumReader. Nothing else has one yet, so every
  other value stays single_reader.
"""

from typing import Any

from kaagaz.ingestion import sniff
from kaagaz.preparing import page_checks
from kaagaz.reading import text_quality
from kaagaz.reading.csv_reader import CsvReader
from kaagaz.reading.image_reader import ImageReader
from kaagaz.reading.ocr import OcrEngine
from kaagaz.reading.pdf_reader import PdfReader, text_layer_checks
from kaagaz.reading.pdf_second_reader import PdfiumReader
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.socket import Reader, ReaderSocket
from kaagaz.reading.text_quality import PageSignals, TextLayerRules
from kaagaz.reading.xlsx_reader import XlsxLimits, XlsxReader
from kaagaz.settings import Settings

# What the PDF reader's route check says about one page, and the signals it saw.
PdfPageCheck = tuple[RouteDecision, PageSignals]


def text_layer_rules(settings: Settings) -> TextLayerRules:
    return TextLayerRules(
        min_chars=settings.text_layer_min_chars,
        min_good_ratio=settings.text_layer_min_good_ratio,
        scan_image_coverage=settings.scan_image_coverage,
    )


def reader_config(settings: Settings) -> dict[str, Any]:
    """How pages are routed and read, as plain JSON for a run's ``config`` (the
    council's ML 10): the OCR engine and DPI, and every route threshold, the
    settings and the fixed numbers alike."""
    return {
        "ocr_engine": settings.ocr_engine,
        "ocr_dpi": settings.ocr_dpi,
        "page_image_dpi": settings.page_image_dpi,
        "text_layer_min_chars": settings.text_layer_min_chars,
        "text_layer_min_good_ratio": settings.text_layer_min_good_ratio,
        "scan_image_coverage": settings.scan_image_coverage,
        "scan_footer_max_chars": text_quality.SCAN_FOOTER_MAX_CHARS,
        "hidden_text_max_share": text_quality.HIDDEN_TEXT_MAX_SHARE,
        "legacy_font_max_share": text_quality.LEGACY_FONT_MAX_SHARE,
        "map_error_max_share": text_quality.MAP_ERROR_MAX_SHARE,
        "malformed_devanagari_max_share": text_quality.MALFORMED_DEVANAGARI_MAX_SHARE,
        "blank_page_stddev": settings.blank_page_stddev,
        "blank_dark_offset": page_checks.BLANK_DARK_OFFSET,
        "blank_dark_pixels": page_checks.BLANK_DARK_PIXELS,
        "blank_sample_width": page_checks.BLANK_SAMPLE_WIDTH,
        "low_res_dpi": settings.low_res_dpi,
    }


def pdf_text_layer_checks(settings: Settings, data: bytes) -> list[PdfPageCheck]:
    """What the PDF reader's route check (R2) says about each page, and the signals
    it saw: for ``cli measure`` only, which may not call a reader itself."""
    return text_layer_checks(data, rules=text_layer_rules(settings), max_pages=settings.max_pages)


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
            dpi=settings.ocr_dpi,
            max_image_pixels=settings.max_image_pixels,
            rules=text_layer_rules(settings),
            blank_stddev=settings.blank_page_stddev,
        ),
    }
    if ocr is not None:
        images = ImageReader(
            ocr,
            max_pages=settings.max_pages,
            max_image_pixels=settings.max_image_pixels,
            ocr_dpi=settings.ocr_dpi,
            blank_stddev=settings.blank_page_stddev,
        )
        readers.update({sniff.PNG: images, sniff.JPEG: images, sniff.TIFF: images})
    second_readers: dict[str, Reader] = {sniff.PDF: PdfiumReader(max_pages=settings.max_pages)}
    return ReaderSocket(readers, second_readers)
