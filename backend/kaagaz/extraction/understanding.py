"""The model stage of the pipeline (PRD 2 D1, D5, D6), kept out of pipeline.py.

* No model (``MODEL_ENABLED=false``): no call, and so no card, no type and no
  links; the rule values go on as they are. The list shows a document with no
  type as "Unknown" (D6): that is the frontend's.
* A model: one card call (``card.read_card``), then ``beat(1, 1)``, so the
  job's heartbeat and progress move after the call (plan 4.2). A document with
  no text, or with no page that fits the budget, makes no call.
* The model down or its reply unusable (``ModelUnavailable``,
  ``ModelOutputError``): no card, the rule values are kept, and ``error_code``
  is ``"model_"`` plus the error's code, for the run (the PRD edge case "the
  model is down halfway").
* A card value whose field the rule values already have is dropped
  (PROVISIONAL, plan conflict 15), so nothing is counted twice (bug B2 of
  PRD 1); with the reserved key-fact names (``card.RESERVED_KEY_FACT_FIELDS``) a
  card key fact never adds a second total either.

The two value lists go to their own labeller (ML amendment 2): ``rule_values``
(cells and labelled.py) to ``labels.label_values``, ``quoted_values`` (the
card's, and PR 6's invoice values) to ``labels.label_quoted``.

``document_links`` gives the link identifiers (D5) from the document's final
values, and only when it has a card (D6).

Time: one model call and ``card.read_card``'s cost, plus O(v) for the
de-duplication.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.extraction.card import CardCounts, ChatModel, read_card
from kaagaz.extraction.links import link_identifiers
from kaagaz.extraction.quotes import ReaderText
from kaagaz.extraction.values import ExtractedValue
from kaagaz.pipeline import DocumentFacts, LinkIdentifier


@dataclass(frozen=True, slots=True)
class Understanding:
    facts: DocumentFacts
    rule_values: list[ExtractedValue]  # cells and labelled.py values: labels.label_values
    quoted_values: list[ExtractedValue]  # the card's values: labels.label_quoted
    counts: CardCounts | None  # the card's counts; None with no card
    model_calls: int
    error_code: str | None  # "model_<code>" when the call failed


def understand(
    chat: ChatModel | None,
    reader: ReaderText,
    rule_values: Sequence[ExtractedValue],
    *,
    card_max_chars: int,
    card_max_tokens: int,
    num_ctx: int,
    run_id: str,
    beat: Callable[[int, int], None],
) -> Understanding:
    """The card for this document, or none (the module docstring)."""
    rules = list(rule_values)
    if chat is None or not reader.norm:
        return Understanding(DocumentFacts(), rules, [], None, 0, None)
    try:
        card = read_card(
            chat, reader, max_chars=card_max_chars, max_tokens=card_max_tokens, num_ctx=num_ctx, run_id=run_id
        )
    except (ModelUnavailable, ModelOutputError) as error:
        beat(1, 1)
        return Understanding(DocumentFacts(), rules, [], None, 1, "model_" + error.code)
    if card is None:  # no page fitted the budget, so no call was made
        return Understanding(DocumentFacts(), rules, [], None, 0, None)
    beat(1, 1)
    known = {value.field for value in rules}
    quoted = [value for value in card.values if value.field not in known]
    return Understanding(card.facts, rules, quoted, card.counts, 1, None)


def document_links(facts: DocumentFacts, values: Sequence[ExtractedValue]) -> tuple[LinkIdentifier, ...]:
    """This document's link identifiers (D5), only when it has a card (D6)."""
    return link_identifiers(values) if facts.card is not None else ()
