"""No source, no answer: the verify step (kaagaz/answering/verify.py)."""

import logging
import random
import re
import unicodedata
from decimal import Decimal

import pytest

from kaagaz.answering.draft import DraftSentence
from kaagaz.answering.verify import (
    FUNCTION_WORDS,
    numbers_in,
    other_script_month,
    unchecked_cross_script,
    verify,
)

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
# Ids the way bills write them; the last label and its value are on two lines.
IDS = (
    "Invoice No. 1042\nBill No. 57\nHSN Code: 8471\nPIN: 400001\nMob: 98200 12345\n"
    "A/c No. 1234567890\nFlat No: A-1\nGat No. 45/2\nSurvey No: 7/12\nChallan No.\n2207"
)
LEDGER = "Opening Balance: 1,500.00 Cr\nClosing Balance: 2,750.00 Dr"  # Cr is credit, not crore
SHORT = "Invoice Date: 12/08/26"  # a two-digit year
WORDS = "Amount in words: Rupees One Lakh Eighteen Thousand Only"  # as Tally prints it
YEARLY = "Invoice Date: 12/08/2026\nGrand Total: Rs. 1,18,000"
PIECES = {1: BILL, 2: HINDI, 3: OTHER_BILL, 4: IDS, 5: LEDGER, 6: SHORT, 7: WORDS, 8: YEARLY}
INVISIBLE = ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad"]
# Marks that are not format characters but still hide in or after a number.
HIDING = ["\u034f", "\ufe0f", "\ufe00", "\u3164", "\uffa0", "\u180b", "\u17b4", "\u0307", "\u2800"]
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
        pytest.param(
            "The grand total is Rs. 1,\u0ae7\u0aee,000.", id="ASCII and Gujarati digits in one token"
        ),
        pytest.param("The grand total is Rs. \u0967,\u0ae7\u0aee,000.", id="Devanagari and Gujarati digits"),
        pytest.param(
            "The grand total is Rs. \u0ae8,\u0ae7\u0aee,\u0ae6\u0ae6\u0ae6.", id="an invented Gujarati number"
        ),
        pytest.param("The grand total is Rs. 1,18,000².", id="a superscript"),
        pytest.param("CGST is ½ of the tax.", id="a fraction"),
        pytest.param("It is invoice ① of the year.", id="a circled digit"),
        pytest.param("It is invoice Ⅻ of the year.", id="a Roman numeral"),
        pytest.param("The grand total is ₹ ९,९९,९९९.", id="an invented Devanagari number"),
        pytest.param("The grand total is Rs. 1,1८,000.", id="ASCII and Devanagari digits in one token"),
        pytest.param("CGST is nine percent.", id="a number word before percent"),
        pytest.param("The total is two lakh rupees.", id="a number word before lakh"),
        *[
            pytest.param(f"The grand total is Rs. 1,18,000{ch}.", id=f"U+{ord(ch):04X} after a number")
            for ch in HIDING
        ],
        *[
            pytest.param(f"The tax is Rs. 2{space}9,000.", id=f"U+{ord(space):04X} inside an amount")
            for space in [" ", "\u00a0", "\u2009"]
        ],
        pytest.param("The quantity is 2,OOO.", id="letter O for zero"),
        pytest.param("The quantity is 2,\u041e\u041e\u041e.", id="Cyrillic O for zero"),
        pytest.param("The quantity is l,2.", id="letter l for one"),
        pytest.param("The grand total is Rs. 1180,00.", id="grouping 1180,00"),
        pytest.param("The grand total is Rs. 11,80,00.", id="grouping 11,80,00"),
        pytest.param("The grand total is Rs. 1.18 lakh crore.", id="two scale words"),
        pytest.param("It is dated the twelfth of August 2026.", id="an ordinal word before a month"),
        pytest.param("It is dated twelve August 2026.", id="a number word before a month"),
        pytest.param("It is dated August thirteenth, 2026.", id="an ordinal word after a month"),
        pytest.param("The grand total is Rs. 1.18 L.", id="L for lakh (PROVISIONAL)"),
        pytest.param("The total is a lakh rupees.", id="a lakh (PROVISIONAL)"),
        pytest.param("It is dated August second, 2026.", id="an ordinal word after a month name"),
    ],
)
def test_must_drop(text: str) -> None:
    assert not kept(text)


