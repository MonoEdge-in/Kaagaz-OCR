"""The numbers rule for a total (PRD 6 C; issue #244 decision 10, approved 24 Oct 2026).

``tell`` writes a total question's answer in code, from every record it matches
(``FixedQueries.figures``; never a card value). A total, the exact sum (Decimal), only when
every record prints the figure as one number, every figure is verified, can be pointed at and
is not hard to read (PRD 5 F6), and with a date range every record has one readable, verified
date; its working follows, each figure as printed with its citation. Otherwise no total: one
sentence gives the first reason (no readable date, no figure, a figure not verified, a date
not verified) in counts of the records listed after it, each with its figure as printed or
none. Never a subtraction, an average, an estimate or a share. At most MAX_CITED_VALUES
records are listed, figures first, and the first sentence says how many more; one with
nothing to point at is counted, never listed. Time O(r log r) in the r records.

PRD 6 C2: a sheet's printed total row is never a record. When the records include a sheet that
prints its own total, no total is added up: the head says so (PRINTED_TOTAL), and the printed
total comes first, as printed and cited. A row that reads like a total but does not add up is a
record that withholds the total ("reads like a total"), before every other reason. ``ranking``:
the same rule for a highest or lowest by party, before any party's sum is shown: every record
must also name one party, verified, and a printed total is left out. With a date range asked, a
printed total is not listed (it covers the whole sheet, not the range): the sheet's rows in range
are added up as any records.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from kaagaz.answering.evidence import valid_regions
from kaagaz.answering.fixed_queries import MAX_CITED_VALUES, Figure, ValueRef
from kaagaz.answering.format import indian_number
from kaagaz.answering.sanitize import clean_text

_MAX_TEXT = 100  # a document name or a figure longer than this is cut in its line
PRINTED_TOTAL = "This sheet prints its own total, so I haven't added up its rows."
PRINTED_TOTALS = "These sheets print their own totals, so I haven't added up their rows."


@dataclass(frozen=True, slots=True)
class Told:
    head: str  # the total, or why there is none
    # Each record listed: its sentence, the value it cites, and that value's regions.
    lines: list[tuple[str, ValueRef, list[dict[str, Any]]]]
    total: Decimal | None  # None: withheld


def tell(
    figures: Sequence[Figure],
    metric: str,
    period: str,
    noun: str,
    hard: Collection[str] = (),
    *,
    ranking: bool = False,
) -> Told:
    """``metric``: the figure's name ("profit"); ``period``: "in September 2026", or "" with no
    range; ``noun``: what a record is ("invoice", "record"); ``hard``: the value ids whose cited
    text holds a word hard to read, never verified; ``ranking``: the records behind a highest or
    lowest by party, whose ``total`` (None: withheld) is never shown."""
    rows, printed = [], []
    for figure in figures:
        regions = [] if figure.value is None else valid_regions([figure.value.region])
        if not figure.printed_total:
            rows.append((figure, regions, _checked(figure, regions, hard)))
        elif not ranking:
            printed.append((figure, regions, _checked(figure, regions, hard)))
    n = len(rows)
    doubtful = sum(figure.looks_total for figure, _, _ in rows)
    undated = sum(not figure.dated for figure, _, _ in rows)
    lacking = sum(figure.amount is None for figure, _, _ in rows)
    nameless = sum(not figure.grouped for figure, _, _ in rows)
    unverified = sum(figure.amount is not None and not checked for figure, _, checked in rows)
    names = sum(figure.grouped and not figure.party_verified for figure, _, _ in rows)
    dates = sum(figure.dated and not figure.date_verified for figure, _, _ in rows)
    if doubtful:
        why = f"{_of(doubtful, n, noun)} {_verb(doubtful, 'reads', 'read')} like a total"
    elif undated:
        why = f"{_of(undated, n, noun)} {_verb(undated, 'has', 'have')} no date I can read"
    elif lacking:
        why = f"{_of(lacking, n, noun)} {_verb(lacking, 'has', 'have')} no {metric} I can add"
    elif nameless:
        why = f"{_of(nameless, n, noun)} {_verb(nameless, 'has', 'have')} no party I can read"
    elif unverified:
        why = f"{_of(unverified, n, metric + ' figure')} {_verb(unverified, 'is', 'are')} not verified yet"
    elif names:  # a party's sum grouped by a name not verified could add up another party's
        why = f"{_of(names, n, 'party name')} {_verb(names, 'is', 'are')} not verified yet"
    elif dates:
        why = f"{_of(dates, n, noun + ' date')} {_verb(dates, 'is', 'are')} not verified yet"
    else:
        why = ""
    after = f" {period}" if period else ""
    total: Decimal | None = None
    if printed:  # the sheet's own figure is never set beside a sum of its rows
        sheets = {figure.value.document_id for figure, _, _ in printed if figure.value is not None}
        head = PRINTED_TOTAL if len(sheets) <= 1 else PRINTED_TOTALS
    elif why:
        done = f"compared the parties' {metric}" if ranking else f"added up the {metric}"
        head = f"{why[0].upper()}{why[1:]}, so I haven't {done}{after}."
    else:
        total = sum((figure.amount for figure, _, _ in rows if figure.amount is not None), Decimal(0))
        counted = f"1 {noun}" if n == 1 else f"{indian_number(n)} {noun}s"
        head = f"The total {metric}{after} was {_exact(total)}, from {counted}."
    ordered = [*printed, *sorted(rows, key=lambda row: row[0].amount is None)]
    listed = [row for row in ordered if row[1]][:MAX_CITED_VALUES]
    if 0 < len(listed) < len(ordered):
        more = len(ordered) - len(listed)
        head += (
            f" {indian_number(len(listed))} {_verb(len(listed), 'is', 'are')} listed here;"
            f" {indian_number(more)} more {_verb(more, 'is', 'are')} not."
        )
    lines = [(_line(f, f.value, metric, ok), f.value, regions) for f, regions, ok in listed if f.value]
    return Told(head, lines, total)


def _checked(figure: Figure, regions: list[dict[str, Any]], hard: Collection[str]) -> bool:
    """The figure may count: verified, with a spot to point at, and not hard to read."""
    value = figure.value
    return value is not None and bool(regions) and figure.verified and value.value_id not in hard


def _line(figure: Figure, value: ValueRef, metric: str, checked: bool) -> str:
    """'INV-2041.pdf: Rs. 18,400.', with what is not verified about it, or 'bill.pdf: no profit
    printed.'; a printed total: 'ledger.xlsx, its printed total: 3,500.'"""
    name = clean_text(value.document_name)[:_MAX_TEXT].strip()
    if figure.printed_total:
        name += ", its printed total"
    if figure.amount is not None:
        said = clean_text(value.value_text)[:_MAX_TEXT].strip()
    elif figure.printed == 0:
        said = f"no {metric} printed"
    else:
        said = f"no {metric} I can add"  # one that is not a number, or several
    notes = ["reads like a total"] if figure.looks_total else []
    if figure.amount is not None and not checked:
        notes.append("not verified yet")
    if not figure.grouped:
        notes.append("no party I can read")
    elif not figure.party_verified:
        notes.append("party not verified yet")
    record = not figure.printed_total  # a printed total is not a record: its date is never asked
    if record and not figure.dated:
        notes.append("no date I can read")
    elif record and not figure.date_verified:
        notes.append("date not verified yet")
    return f"{name}: {said}{''.join(', ' + note for note in notes)}."


def _of(part: int, whole: int, noun: str) -> str:
    """'3 of the 14 invoices', 'all 14 invoices', or 'the one invoice'."""
    if whole == 1:
        return f"the one {noun}"
    if part == whole:
        return f"all {indian_number(whole)} {noun}s"
    return f"{indian_number(part)} of the {indian_number(whole)} {noun}s"


def _verb(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _exact(total: Decimal) -> str:
    """``indian_number``, never rounded: figures printed with three decimals keep them in the sum."""
    written = indian_number(total)
    if Decimal(written.replace(",", "")) == total:
        return written
    whole, _, fraction = f"{abs(total):f}".partition(".")
    return ("-" if total < 0 else "") + indian_number(Decimal(whole)) + "." + fraction
