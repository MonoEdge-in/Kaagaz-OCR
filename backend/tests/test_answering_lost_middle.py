"""Nothing lost in the middle (#381; service step 20, answering/parts.py), with fakes.

A model reads the middle of a long prompt least closely. These tests pin what the service does
about it, never a model's attention: the evidence is laid out with the best at both ends of every
prompt and a citation still names its own page and spot; a Precision answer over a long scope
reads every part, a fact planted in the middle part is found and cited, and the parts stay within
their bounds. No real model is called (scripts/eval_position_recall.py measures one).
"""

import dataclasses
import logging
import re
import threading
from functools import partial
from typing import Any

import pytest

from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.answering import draft, parts
from kaagaz.answering.evidence import number_evidence
from kaagaz.answering.parts import read_part, split
from kaagaz.db.models import Agent, ChatChoice
from tests.test_answering_context import SETTINGS, A, FakeContext, Fused, answers, blocks, page
from tests.test_answering_golden import FakeModel

_BLOCK = re.compile(r'<evidence id="(\d+)"[^>]*>\n(?:Context: [^\n]*\n)?(.*?)\n</evidence>', re.DOTALL)
AGENT = Agent("fake-chat", "google-vertex", "Fake")
PRECISION, SWIFT = ChatChoice([AGENT], "high"), ChatChoice([AGENT], "medium")

FACT = "Plot 7 east boundary: Nala."
SAID = "The east boundary of plot 7 is Nala."
QUESTION = "What is the east boundary of plot 7?"
# Nine pages; the one fact is on page 5, the middle. No other page shares a word with the question.
FILLER = ["Witness signatures follow.", "Stamp duty paid in full.", "Annexure on the next sheet."]
PAGES = [page(n, FACT if n == 5 else f"{FILLER[n % 3]} Sheet {chr(64 + n)}.") for n in range(1, 10)]
# Each piece is about 30 tokens: 9 of them are more than 100, so a Precision answer reads 3 parts.
IN_PARTS = dataclasses.replace(SETTINGS, answer_parts_tokens=100, answer_parts_max=3)


class Reader(FakeModel):
    """Part calls name every block holding ``FACT`` (``named``: the reply instead); a draft cites
    the block holding it. ``parts`` and ``drafts`` keep the user prompts; ``fail``: the part
    holding this text raises it."""

    def __init__(self, *, named: list[int] | None = None, fail: tuple[str, Exception] | None = None) -> None:
        super().__init__()
        self.named, self.fail = named, fail
        self.parts: list[str] = []
        self.drafts: list[str] = []
        self.lock = threading.Lock()

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
        model: str | None = None,
        thinking: str | None = None,
        provider: str | None = None,
        deadline: float | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        found = {text: int(n) for n, text in _BLOCK.findall(user)}
        if system == parts.SYSTEM_PROMPT:
            with self.lock:
                self.parts.append(user)
            if self.fail is not None and self.fail[0] in found:
                raise self.fail[1]
            ids = self.named if self.named is not None else [n for text, n in found.items() if text == FACT]
            return {"facts": [{"evidence": n, "quote": FACT} for n in ids]}
        assert system == draft.SYSTEM_PROMPT
        self.drafts.append(user)
        if FACT not in found:
            return {"status": "not_found", "sentences": []}
        return {"status": "answered", "sentences": [{"text": SAID, "evidence": [found[FACT]]}]}


def ask(model: FakeModel, choice: ChatChoice, settings: Any = IN_PARTS) -> Any:
    return answers(Fused(PAGES), FakeContext(PAGES), model, settings).answer(A, QUESTION, choice=choice)


# --- (a) the order: the best at both ends, citations as before -------------------------------


def test_edges_puts_rank_1_first_rank_2_last_and_the_weakest_in_the_middle() -> None:
    assert draft.edges([1, 2, 3, 4, 5, 6, 7]) == [1, 3, 5, 7, 6, 4, 2]
    assert draft.edges([1, 2]) == [1, 2] and draft.edges([1]) == [1] and draft.edges([]) == []


def test_every_prompt_lays_its_evidence_out_with_the_best_at_both_ends_and_keeps_each_id() -> None:
    evidence = number_evidence(PAGES)  # ranked: page 1 best, page 9 weakest
    prompt = draft.user_prompt(QUESTION, evidence)

    shown = [(int(n), text) for n, text in _BLOCK.findall(prompt)]
    assert [n for n, _ in shown] == [1, 3, 5, 7, 9, 8, 6, 4, 2]
    assert all(text == evidence[n - 1].hit.text for n, text in shown)  # each id names its own piece
    assert len(prompt) == len(draft.user_prompt(QUESTION, list(reversed(evidence))))  # same size


def test_a_whole_scope_is_ranked_by_the_question_so_a_middle_fact_sits_at_the_prompts_start() -> None:
    model = Reader()

    answer = ask(model, SWIFT, SETTINGS)

    (prompt,) = model.drafts
    shown = blocks(prompt)
    # The fact is page 5 of 9 in reading order; ranked first, it opens the prompt.
    assert shown[0] == FACT and sorted(shown) == sorted(p.text for p in PAGES)
    assert shown != [p.text for p in PAGES]
    # Its number is still its reading-order place, and the citation opens page 5 at its spot.
    assert {text: int(n) for n, text in _BLOCK.findall(prompt)}[FACT] == 5
    assert [s.text for s in answer.sentences] == [SAID]
    (cited,) = answer.citations
    assert (cited.page_no, cited.ref_id, cited.regions) == (5, PAGES[4].piece_id, PAGES[4].regions)