@pytest.mark.parametrize("digits", list(FOREIGN.values()), ids=list(FOREIGN))
def test_the_digits_of_any_one_script_read_by_value(digits: str) -> None:
    # Council 4 Oct 2026 (a change: these were dropped as unreadable): ascii_digits on both sides.
    assert kept(f"The grand total is Rs. {digits}.")
    assert verify([DraftSentence("The total is Rs. 1,18,000.", [1])], {1: f"Total {digits}"}) != []


@pytest.mark.parametrize(
    ("text", "cited"),
    [
        pytest.param("The total was Rs. 1042.", 4, id="an amount never matches an id"),
        pytest.param("The total was Rs. 57.", 4, id="an amount never matches a short id"),
        pytest.param("The opening balance is Rs. 1,500 crore.", 5, id="Cr is not crore"),
        pytest.param("It is dated 12 August 2026.", 6, id="no century guess"),
        pytest.param("It is dated 08/12/26.", 6, id="a two-digit-year date swapped"),
        pytest.param("It is dated 12/08/25.", 6, id="another two-digit year"),
        pytest.param("The amount in words is Rupees One Lakh Nineteen Thousand Only.", 7, id="other words"),
        pytest.param("The total is Rupees One Lakh.", 7, id="part of the words"),
        pytest.param("The bill is for Rs. 2026.", 8, id="a year as an amount"),
        pytest.param("The invoice is from 2025.", 8, id="another year"),
    ],
)
def test_must_drop_over_other_bills(text: str, cited: int) -> None:
    assert not kept(text, (cited,))


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
# (sentence, the piece it cites): ids as numbers and numbers as ids, credit, two-digit years,
# amounts in words copied as printed, and a bare year.
MORE_KEEP = [
    ("The invoice number is 1042.", 4),
    ("Invoice No. 1042", 4),
    ("The bill number is 57.", 4),
    ("HSN 8471", 4),
    ("The HSN code is 8471.", 4),
    ("The PIN is 400001.", 4),
    ("PIN: 400001", 4),
    ("The mobile number is 98200 12345.", 4),
    ("The account number is 1234567890.", 4),
    ("A/c No. 1234567890", 4),
    ("The flat is A-1.", 4),
    ("The gat number is 45/2.", 4),
    ("The survey number is 7/12.", 4),
    ("Challan No. 2207", 4),
    ("The opening balance is Rs. 1,500.00.", 5),
    ("Opening Balance: 1,500.00 Cr", 5),
    ("It is dated 12/08/26.", 6),
    ("Invoice Date: 12/08/26", 6),
    ("It is dated 12-08-26.", 6),
    ("It is dated 12 August.", 6),
    ("The amount in words is Rupees One Lakh Eighteen Thousand Only.", 7),
    ("The invoice is from 2026.", 8),
    ("You may first check the total of Rs. 1,18,000.", 8),  # "may first" is not a date
]


@pytest.mark.parametrize("text", MUST_KEEP)
def test_must_keep(text: str) -> None:
    assert kept(text)


@pytest.mark.parametrize("text", HINDI_KEEP)
def test_an_english_answer_over_devanagari_evidence_is_kept(text: str) -> None:
    assert kept(text, (2,))


@pytest.mark.parametrize(("text", "cited"), MORE_KEEP)
def test_must_keep_over_other_bills(text: str, cited: int) -> None:
    assert kept(text, (cited,))


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
    pairs += [(text, (cited,)) for text, cited in MORE_KEEP if re.search("[0-9]", text)]
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


# --- any script's digits, and Devanagari words (council 2 Oct 2026, PR2) ---------

DEVANAGARI = str.maketrans("0123456789", "०१२३४५६७८९")


def _kept(sentence: str, evidence: str) -> bool:
    return verify([DraftSentence(sentence, [1])], {1: evidence}) != []


