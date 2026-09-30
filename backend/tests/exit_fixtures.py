"""The six files of the PRD 2 exit test, built in code (test-engineer amendment 3).

PRD 2 §2 lists them:

1. **The 3-page textile invoice** (a digital PDF). Line items run from page 1 to
   page 3, each page break has a "Carried forward" and a "Brought forward" row,
   item 3's description wraps onto a second line, and the subtotal, CGST, SGST
   and grand total are on page 3. Its items sum to the subtotal (10,000.00),
   and 10,000.00 + 900.00 + 900.00 = 11,800.00. ``textile_invoice_pdf`` also
   builds the mismatched copy (one item amount changed) and a copy without the
   SGST line.
2. **A scanned Hindi retail bill** (a JPG).
3. **A scanned Marathi land record** (a PDF whose one page is a picture, with no
   text layer). It names a former owner as well as the current one (the PRD
   edge case: the card lists every name it can locate).
4. **A bank statement** (an XLSX sheet).
5. **A digital PDF whose text layer is broken**: Hindi typed in the legacy
   KrutiDev010 font, so the text layer holds ASCII such as "Hkkjr ljdkj" (ML
   amendment 3). The font is named but not embedded, with its widths, the way
   PR 3's ``pdf_builder.Page.font_name`` writes one. PR 3 is not in this branch
   yet, so ``broken_font_pdf`` writes the objects itself; it switches to
   ``Page(font_name=...)`` once PR 3 is merged below it.
6. **The 20-page report (C7)**: a "Page n of 20" footer on every page, page 15
   ends with "Profit of the month Sept is" and page 16 starts with "$12,000".
   Every page has at least 40 letters and digits, so it stays text_layer
   (test-engineer amendment 15).

The scans carry no readable text, so their pictures are dark bars where the
lines are (never Devanagari drawn with Pillow, test-engineer amendment 9), and
``ScriptedOcr`` stands in for the OCR engine: it answers each page it is given
with that file's lines, their boxes scaled to the picture. The Devanagari text
is written by hand and is well formed.

The two GSTINs are made up. Their check characters were worked out with
``checks.gstin_check_char``, which ``test_extraction_checks.py`` proves against a
separate implementation.

``QuotingChat`` stands in for the model on invoice windows: it answers each
window with the canned quotes that the window's text holds, the way a model
copies what it sees. So windows that overlap return the same line twice.
"""

import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw

from kaagaz.extraction.invoice import INVOICE_SYSTEM_PROMPT
from kaagaz.ingestion import sniff
from kaagaz.reading.ocr import OcrLine
from kaagaz.reading.pdf_reader import PdfReader
from kaagaz.reading.pdf_second_reader import PdfiumReader
from kaagaz.reading.spans import Span
from tests.pdf_builder import Page, Picture, Text, _assemble, _stream, make_pdf
from tests.xlsx_builder import make_xlsx, sheet_xml

SELLER_GSTIN = "24ABCDE1234F1Z6"
BUYER_GSTIN = "27PQRST5678K1Z2"

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of the page, top left origin


# --- 1. the textile invoice ------------------------------------------------------------


def textile_pages(*, silk_amount: str = "3,000.00", sgst: bool = True) -> list[list[str]]:
    """The invoice's text lines, page by page. ``silk_amount`` is item 4's printed amount."""
    page_1 = [
        "Shree Ganesh Textiles",
        "12 Cloth Market, Ring Road, Surat 395002",
        f"GSTIN: {SELLER_GSTIN}",
        "TAX INVOICE",
        "Invoice No: TX-2026-118",
        "Invoice Date: 12/08/2026",
        "Bill To: Patil Garments",
        f"Buyer GSTIN: {BUYER_GSTIN}",
        "S.No Description HSN Qty Unit Rate Amount",
        "1 Cotton Poplin 5208 120 m 25.00 3,000.00",
        "2 Rayon Print 5407 80 m 37.50 3,000.00",
        "Carried forward 6,000.00",
        "Page 1 of 3",
    ]
    page_2 = [
        "Shree Ganesh Textiles Invoice TX-2026-118 (continued)",
        "S.No Description HSN Qty Unit Rate Amount",
        "Brought forward 6,000.00",
        "3 Sub/Fabric Lining (cotton",
        "twill) 5208 50 m 20.00 1,000.00",
        "Carried forward 7,000.00",
        "Page 2 of 3",
    ]
    page_3 = [
        "Brought forward 7,000.00",
        f"4 Silk Blend 5007 20 m 150.00 {silk_amount}",
        "Sub Total: 10,000.00",
        "CGST: 900.00",
        *(["SGST: 900.00"] if sgst else []),
        "Grand Total: 11,800.00",
        "Amount in words: Rupees Eleven Thousand Eight Hundred Only",
        "Page 3 of 3",
    ]
    return [page_1, page_2, page_3]


