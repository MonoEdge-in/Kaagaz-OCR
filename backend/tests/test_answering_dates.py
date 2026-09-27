"""Date words to date ranges, with a fixed "today" (kaagaz/answering/dates.py)."""

from datetime import date

import pytest

from kaagaz.answering.dates import date_range, describe_range, mentions_date
from kaagaz.answering.hinglish import normalise

TODAY = date(2026, 9, 27)


def _range(question: str, today: date = TODAY) -> tuple[date, date] | None:
    return date_range(normalise(question), today)


def test_last_month() -> None:
    assert _range("profit last month") == (date(2026, 8, 1), date(2026, 9, 1))


def test_last_month_in_january_is_december_of_the_year_before() -> None:
    assert _range("profit last month", date(2026, 1, 1)) == (date(2025, 12, 1), date(2026, 1, 1))
    assert _range("profit last month", date(2026, 1, 31)) == (date(2025, 12, 1), date(2026, 1, 1))


def test_last_month_on_the_first_and_last_day_of_a_month() -> None:
    assert _range("pichle mahine", date(2026, 3, 1)) == (date(2026, 2, 1), date(2026, 3, 1))
    assert _range("pichle mahine", date(2026, 3, 31)) == (date(2026, 2, 1), date(2026, 3, 1))


def test_this_month_including_december() -> None:
    assert _range("is mahine ki bikri") == (date(2026, 9, 1), date(2026, 10, 1))
    assert _range("this month", date(2026, 12, 5)) == (date(2026, 12, 1), date(2027, 1, 1))


def test_last_year_and_this_year() -> None:
    assert _range("pichle saal") == (date(2025, 1, 1), date(2026, 1, 1))
    assert _range("this year") == (date(2026, 1, 1), date(2027, 1, 1))


def test_month_name_with_a_year() -> None:
    assert _range("profit in August 2026") == (date(2026, 8, 1), date(2026, 9, 1))
    assert _range("sales dec 2025") == (date(2025, 12, 1), date(2026, 1, 1))


def test_month_name_without_a_year_is_the_latest_one_that_started() -> None:
    assert _range("profit in august") == (date(2026, 8, 1), date(2026, 9, 1))
    assert _range("profit in september") == (date(2026, 9, 1), date(2026, 10, 1))
    assert _range("profit in november") == (date(2025, 11, 1), date(2025, 12, 1))


def test_in_a_year() -> None:
    assert _range("total sales in 2025") == (date(2025, 1, 1), date(2026, 1, 1))


def test_last_n_days_ends_today() -> None:
    assert _range("expense in the last 30 days") == (date(2026, 8, 29), date(2026, 9, 28))
    assert _range("pichle 1 din") == (TODAY, date(2026, 9, 28))


def test_may_is_a_month_only_next_to_a_year_or_after_in() -> None:
    assert _range("who may have the highest profit") is None
    assert _range("profit in may") == (date(2026, 5, 1), date(2026, 6, 1))
    assert _range("profit may 2025") == (date(2025, 5, 1), date(2025, 6, 1))


def test_two_different_ranges_are_not_guessed() -> None:
    assert _range("profit last month and this month") is None
    assert _range("profit august 2026 or july 2026") is None


def test_the_same_range_said_twice_is_fine() -> None:
    assert _range("august 2026 profit in august") == (date(2026, 8, 1), date(2026, 9, 1))


def test_no_date_words_gives_no_range() -> None:
    assert _range("whose profit highest") is None
    assert not mentions_date(normalise("whose profit highest"))


@pytest.mark.parametrize("question", ["profit last week", "profit last quarter", "profit 2026 se pehle"])
def test_date_words_we_cannot_read_are_still_noticed(question: str) -> None:
    assert _range(question) is None
    assert mentions_date(normalise(question))


def test_describe_range() -> None:
    assert describe_range(date(2026, 8, 1), date(2026, 9, 1)) == "in August 2026"
    assert describe_range(date(2025, 12, 1), date(2026, 1, 1)) == "in December 2025"
    assert describe_range(date(2026, 1, 1), date(2027, 1, 1)) == "in 2026"
    assert describe_range(date(2026, 8, 29), date(2026, 9, 28)) == "from 29 Aug 2026 to 27 Sep 2026"
