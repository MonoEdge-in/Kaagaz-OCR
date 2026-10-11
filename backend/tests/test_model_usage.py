"""The admin usage report's Python half, without a database (model choice, council 11 Oct
2026, SP2, SP4): a period is whole days in TIMEZONE with both ends included and at most
366 days; a CSV cell never starts like a formula; the report splits the grouped rows into
its parts, keeps only document work in the top documents, names a deleted one, cuts the
list at ten and counts an empty period as nothing. The SQL itself: test_model_usage_db."""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy import Connection

from kaagaz.db import usage

TZ = "Asia/Kolkata"


def test_a_period_is_whole_days_in_timezone_both_ends_included() -> None:
    start, end = usage.period_bounds(date(2026, 10, 2), date(2026, 10, 3), TZ)
    assert start.astimezone(UTC) == datetime(2026, 10, 1, 18, 30, tzinfo=UTC)  # 2 Oct 00:00 there
    assert end.astimezone(UTC) == datetime(2026, 10, 3, 18, 30, tzinfo=UTC)  # 4 Oct 00:00 there
    one_day = usage.period_bounds(date(2026, 10, 2), date(2026, 10, 2), "UTC")
    assert one_day == (datetime(2026, 10, 2, tzinfo=UTC), datetime(2026, 10, 3, tzinfo=UTC))
    usage.period_bounds(date(2024, 1, 1), date(2024, 12, 31), TZ)  # a leap year: 366 days


@pytest.mark.parametrize(
    ("start", "end"),
    [(date(2026, 10, 3), date(2026, 10, 2)), (date(2024, 1, 1), date(2025, 1, 1))],  # backwards; 367 days
)
def test_a_period_backwards_or_over_366_days_is_refused(start: date, end: date) -> None:
    with pytest.raises(ValueError, match="366"):
        usage.period_bounds(start, end, TZ)


@pytest.mark.parametrize(
    ("value", "cell"),
    [
        ("=SUM(A1)", "'=SUM(A1)"),
        ("+91 phone", "'+91 phone"),
        ("-1", "'-1"),
        ("@cmd", "'@cmd"),
        ("\tx", "'\tx"),
        ("Asha", "Asha"),
        (None, ""),
        (Decimal("0.250000"), "0.250000"),
        (12, "12"),
    ],
)
def test_a_csv_cell_never_starts_like_a_formula(value: object, cell: str) -> None:
    assert usage.csv_cell(value) == cell


class _Conn:
    """Answers the report's one statement with the grouped rows."""

    def __init__(self, grouped: Sequence[Mapping[str, Any]]) -> None:
        self.grouped = list(grouped)
        self.sent: list[dict[str, Any]] = []

    def execute(self, statement: object, params: dict[str, Any]) -> "_Result":
        self.sent.append(params)
        return _Result(self.grouped)


class _Result:
    def __init__(self, rows: list[Mapping[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[Mapping[str, Any]]:
        return self.rows


def _group(dimension: str, cost: str | None, **keys: Any) -> dict[str, Any]:
    row = {
        "dimension": dimension,
        "user_id": None,
        "model": None,
        "kind": None,
        "day": None,
        "document_id": None,
        "of_document": None,
        "user_name": None,
        "model_name": None,
        "document_name": None,
        "calls": 2,
        "cached_calls": 1,
        "input_tokens": Decimal(10),
        "output_tokens": Decimal(5),
        "reasoning_tokens": Decimal(1),
        "cost_usd": None if cost is None else Decimal(cost),
        "cost_estimated": False,
    }
    return row | keys


def test_the_report_splits_the_groups_and_keeps_document_work_in_the_top_documents() -> None:
    documents = [
        _group("document", "0.5", document_id=f"d{n}", of_document=True, document_name=f"{n}.pdf")
        for n in range(11)
    ]
    grouped = [
        _group("total", "1.5"),
        _group("user", "1.0", user_id="u1", user_name="Asha"),
        _group("user", None, user_id=None),
        _group("model", "1.5", model="a/listed", model_name="Listed"),
        _group("model", None, model="a/unlisted"),
        _group("kind", "1.5", kind="scan"),
        _group("day", "1.5", day=date(2026, 10, 2)),
        _group("document", "0.9", of_document=True),  # deleted for good
        _group("document", "0.8", of_document=False),  # chat and sweeps: no document
        *documents,
    ]
    conn = _Conn(grouped)
    start, end = usage.period_bounds(date(2026, 10, 2), date(2026, 10, 2), TZ)
    report = usage.usage_report(cast(Connection, conn), "c1", start=start, end=end, timezone=TZ, user_id="u1")
    assert conn.sent == [{"c": "c1", "start_at": start, "end_at": end, "tz": TZ, "user": "u1"}]
    assert set(report) == {"totals", "by_user", "by_model", "by_kind", "by_day", "top_documents"}
    assert report["totals"] == {
        "calls": 2,
        "cached_calls": 1,
        "input_tokens": 10,
        "output_tokens": 5,
        "reasoning_tokens": 1,
        "cost_usd": 1.5,
        "cost_estimated": False,
    }
    assert [(row["user_id"], row["user_name"], row["cost_usd"]) for row in report["by_user"]] == [
        ("u1", "Asha", 1.0),
        (None, None, None),
    ]
    assert [(row["model"], row["model_name"]) for row in report["by_model"]] == [
        ("a/listed", "Listed"),
        ("a/unlisted", "a/unlisted"),  # not in the catalogue: its id
    ]
    assert [row["day"] for row in report["by_day"]] == ["2026-10-02"]
    tops = [(row["document_id"], row["name"]) for row in report["top_documents"]]
    assert tops == [(None, usage.DELETED_DOCUMENT)] + [(f"d{n}", f"{n}.pdf") for n in range(9)]


def test_an_empty_period_counts_nothing() -> None:
    start, end = usage.period_bounds(date(2026, 9, 1), date(2026, 9, 30), TZ)
    report = usage.usage_report(cast(Connection, _Conn([])), "c1", start=start, end=end, timezone=TZ)
    assert report["totals"] == {
        "calls": 0,
        "cached_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": 0.0,
        "cost_estimated": False,
    }
    assert [report[key] for key in ("by_user", "by_model", "by_kind", "by_day", "top_documents")] == [[]] * 5
