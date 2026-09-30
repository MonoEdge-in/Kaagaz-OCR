"""Find the model's quotes in the first reader's text (PRD 2 D2, the quote rule).

The model never writes a stored value. It returns quotes, and this module finds
each one in the first reader's text. The stored text is always the reader's
text at the match, never the quote. A quote that cannot be found is dropped
and counted.

**The reader text** (``reader_text``). The first reader's spans in reading
order (a stable sort by page keeps each page's own order), joined by new
lines. Cells of one table row (a sheet row, or an OCR table row) are joined by
" | " instead.

**Normalising, with a map back.** Both sides are normalised the same way, one
cluster at a time (a character and the combining marks after it):

1. NFKC, then casefold for Latin letters only (D2: "case-insensitive for
   Latin"; Devanagari has no case, and other scripts are not promised);
2. Devanagari digits become ASCII digits (L4);
3. the dashes U+2010 to U+2015 and the minus sign become "-", and curly
   quotes become straight ones (ML amendment 9(b));
4. format characters (category Cf: the joiners, the soft hyphen) are dropped;
5. every run of whitespace becomes one space, with none at either end.

Each normalised character keeps the reader-text range of the cluster it came
from (``starts`` and ``ends``), so a match maps back to the exact reader slice.
The quote side also undoes the prompt's ``&lt; &gt; &amp;`` escapes.

**A match is kept only when:**

* it starts and ends on whole clusters (a match starting inside ﬁ or ½ is
  refused, test-engineer amendment 15);
* it starts and ends inside a span's own text, never inside our joins;
* at an edge where it has a letter, digit or mark, the reader's neighbouring
  character is none of those (so पाट is not found inside पाटील);
* a number is not part of a longer one: a match ending in a digit is not
  followed by "." or "," and a digit (12,000 is not in 12,000.50), and one
  starting with a digit is not preceded by a digit and "." or "," (12,000 is
  not in 1,12,000; PROVISIONAL, this second rule is ours);
* the reader's text at the match is at most ``MAX_QUOTE_CHARS`` characters,
  and so is the quote (security amendment A7).

**After a label** (``find_labelled``): for each label match in order, the
value must start within ``LABEL_WINDOW`` characters after the label's end, and
the first pair wins. With no label, the first match in the range wins.

**The loose second pass** (ML amendment 9(c)): only for a quote with a
Devanagari letter, and only when the exact pass finds nothing. On both sides
ळ becomes ल, ी becomes ि, ू becomes ु and ऱ becomes र, and the anusvara,
candrabindu and nukta are dropped. The rules above still hold, and the stored
text is still the reader's. The nukta follows ML 9(c) (the lead's decision).
The loose pass runs on any Devanagari text, a PDF's text layer too, so a value
a loose match locates is never verified (``labels.label_quoted``, council fix 6).

**Counts** (``QuoteCounts``, ML amendment 9(d) and (e)), per item:
``returned`` items searched for; ``dropped`` not found (or over a cap);
``loose`` found only by the loose pass; ``ambiguous`` found, but a later spot
fitted too and the first was taken (for a labelled item: a later label match
with its own value).

**Bounded work** (council fix 8). A document can repeat near-matches (every
"12,000" inside a "12,000.50"), so each item's search checks at most
``MAX_CHECKS`` candidate spots over both passes (``Checks``, optionally drawing on
a wider budget, such as a card's), and a labelled item tries at most
``MAX_LABEL_MATCHES`` label matches. Past either, the item is not found; a first
match whose look-ahead for a second spot ran out is ``ambiguous`` (a second spot
is unknown, so the first is not called unique).

Cost. ``reader_text`` is O(n) time and memory for n characters (two 4-byte
array entries per normalised character), plus the page sort: O(S) for spans
already in page order, O(S log S) otherwise. One search is O(n + m) in
practice (CPython's ``str.find``); a labelled search is O(k * (LABEL_WINDOW + m))
for k label matches; mapping a match back is O(log S) plus its spans.
"""

import unicodedata
from array import array
from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache

from kaagaz.chunking.script import fold_digits, has_devanagari_letters
from kaagaz.extraction.values import ExtractedValue, QuoteMatch
from kaagaz.reading.spans import BoxLocation, CellLocation, Location, Span

