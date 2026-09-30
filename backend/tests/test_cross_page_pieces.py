"""Pieces across pages, from real PDFs built in code (PRD 2 C1, C2 and C7).

C7 is the PRD's required fixture: a 20-page report with a "Page n of 20"
footer on every page, where page 15 ends with "Profit of the month Sept is"
and page 16 starts with "$12,000". Every page has 40 or more letters and
digits, so the page router (R2) keeps it on the text layer.
"""

import io
import unittest

from PIL import Image

from kaagaz.chunking.context import with_context
from kaagaz.chunking.lines import pieces_from_lines
from kaagaz.chunking.piece import Piece
from kaagaz.ingestion import sniff
from kaagaz.reading.ocr import OcrLine
from kaagaz.reading.pdf_reader import PdfReader
from kaagaz.reading.pdf_second_reader import PdfiumReader
from kaagaz.reading.socket import ReaderSocket
from kaagaz.reading.spans import PageInfo, Span
from tests.fakes import FakePrepared, FakePreparer
from tests.pdf_builder import Page, Picture, Text, make_pdf
from tests.picture_support import text_like_picture
from tests.test_pdf_reader import BLANK_PAGE_STDDEV, RULES
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


class ScanLines:
    """A fake OCR engine: the given lines, as fractions of whatever picture it is given."""

    name, version = "fake-ocr", "1"

    def __init__(self, lines: list[tuple[str, float]] | None = None) -> None:
        self.lines = lines or []
        self.calls = 0

    def read(self, image: Image.Image) -> list[OcrLine]:
        self.calls += 1
        w, h = image.width, image.height
        return [OcrLine(t, 0.1 * w, top * h, 0.9 * w, (top + 0.02) * h, 0.9) for t, top in self.lines]


def pdf_reader(ocr: ScanLines | None = None) -> PdfReader:
    """The first PDF reader with the shipped R2 rules (40 letters and digits)."""
    return PdfReader(
        ocr, max_pages=PAGES, dpi=72, max_image_pixels=10_000_000, rules=RULES, blank_stddev=BLANK_PAGE_STDDEV
    )


def read_pdf(data: bytes, ocr: ScanLines | None = None) -> list[Span]:
    return pdf_reader(ocr).read(data)


def pdf_pages(count: int) -> FakePreparer:
    return FakePreparer(
        {sniff.PDF: FakePrepared([PageInfo(n, 612.0, 792.0, "pt") for n in range(1, count + 1)], {})}
    )


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
        (amount,) = [span for span in spans if span.text == AMOUNT]
        self.assertIn(amount.location, piece.locations)  # "$12,000" can be highlighted on page 16
        self.assertEqual(holding(pieces, "Page 15 of 20"), [])
        self.assertEqual(holding(pieces, f"of {PAGES}"), [])  # no footer in any piece

    def test_every_page_has_40_letters_and_digits_and_stays_on_the_text_layer(self) -> None:
        engine = ScanLines()  # with an engine set, a page that failed R2 would be read by OCR
        spans = read_pdf(report_pdf(), engine)
        self.assertEqual(engine.calls, 0)
        self.assertEqual({span.origin for span in spans}, {"text_layer"})
        by_page: dict[int, int] = {}
        for span in spans:
            if not span.text.startswith("Page "):  # the footer alone must not be what counts
                page = span.location.page
                by_page[page] = by_page.get(page, 0) + sum(ch.isalnum() for ch in span.text)
        self.assertEqual(sorted(by_page), list(range(1, PAGES + 1)))
        self.assertGreaterEqual(min(by_page.values()), 40)

    def test_through_the_pipeline_all_20_pages_route_text_layer_and_the_footers_stay_in_the_output(
        self,
    ) -> None:
        system = build(socket=ReaderSocket({sniff.PDF: pdf_reader(ScanLines())}), preparer=pdf_pages(PAGES))
        doc = system.upload.accept(CUSTOMER, io.BytesIO(report_pdf())).document_id
        system.worker.run_once()

        content = system.store.content(CUSTOMER, doc)
        assert content is not None
        self.assertEqual([page.route for page in content.pages], ["text_layer"] * PAGES)
        (output,) = content.outputs
        footers = [span.text for span in output.spans if span.text.startswith("Page ")]
        self.assertEqual(footers, [f"Page {n} of {PAGES}" for n in range(1, PAGES + 1)])
        self.assertEqual(holding(content.pieces, f"of {PAGES}"), [])
        (piece,) = holding(content.pieces, PROFIT)
        self.assertEqual((piece.page_from, piece.page_to, holding(content.pieces, AMOUNT)), (15, 16, [piece]))


class MixedPagesTest(unittest.TestCase):
    def test_a_pdf_mixing_text_and_scanned_pages_joins_across_the_switch(self) -> None:
        text_page = Page(
            texts=[
                Text(72, 720, "Stock register for the Pune branch, April 2026."),
                Text(72, 80, "Closing stock of cotton bales was"),
            ]
        )
        scanned = Page(picture=Picture(text_like_picture(200, 260), 0, 0, 612, 792))  # no text layer
        scan_lines = ["1,240 bales, counted on 30 April.", "Signed by the store keeper."]
        engine = ScanLines([(scan_lines[0], 0.05), (scan_lines[1], 0.08)])
        spans = read_pdf(make_pdf([text_page, scanned]), engine)
        pieces = pieces_from_lines("cust-a", "doc-1", spans)

        self.assertEqual(engine.calls, 1)  # only page 2 was read by OCR
        origins = {(span.location.page, span.origin) for span in spans}
        self.assertEqual(origins, {(1, "text_layer"), (2, "ocr")})  # the routes differ per page
        (piece,) = holding(pieces, "Closing stock")
        self.assertEqual(piece.text, "\n".join(["Closing stock of cotton bales was", *scan_lines]))
        self.assertEqual((piece.page_from, piece.page_to), (1, 2))
        self.assertEqual(piece.label, "needs_review")  # part of it was read by OCR


