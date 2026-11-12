"""A Word or PowerPoint file's PDF rendition (#522) is read from its text layer with no OCR and
no AI wherever the file holds its text, Marathi and Hindi included; a PDF upload is read as
before.

LibreOffice gives some Devanagari glyphs their text only by /ActualText, which pdfplumber
skips: it reads "(cid:N)" gaps and letters out of order, so the route check sent such a page to
OCR, at a page's OCR cost. PDFium reads /ActualText, but drops the space after some vowel
signs; a rendition's PDFium reading splits words at the gaps between them.

The PDFs here were made on 6 Nov 2026 by LibreOffice 25.2.3.2 in the backend image (Noto Sans
Devanagari), as the worker makes them: ``marathi-letter.pdf`` is ``test_office_real.MARATHI``
four times and ``hindi-letter.pdf`` is ``HINDI`` four times, one paragraph each, saved as a
.docx by LibreOffice, then converted by ``office.OfficeConverter``. Both are one page."""

import unicodedata
from pathlib import Path

import pytest
from PIL import Image

from kaagaz.ingestion import sniff
from kaagaz.pipeline import PageRecord, _without_unread_pages
from kaagaz.preparing.pages import open_pdf
from kaagaz.reading import office
from kaagaz.reading.ocr import OcrLine
from kaagaz.reading.pdf_second_reader import PdfiumReader, page_spans
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.socket import ReaderSocket
from kaagaz.reading.spans import BoxLocation, PageInfo, Span
from kaagaz.reading.wiring import build_reader_socket
from kaagaz.scanning.gate import DocumentStatus
from tests.fakes import MemoryStorage
from tests.pdf_builder import Page, Text, make_pdf
from tests.settings_factory import make_settings
from tests.test_office_real import MARATHI
from tests.test_pipeline import FakeConverter
from tests.test_pipeline_gemini import TOTAL, FakeChecker, ScriptedReader, page_read, rig
from tests.xlsx_builder import make_zip

FIXTURES = Path(__file__).with_name("fixtures") / "office"
HINDI = (
    "यह किराया समझौता पुणे में मकान मालिक और किरायेदार के बीच किया गया है। किराया हर महीने की पाँच"
    " तारीख को दिया जाएगा।"
)
LETTERS = {"marathi-letter.pdf": MARATHI, "hindi-letter.pdf": HINDI}
LATIN = "This rent agreement is made at Pune between the owner and the tenant of the flat, for 5000 rupees."


class CountingOcr:
    """An OCR engine (the AI reader or RapidOCR) that notes every page it is asked to read."""

    name = "counting-ocr"
    version = "1"

    def __init__(self) -> None:
        self.pages = 0

    def read(self, image: Image.Image) -> list[OcrLine]:
        self.pages += 1
        return [OcrLine("read by OCR", 0.0, 0.0, 10.0, 10.0, 0.99)]


def socket_with(ocr: CountingOcr) -> ReaderSocket:
    return build_reader_socket(make_settings(), ocr)


def words(spans: list[Span]) -> list[str]:
    return unicodedata.normalize("NFC", " ".join(span.text for span in spans)).split()


@pytest.mark.parametrize("name", sorted(LETTERS))
def test_a_marathi_or_hindi_word_files_pdf_is_read_from_its_text_layer_with_no_ocr(name: str) -> None:
    pdf = (FIXTURES / name).read_bytes()
    ocr, routes = CountingOcr(), list[RouteDecision | None]()
    spans = socket_with(ocr).read(office.RENDITION, pdf, on_page=routes.append)
    assert ocr.pages == 0
    assert routes == [RouteDecision("text_layer", "pdfium_text")]
    assert words(spans) == (" ".join([LETTERS[name]] * 4)).split()  # every word, each on its own


@pytest.mark.parametrize("name", sorted(LETTERS))
def test_a_pdf_upload_is_read_as_before_its_gaps_sent_to_ocr(name: str) -> None:
    # The same file uploaded as a PDF: nothing says how it was made, so its route check sends
    # the page to OCR, as before (#522 changes renditions only).
    pdf = (FIXTURES / name).read_bytes()
    ocr, routes = CountingOcr(), list[RouteDecision | None]()
    spans = socket_with(ocr).read(sniff.PDF, pdf, on_page=routes.append)
    assert ocr.pages == 1 and routes == [RouteDecision("ocr", "bad_chars")]
    assert [span.text for span in spans] == ["read by OCR"]


