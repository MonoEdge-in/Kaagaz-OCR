"""Cost saving tuning (Tushar, 16 Oct 2026: "its token savings aren't measured on real scans yet,
and its limits need tuning"): the limits are settings (COST_SAVING_*), passed down to
``careful_plan``, ``careful_pages`` and ``crop_boxes`` and honoured by the reader; each escalated
page's tokens and the pages kept quick are noted on the run; ``cli cost-saving`` reports what
was measured and what is estimated; scripts/tune_cost_saving.py's pure parts (grid,
comparison, routing, recommendation) and its refusal to run without a key.

No network: tests/test_cost_saving.py's scripted client and fakes."""

import dataclasses
import io
import unittest
from typing import Any, cast

import pytest
from PIL import Image

from kaagaz import cli
from kaagaz.db.models import Agent, Reading
from kaagaz.pipeline import JobReading
from kaagaz.reading.gemini_ocr import CROP_GAP, CROPS_SCHEMA, crop_boxes
from kaagaz.reading.ocr import PATH_PAGE, PATH_WORDS, PageRead, careful_pages, careful_plan
from kaagaz.reading.wiring import reader_config
from kaagaz.settings import Settings, SettingsError
from scripts import tune_cost_saving as tune
from tests.pdf_builder import make_pdf
from tests.test_cost_saving import (
    HARD,
    LINES,
    WORDS,
    Client,
    crops_reply,
    first_reply,
    hard_read,
    page_read,
    pixel_box,
)
from tests.test_dynamic_reading import CAREFUL, STANDARD, engine
from tests.test_fast_scan import inline_pages
from tests.test_gemini_ocr import FakeFinder, line, page, reply, value
from tests.test_pipeline_gemini import SHARP, FakeChecker, rig
from tests.test_settings import shipped

LIMITS = ("COST_SAVING_HARD_LINES_MAX", "COST_SAVING_HARD_SHARE", "COST_SAVING_CROP_PAD")


# The settings.


def test_the_limits_default_to_the_old_constants_when_unset() -> None:
    settings = Settings.from_env(shipped(**dict.fromkeys(LIMITS)))
    assert (settings.cost_saving_hard_lines_max, settings.cost_saving_hard_share) == (12, 0.4)
    assert settings.cost_saving_crop_pad == 0.5
    shipped_values = Settings.from_env(shipped())
    assert (shipped_values.cost_saving_hard_lines_max, shipped_values.cost_saving_hard_share) == (12, 0.4)
    assert shipped_values.cost_saving_crop_pad == 0.5


def test_the_limits_are_read_from_the_environment() -> None:
    env = shipped(COST_SAVING_HARD_LINES_MAX="20", COST_SAVING_HARD_SHARE="0.25", COST_SAVING_CROP_PAD="1.5")
    settings = Settings.from_env(env)
    assert settings.cost_saving_hard_lines_max == 20
    assert settings.cost_saving_hard_share == 0.25
    assert settings.cost_saving_crop_pad == 1.5
    config = reader_config(settings)  # each run notes the limits it was read with
    assert (config["cost_saving_hard_lines_max"], config["cost_saving_hard_share"]) == (20, 0.25)
    assert config["cost_saving_crop_pad"] == 1.5


@pytest.mark.parametrize(
    ("name", "bad"),
    [
        ("COST_SAVING_HARD_LINES_MAX", "0"),
        ("COST_SAVING_HARD_LINES_MAX", "61"),
        ("COST_SAVING_HARD_LINES_MAX", "12.5"),
        ("COST_SAVING_HARD_SHARE", "0.04"),
        ("COST_SAVING_HARD_SHARE", "1.5"),
        ("COST_SAVING_HARD_SHARE", "nan"),
        ("COST_SAVING_CROP_PAD", "0.05"),
        ("COST_SAVING_CROP_PAD", "2.5"),
        ("COST_SAVING_CROP_PAD", "wide"),
    ],
)
def test_a_limit_out_of_its_range_stops_start_up_by_name(name: str, bad: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: bad}))