class TextLayerHeadingsTest(unittest.TestCase):
    """C1 and C3 on a PDF's text layer: its headings come from the fonts (pdf_reader)."""

    def test_a_heading_at_the_bottom_of_page_15_leads_one_piece_with_its_paragraph_on_16(self) -> None:
        pages = [Page(texts=[Text(72, 740, "Annual Report", size=20), Text(72, 700, "For the owners.")])]
        for n in range(2, 17):
            texts = [Text(72, 700, f"Branch report for page {n}, all counted.")]
            texts += [Text(72, 686, "The figures are in rupees and were checked twice.")]
            if n == 15:
                texts.append(Text(72, 60, "Profit", size=16))
            if n == 16:
                texts = [Text(72, 740, "Profit of the month Sept was $12,000."), *texts]
            pages.append(Page(texts=texts))
        pieces = pieces_from_lines("cust-a", "doc-1", read_pdf(make_pdf(pages)))

        (piece,) = holding(pieces, "Profit")
        self.assertTrue(piece.text.startswith("Profit\nProfit of the month Sept was $12,000."))
        self.assertEqual((piece.page_from, piece.page_to), (15, 16))
        self.assertEqual(piece.section, ("Annual Report", "Profit"))
        (before,) = holding(pieces, "Branch report for page 15")  # the heading starts a new piece
        self.assertNotIn("Profit", before.text)

    def test_monthly_results_and_profit_split_into_sections_with_a_section_header(self) -> None:
        page = Page(
            texts=[
                Text(72, 740, "MONTHLY RESULTS", size=18),
                Text(72, 710, "Sales were 40,000 in every branch this month."),
                Text(72, 696, "Returns were low."),
                Text(72, 682, "PROFIT", bold=True),  # bold, no gap above it
                Text(72, 668, "Profit was 12,000 after tax."),
            ]
        )
        pieces = with_context(
            pieces_from_lines("cust-a", "doc-1", read_pdf(make_pdf([page]))),
            document_name="Sept report.pdf",
            doc_type=None,
            document_date=None,
        )
        self.assertEqual(
            [(p.text, p.section) for p in pieces],
            [
                (
                    "MONTHLY RESULTS\nSales were 40,000 in every branch this month.\nReturns were low.",
                    ("MONTHLY RESULTS",),
                ),
                ("PROFIT\nProfit was 12,000 after tax.", ("MONTHLY RESULTS", "PROFIT")),
            ],
        )
        self.assertIn(" | Section: MONTHLY RESULTS > PROFIT | Page 1 | ", pieces[1].context_header)


HINDI = ["पाटील ट्रेडर्स", "बिल संख्या १७ दिनांक १२/०८/२०२६", "ग्राहक का नाम शर्मा", "Total: १,१८०.५०"]
MARATHI = ["सातबारा उतारा", "गाव शिंदेवाडी तालुका हवेली", "क्षेत्र ७/१२ दिनांक", "खातेदार पाटील शिंदे"]


class DevanagariTextLayerTest(unittest.TestCase):
    def test_hindi_and_marathi_text_layers_stay_text_layer_and_both_readers_agree(self) -> None:
        pages = [
            Page(texts=[Text(72, 700 - 20 * i, t, devanagari=True) for i, t in enumerate(ts)])
            for ts in (HINDI, MARATHI)
        ]
        data = make_pdf(pages)
        engine = ScanLines()
        first = read_pdf(data, engine)
        self.assertEqual(engine.calls, 0)  # R2 kept both pages: Devanagari is good text
        self.assertEqual([span.text for span in first], HINDI + MARATHI)  # read back exactly
        self.assertEqual([span.text for span in PdfiumReader(max_pages=2).read(data)], HINDI + MARATHI)

        socket = ReaderSocket({sniff.PDF: pdf_reader(ScanLines())}, {sniff.PDF: PdfiumReader(max_pages=2)})
        system = build(socket=socket, preparer=pdf_pages(2))
        doc = system.upload.accept(CUSTOMER, io.BytesIO(data)).document_id
        system.worker.run_once()
        content = system.store.content(CUSTOMER, doc)
        assert content is not None
        self.assertEqual([page.route for page in content.pages], ["text_layer", "text_layer"])
        (total,) = [v for v in content.values if v.field == "total_amount"]
        self.assertEqual((total.value_text, str(total.value_number)), ("१,१८०.५०", "1180.50"))
        self.assertEqual((total.label, total.label_reason), ("verified", "readers_agree"))
        self.assertEqual(content.facts.languages, ("other",))
        self.assertTrue(all(p.text_latin for p in content.pieces))


if __name__ == "__main__":
    unittest.main()