LABEL_WINDOW = 300  # D2: a value_quote within 300 characters after its label_quote
MAX_QUOTE_CHARS = 300  # security A7: longer quotes, and longer reader text at a match, are dropped
MAX_CHECKS = 512  # council fix 8: candidate spots one item's search checks, over both passes
MAX_LABEL_MATCHES = 64  # council fix 8: label matches one labelled item tries

_DEVANAGARI_FIRST, _DEVANAGARI_LAST = chr(0x0900), chr(0x097F)

_UNIFY = str.maketrans(
    {chr(c): "-" for c in range(0x2010, 0x2016)}  # hyphen, non-breaking hyphen, figure, en, em, bar
    | {"\N{MINUS SIGN}": "-"}
    | {chr(c): "'" for c in range(0x2018, 0x201C)}  # the four single curly quotes
    | {chr(c): '"' for c in range(0x201C, 0x2020)}  # the four double curly quotes
)

# ML 9(c), the loose pass: common OCR misreadings of Marathi and Hindi. "" drops the character.
_LOOSE = {
    "\N{DEVANAGARI LETTER LLA}": "\N{DEVANAGARI LETTER LA}",
    "\N{DEVANAGARI VOWEL SIGN II}": "\N{DEVANAGARI VOWEL SIGN I}",
    "\N{DEVANAGARI VOWEL SIGN UU}": "\N{DEVANAGARI VOWEL SIGN U}",
    "\N{DEVANAGARI LETTER RRA}": "\N{DEVANAGARI LETTER RA}",
    "\N{DEVANAGARI SIGN ANUSVARA}": "",
    "\N{DEVANAGARI SIGN CANDRABINDU}": "",
    "\N{DEVANAGARI SIGN NUKTA}": "",
}


@dataclass(frozen=True, slots=True)
class ReaderText:
    """The first reader's spans in reading order, normalised once, with a map back."""

    text: str  # the spans' text joined by new lines; cells of one row by " | "
    norm: str  # normalised (the module docstring)
    starts: array[int]  # norm index -> text index where its source cluster starts
    ends: array[int]  # norm index -> text index just past its source cluster
    span_starts: list[int]  # text offset of each span, ascending (bisect)
    spans: tuple[Span, ...]  # in the order of ``text``
    loose: str  # ML 9(c): ``norm`` with the loose folds; "" when it has no Devanagari letter
    loose_at: array[int]  # loose index -> norm index


@dataclass(frozen=True, slots=True)
class Found:
    start: int  # norm offsets, for order and dedupe
    end: int
    text: str  # the reader's text at the match; our joins become one space
    location: Location  # the union box on the first matched span's page, or the first cell
    confidence: float | None  # the lowest of the matched spans
    origin: str | None  # "ocr" when any matched span is OCR, else the first span's
    loose: bool = False  # found only by the loose pass
    ambiguous: bool = False  # a later spot fitted too; this, the first, was taken
    spans: int = 1  # the spans the match covers
    pages: int = 1  # the pages those spans are on
    whole: bool = False  # the match is exactly one span's whole text


class Checks:
    """How many more candidate spots (``_kept`` calls) a search may check (council fix
    8). ``shared`` is an optional wider budget, such as a whole card's, that every check
    also draws on. ``ran_out`` says a check was refused, so the search stopped early."""

    def __init__(self, limit: int = MAX_CHECKS, shared: "Checks | None" = None) -> None:
        self.left = limit
        self.ran_out = False
        self._shared = shared

    def take(self) -> bool:
        """Spend one check; False when none is left, here or in the shared budget."""
        if self.left <= 0 or (self._shared is not None and not self._shared.take()):
            self.ran_out = True
            return False
        self.left -= 1
        return True

    def stop(self) -> None:
        """No more checks for this search (a labelled item tried its last label)."""
        self.left = 0
        self.ran_out = True


@dataclass(slots=True)
class QuoteCounts:
    """One run's quote counts (ML 9(d) and (e)); add one item at a time."""

    returned: int = 0
    dropped: int = 0
    loose: int = 0
    ambiguous: int = 0

    def add(self, found: Found | None) -> Found | None:
        """Count one item by what locates it (None: dropped), and hand it back."""
        self.returned += 1
        if found is None:
            self.dropped += 1
        else:
            self.loose += found.loose
            self.ambiguous += found.ambiguous
        return found


