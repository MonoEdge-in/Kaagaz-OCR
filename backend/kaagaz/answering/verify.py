"""No source, no answer: drop every drafted sentence its evidence does not back.

A sentence is kept only if:

1. It cites at least one piece of evidence, and every id it cites is one we
   gave (an unknown id is a made-up source).
2. Every atom in it is an atom of the evidence it cites. One scanner reads
   the atoms of both sides, each line on its own, in this order (a part one
   step takes is never read again by a later one):

   1. dates: 12/08/2026 (one separator throughout), 2026-08-12, 12 Aug 2026,
      12th of August 2026, August 12, 2026; "12 August" with no year (its day
      and month); "August 2026" (its month and year);
   2. amounts: the rupee sign, Rs, Rs. or INR, then a number (an "/-" or
      "Dr" after it changes nothing);
   3. percents: %, percent, per cent;
   4. ids: any token after No, #, HSN, SAC, PIN, Ph, Mob or A/c, and any
      token with a letter, a digit and at least 4 characters (INV-1042, a
      GSTIN), compared whole. A number with an ordinal or a unit (12th,
      10pcs, 5kg) is not an id;
   5. numbers: every digit run left.

   Amounts and numbers compare by value with commas removed, so "1,20,000",
   "120000" and "1,20,000.00" are one number; a following k, hundred,
   thousand, lakh, crore or cr multiplies it. A percent backs only a percent,
   an id only the same id, and a date only its own date: the parts of
   12/08/2026 are never the numbers 12, 8 or 2026, and an id's digits are
   never a number. 12/08/2026 also backs "12 August" and "August 2026".

Both sides are normalised as the quote rule does (``quotes.normalise_quote``):
format characters dropped (U+200B to U+200D, U+2060, U+FEFF, U+00AD: all of
category Cf), Devanagari digits read as ASCII ones, Latin letters case-folded.
A sentence is dropped outright when it holds what cannot be checked: another
script's digit or a numeric symbol (superscripts, fractions, circled digits,
Roman numerals), a token mixing ASCII and Devanagari digits, a date that is
not a real one (12/26/2026, or 12/08/26 whose century would be a guess), or a
number word before percent, lakh, crore, thousand, hundred or rupees. In the
evidence, such tokens are simply not atoms.

Nothing left means the answer is "not found". The drops are logged by reason,
codes only. This runs on the search route whether the sentences came from the
model or were quoted; fixed-route sentences come from templates filled only
from query results.

Known gaps, PROVISIONAL (Part B 1, Vrushit decides): an atom may come from any
line of the evidence a sentence cites, so the sub total given as the grand
total, or the buyer's GSTIN given as the seller's, still passes; names and
other words are not checked. The answer rules are Vrushit's
(docs/architecture.md).

Time is O(s + e) in the characters of the sentences and their evidence; each
piece's atoms are read once per call.
"""

import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal

from kaagaz.answering.draft import DraftSentence
from kaagaz.extraction.parsing import parse_date
from kaagaz.extraction.quotes import normalise_quote

_log = logging.getLogger(__name__)

Atom = tuple[str, Decimal | date | tuple[int, int] | str]
Read = Callable[[re.Match[str]], Atom | None]

_NUMERIC = ("Nd", "No", "Nl")  # digits, other numbers (superscripts, fractions) and Roman numerals
_DEVANAGARI_ZERO, _DEVANAGARI_NINE = "\N{DEVANAGARI DIGIT ZERO}", "\N{DEVANAGARI DIGIT NINE}"

_MONTH = (
    r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?"
)
_DAY = r"(?P<d>\d{1,2})(?:st|nd|rd|th)?"
_START = r"(?<![\w/-])"  # not inside a token
_END = r"(?![\w/-]|[.,]\d)"
_NUMERIC_DATE = re.compile(_START + r"(?:\d{1,2}([./-])\d{1,2}\1\d{2,4}|\d{4}-\d{1,2}-\d{1,2})" + _END)
_DAY_MONTH_YEAR = re.compile(
    _START + _DAY + r"(?:\s+of)?[\s./-]+" + _MONTH + r"[\s.,/-]*(?P<y>\d{4}|\d{2})" + _END
)
_MONTH_DAY_YEAR = re.compile(_START + _MONTH + r"\s*" + _DAY + r"\s*,?\s*(?P<y>\d{4})" + _END)
_MONTH_YEAR = re.compile(_START + _MONTH + r"[\s.,/-]*(?P<y>\d{4})" + _END)
_DAY_MONTH = re.compile(_START + _DAY + r"(?:\s+of)?[\s./-]+" + _MONTH)
_MONTH_DAY = re.compile(_START + _MONTH + r"\s*" + _DAY + r"\b")

