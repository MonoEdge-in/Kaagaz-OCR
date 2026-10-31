"""The accuracy check against max settings without a database (#463, Tushar, 31 Oct 2026):
the comparison's maths (words and numbers), the queue and the worker's routing of a check,
every call of a check billed as accuracy_check, and a page carrying the settings of the read
that produced its text. Against PostgreSQL: test_accuracy_check_db."""

import dataclasses
from contextlib import nullcontext
from typing import Any, cast
from unittest import mock

import pytest

from kaagaz.adapters.queue import _body, _job_from_body
from kaagaz.ai import usage
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import _page_row
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.accuracy import numbers_matched, word_errors
from kaagaz.pipeline import PageRecord, _with_result
from kaagaz.reading.ocr import DocumentPages
from kaagaz.reading.spans import PageInfo
from kaagaz.worker_main import MarkExhausted, RecordAttempt, dispatch
from tests.test_cost_saving import HARD, WORDS, Client, first_reply, reader
from tests.test_dynamic_reading import STANDARD, ledger
from tests.test_fast_scan import inline_pages
from tests.test_gemini_ocr import FakeCache, page, reply, value


def split(words: str) -> list[str]:
    """The words, whitespace normalised (as ``jobs.accuracy.words`` splits a page's text)."""
    return words.split()


def wer(reference: str, read: str) -> float:
    return word_errors(reference.split(), read.split()) / len(reference.split())


# --- the comparison ------------------------------------------------------------------------


def test_identical_words_match_whatever_the_whitespace() -> None:
    assert word_errors(split("Total  1,180.00\n paid"), split("Total 1,180.00 paid")) == 0


def test_a_known_word_error_rate() -> None:
    # One word changed and one left out of four: WER 0.5, so a word match of 0.5.
    assert wer("bill to sharma traders", "bill for sharma") == pytest.approx(0.5)
    assert word_errors(["a", "b"], ["a", "b", "c", "d"]) == 2  # words added count too
    assert word_errors([], ["x"]) == 1 and word_errors(["x"], []) == 1


def test_a_changed_amount_shows_in_the_numbers_not_only_the_words() -> None:
    reference = split("Invoice 1042 dated 12/10/2026 " + "item " * 200 + "Total ₹12,450")
    wrong = split("Invoice 1042 dated 12/10/2026 " + "item " * 200 + "Total ₹8,000")
    assert 1 - word_errors(reference, wrong) / len(reference) > 0.99  # one word in hundreds
    assert numbers_matched(reference, wrong) == (2, 3)  # a clear drop: 1042 and the date kept


def test_reordered_lines_lose_no_numbers_and_the_prefixes_and_commas_go() -> None:
    reference = split("GSTIN 27AAACO1234 Total Rs.12,450 Paid 8,000")
    reordered = split("Paid 8000 Total ₹12450 GSTIN 27AAACO1234")
    assert numbers_matched(reference, reordered) == (3, 3)
    assert numbers_matched(split("1 1 2"), split("1 2")) == (2, 3)  # a multiset: counted as often
    assert numbers_matched(split("no number here"), split("12")) == (0, 0)  # null in the check


# --- the queue and the worker ----------------------------------------------------------------


def test_the_queue_carries_check_id_only_when_set_and_the_worker_routes_and_fails_a_check() -> None:
    check = Job("c", "k", check_id="k")
    assert _job_from_body(_body(check, 1)) == (check, 1)
    assert "check_id" not in _body(Job("c", "d"))
    assert _job_from_body('{"customer_id": "c", "document_id": "d", "check_id": ""}') is None

    seen: list[str] = []
    handle = dispatch(
        lambda job, beat: seen.append("pipeline"),
        lambda job, beat: seen.append("ensemble"),
        lambda job, beat: seen.append("accuracy"),
    )
    for job in (Job("c", "d"), Job("c", "d", run_id="r"), check):
        handle(job, lambda: None)
    assert seen == ["pipeline", "ensemble", "accuracy"]

    marked: list[tuple[str, ...]] = []
    MarkExhausted(
        lambda c, d: marked.append(("document", d)), mark_check=lambda c, k: marked.append(("check", k))
    )(check)
    RecordAttempt(lambda c, d, a, m: marked.append(("try", d)), 5)(check, 2)  # a check keeps no tries
    assert marked == [("check", "k")]


# --- billing -------------------------------------------------------------------------------


def test_every_call_of_a_check_is_billed_as_accuracy_check_careful_too() -> None:
    kept: list[Any] = []

    class _Ledger:
        def customer(self, customer_id: str) -> Any:
            return nullcontext(self)

    def insert(conn: Any, rows: list[Any]) -> None:
        kept.extend(rows)

    db = cast(Database, _Ledger())
    with (
        mock.patch.object(usage, "insert_usage", insert),
        usage.usage_scope(db, customer_id="c", user_id=None, kind=usage.ACCURACY_CHECK),
    ):
        usage.record_call("google/x", None, 1, 1, 0, 0.01, thinking="minimal", resolution="low")
        with usage.billed_as("careful"):
            usage.record_call("google/x", None, 1, 1, 0, 0.02, thinking="high", resolution="high")
    assert {row.kind for row in kept} == {usage.ACCURACY_CHECK}
    assert {(row.thinking, row.resolution) for row in kept} == {(None, None)}  # not a scan's measure


# --- the page's own settings ---------------------------------------------------------------


def test_a_page_keeps_the_settings_of_the_read_that_produced_its_text() -> None:
    client = Client({STANDARD: first_reply(HARD)}, crops=WORDS)
    pages = dataclasses.replace(inline_pages(3), resolution="low", careful_thinking="medium")
    one = reader(client)
    with ledger(), one.document(pages):
        read = one.read_page(page(), page_no=1)
    assert (read.read_thinking, read.read_resolution) == ("low", "low")  # its first read's (words kept)

    again = Client({STANDARD: reply(value())})
    harder = DocumentPages(
        customer_id="c",
        document_id="d",
        cache=FakeCache(),
        model=STANDARD,
        read_again=frozenset({1}),
        careful_thinking="minimal",
        careful_resolution="low",
    )
    other = reader(again)
    with other.document(harder):
        reread = other.read_page(page(), page_no=1)
    # Changed on purpose (#463 review): a page read again keeps its first read's settings (the
    # document's: the env's here), so the free signal counts it by its first read.
    assert (reread.read_thinking, reread.read_resolution, reread.read_pass) == ("low", "high", "again")

    record = _with_result(PageRecord(PageInfo(1, 100.0, 100.0, "px"), True, route="ocr"), read, 0)
    assert (record.read_thinking, record.read_resolution) == ("low", "low")
    row = _page_row({"c": "c", "d": "d"}, record, {})
    assert (row["read_thinking"], row["read_resolution"]) == ("low", "low")
    plain = _page_row({"c": "c", "d": "d"}, PageRecord(PageInfo(1, 100.0, 100.0, "px"), False), {})
    assert (plain["read_thinking"], plain["read_resolution"]) == (None, None)
