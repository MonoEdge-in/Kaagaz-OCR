"""The time-left estimate (kaagaz.eta), with fixed numbers (PRD 4 #13)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kaagaz.eta import (
    MODEL_SLOW_MIN_S,
    OCR_SECONDS_OTHER,
    RunTiming,
    after_reading,
    mean,
    preparing,
    rates,
    reading,
    time_left,
)

T0 = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
NONE = (0.0, 0.0)


def scan_run(seconds_per_page: float = 3.0, after: float = 30.0) -> RunTiming:
    """A ready run of ten scanned pages read by RapidOCR."""
    return RunTiming("rapidocr 3.4", 10, 0, 0, 10 * seconds_per_page, 10 * seconds_per_page, after)


def test_a_steady_rate_is_learned_by_page_3() -> None:
    """Three earlier runs at 3 s a page: by page 3 of 20, within 10% and confident."""
    estimate = reading(rates([scan_run()] * 3, "rapidocr"), T0, 3, 20, (9.0, 3), NONE)
    assert estimate.item_s == pytest.approx(96 / 29.4)  # (2 * 6.9 + 2.44 * 30 + 9) / (2 + 24.4 + 3)
    assert abs(estimate.item_s - 3.0) / 3.0 < 0.1 and estimate.confidence == "high"
    assert estimate.finish_at == T0 + timedelta(seconds=17 * estimate.item_s + 30.0)


def test_a_cold_start_is_low_confidence_on_the_constants() -> None:
    cold = rates([], "rapidocr")
    first = reading(cold, T0, 0, 4, NONE, NONE)
    assert first.item_s == pytest.approx(0.5 * 6.9 + 0.5 * 0.15)  # half OCR until pages say
    assert (first.after_s, first.slow_after_s, first.confidence) == (45.0, 20.0, "low")
    assert reading(cold, T0, 3, 4, (21.0, 3), NONE).confidence == "low"  # no after-reading history
    assert after_reading(cold, T0).confidence == "low"


def test_text_layer_pages_then_scans_raise_the_rate() -> None:
    cold = rates([], "rapidocr")
    text = reading(cold, T0, 3, 6, NONE, (0.3, 3))
    assert text.item_s == pytest.approx(0.2 * 6.9 + 0.8 * 0.12)
    mixed = reading(cold, T0, 5, 6, (14.0, 2), (0.3, 3))
    assert mixed.item_s == pytest.approx(3 / 7 * 6.95 + 4 / 7 * 0.12)
    assert mixed.item_s > 2 * text.item_s


def test_a_stall_is_three_pages_long_and_at_least_20_s() -> None:
    slow = reading(rates([scan_run(10.0)] * 3, "rapidocr"), T0, 3, 5, (30.0, 3), NONE)
    assert slow.slow_after_s == pytest.approx(3 * slow.item_s)
    assert reading(rates([scan_run(1.0)] * 3, "rapidocr"), T0, 0, 5, NONE, NONE).slow_after_s == 20.0


def test_a_page_read_by_a_model_is_not_slow_at_25_s() -> None:
    """Progress fix, 16 Oct 2026: a High-thinking page call often passes 20 s, so a model
    read stalls only past 3 x its own seconds a call (90 s at least, PROVISIONAL)."""
    cold = reading(rates([], "gemini"), T0, 0, 1, NONE, NONE)
    # Changed on purpose (fast scan, council 16 Oct 2026): Gemini starts from 35 s a call.
    assert cold.slow_after_s == 3 * 35.0 > MODEL_SLOW_MIN_S == 90.0
    stored_read = {"step": "reading", "done": 0, "total": 1, "started_at": T0.isoformat(), **cold.to_json()}

    def at(seconds: float) -> dict[str, Any]:
        left = time_left(stored_read, now=T0 + timedelta(seconds=seconds))
        assert left is not None
        return left

    # The screen's rule (lib/timeLeft.ts stalled): longer than usual once since_move passes slow_after.
    assert at(25)["since_move_seconds"] <= at(25)["slow_after_seconds"]  # not slow at 25 s
    assert at(60)["done_fraction"] > at(25)["done_fraction"]  # and the bar still moves
    assert at(200)["since_move_seconds"] > at(200)["slow_after_seconds"]  # slow well past it
    assert at(200)["done_fraction"] == at(400)["done_fraction"]  # held still, no work shown
    run = RunTiming("gemini 2.5", 10, 0, 0, 10 * 40.0, 10 * 40.0, 30.0)  # 40 s a call, learned
    learned = rates([run] * 5, "gemini")
    slow = reading(learned, T0, 0, 1, NONE, NONE).slow_after_s
    assert slow == pytest.approx(3 * mean(learned.ocr)) and slow > MODEL_SLOW_MIN_S
    assert reading(rates([], "rapidocr"), T0, 0, 1, NONE, NONE).slow_after_s == 20.0  # RapidOCR as before


def test_after_reading_is_the_median_and_slow_the_90th_percentile() -> None:
    history = [scan_run(after=after) for after in (50.0, 10.0, 40.0, 20.0, 30.0)]
    estimate = after_reading(rates(history, "rapidocr"), T0)
    assert (estimate.after_s, estimate.slow_after_s, estimate.confidence) == (30.0, 50.0, "high")
    assert estimate.finish_at == T0 + timedelta(seconds=30)
    assert after_reading(rates(history[:1], "rapidocr"), T0).slow_after_s == 70.0  # A50 + 20


def test_only_this_engines_runs_and_the_last_20_count() -> None:
    other = RunTiming("an-old-reader 1.0", 10, 0, 0, 900.0, 900.0, 30.0)
    assert mean(rates([other] * 3, "rapidocr").ocr) == pytest.approx(6.9)
    old = [scan_run(3.0)] * 20 + [scan_run(100.0)] * 5
    assert rates(old, "rapidocr") == rates(old[:20], "rapidocr")


def test_an_engine_with_no_figure_of_its_own_starts_from_the_default() -> None:
    """An old run's reader the server no longer has starts from OCR_SECONDS_OTHER (PRD 5 Q6)
    until its own history replaces it. Changed on purpose (fast scan, council 16 Oct 2026):
    Gemini has its own figure, 35 s a call."""
    assert mean(rates([], "an-old-reader").ocr) == pytest.approx(OCR_SECONDS_OTHER)
    assert mean(rates([], "gemini").ocr) == pytest.approx(35.0)


def test_a_model_readers_pages_are_read_ocr_page_concurrency_at_a_time() -> None:
    """Fast scan (council 16 Oct 2026): a 1-page scan by Gemini starts at 35 s or more, and
    20 pages read 6 at a time take ceil(20 / 6) = 4 calls of 35 s, not 20 x 35 s."""
    one = reading(rates([], "gemini", pages_at_once=6, scanned=True), T0, 0, 1, NONE, NONE)
    assert (one.finish_at - T0).total_seconds() - one.after_s >= 35.0
    twenty = reading(rates([], "gemini", pages_at_once=6, scanned=True), T0, 0, 20, NONE, NONE)
    assert (twenty.finish_at - T0).total_seconds() - twenty.after_s == pytest.approx(140.0)
    # Another engine reads one page at a time, as before.
    assert rates([], "rapidocr", pages_at_once=6).pages_at_once == 1


def test_a_sheet_is_one_file_with_no_pages() -> None:
    assert reading(rates([], None), T0, 0, 0, NONE, NONE).finish_at == T0 + timedelta(seconds=2 + 45)
    sheet = RunTiming(None, 0, 0, 1, None, 1.0, 5.0)
    assert reading(rates([sheet], None), T0, 0, 0, NONE, NONE).item_s == pytest.approx(5 / 3)


def test_preparing_covers_the_pages_left_then_all_of_reading_and_after() -> None:
    """Cold: one page drawn in 2 s of four; the prior (1 s, n0 = 2) and that page give 4/3 s a
    page; then four pages read at the cold mix and 45 s after reading. Low confidence."""
    estimate = preparing(rates([], "rapidocr"), T0, 1, 4, (2.0, 1))
    reading_and_after = 4 * (0.5 * 6.9 + 0.5 * 0.15) + 45.0
    assert estimate.item_s == pytest.approx(4 / 3)
    assert estimate.after_s == pytest.approx(reading_and_after)
    assert estimate.finish_at == T0 + timedelta(seconds=3 * estimate.item_s + estimate.after_s)
    assert (estimate.slow_after_s, estimate.confidence) == (20.0, "low")


def test_preparing_learns_from_seconds_preparing_and_is_confident_with_3_runs() -> None:
    """Three runs of ten pages drawn in 5 s: (2 * 1 + 2.44 * 5) / (2 + 24.4) s a page."""
    timed = RunTiming("rapidocr 3.4", 10, 0, 0, 30.0, 30.0, 30.0, 5.0)
    learned = rates([timed] * 3, "rapidocr")
    estimate = preparing(learned, T0, 0, 10, NONE)
    assert estimate.item_s == pytest.approx(14.2 / 26.4) and estimate.confidence == "high"
    untimed = rates([scan_run()] * 3, "rapidocr")  # runs from before seconds_preparing was kept
    assert mean(untimed.prepare) == 1.0 and preparing(untimed, T0, 0, 10, NONE).confidence == "low"
    assert preparing(untimed, T0, 3, 10, (1.5, 3)).confidence == "high"  # three of its own pages


def stored(**changes: object) -> dict[str, object]:
    moved, finish = T0 - timedelta(seconds=4), T0 + timedelta(seconds=60)
    progress = {"step": "reading", "done": 2, "total": 10, "moved_at": moved.isoformat()}
    progress["started_at"] = (T0 - timedelta(seconds=90)).isoformat()
    timing = {"finish_at": finish.isoformat(), "item_s": 5.0, "after_s": 20.0, "slow_after_s": 20.0}
    return {**progress, **timing, "confidence": "high", **changes}


def test_time_left_counts_from_one_now_and_never_below_0() -> None:
    left = time_left(stored(), now=T0)
    assert left is not None
    assert (left["seconds"], left["since_move_seconds"], left["items_left"]) == (60.0, 4.0, 8)
    assert (left["slow_after_seconds"], left["item_seconds"], left["after_seconds"]) == (20.0, 5.0, 20.0)
    assert left["elapsed_seconds"] == 90.0
    late = time_left(stored(), now=T0 + timedelta(minutes=5))
    after = time_left(stored(step="understanding"), now=T0)
    assert late is not None and late["seconds"] == 0.0
    assert after is not None and after["items_left"] == 0


def test_a_worker_clock_30_s_ahead_never_shows_more_than_its_estimate() -> None:
    moved, finish = (T0 + timedelta(seconds=30)).isoformat(), (T0 + timedelta(seconds=90)).isoformat()
    left = time_left(stored(moved_at=moved, finish_at=finish), now=T0)
    assert left is not None and (left["seconds"], left["since_move_seconds"]) == (60.0, 0.0)
    ahead = time_left(stored(started_at=(T0 + timedelta(seconds=30)).isoformat()), now=T0)
    assert ahead is not None and ahead["elapsed_seconds"] == 0.0


def test_time_left_from_preparing_to_indexing_and_never_while_scanning() -> None:
    """Preparing counts the pages left to draw; understanding and indexing none."""
    lefts = [time_left(stored(step=step), now=T0) for step in ("preparing", "understanding", "indexing")]
    assert [None if left is None else left["items_left"] for left in lefts] == [8, 0, 0]
    assert time_left(stored(step="scanning"), now=T0) is None
    rereading = time_left(stored(step="rereading"), now=T0)  # Careful's second reading keeps its bar
    assert rereading is not None and rereading["items_left"] == 0


BAD = [
    {"step": "scanning"},
    {"started_at": None},
    {"confidence": "sure"},
    {"moved_at": "2026-10-04T10:00:00"},
    {"finish_at": None},
]
BAD += [{"finish_at": (T0 - timedelta(minutes=1)).isoformat()}, {"item_s": float("nan")}, {"after_s": True}]
BAD += [{"slow_after_s": "20"}, {"done": "2"}]


@pytest.mark.parametrize("changes", BAD)  # no time zone; no estimate; finish before move; ...
def test_time_left_is_none_unless_every_key_is_well_formed(changes: dict[str, object]) -> None:
    assert time_left(stored(**changes), now=T0) is None


def done(**changes: object) -> object:
    """The ``done_fraction`` of ``stored(**changes)``; ``now`` among them, else T0."""
    now = changes.pop("now", T0)
    assert isinstance(now, datetime)
    left = time_left(stored(**changes), now=now)
    return None if left is None else left["done_fraction"]


def test_done_fraction_is_elapsed_over_elapsed_plus_left_before_a_stall() -> None:
    """Council of 5 Oct 2026: the same number the screen works out live, at T0."""
    left = time_left(stored(), now=T0)
    assert left is not None
    assert left["done_fraction"] == round(90 / (90 + 60), 4)  # 90 s in, 60 s left
    elapsed, seconds = left["elapsed_seconds"], left["seconds"]
    assert left["done_fraction"] == round(elapsed / (elapsed + seconds), 4)


def test_done_fraction_holds_still_once_it_is_taking_longer() -> None:
    """Moved 4 s before T0, slow after 20 s: from T0 + 16 s no work is shown as done,
    so a refresh 400 s later gives the same percent, never 0 and never more."""
    stall = T0 + timedelta(seconds=16)
    at_stall, later = done(now=stall), done(now=stall + timedelta(seconds=400))
    assert at_stall == later == round(106 / (106 + 44), 4)
    assert isinstance(at_stall, float) and at_stall > 0


def test_the_floor_is_after_s_only_while_preparing_or_reading() -> None:
    soon = (T0 + timedelta(seconds=5)).isoformat()  # 5 s left, after_s 20
    floored = [done(step=step, finish_at=soon) for step in ("preparing", "reading")]
    assert floored == [round(90 / 110, 4)] * 2
    after = [done(step=step, finish_at=soon) for step in ("understanding", "indexing")]
    assert after == [round(90 / 95, 4)] * 2


def test_the_runs_reached_wins_when_it_is_higher_and_a_bad_one_is_ignored() -> None:
    assert done(reached=0.9) == 0.9
    assert done(reached=0.1) == done() == round(0.6, 4)  # an old row has no reached
    for bad in (True, float("nan"), -0.1, 1.5, "0.9", None):
        assert done(reached=bad) == 0.6


def test_a_sheet_has_a_done_fraction_too() -> None:
    sheet = time_left(stored(done=0, total=0), now=T0)
    assert sheet is not None and sheet["items_left"] == 0 and sheet["done_fraction"] == 0.6


def prior(**changes: object) -> dict[str, object]:
    """The estimate written before: moved 4 s before T0 too, 30 s left from T0, no floor."""
    moved, finish = (T0 - timedelta(seconds=4)).isoformat(), (T0 + timedelta(seconds=30)).isoformat()
    timing = {"moved_at": moved, "finish_at": finish, "after_s": 0.0, "slow_after_s": 20.0}
    return {"step": "understanding", **timing, **changes}


def test_the_prior_estimate_wins_while_a_screen_still_counts_on_from_it() -> None:
    """Review of 5 Oct 2026: a screen that has not polled since the last write still shows
    the prior estimate's F, so the API never serves less; held still by its own stall."""
    assert done(prior=prior()) == round(90 / 120, 4)  # above F's 0.6
    stall = T0 + timedelta(seconds=16)
    held = [done(prior=prior(), now=at) for at in (stall, stall + timedelta(seconds=400))]
    assert held == [round(106 / 120, 4)] * 2


BAD_PRIOR: list[object] = ["x", {}, prior(step="scanning"), prior(moved_at=None), prior(after_s=float("nan"))]
BAD_PRIOR += [prior(finish_at=(T0 - timedelta(minutes=1)).isoformat()), prior(moved_at="2026-10-04T10:00:00")]


@pytest.mark.parametrize("bad", BAD_PRIOR)  # not an object; no keys; scanning; no move; NaN; ...
def test_a_missing_or_malformed_prior_is_ignored(bad: object) -> None:
    assert done(prior=bad) == done() == 0.6
