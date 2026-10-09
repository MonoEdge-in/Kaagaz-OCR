"""The PRD 2 exit test in code (PRD 2 §2, test-engineer amendment 3): the six files of
tests/exit_fixtures.py, uploaded and read to the end by the real pipeline on a real
PostgreSQL, each asserting its exit claim. The model is a fake (``QuotingChat``: the
card, then the invoice windows) and the OCR engine is ``ScriptedOcr``. The model-off
twin runs the same six with MODEL_ENABLED=false.
"""

import functools
import io
import json
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments, mark_for_reprocess
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.db.review import list_queue
from kaagaz.db.runs import PgRuns
from kaagaz.extraction.invoice import WindowSettings
from kaagaz.extraction.labels import overlap
from kaagaz.extraction.understanding import ModelBudget
from kaagaz.ingestion import sniff
from kaagaz.ingestion.policy import UploadPolicy, limits_by_type
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.pipeline import DocumentProcessor
from kaagaz.preparing.pages import prepare_pages
from kaagaz.reading.ocr import MeteredOcrEngine
from kaagaz.reading.spans import BoxLocation
from kaagaz.reading.wiring import build_reader_socket
from kaagaz.scanning.gate import ScanGate, Verdict
from tests.db_support import new_customer
from tests.exit_fixtures import (
    PROFIT_LABEL,
    PROFIT_VALUE,
    TEXTILE_FIELDS,
    ExitFile,
    QuotingChat,
    ScriptedOcr,
    exit_files,
    textile_lines,
)
from tests.fakes import FakeScanner, MemoryPageImages, MemoryQueue, MemoryStorage, fake_sheet_pages
from tests.settings_factory import make_settings
from tests.test_extraction_card import fact
from tests.test_extraction_card import reply as card_reply
from tests.test_extraction_invoice import item
from tests.test_invoice_pipeline_db import INVOICE_CARD
from tests.test_pipeline_db import moving_clock
from tests.test_pipeline_runs import Timer
from tests.test_pipeline_store_db import counts

pytestmark = pytest.mark.db

SETTINGS = make_settings(page_image_dpi=72, ocr_dpi=72, max_pages=30)
POLICY = UploadPolicy(
    max_bytes=300_000,
    allowed_types=frozenset({sniff.PDF, sniff.JPEG, sniff.ZIP}),
    type_limits=limits_by_type(pdf=300_000, image=300_000, sheet=300_000),
)
BUDGET = ModelBudget(
    card_max_chars=12000,
    card_max_tokens=1024,
    num_ctx=8192,
    invoice_windows=WindowSettings(1700, 400, 10, 2048),
)

# What the fake model says about each file: its card, and for invoices its window quotes.
HINDI_BILL_CARD = card_reply(
    doc_type="invoice",
    languages=["hi"],
    parties=[{"role": "seller", "name_quote": "शर्मा किराना स्टोर"}],
    title="A retail bill",
    summary="A retail bill for rice and lentils.",
)
HINDI_BILL_FIELDS: dict[str, tuple[str | None, str] | None] = {
    "invoice_number": ("बिल नं.", "२४५"),
    "invoice_date": ("दिनांक", "०५/०९/२०२६"),
    "seller_name": (None, "शर्मा किराना स्टोर"),
    "total": ("कुल", "₹ ४५०.००"),  # noqa: RUF001 - Devanagari digit zero, on purpose
}
HINDI_BILL_LINES = [
    item("चावल", "३००.००", quantity="५", unit="किलो"),  # noqa: RUF001 - Devanagari digit zero, on purpose
    item("दाल", "१५०.००", quantity="२", unit="किलो"),  # noqa: RUF001 - Devanagari digit zero, on purpose
]
LAND_RECORD_CARD = card_reply(
    doc_type="land_record",
    languages=["mr"],
    parties=[
        {"role": "owner", "name_quote": "पाटील रामचंद्र गणपत"},
        {"role": "other", "name_quote": "शिंदे विठ्ठल बाबुराव"},  # the former owner
    ],
    identifiers=[{"kind": "survey_number", "value_quote": "४५/२"}],
    document_date={"value_quote": "१२/०८/२०२६"},
    title="A land record",
    summary="A village land record of one survey number.",
)
CARDS: dict[str, dict[str, Any]] = {
    "hindi_retail_bill": HINDI_BILL_CARD,
    "marathi_land_record": LAND_RECORD_CARD,
    "bank_statement": card_reply(doc_type="bank_statement", title="A bank statement", summary="A statement."),
    "broken_font_pdf": card_reply(
        doc_type="letter", languages=["hi"], title="An office order", summary="An order."
    ),
    "report": card_reply(
        doc_type="register",
        title="A monthly report",
        summary="A monthly operations report.",
        key_facts=[fact("profit", PROFIT_VALUE, PROFIT_LABEL)],
    ),
}


