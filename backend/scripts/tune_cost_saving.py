"""Tune Cost saving's limits on real scans (16 Oct 2026; docs/architecture.md, "Measure and tune
Cost saving on the server").

    python -m scripts.tune_cost_saving SCANS_DIR [--lines-max 6,12,20] [--shares 0.25,0.4,0.6]
        [--tolerance 0] [--lead MODEL] [--careful MODEL] [--provider NAME]

Every file in SCANS_DIR is read the way the worker reads it (``build_reader_socket``); each page
that goes to the scan reader (a PDF page with a good text layer does not) is read at most three
times, whatever the grid: its first read at low thinking (Cost saving's), its whole-page Careful
read at OCR_RETRY_THINKING_LEVEL (the reference: a full careful read, and the read a page sent
the whole-page way gets), and, when any setting sends it the words way, its hard lines cut out
and read in one call (the crops do not depend on the grid; COST_SAVING_CROP_PAD from the
environment sets their padding). Each setting (COST_SAVING_HARD_LINES_MAX x
COST_SAVING_HARD_SHARE) then routes every page with ``careful_plan`` and is scored against
the reference: calls (one per read), tokens, word agreement and the values (words with a digit:
amounts, dates, numbers) that differ. Rows for the reference itself and the old rule (every page
with a reason read whole) come first. The recommendation is the cheapest setting with at most
``--tolerance`` differing values (0: none differs); exit 1 when no setting is within it.

It calls real models through OpenRouter, so it costs money: run it by hand, on the server, in
the worker's image with the stack's settings. It refuses to start without OPENROUTER_API_KEY
and never prints it. It prints no page text: file names, page numbers and numbers only.
"""

import argparse
import dataclasses
import difflib
import functools
import itertools
import os
import sys
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from kaagaz.reading.ocr import PATH_WORDS, PageRead, careful_plan, careful_reasons, values_of

LINES_MAX = (6, 12, 20)
SHARES = (0.25, 0.4, 0.6)
FIRST_THINKING = "low"  # Cost saving's first read (``db.models.resolve_scan``)


@dataclass(frozen=True, slots=True)
class Page:
    """One page the scan reader read: its first read, the reference (Careful's whole-page
    read) and the first read with its hard lines read again as crops (None: not asked)."""

    name: str
    first: PageRead
    reference: PageRead
    words: PageRead | None = None


@dataclass(slots=True)
class Score:
    setting: str
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    words_same: int = 0
    words_all: int = 0
    values_differ: int = 0

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    @property
    def agreement(self) -> float:
        """The share of words that agree with the reference (1.0 with no words at all)."""
        return 1.0 if self.words_all == 0 else self.words_same / self.words_all


def grid(lines_max: Iterable[int], shares: Iterable[float]) -> list[tuple[int, float]]:
    """Every (hard lines max, hard share) pair, in order."""
    return list(itertools.product(lines_max, shares))


def words_of(read: PageRead) -> list[str]:
    return [word for line in read.lines for word in line.text.split()]


def compare(read: PageRead, reference: PageRead) -> tuple[int, int, int]:
    """(words that agree, words in all: the longer of the two, values that differ) for one
    page. A value read differently counts once: the larger of what each has that the other
    has not. O(w²) at worst for w words (difflib)."""
    a, b = words_of(read), words_of(reference)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    same = sum(block.size for block in matcher.get_matching_blocks())
    mine, theirs = values_of(a), values_of(b)
    differ = max(sum((mine - theirs).values()), sum((theirs - mine).values()))
    return same, max(len(a), len(b)), differ


def whole(page: Page) -> PageRead:
    """The page read whole again: the reference, billed with its first read too."""
    return dataclasses.replace(
        page.reference,
        input_tokens=page.first.input_tokens + page.reference.input_tokens,
        output_tokens=page.first.output_tokens + page.reference.output_tokens,
    )


def route(page: Page, lead: str, lines_max: int, share: float) -> tuple[PageRead, int]:
    """(the read the page ends with, its calls) under one setting, as the reader routes it
    (``careful_plan``): its first read, its hard lines as crops, or the whole page again."""
    plan = careful_plan(page.first, lead=lead, lines_max=lines_max, share=share)
    if not plan:
        return page.first, 1
    if plan[-1] == PATH_WORDS and page.words is not None:
        return page.words, 2
    return whole(page), 2


