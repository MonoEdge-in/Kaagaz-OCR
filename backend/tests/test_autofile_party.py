"""Party folders (backlog 28b, final polish 16 Oct 2026), the pure rules: the organisation's
own GSTIN from its invoice counts, the other party of an invoice, and the folder name.
Against PostgreSQL: test_autofile_party_db.py.
"""

import pytest

from kaagaz.db.autofile import own_gstin, party, party_name
from kaagaz.extraction.checks import gstin_check_char


def gstin(base: str) -> str:
    """A checksum-valid GSTIN from its first 14 characters."""
    return base + gstin_check_char(base)


OWN = gstin("27AAACO1234A1Z")
MEHTA = gstin("24AAAPM5678B1Z")
SHARMA = gstin("29AAFCS9012C1Z")
BAD = MEHTA[:-1] + ("A" if MEHTA[-1] != "A" else "B")  # the check character is wrong


def test_own_gstin_is_the_clear_leader_on_most_of_the_invoices() -> None:
    assert own_gstin([(OWN, 5, 6), (MEHTA, 2, 6)]) == OWN


@pytest.mark.parametrize(
    "top",
    [
        [],  # no verified invoice yet
        [(OWN, 2, 2)],  # under 3 invoices
        [(OWN, 4, 4), (MEHTA, 4, 4)],  # a tie: one supplier on every bill
        [(OWN, 3, 6), (MEHTA, 2, 6)],  # on only half of them
        [(BAD, 5, 5)],  # not a real GSTIN
    ],
)
def test_own_gstin_is_none_when_it_is_not_clear(top: list[tuple[str, int, int]]) -> None:
    assert own_gstin(top) is None


def test_the_other_party_is_the_side_that_is_not_the_organisation_whatever_its_role() -> None:
    bought = party("Mehta Textiles", MEHTA, "Our Shop", OWN, OWN)
    swapped = party("Our Shop", OWN, "Mehta Textiles", MEHTA, OWN)  # sold to, or read the wrong way round
    assert bought == swapped == (MEHTA, "Mehta Textiles")


@pytest.mark.parametrize(
    ("seller", "buyer", "own"),
    [
        (MEHTA, OWN, None),  # the organisation's own GSTIN is not clear
        (OWN, OWN, OWN),  # both sides the organisation
        (MEHTA, SHARMA, OWN),  # neither side the organisation: whose bill is it?
        (BAD, OWN, OWN),  # the other side fails the checksum
        (None, OWN, OWN),  # the other side has no GSTIN
    ],
)
def test_no_party_folder_without_one_clear_other_party(
    seller: str | None, buyer: str | None, own: str | None
) -> None:
    assert party("Mehta Textiles", seller, "Our Shop", buyer, own) is None


def test_the_folder_name_is_cleaned_cut_and_never_empty() -> None:
    hidden = "\u202eMehta\u200b  \tTextiles\x00\u2066 "  # bidi override, zero-width space, tab, NUL
    assert party_name(hidden, MEHTA) == "Mehta Textiles"
    assert party_name("A " + "b" * 100, MEHTA) == ("A " + "b" * 100)[:80]
    assert party_name("x" * 79 + " y", MEHTA) == "x" * 79  # no space left at the cut
    assert party_name(None, MEHTA) == party_name(" \u200f ", MEHTA) == f"GSTIN {MEHTA}"