def chat_for(key: str) -> QuotingChat:
    if key.startswith("textile_invoice"):
        silk = "3,500.00" if key.endswith("mismatched") else "3,000.00"
        return QuotingChat(TEXTILE_FIELDS, textile_lines(silk_amount=silk), card=INVOICE_CARD)
    if key == "hindi_retail_bill":
        return QuotingChat(HINDI_BILL_FIELDS, HINDI_BILL_LINES, card=HINDI_BILL_CARD)
    return QuotingChat({}, [], card=CARDS[key])


class Read:
    """Every exit file uploaded and read to the end, for one new customer."""

    def __init__(
        self,
        customer: str,
        ids: dict[str, str],
        makers: dict[str, Callable[[], DocumentProcessor]],
    ) -> None:
        self.customer, self.ids, self.makers = customer, ids, makers

    def again(self, db: Database, key: str) -> None:
        """PRD 2 X1: read one file again (reprocess), with new readers (the scripted OCR
        starts over) and the same fake model answers."""
        with db.customer(self.customer) as conn:
            assert mark_for_reprocess(conn, self.customer, self.ids[key], per_hour=20) is not None
        self.makers[key]()(Job(self.customer, self.ids[key]), lambda: None)


def read_files(
    db: Database,
    *,
    model: bool,
    files: Mapping[str, ExitFile] | None = None,
) -> Read:
    """``files``, the exit files by default."""
    customer, ids, makers = _read(db, model=model, files=exit_files() if files is None else files)
    return Read(customer, ids, makers)


def read_all(db: Database, *, model: bool) -> tuple[str, dict[str, str]]:
    """(customer, document id by file key): every exit file uploaded and read to the end."""
    customer, ids, _ = _read(db, model=model, files=exit_files())
    return customer, ids


def _read(
    db: Database, *, model: bool, files: Mapping[str, ExitFile]
) -> tuple[str, dict[str, str], dict[str, Callable[[], DocumentProcessor]]]:
    customer, _ = new_customer(db)
    storage = MemoryStorage()
    documents = PgDocuments(db)
    upload = UploadService(POLICY, documents, storage, MemoryQueue())
    prepare = functools.partial(
        prepare_pages, dpi=72, max_pages=30, max_image_pixels=10_000_000, blank_stddev=3.0, low_res_dpi=150
    )
    ids: dict[str, str] = {}
    makers: dict[str, Callable[[], DocumentProcessor]] = {}
    for key, file in files.items():
        doc = upload.accept(customer, io.BytesIO(file.data)).document_id
        makers[key] = functools.partial(_processor, db, storage, documents, prepare, key, file, model)
        makers[key]()(Job(customer, doc), lambda: None)
        ids[key] = doc
    return customer, ids, makers


def _processor(
    db: Database,
    storage: MemoryStorage,
    documents: PgDocuments,
    prepare: Callable[[str, bytes, Any], Any],
    key: str,
    file: ExitFile,
    model: bool,
) -> DocumentProcessor:
    """A processor for one exit file: its scripted OCR, and its fake model when ``model``."""
    chat = chat_for(key) if model else None
    timer = Timer()
    return DocumentProcessor(
        gate=ScanGate(storage, FakeScanner(Verdict.CLEAN), storage, documents),
        statuses=documents,
        documents=documents,
        socket=build_reader_socket(SETTINGS, MeteredOcrEngine(ScriptedOcr(file.ocr_pages), timer=timer)),
        prepare=prepare,
        sheet_pages=fake_sheet_pages,
        page_images=MemoryPageImages(),
        store=PgPipelineStore(db),
        runs=PgRuns(db),
        progress=documents,
        clock=moving_clock,
        timer=timer,
        chat=chat,
        chat_model=None if chat is None else chat.chat_model,
        model_budget=None if chat is None else BUDGET,
        timezone="Asia/Kolkata",
    )


