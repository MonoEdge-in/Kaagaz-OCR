"""The answer checks the second code review of PR 2 asked for (fix/s2-answer-checks): English
names over Devanagari evidence, every Devanagari word, every cited hit on its own, numbers
by value from the question's own text, and dashes that join a number to more."""

import dataclasses

import pytest

from kaagaz.answering.draft import DraftSentence
from kaagaz.answering.evidence import has_token, numbers_asked
from kaagaz.answering.hinglish import normalise
from kaagaz.answering.verify import FUNCTION_WORDS, verify
from kaagaz.chunking.script import ascii_digits
from tests.test_answering_cross_language import OWNER, OWNER_QUESTION, ask, says
from tests.test_answering_golden import FakeHit, piece

SURE = 0.9  # a meaning score above the cut, so the gate passes on the score
SATBARA = dataclasses.replace(
    piece("satbara.csv", "गट क्रमांक ४७ | भोगवटादार रामचंद्र गणपत पाटील"), meaning_score=SURE
)
GAT_47 = piece("satbara.csv", "gat 47 malak ramchandra patil")
NB_HYPHEN, EN_DASH = chr(0x2011), chr(0x2013)  # written as code points, never typed


@pytest.mark.parametrize(
    ("question", "reply"),
    [
        (OWNER_QUESTION, "The owner (भोगवटादार) is Sita Deshmukh."),
        ("Who owns gat 47?", "The भोगवटादार of gat 47 is Sita Deshmukh."),
        ("Who owns gat 47?", "गट ४७: Sita Deshmukh."),
    ],
)
def test_an_english_name_devanagari_evidence_does_not_hold_is_needs_review(question: str, reply: str) -> None:
    answer = ask(question, [SATBARA], says(reply))
    assert (answer.status, answer.label) == ("answered", "needs_review")


def test_english_words_the_glossary_gives_the_cited_text_stay_verified() -> None:
    sure = dataclasses.replace(OWNER, meaning_score=SURE)
    answer = ask(OWNER_QUESTION, [sure], says("The owner is रामचंद्र गणपत पाटील."))
    assert (answer.status, answer.label) == ("answered", "verified")  # owner: मालक is in the text


@pytest.mark.parametrize(
    ("sentence", "evidence"),
    [("भुगतान न हुआ।", "भुगतान हुआ"), ("मालक रामचंद्र पाटील न आहे.", "मालक रामचंद्र पाटील")],
)
def test_every_devanagari_word_is_checked_however_short(sentence: str, evidence: str) -> None:
    assert verify([DraftSentence(sentence, [1])], {1: evidence}) == []


def test_ki_is_not_a_function_word_but_va_is() -> None:
    assert "की" not in FUNCTION_WORDS and "व" in FUNCTION_WORDS  # की is "or" in Marathi (PROVISIONAL)
    assert verify([DraftSentence("कुल ₹ ४५० की ५००.", [1])], {1: "कुल ₹ ४५०"}) == []


@pytest.mark.parametrize(
    ("question", "hits", "reply"),
    [
        (
            OWNER_QUESTION,
            [piece("deed.pdf", "Land record of the village"), OWNER],
            "The owner is रामचंद्र गणपत पाटील.",
        ),
        (
            "Who owns gat 48?",
            [GAT_47, piece("village.pdf", "gat 48 village vadgaon")],
            "The owner of gat 48 is ramchandra patil.",
        ),
    ],
)
def test_every_hit_a_sentence_cites_must_pass_the_gate_by_itself(
    question: str, hits: list[FakeHit], reply: str
) -> None:
    answer = ask(question, hits, says(reply, 1, 2))
    assert (answer.status, answer.label) == ("answered", "needs_review")


INVOICE = piece("bill.pdf", "Invoice No: INV-1042\nGrand Total: Rs. 1,20,000")


@pytest.mark.parametrize(
    ("question", "hits", "expected"),
    [
        ("Which invoice is for Rs 1,20,000?", [INVOICE], ["Grand Total: Rs. 1,20,000"]),  # as on main
        ("Which invoice is for 120000?", [INVOICE], ["Invoice No: INV-1042", "Grand Total: Rs. 1,20,000"]),
        (
            "Which invoice is for Rs 450?",
            [piece("bill.pdf", "Bill No: B-77\nTotal: Rs. 450.00")],
            None,
        ),  # as on main
    ],
)
def test_an_amount_asked_is_one_number_compared_by_value(
    question: str, hits: list[FakeHit], expected: list[str] | None
) -> None:
    answer = ask(question, hits)
    if expected is None:
        assert answer.status == "not_found"  # "invoice" is not in that bill, as on main
    else:
        assert (answer.status, answer.label, [s.text for s in answer.sentences]) == (
            "answered",
            "verified",
            expected,
        )


def test_numbers_asked_are_read_whole_from_the_question() -> None:
    cases = {
        "Which invoice is for Rs 1,20,000?": ["1,20,000"],
        "What is the grand total of INV-1042?": ["inv-1042"],
        "Gat 45/2 owner?": ["45/2"],
        "Invoices in August 2026": [],  # a date's year is no keyword
        "Profit in the last 30 days": [],
    }
    assert {q: numbers_asked(q, normalise(q).keywords) for q in cases} == cases


