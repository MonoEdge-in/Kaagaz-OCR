"""#535 (Ask speed, PR 2): a drafted sentence over MAX_SENTENCE_CHARS is cut at its own sentence
ends in code (``draft.split_long``), each part citing what the sentence cited, instead of a second
model call; only a sentence that cannot be cut so is drafted again, as before. Every part still
goes through verify.py: a part of a sentence verify keeps is kept too."""

import logging
import random
import re
from typing import Any

import pytest

from kaagaz.ai.client import ModelOutputError
from kaagaz.answering.draft import MAX_SENTENCE_CHARS, DraftSentence, model_draft, split_long
from kaagaz.answering.evidence import number_evidence
from kaagaz.answering.verify import verify
from tests.test_answering_golden import INVOICE, QUESTION, A, FakeModel, evidence_blocks, service

EVIDENCE = number_evidence([INVOICE])
TEXTS = {1: INVOICE.text}

# Each verify keeps on its own over INVOICE; each under 120 characters; abbreviations, initials,
# amounts, dates and ids that a careless cut would break.
SHORT = [
    "The tax invoice INV-1042 was issued by Sharma Traders Pvt. Ltd. of Pune on 12/08/2026.",
    "Its sub total is Rs. 1,00,000 and its grand total is Rs. 1,20,000.",
    "R. K. Sharma of Sharma Traders signed it.",
    "The seller's GSTIN is 27XXXXXX234F1Z5!",
    "Is the grand total Rs. 1,20,000? Yes, as printed.",
    'The bill says "Grand Total: Rs. 1,20,000."',
    "The invoice No. INV-1042 is dated 12/08/2026, as printed at its top.",
    "The amount due is Rs. 1,20,000, which is the grand total.",
    "Nothing else on it is a total (e.g. no tax line is printed).",
    "It is dated 12 August 2026.",
]


def long_sentence(parts: list[str]) -> str:
    text = " ".join(parts)
    assert len(text) > MAX_SENTENCE_CHARS
    return text


def test_a_long_sentence_is_cut_at_its_sentence_ends_into_the_fewest_parts_that_fit() -> None:
    text = long_sentence(SHORT[:6])
    parts = split_long(text)
    assert parts is not None and len(parts) == 2
    assert all(len(part) <= MAX_SENTENCE_CHARS for part in parts)
    assert " ".join(parts) == text  # nothing lost, nothing added
    # Never after an abbreviation or an initial, nor inside an amount.
    for part in parts:
        assert not part.endswith(("Pvt.", "Ltd.", "Rs.", "R.", "K.", "No."))


@pytest.mark.parametrize(
    "joint",
    [
        "Sharma Traders Pvt. Ltd. Pune",  # an abbreviation before a capital
        "signed by R. K. Sharma",  # initials
        "worth Rs. Twelve lakh",  # a currency word before a capital
        "paid on 12. August 2026",  # a date verify reads whole
        "see e.g. The terms",  # a dotted abbreviation
    ],
)
def test_no_cut_where_a_full_stop_is_not_a_sentence_end(joint: str) -> None:
    filler = "word " * 40  # either side fits, the whole does not: only a cut at the joint would do
    text = f"{filler}{joint} {filler}"
    assert len(text) > MAX_SENTENCE_CHARS
    assert split_long(text) is None
    assert split_long(text.replace(joint, "the end. Then")) is not None  # a real end is cut


def test_a_sentence_with_no_end_to_cut_at_cannot_be_cut() -> None:
    assert split_long("x" * (MAX_SENTENCE_CHARS + 1)) is None
    assert split_long(("word " * 40 + "and ") * 2 + "end.") is None


def test_every_part_of_a_sentence_verify_keeps_is_kept_by_verify() -> None:
    """The proof, over many joins of the short sentences: each long sentence verify keeps whole
    (verify has no length rule) is cut, and verify keeps every part, citing the same source."""
    pick = random.Random(535)  # noqa: S311 - a fixed seed picks test sentences, not a secret
    checked = 0
    for _ in range(300):
        chosen = pick.sample(SHORT, k=pick.randint(4, len(SHORT)))
        text = " ".join(chosen)
        if len(text) <= MAX_SENTENCE_CHARS:
            continue
        assert verify([DraftSentence(text, [1])], TEXTS), text
        parts = split_long(text)
        assert parts is not None, text
        kept = verify([DraftSentence(part, [1]) for part in parts], TEXTS)
        assert [s.text for s in kept] == parts
        assert all(s.evidence == [1] for s in kept)
        checked += 1
    assert checked > 200