_NUMBER = r"(?P<n>\d+(?:,\d+)*(?:\.\d+)?)"
_SCALE = r"(?:\s*(?P<scale>k|hundred|thousand|lakhs?|lacs?|crores?|cr)\b)?"
_SCALES = {"k": 3, "hundred": 2, "thousand": 3, "lakh": 5, "lac": 5, "crore": 7, "cr": 7}  # powers of ten
_AMOUNT = re.compile(
    "(?:\N{INDIAN RUPEE SIGN}|(?<![a-z])(?:rs|inr)\\.?)\\s*-?\\s*" + _NUMBER + _SCALE + r"(?:\s*/-)?"
)
# Starts only where a number starts, so a long digit run is not tried from every digit.
_PERCENT = re.compile(r"(?<![\d,.])" + _NUMBER + r"\s*(?:%|percent\b|per\s*cent\b)")
_BARE = re.compile(_NUMBER + _SCALE)

_RUN = r"[a-z0-9]+(?:[-/.][a-z0-9]+)*"
_LABELLED_ID = re.compile(
    r"(?:(?:(?<![a-z])(?:no|hsn|sac|pin|ph|mob|a/c)\b\.?|#)\s*[:.-]?\s*){1,3}(?P<id>" + _RUN + ")"
)
_ID = re.compile(r"(?<![a-z0-9])" + _RUN)
_NOT_ID = re.compile(r"\d[\d.]*(?:st|nd|rd|th|pcs?|nos|kgs?|k)")  # a number with an ordinal or a unit
_LETTER = re.compile("[a-z]")
_DIGIT = re.compile("[0-9]")

_NUMBER_WORD = re.compile(
    r"\b(?:a|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen"
    r"|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty"
    r"|ninety|hundred)[\s-]+(?:percent|per\s*cent|lakhs?|lacs?|crores?|thousands?|hundreds?|rupees?)\b"
)


def verify(sentences: Sequence[DraftSentence], evidence_text: Mapping[int, str]) -> list[DraftSentence]:
    """The sentences that pass both checks, in order. ``evidence_text`` maps each id to its text."""
    kept: list[DraftSentence] = []
    dropped: Counter[str] = Counter()
    backing: dict[int, set[Atom]] = {}
    for sentence in sentences:
        cited = list(dict.fromkeys(sentence.evidence))
        reason = _drop_reason(sentence.text, cited, evidence_text, backing)
        if reason is None:
            kept.append(DraftSentence(text=sentence.text, evidence=cited))
        else:
            dropped[reason] += 1
    if dropped:
        counts = " ".join(f"{code}={count}" for code, count in sorted(dropped.items()))
        _log.info("answer sentences dropped: %s", counts)
    return kept


def numbers_in(text: str) -> set[Decimal]:
    """Every amount and number in ``text`` by value (not its dates, percents or ids)."""
    atoms, _ = _scan(text, evidence=True)
    return {value for kind, value in atoms if kind == "number" and isinstance(value, Decimal)}


def _drop_reason(
    text: str, cited: list[int], evidence_text: Mapping[int, str], backing: dict[int, set[Atom]]
) -> str | None:
    """Why the sentence is dropped (a short code), or None to keep it."""
    if not cited:
        return "no_source"
    if any(n not in evidence_text for n in cited):
        return "unknown_source"
    atoms, problem = _scan(text, evidence=False)
    if problem is not None:
        return problem
    allowed: set[Atom] = set()
    for n in cited:
        if n not in backing:
            backing[n] = _backed(evidence_text[n])
        allowed |= backing[n]
    missing = next((kind for kind, value in atoms if (kind, value) not in allowed), None)
    return None if missing is None else f"missing_{missing}"


def _backed(text: str) -> set[Atom]:
    """The atoms a piece of evidence backs: its own, and the day and month of each date."""
    atoms = set(_scan(text, evidence=True)[0])
    for kind, value in list(atoms):
        if kind == "date" and isinstance(value, date):
            atoms.add(("day_month", (value.month, value.day)))
            atoms.add(("month_year", (value.year, value.month)))
    return atoms


