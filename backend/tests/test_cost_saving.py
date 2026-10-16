"""Cost saving (Tushar, 16 Oct 2026: "put efforts on words which hard to understand"): which
pages keep their first read, which have only their hard lines read again and which the whole
page (``careful_plan``), where the hard lines are cut out (``crop_boxes``: padding, clamping,
merging), how their new reading goes back into the page (``with_hard_words``: only the hard
lines, boxes kept), and the engine: one call for all of a page's crops, billed as careful, a
call that fails keeping the first read, and the whole page read when the hard lines are many.

No network: the Gemini reader with a scripted client (tests/test_gemini_ocr.py's fakes)."""

import dataclasses
import io
import threading
import unittest
from collections.abc import Callable
from typing import Any, cast

from PIL import Image

from kaagaz.ai import usage
from kaagaz.ai.openrouter import PageReply
from kaagaz.ai.replies import ModelOutputError, ModelUnavailable
from kaagaz.db.models import Agent, Reading
from kaagaz.pipeline import JobReading
from kaagaz.reading.gemini_ocr import (
    CROP_GAP,
    CROPS_SCHEMA,
    PAGE_SCHEMA,
    crop_boxes,
    crop_strip,
    crops_instruction,
    crops_problem,
    with_hard_words,
)
from kaagaz.reading.ocr import (
    HARD_LINES_MAX,
    PATH_PAGE,
    PATH_WORDS,
    OcrLine,
    PageRead,
    careful_pages,
    careful_plan,
    hard_lines,
)
from kaagaz.reading.page_quality import ILLEGIBLE_MARK, ILLEGIBLE_WORDS
from tests.pdf_builder import make_pdf
from tests.test_dynamic_reading import CAREFUL, STANDARD, dynamic_pages, engine, ledger
from tests.test_fast_scan import inline_pages
from tests.test_gemini_ocr import FakeFinder, line, page, reply, value
from tests.test_pipeline_gemini import CUSTOMER as JOB_CUSTOMER
from tests.test_pipeline_gemini import SHARP, FakeChecker, rig

# The page of tests/test_gemini_ocr.py: 1000 x 2000 pixels. Line n's box_2d is
# [100 + 50n, 100, 120 + 50n, 500], so (100, 200 + 100n, 500, 240 + 100n) in pixels: 40 high,
# 60 apart, so the crops of two lines next to each other never meet.
LINES = 10


def pixel_box(n: int) -> tuple[float, float, float, float]:
    return 100.0, 200.0 + 100 * n, 500.0, 240.0 + 100 * n


def first_reply(hard: dict[int, str], **page: Any) -> PageReply:
    """The low read: LINES lines, line n "Line n", but the ``hard`` ones, unsure of their last word."""
    lines = []
    for n in range(LINES):
        text = hard.get(n, f"Line {n}")
        unsure = (text.split()[-1],) if n in hard and ILLEGIBLE_MARK not in text else ()
        lines.append(line(text, (100 + 50 * n, 100, 120 + 50 * n, 500), order=n, unsure=unsure))
    return reply(value(*lines, **page))


def crops_reply(*crops: list[tuple[str, tuple[str, ...]]], tokens: tuple[int, int] = (300, 120)) -> PageReply:
    content = {
        "crops": [
            {"crop": n, "lines": [{"text": text, "unsure": list(unsure)} for text, unsure in lines]}
            for n, lines in enumerate(crops, 1)
        ]
    }
    return PageReply(content, *tokens, "STOP")


Answer = PageReply | Exception | Callable[[dict[str, Any]], PageReply]


