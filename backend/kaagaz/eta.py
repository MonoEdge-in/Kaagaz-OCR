"""How long until a document is ready: the worker's estimate (PRD 4 #13, council of
4 Oct 2026, PROVISIONAL). Pure: no database and no clock of its own.

The worker builds ``Rates`` once a run from this customer's own last HISTORY_RUNS
ready runs (db/runs.py ``recent_timings``; never another customer's), then writes
an ``Estimate`` with each reading and understanding step (pipeline ``_progress``).

* Seconds per unit for ``ocr`` pages (this engine's runs only), ``fast`` pages
  (text layer or blank) and ``cells`` files (a sheet is read whole): a Gamma-style
  posterior mean, the constant counting as PRIOR_WEIGHT (n0) units, run i back
  (0 the newest) weighing w_i = (1 - ALPHA) ** i, this run's own units weighing 1:
  ``rate = (n0 * prior + sum w_i * seconds_i + seconds_now) / (n0 + sum w_i * units_i + units_now)``.
* The route mix, learned as pages finish: ``p = (n0 * p0 + ocr_now) / (n0 + pages_now)``,
  p0 the history's weighted OCR share (one half with none). A page left takes
  ``item_s = p * ocr_rate + (1 - p) * fast_rate``.
* After reading (second reader, model calls, save, index): ``after_s`` is the
  median (A50) and ``slow`` the 90th percentile (A90, at least A50 + SLOW_MIN_S)
  of the history's ``seconds_after_reading``, nearest rank; the constant with none.
* While reading: ``finish_at = now + pages_left * item_s + after_s``, stalled after
  ``max(SLOW_MIN_S, SLOW_FACTOR * item_s)`` with no page read. From the end of
  reading: ``finish_at = now + after_s``, stalled after ``slow``.
* ``confidence`` is "high" only with MIN_EVIDENCE pages read in this run or runs of
  history for reading, and MIN_EVIDENCE runs with an after-reading time; else "low",
  and the screen says "Estimating..." instead of a number.

``time_left`` gives the API's ``time_left`` with one ``now`` a request. A worker
clock that runs ahead never shows more than its own estimate: the seconds left
are clamped to ``finish_at - moved_at``.

Time: ``rates`` O(K) for K runs; the rest O(1). Space: O(1) besides the history.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# PROVISIONAL (council 4 Oct 2026): to be calibrated on the g4dn against real runs.
ALPHA = 0.2
HISTORY_RUNS = 20  # K
PRIOR_WEIGHT = 2.0  # n0
MIN_EVIDENCE = 3
# RapidOCR: 6.9 s a scanned page on average (cli measure, 30 Sep 2026, docs/architecture.md).
# PaddleOCR-VL on the T4 is not measured yet: a guess, until a run's own pages and history replace it.
OCR_SECONDS = {"rapidocr": 6.9, "paddleocr-vl": 5.0}
OCR_SECONDS_OTHER = 7.0  # the upload estimate's 7 s a page
FAST_PAGE_SECONDS = 0.15  # measured 0.05 a text-layer page, plus the page's own work
CELLS_FILE_SECONDS = 2.0
AFTER_READING_SECONDS = 45.0
SLOW_FACTOR = 3.0
SLOW_MIN_S = 20.0
MAX_SECONDS = 86400.0  # a stored number past a day is not believed

Pair = tuple[float, float]  # (seconds, units): a rate's sums, or this run's own measure


@dataclass(frozen=True, slots=True)
class RunTiming:
    """One of this customer's earlier ready runs (db/runs.py ``recent_timings``)."""

    ocr_engine: str | None  # "<name> <version>" when a page went to OCR
    pages_ocr: int
    pages_fast: int  # text layer and blank
    pages_cells: int
    seconds_ocr: float | None
    seconds_reading: float | None  # None for runs from before this estimate
    seconds_after: float | None


@dataclass(frozen=True, slots=True)
class Rates:
    ocr: Pair  # sums, the prior included
    fast: Pair
    cells: Pair
    ocr_share: float  # p0
    after_s: float  # A50
    slow_s: float  # A90
    reading_runs: int
    after_runs: int


@dataclass(frozen=True, slots=True)
class Estimate:
    moved_at: datetime
    finish_at: datetime
    item_s: float
    after_s: float
    slow_after_s: float
    confidence: str  # "low" or "high"

    def to_json(self) -> dict[str, object]:
        numbers = {"item_s": self.item_s, "after_s": self.after_s, "slow_after_s": self.slow_after_s}
        times = {"moved_at": self.moved_at.isoformat(), "finish_at": self.finish_at.isoformat()}
        rounded = {key: round(value, 3) for key, value in numbers.items()}
        return {**times, **rounded, "confidence": self.confidence}


def mean(rate: Pair, now: Pair = (0.0, 0.0)) -> float:
    return (rate[0] + now[0]) / (rate[1] + now[1])