def rows(db: Database, customer: str, sql: str, doc: str) -> list[tuple[Any, ...]]:
    with db.customer(customer) as conn:
        return [tuple(row) for row in conn.execute(text(sql), {"c": customer, "d": doc}).all()]


def routes(db: Database, customer: str, doc: str) -> list[str]:
    return [
        row[0]
        for row in rows(db, customer, "SELECT route FROM pages WHERE document_id = :d ORDER BY page_no", doc)
    ]


ROUTES = {
    "textile_invoice": ["text_layer"] * 3,
    "textile_invoice_mismatched": ["text_layer"] * 3,
    "hindi_retail_bill": ["ocr"],
    "marathi_land_record": ["ocr"],
    "bank_statement": ["cells"],
    "broken_font_pdf": ["ocr"],  # R2: the KrutiDev010 text layer is not trusted
    "report": ["text_layer"] * 20,
}
INVOICE = (
    "SELECT invoice_number, invoice_date, total, check_status, check_reasons FROM invoices"
    " WHERE document_id = :d"
)


def assert_every_file_is_ready_with_its_routes(db: Database, customer: str, ids: dict[str, str]) -> None:
    for key, doc in ids.items():
        assert rows(db, customer, "SELECT status FROM documents WHERE id = :d", doc) == [("ready",)], key
        assert routes(db, customer, doc) == ROUTES[key], key


def assert_the_report_joins_pages_15_and_16(db: Database, customer: str, doc: str) -> None:
    """C7: exactly one piece holds both halves, from page 15 to 16, with a region on each."""
    pieces = rows(
        db, customer, "SELECT page_from, page_to, text, regions FROM pieces WHERE document_id = :d", doc
    )
    both = [piece for piece in pieces if PROFIT_LABEL in piece[2] and PROFIT_VALUE in piece[2]]
    assert len(both) == 1
    page_from, page_to, _, regions = both[0]
    assert (page_from, page_to) == (15, 16)
    assert {region["page"] for region in regions} >= {15, 16}
    assert all("Page 15 of 20" not in piece[2] for piece in pieces)
    footers = rows(db, customer, "SELECT output::text FROM reader_outputs WHERE document_id = :d", doc)
    assert "Page 15 of 20" in footers[0][0]  # kept in the reader output


def without_region(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "region"}