@pytest.mark.parametrize(
    ("name", "edge"),
    [
        ("COST_SAVING_HARD_LINES_MAX", "1"),
        ("COST_SAVING_HARD_LINES_MAX", "60"),
        ("COST_SAVING_HARD_SHARE", "0.05"),
        ("COST_SAVING_HARD_SHARE", "1.0"),
        ("COST_SAVING_CROP_PAD", "0.1"),
        ("COST_SAVING_CROP_PAD", "2.0"),
    ],
)
def test_the_ends_of_each_range_are_allowed(name: str, edge: str) -> None:
    Settings.from_env(shipped(**{name: edge}))


# The pure functions take the limits.


class LimitsTest(unittest.TestCase):
    def test_careful_plan_and_careful_pages_honour_the_limits_passed(self) -> None:
        three = hard_read(3, 10)
        self.assertEqual(careful_plan(three, lead=STANDARD)[-1], PATH_WORDS)  # the defaults
        self.assertEqual(careful_plan(three, lead=STANDARD, lines_max=2)[-1], PATH_PAGE)
        self.assertEqual(careful_plan(three, lead=STANDARD, share=0.25)[-1], PATH_PAGE)  # 3 > 2.5
        self.assertEqual(careful_plan(hard_read(20, 60), lead=STANDARD, lines_max=20)[-1], PATH_WORDS)
        chosen, _cap = careful_pages({1: three}, lead=STANDARD, lines_max=2)
        self.assertEqual(chosen, [{"page": 1, "reasons": ["unsure_words", PATH_PAGE]}])

    def test_crop_boxes_honour_the_pad_passed(self) -> None:
        lines = page_read(["x"], unsure={}).lines
        self.assertEqual(crop_boxes(lines, [0], 1000, 3000)[0][0], (80.0, 180.0, 520.0, 260.0))  # 0.5
        self.assertEqual(crop_boxes(lines, [0], 1000, 3000, pad=1.0)[0][0], (60.0, 160.0, 540.0, 280.0))


def reader(client: Client, **settings: Any) -> Any:
    finder = FakeFinder(*(pixel_box(n) for n in range(LINES)))
    return engine(cast(Any, client), finder=finder, careful_early_max_pages=0, **settings)


class EngineLimitsTest(unittest.TestCase):
    def test_the_reader_reads_the_whole_page_past_its_hard_lines_max(self) -> None:
        careful = reply(
            value(
                *(line(f"Careful {n}", (100 + 50 * n, 100, 120 + 50 * n, 500), order=n) for n in range(LINES))
            )
        )
        client = Client({STANDARD: first_reply(HARD), CAREFUL: careful})
        pages = inline_pages(3)
        one = reader(client, cost_saving_hard_lines_max=2)  # HARD has 3
        with one.document(pages):
            one.read_page(page(), page_no=1)
        self.assertEqual(client.calls(), [(STANDARD, "low", "page"), (CAREFUL, "high", "page")])
        self.assertEqual(pages.escalated, {1: ["unsure_words", PATH_PAGE]})

    def test_the_reader_pads_its_crops_by_the_setting(self) -> None:
        client = Client({STANDARD: first_reply(HARD)}, crops=WORDS)
        one = reader(client, cost_saving_crop_pad=0.25)
        with one.document(inline_pages(3)):
            one.read_page(page(), page_no=1)
        (crops,) = [r for r in client.requests if r["schema"] is CROPS_SCHEMA]
        with Image.open(io.BytesIO(crops["png"])) as strip:
            self.assertEqual(strip.size, (420, 3 * 60 + 2 * CROP_GAP))  # each 40 + 2 x 10 high


# The run notes each page's tokens (pipeline._note_tokens).


