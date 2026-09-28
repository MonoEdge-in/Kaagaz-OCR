"""No source, no answer: the verify step (kaagaz/answering/verify.py)."""

from decimal import Decimal

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
    assert numbers_in("Rs. 1,20,000 and 12,34,567.50, INV-7") == {
        Decimal(120000),
        Decimal("1234567.50"),
        Decimal(7),
    }