def old_rule(page: Page, lead: str) -> tuple[PageRead, int]:
    """Before Cost saving: a page with any reason is read whole again."""
    return (whole(page), 2) if careful_reasons(page.first, lead=lead) else (page.first, 1)


def score(name: str, pages: Iterable[Page], decide: Callable[[Page], tuple[PageRead, int]]) -> Score:
    """One setting's calls, tokens and agreement with the reference over ``pages``."""
    total = Score(name)
    for page in pages:
        read, calls = decide(page)
        same, words, differ = compare(read, page.reference)
        total.calls += calls
        total.tokens_in += read.input_tokens
        total.tokens_out += read.output_tokens
        total.words_same += same
        total.words_all += words
        total.values_differ += differ
    return total


def score_all(
    pages: Sequence[Page], lead: str, settings: Sequence[tuple[int, float]]
) -> tuple[list[Score], list[Score]]:
    """(the reference and old-rule rows, one row per setting)."""
    baseline = [
        score("reference (all whole, High)", pages, lambda page: (page.reference, 1)),
        score("old rule (reasons: whole)", pages, lambda page: old_rule(page, lead)),
    ]
    rows = [
        score(f"lines_max={m} share={s}", pages, functools.partial(route, lead=lead, lines_max=m, share=s))
        for m, s in settings
    ]
    return baseline, rows


def recommend(rows: Sequence[Score], tolerance: int = 0) -> Score | None:
    """The cheapest row (tokens, then calls) with at most ``tolerance`` values that differ
    from the reference; None when no row is within it."""
    within = [row for row in rows if row.values_differ <= tolerance]
    return min(within, key=lambda row: (row.tokens, row.calls), default=None)


def report(baseline: Sequence[Score], rows: Sequence[Score], tolerance: int, pages: int) -> str:
    lines = [
        f"pages {pages} (each read low first, and whole at High as the reference)",
        f"{'setting':<30}{'calls':>7}{'tokens_in':>11}{'tokens_out':>11}{'agreement':>11}"
        f"{'values_differ':>15}",
    ]
    for row in [*baseline, *rows]:
        lines.append(
            f"{row.setting:<30}{row.calls:>7}{row.tokens_in:>11}{row.tokens_out:>11}"
            f"{row.agreement:>11.1%}{row.values_differ:>15}"
        )
    best = recommend(rows, tolerance)
    if best is not None:
        lines.append(f"recommend: {best.setting} (the cheapest with at most {tolerance} differing values)")
        return "\n".join(lines)
    closest = min(rows, key=lambda row: (row.values_differ, row.tokens), default=None)
    lines.append(
        f"recommend: none within {tolerance} differing values"
        + ("" if closest is None else f"; the closest is {closest.setting} ({closest.values_differ})")
    )
    return "\n".join(lines)


# --- the live part: real models, on the server ---------------------------------------------


class _NoCache:
    """No page cache: every read is a real call, so its tokens are real."""

    def get(self, key: Any) -> None:
        return None

    def put(self, key: Any, result: Any, input_tokens: int, output_tokens: int) -> None:
        return None