def _scan(text: str, *, evidence: bool) -> tuple[list[Atom], str | None]:
    """The atoms of ``text``, line by line (the module docstring), and for a sentence the
    first reason it cannot be checked. In evidence, what cannot be checked is skipped."""
    atoms: list[Atom] = []
    for raw in text.splitlines():
        tokens = raw.split()
        if any(_unreadable(token) for token in tokens):
            if not evidence:
                return [], "odd_digits"
            tokens = [token for token in tokens if not _unreadable(token)]
        line = " ".join(tokens)
        # On ASCII text normalise_quote only lower-cases here (the tokens are already
        # joined by one space), so its per-character work is skipped.
        line = line.lower() if line.isascii() else normalise_quote(line)
        if not evidence and _NUMBER_WORD.search(line):
            return [], "number_word"
        if not _DIGIT.search(line):
            continue  # every atom has a digit
        for pattern, read in _STEPS:
            line = _take(pattern, line, atoms, read)
    if any(kind == "bad_date" for kind, _ in atoms):
        if not evidence:
            return [], "bad_date"
        atoms = [atom for atom in atoms if atom[0] != "bad_date"]
    return atoms, None


def _unreadable(token: str) -> bool:
    """Another script's digit or a numeric symbol, or ASCII and Devanagari digits together."""
    if token.isascii():
        return False
    ascii_digit = devanagari_digit = False
    for ch in token:
        if "0" <= ch <= "9":
            ascii_digit = True
        elif _DEVANAGARI_ZERO <= ch <= _DEVANAGARI_NINE:
            devanagari_digit = True
        elif ch >= "\x80" and unicodedata.category(ch) in _NUMERIC:
            return True
    return ascii_digit and devanagari_digit


def _take(pattern: re.Pattern[str], line: str, atoms: list[Atom], read: Read) -> str:
    """Every match of ``pattern`` that ``read`` makes an atom, added and blanked out."""

    def take(match: re.Match[str]) -> str:
        atom = read(match)
        if atom is None:
            return match[0]
        atoms.append(atom)
        return " "

    return pattern.sub(take, line)


def _dated(kind: str, written: Callable[[re.Match[str]], str]) -> Read:
    """Read a date match through ``parse_date``; one that is not a real date is a "bad_date"."""

    def read(match: re.Match[str]) -> Atom:
        day = parse_date(written(match))
        if day is None:
            return ("bad_date", match[0])
        if kind == "month_year":
            return (kind, (day.year, day.month))
        if kind == "day_month":
            return (kind, (day.month, day.day))
        return (kind, day)

    return read


def _number(kind: str) -> Read:
    def read(match: re.Match[str]) -> Atom:
        number = Decimal(match["n"].replace(",", ""))
        scale = match.groupdict().get("scale")  # a percent has none
        if scale is not None:
            number = number.scaleb(_SCALES[scale.removesuffix("s")])
        return (kind, number)

    return read


def _labelled_id(match: re.Match[str]) -> Atom | None:
    return ("id", match["id"]) if _DIGIT.search(match["id"]) else None


def _generic_id(match: re.Match[str]) -> Atom | None:
    token = match[0]
    if len(token) < 4 or _NOT_ID.fullmatch(token) or not (_LETTER.search(token) and _DIGIT.search(token)):
        return None
    return ("id", token)


def _day_month_year(match: re.Match[str]) -> str:
    return f"{match['d']} {match['mon']} {match['y']}"


def _day_month(match: re.Match[str]) -> str:
    return f"{match['d']} {match['mon']} 2024"  # a leap year, so 29 February is a real day


# The scanner's steps, in order (the module docstring).
_STEPS: tuple[tuple[re.Pattern[str], Read], ...] = (
    (_NUMERIC_DATE, _dated("date", lambda match: match[0])),
    (_DAY_MONTH_YEAR, _dated("date", _day_month_year)),
    (_MONTH_DAY_YEAR, _dated("date", _day_month_year)),
    (_MONTH_YEAR, _dated("month_year", lambda match: f"{match['mon']} {match['y']}")),
    (_DAY_MONTH, _dated("day_month", _day_month)),
    (_MONTH_DAY, _dated("day_month", _day_month)),
    (_AMOUNT, _number("number")),
    (_PERCENT, _number("percent")),
    (_LABELLED_ID, _labelled_id),
    (_ID, _generic_id),
    (_BARE, _number("number")),
)
