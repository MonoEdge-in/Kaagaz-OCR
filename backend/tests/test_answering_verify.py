"""No source, no answer: the verify step (kaagaz/answering/verify.py)."""

import logging
import random
import re
from decimal import Decimal

import pytest

from kaagaz.answering.draft import DraftSentence
from kaagaz.answering.verify import numbers_in, verify

EVIDENCE = {
    1: "Invoice No: INV-1042\nInvoice Date: 12/08/2026\nGrand Total: Rs. 1,20,000",
    2: "Sub Total: 1,00,000.00\nCGST: 9,000.50",
}


def test_keeps_a_sentence_whose_numbers_are_in_its_evidence() -> None:
    sentence = DraftSentence("The grand total of INV-1042 is Rs. 1,20,000.", [1])
    assert verify([sentence], EVIDENCE) == [sentence]


def test_drops_a_sentence_that_cites_nothing() -> None:
    assert verify([DraftSentence("The total is due.", [])], EVIDENCE) == []


def test_drops_a_sentence_that_cites_an_unknown_id() -> None:
    assert verify([DraftSentence("Grand Total: Rs. 1,20,000", [1, 7])], EVIDENCE) == []


def test_drops_a_sentence_with_an_invented_number() -> None:
    invented = DraftSentence("With tax the total is 1,41,600.", [1])
    kept = DraftSentence("The grand total is 1,20,000.", [1])
    assert verify([invented, kept], EVIDENCE) == [kept]


def test_a_number_must_be_in_the_evidence_the_sentence_cites() -> None:
    # 9,000.50 is in evidence 2, but the sentence cites only evidence 1.
    assert verify([DraftSentence("CGST was 9,000.50.", [1])], EVIDENCE) == []
    assert verify([DraftSentence("CGST was 9,000.50.", [2])], EVIDENCE) != []


def test_numbers_compare_by_value_not_by_writing() -> None:
    assert verify([DraftSentence("The grand total is 120000.", [1])], EVIDENCE) != []
    assert verify([DraftSentence("The subtotal is 100000.", [2])], EVIDENCE) != []
    assert verify([DraftSentence("The grand total is 1,20,000.01.", [1])], EVIDENCE) == []


def test_a_date_counts_as_its_parts() -> None:
    assert verify([DraftSentence("It is dated 12 August 2026.", [1])], EVIDENCE) != []
    assert verify([DraftSentence("It is dated 13 August 2026.", [1])], EVIDENCE) == []


def test_repeated_ids_are_kept_once_and_order_is_kept() -> None:
    first = DraftSentence("Invoice INV-1042.", [1, 1])
    second = DraftSentence("CGST was 9,000.50.", [2])
    assert verify([first, second], EVIDENCE) == [DraftSentence("Invoice INV-1042.", [1]), second]


def test_numbers_in_reads_indian_and_decimal_writing() -> None:
    # INV-7 is an id (a letter, a digit and at least 4 characters), so its 7 is not a number.
    assert numbers_in("Rs. 1,20,000 and 12,34,567.50, INV-7") == {Decimal(120000), Decimal("1234567.50")}


# --- exact atoms (critical finding 1, the part the lead decides) -------------------

BILL = (
    "Tax Invoice\nSeller: Sharma Traders\nGSTIN: 27ABCDE1234F1Z5\n"
    "Buyer: Mehta Textiles\nBuyer GSTIN: 24PQRST5678K1Z2\n"
    "Invoice No: INV-1042\nInvoice Date: 12/08/2026\nFY 2025-26\nQty: 2\n"
    "Sub Total: Rs. 1,00,000\nCGST @ 9%: Rs. 9,000\nSGST @ 9%: Rs. 9,000\n"
    "Grand Total: \u20b91,18,000/-"
)
HINDI = "कुल रकम: १,१८,०००\nदिनांक: १२/०८/२०२६"  # noqa: RUF001 - Devanagari digits, on purpose
OTHER_BILL = "Invoice No: INV-2001\nGrand Total: Rs. 2,50,000"
PIECES = {1: BILL, 2: HINDI, 3: OTHER_BILL}
INVISIBLE = ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad"]
# 1,18,000 in the digits of other scripts.
FOREIGN = {
    "fullwidth": "１,１８,０００",  # noqa: RUF001 - on purpose
    "Gujarati": "૧,૧૮,૦૦૦",  # noqa: RUF001 - on purpose
    "Bengali": "১,১৮,০০০",  # noqa: RUF001 - on purpose
    "Arabic-Indic": "١,١٨,٠٠٠",  # noqa: RUF001 - on purpose
}