@pytest.mark.parametrize(
    ("keyword", "text", "found"),
    [
        ("450", "Rs. 450.00", True),
        ("120000", "Rs. 1,20,000", True),
        ("1,20,000", "120000", True),
        ("48", "1,48,000", False),
        ("48", "4.48", False),
        ("48", "148", False),
        ("48", "1180,00 48x", False),  # bad grouping is no number; 48x is no token
        ("48", "gat 48-A", False),
        ("48", f"gat 48{NB_HYPHEN}A", False),
        ("48", f"gat 48{EN_DASH}49", False),
        ("48", f"gat 47{EN_DASH}48", False),
        ("inv-1042", "INV-1042", True),
        ("inv-1042", "INV-10421", False),
    ],
)
def test_numbers_compare_by_value_and_dashes_join_them_to_more(keyword: str, text: str, found: bool) -> None:
    assert has_token(keyword, ascii_digits(text).casefold()) is found


def test_a_receipt_for_48_00_next_to_gat_47_is_never_verified() -> None:
    close = dataclasses.replace(GAT_47, meaning_score=SURE, fuzzy_score=0.733)
    answer = ask(
        "Who owns gat 48?",
        [close, piece("r.pdf", "Paid Rs 48.00 cash")],
        says("The owner is ramchandra patil."),
    )
    assert answer.label != "verified"


@pytest.mark.parametrize(
    "text", ["gat 48-A malak ramchandra patil", f"gat 48{EN_DASH}49 malak ramchandra patil"]
)
def test_gat_48_is_not_found_in_48_a_or_48_to_49(text: str) -> None:
    hit = dataclasses.replace(piece("satbara.csv", text), meaning_score=SURE)
    assert ask("Who owns gat 48?", [hit], says("The owner is ramchandra patil.")).status == "not_found"


# --- the third code review: fail closed ------------------------------------------------------

G47 = dataclasses.replace(GAT_47, meaning_score=SURE)


@pytest.mark.parametrize("joined", [f"48{EN_DASH}49", f"48{NB_HYPHEN}A"])
@pytest.mark.parametrize("with_model", [True, False])
def test_a_number_joined_by_a_unicode_dash_in_the_question_is_never_lost(
    joined: str, with_model: bool
) -> None:
    question = f"Who owns gat {joined}?"
    if with_model:
        answer = ask(question, [G47], says("The owner of gat 47 is ramchandra patil."))
    else:
        answer = ask(
            question,
            [dataclasses.replace(piece("s.csv", "gat 47 owns ramchandra patil"), meaning_score=SURE)],
        )
    assert answer.status == "not_found"


def test_numbers_asked_never_drop_a_digit_keyword() -> None:
    assert numbers_asked(f"Who owns gat 48{EN_DASH}49?", ["owns", "gat", "48", "49"]) == ["48-49"]
    assert numbers_asked(f"Who owns gat 48{NB_HYPHEN}A?", ["owns", "gat", "48"]) == ["48-a"]
    assert numbers_asked("Who owns gat 48?", ["owns", "gat", "48", "7"]) == ["48", "7"]  # 7: no token, kept


def test_a_joined_number_asked_is_found_as_written() -> None:
    record = dataclasses.replace(piece("s.csv", "gat 48-A malak ramchandra patil"), meaning_score=SURE)
    answer = ask("Who owns gat 48-A?", [record], says("The owner of gat 48-A is ramchandra patil."))
    assert (answer.status, answer.label) == ("answered", "verified")
    assert ask("gat 48 malak?", [G47], says("The owner of gat 47 is ramchandra patil.")).status == "not_found"


@pytest.mark.parametrize(
    "hits",
    [
        [dataclasses.replace(SATBARA, text=f"{SATBARA.text}\nPage 1 of 1")],
        [dataclasses.replace(SATBARA, text=f"{SATBARA.text} | Rs GSTIN")],
        [
            SATBARA,
            dataclasses.replace(
                piece("e.pdf", "Land record of the village gat 47 owner"), meaning_score=SURE
            ),
        ],
    ],
)
def test_latin_words_in_the_cited_text_never_turn_the_name_check_off(hits: list[FakeHit]) -> None:
    answer = ask(
        "Who owns gat 47?", hits, says("The भोगवटादार of gat 47 is Sita Deshmukh.", *range(1, len(hits) + 1))
    )
    assert (answer.status, answer.label) == ("answered", "needs_review")


@pytest.mark.parametrize(
    ("question", "reply"),
    [
        ("Is Sita Deshmukh the owner of gat 47?", "Sita Deshmukh is the owner of gat 47."),
        ("Is Sita Deshmukh the owner of gat 47?", "Sita Deshmukh is the भोगवटादार of gat 47."),
        ("gat 47 ka malak kon hai sita deshmukh?", "Sita Deshmukh is the भोगवटादार of gat 47."),
    ],
)
def test_a_name_the_question_puts_forward_is_never_backed_by_the_question(question: str, reply: str) -> None:
    answer = ask(question, [SATBARA], says(reply))
    assert (answer.status, answer.label) == ("answered", "needs_review")


def test_the_question_rule_leaves_good_answers_verified() -> None:
    marathi = ask("Who owns gat 47?", [SATBARA], says("The owner is रामचंद्र गणपत पाटील."))
    english = ask("Who owns gat 47?", [G47], says("The owner of gat 47 is ramchandra patil."))
    assert (marathi.label, english.label) == ("verified", "verified")


@pytest.mark.parametrize(("keyword", "text"), [("48", "A-48"), ("48", "INV-48"), ("48", "A/48")])
def test_a_letter_joined_before_a_number_makes_it_an_id(keyword: str, text: str) -> None:
    folded = text.casefold()
    assert not has_token(keyword, folded)
    assert has_token(folded, folded)  # the id itself, asked as an id
