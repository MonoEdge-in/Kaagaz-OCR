"""Build the reader socket from the settings: the one place readers are chosen.

Application code (the pipeline) calls ``build_reader_socket`` and then only
ever talks to the socket, never to a reader (CLAUDE.md "The reader plug
socket"). Adding a reader means one new module and one entry here.

* Readers are chosen by the kind ``kaagaz/ingestion/resolve.py`` tells after the
  virus scan (#522): csv -> CsvReader; txt, md, json and html -> TextReader (each
  file's text as lines on pages, no AI); xlsx -> XlsxReader; pdf -> PdfReader (text layer, and OCR
  for pages whose text layer fails the route check when an engine is set up,
  drawn at OCR_DPI or, by OCR_DPI_RULE, at the DPI its text needs with OCR_DPI
  as the ceiling; PRD 2 R2 and R3 with the TEXT_LAYER_*, SCAN_IMAGE_COVERAGE
  and BLANK_PAGE_STDDEV settings).
* png, jpeg, tiff, heic -> ImageReader, only with an OCR engine. Without one they
  have no reader, so they are stored and marked needs_reader.
* Second readers: pdf -> PdfiumReader. Nothing else has one yet, so every
  other value stays single_reader.
* Pages in parallel (PRD 5 G8): with OCR_ENGINE=gemini, the PDF and image
  readers share one ``PagePool`` of OCR_PAGE_CONCURRENCY threads, because each
  Gemini call is mostly waiting on the network. Any other engine reads one page
  at a time, as before: RapidOCR is CPU work, one page at a time per engine.
* The Gemini reader (PRD 5 G1) is built by ``build_gemini_engine``, the one
  place that names its module, imported by its path only when OCR_ENGINE=gemini,
  so OCR_ENGINE=rapidocr never loads it or calls Gemini. ``build_ocr_engine`` here
  is the product's engine: ``kaagaz.reading.ocr``'s, with that factory.
"""

import importlib
from typing import Any

from kaagaz.db.engine import Database
from kaagaz.ingestion import resolve, sniff
from kaagaz.preparing import page_checks
from kaagaz.reading import ocr_dpi, text_quality
from kaagaz.reading.csv_reader import CsvReader
from kaagaz.reading.image_reader import ImageReader
from kaagaz.reading.ocr import GEMINI, OcrEngine, PageEngine
from kaagaz.reading.ocr import build_ocr_engine as _ocr_engine
from kaagaz.reading.pdf_reader import PdfReader, text_layer_checks
from kaagaz.reading.pdf_second_reader import PdfiumReader
from kaagaz.reading.rapid_pool import RapidOcrPool
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.socket import PagePool, Reader, ReaderSocket
from kaagaz.reading.text_quality import PageSignals, TextLayerRules
from kaagaz.reading.text_reader import KINDS as TEXT_KINDS
from kaagaz.reading.text_reader import TextReader
from kaagaz.reading.xlsx_reader import XlsxLimits, XlsxReader
from kaagaz.settings import Settings

# What the PDF reader's route check says about one page, and the signals it saw.
PdfPageCheck = tuple[RouteDecision, PageSignals]
# The Gemini reader's module (PRD 5 G1): imported by this path only for OCR_ENGINE=gemini.
GEMINI_OCR_MODULE = "kaagaz.reading.gemini_ocr"


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
        "ocr_dpi_rule": settings.ocr_dpi_rule,
        "ocr_dpi_target_line_px": ocr_dpi.TARGET_LINE_PX,
        "ocr_dpi_probe_long_side": ocr_dpi.PROBE_LONG_SIDE,
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
        # Cost saving's limits, so ``cli cost-saving`` can tell runs read at other limits apart.
        "cost_saving_hard_lines_max": settings.cost_saving_hard_lines_max,
        "cost_saving_hard_share": settings.cost_saving_hard_share,
        "cost_saving_crop_pad": settings.cost_saving_crop_pad,
        # #384, #385: whether pages were routed, and at which thresholds.
        "page_routing": settings.page_routing,
        "page_route_clean_confidence": settings.page_route_clean_confidence,
        "page_route_min_words": settings.page_route_min_words,
        "page_route_min_line_px": settings.page_route_min_line_px,
        "page_route_height_spread": settings.page_route_height_spread,
        "page_route_table_share": settings.page_route_table_share,
        "page_route_region_lines_max": settings.page_route_region_lines_max,
        "page_route_region_share": settings.page_route_region_share,
        "page_route_region_pages_max": settings.page_route_region_pages_max,
    }


