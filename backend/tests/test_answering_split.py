"""#535 (Ask speed, PR 2): a drafted sentence over MAX_SENTENCE_CHARS is cut at its own sentence
ends in code (``draft.split_long``), each part citing what the sentence cited, instead of a second
model call; only a sentence that cannot be cut so is drafted again, as before. Every part still
goes through verify.py: a part of a sentence verify keeps is kept too. Review of #535: only a
sentence citing one source is cut (more than one: drafted again, as no code can tell which part
each source backs); a cut that would pass the answer's MAX_SENTENCES is drafted again; and when
verify drops any part, every part goes (``service._kept``), so no part stands without its subject."""

import logging
import random
import re
from typing import Any

import pytest

from kaagaz.ai.client import ModelOutputError
from kaagaz.answering.draft import MAX_SENTENCE_CHARS, Draft, DraftSentence, model_draft, split_long
from kaagaz.answering.evidence import number_evidence
from kaagaz.answering.service import _kept
from kaagaz.answering.verify import verify
from tests.test_answering_golden import INVOICE, QUESTION, A, FakeModel, evidence_blocks, service

EVIDENCE = number_evidence([INVOICE])
TEXTS = {1: INVOICE.text}
TEXTS_TWO = {**TEXTS, 2: "Payment terms: payment due within 30 days of the invoice date."}

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
        # Review of #535: Indian abbreviations, and any token with a slash.
        "paid to M/s. Shree Ganesh Traders",
        "at Tal. Haveli, Dist. Pune",
        "signed by Adv. Patil",
        "the plot Sy. No. 45/2 Kothrud",
        "near Opp. Station Road",
        "Sau. Sunita and Chi. Rahul",
        "in the A/C. Holder name",
        "Nr. Bus Stand, Bldg. Two",
        "for Prof. Joshi and Hon. Court",
        "Kum. Asha of Ta. Mulshi",
        "\u092a\u0942\u0930\u094d\u0923\u0964 Then",  # the danda is never a cut
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
    (verify has no length rule) and citing one source is cut with no second call, and verify keeps
    every part, citing that source; one citing two sources is drafted again, never cut."""
    pick = random.Random(535)  # noqa: S311 - a fixed seed picks test sentences, not a secret
    checked = {1: 0, 2: 0}
    good = answered(("The grand total is Rs. 1,20,000.", [1]))
    for _ in range(300):
        chosen = pick.sample(SHORT, k=pick.randint(4, len(SHORT)))
        text = " ".join(chosen)
        if len(text) <= MAX_SENTENCE_CHARS:
            continue
        cited = pick.choice([[1], [1, 2]])
        assert verify([DraftSentence(text, cited)], TEXTS_TWO), text
        model = replies(answered((text, cited)), good)
        draft = model_draft(model, QUESTION, EVIDENCE, max_tokens=512)
        checked[len(cited)] += 1
        if len(cited) > 1:
            assert (len(model.prompts), draft.redrafts) == (2, 1)
            continue
        parts = split_long(text)
        assert parts is not None and len(model.prompts) == 1, text
        assert [s.text for s in draft.sentences] == parts
        kept = verify([DraftSentence(part, [1]) for part in parts], TEXTS)
        assert [s.text for s in kept] == parts
        assert all(s.evidence == [1] for s in kept)
        assert [s.text for s in _kept(draft, TEXTS)] == parts
    assert min(checked.values()) > 100


def test_when_verify_drops_one_part_every_part_of_that_sentence_goes() -> None:
    wrong = "It also says the tax is Rs. 99,999."  # no such amount in the evidence
    text = long_sentence([*SHORT[:5], wrong])
    assert verify([DraftSentence(text, [1])], TEXTS) == []  # whole: dropped
    short = ("The grand total is Rs. 1,20,000.", [1])
    draft = model_draft(replies(answered((text, [1]), short)), QUESTION, EVIDENCE, max_tokens=512)
    assert len(draft.sentences) == 3  # cut in two, then the short one
    assert verify(draft.sentences[:2], TEXTS) != []  # verify alone would keep a part
    assert [s.text for s in _kept(draft, TEXTS)] == [short[0]]
    # A part of one sentence never takes another's down: two cut sentences, one wrong.
    good = DraftSentence(short[0], [1], part_of=0)
    bad = DraftSentence("The tax is Rs. 99,999.", [1], part_of=1)
    other = DraftSentence("Its sub total is Rs. 1,00,000.", [1], part_of=1)
    assert [s.text for s in _kept(Draft("answered", [good, bad, other]), TEXTS)] == [good.text]


def test_the_service_drops_every_part_of_a_sentence_one_part_of_which_fails() -> None:
    text = long_sentence([*SHORT[:5], "It also says the tax is Rs. 99,999."])

    def reply(system: str, user: str) -> dict[str, Any]:
        (n,) = [n for n, block in evidence_blocks(user).items() if "Grand Total" in block]
        return answered((text, [n]), ("The grand total is Rs. 1,20,000.", [n]))

    answer = service(model=FakeModel(reply))[0].answer(A, QUESTION)
    assert [s.text for s in answer.sentences] == ["The grand total is Rs. 1,20,000."]


def replies(*answers: dict[str, Any]) -> FakeModel:
    queue = list(answers)
    return FakeModel(lambda system, user: queue.pop(0))


def answered(*sentences: tuple[str, list[int]]) -> dict[str, Any]:
    return {"status": "answered", "sentences": [{"text": t, "evidence": e} for t, e in sentences]}


def test_a_long_sentence_citing_one_source_is_split_with_no_second_model_call() -> None:
    text = long_sentence(SHORT[:6])
    model = replies(answered(("The grand total is Rs. 1,20,000.", [1]), (text, [1, 1])))

    draft = model_draft(model, QUESTION, EVIDENCE, max_tokens=512)

    assert len(model.prompts) == 1 and draft.redrafts == 0
    assert [s.text for s in draft.sentences] == [
        "The grand total is Rs. 1,20,000.",
        *(split_long(text) or []),
    ]
    assert all(s.evidence == [1, 1] and s.part_of == 1 for s in draft.sentences[1:])
    assert draft.sentences[0].part_of is None


def test_a_long_sentence_citing_two_sources_is_drafted_again() -> None:
    good = answered(("The grand total is Rs. 1,20,000.", [1]))
    model = replies(answered((long_sentence(SHORT[:6]), [1, 2])), good)
    draft = model_draft(model, QUESTION, EVIDENCE, max_tokens=512)
    assert (len(model.prompts), draft.redrafts) == (2, 1)
    assert [s.text for s in draft.sentences] == ["The grand total is Rs. 1,20,000."]


def test_a_cut_past_the_answers_sentence_cap_is_drafted_again() -> None:
    short = ("The grand total is Rs. 1,20,000.", [1])
    good = answered(short)
    fits = replies(answered(short, (long_sentence(SHORT[:6]), [1])), good)  # 1 + 2 parts: 3
    assert model_draft(fits, QUESTION, EVIDENCE, max_tokens=512).redrafts == 0
    over = replies(answered(short, (long_sentence(SHORT[:6]), [1]), short), good)  # 1 + 2 + 1: 4
    draft = model_draft(over, QUESTION, EVIDENCE, max_tokens=512)
    assert (len(over.prompts), draft.redrafts, len(draft.sentences)) == (2, 1, 1)


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