class Client:
    """The model client: a page request is answered by its model (``pages``), a crops request
    (CROPS_SCHEMA) by ``crops``; each is billed as the real client bills it and kept."""

    def __init__(self, pages: dict[str, Answer], crops: Answer | None = None) -> None:
        self.pages, self.crops = pages, crops
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def now(self) -> float:
        return 1000.0

    def read_page(self, **request: Any) -> PageReply:
        with self._lock:
            self.requests.append(request)
        usage.record_call(str(request["model"]), None, 10, 20, 0, 0.0)
        answer = self.crops if request["schema"] is CROPS_SCHEMA else self.pages[request["model"]]
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return answer(request)
        assert answer is not None, "no crops request expected"
        return answer

    def calls(self) -> list[tuple[str, str, str]]:
        return [
            (r["model"], r["thinking_level"], "crops" if r["schema"] is CROPS_SCHEMA else "page")
            for r in self.requests
        ]


def reader(client: Client) -> Any:
    finder = FakeFinder(*(pixel_box(n) for n in range(LINES)))
    return engine(cast(Any, client), finder=finder, careful_early_max_pages=0)


def page_read(texts: list[str], *, unsure: dict[int, tuple[str, ...]], source: str = "found") -> PageRead:
    """A low read with these lines (line n at ``pixel_box(n)``) and unsure words."""
    lines = tuple(
        OcrLine(text, *pixel_box(n), None, "paragraph", unsure=unsure.get(n, ()), box_source=source)
        for n, text in enumerate(texts)
    )
    return PageRead(
        page_no=1,
        lines=lines,
        quality="good",
        handwritten=False,
        scripts=("latin",),
        issues=(),
        read_by=STANDARD,
    )


def hard_read(count: int, total: int, **kwargs: Any) -> PageRead:
    """``total`` lines, the first ``count`` of them with an unsure word."""
    return page_read(
        [f"Line {n}" for n in range(total)], unsure=dict.fromkeys(range(count), ("Line",)), **kwargs
    )


# Which way a page is read again (careful_plan).


class PlanTest(unittest.TestCase):
    def test_a_page_with_no_hard_line_and_no_page_wide_problem_keeps_its_first_read(self) -> None:
        clear = dataclasses.replace(hard_read(0, 5), quality="fair")
        self.assertEqual(careful_plan(clear, lead=STANDARD), [])
        self.assertEqual(careful_pages({1: clear}, lead=STANDARD), ([], 0))

    def test_a_handwritten_page_is_read_whole_even_when_every_word_was_sure(self) -> None:
        # Tushar's accuracy first (16 Oct 2026): a quick read of handwriting can be sure and wrong.
        written = dataclasses.replace(hard_read(0, 5), handwritten=True)
        self.assertEqual(careful_plan(written, lead=STANDARD), ["handwritten", PATH_PAGE])

    def test_a_few_pinned_hard_lines_are_read_as_words(self) -> None:
        self.assertEqual(careful_plan(hard_read(2, 10), lead=STANDARD), ["unsure_words", PATH_WORDS])
        self.assertEqual(careful_plan(hard_read(4, 10), lead=STANDARD)[-1], PATH_WORDS)  # 40%: still few

    def test_over_the_share_or_the_count_the_whole_page_is_read(self) -> None:
        self.assertEqual(careful_plan(hard_read(5, 10), lead=STANDARD)[-1], PATH_PAGE)  # 50% of the lines
        self.assertEqual(careful_plan(hard_read(HARD_LINES_MAX, 40), lead=STANDARD)[-1], PATH_WORDS)
        self.assertEqual(careful_plan(hard_read(HARD_LINES_MAX + 1, 40), lead=STANDARD)[-1], PATH_PAGE)

    def test_an_unpinned_hard_line_or_a_page_wide_problem_reads_the_whole_page(self) -> None:
        self.assertEqual(careful_plan(hard_read(1, 10, source="model"), lead=STANDARD)[-1], PATH_PAGE)
        blurry = dataclasses.replace(hard_read(1, 10), issues=("blurry",))
        self.assertEqual(careful_plan(blurry, lead=STANDARD), ["blurry", "unsure_words", PATH_PAGE])
        poor = dataclasses.replace(hard_read(0, 10), quality="poor")
        self.assertEqual(careful_plan(poor, lead=STANDARD), ["quality_poor", PATH_PAGE])

    def test_a_line_with_an_illegible_part_is_hard(self) -> None:
        read = page_read(["Line 0", f"Total {ILLEGIBLE_MARK}", "Line 2"], unsure={2: ("Line",)})
        self.assertEqual(hard_lines(read), [1, 2])

    def test_never_a_page_the_lead_did_not_read_or_one_read_again(self) -> None:
        self.assertEqual(
            careful_plan(dataclasses.replace(hard_read(1, 10), read_by="rapidocr"), lead=STANDARD), []
        )
        self.assertEqual(careful_plan(hard_read(1, 10), lead=STANDARD, again=True), [])