def run_one(client: Client, lines: list[Any], **page: Any) -> dict[str, Any]:
    """One one-page Dynamic document through the pipeline; its run_config.reading."""

    def found(n: int) -> tuple[float, float, float, float]:
        y0, x0, y1, x1 = 100 + 100 * n, 100, 130 + 100 * n, 650
        return x0 * 0.612, y0 * 0.792, x1 * 0.612, y1 * 0.792

    reading = Reading(
        "dynamic",
        Agent(STANDARD, "google-vertex", "Standard"),
        careful=Agent(CAREFUL, "google-vertex", "Careful"),
        thinking="low",
    )
    system = rig(
        engine(
            cast(Any, client),
            finder=FakeFinder(*map(found, range(len(lines)))),
            ocr_engine="gemini",
            careful_early_max_pages=0,
        ),
        checker=FakeChecker("Total: 1,180.00"),
        ocr_model=STANDARD,
        job_reading=lambda customer, document: JobReading(None, STANDARD, "google-vertex", reading=reading),
    )
    job = system.run(make_pdf([SHARP]))
    return cast(dict[str, Any], system.totals(job).config["reading"])


def doc_lines(unsure: bool) -> list[Any]:
    texts = ["Invoice No: INV-17", "Sharma Traders", "Total: 1,18O.00", "Pune", "Thank you"]
    return [
        line(
            text,
            (100 + 100 * n, 100, 130 + 100 * n, 650),
            order=n,
            unsure=("1,18O.00",) if unsure and n == 2 else (),
        )
        for n, text in enumerate(texts)
    ]


class RunTokensTest(unittest.TestCase):
    def test_an_escalated_page_carries_its_first_and_careful_tokens(self) -> None:
        lines = doc_lines(unsure=True)
        client = Client({STANDARD: reply(value(*lines))}, crops=crops_reply([("Total: 1,180.00", ())]))
        config = run_one(client, lines)
        # Changed on purpose (16 Oct 2026): the entries carry tokens; the reasons are as before.
        self.assertEqual(
            config["escalated"],
            [
                {
                    "page": 1,
                    "reasons": ["unsure_words", PATH_WORDS],
                    "first_in": 1700,
                    "first_out": 4000,
                    "tokens_in": 300,
                    "tokens_out": 120,
                }
            ],
        )
        self.assertEqual(config["kept_quick"], [])

    def test_a_page_kept_quick_that_the_old_rule_read_whole_is_noted(self) -> None:
        lines = doc_lines(unsure=False)
        client = Client({STANDARD: reply(value(*lines, quality="fair"))})
        config = run_one(client, lines)
        self.assertEqual(config["escalated"], [])
        self.assertEqual(
            config["kept_quick"],
            [
                {
                    "page": 1,
                    "reasons": ["quality_fair"],
                    "first_in": 1700,
                    "first_out": 4000,
                    "tokens_in": 0,
                    "tokens_out": 0,
                }
            ],
        )


# cli cost-saving.


def entry(
    page_no: int, path: str, first: tuple[int, int] | None, careful: tuple[int, int] | None
) -> dict[str, Any]:
    found: dict[str, Any] = {"page": page_no, "reasons": ["unsure_words", path]}
    if first is not None:
        found.update(first_in=first[0], first_out=first[1])
    if careful is not None:
        found.update(tokens_in=careful[0], tokens_out=careful[1])
    return found


def kept(page_no: int, first: tuple[int, int]) -> dict[str, Any]:
    return {
        "page": page_no,
        "reasons": ["quality_fair"],
        "first_in": first[0],
        "first_out": first[1],
        "tokens_in": 0,
        "tokens_out": 0,
    }