@pytest.mark.parametrize(
    ("sentence", "evidence", "kept"),
    [
        ("The area is ४५१ R.", "क्षेत्र ४५०", False),
        ("The area is ४५० R.", "Area 450", True),
        ("The area is 450 R.", "क्षेत्र ४५०", True),
        (
            "The area is \u0aea\u0aeb\u0ae7 R.",
            "\u0915\u094d\u0937\u0947\u0924\u094d\u0930 \u096a\u096b\u0966",
            False,
        ),  # 451 is not 450
        ("The total is " + "1,20,000.00.".translate(DEVANAGARI), "Total: 1,20,000", True),
        ("Gat ४५/२.", "Gat No. 45/2", True),
        ("Gat ४५/३.", "Gat No. 45/2", False),
        ("It is dated 12 August 2026.", "दिनांक १२/०८/२०२६", True),
        ("It is dated 13 August 2026.", "दिनांक १२/०८/२०२६", False),
        ("The total is 1.2 lakh.", "Total: " + "1,20,000".translate(DEVANAGARI), True),  # lakh is read
        ("The total is Rs. 1.2 L.", "Total: " + "1,20,000".translate(DEVANAGARI), False),  # L is not
    ],
)
def test_numbers_of_any_script_compare_by_value(sentence: str, evidence: str, kept: bool) -> None:
    assert _kept(sentence, evidence) is kept


def test_numbers_in_reads_devanagari_digits() -> None:
    assert numbers_in("एकूण ₹ " + "1,20,000".translate(DEVANAGARI)) == {Decimal(120000)}


OWNER = "A: जमीन मालकाचे नाव | B: रामचंद्र गणपत पाटील"


@pytest.mark.parametrize(
    ("sentence", "kept"),
    [
        ("The owner is रामचंद्र गणपत पाटील.", True),
        ("The owner is रामचंद्र पाटील.", True),  # each word is checked on its own
        ("The owner is रामचंद्र पाटिल.", False),  # respelled
        ("The owner is सीता पाटील.", False),  # invented
        ("The owner is पाट.", False),  # a part of a word is not that word
    ],
)
def test_every_devanagari_word_must_be_a_word_of_the_evidence(sentence: str, kept: bool) -> None:
    assert _kept(sentence, OWNER) is kept


def test_a_sentence_checked_against_nothing_over_devanagari_evidence_is_unchecked() -> None:
    texts = {1: OWNER, 2: "Owner: Ram Patil", 3: "गट क्रमांक ४७ | भोगवटादार रामचंद्र गणपत पाटील"}
    assert unchecked_cross_script(DraftSentence("The owner is Ramchandra Patil.", [1]), texts)
    assert not unchecked_cross_script(DraftSentence("The owner is रामचंद्र गणपत पाटील.", [1]), texts)
    assert not unchecked_cross_script(DraftSentence("A: जमीन मालकाचे नाव", [1]), texts)  # a quote
    # A number is no quote: the 47 is checked, the name never is.
    assert unchecked_cross_script(DraftSentence("Gat ४७.", [3]), texts)
    invented = DraftSentence("The owner of gat 47 is Sita Deshmukh.", [3])
    assert verify([invented], texts) == [invented] and unchecked_cross_script(invented, texts)
    assert unchecked_cross_script(DraftSentence("It है.", [1]), texts)  # a function word is no quote
    english = DraftSentence("The owner is Ram Patil.", [2])
    assert not unchecked_cross_script(english, texts)  # English evidence


@pytest.mark.parametrize(
    ("sentence", "kept"),
    [
        ("कुल ₹ ४५० है।", True),  # है is a function word
        ("कुल ₹ ४५० आणि रक्कम.", False),  # आणि may be added, रक्कम not
        ("कुल ₹ ४५० नहीं है।", False),  # a negation is never a function word
        ("एकूण ४५० नाही.", False),
        ("कुल ₹ ४५० या ५००.", False),
    ],
)
def test_only_closed_function_words_may_be_added(sentence: str, kept: bool) -> None:
    assert _kept(sentence, "कुल ₹ ४५०\nएकूण ४५०") is kept


def test_function_words_are_whole_nfc_words_and_no_negation() -> None:
    assert all(unicodedata.is_normalized("NFC", word) for word in FUNCTION_WORDS)
    assert not {"नहीं", "नाही", "न", "नका", "नये", "नव्हे", "या", "किंवा"} & FUNCTION_WORDS


# --- any script but Latin (council 4 Oct 2026: the other-script hole) --------------------------