def kept(text: str, cited: tuple[int, ...] = (1,)) -> bool:
    return verify([DraftSentence(text, list(cited))], PIECES) != []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("The bill is for Rs. 2026.", id="the year as an amount"),
        pytest.param("The tax was Rs. 1234.", id="a GSTIN fragment"),
        pytest.param("CGST was Rs. 9.", id="Rs 9 from 9%"),
        pytest.param("The total was Rs. 1042.", id="Rs 1042 from INV-1042"),
        pytest.param("The order is worth Rs. 2 crore.", id="Rs 2 crore from Qty 2"),
        pytest.param("It is dated 8 December 2026.", id="day and month swapped"),
        pytest.param("It is dated 12 September 2026.", id="another month reusing the parts"),
        pytest.param("It is dated 12/26/2026.", id="an invalid date"),
        pytest.param("It is dated 12/08/26.", id="a date with a guessed century"),
        *[
            pytest.param(f"The total is Rs. 9{ch}9,000.", id=f"U+{ord(ch):04X} inside a number")
            for ch in INVISIBLE
        ],
        *[pytest.param(f"The grand total is Rs. {d}.", id=f"{name} digits") for name, d in FOREIGN.items()],
        pytest.param("The grand total is Rs. 1,18,000².", id="a superscript"),
        pytest.param("CGST is ½ of the tax.", id="a fraction"),
        pytest.param("It is invoice ① of the year.", id="a circled digit"),
        pytest.param("It is invoice Ⅻ of the year.", id="a Roman numeral"),
        pytest.param("The grand total is ₹ ९,९९,९९९.", id="an invented Devanagari number"),
        pytest.param("The grand total is Rs. 1,1८,000.", id="ASCII and Devanagari digits in one token"),
        pytest.param("CGST is nine percent.", id="a number word before percent"),
        pytest.param("The total is two lakh rupees.", id="a number word before lakh"),
    ],
)
def test_must_drop(text: str) -> None:
    assert not kept(text)


MUST_KEEP = [
    "The grand total is ₹1,18,000/-.",
    "The grand total is Rs 118000.",
    "The grand total is 118,000.",
    "The grand total is 1,18,000.00.",
    "It is dated 12th August 2026.",
    "It is dated August 12, 2026.",
    "It is dated 12-08-2026.",
    "CGST is 9 percent.",
    "The invoice number is INV-1042.",
    "It is for FY 2025-26.",
]
# An English answer over Devanagari evidence: its digits are folded on both sides.
HINDI_KEEP = ["The total amount is Rs. 1,18,000.", "It is dated 12 August 2026."]


@pytest.mark.parametrize("text", MUST_KEEP)
def test_must_keep(text: str) -> None:
    assert kept(text)


@pytest.mark.parametrize("text", HINDI_KEEP)
def test_an_english_answer_over_devanagari_evidence_is_kept(text: str) -> None:
    assert kept(text, (2,))


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="Part B 1: atoms bound to their own line")
@pytest.mark.parametrize(
    ("text", "cited"),
    [
        pytest.param("The seller's GSTIN is 24PQRST5678K1Z2.", (1,), id="the buyer's GSTIN as the seller's"),
        pytest.param("The grand total is Rs. 1,00,000.", (1,), id="the sub total as the grand total"),
        pytest.param("The grand total of INV-1042 is Rs. 2,50,000.", (1, 3), id="mixing two pieces"),
        pytest.param("The seller is Gupta Stores.", (1,), id="a wrong seller name"),
    ],
)
def test_part_b_1_only(text: str, cited: tuple[int, ...]) -> None:
    assert not kept(text, cited)


def test_changing_one_atom_of_a_kept_sentence_drops_it() -> None:
    rng = random.Random(20261001)  # noqa: S311 - a fixed seed picks test cases, not a secret
    pairs = [(text, (1,)) for text in MUST_KEEP] + [(text, (2,)) for text in HINDI_KEEP]
    for _ in range(500):
        text, cited = rng.choice(pairs)
        spot = rng.choice(list(re.finditer(r"[0-9]|August", text)))
        if spot[0] == "August":
            new = rng.choice(["July", "September", "December"])
        else:
            new = rng.choice([digit for digit in "0123456789" if digit != spot[0]])
        mutated = text[: spot.start()] + new + text[spot.end() :]
        assert not kept(mutated, cited), mutated


def test_evidence_parts_count_only_as_what_they_are() -> None:
    # 12/08/2026 backs that date (and "12 August", "August 2026"), never 12, 8 or 2026.
    assert kept("It was billed on 12 August.")
    assert kept("It was billed in August 2026.")
    assert not kept("It was billed in July 2026.")
    assert not kept("It holds 12 items.")


def test_drops_are_logged_by_reason_with_codes_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="kaagaz.answering.verify")
    sentences = [
        DraftSentence("The total was Rs. 1042.", [1]),
        DraftSentence("The total was Rs. 2026.", [1]),
        DraftSentence("CGST is nine percent.", [1]),
        DraftSentence("Sharma Traders.", []),
        DraftSentence("The grand total is Rs 118000.", [1]),
    ]
    assert len(verify(sentences, PIECES)) == 1
    assert [record.getMessage() for record in caplog.records] == [
        "answer sentences dropped: missing_number=2 no_source=1 number_word=1"
    ]