class _Recorder:
    """The engine, keeping each page's picture and read (page threads call it at once)."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._lock = threading.Lock()
        self.pages: dict[int, tuple[Any, PageRead]] = {}

    @property
    def name(self) -> str:
        return str(self._engine.name)

    @property
    def version(self) -> str:
        return str(self._engine.version)

    def read(self, image: Any) -> Any:
        return self._engine.read(image)

    def read_page(self, image: Any, *, page_no: int) -> PageRead:
        read: PageRead = self._engine.read_page(image, page_no=page_no)
        with self._lock:
            self.pages[page_no] = (image.copy(), read)  # a page read twice keeps its last read
        return read


def live_pages(
    args: argparse.Namespace, settings_grid: Sequence[tuple[int, float]], out: TextIO
) -> tuple[list[Page], str]:
    """(each scan page's reads, see the module notes; the lead model)."""
    from kaagaz.ingestion import sniff
    from kaagaz.ingestion.errors import BackendUnavailable
    from kaagaz.ingestion.spool import HEAD_SIZE
    from kaagaz.reading.gemini_ocr import GeminiEngine
    from kaagaz.reading.ocr import DocumentPages
    from kaagaz.reading.rapid_pool import shared_pool
    from kaagaz.reading.spans import ReadError
    from kaagaz.reading.wiring import build_reader_socket
    from kaagaz.settings import DEFAULT_PROVIDER, Settings

    settings = Settings.from_env(os.environ)
    lead = args.lead or settings.ocr_model
    careful = args.careful or lead
    provider = args.provider or DEFAULT_PROVIDER
    engine = GeminiEngine.from_settings(settings, pool=shared_pool(settings.cpu_workers))

    def document(name: str, model: str, thinking: str, *, careful_read: bool = False) -> Any:
        pages = DocumentPages(
            customer_id="tune",
            document_id=name,
            cache=_NoCache(),
            model=model,
            provider=provider,
            thinking=thinking,
            careful_read=careful_read,
        )
        return engine.document(pages)

    files = sorted(path for path in Path(args.scans).iterdir() if path.is_file())
    if not files:
        raise SystemExit(f"no files in {args.scans}")
    pages: list[Page] = []
    for path in files:
        data = path.read_bytes()
        detected = sniff.detect_type(data[:HEAD_SIZE])
        if detected is None:
            out.write(f"skipped {path.name}: not a kind of file the worker reads\n")
            continue
        recorder = _Recorder(engine)
        try:
            with document(path.name, lead, FIRST_THINKING):
                build_reader_socket(settings, recorder).read(detected, data)
        except ReadError as error:
            out.write(f"skipped {path.name}: it cannot be read ({error.code})\n")
            continue
        for page_no, (image, first) in sorted(recorder.pages.items()):
            name = f"{path.name} page {page_no}"
            try:
                with document(path.name, careful, settings.ocr_retry_thinking_level, careful_read=True):
                    reference = engine.read_page(image, page_no=page_no)
            except BackendUnavailable:
                reference = None
            if reference is None or reference.read_by != careful or first.read_by != lead:
                out.write(f"skipped {name}: the model did not read it (an outage or the local reader)\n")
                continue
            plans = [careful_plan(first, lead=lead, lines_max=m, share=s) for m, s in settings_grid]
            words = None
            if any(plan[-1:] == [PATH_WORDS] for plan in plans):
                # The reader's own crops call, at COST_SAVING_CROP_PAD.
                words = engine._careful_words(image, first, None, (careful, provider))
            pages.append(Page(name, first, reference, words))
            out.write(f"read {name}\n")
    return pages, lead


def numbers(text: str, kind: Callable[[str], Any]) -> list[Any]:
    return [kind(part) for part in text.split(",") if part.strip()]


def main(
    argv: Sequence[str] | None = None, out: TextIO = sys.stdout, environ: Mapping[str, str] | None = None
) -> int:
    parser = argparse.ArgumentParser(description="Tune Cost saving's limits on real scans")
    parser.add_argument("scans", help="a folder of real scans (PDFs and images)")
    parser.add_argument(
        "--lines-max", default=",".join(map(str, LINES_MAX)), help="COST_SAVING_HARD_LINES_MAX to try"
    )
    parser.add_argument("--shares", default=",".join(map(str, SHARES)), help="COST_SAVING_HARD_SHARE to try")
    parser.add_argument("--tolerance", type=int, default=0, help="values that may differ from the reference")
    parser.add_argument("--lead", help="the first read's model (default OCR_MODEL)")
    parser.add_argument("--careful", help="Careful's model (default the lead model itself)")
    parser.add_argument("--provider", help="the OpenRouter provider (default the default provider)")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    if not (env.get("OPENROUTER_API_KEY") or "").strip():
        raise SystemExit(
            "OPENROUTER_API_KEY is not set: this calls real models, so run it on the server inside"
            " the worker's image with the stack's settings (docs/architecture.md, Cost saving runbook)"
        )
    try:
        settings_grid = grid(numbers(args.lines_max, int), numbers(args.shares, float))
    except ValueError:
        parser.error("--lines-max takes whole numbers and --shares numbers, comma separated")
    if not settings_grid:
        parser.error("the grid is empty")
    pages, lead = live_pages(args, settings_grid, out)
    baseline, rows = score_all(pages, lead, settings_grid)
    out.write(report(baseline, rows, args.tolerance, len(pages)) + "\n")
    return 0 if recommend(rows, args.tolerance) is not None else 1


if __name__ == "__main__":
    sys.exit(main())
