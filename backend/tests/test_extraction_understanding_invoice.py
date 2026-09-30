"""The model stage with invoice windows (PRD 2 E2, E4; plan 3.7 and 3.8).

After the card, and only when the card says ``invoice``, the invoice windows are
read. Their values replace labelled.py's for the fields they located (ML 6(f)),
go to label_quoted with the card's (ML 2), and their calls and quote counts go
into the run's numbers (ML 10).
"""

import unittest

from kaagaz.ai.client import ModelUnavailable
from kaagaz.extraction.invoice import INVOICE_SYSTEM_PROMPT, WindowSettings, rule_values_left
from kaagaz.extraction.labelled import values_from_lines
from kaagaz.extraction.quotes import reader_text
from kaagaz.extraction.understanding import Understanding, understand
from kaagaz.extraction.values import ExtractedValue
from kaagaz.reading.spans import Span
from kaagaz.settings import Settings
from tests.fakes import FakeChat
from tests.settings_factory import make_settings
from tests.test_extraction_card import RUN, fact
from tests.test_extraction_card import reply as card_reply
from tests.test_extraction_invoice import item, page_lines
from tests.test_extraction_invoice import reply as invoice_reply

SPANS = page_lines(
    "Sharma Traders",
    "Invoice No: INV-17",
    "Invoice Date: 12/08/2026",
    "1 Cotton Poplin 3,000.00",
    "Sub Total: 3,000.00",
    "Grand Total: 3,000.00",
)
RULES = values_from_lines(SPANS)  # invoice_number, invoice_date, subtotal, total_amount
CARD = card_reply(
    doc_type="invoice",
    parties=[{"role": "seller", "name_quote": "Sharma Traders"}],
    identifiers=[{"kind": "invoice_number", "value_quote": "INV-17"}],
    key_facts=[fact("grand_total", "3,000.00")],
)
INVOICE = invoice_reply(
    {
        "invoice_number": ("Invoice No", "INV-17"),
        "seller_name": (None, "Sharma Traders"),
        "buyer_name": ("Bill To", "Nobody"),  # made up: dropped and counted
        "total": ("Grand Total", "3,000.00"),
    },
    lines=[item("Cotton Poplin", "3,000.00")],
)
WINDOWS = WindowSettings(chars=6000, overlap=1000, max_windows=10, max_tokens=2048)


def run(
    chat: FakeChat, *, windows: WindowSettings | None = WINDOWS, spans: list[Span] = SPANS
) -> tuple[Understanding, list[tuple[int, int]]]:
    beats: list[tuple[int, int]] = []

    def beat(done: int, total: int) -> None:
        beats.append((done, total))

    understanding = understand(
        chat,
        reader_text(spans),
        RULES,
        card_max_chars=12000,
        card_max_tokens=1024,
        num_ctx=8192,
        run_id=RUN,
        beat=beat,
        invoice_windows=windows,
    )
    return understanding, beats


def fields(values: list[ExtractedValue]) -> list[str]:
    return [value.field for value in values]


class InvoiceWindowsTest(unittest.TestCase):
    def test_an_invoice_card_is_followed_by_one_call_per_window(self) -> None:
        chat = FakeChat(CARD, INVOICE)
        understanding, beats = run(chat)
        self.assertEqual(len(chat.calls), 2)
        self.assertEqual((chat.calls[0]["max_tokens"], chat.calls[1]["max_tokens"]), (1024, 2048))
        self.assertEqual(chat.calls[1]["system"], INVOICE_SYSTEM_PROMPT)
        self.assertEqual(beats, [(1, 2), (2, 2), (3, 3)])  # the card's call and search, then the window
        self.assertEqual((understanding.model_calls, understanding.error_code), (2, None))
        invoice = understanding.invoice
        assert invoice is not None
        self.assertEqual(
            fields(invoice.values), ["invoice_number", "party_name", "total_amount", "description", "amount"]
        )
        # E4 replaced labelled.py's invoice number and total, and only those (ML 6(f)).
        self.assertEqual(understanding.rule_values, rule_values_left(RULES, invoice.values))
        self.assertEqual(fields(understanding.rule_values), ["invoice_date", "subtotal"])

    def test_card_values_for_fields_the_rules_or_e4_have_are_dropped_and_e4_values_are_quoted(self) -> None:
        understanding, _ = run(FakeChat(CARD, INVOICE))
        # The card's seller (party_name) and invoice number are E4 fields now; its key fact is kept.
        self.assertEqual(
            fields(understanding.quoted_values),
            ["grand_total", "invoice_number", "party_name", "total_amount", "description", "amount"],
        )
        every = understanding.rule_values + understanding.quoted_values
        self.assertEqual(fields(every).count("invoice_number"), 1)
        self.assertEqual(fields(every).count("total_amount"), 1)

    def test_the_quote_counts_add_the_card_and_the_windows(self) -> None:
        understanding, _ = run(FakeChat(CARD, INVOICE))
        card = understanding.counts
        assert card is not None
        # The windows: 4 fields and 1 line searched for, the made-up buyer dropped, and the
        # line's amount also found later (the subtotal and total lines), so ambiguous.
        self.assertEqual(understanding.quotes_returned, card.quotes_returned + 5)
        self.assertEqual(understanding.dropped_quotes, card.dropped_quotes + 1)
        self.assertEqual(understanding.ambiguous_quotes, card.ambiguous_quotes + 1)
        self.assertEqual(understanding.loose_quotes, card.loose_quotes)

    def test_a_card_of_another_type_reads_no_windows(self) -> None:
        chat = FakeChat(card_reply(doc_type="receipt"))
        understanding, beats = run(chat)
        self.assertEqual((len(chat.calls), beats, understanding.model_calls), (1, [(1, 2), (2, 2)], 1))
        self.assertIsNone(understanding.invoice)
        self.assertEqual(understanding.rule_values, RULES)

    def test_without_window_settings_no_windows_are_read(self) -> None:
        chat = FakeChat(CARD)
        understanding, _ = run(chat, windows=None)
        self.assertEqual((len(chat.calls), understanding.invoice), (1, None))

    def test_no_card_means_no_type_and_no_windows(self) -> None:
        chat = FakeChat(ModelUnavailable("timeout"))
        understanding, _ = run(chat)
        self.assertEqual(
            (len(chat.calls), understanding.error_code, understanding.invoice), (1, "model_timeout", None)
        )
        self.assertEqual(understanding.quotes_returned, 0)

    def test_the_model_down_on_a_window_keeps_the_card_and_the_rule_values(self) -> None:
        chat = FakeChat(CARD, ModelUnavailable("timeout"))
        understanding, beats = run(chat)
        self.assertEqual(understanding.facts.doc_type, "invoice")
        self.assertEqual(
            (understanding.error_code, understanding.model_calls, beats),
            ("model_timeout", 2, [(1, 2), (2, 2), (3, 3)]),
        )
        self.assertEqual(understanding.rule_values, RULES)
        self.assertEqual(fields(understanding.quoted_values), ["party_name", "grand_total"])
        assert understanding.invoice is not None
        self.assertEqual(
            (understanding.invoice.values, understanding.invoice.error_code), ([], "model_timeout")
        )


class WindowSettingsTest(unittest.TestCase):
    def test_from_the_settings(self) -> None:
        settings: Settings = make_settings(
            extract_window_chars=5000,
            extract_window_overlap=500,
            extract_max_windows=4,
            model_window_max_tokens=1500,
        )
        self.assertEqual(WindowSettings.from_settings(settings), WindowSettings(5000, 500, 4, 1500))