def test_all_six_files_with_a_fake_model(db: Database) -> None:
    files = read_files(db, model=True)
    customer, ids = files.customer, files.ids
    assert_every_file_is_ready_with_its_routes(db, customer, ids)

    # The textile invoice: one row, one row per item, the check passes.
    textile = ids["textile_invoice"]
    assert rows(db, customer, INVOICE, textile) == [
        ("TX-2026-118", date(2026, 8, 12), Decimal("11800.00"), "passed", [])
    ]
    assert rows(db, customer, "SELECT count(*) FROM invoice_lines WHERE document_id = :d", textile) == [(4,)]
    labels = dict(
        rows(
            db,
            customer,
            "SELECT field, label_reason FROM extracted_values"
            " WHERE document_id = :d AND record_key = 'document'",
            textile,
        )
    )
    assert (labels["total_amount"], labels["party_name"]) == ("readers_agree", "model_chosen")

    # The copy with one line amount changed fails lines_sum_mismatch, and its values are in review.
    copy = ids["textile_invoice_mismatched"]
    assert rows(
        db, customer, "SELECT check_status, check_reasons FROM invoices WHERE document_id = :d", copy
    ) == [("failed", ["lines_sum_mismatch"])]
    with db.customer(customer) as conn:
        queued, _ = list_queue(conn, customer, limit=500, offset=0, document_id=copy)
    assert {row["field"] for row in queued if row["label_reason"] == "check_failed"} == {
        "amount",
        "subtotal",
        "tax_amount",
    }

    # The Hindi bill: its language, typed values from Devanagari digits, and OCR script labels.
    bill = ids["hindi_retail_bill"]
    assert rows(db, customer, "SELECT doc_type, languages FROM documents WHERE id = :d", bill) == [
        ("invoice", ["hi"])
    ]
    assert rows(db, customer, INVOICE, bill) == [("२४५", date(2026, 9, 5), Decimal("450.00"), "passed", [])]
    amounts = rows(
        db, customer, "SELECT amount FROM invoice_lines WHERE document_id = :d ORDER BY line_no", bill
    )
    assert amounts == [(Decimal("300.00"),), (Decimal("150.00"),)]
    total = rows(
        db,
        customer,
        "SELECT value_text, value_number FROM extracted_values WHERE document_id = :d"
        " AND record_key = 'document' AND field = 'total_amount'",
        bill,
    )
    assert total == [("₹ ४५०.००", Decimal("450.00"))]  # noqa: RUF001 - Devanagari digit zero, on purpose
    descriptions = rows(
        db,
        customer,
        "SELECT DISTINCT label, label_reason FROM extracted_values"
        " WHERE document_id = :d AND field = 'description'",
        bill,
    )
    assert descriptions == [("needs_review", "ocr_script")]

    # The land record: every name located, the former owner included, with Latin spellings.
    land = ids["marathi_land_record"]
    card = rows(db, customer, "SELECT doc_type, languages, card FROM documents WHERE id = :d", land)[0]
    assert card[:2] == ("land_record", ["mr"])
    parties = {(party["role"], party["name"], party["name_latin"]) for party in card[2]["parties"]}
    assert {role for role, _, _ in parties} == {"owner", "other"}
    assert {name for _, name, _ in parties} == {"पाटील रामचंद्र गणपत", "शिंदे विठ्ठल बाबुराव"}
    assert all(latin for _, _, latin in parties)
    assert rows(
        db, customer, "SELECT bool_and(text_latin IS NOT NULL) FROM pieces WHERE document_id = :d", land
    ) == [(True,)]
    # Its summary, date and identifier, exactly (the Devanagari test below checks their spots).
    summary = "A village land record of one survey number."
    facts = rows(db, customer, "SELECT summary, document_date FROM documents WHERE id = :d", land)
    assert facts == [(summary, date(2026, 8, 12))]
    assert card[2]["summary"] == summary
    assert without_region(card[2]["document_date"]) == {
        "date": "2026-08-12",
        "page": 1,
        "value": "१२/०८/२०२६",
    }
    assert [without_region(item) for item in card[2]["identifiers"]] == [
        {"kind": "survey_number", "field": "survey_number", "page": 1, "value": "४५/२"}
    ]

    # The bank statement: its cells, read directly.
    bank = ids["bank_statement"]
    credit = rows(
        db,
        customer,
        "SELECT value_number, label_reason FROM extracted_values WHERE document_id = :d AND field = 'credit'",
        bank,
    )
    assert credit == [(Decimal(11800), "direct_cell")]

    # The broken font PDF went to OCR, so its pieces hold the Devanagari OCR read.
    broken = ids["broken_font_pdf"]
    texts = rows(db, customer, "SELECT text FROM pieces WHERE document_id = :d", broken)
    assert any("भारत सरकार" in row[0] for row in texts)
    assert all("Hkkjr" not in row[0] for row in texts)

    # The report: one piece across pages 15 and 16, and the profit value on 16 with its label on 15.
    report = ids["report"]
    assert_the_report_joins_pages_15_and_16(db, customer, report)
    profit = rows(
        db,
        customer,
        "SELECT value_text, page_no, label_page_no FROM extracted_values"
        " WHERE document_id = :d AND field = 'profit'",
        report,
    )
    assert profit == [(PROFIT_VALUE, 16, 15)]


