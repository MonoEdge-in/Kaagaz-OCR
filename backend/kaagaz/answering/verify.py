"""No source, no answer: drop every drafted sentence its evidence does not back.

A sentence is kept only if:

1. It cites at least one piece of evidence, and every id it cites is one we
   gave (an unknown id is a made-up source).
2. Every atom in it is backed by the evidence it cites. One scanner reads the
   atoms of both sides, each line on its own, in this order (a part one step
   takes is never read again by a later one):

   1. dates: 12/08/2026 (one separator throughout), 2026-08-12, 12 Aug 2026,
      12th of August 2026, August 12, 2026; "12 August" with no year (its day
      and month); "August 2026" (its month and year); 12/08/26 with a
      two-digit year, as written (no century is guessed);
   2. amounts: the rupee sign, Rs, Rs. or INR, then a number (an "/-", "Dr"
      or "Cr" after it changes nothing: Cr is credit, not crore);
   3. percents: %, percent, per cent;
   4. ids: any token after No, #, HSN, SAC, PIN, Ph, Mob or A/c, and any
      token with a letter, a digit and at least 4 characters (INV-1042, a
      GSTIN), compared whole. A number with an ordinal or a unit (12th,
      10pcs, 5kg) is not an id;
   5. numbers: every digit run left.

   What backs what:

   * Amounts and numbers compare by value with commas removed, so
     "1,20,000", "120000" and "1,20,000.00" are one number; a following k,
     hundred, thousand, lakh or crore multiplies it. Commas must group the
     plain, Indian or Western way (1180,00 is not a number).
   * A percent backs only a percent, and a date only its own date (and its
     day and month, and its month and year): the parts of 12/08/2026 are
     never the numbers 12, 8 or 2026. 12/08/26 backs 12/08/26, 12-08-26,
     12.08.26 and 12 August, never 12 August 2026.
   * An id in a sentence is backed by the same token in the evidence,
     however that token was read ("HSN 8471" over "HSN Code: 8471"). A token
     under 4 characters that is an id of the evidence (A-1) is that id.
   * A plain digit run (no comma, decimal, currency or scale) is also backed
     by an evidence id written the same ("The invoice number is 1042." over
     "Invoice No. 1042"). A plain year on its own (1900 to 2099) is also
     backed by the year of a date. An amount or a percent never matches an
     id, so "Rs. 1042" is not backed by INV-1042 or No. 1042.

Both sides lose their default-ignorable code points (``sanitize.strip_ignorable``)
and are normalised as the quote rule does (``quotes.normalise_quote``):
Devanagari digits read as ASCII ones, Latin letters case-folded. A sentence is
dropped outright when it holds what cannot be checked:

* a token with a digit and another script's digit, a numeric symbol
  (superscripts, fractions, circled digits, Roman numerals), ASCII and
  Devanagari digits together, or any other character that is not ASCII,
  Devanagari, the rupee sign, the minus sign or punctuation (a hidden mark);
* a letter next to a comma in a number (2,OOO, l,18,000), or a currency
  marker whose number has a space inside (Rs. 2 18,000);
* a date that is not a real one (12/26/2026), bad digit grouping, or two
  scale words in a row (1.18 lakh crore);
* a number written in words: before percent, lakh, crore, thousand, hundred
  or rupees, or an ordinal or number word next to a month, unless the whole
  run of number words is copied from a cited line as printed ("Rupees One
  Lakh Eighteen Thousand" from a Tally bill's amount in words).

In the evidence, such tokens are simply not atoms. Nothing left means the
answer is "not found". The drops are logged by reason, codes only. This runs
on the search route whether the sentences came from the model or were quoted;
fixed-route sentences come from templates filled only from query results.

Known gaps, PROVISIONAL (Part B 1, Vrushit decides): an atom may come from any
line of the evidence a sentence cites, so the sub total given as the grand
total, or the buyer's GSTIN given as the seller's, still passes; names and
other words are not checked. "1.18 L" and "a lakh" are dropped, not read. The
answer rules are Vrushit's (docs/architecture.md).

Time is O(s + e) in the characters of the sentences and their evidence; each
piece is read once per call.
"""