LIMITS_SEEN = {"cost_saving_hard_lines_max": 12, "cost_saving_hard_share": 0.4, "cost_saving_crop_pad": 0.5}
ROWS: list[dict[str, Any]] = [
    # Read after per-page tokens: a words page, a whole page (the document's own average), one kept.
    {
        "pages_ocr": 5,
        "config": {
            **LIMITS_SEEN,
            "reading": {
                "mode": "dynamic",
                "escalated": [
                    entry(1, PATH_WORDS, (1000, 200), (300, 50)),
                    entry(2, PATH_PAGE, (1000, 200), (2000, 600)),
                ],
                "kept_quick": [kept(3, (800, 100))],
            },
        },
    },
    # Read before: no tokens, no kept_quick; its pages are counted, its tokens are not.
    {
        "pages_ocr": 3,
        "config": {"reading": {"mode": "dynamic", "escalated": [entry(1, PATH_WORDS, None, None)]}},
    },
    # Read after, no whole page of its own: the run set's ratio (2.0 in, 3.0 out); a cached
    # kept page (no first-read tokens) has not enough data.
    {
        "pages_ocr": 2,
        "config": {
            **LIMITS_SEEN,
            "reading": {
                "mode": "dynamic",
                "escalated": [entry(1, PATH_WORDS, (500, 100), (100, 20))],
                "kept_quick": [kept(2, (0, 0))],
            },
        },
    },
    {"pages_ocr": 4, "config": {"reading": {"mode": "single"}}},  # not Cost saving
    {"pages_ocr": 1, "config": None},
]


class CostSavingReportTest(unittest.TestCase):
    def test_measured_and_estimated_numbers_are_labelled_and_old_rows_are_counted_apart(self) -> None:
        lines = cli.cost_saving_report(ROWS).splitlines()
        self.assertEqual(
            lines[:10],
            [
                "cost saving: last 5 ready documents, 3 read with Cost saving"
                " (2 with per-page tokens, 1 read before they were recorded)",
                "limits seen (hard lines max/hard share/crop pad): -/-/-, 12/0.4/0.5",
                "measured  pages read                     10",
                "measured  pages that kept the quick read 6",
                "measured  pages read via the words path  3",
                "measured  pages read whole again         1",
                "measured  careful tokens spent           in 2400 out 670",
                # words: (2000 - 300, 600 - 50) on its own document's average, (500 x 2 - 100,
                # 100 x 3 - 20) on the ratio; kept: (2000, 600).
                "estimated tokens saved, words path       in 2600 out 830 (2 pages)",
                "estimated tokens saved, kept quick       in 2000 out 600 (1 pages)",
                "estimated tokens saved, total            in 4600 out 1430",
            ],
        )
        self.assertEqual(
            lines[10], "estimated tokens saved: not enough data for 1 pages (no whole-page read)"
        )
        self.assertIn("in 2.00 out 3.00", lines[11])
        self.assertTrue(lines[12].startswith("note: the token lines are exact only for documents read after"))

    def test_only_old_rows_say_there_is_no_token_data(self) -> None:
        lines = cli.cost_saving_report(ROWS[1:2]).splitlines()
        self.assertIn("(0 with per-page tokens, 1 read before they were recorded)", lines[0])
        self.assertEqual(lines[6], "measured  careful tokens spent           in 0 out 0")
        self.assertEqual(lines[7], "estimated tokens saved, words path       in 0 out 0 (0 pages)")
        self.assertIn("none yet", " ".join(lines))

    def test_the_command_is_wired_with_both_customer_flags(self) -> None:
        parser = cli._parser()
        args = parser.parse_args(["cost-saving", "--last", "50", "--customer", "c-1"])
        self.assertEqual((args.command, args.last, args.customer_id), ("cost-saving", 50, "c-1"))
        self.assertEqual(parser.parse_args(["cost-saving"]).last, 50)


# scripts/tune_cost_saving.py.


def tokens(read: PageRead, tokens_in: int, tokens_out: int) -> PageRead:
    return dataclasses.replace(read, input_tokens=tokens_in, output_tokens=tokens_out)


def tune_page(
    first_texts: list[str], unsure: dict[int, tuple[str, ...]], reference: list[str], words: list[str]
) -> tune.Page:
    return tune.Page(
        "scan.pdf page 1",
        tokens(page_read(first_texts, unsure=unsure), 100, 10),
        tokens(dataclasses.replace(page_read(reference, unsure={}), read_by=CAREFUL), 1000, 300),
        tokens(page_read(words, unsure={}), 100 + 50, 10 + 5),
    )