def located_value(found: Found, *, record_key: str, field: str, label: Found | None = None) -> ExtractedValue:
    """The value a located quote gives: the reader's text at its spot, with its label's
    spot, how it was found and how its spans were read (their origin), not labelled yet
    (the card, and PR 6's invoice values)."""
    return ExtractedValue(
        record_key=record_key,
        field=field,
        value_text=found.text,
        value_raw=found.text,
        location=found.location,
        confidence=found.confidence,
        label_location=None if label is None else label.location,
        origin=found.origin,
        quote=QuoteMatch(found.spans, found.pages, found.whole, found.loose, found.ambiguous),
    )


def reader_text(spans: Sequence[Span]) -> ReaderText:
    """The reader text of ``spans``, normalised with its map back. O(n) (module docstring)."""
    ordered = tuple(sorted(spans, key=lambda span: span.location.page))
    builder = _Normaliser()
    parts: list[str] = []
    span_starts: list[int] = []
    offset = 0
    previous: tuple[object, ...] | None = None
    for i, span in enumerate(ordered):
        row = _row_key(span)
        if i:
            join = " | " if row is not None and row == previous else "\n"
            builder.feed(join, offset)
            parts.append(join)
            offset += len(join)
        span_starts.append(offset)
        builder.feed(span.text, offset)
        parts.append(span.text)
        offset += len(span.text)
        previous = row
    norm = "".join(builder.chars)
    loose, loose_at = _loose_view(norm)
    return ReaderText(
        "".join(parts), norm, builder.starts, builder.ends, span_starts, ordered, loose, loose_at
    )