def rates(history: Sequence[RunTiming], engine: str | None) -> Rates:
    """The rates from this customer's history, newest first, for the OCR engine in use
    (its ``name``). O(K)."""
    prior = OCR_SECONDS.get(engine or "", OCR_SECONDS_OTHER)
    ocr = [PRIOR_WEIGHT * prior, PRIOR_WEIGHT]
    fast = [PRIOR_WEIGHT * FAST_PAGE_SECONDS, PRIOR_WEIGHT]
    cells = [PRIOR_WEIGHT * CELLS_FILE_SECONDS, PRIOR_WEIGHT]
    ocr_pages = routed_pages = 0.0
    after = sorted(run.seconds_after for run in history[:HISTORY_RUNS] if run.seconds_after is not None)
    for i, run in enumerate(history[:HISTORY_RUNS]):
        w = (1 - ALPHA) ** i
        if run.pages_ocr and run.seconds_ocr is not None and (run.ocr_engine or "").split(" ")[0] == engine:
            ocr[0] += w * run.seconds_ocr
            ocr[1] += w * run.pages_ocr
        ocr_pages += w * run.pages_ocr
        routed_pages += w * (run.pages_ocr + run.pages_fast)
        if run.seconds_reading is not None and run.pages_cells:
            cells[0] += w * run.seconds_reading
            cells[1] += w
        elif run.seconds_reading is not None and run.pages_fast:
            fast[0] += w * max(0.0, run.seconds_reading - (run.seconds_ocr or 0.0))
            fast[1] += w * run.pages_fast
    after_s = _nearest_rank(after, 0.5) if after else AFTER_READING_SECONDS
    slow = max(_nearest_rank(after, 0.9) if after else 0.0, after_s + SLOW_MIN_S)
    reading_runs = sum(1 for run in history[:HISTORY_RUNS] if run.seconds_reading is not None)
    share = ocr_pages / routed_pages if routed_pages else 0.5
    sums = ((ocr[0], ocr[1]), (fast[0], fast[1]), (cells[0], cells[1]))
    return Rates(*sums, share, after_s, slow, reading_runs, len(after))


def reading(rates: Rates, now: datetime, done: int, total: int, ocr: Pair, fast: Pair) -> Estimate:
    """While reading: ``done`` of ``total`` pages read (total 0: a sheet, read whole),
    with this run's own measured (seconds, pages) by kind. O(1)."""
    if total <= 0:
        item_s = left = mean(rates.cells)
    else:
        share = (PRIOR_WEIGHT * rates.ocr_share + ocr[1]) / (PRIOR_WEIGHT + ocr[1] + fast[1])
        item_s = share * mean(rates.ocr, ocr) + (1 - share) * mean(rates.fast, fast)
        left = max(0, total - done) * item_s
    known = done >= MIN_EVIDENCE or rates.reading_runs >= MIN_EVIDENCE
    confidence = "high" if known and rates.after_runs >= MIN_EVIDENCE else "low"
    finish, slow = now + timedelta(seconds=left + rates.after_s), max(SLOW_MIN_S, SLOW_FACTOR * item_s)
    return Estimate(now, finish, item_s, rates.after_s, slow, confidence)


def after_reading(rates: Rates, now: datetime) -> Estimate:
    """From the end of reading until ready. O(1)."""
    confidence = "high" if rates.after_runs >= MIN_EVIDENCE else "low"
    return Estimate(now, now + timedelta(seconds=rates.after_s), 0.0, rates.after_s, rates.slow_s, confidence)


def time_left(progress: Mapping[str, Any], *, now: datetime) -> dict[str, Any] | None:
    """The API's ``time_left`` from a stored progress at ``now``; None unless the step is
    reading or understanding and every estimate key is well formed. O(1)."""
    step, done, total = progress.get("step"), progress.get("done"), progress.get("total")
    if step not in ("reading", "understanding") or not isinstance(done, int) or not isinstance(total, int):
        return None
    moved, finish = _instant(progress.get("moved_at")), _instant(progress.get("finish_at"))
    item_s, after_s, slow_s = (as_seconds(progress.get(key)) for key in ("item_s", "after_s", "slow_after_s"))
    if moved is None or finish is None or item_s is None or after_s is None or slow_s is None:
        return None
    span = (finish - moved).total_seconds()
    if not 0 <= span <= MAX_SECONDS or progress.get("confidence") not in ("low", "high"):
        return None
    return {
        "seconds": round(min(span, max(0.0, (finish - now).total_seconds())), 1),
        "since_move_seconds": round(max(0.0, (now - moved).total_seconds()), 1),
        "slow_after_seconds": round(slow_s, 1),
        "items_left": max(0, total - done) if step == "reading" else 0,
        "item_seconds": round(item_s, 2),
        "after_seconds": round(after_s, 1),
        "confidence": progress["confidence"],
    }


def _nearest_rank(ordered: Sequence[float], q: float) -> float:
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _instant(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return parsed if parsed is not None and parsed.tzinfo is not None else None


def as_seconds(value: object) -> float | None:
    """A JSON number of seconds, 0 to MAX_SECONDS, or None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and 0 <= value <= MAX_SECONDS else None