def textile_invoice_pdf(*, silk_amount: str = "3,000.00", sgst: bool = True) -> bytes:
    """The invoice as a digital PDF: 10 pt Helvetica, one text line every 22 points."""
    return _text_pdf(textile_pages(silk_amount=silk_amount, sgst=sgst))


def textile_spans(*, silk_amount: str = "3,000.00", sgst: bool = True) -> list[Span]:
    """The first reader's spans of the invoice: its text layer read by the real PdfReader."""
    reader = PdfReader(None, max_pages=10, dpi=100, max_image_pixels=10**8)
    return reader.read(textile_invoice_pdf(silk_amount=silk_amount, sgst=sgst))


def textile_second_spans(*, silk_amount: str = "3,000.00", sgst: bool = True) -> list[Span]:
    """The second reader's spans of the invoice: its text layer read by PDFium."""
    return PdfiumReader(max_pages=10).read(textile_invoice_pdf(silk_amount=silk_amount, sgst=sgst))


# What a model that copies correctly returns for the textile invoice (E2's shape).
# There is no explicit total-tax line, so tax_total is null (ML amendment 5).
TEXTILE_FIELDS: dict[str, tuple[str | None, str] | None] = {
    "invoice_number": ("Invoice No", "TX-2026-118"),
    "invoice_date": ("Invoice Date", "12/08/2026"),
    "seller_name": (None, "Shree Ganesh Textiles"),
    "seller_gstin": ("GSTIN", SELLER_GSTIN),
    "buyer_name": ("Bill To", "Patil Garments"),
    "buyer_gstin": ("Buyer GSTIN", BUYER_GSTIN),
    "subtotal": ("Sub Total", "10,000.00"),
    "tax_total": None,
    "total": ("Grand Total", "11,800.00"),
}


def textile_line(description: str, hsn: str, quantity: str, rate: str, amount: str) -> dict[str, str | None]:
    return {
        "description_quote": description,
        "hsn_quote": hsn,
        "quantity_quote": quantity,
        "unit_quote": "m",
        "rate_quote": rate,
        "amount_quote": amount,
    }


def textile_lines(*, silk_amount: str = "3,000.00") -> list[dict[str, str | None]]:
    return [
        textile_line("Cotton Poplin", "5208", "120", "25.00", "3,000.00"),
        textile_line("Rayon Print", "5407", "80", "37.50", "3,000.00"),
        textile_line("Sub/Fabric Lining (cotton twill)", "5208", "50", "20.00", "1,000.00"),
        textile_line("Silk Blend", "5007", "20", "150.00", silk_amount),
    ]


# --- 2. the scanned Hindi retail bill -------------------------------------------------------

HINDI_BILL_LINES = (
    "शर्मा किराना स्टोर",
    "बिल नं. 245",
    "दिनांक 05/09/2026",
    "चावल 5 किलो 300.00",
    "दाल 2 किलो 150.00",
    "कुल ₹ 450.00",
)


def hindi_bill_jpg() -> bytes:
    """A phone photo of the bill: a white page with a dark bar where each line is."""
    out = io.BytesIO()
    _scan_picture(_lines_on_page(HINDI_BILL_LINES), 620, 877).save(out, format="JPEG", quality=85)
    return out.getvalue()


# --- 3. the scanned Marathi land record -----------------------------------------------------

LAND_RECORD_LINES = (
    "गाव नमुना सात (७/१२ उतारा)",
    "गाव: शिरोळ तालुका: हवेली जिल्हा: पुणे",
    "गट क्रमांक: ४५/२",
    "भोगवटादाराचे नाव: पाटील रामचंद्र गणपत",
    "पूर्वीचे नाव: शिंदे विठ्ठल बाबुराव",
    "क्षेत्र: १.२० हेक्टर",
    "दिनांक: १२/०८/२०२६",
)


def land_record_pdf() -> bytes:
    """A scanned PDF: one A4 page that is a picture only, so it has no text layer."""
    picture = _scan_picture(_lines_on_page(LAND_RECORD_LINES), 413, 535)
    return make_pdf([Page(picture=Picture(picture, 0, 0, 612, 792))])


# --- 4. the bank statement ----------------------------------------------------------------

BANK_STATEMENT_ROWS: tuple[tuple[str | float, ...], ...] = (
    ("Date", "Narration", "Debit", "Credit", "Balance"),
    ("01/08/2026", "Opening balance", "", "", 50000.0),
    ("03/08/2026", "NEFT from Patil Garments", "", 11800.0, 61800.0),
    ("10/08/2026", "Rent for August", 15000.0, "", 46800.0),
    ("25/08/2026", "UPI to Shree Ganesh Textiles", 10000.0, "", 36800.0),
)