# Where the hard lines are cut out (crop_boxes).


def ocr_line(x0: float, y0: float, x1: float, y1: float, text: str = "x") -> OcrLine:
    return OcrLine(text, x0, y0, x1, y1, None, "paragraph", box_source="found")


class CropTest(unittest.TestCase):
    def test_each_side_is_padded_by_half_the_lines_height(self) -> None:
        lines = [ocr_line(100, 200, 300, 220)]
        self.assertEqual(crop_boxes(lines, [0], 1000, 1000), [((90.0, 190.0, 310.0, 230.0), (0,))])

    def test_a_crop_stays_inside_the_page(self) -> None:
        lines = [ocr_line(2, 3, 50, 23), ocr_line(900, 960, 995, 990)]
        self.assertEqual(
            crop_boxes(lines, [0, 1], 1000, 1000),
            [((0.0, 0.0, 60.0, 33.0), (0,)), ((885.0, 945.0, 1000.0, 1000.0), (1,))],
        )

    def test_overlapping_crops_are_merged_until_none_overlap(self) -> None:
        # 0 and 2 meet only through 1's crop; 3 sits beside them, far to the right, on its own.
        lines = [
            ocr_line(100, 100, 300, 120),
            ocr_line(100, 125, 300, 145),
            ocr_line(100, 150, 300, 170),
            ocr_line(700, 100, 900, 120),
        ]
        self.assertEqual(
            crop_boxes(lines, [0, 1, 2, 3], 1000, 1000),
            [((90.0, 90.0, 310.0, 180.0), (0, 1, 2)), ((690.0, 90.0, 910.0, 130.0), (3,))],
        )
        self.assertEqual(len(crop_boxes(lines, [0, 2], 1000, 1000)), 2)  # without 1 they never meet
        self.assertEqual(crop_boxes(lines, [2, 0, 1], 1000, 1000)[0][1], (0, 1, 2))  # in any order
        self.assertEqual(  # 0 and 1 meet with 3 between them in the list
            crop_boxes(lines, [0, 3, 1], 1000, 1000),
            [((90.0, 90.0, 310.0, 155.0), (0, 1)), ((690.0, 90.0, 910.0, 130.0), (3,))],
        )

    def test_a_crop_holds_every_line_whose_middle_is_in_it(self) -> None:
        lines = [ocr_line(100, 100, 400, 120), ocr_line(320, 104, 360, 116)]  # 1: a small mark on 0
        self.assertEqual(crop_boxes(lines, [0], 1000, 1000), [((90.0, 90.0, 410.0, 130.0), (0, 1))])

    def test_crops_far_apart_stay_apart_top_to_bottom(self) -> None:
        lines = [ocr_line(100, 500, 300, 520), ocr_line(100, 100, 300, 120)]
        self.assertEqual([members for _box, members in crop_boxes(lines, [0, 1], 1000, 1000)], [(1,), (0,)])

    def test_the_strip_stacks_the_crops_with_a_gap_between(self) -> None:
        with crop_strip(page(), [(100, 180, 540, 260), (100, 380.5, 540, 460)]) as strip:
            self.assertEqual(strip.size, (440, 80 + 80 + CROP_GAP))  # 380.5 is cut from row 380
            self.assertEqual(strip.getpixel((5, 80 + CROP_GAP // 2)), (64, 64, 64))  # the rule
            self.assertEqual(strip.getpixel((5, 5)), (255, 255, 255))

    def test_the_instruction_says_each_strips_lines_and_the_check_holds_to_them(self) -> None:
        self.assertIn("Strip 1 holds 1 line. Strip 2 holds 3 lines.", crops_instruction([1, 3]))
        check = crops_problem([1, 2])
        good = crops_reply([("a", ())], [("b", ()), ("c", ())]).value
        self.assertIsNone(check(good))
        self.assertIsNotNone(check(crops_reply([("a", ())], [("b", ())]).value))  # a line short
        self.assertIsNotNone(check(crops_reply([("b", ()), ("c", ())], [("a", ())]).value))
        self.assertIsNotNone(check({"crops": [{"crop": 2, "lines": [{}]}, {"crop": 1, "lines": [{}, {}]}]}))


# The new reading back into the page (with_hard_words).


class MergeTest(unittest.TestCase):
    def test_only_the_hard_lines_take_the_new_reading_and_keep_their_boxes(self) -> None:
        first = page_read(["Invoice 17", f"Total 1,18{ILLEGIBLE_MARK}.00", "Paid"], unsure={1: ("Total",)})
        first = dataclasses.replace(first, issues=(ILLEGIBLE_WORDS,), input_tokens=100, output_tokens=50)
        # One crop around line 1 that also holds line 0 and line 2 whole (read, never used).
        crops = [((0.0, 0.0, 1000.0, 2000.0), (0, 1, 2))]
        reading = crops_reply([("Invoice 71", ()), ("Total 1,180.00", ("1,180.00",)), ("Pa1d", ())]).value
        merged = with_hard_words(first, crops, reading, (300, 120))
        self.assertEqual([item.text for item in merged.lines], ["Invoice 17", "Total 1,180.00", "Paid"])
        self.assertEqual(merged.lines[1].unsure, ("1,180.00",))
        self.assertEqual(
            [(item.x0, item.y0, item.x1, item.y1, item.box_source) for item in merged.lines],
            [(item.x0, item.y0, item.x1, item.y1, item.box_source) for item in first.lines],
        )
        self.assertEqual(merged.issues, ())  # no [?] left
        self.assertEqual((merged.input_tokens, merged.output_tokens), (400, 170))
        self.assertEqual(merged.read_by, STANDARD)

    def test_a_line_read_empty_or_with_bad_characters_keeps_its_first_reading(self) -> None:
        first = page_read(["Total 1,18O.00", "Date 12/1O"], unsure={0: ("1,18O.00",), 1: ("12/1O",)})
        crops = [((0.0, 0.0, 1000.0, 1000.0), (0,)), ((0.0, 1000.0, 1000.0, 2000.0), (1,))]
        reading = crops_reply([("  ", ())], [("Date \ud800", ())]).value
        self.assertEqual(with_hard_words(first, crops, reading, (0, 0)).lines, first.lines)

    def test_a_new_illegible_part_is_counted(self) -> None:
        first = page_read(["Total 1,18O.00"], unsure={0: ("1,18O.00",)})
        crops = [((0.0, 0.0, 1000.0, 1000.0), (0,))]
        merged = with_hard_words(first, crops, crops_reply([(f"Total {ILLEGIBLE_MARK}", ())]).value, (0, 0))
        self.assertEqual(merged.issues, (ILLEGIBLE_WORDS,))


# The engine.


HARD = {2: "Total 1,18O.00", 5: "Date 12/1O/2026", 8: "GSTIN 27AAAC0"}
WORDS = crops_reply([("Total 1,180.00", ())], [("Date 12/10/2026", ())], [("GSTIN 27AAACO", ("27AAACO",))])


class EngineTest(unittest.TestCase):
    def test_a_few_hard_lines_are_read_in_one_careful_call_and_only_they_change(self) -> None:
        client = Client({STANDARD: first_reply(HARD)}, crops=WORDS)
        one = reader(client)
        with ledger() as kept, one.document(inline_pages(3)):
            read = one.read_page(page(), page_no=1)
        self.assertEqual(client.calls(), [(STANDARD, "low", "page"), (CAREFUL, "high", "crops")])
        texts = [item.text for item in read.lines]
        self.assertEqual([texts[n] for n in HARD], ["Total 1,180.00", "Date 12/10/2026", "GSTIN 27AAACO"])
        clear = [n for n in range(LINES) if n not in HARD]
        self.assertEqual([texts[n] for n in clear], [f"Line {n}" for n in clear])
        self.assertEqual([read.lines[n].unsure for n in HARD], [(), (), ("27AAACO",)])
        boxes = [(item.y0, item.y1, item.box_source) for item in read.lines]
        self.assertEqual(boxes, [(200.0 + 100 * n, 240.0 + 100 * n, "found") for n in range(LINES)])
        self.assertEqual(read.read_by, STANDARD)
        self.assertEqual((read.input_tokens, read.output_tokens), (1700 + 300, 4000 + 120))
        kinds = sorted((row.model, row.kind, row.calls) for row in kept.rows)
        self.assertEqual(kinds, [(CAREFUL, "careful", 1), (STANDARD, "scan", 1)])

    def test_the_crops_call_sends_one_picture_of_the_three_crops(self) -> None:
        client = Client({STANDARD: first_reply(HARD)}, crops=WORDS)
        one = reader(client)
        pages = inline_pages(3)
        with one.document(pages):
            one.read_page(page(), page_no=1)
        (crops,) = [r for r in client.requests if r["schema"] is CROPS_SCHEMA]
        with Image.open(io.BytesIO(crops["png"])) as strip:
            self.assertEqual(strip.size, (440, 3 * 80 + 2 * CROP_GAP))  # each 40 + 2 x 20 high
        self.assertIn("Strip 3 holds 1 line.", crops["instruction"])
        self.assertIsNone(crops["validate"](WORDS.value))
        assert pages.escalated is not None
        self.assertEqual(pages.escalated, {1: ["unsure_words", PATH_WORDS]})

    def test_a_crops_call_that_fails_or_answers_wrong_keeps_the_first_read(self) -> None:
        failures: list[Answer] = [
            ModelUnavailable("timeout", input_tokens=0, output_tokens=0),
            ModelUnavailable("server_error"),
            ModelOutputError("invalid_output", input_tokens=300, output_tokens=50),
            crops_reply([("Total 1,180.00", ())], [("Date 12/10/2026", ())]),  # a strip short
        ]
        for failure in failures:
            with self.subTest(repr(failure)):
                client = Client({STANDARD: first_reply(HARD)}, crops=failure)
                low = Client({STANDARD: first_reply(HARD)})
                plain = reader(low)
                with plain.document(dataclasses.replace(inline_pages(3), careful=None)):
                    expected = plain.read_page(page(), page_no=1)
                one = reader(client)
                with one.document(inline_pages(3)):
                    read = one.read_page(page(), page_no=1)
                self.assertEqual(read.lines, expected.lines)
                self.assertEqual(read.issues, expected.issues)
                self.assertEqual([kind for _m, _l, kind in client.calls()], ["page", "crops"])

    def test_many_hard_lines_read_the_whole_page_as_before(self) -> None:
        many = {n: f"Word {n}x" for n in range(5)}  # 5 of 10: over HARD_SHARE
        careful = reply(
            value(
                *(line(f"Careful {n}", (100 + 50 * n, 100, 120 + 50 * n, 500), order=n) for n in range(LINES))
            )
        )
        client = Client({STANDARD: first_reply(many), CAREFUL: careful})
        one = reader(client)
        pages = inline_pages(3)
        with one.document(pages):
            read = one.read_page(page(), page_no=1)
        self.assertEqual(client.calls(), [(STANDARD, "low", "page"), (CAREFUL, "high", "page")])
        self.assertIs(client.requests[1]["schema"], PAGE_SCHEMA)
        self.assertEqual(read.read_by, CAREFUL)
        self.assertEqual(pages.escalated, {1: ["unsure_words", PATH_PAGE]})

    def test_a_clear_page_makes_no_second_call(self) -> None:
        client = Client({STANDARD: first_reply({}, quality="fair")})
        one = reader(client)
        pages = inline_pages(3)
        with one.document(pages):
            one.read_page(page(), page_no=1)
        self.assertEqual(client.calls(), [(STANDARD, "low", "page")])
        self.assertEqual(pages.escalated, {})

    def test_a_long_documents_second_reading_reads_the_words_too(self) -> None:
        client = Client({STANDARD: first_reply(HARD)}, crops=WORDS)
        one, memo = reader(client), cast(dict[tuple[int, str], PageRead], {})
        with one.document(dynamic_pages(memo)):
            first = one.read_page(page(), page_no=1)
        chosen, _cap = careful_pages({1: first}, lead=STANDARD)
        self.assertEqual(chosen, [{"page": 1, "reasons": ["unsure_words", PATH_WORDS]}])
        with one.document(dynamic_pages(memo, frozenset({1}))):
            second = one.read_page(page(), page_no=1)
        self.assertEqual([kind for _m, _l, kind in client.calls()], ["page", "crops"])
        self.assertEqual(second.lines[2].text, "Total 1,180.00")


# Through the pipeline: the rig draws its scans at 72 DPI, 612 x 792 pixels.


class PipelineTest(unittest.TestCase):
    def test_the_words_reading_is_saved_and_noted_on_the_run(self) -> None:
        def box(n: int) -> tuple[float, float, float, float]:  # box_2d of line n
            return 100 + 100 * n, 100, 130 + 100 * n, 650

        def found(n: int) -> tuple[float, float, float, float]:  # the same box in pixels
            y0, x0, y1, x1 = box(n)
            return x0 * 0.612, y0 * 0.792, x1 * 0.612, y1 * 0.792

        texts = ["Invoice No: INV-17", "Sharma Traders", "Total: 1,18O.00", "Pune", "Thank you"]
        lines = [
            line(text, box(n), order=n, unsure=("1,18O.00",) if n == 2 else ())
            for n, text in enumerate(texts)
        ]
        client = Client({STANDARD: reply(value(*lines))}, crops=crops_reply([("Total: 1,180.00", ())]))
        reading = Reading(
            "dynamic",
            Agent(STANDARD, "google-vertex", "Standard"),
            careful=Agent(CAREFUL, "google-vertex", "Careful"),
            thinking="low",
        )
        system = rig(
            engine(
                cast(Any, client),
                finder=FakeFinder(*map(found, range(5))),
                ocr_engine="gemini",
                careful_early_max_pages=0,
            ),
            checker=FakeChecker("Total: 1,180.00"),
            ocr_model=STANDARD,
            job_reading=lambda customer, document: JobReading(
                None, STANDARD, "google-vertex", reading=reading
            ),
        )
        job = system.run(make_pdf([SHARP]))
        self.assertEqual(client.calls(), [(STANDARD, "low", "page"), (CAREFUL, "high", "crops")])
        (page_record,) = system.pages(job)
        self.assertEqual((page_record.read_by, page_record.unsure_words), (STANDARD, 0))
        values = system.store.values_for(JOB_CUSTOMER, job.document_id)
        self.assertIn(("1,180.00", "verified"), [(item.value_text, item.label) for item in values])
        config = system.totals(job).config["reading"]
        # Changed on purpose (16 Oct 2026): the entries carry tokens too (test_cost_saving_tuning).
        escalated = [{key: item[key] for key in ("page", "reasons")} for item in config["escalated"]]
        self.assertEqual(escalated, [{"page": 1, "reasons": ["unsure_words", PATH_WORDS]}])
