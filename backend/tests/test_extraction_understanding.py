"""The model stage (PRD 2 D1, D5, D6): the card with the model on, off or down.

"Unknown" for a document with no type is the frontend's job (D6): here the type
is simply None.
"""

import unittest
from typing import Any

from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.extraction.quotes import reader_text
from kaagaz.extraction.understanding import Understanding, document_links, understand
from kaagaz.extraction.values import ExtractedValue
from kaagaz.pipeline import DocumentFacts, LinkIdentifier
from kaagaz.reading.spans import BoxLocation, CellLocation, Span
from tests.fakes import FakeChat
from tests.test_extraction_card import INVOICE, RUN, fact, reply

TOTAL = ExtractedValue("document", "total_amount", "Rs. 1,180.00", "Rs. 1,180.00", INVOICE[5].location)
NUMBER = ExtractedValue("document", "invoice_number", "INV 17", "INV 17", INVOICE[1].location)
RULES = [NUMBER, TOTAL]

CARD = reply(
    parties=[{"role": "seller", "name_quote": "Sharma Traders"}],
    identifiers=[
        {"kind": "invoice_number", "value_quote": "INV 17"},
        {"kind": "gstin", "value_quote": "27AAPFU0939F1ZV"},
    ],
    key_facts=[fact("total_amount", "Rs. 1,180.00", "Grand Total:"), fact("grand_total", "Rs. 1,180.00")],
)


def run(
    chat: FakeChat | None, spans: list[Span] = INVOICE, rules: list[ExtractedValue] = RULES
) -> tuple[Understanding, list[tuple[int, int]]]:
    beats: list[tuple[int, int]] = []

    def beat(done: int, total: int) -> None:
        beats.append((done, total))

    understanding = understand(
        chat,
        reader_text(spans),
        rules,
        card_max_chars=12000,
        card_max_tokens=1024,
        num_ctx=8192,
        run_id=RUN,
        beat=beat,
        spans=spans,
    )
    return understanding, beats


def fields(values: list[ExtractedValue]) -> list[str]:
    return [value.field for value in values]


class ModelOffTest(unittest.TestCase):
    def test_no_card_no_type_and_no_links(self) -> None:
        understanding, beats = run(None)
        self.assertEqual(understanding.facts, DocumentFacts())
        self.assertIsNone(understanding.facts.doc_type)
        self.assertEqual((understanding.rule_values, understanding.quoted_values), (RULES, []))
        self.assertEqual(
            (understanding.model_calls, understanding.error_code, understanding.counts), (0, None, None)
        )
        self.assertEqual(beats, [])
        # D6: the labelled invoice number is there, but with no card there are no links.
        self.assertEqual(document_links(understanding.facts, RULES), ())


class ModelOnTest(unittest.TestCase):
    def test_the_card_its_values_and_one_beat_after_the_call(self) -> None:
        chat = FakeChat(CARD)
        understanding, beats = run(chat)
        assert understanding.facts.card is not None
        self.assertEqual(understanding.facts.doc_type, "invoice")
        # Council fix 8: one beat after the call, one after the searches.
        self.assertEqual(
            (understanding.model_calls, understanding.error_code, beats), (1, None, [(1, 2), (2, 2)])
        )
        self.assertEqual(understanding.rule_values, RULES)
        # The card's invoice_number is a field the rules already have, so it is not stored
        # twice (conflict 15); the total_amount key fact is reserved (A1(a)).
        self.assertEqual(fields(understanding.quoted_values), ["party_name", "gstin", "grand_total"])
        assert understanding.counts is not None
        self.assertEqual(understanding.counts.reserved_fields, 1)

    def test_the_card_json_drops_the_items_whose_values_were_duplicates(self) -> None:
        """Council fix 10: the card shows only what is stored as its own values."""
        understanding, _ = run(FakeChat(CARD))
        card = understanding.facts.card
        assert card is not None
        self.assertEqual([item["field"] for item in card["identifiers"]], ["gstin"])
        self.assertEqual([item["field"] for item in card["parties"]], ["party_name"])

    def test_a_total_amount_key_fact_on_an_invoice_is_never_counted_twice(self) -> None:
        """PRD edge case: the rule (later E4) value wins, and the card adds no second one."""
        understanding, _ = run(FakeChat(CARD))
        every = understanding.rule_values + understanding.quoted_values
        self.assertEqual(fields(every).count("total_amount"), 1)

    def test_a_card_value_for_a_field_a_sheet_already_has_is_dropped(self) -> None:
        cells = [
            Span("Profit", "Profit", CellLocation(1, 1, 1)),
            Span("12000", "12000", CellLocation(1, 2, 1)),
            Span("Sales 50000", "Sales 50000", BoxLocation(2, 0.1, 0.1, 0.9, 0.12)),
        ]
        rules = [ExtractedValue("sheet:1:row:2", "profit", "12000", "12000", CellLocation(1, 2, 1))]
        answer = reply(key_facts=[fact("profit", "12000"), fact("sales", "50000")])
        understanding, _ = run(FakeChat(answer), cells, rules)
        self.assertEqual(fields(understanding.quoted_values), ["sales"])

    def test_links_come_from_the_card_and_document_values_only_with_a_card(self) -> None:
        understanding, _ = run(FakeChat(CARD))
        links = document_links(understanding.facts, understanding.rule_values + understanding.quoted_values)
        self.assertEqual(links, (LinkIdentifier("invoice_number", "inv17"),))  # gstin is never a link

    def test_no_page_fitting_the_budget_makes_no_call(self) -> None:
        """Council fix 7: nothing to send, so no call, no beat and no card."""
        chat = FakeChat(CARD)
        beats: list[tuple[int, int]] = []
        understanding = understand(
            chat,
            reader_text(INVOICE),
            RULES,
            card_max_chars=10,
            card_max_tokens=1024,
            num_ctx=8192,
            run_id=RUN,
            beat=lambda done, total: beats.append((done, total)),
            spans=INVOICE,
        )
        self.assertEqual((chat.calls, beats, understanding.model_calls), ([], [], 0))
        self.assertEqual((understanding.facts, understanding.error_code), (DocumentFacts(), None))

    def test_a_document_with_no_text_makes_no_call(self) -> None:
        chat = FakeChat()
        understanding, beats = run(chat, spans=[])
        self.assertEqual((chat.calls, beats, understanding.model_calls), ([], [], 0))
        self.assertEqual(understanding.facts, DocumentFacts())


class ModelDownTest(unittest.TestCase):
    """PRD edge case: the model is down halfway. No card, the rule values stay, and the
    run records the error code; the document still reaches ready (the pipeline's part)."""

    def check(self, error: Exception, code: str) -> None:
        understanding, beats = run(FakeChat(error))
        self.assertEqual(understanding.facts, DocumentFacts())
        self.assertEqual((understanding.rule_values, understanding.quoted_values), (RULES, []))
        self.assertEqual((understanding.error_code, understanding.model_calls, beats), (code, 1, [(1, 2)]))
        self.assertEqual(document_links(understanding.facts, RULES), ())

    def test_the_model_timing_out(self) -> None:
        self.check(ModelUnavailable("timeout"), "model_timeout")

    def test_a_reply_cut_at_its_budget(self) -> None:
        self.check(ModelOutputError("output_truncated"), "model_output_truncated")

    def test_a_reply_of_the_wrong_shape(self) -> None:
        bad: dict[str, Any] = {"doc_type": "invoice"}
        self.check(ModelOutputError("card_shape"), "model_card_shape")
        understanding, _ = run(FakeChat(bad))
        self.assertEqual(understanding.error_code, "model_card_shape")
