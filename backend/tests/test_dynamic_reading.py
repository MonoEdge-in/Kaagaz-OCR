"""Dynamic reading (feature 26, council 15 Oct 2026, C4, C5; Tushar: the mode is called
Dynamic): which pages are read again with Careful (each trigger and exclusion, worst
first, a cap that does not depend on thread order), the engine's two readings (the memo,
Careful's read replacing the page's, a Careful that fails keeping the first read), the
thinking level and its cache key, and the pipeline: an escalated page is still checked by
RapidOCR, and is never Verified without it.

No network and no database: the Gemini reader with a scripted client that answers by
model (tests/test_gemini_ocr.py's fakes), behind the real meter and readers in the
pipeline rig of tests/test_pipeline_gemini.py."""

import io
import threading
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, ClassVar, cast
from unittest import mock

from PIL import Image

from kaagaz.ai import usage
from kaagaz.ai.openrouter import PageReply
from kaagaz.ai.replies import ModelOutputError, ModelUnavailable
from kaagaz.db.engine import Database
from kaagaz.db.models import Agent, Reading
from kaagaz.db.usage import UsageRow
from kaagaz.pipeline import JobReading
from kaagaz.reading.gemini_ocr import GeminiEngine
from kaagaz.reading.ocr import (
    CROWDED_LINES,
    DYNAMIC_MAX_PAGES,
    READER_PROMPT_VERSION,
    DocumentPages,
    OcrLine,
    PageRead,
    as_page_engine,
    cache_prompt_version,
    careful_pages,
)
from kaagaz.reading.rapid_pool import PixelBox
from tests.pdf_builder import make_pdf
from tests.settings_factory import make_settings
from tests.test_gemini_ocr import (
    CUSTOMER,
    DOCUMENT,
    FakeCache,
    FakeFinder,
    FakeRapidOcr,
    line,
    page,
    reply,
    value,
)
from tests.test_pipeline_gemini import CUSTOMER as JOB_CUSTOMER
from tests.test_pipeline_gemini import SHARP, FakeChecker, rig

STANDARD = "google/gemini-3.8-flash"
CAREFUL = "google/gemini-3.1-pro-preview"
# The rig draws its scans at 72 DPI: 612 x 792 pixels. This box_2d, [ymin, xmin, ymax, xmax]
# in 0..1000, is (61.2, 396, 397.8, 419.8) in pixels, and FOUND is the text finder's box there.
TOTAL_BOX = (500, 100, 530, 650)
FOUND: PixelBox = (60.0, 395.0, 400.0, 421.0)


def read(
    *,
    issues: tuple[str, ...] = (),
    quality: str = "good",
    handwritten: bool = False,
    unsure: int = 0,
    lines: int = 1,
    read_by: str = STANDARD,
) -> PageRead:
    """A page read by ``read_by`` with these marks."""
    words = tuple(f"w{n}" for n in range(unsure))
    return PageRead(
        page_no=0,
        lines=tuple(
            OcrLine(
                f"line {n} " + " ".join(words),
                0,
                n,
                10,
                n + 1,
                None,
                "paragraph",
                unsure=words if n == 0 else (),
            )
            for n in range(lines)
        ),
        quality=quality,
        handwritten=handwritten,
        scripts=("latin",),
        issues=issues,
        read_by=read_by,
    )


class ModelClient:
    """The model client, answering by model: ``answers[model]`` is a reply, an exception
    raised, or a function of the image. Each call is billed as the real client bills it
    (``usage.record_call``) and noted with its model and thinking level."""

    def __init__(
        self, answers: dict[str, PageReply | Exception | Callable[[Image.Image], PageReply]]
    ) -> None:
        self.answers = answers
        self.calls: list[tuple[str | None, str]] = []
        self._lock = threading.Lock()

    def now(self) -> float:
        return 1000.0

    def read_page(self, *, png: bytes, thinking_level: str, model: str | None = None, **_: Any) -> PageReply:
        with self._lock:
            self.calls.append((model, thinking_level))
        answer = self.answers[str(model)]
        usage.record_call(str(model), None, 10, 20, 0, 0.0)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            with Image.open(io.BytesIO(png)) as image:
                return answer(image)
        return answer