def test_ranking_a_whole_scope_never_turns_a_down_embedding_into_an_outage() -> None:
    model = Reader()
    model.embed_error = ModelUnavailable("timeout")
    assert ask(model, SWIFT, SETTINGS).status == "answered"
    assert blocks(model.drafts[0])[0] == FACT  # the words alone still rank it first

    nothing = FakeModel(lambda system, user: {"status": "not_found", "sentences": []})
    nothing.embed_error = ModelUnavailable("timeout")
    answer = ask(nothing, SWIFT, SETTINGS)
    # The model answered "not found": a true not found, never said as an outage.
    assert answer.status == "not_found" and not answer.unavailable


# --- (b) read in parts: every part read, the middle fact found and cited, the bounds kept -----


def test_precision_reads_every_part_and_finds_and_cites_the_fact_in_the_middle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="kaagaz.answering.timing")
    model = Reader()

    answer = ask(model, PRECISION)

    assert len(model.parts) == 3  # ceil(9 pieces of ~30 tokens / 100), at most ANSWER_PARTS_MAX
    read = [text for prompt in model.parts for text in blocks(prompt)]
    assert sorted(read) == sorted(p.text for p in PAGES)  # every piece, each in exactly one part
    (final,) = model.drafts
    assert blocks(final) == [FACT]  # drafted only from what the parts named
    assert [s.text for s in answer.sentences] == [SAID]
    (cited,) = answer.citations
    assert (cited.page_no, cited.ref_id) == (5, PAGES[4].piece_id)
    (line,) = [r.getMessage() for r in caplog.records if r.name == "kaagaz.answering.timing"]
    assert line.endswith(" parts=3 redrafts=0")  # Changed on purpose (#535): redrafts after parts


def test_the_parts_never_number_more_than_answer_parts_max() -> None:
    model = Reader()
    ask(model, PRECISION, dataclasses.replace(IN_PARTS, answer_parts_tokens=1, answer_parts_max=2))
    assert len(model.parts) == 2 and len(model.drafts) == 1


def test_split_keeps_every_item_once_in_order_in_at_most_most_parts_none_empty() -> None:
    items = list(range(10))
    for sizes, part_tokens, most, count in (
        ([10] * 10, 25, 8, 4),  # 100 tokens in parts of 25
        ([10] * 10, 1, 3, 3),  # bounded by most
        ([10] * 10, 1000, 5, 1),  # it fits one part
        ([500, *([1] * 9)], 100, 4, 2),  # one huge piece is a part of its own, the rest another
    ):
        cut = split(items, sizes, part_tokens, most)
        assert [i for part in cut for i in part] == items and all(cut) and len(cut) == count, cut
    assert [len(part) for part in split(items, [10] * 10, 25, 8)] == [2, 3, 2, 3]  # about equal


def test_swift_and_a_short_scope_never_read_in_parts() -> None:
    swift = Reader()
    ask(swift, SWIFT)
    short = Reader()
    ask(short, PRECISION, dataclasses.replace(IN_PARTS, answer_parts_tokens=100_000))
    off = Reader()
    ask(off, PRECISION, dataclasses.replace(IN_PARTS, answer_parts_tokens=0))
    for model in (swift, short, off):
        assert model.parts == [] and len(blocks(model.drafts[0])) == len(PAGES)


@pytest.mark.parametrize("error", [ModelUnavailable("timeout"), ModelOutputError("parts_shape")])
def test_a_part_that_fails_leaves_the_answer_to_all_the_evidence_in_one_call(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="kaagaz.answering.service")
    model = Reader(fail=(PAGES[8].text, error))

    answer = ask(model, PRECISION)

    assert len(model.parts) == 3
    (final,) = model.drafts
    assert len(blocks(final)) == len(PAGES)  # today's path: every piece in one call
    assert [s.text for s in answer.sentences] == [SAID]
    codes = [getattr(r, "code", None) for r in caplog.records if "read in parts failed" in r.getMessage()]
    assert isinstance(error, ModelUnavailable | ModelOutputError) and codes == [error.code]


def test_no_part_naming_a_piece_is_not_found_without_a_draft() -> None:
    model = Reader(named=[])
    answer = ask(model, PRECISION)
    assert answer.status == "not_found" and len(model.parts) == 3 and model.drafts == []


def test_an_id_a_part_was_not_given_is_dropped() -> None:
    model = Reader(named=[5, 99])  # 5 is in one part only; 99 in none
    answer = ask(model, PRECISION)
    (final,) = model.drafts
    assert blocks(final) == [FACT] and answer.status == "answered"


def test_a_part_reply_of_the_wrong_shape_is_parts_shape() -> None:
    part = number_evidence(PAGES[:2])
    replies: list[dict[str, Any]] = [
        {"facts": "none"},
        {"facts": [{"evidence": True, "quote": "x"}]},
        {},
        {"facts": [1]},
    ]
    for reply in replies:
        model = FakeModel(partial(lambda reply, system, user: reply, reply))
        with pytest.raises(ModelOutputError) as raised:
            read_part(model, QUESTION, part, max_tokens=100)
        assert raised.value.code == "parts_shape"
    empty_quote = FakeModel(lambda system, user: {"facts": [{"evidence": 1, "quote": " "}]})
    assert read_part(empty_quote, QUESTION, part, max_tokens=100) == []