GUJARATI_OWNER = (
    "\u0aae\u0abe\u0ab2\u0abf\u0a95: \u0ab0\u0aae\u0ac7\u0ab6 \u0aaa\u0a9f\u0ac7\u0ab2"  # owner: Ramesh Patel
)
SURESH = "\u0ab8\u0ac1\u0ab0\u0ac7\u0ab6"


def test_a_made_up_gujarati_quote_is_a_missing_word(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="kaagaz.answering.verify")
    invented = DraftSentence(f"The owner is {SURESH} \u0aaa\u0a9f\u0ac7\u0ab2.", [1])
    assert verify([invented], {1: GUJARATI_OWNER}) == []
    assert [record.getMessage() for record in caplog.records] == ["answer sentences dropped: missing_word=1"]
    quoted = DraftSentence("The owner is \u0ab0\u0aae\u0ac7\u0ab6 \u0aaa\u0a9f\u0ac7\u0ab2.", [1])
    assert verify([quoted], {1: GUJARATI_OWNER}) == [quoted]


def test_an_english_sentence_over_gujarati_or_tamil_evidence_is_unchecked() -> None:
    # owner Raman
    tamil = "\u0b89\u0bb0\u0bbf\u0bae\u0bc8\u0baf\u0bbe\u0bb3\u0bb0\u0bcd \u0bb0\u0bbe\u0bae\u0ba9\u0bcd"
    texts = {1: GUJARATI_OWNER, 2: tamil, 3: "Owner: Ramesh Patel"}
    for n in (1, 2):
        sentence = DraftSentence("The owner is Ramesh Patel.", [n])
        assert verify([sentence], texts) == [sentence] and unchecked_cross_script(sentence, texts)
    assert not unchecked_cross_script(DraftSentence("The owner is Ramesh Patel.", [3]), texts)
    assert not unchecked_cross_script(
        DraftSentence("\u0ab0\u0aae\u0ac7\u0ab6 \u0aaa\u0a9f\u0ac7\u0ab2.", [1]), texts
    )


def test_gujarati_letters_may_sit_next_to_gujarati_digits_but_cyrillic_never() -> None:
    evidence = "\u0a97\u0aa4 \u0aa8\u0a82. \u0aea\u0aed\u0aae\u0acb"  # gat no. 47th, in Gujarati
    assert verify([DraftSentence("It is gat 47.", [1])], {1: evidence}) != []
    assert verify([DraftSentence("It is gat 48.", [1])], {1: evidence}) == []
    assert verify([DraftSentence("It is 47\u0434.", [1])], {1: "47"}) == []  # a Cyrillic letter: odd


# --- a month written in another script, read by sound (council 4 Oct 2026, PR C) ---------
# Test data only (the code has no list of month words): Hindi, then Marathi, January to December.
HINDI_MONTHS = [
    "\u091c\u0928\u0935\u0930\u0940",
    "\u092b\u093c\u0930\u0935\u0930\u0940",
    "\u092e\u093e\u0930\u094d\u091a",
    "\u0905\u092a\u094d\u0930\u0948\u0932",
    "\u092e\u0908",
    "\u091c\u0942\u0928",
    "\u091c\u0941\u0932\u093e\u0908",
    "\u0905\u0917\u0938\u094d\u0924",
    "\u0938\u093f\u0924\u0902\u092c\u0930",
    "\u0905\u0915\u094d\u091f\u0942\u092c\u0930",
    "\u0928\u0935\u0902\u092c\u0930",
    "\u0926\u093f\u0938\u0902\u092c\u0930",
]
MARATHI_MONTHS = [
    "\u091c\u093e\u0928\u0947\u0935\u093e\u0930\u0940",
    "\u092b\u0947\u092c\u094d\u0930\u0941\u0935\u093e\u0930\u0940",
    "\u092e\u093e\u0930\u094d\u091a",
    "\u090f\u092a\u094d\u0930\u093f\u0932",
    "\u092e\u0947",
    "\u091c\u0942\u0928",
    "\u091c\u0941\u0932\u0948",
    "\u0911\u0917\u0938\u094d\u091f",
    "\u0938\u092a\u094d\u091f\u0947\u0902\u092c\u0930",
    "\u0911\u0915\u094d\u091f\u094b\u092c\u0930",
    "\u0928\u094b\u0935\u094d\u0939\u0947\u0902\u092c\u0930",
    "\u0921\u093f\u0938\u0947\u0902\u092c\u0930",
]
MARATHI_AUGUST = MARATHI_MONTHS[7]
GUNTHE = "\u0917\u0941\u0902\u0920\u0947"  # a land unit, near "june" by sound