def engine(client: ModelClient, *, finder: FakeFinder | None = None, **settings: Any) -> GeminiEngine:
    local = FakeRapidOcr()
    return GeminiEngine(
        make_settings(**settings),
        client=client,
        pool=finder or FakeFinder(),
        local=lambda: as_page_engine(local),
    )


def dynamic_pages(
    memo: dict[tuple[int, str], PageRead], escalate: frozenset[int] = frozenset()
) -> DocumentPages:
    return DocumentPages(
        customer_id=CUSTOMER,
        document_id=DOCUMENT,
        cache=FakeCache(),
        model=STANDARD,
        provider="google-vertex",
        careful=(CAREFUL, "google-vertex"),
        escalate=escalate,
        memo=memo,
    )


class _Ledger:
    """A database whose usage saves are kept in memory."""

    def __init__(self) -> None:
        self.rows: list[UsageRow] = []

    @contextmanager
    def customer(self, customer_id: str) -> Iterator["_Ledger"]:
        yield self


@contextmanager
def ledger() -> Iterator[_Ledger]:
    """A usage scope (kind scan) whose rows are kept in ``rows`` when it ends."""
    kept = _Ledger()

    def insert(conn: _Ledger, rows: list[UsageRow]) -> None:
        conn.rows.extend(rows)

    with (
        mock.patch.object(usage, "insert_usage", insert),
        usage.usage_scope(cast(Database, kept), customer_id=CUSTOMER, user_id=None, kind="scan"),
    ):
        yield kept


# C5: which pages are read again with Careful.


class CarefulPagesTest(unittest.TestCase):
    def test_c5_each_trigger_chooses_the_page_with_its_reason(self) -> None:
        cases = {
            "blurry": read(issues=("blurry",)),
            "faint": read(issues=("faint",)),
            "glare": read(issues=("glare",)),
            "shadow": read(issues=("shadow",)),
            "stamp_over_text": read(issues=("stamp_over_text",)),
            "overwritten": read(issues=("overwritten",)),
            "handwriting_hard": read(issues=("handwriting_hard",)),
            "illegible_words": read(issues=("illegible_words",)),
            "no_text": read(issues=("no_text",), lines=0),
            "handwritten": read(handwritten=True),
            "quality_fair": read(quality="fair"),
            "quality_poor": read(quality="poor"),
            "unsure_words": read(unsure=1),
            "crowded": read(lines=CROWDED_LINES),
        }
        for reason, page_read in cases.items():
            with self.subTest(reason):
                chosen, cap_hit = careful_pages({3: page_read}, lead=STANDARD)
                self.assertEqual((chosen, cap_hit), ([{"page": 3, "reasons": [reason]}], 0))

    def test_c5_never_for_cut_off_low_resolution_tilted_or_a_clean_page(self) -> None:
        for issues in (("cut_off",), ("low_resolution",), ("tilted",), ()):
            with self.subTest(issues):
                self.assertEqual(careful_pages({1: read(issues=issues)}, lead=STANDARD), ([], 0))
        self.assertEqual(careful_pages({1: read(lines=CROWDED_LINES - 1)}, lead=STANDARD), ([], 0))

    def test_c5_never_a_page_the_lead_did_not_read_or_a_page_read_again(self) -> None:
        local = read(issues=("read_locally", "blurry"), read_by="rapidocr")
        self.assertEqual(careful_pages({1: local}, lead=STANDARD), ([], 0))
        self.assertEqual(careful_pages({2: read(quality="poor")}, lead=STANDARD, read_again={2}), ([], 0))

    def test_c5_worst_first_capped_and_the_same_whatever_order_the_pages_were_read_in(self) -> None:
        reads = {n: read(quality="fair") for n in range(1, DYNAMIC_MAX_PAGES + 6)}
        reads[30] = read(quality="poor", handwritten=True, issues=("blurry",))  # three reasons: first
        reads[17] = read(quality="fair", unsure=2)  # two reasons: next
        chosen, cap_hit = careful_pages(reads, lead=STANDARD)
        self.assertEqual([page["page"] for page in chosen[:2]], [30, 17])
        self.assertEqual(len(chosen), DYNAMIC_MAX_PAGES)
        self.assertEqual(cap_hit, len(reads) - DYNAMIC_MAX_PAGES)
        self.assertEqual([page["page"] for page in chosen[2:5]], [1, 2, 3])  # ties in page order
        items = list(reads.items())
        # The orders page threads may finish in: backwards, and odd pages after even ones.
        for order in (items[::-1], items[1::2] + items[::2], items[7:] + items[:7]):
            self.assertEqual(careful_pages(dict(order), lead=STANDARD), (chosen, cap_hit))