import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from kaagaz.answering.draft import DraftSentence
from kaagaz.answering.sanitize import strip_ignorable
from kaagaz.extraction.parsing import _INDIAN, _WESTERN, parse_date
from kaagaz.extraction.quotes import normalise_quote

_log = logging.getLogger(__name__)

Atom = tuple[str, Decimal | date | tuple[int, ...] | str]
Read = Callable[[re.Match[str]], Atom | None]

_NUMERIC = ("Nd", "No", "Nl")  # digits, other numbers (superscripts, fractions) and Roman numerals
_DEVANAGARI_FIRST, _DEVANAGARI_LAST = "ऀ", "ॿ"
_DEVANAGARI_ZERO, _DEVANAGARI_NINE = "\N{DEVANAGARI DIGIT ZERO}", "\N{DEVANAGARI DIGIT NINE}"
_ALLOWED_NEXT_TO_DIGITS = frozenset("\N{INDIAN RUPEE SIGN}\N{MINUS SIGN}")  # with ASCII, Devanagari, P*

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
_DATE_PARTS = re.compile(r"[./-]")

_NUMBER = r"(?P<n>\d+(?:,\d+)*(?:\.\d+)?)"
_SCALE_WORDS = r"k|hundred|thousand|lakhs?|lacs?|crores?"  # never "cr": that is credit
_SCALE = rf"(?:\s*(?P<scale>{_SCALE_WORDS})\b)?"
_SCALES = {"k": 3, "hundred": 2, "thousand": 3, "lakh": 5, "lac": 5, "crore": 7}  # powers of ten
_CURRENCY = "(?:\N{INDIAN RUPEE SIGN}|(?<![a-z])(?:rs|inr)\\.?)"
_AMOUNT = re.compile(_CURRENCY + r"\s*-?\s*" + _NUMBER + _SCALE + r"(?:\s*/-)?")
# Starts only where a number starts, so a long digit run is not tried from every digit.
_PERCENT = re.compile(r"(?<![\d,.])" + _NUMBER + r"\s*(?:%|percent\b|per\s*cent\b)")
_BARE = re.compile(_NUMBER + _SCALE)

_RUN = r"[a-z0-9]+(?:[-/.][a-z0-9]+)*"
_LABELLED_ID = re.compile(
    r"(?:(?:(?<![a-z])(?:no|hsn|sac|pin|ph|mob|a/c)\b\.?|#)\s*[:.-]?\s*){1,3}(?P<id>" + _RUN + ")"
)
_ID = re.compile(r"(?<![a-z0-9])" + _RUN)
_NOT_ID = re.compile(r"\d[\d.]*(?:st|nd|rd|th|pcs?|nos|kgs?|k)")  # a number with an ordinal or a unit
_TOKEN = re.compile(r"[a-z0-9]+(?:[-/.,][a-z0-9]+)*")  # a token as an id in a sentence may name it
_LETTER = re.compile("[a-z]")
_DIGIT = re.compile("[0-9]")

# What a sentence cannot hold (the module docstring).
_SPLIT_AMOUNT = re.compile(_CURRENCY + r"\s*-?\s*\d[\d,]*(?:\.\d+)?\s+\d")
_LETTER_IN_NUMBER = re.compile(r"\d,[a-z]|[a-z],\d")
_DOUBLE_SCALE = re.compile(rf"\d\s*(?:{_SCALE_WORDS})\s+(?:hundreds?|thousands?|lakhs?|lacs?|crores?)\b")