def test_a_rendition_page_pdfplumber_reads_right_is_read_as_a_pdfs_page() -> None:
    # A Latin page: both readers read the same letters, so pdfplumber's reading stands, as for a
    # PDF, and the second reader stays an independent reading of it.
    pdf = make_pdf([Page(texts=[Text(72, 700, LATIN)])])
    socket = socket_with(CountingOcr())
    as_rendition: list[RouteDecision | None] = []
    as_pdf: list[RouteDecision | None] = []
    read = socket.read(office.RENDITION, pdf, on_page=as_rendition.append)
    assert read == socket.read(sniff.PDF, pdf, on_page=as_pdf.append)
    assert as_rendition == as_pdf == [RouteDecision("text_layer", "ok")]
    assert socket.reader_id(office.RENDITION, second=True) == socket.reader_id(sniff.PDF, second=True)


def test_pdfium_drops_the_space_after_a_vowel_sign_and_a_renditions_reading_splits_at_the_gap() -> None:
    pdf = (FIXTURES / "marathi-letter.pdf").read_bytes()
    document = open_pdf(pdf)
    try:
        page = document[0]
        try:
            joined, split = words(page_spans(page, 1)), words(page_spans(page, 1, word_gaps=True))
        finally:
            page.close()
    finally:
        document.close()
    assert "पुणेयेथे" in joined and "दिलेजाईल." in joined  # PDFium's own words
    assert split == (" ".join([MARATHI] * 4)).split()
    # The second reader of every PDF reads as before.
    assert words(PdfiumReader(max_pages=10).read(pdf)) == joined


def test_a_rendition_page_whose_text_layer_neither_reader_can_use_still_goes_to_ocr() -> None:
    # A page of a few characters: too few for the route check, whoever read them.
    pdf = make_pdf([Page(texts=[Text(72, 700, "Rent 5000")])])
    ocr, routes = CountingOcr(), list[RouteDecision | None]()
    socket_with(ocr).read(office.RENDITION, pdf, on_page=routes.append)
    assert ocr.pages == 1 and routes == [RouteDecision("ocr", "few_chars")]


def test_a_marathi_word_file_reaches_ready_with_no_ocr_or_ai_call() -> None:
    # The worker's steps, LibreOffice faked by the PDF it made of the Marathi letter, with an AI
    # reader and RapidOCR's checker that note every call: none is made.
    reader, checker = ScriptedReader({1: page_read(TOTAL)}), FakeChecker()
    rendition = (FIXTURES / "marathi-letter.pdf").read_bytes()
    system = rig(reader, checker=checker, office=FakeConverter(rendition), renditions=MemoryStorage())
    job = system.run(make_zip({"word/document.xml": b"<w/>"}))
    assert system.status(job) == DocumentStatus.READY
    assert reader.read_inside == [] and checker.crops == []  # no page to the AI reader, no crop checked
    [page] = system.pages(job)
    assert (page.route, page.read_by) == ("text_layer", "text_layer")
    text = " ".join(piece.text for piece in system.store.for_document(job.customer_id, job.document_id))
    assert set(MARATHI.split()) <= set(unicodedata.normalize("NFC", text).split())
    first, second = system.store.outputs_for(job.customer_id, job.document_id)
    assert first.version.endswith(";pdfium_text=1") and second.reader == "pdfium"


def test_a_page_read_from_pdfiums_text_is_never_compared_with_pdfiums_second_reading() -> None:
    # The second reader is PDFium too: there it could only agree with itself (#522).
    pages = [PageRecord(PageInfo(n, 612.0, 792.0, "pt"), True, route="text_layer") for n in (1, 2)]
    second = [Span(f"line {n}", f"line {n}", BoxLocation(n, 0.1, 0.1, 0.5, 0.2)) for n in (1, 2)]
    reads = [RouteDecision("text_layer", "pdfium_text"), RouteDecision("text_layer", "ok")]
    assert _without_unread_pages(second, pages, reads) == second[1:]
    assert _without_unread_pages(second, pages) == second  # a PDF's: as before