def test_all_six_files_reach_ready_with_the_model_off(db: Database) -> None:
    customer, ids = read_all(db, model=False)
    assert_every_file_is_ready_with_its_routes(db, customer, ids)

    for key, doc in ids.items():
        assert rows(db, customer, "SELECT doc_type, card FROM documents WHERE id = :d", doc) == [
            (None, None)
        ], key
        assert rows(db, customer, "SELECT count(*) FROM document_links WHERE document_id = :d", doc) == [
            (0,)
        ], key
    # The textile invoice's row comes from the label rules, with no lines; its check passes.
    textile = ids["textile_invoice"]
    assert rows(db, customer, INVOICE, textile) == [
        ("TX-2026-118", date(2026, 8, 12), Decimal("11800.00"), "passed", [])
    ]
    assert rows(db, customer, "SELECT count(*) FROM invoice_lines WHERE document_id = :d", textile) == [(0,)]
    # With no card, the languages come from the pieces: Devanagari is "other" (L2).
    for key in ("hindi_retail_bill", "marathi_land_record", "broken_font_pdf"):
        languages = rows(db, customer, "SELECT languages FROM documents WHERE id = :d", ids[key])
        assert languages == [(["other"],)], key
    # No label rule reads the Hindi bill, so it has no row.
    assert rows(db, customer, INVOICE, ids["hindi_retail_bill"]) == []
    for key in ("hindi_retail_bill", "marathi_land_record", "broken_font_pdf"):
        latin = rows(
            db,
            customer,
            "SELECT bool_and(text_latin IS NOT NULL) FROM pieces WHERE document_id = :d",
            ids[key],
        )
        assert latin == [(True,)], key
    assert_the_report_joins_pages_15_and_16(db, customer, ids["report"])
    runs = rows(
        db,
        customer,
        "SELECT pages_text_layer, pages_ocr, model_calls FROM processing_runs WHERE document_id = :d",
        ids["report"],
    )
    assert runs == [(20, 0, 0)]


VERIFIED = "SELECT count(*) FROM extracted_values WHERE document_id = :d AND label = 'verified'"
OCR_SCRIPT = "SELECT count(*) FROM extracted_values WHERE document_id = :d AND label_reason = 'ocr_script'"
LABELS = (
    "SELECT record_key, field, value_text, label, label_reason FROM extracted_values WHERE document_id = :d"
)
# The values with a Devanagari letter, counted by hand: the bill's seller, and its two
# items' descriptions and units. The land record's values are all the card's (model_chosen
# first), and no rule or quote takes a value from the broken font's OCR text.
DEVANAGARI_OCR_SCRIPT = {"hindi_retail_bill": 5, "marathi_land_record": 0, "broken_font_pdf": 0}


def test_what_the_model_chose_or_ocr_read_from_devanagari_is_never_verified(db: Database) -> None:
    """Security A1 and ML 1 across the exit files (final council 2): no card value is
    verified anywhere; nothing read by OCR from Hindi or Marathi, or from the broken font,
    is verified; and the mismatched copy's document values are all in review.
    Each of the land record card's spots is its OCR line's box (IoU at least 0.5)."""
    files = read_files(db, model=True)
    customer, ids = files.customer, files.ids
    with db.customer(customer) as conn:
        cards: int = conn.execute(
            text(
                "SELECT count(*) FROM extracted_values WHERE customer_id = :c AND record_key = 'card'"
                " AND label = 'verified'"
            ),
            {"c": customer},
        ).scalar_one()
    assert cards == 0
    for key, expected in DEVANAGARI_OCR_SCRIPT.items():
        assert rows(db, customer, VERIFIED, ids[key]) == [(0,)], key
        assert rows(db, customer, OCR_SCRIPT, ids[key]) == [(expected,)], key
    copy_verified = (
        "SELECT count(*) FROM extracted_values WHERE document_id = :d AND record_key = 'document'"
        " AND label = 'verified'"
    )
    assert rows(db, customer, copy_verified, ids["textile_invoice_mismatched"]) == [(0,)]
    land_card = rows(db, customer, "SELECT card FROM documents WHERE id = :d", ids["marathi_land_record"])[0][
        0
    ]
    spots = [(party["name"], party["region"]) for party in land_card["parties"]]
    spots += [
        (item["value"], item["region"]) for item in [*land_card["identifiers"], land_card["document_date"]]
    ]
    assert len(spots) == 4
    ocr_lines = exit_files()["marathi_land_record"].ocr_pages[0]
    for value, region in spots:
        (line,) = [box for line_text, box in ocr_lines if value in line_text]
        assert region["page"] == 1 and region["kind"] == "box", value
        assert overlap(BoxLocation(1, *region["box"]), BoxLocation(1, *line)) >= 0.5, value