# C4: the thinking level and the page cache's key.


class ThinkingTest(unittest.TestCase):
    def test_c4_the_cache_key_names_a_level_other_than_the_envs_and_old_entries_stay_valid(self) -> None:
        self.assertEqual(cache_prompt_version("low", "low"), READER_PROMPT_VERSION)
        self.assertEqual(cache_prompt_version("medium", "low"), f"{READER_PROMPT_VERSION}+medium")

    def test_c4_a_document_is_read_at_its_level_and_cached_under_it(self) -> None:
        client = ModelClient({STANDARD: reply(value(line("INVOICE 1042")))})
        reader = engine(client)
        cache = FakeCache()
        pages = DocumentPages(
            customer_id=CUSTOMER, document_id=DOCUMENT, cache=cache, model=STANDARD, thinking="medium"
        )
        with reader.document(pages):
            reader.read_page(page(), page_no=1)
        self.assertEqual(client.calls, [(STANDARD, "medium")])
        (key,) = cache.puts
        self.assertEqual(key.prompt_version, f"{READER_PROMPT_VERSION}+medium")


# C5: the engine's two readings.


class TwoReadingsTest(unittest.TestCase):
    def test_c5_the_second_reading_calls_no_model_but_careful_on_the_escalated_pages(self) -> None:
        client = ModelClient(
            {
                STANDARD: reply(value(line("Total 1,18O.00"), quality="fair")),
                CAREFUL: reply(value(line("Total 1,180.00")), 30, 70),
            }
        )
        reader, memo = engine(client), cast(dict[tuple[int, str], PageRead], {})
        with ledger() as kept:
            with reader.document(dynamic_pages(memo)):
                first_one = reader.read_page(page(), page_no=1)
                first_two = reader.read_page(page(colour=(250, 250, 250)), page_no=2)
            with reader.document(dynamic_pages(memo, frozenset({1}))):
                second_one = reader.read_page(page(), page_no=1)
                second_two = reader.read_page(page(colour=(250, 250, 250)), page_no=2)
        self.assertEqual(client.calls, [(STANDARD, "low"), (STANDARD, "low"), (CAREFUL, "low")])
        self.assertEqual(second_two, first_two)  # from the memo, no call
        self.assertEqual(second_one.read_by, CAREFUL)
        self.assertEqual([item.text for item in second_one.lines], ["Total 1,180.00"])
        self.assertEqual(
            (second_one.input_tokens, second_one.output_tokens),
            (first_one.input_tokens + 30, first_one.output_tokens + 70),
        )
        kinds = sorted((row.model, row.kind, row.calls) for row in kept.rows)
        self.assertEqual(kinds, [(CAREFUL, "careful", 1), (STANDARD, "scan", 2)])  # never billed as scan

    def test_c5_a_careful_read_that_fails_keeps_the_first_read(self) -> None:
        failures: list[Exception] = [ModelOutputError("invalid_output"), ModelUnavailable("server_error")]
        for failure in failures:
            with self.subTest(type(failure).__name__):
                client = ModelClient(
                    {STANDARD: reply(value(line("Total 1,18O.00"), quality="fair")), CAREFUL: failure}
                )
                reader, memo = engine(client), cast(dict[tuple[int, str], PageRead], {})
                with reader.document(dynamic_pages(memo)):
                    first = reader.read_page(page(), page_no=1)
                with reader.document(dynamic_pages(memo, frozenset({1}))):
                    second = reader.read_page(page(), page_no=1)
                self.assertEqual(second.read_by, STANDARD)
                self.assertEqual(second.lines, first.lines)

    def test_c5_no_second_careful_read(self) -> None:
        client = ModelClient(
            {STANDARD: reply(value(line("Total"), quality="fair")), CAREFUL: reply(value(line("Total")))}
        )
        reader, memo = engine(client), cast(dict[tuple[int, str], PageRead], {})
        with reader.document(dynamic_pages(memo)):
            first = reader.read_page(page(), page_no=1)
        pages = dynamic_pages(memo, frozenset({1}))
        with reader.document(pages):
            reader.read_page(page(), page_no=1)
            again = reader.read_page(page(), page_no=1)  # asked twice: Careful's own page cache
        self.assertEqual([model for model, _level in client.calls], [STANDARD, CAREFUL])
        self.assertTrue(again.cached)
        self.assertEqual(list(memo.values()), [first])  # Careful's read never becomes a first read