def bank_statement_xlsx() -> bytes:
    """One sheet: a header row, then one row per entry; text cells inline, amounts as numbers."""
    rows = []
    for r, cells in enumerate(BANK_STATEMENT_ROWS, start=1):
        xml = []
        for c, cell in enumerate(cells):
            ref = f"{'ABCDE'[c]}{r}"
            if isinstance(cell, float):
                xml.append(f'<c r="{ref}"><v>{cell:g}</v></c>')
            elif cell:
                xml.append(f'<c r="{ref}" t="inlineStr"><is><t>{cell}</t></is></c>')
        rows.append(f'<row r="{r}">{"".join(xml)}</row>')
    return make_xlsx([("Statement", sheet_xml("".join(rows)))])


# --- 5. the digital PDF with a broken (legacy font) text layer ----------------------------------

LEGACY_FONT = "KrutiDev010"
# Hindi typed with KrutiDev010's keyboard: the text layer holds this ASCII.
BROKEN_FONT_TEXT = (
    "Hkkjr ljdkj",
    "dk;kZy; vkns'k la[;k 45",
    "fnukad 12@08@2026",
    "jkT; foRr foHkkx] eqacbZ ds vkns'k ds vuqlkj",
)
# What OCR reads from the page as drawn.
BROKEN_FONT_OCR = (
    "भारत सरकार",
    "कार्यालय आदेश संख्या 45",
    "दिनांक 12/08/2026",
    "राज्य वित्त विभाग, मुंबई के आदेश के अनुसार",
)


def broken_font_pdf() -> bytes:
    """One page in the named, not embedded, TrueType font LEGACY_FONT (widths 500, WinAnsi)."""
    widths = b" ".join([b"500"] * 95)
    font = (
        b"<< /Type /Font /Subtype /TrueType /BaseFont /%s /FirstChar 32 /LastChar 126 /Widths [%s]"
        b" /FontDescriptor 4 0 R /Encoding /WinAnsiEncoding >>" % (LEGACY_FONT.encode(), widths)
    )
    descriptor = (
        b"<< /Type /FontDescriptor /FontName /%s /Flags 32 /FontBBox [0 -200 1000 900]"
        b" /ItalicAngle 0 /Ascent 900 /Descent -200 /CapHeight 700 /StemV 80 >>" % LEGACY_FONT.encode()
    )
    content = "".join(
        f"BT /F1 12 Tf 1 0 0 1 50 {760 - 22 * i} Tm ({text}) Tj ET\n"
        for i, text in enumerate(BROKEN_FONT_TEXT)
    )
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 5 0 R"
    page += b" /Resources << /Font << /F1 3 0 R >> >> >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [6 0 R] /Count 1 >>",
        font,
        descriptor,
        _stream(b"", content.encode("ascii")),
        page,
    ]
    return _assemble(objects, b"")


# --- 6. the 20-page report (C7) -----------------------------------------------------------------

REPORT_PAGES = 20
PROFIT_LABEL = "Profit of the month Sept is"
PROFIT_VALUE = "$12,000"


def report_pages() -> list[list[str]]:
    """Each page's body lines, top to bottom; the footer is added by ``report_pdf``."""
    pages: list[list[str]] = []
    for n in range(1, REPORT_PAGES + 1):
        body = [f"Section {n}: sales and operations for the month, with notes on stock and staff."]
        if n == 1:
            body.insert(0, "Monthly Operations Report 2026")
        if n == 15:
            body += ["Costs stayed close to the plan through the quarter.", PROFIT_LABEL]
        if n == 16:
            body = [PROFIT_VALUE, *body]
        pages.append(body)
    return pages


def report_pdf() -> bytes:
    """The report as a digital PDF: the body lines from the top, the footer near the bottom."""
    pages = []
    for n, body in enumerate(report_pages(), start=1):
        texts = [Text(50, 760 - 22 * i, line, 10) for i, line in enumerate(body)]
        texts.append(Text(50, 30, f"Page {n} of {REPORT_PAGES}", 8))
        pages.append(Page(texts=texts))
    return make_pdf(pages)


# --- all six ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExitFile:
    name: str  # the file name it is uploaded as
    detected_type: str  # what kaagaz.ingestion.sniff gives for it
    data: bytes
    ocr_pages: tuple[tuple[tuple[str, Box], ...], ...] = ()  # what OCR reads, per scanned page