def test_month_words_of_the_table_are_read_right_or_not_at_all() -> None:
    """The measurement: 23 of 24 read as their month, none as a wrong one."""
    read = [other_script_month(word) for word in HINDI_MONTHS + MARATHI_MONTHS]
    expected = list(range(1, 13)) * 2
    assert all(month in (None, right) for month, right in zip(read, expected, strict=True))
    assert sum(month is not None for month in read) == 23
    assert read[1] is None  # Hindi February is too far from "february": dropped as before


@pytest.mark.parametrize(
    "word",
    [
        pytest.param("\u0a93\u0a97\u0ab8\u0acd\u0a9f", id="gujarati"),
        pytest.param("\u0986\u0997\u09b8\u09cd\u099f", id="bengali"),
    ],
)
def test_other_indian_scripts_are_read_through_transliteration(word: str) -> None:
    assert other_script_month(word) == 8


@pytest.mark.parametrize(
    "word",
    [
        pytest.param("\u0627\u06af\u0633\u062a", id="arabic script: not transliterated, as before"),
        pytest.param("\u0926\u093f\u0928", id="day"),
        pytest.param("\u0930\u0941\u092a\u092f\u0947", id="rupees"),
        pytest.param("\u0915\u094d\u0937\u0947\u0924\u094d\u0930", id="area"),
        pytest.param("\u0911\u0917\u0938\u094d\u091fx", id="two scripts in one word"),
    ],
)
def test_other_words_are_no_month(word: str) -> None:
    assert other_script_month(word) is None


@pytest.mark.parametrize("separator", [" ", "-", "/"])
def test_an_english_date_is_backed_by_the_same_date_with_a_devanagari_month(separator: str) -> None:
    # "dated 12 August 2026" in Marathi, the digits Devanagari too
    dated, day, year = "\u0926\u093f\u0928\u093e\u0902\u0915", "\u0967\u0968", "\u0968\u0966\u0968\u096c"
    evidence = {1: f"{dated} {day}{separator}{MARATHI_AUGUST}{separator}{year}"}
    for kept in (
        "Dated 12 August 2026.",
        "Dated 12 Aug 2026.",
        "Signed in August 2026.",
        "Signed on 12 August.",
    ):
        assert verify([DraftSentence(kept, [1])], evidence) != [], kept


def test_the_day_and_year_must_match_exactly(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    evidence = {1: f"12 {MARATHI_AUGUST} 2026"}
    for wrong in ("Dated 13 August 2026.", "Dated 12 August 2025.", "Dated 12 September 2026."):
        assert verify([DraftSentence(wrong, [1])], evidence) == [], wrong
    assert [record.getMessage() for record in caplog.records] == [
        "answer sentences dropped: missing_date=1"
    ] * 3


def test_only_a_day_word_and_four_digit_year_make_a_date() -> None:
    assert verify([DraftSentence("Signed in August 2026.", [1])], {1: f"{MARATHI_AUGUST} 2026"}) == []
    assert verify([DraftSentence("Signed on 12 August.", [1])], {1: f"12 {MARATHI_AUGUST}"}) == []
    assert verify([DraftSentence("Dated 31 February 2026.", [1])], {1: f"31 {MARATHI_MONTHS[1]} 2026"}) == []


def test_reading_a_date_only_adds_its_day_and_year_stay_numbers() -> None:
    # "5 gunthe 2019": read as 5 June 2019 by chance, and 5 and 2019 still back a sentence.
    evidence = {1: f"5 {GUNTHE} 2019"}
    for kept in ("The area is 5 gunthe.", "It was bought in 2019.", f"The area is 5 {GUNTHE}."):
        assert verify([DraftSentence(kept, [1])], evidence) != [], kept
    quoted = DraftSentence(f"\u0926\u093f\u0928\u093e\u0902\u0915 12 {MARATHI_AUGUST} 2026", [1])
    assert verify([quoted], {1: quoted.text}) == [quoted]  # a quoted date reads as before
