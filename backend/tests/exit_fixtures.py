"""Files for the PRD 2 exit test, built in code (test-engineer amendment 3).

**The 3-page textile invoice** (PRD 2 WP2.6): line items run from page 1 to
page 3, each page break has a "Carried forward" and a "Brought forward" row,
item 3's description wraps onto a second line, and the subtotal, CGST, SGST and
grand total are on page 3. Its items sum to the subtotal (10,000.00) and
10,000.00 + 900.00 + 900.00 = 11,800.00. ``textile_invoice_pdf`` also builds the
mismatched copy (one item amount changed) and a copy without the SGST line.

The two GSTINs are made up. Their check characters were worked out with
``checks.gstin_check_char``, which ``test_extraction_checks.py`` proves against a
separate implementation.

``QuotingChat`` stands in for the model on invoice windows: it answers each
window with the canned quotes that the window's text holds, the way a model
copies what it sees. So windows that overlap return the same line twice.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from kaagaz.reading.pdf_reader import PdfReader
from kaagaz.reading.spans import Span
from tests.pdf_builder import Page, Text, make_pdf

SELLER_GSTIN = "24ABCDE1234F1Z6"
BUYER_GSTIN = "27PQRST5678K1Z2"


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
    pages = textile_pages(silk_amount=silk_amount, sgst=sgst)
    return make_pdf(
        [Page(texts=[Text(50, 760 - 22 * i, line, 10) for i, line in enumerate(lines)]) for lines in pages]
    )


def textile_spans(*, silk_amount: str = "3,000.00", sgst: bool = True) -> list[Span]:
    """The first reader's spans of the invoice: its text layer read by the real PdfReader."""
    reader = PdfReader(None, max_pages=10, dpi=100, max_image_pixels=10**8)
    return reader.read(textile_invoice_pdf(silk_amount=silk_amount, sgst=sgst))


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


class QuotingChat:
    """The model on invoice windows (``chat_json``). Each window is answered with every
    canned field whose quotes the window's text holds (else null), and every canned line
    whose description it holds, all its quotes as given. Records each call as
    (system, user, schema); raises ``error`` on call number ``fail_at`` (from 1)."""

    chat_model = "fake-chat"

    def __init__(
        self,
        fields: Mapping[str, tuple[str | None, str] | None],
        lines: Sequence[Mapping[str, str | None]],
        *,
        fail_at: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self.fields = dict(fields)
        self.lines = [dict(line) for line in lines]
        self.fail_at = fail_at
        self.error = error
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def chat_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user, schema))
        if self.fail_at == len(self.calls) and self.error is not None:
            raise self.error
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