_CARDINAL = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen"
    "|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred"
)
_ORDINAL = (
    "first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth"
    "|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|thirtieth"
)
# "may" and "mar" only after an ordinal: "one may" and "may first" are not dates.
_MONTH_WORD = (
    "january|february|march|april|june|july|august|september|october|november|december"
    "|jan|feb|apr|jun|jul|aug|sept?|oct|nov|dec"
)
_NUMBER_WORD = re.compile(
    rf"\b(?:(?:a|{_CARDINAL}|{_ORDINAL})[\s-]+"
    r"(?:percent|per\s*cent|lakhs?|lacs?|crores?|thousands?|hundreds?|rupees?)"
    rf"|(?:{_CARDINAL})[\s-]+(?:of\s+)?(?:{_MONTH_WORD})"
    rf"|(?:{_ORDINAL})[\s-]+(?:of\s+)?(?:{_MONTH_WORD}|may|mar)"
    rf"|(?:{_MONTH_WORD})[\s-]+(?:the\s+)?(?:{_ORDINAL}))\b"
)
# A run of number words, as an amount in words prints it ("rupees one lakh eighteen thousand").
_RUN_WORD = rf"(?:{_CARDINAL}|hundreds?|thousands?|lakhs?|lacs?|crores?|rupees?|paise|percent|per\s+cent|and)"
_WORD_RUN = re.compile(rf"\b{_RUN_WORD}(?:[\s-]+{_RUN_WORD})*\b")


@dataclass(slots=True)
class _Backing:
    """What the cited evidence backs (the module docstring)."""

    atoms: set[Atom] = field(default_factory=set)  # numbers, percents and dates, by value
    ids: set[str] = field(default_factory=set)  # tokens read as ids
    tokens: set[str] = field(default_factory=set)  # every token with a digit, however read
    years: set[int] = field(default_factory=set)  # the years of its dates
    words: set[str] = field(default_factory=set)  # its runs of number words

    def add(self, other: "_Backing") -> None:
        self.atoms |= other.atoms
        self.ids |= other.ids
        self.tokens |= other.tokens
        self.years |= other.years
        self.words |= other.words


def verify(sentences: Sequence[DraftSentence], evidence_text: Mapping[int, str]) -> list[DraftSentence]:
    """The sentences that pass both checks, in order. ``evidence_text`` maps each id to its text."""
    kept: list[DraftSentence] = []
    dropped: Counter[str] = Counter()
    read: dict[int, _Backing] = {}
    for sentence in sentences:
        cited = list(dict.fromkeys(sentence.evidence))
        reason = _drop_reason(sentence.text, cited, evidence_text, read)
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
    return {value for kind, value in _backed(text).atoms if kind == "number" and isinstance(value, Decimal)}


def _drop_reason(
    text: str, cited: list[int], evidence_text: Mapping[int, str], read: dict[int, _Backing]
) -> str | None:
    """Why the sentence is dropped (a short code), or None to keep it."""
    if not cited:
        return "no_source"
    if any(n not in evidence_text for n in cited):
        return "unknown_source"
    lines = _sentence_lines(text)
    if lines is None:
        return "odd_digits"
    backing = _Backing()
    for n in cited:
        if n not in read:
            read[n] = _backed(evidence_text[n])
        backing.add(read[n])
    for line in lines:
        problem = _line_problem(line, backing.words)
        if problem is not None:
            return problem
        atoms = _atoms(line, known=backing.ids)
        problem = next((kind for kind, _ in atoms if kind.startswith("bad_")), None)
        if problem is not None:
            return problem
        missing = next((kind for kind, value in atoms if not _is_backed(kind, value, backing)), None)
        if missing is not None:
            return f"missing_{_KIND_CODES.get(missing, missing)}"
    return None


# The drop code of a missing atom, by kind.
_KIND_CODES = {
    "plain": "number",
    "year": "number",
    "short_date": "date",
    "day_month": "date",
    "month_year": "date",
}


def _is_backed(kind: str, value: object, backing: _Backing) -> bool:
    if kind in ("plain", "year") and isinstance(value, str):
        if ("number", Decimal(value)) in backing.atoms or value in backing.ids:
            return True
        return kind == "year" and int(value) in backing.years
    if kind == "id":
        return value in backing.tokens
    return (kind, value) in backing.atoms