def pdf_text_layer_checks(settings: Settings, data: bytes) -> list[PdfPageCheck]:
    """What the PDF reader's route check (R2) says about each page, and the signals
    it saw: for ``cli measure`` only, which may not call a reader itself."""
    return text_layer_checks(data, rules=text_layer_rules(settings), max_pages=settings.max_pages)


def build_gemini_engine(settings: Settings, *, pool: RapidOcrPool, db: Database | None = None) -> PageEngine:
    """The Gemini reader for OCR_ENGINE=gemini (PRD 5 G1): ``GeminiEngine.from_settings``
    in ``GEMINI_OCR_MODULE``, imported here, on first need, and nowhere else.
    ``kaagaz.reading.ocr.GeminiEngineFactory`` in the product. #473: with Mistral set up (its
    key and zero data retention confirmed), its client too, for the documents on Mistral."""
    module = importlib.import_module(GEMINI_OCR_MODULE)
    key = settings.mistral_api_key if settings.mistral_ready else None
    mistral = None
    if key is not None:
        from kaagaz.reading.mistral_reader import MistralClient  # only where Mistral is set up

        mistral = MistralClient(key)
    engine: PageEngine = module.GeminiEngine.from_settings(settings, pool=pool, db=db, mistral=mistral)
    return engine


def gemini_reader_module() -> Any:
    """``GEMINI_OCR_MODULE`` itself, for ``verify-models`` only (model choice, council 11 Oct
    2026): a model is checked with the reader's own INSTRUCTION, PAGE_SCHEMA, ``checked``
    and ``pinned``. Imported here, on first need, as ``build_gemini_engine`` does."""
    return importlib.import_module(GEMINI_OCR_MODULE)


def build_ocr_engine(settings: Settings, *, db: Database | None = None) -> OcrEngine | None:
    """The engine for OCR_ENGINE (``ocr.build_ocr_engine``), the Gemini reader from
    ``build_gemini_engine``; None for "none". For the worker and ``cli measure``."""
    return _ocr_engine(
        settings, gemini=lambda configured, pool: build_gemini_engine(configured, pool=pool, db=db)
    )


def page_pool(settings: Settings, engine: OcrEngine | None) -> PagePool | None:
    """The threads a document's ocr pages are read on (G8): OCR_PAGE_CONCURRENCY of them
    for the Gemini reader; None, one page at a time as before, for any other engine,
    for no engine, or for a concurrency of 1."""
    if engine is None or settings.ocr_engine != GEMINI or settings.ocr_page_concurrency < 2:
        return None
    return PagePool.threads(settings.ocr_page_concurrency)


def build_reader_socket(settings: Settings, ocr: OcrEngine | None) -> ReaderSocket:
    pages = page_pool(settings, ocr)
    readers: dict[str, Reader] = {
        resolve.CSV: CsvReader(max_cells=settings.csv_max_cells),
        **{kind: TextReader(kind) for kind in sorted(TEXT_KINDS)},
        resolve.XLSX: XlsxReader(
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
            dpi_rule=settings.ocr_dpi_rule,
            pages=pages,
        ),
    }
    if ocr is not None:
        images = ImageReader(
            ocr,
            max_pages=settings.max_pages,
            max_image_pixels=settings.max_image_pixels,
            ocr_dpi=settings.ocr_dpi,
            blank_stddev=settings.blank_page_stddev,
            dpi_rule=settings.ocr_dpi_rule,
            pages=pages,
        )
        readers.update({sniff.PNG: images, sniff.JPEG: images, sniff.TIFF: images, sniff.HEIC: images})
    second_readers: dict[str, Reader] = {sniff.PDF: PdfiumReader(max_pages=settings.max_pages)}
    return ReaderSocket(readers, second_readers)