def exit_files() -> dict[str, ExitFile]:
    """The six exit-test files by key, plus the textile invoice's mismatched copy."""
    return {
        "textile_invoice": ExitFile("textile-invoice.pdf", sniff.PDF, textile_invoice_pdf()),
        "textile_invoice_mismatched": ExitFile(
            "textile-invoice-copy.pdf", sniff.PDF, textile_invoice_pdf(silk_amount="3,500.00")
        ),
        "hindi_retail_bill": ExitFile(
            "retail-bill.jpg", sniff.JPEG, hindi_bill_jpg(), (_lines_on_page(HINDI_BILL_LINES),)
        ),
        "marathi_land_record": ExitFile(
            "land-record.pdf", sniff.PDF, land_record_pdf(), (_lines_on_page(LAND_RECORD_LINES),)
        ),
        "bank_statement": ExitFile("bank-statement.xlsx", sniff.ZIP, bank_statement_xlsx()),
        "broken_font_pdf": ExitFile(
            "office-order.pdf", sniff.PDF, broken_font_pdf(), (_lines_on_page(BROKEN_FONT_OCR),)
        ),
        "report": ExitFile("monthly-report.pdf", sniff.PDF, report_pdf()),
    }


class ScriptedOcr:
    """An OCR engine (kaagaz.reading.ocr.OcrEngine) that answers the k-th page it is given
    with ``pages[k]``, each box scaled to that picture, at confidence 0.95. Past the script,
    a page reads as nothing. ``seen`` records each picture's (width, height)."""

    name = "scripted-ocr"
    version = "1"

    def __init__(self, pages: Sequence[Sequence[tuple[str, Box]]]) -> None:
        self.pages = [list(page) for page in pages]
        self.seen: list[tuple[int, int]] = []

    def read(self, image: Image.Image) -> list[OcrLine]:
        self.seen.append(image.size)
        if len(self.seen) > len(self.pages):
            return []
        width, height = image.size
        return [
            OcrLine(text, x0 * width, y0 * height, x1 * width, y1 * height, 0.95)
            for text, (x0, y0, x1, y1) in self.pages[len(self.seen) - 1]
        ]


# --- the model ----------------------------------------------------------------------------------


class QuotingChat:
    """The model (``chat_json``). Each invoice window is answered with every canned field
    whose quotes the window's text holds (else null), and every canned line whose
    description it holds, all its quotes as given. Any other call (the document card) gets
    ``card``. Records each call as a dict (system, user, schema, max_tokens); raises
    ``error`` on call number ``fail_at`` (from 1)."""

    chat_model = "fake-chat"

    def __init__(
        self,
        fields: Mapping[str, tuple[str | None, str] | None],
        lines: Sequence[Mapping[str, str | None]],
        *,
        card: Mapping[str, Any] | None = None,
        fail_at: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self.fields = dict(fields)
        self.lines = [dict(line) for line in lines]
        self.card = card
        self.fail_at = fail_at
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def chat_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int | None = None
    ) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "schema": schema, "max_tokens": max_tokens})
        if self.fail_at == len(self.calls) and self.error is not None:
            raise self.error
        if system != INVOICE_SYSTEM_PROMPT:
            if self.card is None:
                raise AssertionError("QuotingChat got a card call but has no card reply")
            return dict(self.card)
        text = window_text(user)
        fields: dict[str, Any] = {}
        for name, quotes in self.fields.items():
            if quotes is not None and all(quote is None or holds(text, quote) for quote in quotes):
                fields[name] = {"label_quote": quotes[0], "value_quote": quotes[1]}
            else:
                fields[name] = None
        lines = [line for line in self.lines if holds(text, line["description_quote"] or "")]
        return {"fields": fields, "lines": lines}


def window_text(user: str) -> str:
    """The document text of one window prompt, with the prompt's escapes undone."""
    fenced = user.split("<document>\n", 1)[1].rsplit("\n</document>", 1)[0]
    return fenced.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def holds(text: str, quote: str) -> bool:
    """Whether ``text`` holds ``quote``, any run of whitespace matching any other."""
    return bool(quote) and " ".join(quote.split()) in " ".join(text.split())


# --- helpers ------------------------------------------------------------------------------------


def _text_pdf(pages: Sequence[Sequence[str]]) -> bytes:
    """10 pt Helvetica, one text line every 22 points from the top."""
    return make_pdf(
        [Page(texts=[Text(50, 760 - 22 * i, line, 10) for i, line in enumerate(lines)]) for lines in pages]
    )


def _lines_on_page(lines: Sequence[str]) -> tuple[tuple[str, Box], ...]:
    """Each line with its box: left aligned, one line every 8% of the page from the top.
    PROVISIONAL: the width follows the line's length, as a scan's text would."""
    return tuple(
        (text, (0.08, 0.08 + 0.08 * i, min(0.92, 0.08 + 0.02 * len(text)), 0.12 + 0.08 * i))
        for i, text in enumerate(lines)
    )


def _scan_picture(lines: Sequence[tuple[str, Box]], width: int, height: int) -> Image.Image:
    """A white page with one dark bar for each line's box: text-like, never blank."""
    picture = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(picture)
    for _, (x0, y0, x1, y1) in lines:
        draw.rectangle((x0 * width, y0 * height, x1 * width, y1 * height), fill=(40, 40, 40))
    return picture
