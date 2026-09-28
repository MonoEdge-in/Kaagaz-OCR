"""Turn the date words of a question into a date range.

Every range is ``[start, end)``: start included, end excluded, so "last
month" asked on 27 Sep 2026 is 1 Aug 2026 up to (not including) 1 Sep 2026.
``today`` is the customer's local date: the service takes it from its clock
in ``settings.timezone``, never from the server's own zone.

Understood (on the English text from ``hinglish.normalise``):

* "last month", "this month", "last year", "this year" (calendar years)
* a month name, with an optional year: "august 2026", "aug". With no year it
  is the latest such month that has started (asked in September, "november"
  is last November)
* "in 2026"
* "last 30 days": the 30 days ending today, today included

Never a guess: two different ranges in one question give None, and the
service then does not use a fixed query (``mentions_date`` tells it the
question spoke about a date it could not pin down).

PROVISIONAL: calendar years, not financial years (April to March); the answer
rules are Vrushit's (docs/system-design/03).

Time is O(n) in the number of words.
"""

import re
from datetime import date, timedelta

from kaagaz.answering.hinglish import DATE_WORDS, NormalisedQuestion

DateRange = tuple[date, date]

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}  # fmt: skip
_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
_YEAR = re.compile(r"(?:19|20)[0-9]{2}")
# "may" is also an English verb: it is a month only next to a year or after "in".
_MAY_NEEDS = frozenset({"in", "of", "for", "during"})
_MAX_DAYS = 3660


def date_range(normalised: NormalisedQuestion, today: date) -> DateRange | None:
    """The one date range the question names, or None (none, or more than one)."""
    found = _ranges(normalised.words, today)
    distinct = set(found)
    if len(distinct) != 1:
        return None
    return distinct.pop()


def mentions_date(normalised: NormalisedQuestion) -> bool:
    """True if any word of the question is about dates (even one we cannot read)."""
    return any(word in DATE_WORDS or _YEAR.fullmatch(word) for word in normalised.words)


def describe_range(start: date, end: date) -> str:
    """ "in August 2026", "in 2026", or "from 29 Aug 2026 to 27 Sep 2026"."""
    if start.day == 1 and end == _next_month(start):
        return f"in {_MONTH_NAMES[start.month - 1]} {start.year}"
    if (start.month, start.day) == (1, 1) and end == date(start.year + 1, 1, 1):
        return f"in {start.year}"
    last = end - timedelta(days=1)
    return f"from {_day(start)} to {_day(last)}"


def _ranges(words: list[str], today: date) -> list[DateRange]:
    found: list[DateRange] = []
    used: set[int] = set()
    for i, word in enumerate(words):
        following = words[i + 1] if i + 1 < len(words) else ""
        after_that = words[i + 2] if i + 2 < len(words) else ""
        if word in {"last", "this"} and following in {"month", "year"}:
            found.append(_period(word, following, today))
            used.update({i, i + 1})
        elif word == "last" and following.isdigit() and after_that in {"days", "day"}:
            days = int(following)
            if 1 <= days <= _MAX_DAYS:
                found.append((today - timedelta(days=days - 1), today + timedelta(days=1)))
                used.update({i, i + 1, i + 2})
        elif word in _MONTHS and i not in used:
            year = int(following) if _YEAR.fullmatch(following) else None
            before = words[i - 1] if i > 0 else ""
            if word == "may" and year is None and before not in _MAY_NEEDS:
                continue
            found.append(_month(_MONTHS[word], year, today))
            used.add(i)
            if year is not None:
                used.add(i + 1)
        elif _YEAR.fullmatch(word) and i not in used and i > 0 and words[i - 1] == "in":
            year = int(word)
            found.append((date(year, 1, 1), date(year + 1, 1, 1)))
    return found


def _period(which: str, unit: str, today: date) -> DateRange:
    if unit == "year":
        year = today.year - 1 if which == "last" else today.year
        return date(year, 1, 1), date(year + 1, 1, 1)
    this_month = today.replace(day=1)
    if which == "this":
        return this_month, _next_month(this_month)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    return last_month, this_month


def _month(month: int, year: int | None, today: date) -> DateRange:
    if year is None:
        year = today.year if month <= today.month else today.year - 1
    start = date(year, month, 1)
    return start, _next_month(start)


def _next_month(first: date) -> date:
    return date(first.year + 1, 1, 1) if first.month == 12 else date(first.year, first.month + 1, 1)


def _day(value: date) -> str:
    return f"{value.day} {_MONTH_NAMES[value.month - 1][:3]} {value.year}"