class TuneTest(unittest.TestCase):
    def test_the_grid_is_every_pair_in_order(self) -> None:
        self.assertEqual(tune.grid([6, 12], [0.25, 0.4]), [(6, 0.25), (6, 0.4), (12, 0.25), (12, 0.4)])

    def test_compare_counts_agreeing_words_and_differing_values_once(self) -> None:
        reference = page_read(["Total 1,180.00 Pune", "Date 12/10/2026"], unsure={})
        read = page_read(["Total 1,18O.00 Pune", "Date 12/10/2026."], unsure={})
        self.assertEqual(tune.compare(read, reference), (3, 5, 1))  # 1,18O.00; the "." is stripped
        self.assertEqual(tune.compare(reference, reference), (5, 5, 0))
        self.assertEqual(tune.compare(page_read(["Total"], unsure={}), reference), (1, 5, 2))

    def test_each_setting_routes_and_scores_its_pages_against_the_reference(self) -> None:
        # Three hard lines of ten: words at 12 / 0.4, whole at 2 / 0.4.
        first = [f"Line {n}" for n in range(10)]
        reference = [*first[:9], "Total 1,180.00"]
        page_one = tune_page(
            [*first[:9], "Total 1,18O.00"],
            dict.fromkeys(range(3), ("Line",)),
            reference,
            words=[*first[:9], "Total 1,18O.00"],  # the crops did not fix the total
        )
        baseline, rows = tune.score_all([page_one], STANDARD, [(12, 0.4), (2, 0.4)])
        reference_row, old = baseline
        self.assertEqual(
            (reference_row.calls, reference_row.tokens, reference_row.values_differ), (1, 1300, 0)
        )
        self.assertEqual((old.calls, old.tokens_in, old.tokens_out, old.values_differ), (2, 1100, 310, 0))
        words, whole = rows
        self.assertEqual(
            (words.setting, words.calls, words.tokens, words.values_differ),
            ("lines_max=12 share=0.4", 2, 165, 1),
        )
        self.assertEqual((whole.calls, whole.tokens, whole.values_differ), (2, 1410, 0))
        self.assertAlmostEqual(words.agreement, 19 / 20)
        self.assertIs(tune.recommend(rows), whole)  # the only one with no value differing
        self.assertIs(tune.recommend(rows, tolerance=1), words)  # cheaper, within 1

    def test_a_clear_page_keeps_its_first_read_under_every_setting(self) -> None:
        clear = tune_page(["Total 1,180.00"], {}, ["Total 1,180.00"], ["Total 1,180.00"])
        _baseline, rows = tune.score_all([clear], STANDARD, tune.grid(tune.LINES_MAX, tune.SHARES))
        self.assertEqual({(row.calls, row.tokens) for row in rows}, {(1, 110)})

    def test_recommend_takes_the_cheapest_within_tolerance_and_says_when_none_is(self) -> None:
        a = tune.Score("a", calls=4, tokens_in=100, values_differ=0)
        b = tune.Score("b", calls=3, tokens_in=50, values_differ=1)
        c = tune.Score("c", calls=5, tokens_in=80, values_differ=0)
        self.assertIs(tune.recommend([a, b, c]), c)
        self.assertIs(tune.recommend([a, b, c], tolerance=1), b)
        self.assertIsNone(tune.recommend([b]))
        text = tune.report([], [b], 0, 1)
        self.assertIn("recommend: none within 0 differing values; the closest is b (1)", text)
        self.assertIn("recommend: c (the cheapest", tune.report([], [a, b, c], 0, 1))

    def test_it_refuses_to_run_without_a_key_and_never_prints_one(self) -> None:
        with pytest.raises(SystemExit) as stopped:
            tune.main(["/scans"], environ={"OPENROUTER_API_KEY": "  "})
        self.assertIn("OPENROUTER_API_KEY is not set", str(stopped.value))