def _backed(text: str) -> _Backing:
    """What one piece of evidence backs: its atoms, ids, tokens, years and number words."""
    backing = _Backing()
    for raw in text.splitlines():
        tokens = [token for token in strip_ignorable(raw).split() if not _unreadable(token)]
        line = _normalised(" ".join(tokens))
        backing.words.update(_run_text(match[0]) for match in _WORD_RUN.finditer(line))
        for kind, value in _atoms(line, tokens=backing.tokens):
            if kind == "id" and isinstance(value, str):
                backing.ids.add(value)
                backing.tokens.add(value)
            elif kind in ("plain", "year") and isinstance(value, str):
                backing.atoms.add(("number", Decimal(value)))
            elif kind == "date" and isinstance(value, date):
                backing.atoms |= {(kind, value), ("day_month", (value.month, value.day))}
                backing.atoms.add(("month_year", (value.year, value.month)))
                backing.years.add(value.year)
            elif kind == "short_date" and isinstance(value, tuple):
                backing.atoms |= {(kind, value), ("day_month", (value[1], value[0]))}
            elif kind == "month_year" and isinstance(value, tuple):
                backing.atoms.add((kind, value))
                backing.years.add(value[0])
            elif not kind.startswith("bad_"):
                backing.atoms.add((kind, value))
    return backing


def _sentence_lines(text: str) -> list[str] | None:
    """The sentence's lines, normalised; None when a token cannot be checked."""
    lines = []
    for raw in text.splitlines():
        tokens = raw.split()
        if any(_unreadable(token) for token in tokens):
            return None  # checked before the hidden marks go: they must not join two numbers
        lines.append(_normalised(strip_ignorable(" ".join(tokens))))
    return lines


def _normalised(line: str) -> str:
    # On ASCII text normalise_quote only lower-cases here (the tokens are already
    # joined by one space), so its per-character work is skipped.
    return line.lower() if line.isascii() else normalise_quote(line)


def _line_problem(line: str, words: Collection[str]) -> str | None:
    """Why a sentence line cannot be checked, before its atoms are read; None if it can."""
    if _SPLIT_AMOUNT.search(line) or _LETTER_IN_NUMBER.search(line):
        return "odd_digits"
    if _DOUBLE_SCALE.search(line):
        return "double_scale"
    runs = [(match.start(), match.end(), _run_text(match[0])) for match in _WORD_RUN.finditer(line)]
    for match in _NUMBER_WORD.finditer(line):
        run = next((text for start, end, text in runs if start <= match.start() and match.end() <= end), None)
        if run is None or run not in words:
            return "number_word"
    return None


def _run_text(run: str) -> str:
    """A run of number words, spaced one way, without an "and" at either end."""
    words = run.replace("-", " ").split()
    while words and words[0] == "and":
        words.pop(0)
    while words and words[-1] == "and":
        words.pop()
    return " ".join(words)


def _unreadable(token: str) -> bool:
    """A token with a digit that cannot be checked (the module docstring)."""
    if token.isascii():
        return False
    digit = devanagari_digit = ascii_digit = odd = False
    for ch in token:
        if "0" <= ch <= "9":
            digit = ascii_digit = True
        elif _DEVANAGARI_ZERO <= ch <= _DEVANAGARI_NINE:
            digit = devanagari_digit = True
        elif ch >= "\x80" and unicodedata.category(ch) in _NUMERIC:
            return True
        elif ch >= "\x80" and not (
            _DEVANAGARI_FIRST <= ch <= _DEVANAGARI_LAST
            or ch in _ALLOWED_NEXT_TO_DIGITS
            or unicodedata.category(ch).startswith("P")
        ):
            odd = True
    return (ascii_digit and devanagari_digit) or (digit and odd)


def _atoms(line: str, *, known: Collection[str] = (), tokens: set[str] | None = None) -> list[Atom]:
    """The atoms of one normalised line, in the module docstring's order. ``known``: the
    evidence's ids, so a short token that is one of them is that id. ``tokens``, for
    evidence: gets every token with a digit that an id in a sentence may name."""
    atoms: list[Atom] = []
    if not _DIGIT.search(line):
        return atoms  # every atom has a digit
    for pattern, read in _TYPED_STEPS:
        line = _take(pattern, line, atoms, read)
    if tokens is not None:
        tokens.update(token for token in _TOKEN.findall(line) if _DIGIT.search(token))
    line = _take(_LABELLED_ID, line, atoms, _labelled_id)
    line = _take(_ID, line, atoms, lambda match: _generic_id(match, known))
    _take(_BARE, line, atoms, _bare)
    return atoms


