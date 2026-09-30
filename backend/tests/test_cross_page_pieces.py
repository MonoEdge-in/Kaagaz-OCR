"""Pieces across pages, from real PDFs built in code (PRD 2 C1, C2 and C7).

C7 is the PRD's required fixture: a 20-page report with a "Page n of 20"
footer on every page, where page 15 ends with "Profit of the month Sept is"
and page 16 starts with "$12,000". Every page has 40 or more letters and
digits, so the page router (R2) keeps it on the text layer.
"""

import io
import unittest

from PIL import Image, ImageDraw

from kaagaz.chunking.lines import pieces_from_lines
from kaagaz.chunking.piece import Piece
from kaagaz.ingestion import sniff
from kaagaz.reading.ocr import OcrEngine, OcrLine
from kaagaz.reading.pdf_reader import PdfReader
from kaagaz.reading.socket import ReaderSocket
from kaagaz.reading.spans import Span
from tests.pdf_builder import Page, Picture, Text, make_pdf
from tests.test_pipeline import CUSTOMER, build

PAGES = 20
PROFIT, AMOUNT = "Profit of the month Sept is", "$12,000"


def report_pdf() -> bytes:
    pages = []
    for n in range(1, PAGES + 1):
        texts = [Text(72, 30, f"Page {n} of {PAGES}", size=9)]
        if n == 15:  # a paragraph, then after a blank gap the sentence that runs on
            texts += [Text(72, 700, "Expenses of the month Sept were checked by the accountant.")]
            texts += [Text(72, 90, PROFIT)]
        elif n == 16:
            texts += [Text(72, 740, AMOUNT), Text(72, 726, "after tax, which is 8% more than August 2026.")]
            texts += [Text(72, 500, "Sales in all the branches were steady this month.")]
        else:
            texts += [Text(72, 700, f"Branch report, page {n}: the sales and the stock were counted.")]
            texts += [Text(72, 686, "All the figures are in rupees and were checked twice.")]
        pages.append(Page(texts=texts))
    return make_pdf(pages)


def pdf_reader(ocr: OcrEngine | None = None) -> PdfReader:
    return PdfReader(ocr, max_pages=PAGES, dpi=72, max_image_pixels=10_000_000)


def read_pdf(data: bytes, ocr: OcrEngine | None = None) -> list[Span]:
    return pdf_reader(ocr).read(data)


def holding(pieces: list[Piece], text: str) -> list[Piece]:
    return [p for p in pieces if text in p.text]


class CrossPageFixtureTest(unittest.TestCase):
    """C7."""

    def test_the_sentence_across_pages_15_and_16_is_one_piece_with_no_footer(self) -> None:
        spans = read_pdf(report_pdf())
        pieces = pieces_from_lines("cust-a", "doc-1", spans)

        (piece,) = holding(pieces, PROFIT)
        self.assertEqual(holding(pieces, AMOUNT), [piece])  # exactly one piece holds both
        self.assertIn(f"{PROFIT}\n{AMOUNT}", piece.text)  # nothing in between
        self.assertEqual((piece.page_from, piece.page_to), (15, 16))
        self.assertEqual({region["page"] for region in piece.regions()}, {15, 16})
        self.assertEqual(holding(pieces, "Page 15 of 20"), [])
        self.assertEqual(holding(pieces, f"of {PAGES}"), [])  # no footer in any piece

    def test_every_page_has_40_letters_and_digits_so_it_stays_on_the_text_layer(self) -> None:
        by_page: dict[int, int] = {}
        for span in read_pdf(report_pdf()):
            if not span.text.startswith("Page "):  # the footer alone must not be what counts
                page = span.location.page
                by_page[page] = by_page.get(page, 0) + sum(ch.isalnum() for ch in span.text)
        self.assertEqual(sorted(by_page), list(range(1, PAGES + 1)))
        self.assertGreaterEqual(min(by_page.values()), 40)

    def test_through_the_pipeline_the_footers_stay_in_the_reader_output(self) -> None:
        system = build(socket=ReaderSocket({sniff.PDF: pdf_reader()}))
        doc = system.upload.accept(CUSTOMER, io.BytesIO(report_pdf())).document_id
        system.worker.run_once()

        content = system.store.content(CUSTOMER, doc)
        assert content is not None
        (output,) = content.outputs
        footers = [span.text for span in output.spans if span.text.startswith("Page ")]
        self.assertEqual(footers, [f"Page {n} of {PAGES}" for n in range(1, PAGES + 1)])
        self.assertEqual(holding(content.pieces, f"of {PAGES}"), [])
        (piece,) = holding(content.pieces, PROFIT)
        self.assertEqual((piece.page_from, piece.page_to, holding(content.pieces, AMOUNT)), (15, 16, [piece]))


class ScanLines:
    """A fake OCR engine: the given lines, as fractions of whatever picture it is given."""

    name, version = "fake-ocr", "1"

    def __init__(self, lines: list[tuple[str, float]]) -> None:
        self.lines = lines
        self.calls = 0

    def read(self, image: Image.Image) -> list[OcrLine]:
        self.calls += 1
        w, h = image.width, image.height
        return [OcrLine(t, 0.1 * w, top * h, 0.9 * w, (top + 0.02) * h, 0.9) for t, top in self.lines]


def scan_picture() -> Image.Image:
    """Dark bars like lines of words on white paper, so the page is never blank (R3)."""
    image = Image.new("RGB", (200, 260), "white")
    draw = ImageDraw.Draw(image)
    for top in range(20, 240, 12):
        draw.rectangle((20, top, 180, top + 4), fill=(24, 24, 24))
    return image


class MixedPagesTest(unittest.TestCase):
    def test_a_pdf_mixing_text_and_scanned_pages_joins_across_the_switch(self) -> None:
        text_page = Page(
            texts=[
                Text(72, 720, "Stock register for the Pune branch, April 2026."),
                Text(72, 80, "Closing stock of cotton bales was"),
            ]
        )
        scanned = Page(picture=Picture(scan_picture(), 0, 0, 612, 792))  # no text layer
        scan_lines = ["1,240 bales, counted on 30 April.", "Signed by the store keeper."]
        engine = ScanLines([(scan_lines[0], 0.05), (scan_lines[1], 0.08)])
        spans = read_pdf(make_pdf([text_page, scanned]), engine)
        pieces = pieces_from_lines("cust-a", "doc-1", spans)

        self.assertEqual(engine.calls, 1)  # only page 2 was read by OCR
        (piece,) = holding(pieces, "Closing stock")
        self.assertEqual(piece.text, "\n".join(["Closing stock of cotton bales was", *scan_lines]))
        self.assertEqual((piece.page_from, piece.page_to), (1, 2))
        self.assertEqual(piece.label, "needs_review")  # part of it was read by OCR


if __name__ == "__main__":
    unittest.main()
