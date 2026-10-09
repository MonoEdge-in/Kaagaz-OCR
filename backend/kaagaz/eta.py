"""How long until a document is ready: the worker's estimate (PRD 4 #13, council of
4 Oct 2026, PROVISIONAL). Pure: no database and no clock of its own.

The worker builds ``Rates`` once a run from this customer's own last HISTORY_RUNS
ready runs (db/runs.py ``recent_timings``; never another customer's), then writes
an ``Estimate`` with each step from preparing on (pipeline ``_progress``).

* Seconds per unit for ``ocr`` pages (this engine's runs only), ``fast`` pages
  (text layer or blank) and ``cells`` files (a sheet is read whole): a Gamma-style
  posterior mean, the constant counting as PRIOR_WEIGHT (n0) units, run i back
  (0 the newest) weighing w_i = (1 - ALPHA) ** i, this run's own units weighing 1:
  ``rate = (n0 * prior + sum w_i * seconds_i + seconds_now) / (n0 + sum w_i * units_i + units_now)``.
* Seconds a page ``prepare`` (drawn at OCR_DPI and stored), the same posterior from
  the history's ``seconds_preparing`` over its pages read and this run's own pages.
* The route mix, learned as pages finish: ``p = (n0 * p0 + ocr_now) / (n0 + pages_now)``,
  p0 the history's weighted OCR share (one half with none). A page left takes
  ``item_s = p * ocr_rate + (1 - p) * fast_rate``.
* After reading (second reader, model calls, save, index): ``after_s`` is the
  median (A50) and ``slow`` the 90th percentile (A90, at least A50 + SLOW_MIN_S)
  of the history's ``seconds_after_reading``, nearest rank; the constant with none.
* While preparing: ``finish_at = now + pages_left * prepare_s + after_s``, where
  ``after_s`` is all of reading (``total * item_s`` before any page is read) and the
  time after it; stalled after ``max(SLOW_MIN_S, SLOW_FACTOR * prepare_s)``.
* While reading: ``finish_at = now + pages_left * item_s + after_s``, stalled after
  ``max(SLOW_MIN_S, SLOW_FACTOR * item_s)`` with no page read. From the end of
  reading (understanding, then indexing): ``finish_at = now + after_s``, stalled after ``slow``.
* ``confidence`` is "high" only with MIN_EVIDENCE pages read in this run or runs of
  history for reading, and MIN_EVIDENCE runs with an after-reading time (and, while
  preparing, MIN_EVIDENCE pages or runs for preparing too); else "low", and the
  screen labels its number a rough guess.

``time_left`` gives the API's ``time_left`` with one ``now`` a request, and the
seconds since the run started (``started_at``), so the screen can show a percentage:
elapsed / (elapsed + left). A worker
clock that runs ahead never shows more than its own estimate: the seconds left
are clamped to ``finish_at - moved_at``.

``done_fraction`` is that percentage worked out here, the same on every load, so a
refreshed page starts from it, not from 0 (council of 5 Oct 2026, PROVISIONAL):
``F = e / (e + max(finish_at - tau, floor))``, ``e = tau - started_at``, at
``tau = clamp(now, moved_at, moved_at + slow_after_s)``: it stops when no page moves
for longer than usual, so it never climbs while no work is done. ``floor`` is
``after_s`` while preparing or reading and 0 after, as the screen's countdown
(lib/timeLeft.ts secondsLeft). The worker keeps the highest F it has shown in this
run as ``reached`` (pipeline ``_progress``), so a new estimate never takes it back,
and the estimate written before this one as ``prior``: a screen that has not polled
since still counts on from it, so a refresh never shows less than that screen did
(review of 5 Oct 2026). ``done_fraction = max(reached, F_prior(now), F(now))``, each
F held still by its own stall.

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
# Any other engine name (an old run's reader too) starts from OCR_SECONDS_OTHER.
# Gemini (PRD 5 Q6, V6) has no entry: its figure is the seconds a page from the `measure` run,
# divided by OCR_PAGE_CONCURRENCY (the pages read at once), and that run has not been made, so
# no number is guessed. Until it is, a "gemini" page starts from OCR_SECONDS_OTHER, and the
# customer's own Gemini runs replace it as they finish.
OCR_SECONDS = {"rapidocr": 6.9}
OCR_SECONDS_OTHER = 7.0  # the upload estimate's 7 s a page
# Not measured yet: a guess for an A4 page drawn at 400 DPI, checked and stored, until history replaces it.
PREPARE_PAGE_SECONDS = 1.0
FAST_PAGE_SECONDS = 0.15  # measured 0.05 a text-layer page, plus the page's own work
CELLS_FILE_SECONDS = 2.0
AFTER_READING_SECONDS = 45.0
SLOW_FACTOR = 3.0
SLOW_MIN_S = 20.0
MAX_SECONDS = 86400.0  # a stored number past a day is not believed

_TIMED_STEPS = ("preparing", "reading", "understanding", "indexing")
FLOORED_STEPS = ("preparing", "reading")  # the time left never counts below after_s

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
    seconds_preparing: float | None = None  # None for runs from before it was kept


@dataclass(frozen=True, slots=True)
class Rates:
    ocr: Pair  # sums, the prior included
    fast: Pair
    cells: Pair
    prepare: Pair
    ocr_share: float  # p0
    after_s: float  # A50
    slow_s: float  # A90
    reading_runs: int
    after_runs: int
    preparing_runs: int


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
    prepare = [PRIOR_WEIGHT * PREPARE_PAGE_SECONDS, PRIOR_WEIGHT]
    preparing_runs = 0
    ocr_pages = routed_pages = 0.0
    after = sorted(run.seconds_after for run in history[:HISTORY_RUNS] if run.seconds_after is not None)
    for i, run in enumerate(history[:HISTORY_RUNS]):
        w = (1 - ALPHA) ** i
        if run.pages_ocr and run.seconds_ocr is not None and (run.ocr_engine or "").split(" ")[0] == engine:
            ocr[0] += w * run.seconds_ocr
            ocr[1] += w * run.pages_ocr
        ocr_pages += w * run.pages_ocr
        if run.seconds_preparing is not None and run.pages_ocr + run.pages_fast:
            prepare[0] += w * run.seconds_preparing
            prepare[1] += w * (run.pages_ocr + run.pages_fast)
            preparing_runs += 1
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
    sums = ((ocr[0], ocr[1]), (fast[0], fast[1]), (cells[0], cells[1]), (prepare[0], prepare[1]))
    return Rates(*sums, share, after_s, slow, reading_runs, len(after), preparing_runs)


def preparing(rates: Rates, now: datetime, done: int, total: int, own: Pair) -> Estimate:
    """While preparing: ``done`` of ``total`` pages drawn, with this run's own measured
    (seconds, pages). Then every page is read and the time after reading follows. O(1)."""
    prepare_s = mean(rates.prepare, own)
    read = reading(rates, now, 0, total, (0.0, 0.0), (0.0, 0.0))
    after_s = (read.finish_at - now).total_seconds()
    finish = now + timedelta(seconds=max(0, total - done) * prepare_s + after_s)
    known = done >= MIN_EVIDENCE or rates.preparing_runs >= MIN_EVIDENCE
    confidence = "high" if known and read.confidence == "high" else "low"
    return Estimate(now, finish, prepare_s, after_s, max(SLOW_MIN_S, SLOW_FACTOR * prepare_s), confidence)


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


def fraction_at(estimate: Estimate, *, started_at: datetime, floored: bool, now: datetime) -> float:
    """F: how far the run has got at ``now`` by this estimate, 0 to 1, held still from
    ``slow_after_s`` with no move (``tau``); ``floored`` while preparing or reading. O(1)."""
    stall = estimate.moved_at + timedelta(seconds=estimate.slow_after_s)
    tau = min(max(now, estimate.moved_at), stall)
    elapsed = min(MAX_SECONDS, max(0.0, (tau - started_at).total_seconds()))
    left = max(0.0, (estimate.finish_at - tau).total_seconds())
    whole = elapsed + (max(left, estimate.after_s) if floored else left)
    return elapsed / whole if whole > 0 else 0.0


def time_left(progress: Mapping[str, Any], *, now: datetime) -> dict[str, Any] | None:
    """The API's ``time_left`` from a stored progress at ``now``; None while scanning (no
    estimate yet) and unless every estimate key is well formed. ``done_fraction`` is
    the largest of the stored ``reached`` (when it is a number 0 to 1), F of a well
    formed ``prior`` and F. O(1)."""
    step, done, total = progress.get("step"), progress.get("done"), progress.get("total")
    if step not in _TIMED_STEPS or not isinstance(done, int) or not isinstance(total, int):
        return None
    moved, finish = _instant(progress.get("moved_at")), _instant(progress.get("finish_at"))
    started = _instant(progress.get("started_at"))
    item_s, after_s, slow_s = (as_seconds(progress.get(key)) for key in ("item_s", "after_s", "slow_after_s"))
    if moved is None or finish is None or started is None:
        return None
    if item_s is None or after_s is None or slow_s is None:
        return None
    span = (finish - moved).total_seconds()
    if not 0 <= span <= MAX_SECONDS or progress.get("confidence") not in ("low", "high"):
        return None
    estimate = Estimate(moved, finish, item_s, after_s, slow_s, progress["confidence"])
    shown = fraction_at(estimate, started_at=started, floored=step in FLOORED_STEPS, now=now)
    return {
        "seconds": round(min(span, max(0.0, (finish - now).total_seconds())), 1),
        "since_move_seconds": round(max(0.0, (now - moved).total_seconds()), 1),
        "slow_after_seconds": round(slow_s, 1),
        "items_left": max(0, total - done) if step in ("preparing", "reading") else 0,
        "item_seconds": round(item_s, 2),
        "after_seconds": round(after_s, 1),
        "confidence": progress["confidence"],
        "elapsed_seconds": round(min(MAX_SECONDS, max(0.0, (now - started).total_seconds())), 1),
        "done_fraction": round(
            max(_fraction(progress.get("reached")), _prior(progress, started, now), shown), 4
        ),
    }


def _nearest_rank(ordered: Sequence[float], q: float) -> float:
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _instant(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return parsed if parsed is not None and parsed.tzinfo is not None else None


def _prior(progress: Mapping[str, Any], started: datetime, now: datetime) -> float:
    """F of the stored ``prior`` estimate at ``now``, held still by its own stall; 0
    without one, or unless each of its keys is well formed (an old row has none)."""
    prior = progress.get("prior")
    if not isinstance(prior, Mapping) or prior.get("step") not in _TIMED_STEPS:
        return 0.0
    moved, finish = _instant(prior.get("moved_at")), _instant(prior.get("finish_at"))
    after_s, slow_s = as_seconds(prior.get("after_s")), as_seconds(prior.get("slow_after_s"))
    if moved is None or finish is None or after_s is None or slow_s is None:
        return 0.0
    if not 0 <= (finish - moved).total_seconds() <= MAX_SECONDS:
        return 0.0
    estimate = Estimate(moved, finish, 0.0, after_s, slow_s, "low")  # item_s and confidence unused
    return fraction_at(estimate, started_at=started, floored=prior["step"] in FLOORED_STEPS, now=now)


def _fraction(value: object) -> float:
    """A stored ``reached``: a JSON number 0 to 1, else 0 (an old row has none)."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return 0.0
    return float(value) if 0 <= value <= 1 else 0.0


def as_seconds(value: object) -> float | None:
    """A JSON number of seconds, 0 to MAX_SECONDS, or None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and 0 <= value <= MAX_SECONDS else None