# Everything a read leaves for one document, apart from ids and times.
RESULTS = (
    "SELECT status, doc_type, languages, summary, document_date, card - 'run_id', page_count"
    " FROM documents WHERE id = :d",
    "SELECT page_no, route, width, height, unit, has_image FROM pages WHERE document_id = :d",
    "SELECT seq, page_from, page_to, text, regions, label, context_header, text_latin FROM pieces"
    " WHERE document_id = :d",
    "SELECT record_key, field, value_text, value_raw, second_reading, value_number, value_date, page_no,"
    " region, reader, confidence, label, label_reason, label_page_no, label_region FROM extracted_values"
    " WHERE document_id = :d",
    "SELECT invoice_number, invoice_date, seller_name, seller_gstin, buyer_name, buyer_gstin, subtotal,"
    " tax_total, total, currency, label, check_status, check_reasons FROM invoices WHERE document_id = :d",
    "SELECT line_no, description, hsn, quantity, unit, rate, amount, label FROM invoice_lines"
    " WHERE document_id = :d",
    "SELECT identifier_kind, identifier_value FROM document_links WHERE document_id = :d",
    "SELECT status, error_code, pages_cells, pages_text_layer, pages_ocr, pages_blank, vision_crops,"
    " model_calls, quotes_returned, dropped_quotes, values_verified, values_needs_review, config"
    " FROM processing_runs WHERE document_id = :d",
)


def results(db: Database, files: Read) -> dict[str, list[list[str]]]:
    """For each file, RESULTS' rows as JSON, sorted."""
    return {
        key: [
            sorted(
                json.dumps(row, default=str, ensure_ascii=False) for row in rows(db, files.customer, sql, doc)
            )
            for sql in RESULTS
        ]
        for key, doc in files.ids.items()
    }


def test_two_reads_of_the_exit_files_give_the_same_results(db: Database) -> None:
    """Two customers read the exit files with the model: every row
    either read leaves is the same as the other's, apart from ids and times."""
    first, second = (read_files(db, model=True) for _ in range(2))
    assert results(db, first) == results(db, second)


SNAPSHOT = Path(__file__).parent / "fixtures" / "exit_labels.json"


def labels_json(db: Database, files: Read) -> str:
    """Each value's [file, record_key, field, value_text, label, label_reason], sorted, one
    per line."""
    found = sorted(
        [key, *row] for key, doc in files.ids.items() for row in rows(db, files.customer, LABELS, doc)
    )
    return "[\n" + ",\n".join(json.dumps(row, ensure_ascii=False) for row in found) + "\n]\n"


def test_the_exit_labels_match_the_committed_snapshot(db: Database) -> None:
    """A characterization snapshot for A9's clean-up, which must keep it byte for byte: each
    value's label and reason when the exit files are read with the model.
    It was generated from the code at de36d96 (W6), and again in PRD 5 PR 4, when the scans'
    second reading went (the Hindi bill's three readers_disagree became model_chosen). A
    change to it changes what is verified, so it needs its own reason and review, never a
    refactor's."""
    files = read_files(db, model=True)
    assert labels_json(db, files) == SNAPSHOT.read_text(encoding="utf-8")


def test_reprocessing_replaces_the_results_and_keeps_one_set(db: Database) -> None:
    """PRD 2 X1 and the exit test: reading the land record and the textile invoice again
    makes a second run, and every table (the PRD 2 ones included) holds the same counts."""
    files = read_files(db, model=True)
    card_values = "SELECT count(*) FROM extracted_values WHERE document_id = :d AND record_key = 'card'"
    before = {
        key: (
            counts(db, files.customer, files.ids[key]),
            rows(db, files.customer, card_values, files.ids[key]),
        )
        for key in ("marathi_land_record", "textile_invoice")
    }
    textile_counts = before["textile_invoice"][0]
    assert (textile_counts["invoices"], textile_counts["invoice_lines"]) == (1, 4)
    assert textile_counts["document_links"] == 2  # both ways with the copy (the same invoice number)
    assert before["marathi_land_record"][1] != [(0,)]

    for key in ("marathi_land_record", "textile_invoice"):
        files.again(db, key)

    for key, (table_counts, card_count) in before.items():
        doc = files.ids[key]
        assert counts(db, files.customer, doc) == {**table_counts, "processing_runs": 2}, key
        assert rows(db, files.customer, card_values, doc) == card_count, key
        runs = "SELECT status FROM processing_runs WHERE document_id = :d"
        assert rows(db, files.customer, runs, doc) == [("ready",), ("ready",)], key
