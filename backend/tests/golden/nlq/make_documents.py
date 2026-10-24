"""The NLQ eval's sample documents (PRD 6 package G): made up for the purpose, never a
customer's. Every person, firm, address and figure is invented.

    cd backend
    python -m tests.golden.nlq.make_documents   # rewrites the files and documents.sha256

30 documents: six 7/12 extracts (one owner; two at 50/50; three with printed shares; shares
not printed; the unallotted share printed; 50% and 30% with the rest not stated), four sale
deeds and a property card (Maval, Khalapur, Panvel, Thane, Pune city; Marathi in Devanagari
and English), 14 invoices of Sahyadri Agro Traders (eight in September 2026, three in August,
two in July, one in September 2025; some print a profit), a profit and loss register (CSV,
so its cells are Verified, ``direct_cell``), a rent agreement and a leave and licence
agreement (the soft rejection traps), a quotation and an electricity bill. Two people share
the first name Aniket; Hrishikesh Ghatge is spelled Rushikesh Ghatge in two documents.

PDFs have a text layer (tests/pdf_builder.py: Helvetica in cp1252, so "Rs." for the rupee
sign; a line with Devanagari uses the Type0 font). INV-2057 is forced to Needs review: its
items do not add up to its subtotal (the invoice checks fail, as exit_fixtures' mismatched
copy) and its profit shares a line with the total (one box, two values).

``DOCUMENTS`` is the truth table the questions are checked against: each document's pages,
its figures as printed with their page, and the Latin names of a Devanagari one. The set is
small enough for whole-document mode, so it is run twice: at ANSWER_WHOLE_DOC_TOKENS=0
(search alone) and at the default (scripts/run_golden.py). The bytes are deterministic.
"""

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.pdf_builder import Page, Text, make_pdf

HERE = Path(__file__).resolve().parent
SELLER = "Sahyadri Agro Traders\nPlot 14, MIDC Chakan, Pune 410501"


@dataclass(frozen=True)
class Figure:
    field: str
    as_printed: str
    page: int


@dataclass(frozen=True)
class Document:
    pages: tuple[tuple[str, ...], ...]  # lines per page; a CSV has one page of rows
    figures: tuple[Figure, ...] = ()
    latin: str = ""  # what a Devanagari document names, in English letters


def doc(text: str, *figures: tuple[str, str, int], latin: str = "") -> Document:
    """A document from its text, pages split by a "---" line; figures are (field, as printed, page)."""
    pages = tuple(tuple(page.strip("\n").split("\n")) for page in text.split("\n---\n"))
    return Document(pages, tuple(Figure(*figure) for figure in figures), latin)


def inr(rupees: int) -> str:
    """Indian grouping with paise: 4200000 -> "42,00,000.00"."""
    digits, groups = str(rupees), list[str]()
    head, last = digits[:-3], digits[-3:]
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([part for part in (head, *groups) if part] + [last]) + ".00"


def extract_712(place: str, survey: str, area: str, *owners: str, more: str = "") -> Document:
    """An English 7/12 extract: ``place`` is "village, taluka, district"; an owner ends with
    the share when one is printed, and ``more`` is a line after them."""
    village, taluka, district = place.split(", ")
    lines = [f"{n}. {owner}" for n, owner in enumerate(owners, start=1)] + ([more] if more else [])
    shares = [("owner_share", owner.rsplit(" ", 1)[1], 1) for owner in owners if owner.endswith("%")]
    unowned = [("unowned_share", more.rsplit(" ", 1)[1], 1)] if more else []
    return doc(
        "Village Form VII and XII (7/12 Extract)\n"
        f"Village: {village}   Taluka: {taluka}   District: {district}\n"
        f"Survey {survey}   Area: {area} H.R.\nOccupants and shares:\n"
        + "\n".join(lines)
        + "\nComputer generated extract, Talathi office.",
        *shares,
        *unowned,
    )