def _take(pattern: re.Pattern[str], line: str, atoms: list[Atom], read: Read) -> str:
    """Every match of ``pattern`` that ``read`` makes an atom, added and blanked out."""

    def take(match: re.Match[str]) -> str:
        atom = read(match)
        if atom is None:
            return match[0]
        atoms.append(atom)
        return " "

    return pattern.sub(take, line)


def _numeric_date(match: re.Match[str]) -> Atom:
    """12/08/2026 or 2026-08-12 as a date; 12/08/26 as written (day, month, two-digit year)."""
    day, month, year = _DATE_PARTS.split(match[0])
    if len(day) != 4 and len(year) == 2:
        written = (int(day), int(month), int(year))
        return ("short_date", written) if _real_day(written[0], written[1]) else ("bad_date", match[0])
    found = parse_date(match[0])
    return ("bad_date", match[0]) if found is None else ("date", found)


def _real_day(day: int, month: int) -> bool:
    try:
        date(2024, month, day)  # a leap year, so 29 February is a real day
    except ValueError:
        return False
    return True


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


def _number(kind: str) -> Callable[[re.Match[str]], Atom]:
    def read(match: re.Match[str]) -> Atom:
        written = match["n"]
        whole = written.partition(".")[0]
        if "," in whole and not (_INDIAN.fullmatch(whole) or _WESTERN.fullmatch(whole)):
            return ("bad_number", written)
        number = Decimal(written.replace(",", ""))
        scale = match.groupdict().get("scale")  # a percent has none
        if scale is not None:
            number = number.scaleb(_SCALES[scale.removesuffix("s")])
        return (kind, number)

    return read


_AS_NUMBER = _number("number")


def _bare(match: re.Match[str]) -> Atom:
    """Step 5: a plain digit run (a "year" when it is one on its own), else a number."""
    written = match["n"]
    if match["scale"] is not None or not written.isdigit():
        return _AS_NUMBER(match)
    line, start, end = match.string, match.start(), match.end()
    alone = line[start - 1 : start] not in ("-", "/") and line[end : end + 1] not in ("-", "/")
    if alone and len(written) == 4 and 1900 <= int(written) <= 2099:
        return ("year", written)
    return ("plain", written)


def _labelled_id(match: re.Match[str]) -> Atom | None:
    return ("id", match["id"]) if _DIGIT.search(match["id"]) else None


def _generic_id(match: re.Match[str], known: Collection[str]) -> Atom | None:
    token = match[0]
    if not _DIGIT.search(token):
        return None
    if len(token) < 4:
        return ("id", token) if token in known else None
    if _NOT_ID.fullmatch(token) or not _LETTER.search(token):
        return None
    return ("id", token)


def _day_month_year(match: re.Match[str]) -> str:
    return f"{match['d']} {match['mon']} {match['y']}"


def _day_month(match: re.Match[str]) -> str:
    return f"{match['d']} {match['mon']} 2024"  # a leap year, so 29 February is a real day


# Steps 1 to 3 (the module docstring); ids and numbers follow in _atoms.
_TYPED_STEPS: tuple[tuple[re.Pattern[str], Read], ...] = (
    (_NUMERIC_DATE, _numeric_date),
    (_DAY_MONTH_YEAR, _dated("date", _day_month_year)),
    (_MONTH_DAY_YEAR, _dated("date", _day_month_year)),
    (_MONTH_YEAR, _dated("month_year", lambda match: f"{match['mon']} {match['y']}")),
    (_DAY_MONTH, _dated("day_month", _day_month)),
    (_MONTH_DAY, _dated("day_month", _day_month)),
    (_AMOUNT, _AS_NUMBER),
    (_PERCENT, _number("percent")),
)