# C5 in the pipeline: the checker still reads an escalated page.


def _reading_rig(answers: dict[str, Any], *, checker: FakeChecker | None) -> Any:
    client = ModelClient(answers)
    finder = FakeFinder(FOUND)
    reader = engine(client, finder=finder, ocr_engine="gemini")
    reading = Reading(
        "dynamic",
        Agent(STANDARD, "google-vertex", "Standard"),
        careful=Agent(CAREFUL, "google-vertex", "Careful"),
        thinking="low",
    )
    system = rig(
        reader,
        checker=checker,
        ocr_model=STANDARD,
        job_reading=lambda customer, document: JobReading(None, STANDARD, "google-vertex", reading=reading),
    )
    return system, client


class PipelineTest(unittest.TestCase):
    ANSWERS: ClassVar[dict[str, Any]] = {
        STANDARD: reply(value(line("Total: 1,180.00", TOTAL_BOX, unsure=("1,180.00",)), quality="fair")),
        CAREFUL: reply(value(line("Total: 1,180.00", TOTAL_BOX))),
    }

    def test_c5_an_escalated_page_is_read_by_careful_and_checked_by_rapidocr(self) -> None:
        system, client = _reading_rig(self.ANSWERS, checker=FakeChecker("Total: 1,180.00"))
        job = system.run(make_pdf([SHARP]))
        self.assertEqual([model for model, _level in client.calls], [STANDARD, CAREFUL])
        (page_record,) = system.pages(job)
        self.assertEqual(page_record.read_by, CAREFUL)
        values = system.store.values_for(JOB_CUSTOMER, job.document_id)
        self.assertEqual(
            [(item.value_text, item.label, item.label_reason) for item in values],
            [("1,180.00", "verified", "readers_agree")],
        )
        self.assertEqual(len(system.checker.crops), 1)
        config = system.totals(job).config["reading"]
        self.assertEqual(config["mode"], "dynamic")
        self.assertEqual(config["escalated"], [{"page": 1, "reasons": ["quality_fair", "unsure_words"]}])
        self.assertEqual(config["cap_hit"], 0)

    def test_the_careful_reading_is_its_own_step_with_a_time_left(self) -> None:
        """Progress fix, 16 Oct 2026: the second reading wrote no progress, so the screen
        froze on "Reading page 1 of 1…" while Careful read; now it says what it does."""
        system, _client = _reading_rig(self.ANSWERS, checker=FakeChecker("Total: 1,180.00"))
        system.run(make_pdf([SHARP]))
        steps = [progress.step for _, _, progress in system.progress.history]
        in_turn = [step for i, step in enumerate(steps) if i == 0 or steps[i - 1] != step]
        self.assertEqual(in_turn[-4:], ["reading", "rereading", "understanding", "indexing"])
        careful = [progress for _, _, progress in system.progress.history if progress.step == "rereading"]
        self.assertTrue(careful)
        self.assertEqual({(p.done, p.total) for p in careful}, {(0, 1)})  # one page read again
        self.assertTrue(all(p.estimate is not None for p in careful))

    def test_c5_without_the_checker_an_escalated_page_is_never_verified(self) -> None:
        system, _client = _reading_rig(self.ANSWERS, checker=None)
        job = system.run(make_pdf([SHARP]))
        values = system.store.values_for(JOB_CUSTOMER, job.document_id)
        self.assertEqual(
            [(item.label, item.label_reason) for item in values], [("needs_review", "single_reader")]
        )

    def test_c5_a_clean_page_is_read_once_and_the_document_once(self) -> None:
        answers = {
            STANDARD: reply(value(line("Total: 1,180.00", TOTAL_BOX))),
            CAREFUL: AssertionError("no careful read"),
        }
        system, client = _reading_rig(answers, checker=FakeChecker("Total: 1,180.00"))
        job = system.run(make_pdf([SHARP]))
        self.assertEqual([model for model, _level in client.calls], [STANDARD])
        self.assertEqual(system.totals(job).config["reading"]["escalated"], [])