def invoice(
    number: str, date: str, buyer: str, *items: tuple[str, int], profit: int = 0, **odd: int
) -> Document:
    """A tax invoice: items are (one of ITEMS, quantity); CGST and SGST at 9%. ``profit``
    prints a profit line; ``odd`` may print a wrong ``subtotal`` (the checks fail), put the
    profit on the total's line (``same_line``) or the totals on a second page (``split``)."""
    rows = [f"{n} {name} {q} {ITEMS[name][0]} {inr(ITEMS[name][1])}" for n, (name, q) in enumerate(items, 1)]
    rows = [f"{row} {inr(q * ITEMS[name][1])}" for row, (name, q) in zip(rows, items, strict=True)]
    subtotal = odd.get("subtotal", sum(q * ITEMS[name][1] for name, q in items))
    tax = subtotal * 9 // 100
    total = inr(subtotal + 2 * tax)
    tail = f"Sub Total: {inr(subtotal)}\nCGST 9%: {inr(tax)}\nSGST 9%: {inr(tax)}\nGrand Total: {total}"
    if profit:
        tail += (
            "   Profit: " if odd.get("same_line") else "\nProfit on this invoice: "
        ) + f"Rs. {inr(profit)}"
    head = f"{SELLER}\nTAX INVOICE\nInvoice No: {number}\nInvoice Date: {date}\nBill To: {buyer}\n"
    table = "S.No Description Qty Unit Rate Amount\n" + "\n".join(rows)
    text = (
        f"{head}{table}\nPage 1 of 2\n---\n{tail}\nPage 2 of 2"
        if odd.get("split")
        else f"{head}{table}\n{tail}"
    )
    last = 2 if odd.get("split") else 1
    profits = [("profit", f"Rs. {inr(profit)}", last)] if profit else []
    return doc(text, ("invoice_date", date, 1), ("total_amount", total, last), *profits)


ITEMS = {
    "Drip pipe 16 mm": ("m", 30),
    "Sprinkler head": ("nos", 120),
    "Water tank 1000 L": ("nos", 6500),
    "Mulching film roll": ("nos", 2400),
}
PIPE, HEAD, TANK, FILM = ITEMS