def normalise_quote(quote: str) -> str:
    """A quote normalised by the same rules as the reader text, after undoing the prompt's escapes."""
    builder = _Normaliser()
    builder.feed(quote.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"), 0)
    return "".join(builder.chars)


def find_quote(
    reader: ReaderText, quote: str, *, start: int = 0, end: int | None = None, checks: Checks | None = None
) -> Found | None:
    """The first kept match of ``quote`` whose normalised text lies in [start, end), or None.
    ``checks``: this item's budget (a fresh ``Checks()`` when None)."""
    if len(quote) > MAX_QUOTE_CHARS:
        return None
    budget = Checks() if checks is None else checks
    needle = normalise_quote(quote)
    lo, hi = _bounds(reader, start, end)
    for loose in _passes(reader, needle):
        matches = _matches(reader, _loosen(needle) if loose else needle, loose, lo, hi, budget)
        first = next(matches, None)
        if first is not None:
            ambiguous = next(matches, None) is not None or budget.ran_out
            return replace(first, ambiguous=ambiguous)
    return None


def find_labelled(
    reader: ReaderText,
    label_quote: str | None,
    value_quote: str,
    *,
    start: int = 0,
    end: int | None = None,
    checks: Checks | None = None,
) -> tuple[Found | None, Found | None]:
    """(label, value). With a label: for each label match in order, the value starting within
    LABEL_WINDOW characters after the label's end; the first pair wins, and no pair gives
    (None, None), so the item is dropped. With no label (None or blank): (None, the first match)."""
    if label_quote is not None and len(label_quote) > MAX_QUOTE_CHARS:
        return None, None
    budget = Checks() if checks is None else checks
    label_needle = "" if label_quote is None else normalise_quote(label_quote)
    if not label_needle:
        return None, find_quote(reader, value_quote, start=start, end=end, checks=budget)
    if len(value_quote) > MAX_QUOTE_CHARS:
        return None, None
    value_needle = normalise_quote(value_quote)
    lo, hi = _bounds(reader, start, end)
    for loose in _passes(reader, label_needle + value_needle):
        if loose:
            pairs = _pairs(reader, _loosen(label_needle), _loosen(value_needle), True, lo, hi, budget)
        else:
            pairs = _pairs(reader, label_needle, value_needle, False, lo, hi, budget)
        first = next(pairs, None)
        if first is not None:
            ambiguous = next(pairs, None) is not None or budget.ran_out
            return replace(first[0], ambiguous=ambiguous), replace(first[1], ambiguous=ambiguous)
    return None, None


class _Normaliser:
    """Builds normalised text one segment at a time. For each normalised character it keeps
    the reader range of the cluster it came from. A cluster never crosses a segment, so it
    never crosses a span or one of our joins."""

    def __init__(self) -> None:
        self.chars: list[str] = []
        self.starts: array[int] = array("I")
        self.ends: array[int] = array("I")
        self._space: tuple[int, int] | None = None  # a pending space and the whitespace it stands for

    def feed(self, segment: str, offset: int) -> None:
        i, size = 0, len(segment)
        while i < size:
            j = i + 1
            while j < size and unicodedata.combining(segment[j]):
                j += 1
            self._cluster(segment[i:j], offset + i, offset + j)
            i = j

    def _cluster(self, cluster: str, start: int, end: int) -> None:
        for ch in _fold(cluster):
            if ch.isspace():
                self._space = (start if self._space is None else self._space[0], end)
            elif unicodedata.category(ch) != "Cf":
                if self._space is not None and self.chars:
                    self._add(" ", *self._space)
                self._space = None
                self._add(ch, start, end)

    def _add(self, ch: str, start: int, end: int) -> None:
        self.chars.append(ch)
        self.starts.append(start)
        self.ends.append(end)


@lru_cache(maxsize=4096)  # a document repeats a small set of clusters; each is folded once
def _fold(cluster: str) -> str:
    """Rules 1 to 3 of the module docstring on one cluster."""
    if len(cluster) == 1 and cluster < "\x80":
        return cluster.lower()  # ASCII: NFKC changes nothing, and lower() is its casefold
    folded = "".join(_latin_casefold(ch) for ch in unicodedata.normalize("NFKC", cluster))
    return fold_digits(folded).translate(_UNIFY)


def _latin_casefold(ch: str) -> str:
    if ch < "\x80" or _DEVANAGARI_FIRST <= ch <= _DEVANAGARI_LAST:
        return ch.lower()
    return ch.casefold() if unicodedata.name(ch, "").startswith("LATIN ") else ch


def _loose_view(norm: str) -> tuple[str, array[int]]:
    at: array[int] = array("I")
    if not has_devanagari_letters(norm):
        return "", at
    chars: list[str] = []
    for j, ch in enumerate(norm):
        folded = _LOOSE.get(ch, ch)
        if folded:
            chars.append(folded)
            at.append(j)
    return "".join(chars), at


def _loosen(needle: str) -> str:
    return "".join(_LOOSE.get(ch, ch) for ch in needle)


def _passes(reader: ReaderText, needle: str) -> tuple[bool, ...]:
    """Which passes to try: exact, then loose for a Devanagari quote (ML 9(c)). None for an empty quote."""
    if not needle:
        return ()
    if reader.loose and has_devanagari_letters(needle):
        return (False, True)
    return (False,)


def _bounds(reader: ReaderText, start: int, end: int | None) -> tuple[int, int]:
    lo = max(0, start)
    hi = len(reader.norm) if end is None else min(end, len(reader.norm))
    return lo, max(lo, hi)


def _pairs(
    reader: ReaderText, label: str, value: str, loose: bool, lo: int, hi: int, checks: Checks
) -> Iterator[tuple[Found, Found]]:
    """Each label match in order, with the first value that starts within LABEL_WINDOW after
    it; at most MAX_LABEL_MATCHES label matches."""
    if not label or not value:
        return
    for tried, found_label in enumerate(_matches(reader, label, loose, lo, hi, checks), start=1):
        last_start = found_label.end + LABEL_WINDOW
        found_value = next(_matches(reader, value, loose, found_label.end, hi, checks, last_start), None)
        if found_value is not None:
            yield found_label, found_value
        if tried >= MAX_LABEL_MATCHES:
            checks.stop()
            return


def _matches(
    reader: ReaderText,
    needle: str,
    loose: bool,
    lo: int,
    hi: int,
    checks: Checks,
    last_start: int | None = None,
) -> Iterator[Found]:
    """Every kept match of ``needle`` inside norm [lo, hi), in order, while ``checks`` last;
    with ``last_start``, only matches starting at or before that norm offset. ``needle`` is
    already normalised (and loosened for the loose pass, which searches ``reader.loose``)."""
    if not needle:
        return
    view = reader.loose if loose else reader.norm
    at = reader.loose_at if loose else None
    view_lo, view_hi = _to_view(at, lo), _to_view(at, hi)
    if last_start is not None:
        view_hi = min(view_hi, _to_view(at, last_start + 1) - 1 + len(needle))
    i = view.find(needle, view_lo, view_hi)
    while i >= 0:
        if not checks.take():
            return
        last = i + len(needle) - 1
        found = _kept(reader, at[i] if at else i, (at[last] if at else last) + 1, loose)
        if found is not None:
            yield found
        i = view.find(needle, i + 1, view_hi)


def _to_view(at: array[int] | None, norm_offset: int) -> int:
    """The first view index at or after a norm offset (the norm itself when ``at`` is None)."""
    return norm_offset if at is None else bisect_left(at, norm_offset)


def _kept(reader: ReaderText, j0: int, j1: int, loose: bool) -> Found | None:
    """The match norm[j0:j1] as a Found, or None when a rule in the module docstring refuses it."""
    norm, starts, ends = reader.norm, reader.starts, reader.ends
    size = len(norm)
    if (j0 > 0 and starts[j0 - 1] == starts[j0]) or (j1 < size and ends[j1] == ends[j1 - 1]):
        return None  # it starts or ends inside an expanded cluster
    if (j0 > 0 and _is_word(norm[j0]) and _is_word(norm[j0 - 1])) or (
        j1 < size and _is_word(norm[j1 - 1]) and _is_word(norm[j1])
    ):
        return None  # part of a longer word or number
    if _is_digit(norm[j1 - 1]) and j1 + 1 < size and norm[j1] in ".," and _is_digit(norm[j1 + 1]):
        return None  # 12,000 inside 12,000.50
    if _is_digit(norm[j0]) and j0 >= 2 and norm[j0 - 1] in ".," and _is_digit(norm[j0 - 2]):
        return None  # 12,000 inside 1,12,000
    begin, finish = starts[j0], ends[j1 - 1]
    first = bisect_right(reader.span_starts, begin) - 1
    last = bisect_right(reader.span_starts, finish - 1) - 1
    if begin >= reader.span_starts[first] + len(reader.spans[first].text):
        return None  # it starts inside one of our joins
    if finish > reader.span_starts[last] + len(reader.spans[last].text):
        return None  # it ends inside one of our joins
    pieces: list[str] = []
    matched: list[Span] = []
    for k in range(first, last + 1):
        span, span_start = reader.spans[k], reader.span_starts[k]
        piece = span.text[
            max(begin, span_start) - span_start : min(finish, span_start + len(span.text)) - span_start
        ]
        if piece:
            pieces.append(piece)
            matched.append(span)
    text = " ".join(pieces)
    if len(text) > MAX_QUOTE_CHARS:
        return None
    confidences = [span.confidence for span in matched if span.confidence is not None]
    origins = [_origin(span) for span in matched]
    return Found(
        start=j0,
        end=j1,
        text=text,
        location=_location(matched),
        confidence=min(confidences) if confidences else None,
        origin="ocr" if "ocr" in origins else origins[0],
        loose=loose,
        spans=len(matched),
        pages=len({span.location.page for span in matched}),
        whole=len(matched) == 1 and text == matched[0].text,
    )


def _location(matched: list[Span]) -> Location:
    """The first span's cell, or the union of the matched boxes on the first span's page."""
    head = matched[0].location
    if not isinstance(head, BoxLocation):
        return head
    boxes = [
        span.location
        for span in matched
        if isinstance(span.location, BoxLocation) and span.location.page == head.page
    ]
    return BoxLocation(
        head.page,
        min(box.x0 for box in boxes),
        min(box.y0 for box in boxes),
        max(box.x1 for box in boxes),
        max(box.y1 for box in boxes),
    )


def _row_key(span: Span) -> tuple[object, ...] | None:
    """Spans with the same key are cells of one table row, joined by " | "."""
    if isinstance(span.location, CellLocation):
        return ("sheet", span.location.sheet, span.location.row)
    if span.table_id is not None and span.row is not None:
        return ("table", span.location.page, span.table_id, span.row)
    return None


def _origin(span: Span) -> str | None:
    return span.origin


def _is_word(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "LMN"


def _is_digit(ch: str) -> bool:
    return "0" <= ch <= "9"
