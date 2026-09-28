"""No source, no answer: drop every drafted sentence its evidence does not back.

A sentence is kept only if:

1. It cites at least one piece of evidence, and every id it cites is one we
   gave (an unknown id is a made-up source).
2. Every number in it appears in the evidence it cites. Numbers are compared
   by value with commas removed, so "1,20,000", "120000" and "120000.00" are
   the same number, and "1,41,600" is not in evidence that says "1,20,000".
   A date such as 12/08/2026 also counts as its parts (12, 08, 2026), so a
   sentence may write "12 August 2026".

Nothing left means the answer is "not found". This runs on the search route
whether the sentences came from the model or were quoted; fixed-route
sentences come from templates filled only from query results.

Known gap: a number written in words ("one lakh") is not a digit and is not
checked. PROVISIONAL: the answer rules are Vrushit's (docs/architecture.md).

Time is O(s + e) in the characters of the sentences and their evidence.
"""

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal

from kaagaz.answering.draft import DraftSentence

_NUMBER = re.compile(r"[0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?")
_DATE = re.compile(r"[0-9]{1,4}[./-][0-9]{1,2}[./-][0-9]{1,4}")
_DIGITS = re.compile(r"[0-9]+")


def verify(sentences: Sequence[DraftSentence], evidence_text: Mapping[int, str]) -> list[DraftSentence]:
    """The sentences that pass both checks, in order. ``evidence_text`` maps each id to its text."""
    kept: list[DraftSentence] = []
    for sentence in sentences:
        cited = list(dict.fromkeys(sentence.evidence))
        if not cited or any(n not in evidence_text for n in cited):
            continue
        allowed: set[Decimal] = set()
        for n in cited:
            allowed |= numbers_in(evidence_text[n], dates_split=True)
        if numbers_in(sentence.text) <= allowed:
            kept.append(DraftSentence(text=sentence.text, evidence=cited))
    return kept


def numbers_in(text: str, *, dates_split: bool = False) -> set[Decimal]:
    """Every number in ``text`` by value. With ``dates_split``, each part of a date too."""
    numbers = {Decimal(match.replace(",", "")) for match in _NUMBER.findall(text)}
    if dates_split:
        for found in _DATE.findall(text):
            numbers.update(Decimal(part) for part in _DIGITS.findall(found))
    return numbers