DOCUMENTS: dict[str, Document] = {
    "712-kusgaon-45-2.pdf": extract_712(
        "Kusgaon, Maval, Pune", "45/2", "1.60", "Nikhil Varde 50%", "Ketaki Mhaskar 50%"
    ),
    "712-ulwe-9.pdf": extract_712(
        "Ulwe, Panvel, Raigad", "9", "0.95", "Nikhil Varde 40%", "Ketaki Mhaskar 40%", "Sayali Mhaskar 20%"
    ),
    "712-chowk-31-4.pdf": extract_712(
        "Chowk, Khalapur, Raigad",
        "31/4",
        "1.10",
        "Rushikesh Ghatge 50%",
        "Aniket Sohoni 30%",
        more="Unallotted share: 20%",
    ),
    "712-owale-18.pdf": extract_712(
        "Owale, Thane, Thane", "18", "0.40", "Aniket Rege 50%", "Hrishikesh Ghatge 30%"
    ),
    "712-hinjewadi-12-mr.pdf": doc(
        """
गाव नमुना सात बारा उतारा
गाव: हिंजवडी   तालुका: मुळशी   जिल्हा: पुणे
सर्वे क्रमांक 12   क्षेत्र: 1.25 हे.आर.
भोगवटादार: निखिल वर्दे
फेरफार क्रमांक 3920 दिनांक 02/05/2026""",
        latin="7/12 extract Survey 12 Hinjewadi Mulshi Pune Nikhil Varde",
    ),
    "712-kamshet-77-mr.pdf": doc(
        """
गाव नमुना सात बारा उतारा
गाव: कामशेत   तालुका: मावळ   जिल्हा: पुणे
गट क्रमांक 77   क्षेत्र: 2.05 हे.आर.
भोगवटादार: 1. केतकी म्हसकर  2. ओंकार लोणकर
फेरफार क्रमांक 5108 दिनांक 19/06/2026""",
        latin="7/12 extract Gat 77 Kamshet Maval Pune Ketaki Mhaskar Omkar Lonkar",
    ),
    "sale-deed-khalapur-gat-118.pdf": doc(
        """
SALE DEED
Executed at Khalapur on 02/02/2026
Consideration: Rs. 42,00,000.00
Page 1 of 3
---
Vendor: Vasant Kolte, of Savroli
Purchasers: Nikhil Varde and Ketaki Mhaskar, jointly
Page 2 of 3
---
Schedule: Gat 118, Village Savroli, Taluka Khalapur, District Raigad
Area: 0.80 H.R.
Page 3 of 3""",
        ("consideration", "Rs. 42,00,000.00", 1),
    ),
    "sale-deed-wakad-flat-302-mr.pdf": doc(
        """
खरेदी खत
सदनिका क्रमांक 302, वाकड, पुणे शहर
विक्रेता: शालन धुमाळ
खरेदीदार: निखिल वर्दे
मोबदला: रु. 68,50,000
दिनांक 11/01/2026""",
        ("consideration", "रु. 68,50,000", 1),
        latin="sale deed Flat 302 Wakad Pune city Nikhil Varde Shalan Dhumal",
    ),
    "property-card-kothrud-shop-4.pdf": doc(
        """
PROPERTY CARD / मालमत्ता पत्रक
City Survey (CTS) No. 1187, Kothrud, Pune city
Unit: Shop 4, ground floor   Area: 22.5 sq m
Holder: Ketaki Mhaskar
धारक: केतकी म्हसकर
Entry certified on 07/07/2026""",
        ("area", "22.5 sq m", 1),
        latin="property card Shop 4 Kothrud Pune city Ketaki Mhaskar",
    ),
    "sale-deed-thane-flat-1104.pdf": doc(
        """
SALE DEED
Flat 1104, Lakeview Towers, Majiwada, Thane
Vendor: Ramesh Kadu
Purchaser: Aniket Sohoni
Consideration: Rs. 1,12,00,000.00
Executed at Thane on 24/04/2026""",
        ("consideration", "Rs. 1,12,00,000.00", 1),
    ),
    "sale-deed-panvel-plot-56.pdf": doc(
        """
SALE DEED
Plot 56, Sector 7, Kalamboli, Taluka Panvel, District Raigad
Vendor: Leela Gharat
Purchaser: Rushikesh Ghatge
Consideration: Rs. 38,75,000.00
Executed at Panvel on 30/06/2026""",
        ("consideration", "Rs. 38,75,000.00", 1),
    ),
    "invoice-inv-2041.pdf": invoice(
        "INV-2041", "12/09/2026", "Kelkar Hardware", (TANK, 12), (PIPE, 500), profit=18400, split=1
    ),
    "invoice-inv-2045.pdf": invoice("INV-2045", "15/09/2026", "Dhavale Textiles", (HEAD, 150)),
    "invoice-inv-2049.pdf": invoice("INV-2049", "17/09/2026", "Gokhale Traders", (FILM, 20), (PIPE, 1000)),
    "invoice-inv-2052.pdf": invoice("INV-2052", "18/09/2026", "Nimbalkar Dairy", (TANK, 4)),
    "invoice-inv-2057.pdf": invoice(
        "INV-2057",
        "19/09/2026",
        "Sutar Furniture",
        (TANK, 5),
        (FILM, 10),
        profit=9250,
        subtotal=55000,
        same_line=1,
    ),
    "invoice-inv-2060.pdf": invoice("INV-2060", "23/09/2026", "Bapat Electricals", (HEAD, 250), (PIPE, 300)),
    "invoice-inv-2063.pdf": invoice(
        "INV-2063", "26/09/2026", "Om Steel Works", (TANK, 30), (FILM, 25), profit=31000
    ),
    "invoice-inv-2066.pdf": invoice("INV-2066", "29/09/2026", "Rane Plastics", (FILM, 15)),
    "invoice-inv-2011.pdf": invoice("INV-2011", "05/08/2026", "Kelkar Hardware", (PIPE, 800)),
    "invoice-inv-2015.pdf": invoice("INV-2015", "11/08/2026", "Joshi Agencies", (TANK, 8), profit=7500),
    "invoice-inv-2030.pdf": invoice(
        "INV-2030", "28/08/2026", "Dhavale Textiles", (TANK, 10), (HEAD, 100), profit=12300
    ),
    "invoice-inv-1987.pdf": invoice("INV-1987", "08/07/2026", "Om Steel Works", (FILM, 30), profit=6800),
    "invoice-inv-1995.pdf": invoice("INV-1995", "21/07/2026", "Gokhale Traders", (TANK, 7), profit=5450),
    "invoice-inv-1803.pdf": invoice("INV-1803", "18/09/2025", "Nimbalkar Dairy", (HEAD, 210), profit=4100),
    "profit-and-loss-2026.csv": doc(
        '''
Month,Revenue,Expenses,Profit
July 2026,"4,10,000.00","3,57,200.00","52,800.00"
August 2026,"3,96,450.00","3,42,000.00","54,450.00"
September 2026,"4,48,700.00","3,84,500.00","64,200.00"''',
        *(("profit", amount, 1) for amount in ("52,800.00", "54,450.00", "64,200.00")),
    ),
    "rent-agreement-sinnar-godown-7.pdf": doc(
        """
RENT AGREEMENT
Made at Sinnar on 20/12/2025
Landlord: Sunanda Kale
Tenant: Sahyadri Agro Traders, Chakan
Premises: Godown 7, MIDC Malegaon, Sinnar
Period: 11 months, from 01/01/2026 to 30/11/2026
Monthly rent: Rs. 18,000.00
Security deposit: Rs. 54,000.00
---
The tenant pays the electricity charges.
Either side may end it with one month's notice.""",
        ("monthly_rent", "Rs. 18,000.00", 1),
        ("deposit", "Rs. 54,000.00", 1),
    ),
    "leave-licence-nashik-road-office.pdf": doc(
        """
LEAVE AND LICENCE AGREEMENT
Licensor: Mohan Tare
Licensee: Sahyadri Agro Traders
Premises: Office 5, second floor, Datta Mandir Road, Nashik Road, Nashik
Licence period: 01/04/2026 to 28/02/2027
Licence fee: Rs. 25,000.00 per month
Deposit: Rs. 1,50,000.00
Use: office only, no storage of goods.""",
        ("licence_fee", "Rs. 25,000.00", 1),
    ),
    "quotation-q-118.pdf": doc(
        f"""
{SELLER}
QUOTATION
Quotation No: Q-118
Date: 15/09/2026
To: Bapat Electricals
Solar pump controller 2 nos 18,000.00 36,000.00
Total quoted: Rs. 36,000.00
Estimated margin: Rs. 3,900.00
Valid for 30 days. This is not an invoice.""",
        ("margin", "Rs. 3,900.00", 1),
    ),
    "electricity-bill-chakan-sept-2026.pdf": doc(
        """
Chakan Power Distribution Co-operative
ELECTRICITY BILL
Consumer: Sahyadri Agro Traders, Chakan works
Bill month: September 2026
Units consumed: 2,140
Amount payable: Rs. 21,870.00
Due date: 15/10/2026""",
        ("amount_payable", "Rs. 21,870.00", 1),
    ),
}


def render(name: str, document: Document) -> bytes:
    """A CSV's rows, or a PDF: 10 pt text, one line every 22 points from the top."""
    if name.endswith(".csv"):
        return ("\n".join(document.pages[0]) + "\n").encode("utf-8")
    devanagari = range(0x0900, 0x0980)
    return make_pdf(
        [
            Page(
                texts=[
                    Text(50, 760 - 22 * i, s, 10, devanagari=any(ord(c) in devanagari for c in s))
                    for i, s in enumerate(lines)
                ]
            )
            for lines in document.pages
        ]
    )


def build() -> dict[str, bytes]:
    return {name: render(name, document) for name, document in DOCUMENTS.items()}


def manifest(files: dict[str, bytes]) -> str:
    """``sha256sum`` lines, by name."""
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items()))


def main() -> int:
    files = build()
    for name, data in files.items():
        (HERE / name).write_bytes(data)
    (HERE / "documents.sha256").write_text(manifest(files), encoding="utf-8", newline="\n")
    sys.stdout.write(f"{len(files)} documents written to {HERE}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