def test_a_part_verify_cannot_back_is_dropped_on_its_own() -> None:
    wrong = "It also says the tax is Rs. 99,999."  # no such amount in the evidence
    text = long_sentence([*SHORT[:5], wrong])
    assert verify([DraftSentence(text, [1])], TEXTS) == []  # whole: dropped
    parts = split_long(text)
    assert parts is not None
    kept = [s.text for s in verify([DraftSentence(part, [1]) for part in parts], TEXTS)]
    assert kept and all("99,999" not in part for part in kept)


def replies(*answers: dict[str, Any]) -> FakeModel:
    queue = list(answers)
    return FakeModel(lambda system, user: queue.pop(0))


def answered(*sentences: tuple[str, list[int]]) -> dict[str, Any]:
    return {"status": "answered", "sentences": [{"text": t, "evidence": e} for t, e in sentences]}


def test_a_long_sentence_is_split_with_no_second_model_call() -> None:
    text = long_sentence(SHORT[:6])
    model = replies(answered(("The grand total is Rs. 1,20,000.", [1]), (text, [1, 2])))

    draft = model_draft(model, QUESTION, EVIDENCE, max_tokens=512)

    assert len(model.prompts) == 1 and draft.redrafts == 0
    assert [s.text for s in draft.sentences] == [
        "The grand total is Rs. 1,20,000.",
        *(split_long(text) or []),
    ]
    assert all(s.evidence == [1, 2] for s in draft.sentences[1:])  # each part cites what it cited


def test_only_a_sentence_that_cannot_be_cut_is_drafted_again_as_before() -> None:
    good = answered(("The grand total is Rs. 1,20,000.", [1]))
    model = replies(answered(("x" * (MAX_SENTENCE_CHARS + 1), [1])), good)
    draft = model_draft(model, QUESTION, EVIDENCE, max_tokens=512)
    assert len(model.prompts) == 2 and draft.redrafts == 1
    assert "longer than 300 characters" in model.prompts[1][1]
    with pytest.raises(ModelOutputError) as caught:  # a second one is still answer_shape
        model_draft(replies(*[answered(("x" * 301, [1]))] * 2), QUESTION, EVIDENCE, max_tokens=512)
    assert caught.value.code == "answer_shape"


def test_a_reply_wrong_in_another_way_is_drafted_again_as_before() -> None:
    bad = answered(("y" * (MAX_SENTENCE_CHARS + 1), [1]), ("Fine.", [True]))  # a true/false id
    model = replies(bad, answered(("The grand total is Rs. 1,20,000.", [1])))
    assert model_draft(model, QUESTION, EVIDENCE, max_tokens=512).redrafts == 1


@pytest.mark.parametrize(("long", "redrafts"), [(True, 0), (False, 1)])
def test_the_timing_line_counts_the_redrafts(
    long: bool, redrafts: int, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []

    def reply(system: str, user: str) -> dict[str, Any]:
        calls.append(user)
        (n,) = [n for n, text in evidence_blocks(user).items() if "Grand Total" in text]
        text = long_sentence(SHORT[:6]) if long else "x" * (MAX_SENTENCE_CHARS + 1)
        return answered((text if len(calls) == 1 else "The grand total is Rs. 1,20,000.", [n]))

    caplog.set_level(logging.INFO, logger="kaagaz.answering.timing")
    answer = service(model=FakeModel(reply))[0].answer(A, QUESTION)

    assert answer.status == "answered" and len(calls) == 1 + redrafts
    (line,) = [r.getMessage() for r in caplog.records if r.name == "kaagaz.answering.timing"]
    assert re.search(rf" redrafts={redrafts}\b", line), line
