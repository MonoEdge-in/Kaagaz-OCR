"""The model stage of the pipeline (PRD 2 D1, D5, D6), kept out of pipeline.py.

* No model (``MODEL_ENABLED=false``): no call, and so no card, no type and no
  links; the rule values go on as they are. The list shows a document with no
  type as "Unknown" (D6): that is the frontend's.
* A model: one card call (``card.ask_card``), then ``beat(1, 2)``; the
  searches for its quotes (``card.locate_card``), then ``beat(2, 2)``. So the
  job's heartbeat and progress move after each (plan 4.2, council fix 8). A
  document with no text, or with no page that fits the budget, makes no call.
* The model down or its reply unusable (``ModelUnavailable``,
  ``ModelOutputError``): no card, the rule values are kept, and ``error_code``
  is ``"model_"`` plus the error's code, for the run (the PRD edge case "the
  model is down halfway"), after ``beat(1, 2)``.
* **Invoice windows** (PRD 2 E2, PR 6): after the card, and only when the card's
  type is ``invoice`` and ``invoice_windows`` is given, ``invoice.read_invoice``
  reads the windows, one call each, with ``beat(2 + i, 2 + n)`` after window i
  of n. Its E4 values replace labelled.py's values of the fields they located,
  and only those (``invoice.rule_values_left``, ML 6(f)). A window that fails
  (the model down or its reply unusable) gives no E4 values at all: labelled.py's
  values stay, the card stays, and ``error_code`` is ``"model_"`` plus the code.
* A card value whose field the rule values or the E4 values already have is
  dropped (PROVISIONAL, plan conflict 15), so nothing is counted twice (bug B2 of
  PRD 1), and its item leaves the card JSON too (council fix 10); with the
  reserved key-fact names (``card.RESERVED_KEY_FACT_FIELDS``) a card key fact
  never adds a second total either.

The two value lists go to their own labeller (ML amendment 2): ``rule_values``
(cells and labelled.py) to ``labels.label_values``, ``quoted_values`` (the
card's, then the E4 values) to ``labels.label_quoted``.

The run's numbers (``processing_runs``, ML 10): ``model_calls`` counts the card
and every window called; ``quotes_returned``, ``dropped_quotes``, ``loose_quotes``
and ``ambiguous_quotes`` add the card's and the windows' counts.

``document_links`` gives the link identifiers (D5) from the document's final
values, and only when it has a card (D6).

Time: one card call and ``card.locate_card``'s bounded searches, then one call
per invoice window and ``invoice.read_invoice``'s cost, plus O(v) for the
de-duplication.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.extraction.card import (
    CARD_PROMPT_HASH,
    CardCounts,
    ChatModel,
    ask_card,
    locate_card,
    without_fields,
)
from kaagaz.extraction.invoice import (
    INVOICE_PROMPT_HASH,
    InvoiceExtraction,
    WindowSettings,
    read_invoice,
    rule_values_left,
)
from kaagaz.extraction.links import link_identifiers
from kaagaz.extraction.quotes import ReaderText
from kaagaz.extraction.values import ExtractedValue
from kaagaz.pipeline import DocumentFacts, LinkIdentifier

if TYPE_CHECKING:
    from kaagaz.settings import Settings


@dataclass(frozen=True, slots=True)
class ModelBudget:
    """What ``understand`` may spend (ML amendment 5): the card's input and reply, the
    context window, and the invoice windows (None: no invoice windows are read)."""

    card_max_chars: int  # CARD_MAX_CHARS
    card_max_tokens: int  # MODEL_CARD_MAX_TOKENS
    num_ctx: int  # MODEL_NUM_CTX
    invoice_windows: WindowSettings | None

    @classmethod
    def from_settings(cls, settings: "Settings") -> "ModelBudget":
        return cls(
            settings.card_max_chars,
            settings.model_card_max_tokens,
            settings.model_num_ctx,
            WindowSettings.from_settings(settings),
        )


def model_config(settings: "Settings") -> dict[str, Any]:
    """The model stage's set-up, kept with every run (ML amendment 10): which card prompt
    and schema were used (their hash), and the budgets the calls ran with."""
    return {
        "card_prompt": CARD_PROMPT_HASH,
        "invoice_prompt": INVOICE_PROMPT_HASH,
        "model_num_ctx": settings.model_num_ctx,
        "card_max_chars": settings.card_max_chars,
        "card_max_tokens": settings.model_card_max_tokens,
        "extract_window_chars": settings.extract_window_chars,
        "extract_window_overlap": settings.extract_window_overlap,
        "extract_max_windows": settings.extract_max_windows,
        "window_max_tokens": settings.model_window_max_tokens,
    }


@dataclass(frozen=True, slots=True)
class Understanding:
    facts: DocumentFacts
    rule_values: list[ExtractedValue]  # cells and labelled.py values: labels.label_values
    quoted_values: list[ExtractedValue]  # the card's values: labels.label_quoted
    counts: CardCounts | None  # the card's counts; None with no card
    model_calls: int  # the card and the invoice windows called
    error_code: str | None  # "model_<code>" when a call failed
    invoice: InvoiceExtraction | None = None  # the invoice windows' reading, when they were read

    @property
    def quotes_returned(self) -> int:
        return self._card("quotes_returned") + (self.invoice.quotes.returned if self.invoice else 0)

    @property
    def dropped_quotes(self) -> int:
        return self._card("dropped_quotes") + (self.invoice.quotes.dropped if self.invoice else 0)

    @property
    def loose_quotes(self) -> int:
        return self._card("loose_quotes") + (self.invoice.quotes.loose if self.invoice else 0)

    @property
    def ambiguous_quotes(self) -> int:
        return self._card("ambiguous_quotes") + (self.invoice.quotes.ambiguous if self.invoice else 0)

    def _card(self, name: str) -> int:
        return 0 if self.counts is None else int(getattr(self.counts, name))


def understand(
    chat: ChatModel | None,
    reader: ReaderText,
    rule_values: Sequence[ExtractedValue],
    *,
    card_max_chars: int,
    card_max_tokens: int,
    num_ctx: int,
    run_id: str | None,
    beat: Callable[[int, int], None],
    invoice_windows: WindowSettings | None = None,
) -> Understanding:
    """The card for this document, or none, then its invoice windows (the module docstring)."""
    rules = list(rule_values)
    if chat is None or not reader.norm:
        return Understanding(DocumentFacts(), rules, [], None, 0, None)
    try:
        reply = ask_card(chat, reader, max_chars=card_max_chars, max_tokens=card_max_tokens, num_ctx=num_ctx)
    except (ModelUnavailable, ModelOutputError) as error:
        beat(1, 2)
        return Understanding(DocumentFacts(), rules, [], None, 1, "model_" + error.code)
    if reply is None:  # no page fitted the budget, so no call was made
        return Understanding(DocumentFacts(), rules, [], None, 0, None)
    beat(1, 2)
    card = locate_card(reply, reader, model=chat.chat_model, run_id=run_id)
    beat(2, 2)
    invoice: InvoiceExtraction | None = None
    if invoice_windows is not None and card.facts.doc_type == "invoice":
        invoice = read_invoice(
            chat,
            reader,
            chars=invoice_windows.chars,
            overlap=invoice_windows.overlap,
            max_windows=invoice_windows.max_windows,
            max_tokens=invoice_windows.max_tokens,
            num_ctx=num_ctx,
            beat=lambda done, total: beat(2 + done, 2 + total),
        )
    e4 = [] if invoice is None else invoice.values
    rules = rule_values_left(rules, e4)
    card = without_fields(card, {value.field for value in rules} | {value.field for value in e4})
    calls = 1 + (0 if invoice is None else invoice.model_calls)
    window_error = None if invoice is None else invoice.error_code
    return Understanding(card.facts, rules, card.values + e4, card.counts, calls, window_error, invoice)


def document_links(facts: DocumentFacts, values: Sequence[ExtractedValue]) -> tuple[LinkIdentifier, ...]:
    """This document's link identifiers (D5), only when it has a card (D6)."""
    return link_identifiers(values) if facts.card is not None else ()
